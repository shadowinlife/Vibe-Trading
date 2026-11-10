"""Process-local native worker supervision with durable terminal evidence.

Only a committed dispatch can spawn. Process groups are signalled exclusively
through in-memory handles created by this supervisor; restart reconciliation
never signals, replays, or rewrites an old row.
"""

# noqa: SIZE_OK - one indivisible process-lifetime state machine.

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Final, Literal, TypedDict

from src.config.accessor import get_env_value
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    DispatchRow,
    DispatchTransitionError,
    StopClaim,
    StopRow,
    StopTransitionError,
)
from src.strategy_runs.execution_worker import WorkerSpec

__all__ = ["NativeSupervisor", "SupervisorError"]

_TERMINAL_GRACE_S: Final = 2.0
_SHUTDOWN_JOIN_S: Final = 1.0
_MAX_TERMINAL_BYTES: Final = 64 * 1024


class SupervisorError(ValueError):
    """A rejected start or persistence operation with stable error fields."""

    __slots__ = ("field", "reason")

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(slots=True)  # noqa: MUTABLE_OK - stop state changes under Part C.
class _WorkerHandle:
    remote_id: str
    dispatch_key: str
    proc: subprocess.Popen[bytes]
    pid: int
    pgid: int
    started_monotonic: float
    spec: WorkerSpec
    stop_requested: bool = False
    signal_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    signals_sent: set[int] = field(default_factory=set, repr=False)
    terminal_event: threading.Event = field(default_factory=threading.Event, repr=False)


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: Literal["completed", "failed", "cancelled"]
    exit_code: int | None
    error_kind: str | None


class _TerminalResult(TypedDict):
    remote_id: str
    dispatch_key: str
    status: Literal["completed", "failed", "cancelled"]
    exit_code: int | None
    error_kind: str | None
    finished_at: str
    contract_version: str


