"""Native worker supervision, reaping, and durable terminal evidence (todo 4B).
The tested seams are NativeSupervisor's public lifecycle methods, the committed
ControlStore row, and the atomic terminal JSON. Part C extends this module with
stop/restart race cases; these names intentionally remain stable.
"""

# noqa: SIZE_OK - real process lifecycle cases share the same fixtures and seams.
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
import pytest
from src.strategy_runs import native_supervisor as native
from src.strategy_runs.control_contract import CONTRACT_VERSION, JsonValue
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    StopConflict,
)
from src.strategy_runs.execution_worker import WorkerSpec

AGENT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_KEYS = {
    "remote_id",
    "dispatch_key",
    "status",
    "exit_code",
    "error_kind",
    "finished_at",
    "contract_version",
}


@pytest.fixture
def allowed_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "runs").resolve()
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


def _store(tmp_path: Path) -> ControlStore:
    return ControlStore(tmp_path / "strategy_runs.sqlite3")


def _claim(store: ControlStore, run_dir: Path, key: str = "dispatch-a") -> None:
    store.claim_dispatch(
        key,
        "backtest",
        {"run_dir": str(run_dir)},
        f"remote-{key}",
        CONTRACT_VERSION,
    )


def _claimed_store(tmp_path: Path, run_dir: Path) -> ControlStore:
    store = _store(tmp_path)
    _claim(store, run_dir)
    return store


def _spec(run_dir: Path, source: str, timeout_s: int = 10) -> WorkerSpec:
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / "worker.py"
    script.write_text(source, encoding="utf-8")
    return WorkerSpec(
        kind="backtest",
        argv=(sys.executable, str(script)),
        cwd=AGENT_ROOT,
        timeout_s=timeout_s,
        run_dir=run_dir,
        result_path=run_dir / "worker_result.json",
        log_path=run_dir / "worker.log",
    )


def _threads() -> tuple[list[threading.Thread], Callable[..., threading.Thread]]:
    created: list[threading.Thread] = []

    def factory(**kwargs: Any) -> threading.Thread:
        thread = threading.Thread(**kwargs)
        created.append(thread)
        return thread

    return created, factory


def _join(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()


def _group_is_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def _result(spec: WorkerSpec) -> dict[str, Any]:
    payload = json.loads(spec.result_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _status(store: ControlStore) -> str:
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    return row.status


def test_dispatch_row_commit_precedes_spawn_and_returns_without_waiting(
    allowed_runs: Path, tmp_path: Path
) -> None:
    # Given a committed dispatch and a worker that stays alive briefly
    run_dir = allowed_runs / "commit-first"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(
        run_dir, "import time\nprint('started', flush=True)\ntime.sleep(0.2)\n"
    )
    spec = replace(spec, env={"VT_SUPERVISOR_TEST": "present", "PATH": ""})
    spec.log_path.write_bytes(b"existing-log\n")
    observed: list[str] = []
    real_popen = subprocess.Popen

    def checked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        row = store.get_dispatch("dispatch-a")
        assert row is not None
        observed.append(row.status)
        assert args == (spec.argv,)
        assert kwargs["cwd"] == str(spec.cwd)
        assert kwargs["start_new_session"] is True
        assert kwargs["close_fds"] is True
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["stdout"].mode == "ab"
        assert kwargs["env"]["VT_SUPERVISOR_TEST"] == "present"
        assert kwargs["env"]["PATH"]
        return real_popen(*args, **kwargs)

    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(
        store, popen=checked_popen, thread_factory=thread_factory
    )
    # When start returns
    started = supervisor.start("dispatch-a", spec)
    # Then the row preceded Popen and worker identity is already durable
    assert observed == ["dispatched"]
    assert started.worker_pid is not None
    assert started.worker_pgid == started.worker_pid
    assert started.status == "dispatched"
    assert threads[0].daemon
    _join(threads)
    assert _status(store) == "completed"
    assert spec.log_path.read_bytes().startswith(b"existing-log\nstarted\n")
    supervisor.shutdown()


def test_clean_exit_zero_writes_completed_terminal_and_clears_live_handle(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "clean"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "print('clean exit', flush=True)\n")
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _join(threads)
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.error_kind, row.result_path) == (
        "completed",
        None,
        str(spec.result_path),
    )
    result = _result(spec)
    assert set(result) == TERMINAL_KEYS
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["remote_id"] == started.remote_id
    assert result["dispatch_key"] == "dispatch-a"
    assert result["contract_version"] == CONTRACT_VERSION
    assert (
        datetime.fromisoformat(
            str(result["finished_at"]).replace("Z", "+00:00")
        ).utcoffset()
        is not None
    )
    assert spec.result_path.stat().st_size <= 64 * 1024
    assert not supervisor.handle_is_live(started.remote_id)
    supervisor.shutdown()
    assert _status(store) == "completed"


def test_nonzero_exit_maps_to_runner_error(allowed_runs: Path, tmp_path: Path) -> None:
    run_dir = allowed_runs / "exit-one"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "raise SystemExit(1)\n")
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-a", spec)
    _join(threads)
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.error_kind) == ("failed", "runner_error")
    assert _result(spec)["exit_code"] == 1