class NativeSupervisor:
    """Own live native processes and their one-shot terminal transitions."""

    def __init__(
        self,
        control_store: ControlStore,
        *,
        clock: Callable[[], float] = time.monotonic,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        thread_factory: Callable[..., threading.Thread] | None = None,
    ) -> None:
        """Initialize empty process-local registries.

        Args:
            control_store: Durable dispatch and terminal-evidence store.
            clock: Monotonic clock for handle timestamps and shutdown joins.
            popen: Process factory; production uses ``subprocess.Popen``.
            thread_factory: Optional watcher factory for deterministic tests.
        """
        self._store = control_store
        self._clock = clock
        self._popen = popen
        self._thread_factory = thread_factory or threading.Thread
        self._handles: dict[str, _WorkerHandle] = {}
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    def start(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        """Spawn one committed dispatch and return after registering its watcher.

        Args:
            dispatch_key: Existing durable dispatch identity.
            spec: Server-owned and validated worker declaration.

        Returns:
            The dispatch row with persisted pid, pgid, and worker metadata.

        Raises:
            SupervisorError: If the row is not dispatched or spawn/persistence fails.
        """
        with self._start_lock:
            return self._start_locked(dispatch_key, spec)

    def _start_locked(self, dispatch_key: str, spec: WorkerSpec) -> DispatchRow:
        row = self._store.get_dispatch(dispatch_key)
        if row is None:
            raise SupervisorError("dispatch_key", "dispatch row not found")
        if row.status != "dispatched":
            raise SupervisorError("status", f"expected dispatched, got {row.status}")
        with self._lock:
            if row.remote_id in self._handles:
                raise SupervisorError("remote_id", "worker already has a live handle")

        try:
            spec.run_dir.mkdir(parents=True, exist_ok=True)
            log = spec.log_path.open("ab")
        except OSError as exc:
            raise SupervisorError("log_path", str(exc)) from exc

        env = {**os.environ, **(spec.env or {})}
        if not env.get("PATH"):
            env["PATH"] = get_env_value("PATH") or os.defpath
        try:
            proc = self._popen(
                spec.argv,
                cwd=str(spec.cwd),
                env=env,
                start_new_session=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        except OSError as exc:
            log.close()
            raise SupervisorError("spawn", str(exc)) from exc

        handle: _WorkerHandle | None = None
        try:
            pgid = os.getpgid(proc.pid)
            handle = _WorkerHandle(
                remote_id=row.remote_id,
                dispatch_key=dispatch_key,
                proc=proc,
                pid=proc.pid,
                pgid=pgid,
                started_monotonic=self._clock(),
                spec=spec,
            )
            started = self._store.record_worker(
                dispatch_key,
                proc.pid,
                pgid,
                {
                    "kind": spec.kind,
                    "log_path": str(spec.log_path),
                    "result_path": str(spec.result_path),
                },
            )
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - spawn rollback boundary.
            if handle is None:
                self._terminate_unidentified(proc)
            else:
                self._signal_group(handle, signal.SIGKILL)
                self._wait_after_kill(handle)
            log.close()
            field = "pgid" if handle is None else "record_worker"
            raise SupervisorError(field, str(exc)) from exc

        with self._lock:
            self._handles[handle.remote_id] = handle
        watcher: threading.Thread | None = None
        try:
            watcher = self._thread_factory(
                target=self._watch,
                args=(handle, log),
                name=f"native-worker-{handle.remote_id}",
                daemon=True,
            )
            with self._lock:
                self._threads.add(watcher)
            watcher.start()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - watcher rollback boundary.
            self._signal_group(handle, signal.SIGKILL)
            self._wait_after_kill(handle)
            with self._lock:
                self._handles.pop(handle.remote_id, None)
                if watcher is not None:
                    self._threads.discard(watcher)
            log.close()
            self._persist_terminal(handle, _Outcome("failed", None, "runner_error"))
            raise SupervisorError("watcher", str(exc)) from exc
        return started

    def handle_is_live(self, remote_id: str) -> bool:
        """Return whether an in-memory handle still identifies a live process.

        Args:
            remote_id: Original remote identity registered by this supervisor.

        Returns:
            ``True`` only when the process is running and its pgid still matches.
        """
        return self._live_handle(remote_id) is not None

    def stop(
        self,
        dispatch_key: str,
        remote_id: str,
        stop_key: str,
        deadline_at: int,
    ) -> StopClaim:
        """Persist a stop before signalling only its matching live handle.

        Args:
            dispatch_key: Durable dispatch identity to stop.
            remote_id: Original remote identity bound to the dispatch.
            stop_key: Idempotent stop identity.
            deadline_at: Epoch milliseconds after which a request may expire.

        Returns:
            The replayed, still-requested, or evidence-confirmed stop claim.
        """
        claim = self._store.begin_stop(
            dispatch_key, remote_id, stop_key, deadline_at, CONTRACT_VERSION
        )
        if claim.replayed:
            if claim.row.state == "requested":
                record = self._terminal_record_evidence(dispatch_key)
                if record is not None:
                    row = self._confirm_stop(stop_key, "terminal_record", record)
                    return StopClaim(replayed=True, row=row)
            return claim

        record = self._terminal_record_evidence(dispatch_key)
        if record is not None:
            row = self._confirm_stop(stop_key, "terminal_record", record)
            return StopClaim(replayed=claim.replayed, row=row)

        handle = self._live_handle(remote_id, dispatch_key)
        if handle is None:
            return claim
        handle.stop_requested = True
        self._signal_group(handle, signal.SIGTERM)
        exit_code = self._wait_for_stop(handle)
        if exit_code is None:
            return claim

        handle.terminal_event.wait(timeout=_TERMINAL_GRACE_S)
        record = self._terminal_record_evidence(dispatch_key)
        if record is not None:
            row = self._confirm_stop(stop_key, "terminal_record", record)
            return StopClaim(replayed=claim.replayed, row=row)
        if exit_code == 0:
            evidence: dict[str, JsonValue] = {
                "pid": handle.pid,
                "pgid": handle.pgid,
                "exit_code": exit_code,
            }
            row = self._confirm_stop(stop_key, "process_exit", evidence)
            return StopClaim(replayed=claim.replayed, row=row)
        try:
            os.killpg(handle.pgid, 0)
        except (ProcessLookupError, PermissionError):
            evidence = {"pid": handle.pid, "pgid": handle.pgid}
            row = self._confirm_stop(stop_key, "process_absent", evidence)
            return StopClaim(replayed=claim.replayed, row=row)
        return claim

    def expire_stop(self, stop_key: str, now_ms: int | None = None) -> StopRow:
        """Expire only an overdue requested stop; never signal or upgrade it.

        Args:
            stop_key: Durable stop identity.
            now_ms: Optional epoch-millisecond comparison timestamp.

        Returns:
            The unchanged row, or its atomic ``unknown`` resolution.
        """
        row = self._required_stop(stop_key)
        stamp = int(time.time() * 1000) if now_ms is None else now_ms
        if row.state != "requested" or stamp < row.deadline_at:
            return row
        try:
            return self._store.resolve_stop(stop_key, "unknown", resolved_at=stamp)
        except StopTransitionError:
            current = self._required_stop(stop_key)
            if current.state == "requested":
                raise
            return current

    def _live_handle(
        self, remote_id: str, dispatch_key: str | None = None
    ) -> _WorkerHandle | None:
        with self._lock:
            handle = self._handles.get(remote_id)
        if (
            handle is None
            or (dispatch_key is not None and handle.dispatch_key != dispatch_key)
            or handle.proc.poll() is not None
        ):
            return None
        try:
            return handle if os.getpgid(handle.pid) == handle.pgid else None
        except (ProcessLookupError, PermissionError):
            return None

    def _confirm_stop(
        self,
        stop_key: str,
        evidence_kind: Literal["process_exit", "terminal_record", "process_absent"],
        evidence_payload: JsonValue,
    ) -> StopRow:
        try:
            return self._store.resolve_stop(
                stop_key, "confirmed", evidence_kind, evidence_payload
            )
        except StopTransitionError:
            current = self._required_stop(stop_key)
            if current.state == "requested":
                raise
            return current

    def _required_stop(self, stop_key: str) -> StopRow:
        row = self._store.get_stop(stop_key)
        if row is None:
            raise SupervisorError("stop_key", "stop row not found")
        return row

    def _terminal_record_evidence(
        self, dispatch_key: str
    ) -> dict[str, JsonValue] | None:
        row = self._store.get_dispatch(dispatch_key)
        if (
            row is None
            or row.status not in ("completed", "failed", "cancelled")
            or row.result_path is None
        ):
            return None
        try:
            encoded = Path(row.result_path).read_bytes()
            if len(encoded) > _MAX_TERMINAL_BYTES:
                return None
            result: object = json.loads(encoded)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        status = result.get("status")
        finished_at = result.get("finished_at")
        if (
            result.get("remote_id") != row.remote_id
            or result.get("dispatch_key") != dispatch_key
            or result.get("contract_version") != CONTRACT_VERSION
            or not isinstance(status, str)
            or status != row.status
            or not isinstance(finished_at, str)
            or not finished_at
        ):
            return None
        return {"status": status, "finished_at": finished_at}

    def _wait_for_stop(self, handle: _WorkerHandle) -> int | None:
        try:
            exit_code = handle.proc.wait(timeout=_TERMINAL_GRACE_S)
        except subprocess.TimeoutExpired:
            if handle.proc.poll() is None:
                self._signal_group(handle, signal.SIGKILL)
            try:
                exit_code = handle.proc.wait(timeout=_TERMINAL_GRACE_S)
            except (subprocess.TimeoutExpired, OSError):
                exit_code = handle.proc.poll()
        except OSError:
            exit_code = handle.proc.poll()
        return exit_code if type(exit_code) is int else None

    def reconcile_on_start(self) -> None:
        """Keep durable rows and old pids untouched after a supervisor restart.

        Returns:
            None. Restart never signals, replays, or rewrites a dispatch.
        """

    def shutdown(self) -> None:
        """Briefly join watchers without signalling or changing terminal rows.

        Returns:
            None, even if a watcher outlives the short join budget.
        """
        with self._lock:
            threads = tuple(self._threads)
        deadline = self._clock() + _SHUTDOWN_JOIN_S
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=max(0.0, deadline - self._clock()))

    def _watch(self, handle: _WorkerHandle, log: BinaryIO) -> None:
        outcome = self._wait_for_exit(handle)
        try:
            self._signal_group(handle, signal.SIGKILL)
            reaped = self._wait_after_kill(handle)
            if outcome.error_kind == "timeout" and type(reaped) is int:
                outcome = replace(outcome, exit_code=reaped)
            self._persist_terminal(handle, outcome)
        finally:
            with self._lock:
                if self._handles.get(handle.remote_id) is handle:
                    self._handles.pop(handle.remote_id, None)
                self._threads.discard(threading.current_thread())
            log.close()
            handle.terminal_event.set()

    def _wait_for_exit(self, handle: _WorkerHandle) -> _Outcome:
        try:
            exit_code = handle.proc.wait(timeout=handle.spec.timeout_s)
        except subprocess.TimeoutExpired:
            self._signal_group(handle, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                handle.proc.wait(timeout=_TERMINAL_GRACE_S)
            if handle.stop_requested:
                return _Outcome("cancelled", None, None)
            return _Outcome("failed", None, "timeout")
        except Exception:  # noqa: BROAD_EXCEPT_OK - watcher is the error boundary.
            return _Outcome("failed", None, "runner_error")
        if handle.stop_requested:
            return _Outcome("cancelled", exit_code, None)
        if type(exit_code) is not int:
            return _Outcome("failed", None, "runner_error")
        if exit_code == 0:
            return _Outcome("completed", exit_code, None)
        return _Outcome("failed", exit_code, "runner_error")

    def _wait_after_kill(self, handle: _WorkerHandle) -> int | None:
        try:
            return handle.proc.wait()
        except Exception:  # noqa: BROAD_EXCEPT_OK - terminal evidence must proceed.
            return None

    def _signal_group(self, handle: _WorkerHandle, sig: int) -> None:
        with handle.signal_lock:
            if sig in handle.signals_sent:
                return
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(handle.pgid, sig)
            handle.signals_sent.add(sig)

    def _terminate_unidentified(self, proc: subprocess.Popen[bytes]) -> None:
        """Clean a new session through its guaranteed pgid after getpgid fails."""
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            proc.wait(timeout=_TERMINAL_GRACE_S)
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            proc.wait()

    def _persist_terminal(self, handle: _WorkerHandle, outcome: _Outcome) -> None:
        temporary = _write_terminal(handle, outcome)
        try:
            try:
                self._store.mark_dispatch_terminal(
                    handle.dispatch_key,
                    outcome.status,
                    error_kind=outcome.error_kind,
                    result_path=str(handle.spec.result_path),
                )
            except DispatchTransitionError:
                return
            except ControlStoreError:
                pass
            _replace_terminal(temporary, handle.spec.result_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


def _write_terminal(handle: _WorkerHandle, outcome: _Outcome) -> Path:
    result: _TerminalResult = {
        "remote_id": handle.remote_id,
        "dispatch_key": handle.dispatch_key,
        "status": outcome.status,
        "exit_code": outcome.exit_code,
        "error_kind": outcome.error_kind,
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "contract_version": CONTRACT_VERSION,
    }
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_TERMINAL_BYTES:
        raise SupervisorError("result_path", "terminal JSON exceeds 64 KiB")
    destination = handle.spec.result_path
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        raise SupervisorError("result_path", str(exc)) from exc
    return temporary


def _replace_terminal(temporary: Path, destination: Path) -> None:
    try:
        os.replace(temporary, destination)
    except OSError as exc:
        raise SupervisorError("result_path", str(exc)) from exc