def test_timeout_kills_process_group_and_records_timeout(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "timeout"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(
        run_dir, "import time\nprint('waiting', flush=True)\ntime.sleep(60)\n", 1
    )
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _join(threads)
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.error_kind) == ("failed", "timeout")
    assert _result(spec)["error_kind"] == "timeout"
    assert started.worker_pgid is not None
    assert _group_is_gone(started.worker_pgid)
    assert not supervisor.handle_is_live(started.remote_id)


def test_clean_leader_exit_reaps_sleeping_descendant(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "descendants"
    store = _claimed_store(tmp_path, run_dir)
    source = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, __file__, 'grandchild'])\n"
        "if len(sys.argv) > 1:\n"
        "    time.sleep(60)\n"
        "else:\n"
        "    print(f'grandchild={child.pid}', flush=True)\n"
        "    time.sleep(0.2)\n"
    )
    spec = _spec(run_dir, source)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _join(threads)
    assert "grandchild=" in spec.log_path.read_text(encoding="utf-8")
    assert started.worker_pgid is not None
    assert _group_is_gone(started.worker_pgid)
    assert _status(store) == "completed"


def test_terminal_json_is_replaced_atomically_without_temp_files(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "atomic"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "pass\n")
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def checked_replace(source: Path, target: Path) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent == spec.result_path.parent
        assert set(json.loads(source_path.read_text(encoding="utf-8"))) == TERMINAL_KEYS
        replacements.append((source_path, target_path))
        real_replace(source, target)

    monkeypatch.setattr(native.os, "replace", checked_replace)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-a", spec)
    _join(threads)
    assert replacements == [(replacements[0][0], spec.result_path)]
    assert not list(run_dir.glob(".worker_result.json.*"))


def test_restart_reconcile_does_not_signal_replay_or_rewrite_dispatched_row(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "restart"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    old_process = subprocess.Popen(
        spec.argv,
        cwd=spec.cwd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    old_pgid = os.getpgid(old_process.pid)
    real_killpg = os.killpg
    store.record_worker("dispatch-a", old_process.pid, old_pgid, {"kind": "backtest"})
    before = store.get_dispatch("dispatch-a")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("restart must not spawn or signal")

    monkeypatch.setattr(native.os, "killpg", forbidden)
    supervisor = native.NativeSupervisor(store, popen=forbidden)
    try:
        supervisor.reconcile_on_start()
        assert store.get_dispatch("dispatch-a") == before
        assert not supervisor.handle_is_live("remote-dispatch-a")
        assert not spec.result_path.exists()
    finally:
        real_killpg(old_pgid, signal.SIGKILL)
        old_process.wait()


def test_start_without_dispatch_row_raises_and_never_spawns(
    allowed_runs: Path, tmp_path: Path
) -> None:
    store = _store(tmp_path)
    spec = _spec(allowed_runs / "missing-row", "pass\n")
    calls: list[Any] = []

    def forbidden_popen(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    supervisor = native.NativeSupervisor(store, popen=forbidden_popen)
    with pytest.raises(native.SupervisorError) as caught:
        supervisor.start("dispatch-a", spec)
    assert caught.value.field == "dispatch_key"
    assert caught.value.reason
    assert calls == []
    assert store.get_dispatch("dispatch-a") is None


def test_start_rejects_nondispatched_row_without_second_spawn(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "already-terminal"
    store = _claimed_store(tmp_path, run_dir)
    store.mark_dispatch_terminal("dispatch-a", "completed")
    spec = _spec(run_dir, "pass\n")
    calls: list[Any] = []

    def forbidden_popen(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    supervisor = native.NativeSupervisor(store, popen=forbidden_popen)
    with pytest.raises(native.SupervisorError) as caught:
        supervisor.start("dispatch-a", spec)
    assert caught.value.field == "status"
    assert calls == []


def test_record_worker_crash_kills_group_without_terminal_or_replay(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "persist-crash"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    observed_pgids: list[int] = []

    def fail_record(
        dispatch_key: str,
        pid: int,
        pgid: int,
        worker_meta: Mapping[str, JsonValue],
    ) -> None:
        observed_pgids.append(pgid)
        raise ControlStoreError("record_worker", "injected failure")

    monkeypatch.setattr(store, "record_worker", fail_record)
    supervisor = native.NativeSupervisor(store)
    with pytest.raises(native.SupervisorError) as caught:
        supervisor.start("dispatch-a", spec)
    assert caught.value.field == "record_worker"
    assert observed_pgids and _group_is_gone(observed_pgids[0])
    assert _status(store) == "dispatched"
    assert not spec.result_path.exists()


def test_getpgid_failure_kills_new_session_without_terminal(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "getpgid-crash"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    observed_pgids: list[int] = []
    real_popen = subprocess.Popen
    real_getpgid = os.getpgid

    def checked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        observed_pgids.append(real_getpgid(process.pid))
        return process

    def fail_getpgid(pid: int) -> int:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(native.os, "getpgid", fail_getpgid)
    supervisor = native.NativeSupervisor(store, popen=checked_popen)
    try:
        with pytest.raises(native.SupervisorError) as caught:
            supervisor.start("dispatch-a", spec)
        assert caught.value.field == "pgid"
        assert observed_pgids and _group_is_gone(observed_pgids[0])
        assert _status(store) == "dispatched"
        assert not spec.result_path.exists()
    finally:
        if observed_pgids and not _group_is_gone(observed_pgids[0]):
            os.killpg(observed_pgids[0], signal.SIGKILL)


class _MalformedWaitProcess:
    """A real child whose first wait result violates the Popen contract."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self.pid = process.pid
        self._first_wait = True

    def wait(self, timeout: float | None = None) -> int | None:
        if self._first_wait:
            self._first_wait = False
            return None
        return self._process.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._process.poll()


def test_malformed_process_result_maps_to_runner_error(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "malformed"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    real_popen = subprocess.Popen

    def malformed_popen(*args: Any, **kwargs: Any) -> _MalformedWaitProcess:
        return _MalformedWaitProcess(real_popen(*args, **kwargs))

    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(
        store, popen=malformed_popen, thread_factory=thread_factory
    )
    supervisor.start("dispatch-a", spec)
    _join(threads)
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.error_kind) == ("failed", "runner_error")
    assert _result(spec)["exit_code"] is None
    assert row.worker_pgid is not None
    assert _group_is_gone(row.worker_pgid)


def _term_spec(run_dir: Path, exit_code: int | None = None) -> WorkerSpec:
    handler = (
        "signal.SIG_IGN" if exit_code is None else f"lambda *_: sys.exit({exit_code})"
    )
    source = (
        "import signal, sys, time\n"
        f"signal.signal(signal.SIGTERM, {handler})\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    return _spec(run_dir, source)


def _wait_for_text(path: Path, text: str) -> None:
    waiter = threading.Event()
    for _ in range(500):
        if path.exists() and text in path.read_text(encoding="utf-8"):
            return
        waiter.wait(0.01)
    raise AssertionError(f"timed out waiting for {text!r} in {path}")


def test_stop_persists_requested_before_first_signal(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "requested-first"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    real_killpg = os.killpg
    observed: list[tuple[int, int, str]] = []

    def checked_killpg(pgid: int, sig: int) -> None:
        stop = store.get_stop("stop-a")
        assert stop is not None
        observed.append((pgid, sig, stop.state))
        real_killpg(pgid, sig)

    monkeypatch.setattr(native.os, "killpg", checked_killpg)
    claim = supervisor.stop(
        "dispatch-a", started.remote_id, "stop-a", deadline_at=60_000
    )
    _join(threads)
    assert observed[0][1:] == (signal.SIGTERM, "requested")
    assert (claim.row.state, claim.row.evidence_kind) == (
        "confirmed",
        "terminal_record",
    )


def test_live_stop_cancels_and_sends_one_term_kill_sequence(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "live-stop"
    store = _claimed_store(tmp_path, run_dir)
    spec = _term_spec(run_dir)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _wait_for_text(spec.log_path, "ready")
    real_killpg = os.killpg
    signals: list[tuple[int, int]] = []

    def recording_killpg(pgid: int, sig: int) -> None:
        signals.append((pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(native.os, "killpg", recording_killpg)
    stopped = supervisor.stop(
        "dispatch-a", started.remote_id, "stop-a", deadline_at=60_000
    )
    _join(threads)
    assert stopped.row.state == "confirmed"
    assert stopped.row.evidence_kind == "terminal_record"
    evidence = json.loads(stopped.row.evidence_json or "{}")
    assert set(evidence) == {"status", "finished_at"}
    assert evidence["status"] == "cancelled"
    assert [sig for pgid, sig in signals if pgid == started.worker_pgid] == [
        signal.SIGTERM,
        signal.SIGKILL,
    ]
    assert _status(store) == "cancelled"
    assert _result(spec)["status"] == "cancelled"


def test_replay_same_stop_key_does_not_signal_again(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "replay-stop"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "import time\ntime.sleep(60)\n")
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    real_killpg = os.killpg
    calls: list[tuple[int, int]] = []

    def recording_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(native.os, "killpg", recording_killpg)
    first = supervisor.stop("dispatch-a", started.remote_id, "stop-a", 60_000)
    _join(threads)
    count = len(calls)
    replay = supervisor.stop("dispatch-a", started.remote_id, "stop-a", 60_000)
    assert replay.replayed is True
    assert replay.row == first.row
    assert len(calls) == count
    assert supervisor.expire_stop("stop-a", now_ms=2**63 - 1) == first.row


def test_stop_key_reuse_for_other_remote_conflicts_without_signal(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _claimed_store(tmp_path, allowed_runs / "remote-a")
    supervisor = native.NativeSupervisor(store)
    requested = supervisor.stop(
        "dispatch-a", "remote-dispatch-a", "stop-a", deadline_at=60_000
    )
    _claim(store, allowed_runs / "remote-b", key="dispatch-b")
    calls: list[tuple[int, int]] = []

    def forbidden(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        raise AssertionError("conflicting stop must not signal")

    monkeypatch.setattr(native.os, "killpg", forbidden)
    with pytest.raises(StopConflict):
        supervisor.stop("dispatch-b", "remote-dispatch-b", "stop-a", deadline_at=60_000)
    assert store.get_stop("stop-a") == requested.row
    assert calls == []


def test_restart_without_handle_expires_unknown_without_signal(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _claimed_store(tmp_path, allowed_runs / "restart-stop")
    store.record_worker("dispatch-a", 999_999, 999_999, {})
    supervisor = native.NativeSupervisor(store)

    def forbidden(pgid: int, sig: int) -> None:
        raise AssertionError(f"restart must not signal {(pgid, sig)}")

    monkeypatch.setattr(native.os, "killpg", forbidden)
    requested = supervisor.stop(
        "dispatch-a", "remote-dispatch-a", "stop-a", deadline_at=60_000
    )
    early = supervisor.expire_stop("stop-a", now_ms=59_999)
    expired = supervisor.expire_stop("stop-a", now_ms=60_000)
    replay = supervisor.stop(
        "dispatch-a", "remote-dispatch-a", "stop-a", deadline_at=60_000
    )
    assert requested.row.state == "requested"
    assert early == requested.row
    assert expired.state == "unknown"
    assert (expired.evidence_kind, expired.evidence_json) == (None, None)
    assert supervisor.expire_stop("stop-a", now_ms=60_001) == expired
    assert replay.replayed is True
    assert replay.row == expired


def test_recycled_recorded_identity_never_signals_unrelated_sleeper(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _claimed_store(tmp_path, allowed_runs / "recycled")
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    real_killpg = os.killpg
    pgid = os.getpgid(sleeper.pid)
    try:
        store.record_worker("dispatch-a", sleeper.pid, pgid, {})
        supervisor = native.NativeSupervisor(store)

        def forbidden(called_pgid: int, sig: int) -> None:
            raise AssertionError(f"recycled identity signaled {(called_pgid, sig)}")

        monkeypatch.setattr(native.os, "killpg", forbidden)
        requested = supervisor.stop(
            "dispatch-a", "remote-dispatch-a", "stop-a", deadline_at=60_000
        )
        expired = supervisor.expire_stop("stop-a", now_ms=60_000)
        assert requested.row.state == "requested"
        assert expired.state == "unknown"
        assert sleeper.poll() is None
    finally:
        real_killpg(pgid, signal.SIGKILL)
        sleeper.wait(timeout=5)


def test_terminal_record_resolves_without_signal_and_never_downgrades(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _claimed_store(tmp_path, allowed_runs / "terminal-record")
    result = allowed_runs / "terminal.json"
    record = {
        "contract_version": CONTRACT_VERSION,
        "remote_id": "remote-dispatch-a",
        "dispatch_key": "dispatch-a",
        "status": "completed",
        "finished_at": "2026-01-01T00:00:00Z",
    }
    result.write_text(json.dumps(record), encoding="utf-8")
    store.mark_dispatch_terminal("dispatch-a", "completed", result_path=str(result))
    supervisor = native.NativeSupervisor(store)

    def forbidden(pgid: int, sig: int) -> None:
        raise AssertionError(f"terminal record must not signal {(pgid, sig)}")

    monkeypatch.setattr(native.os, "killpg", forbidden)
    stopped = supervisor.stop(
        "dispatch-a", "remote-dispatch-a", "stop-a", deadline_at=60_000
    )
    assert stopped.row.state == "confirmed"
    assert stopped.row.evidence_kind == "terminal_record"
    assert json.loads(stopped.row.evidence_json or "{}") == {
        "status": "completed",
        "finished_at": "2026-01-01T00:00:00Z",
    }
    assert supervisor.expire_stop("stop-a", now_ms=2**63 - 1) == stopped.row


def test_cas_loss_preserves_existing_terminal_file_and_cleans_temp(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "cas-loss"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "pass\n")
    existing = b'{"status":"failed","finished_at":"existing"}\n'
    spec.result_path.write_bytes(existing)
    original = store.mark_dispatch_terminal

    def lose_cas(
        dispatch_key: str,
        status: str,
        error_kind: str | None = None,
        result_path: str | None = None,
    ) -> Any:
        assert result_path is not None
        original(
            dispatch_key,
            "failed",
            error_kind="runner_error",
            result_path=result_path,
        )
        return original(
            dispatch_key, status, error_kind=error_kind, result_path=result_path
        )

    monkeypatch.setattr(store, "mark_dispatch_terminal", lose_cas)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-a", spec)
    _join(threads)
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.result_path) == ("failed", str(spec.result_path))
    assert spec.result_path.read_bytes() == existing
    assert not list(run_dir.glob(".worker_result.json.*"))


def test_generic_control_store_error_keeps_result_and_dispatch_nonterminal(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "store-error"
    store = _claimed_store(tmp_path, run_dir)
    spec = _spec(run_dir, "pass\n")

    def fail_terminal(*args: Any, **kwargs: Any) -> None:
        raise ControlStoreError("mark_dispatch_terminal", "injected generic failure")

    monkeypatch.setattr(store, "mark_dispatch_terminal", fail_terminal)
    thread_errors: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    supervisor.start("dispatch-a", spec)
    _join(threads)
    assert thread_errors == []
    assert _result(spec)["status"] == "completed"
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.result_path) == ("dispatched", None)
    assert not list(run_dir.glob(".worker_result.json.*"))


def test_nonzero_sigterm_exit_confirms_terminal_record_not_process_exit(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "nonzero-stop"
    store = _claimed_store(tmp_path, run_dir)
    spec = _term_spec(run_dir, 7)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _wait_for_text(spec.log_path, "ready")
    stopped = supervisor.stop("dispatch-a", started.remote_id, "stop-a", 60_000)
    _join(threads)
    assert stopped.row.state == "confirmed"
    assert stopped.row.evidence_kind == "terminal_record"
    evidence = json.loads(stopped.row.evidence_json or "{}")
    assert set(evidence) == {"status", "finished_at"}
    assert evidence["status"] == "cancelled"
    assert _result(spec)["exit_code"] == 7


def test_clean_zero_stop_without_terminal_record_uses_process_exit(
    allowed_runs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = allowed_runs / "zero-stop"
    store = _claimed_store(tmp_path, run_dir)
    spec = _term_spec(run_dir, 0)

    def fail_terminal(*args: Any, **kwargs: Any) -> None:
        raise ControlStoreError("mark_dispatch_terminal", "no durable terminal record")

    monkeypatch.setattr(store, "mark_dispatch_terminal", fail_terminal)
    threads, thread_factory = _threads()
    supervisor = native.NativeSupervisor(store, thread_factory=thread_factory)
    started = supervisor.start("dispatch-a", spec)
    _wait_for_text(spec.log_path, "ready")
    stopped = supervisor.stop("dispatch-a", started.remote_id, "stop-a", 60_000)
    _join(threads)
    assert stopped.row.state == "confirmed"
    assert stopped.row.evidence_kind == "process_exit"
    assert json.loads(stopped.row.evidence_json or "{}") == {
        "pid": started.worker_pid,
        "pgid": started.worker_pgid,
        "exit_code": 0,
    }
    assert _status(store) == "dispatched"
