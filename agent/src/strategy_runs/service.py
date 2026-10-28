"""Persistent three-state lifecycle for one RM-triggered strategy run.

The ledger (:mod:`src.strategy_runs.store`) answers *may this ``run_key``
start?*; this module answers everything after that: the version precheck, the
F9 runner call, the persisted five-key handle, one polling sweeper, and restart
convergence. The ledger is the only status authority — :meth:`status` never
polls or writes, and terminal writes go through the store's compare-and-swap so
a stale poll or recovery pass cannot overwrite a finished run.

Per-box targeting is checked *before* the claim: a request naming another box
is refused with ``403 target_mismatch`` and never consumes this box's
``run_key``, so the target box's later, correct request still starts normally.

A service run builds a ``ScheduledResearchJob`` in memory only; it is never
written to ``scheduled_research_jobs.json`` and never registered with the F9
scheduler. A ``running`` row with no handle is left alone inside the recovery
grace window, then becomes ``failed/recovered_after_restart``; a handle that is
already past ``timeout_s`` becomes ``failed/timeout``. A row that does carry a
handle is rehydrated and polled, never re-executed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from src.api.strategy_run_routes import DuplicateRunKeyError, StrategyRunCommand
from src.config.paths import get_runtime_root
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.strategy_policy import InFlightHandle
from src.strategy_library import store as strategy_store
from src.strategy_library.paths import (
    version_code_path,
    version_config_path,
    version_spec_path,
)
from src.strategy_library.runner import resolve_strategy_runner
from src.strategy_runs.store import RunRow, StrategyRunStore
from src.tools.redaction import redact_internal_paths, redact_text

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RECOVERY_GRACE_S",
    "DEFAULT_SANDBOX_ID_PREFIX",
    "DEFAULT_SWEEP_INTERVAL_S",
    "MAX_ERROR_CHARS",
    "SubmitResult",
    "StrategyRunService",
    "TARGET_MISMATCH",
    "build_service",
]

DEFAULT_SWEEP_INTERVAL_S = 5.0
DEFAULT_RECOVERY_GRACE_S = 120.0
MIN_SWEEP_INTERVAL_S = 1.0
MIN_RECOVERY_GRACE_S = 1.0

#: Fixed ``detail`` for a request that names another box.
TARGET_MISMATCH = "target_mismatch"
#: RM stores ``sandbox_id`` as this prefix plus the pod hostname.
DEFAULT_SANDBOX_ID_PREFIX = "default--"

#: Bound on stored diagnostics; enough to diagnose, small enough to GET.
MAX_ERROR_CHARS = 500
_MAX_ERROR_KIND_CHARS = 64

#: Exactly the keys persisted as ``handle_json`` (F9 five-key contract).
_IN_FLIGHT_KEYS = ("run_id", "kind", "session_id", "run_dir", "started_at")
_RUN_KINDS = frozenset({"code", "workflow"})
#: A valid interval string for the in-memory job; never scheduled or stored.
_MEMORY_JOB_SCHEDULE_MS = "60000"


@dataclass(frozen=True)
class SubmitResult:
    """What ``POST /strategy-runs`` needs: the run id, and nothing else."""

    vt_run_id: str


class StrategyRunService:
    """Claim, start, poll and persist the runs behind the two RM routes.

    Every dependency is injected so a test can run against a temporary SQLite
    ledger and a fake ``start``/``poll`` pair without an F9 runner, a session
    runtime or the network.
    """

    def __init__(
        self,
        *,
        store: StrategyRunStore,
        runner: Any,
        strategy_lookup: Callable[[str], Any | None],
        version_checker: Callable[[str, str, str], str | None],
        now_ms: Callable[[], int],
        target_validator: Callable[[StrategyRunCommand], bool] | None = None,
        sweep_interval_s: float = DEFAULT_SWEEP_INTERVAL_S,
        recovery_grace_s: float = DEFAULT_RECOVERY_GRACE_S,
    ) -> None:
        """Bind the ledger, runner, book lookups and clock.

        ``strategy_lookup`` returns the artifact (needs ``kind``) or ``None``;
        ``version_checker`` returns an ``error_kind`` or ``None``. Both
        intervals must be positive and finite. ``target_validator`` answers
        *is this request for this box?* and is consulted before the claim;
        ``None`` accepts every target for tests that do not exercise
        targeting.
        """
        self._store = store
        self._runner = runner
        self._strategy_lookup = strategy_lookup
        self._version_checker = version_checker
        self._now_ms = now_ms
        self._target_validator = target_validator
        self.sweep_interval_s = _positive_seconds("sweep_interval_s", sweep_interval_s)
        self.recovery_grace_s = _positive_seconds("recovery_grace_s", recovery_grace_s)
        self._task: asyncio.Task[None] | None = None

    @property
    def sweeper_task(self) -> asyncio.Task[None] | None:
        """Return the live sweeper task, or ``None`` after ``stop()``."""
        return self._task

    async def submit(self, command: StrategyRunCommand) -> SubmitResult:
        """Claim and start one run; a duplicate raises with the existing id.

        Every failure after the claim — missing strategy, missing version
        snapshot, or a runner exception — persists a terminal row and still
        returns the run id, so the caller's 202 always has something to poll.
        A request for another box is refused before the claim, so it cannot
        consume this box's ``run_key``.

        Raises:
            HTTPException: ``403 target_mismatch`` when the command names
                another box.
            DuplicateRunKeyError: When ``run_key`` was already claimed.
        """
        if self._target_validator is not None and not self._target_validator(command):
            raise HTTPException(status_code=403, detail=TARGET_MISMATCH)
        row = self._claim(command)
        artifact = self._strategy_lookup(command.strategy_id)
        if artifact is None:
            self._fail(
                row.vt_run_id,
                "strategy_not_found",
                f"strategy {command.strategy_id!r} is not in the library",
            )
            return SubmitResult(row.vt_run_id)

        version_error = self._version_checker(
            command.strategy_id,
            command.strategy_version,
            getattr(artifact, "kind", ""),
        )
        if version_error is not None:
            self._fail(
                row.vt_run_id,
                version_error,
                f"strategy version {command.strategy_id}/{command.strategy_version} "
                "is not available",
            )
            return SubmitResult(row.vt_run_id)

        job = _memory_job(row, command)
        try:
            handle = await self._runner.start(job)
        except LookupError as exc:
            logger.warning(
                "strategy run %s start failed (%s)", row.vt_run_id, type(exc).__name__
            )
            self._fail(row.vt_run_id, "strategy_not_found", _exception_text(exc))
            return SubmitResult(row.vt_run_id)
        except Exception as exc:  # noqa: BLE001 — a start fault is a run failure
            logger.warning(
                "strategy run %s start failed (%s)", row.vt_run_id, type(exc).__name__
            )
            self._fail(row.vt_run_id, "runner_error", _exception_text(exc))
            return SubmitResult(row.vt_run_id)

        try:
            payload = _project_handle(handle)
            handle_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._fail(
                row.vt_run_id,
                "runner_error",
                f"strategy runner returned an unusable handle: {_exception_text(exc)}",
            )
            return SubmitResult(row.vt_run_id)

        if not self._store.set_handle(
            row.vt_run_id, handle_json, now_ms=self._now_ms()
        ):
            logger.warning(
                "strategy run %s finished before its handle was persisted",
                row.vt_run_id,
            )
        return SubmitResult(row.vt_run_id)

    def status(self, vt_run_id: str) -> RunRow | None:
        """Read one ledger row without polling or writing anything."""
        return self._store.get(vt_run_id)

    async def sweep_once(self) -> None:
        """Poll every ``running`` row once; one broken row never stops the pass."""
        now = self._now_ms()
        for row in self._store.list_running():
            try:
                self._sweep_row(row, now)
            except Exception:  # noqa: BLE001 — one row must not stop the sweep
                logger.error(
                    "strategy run %s sweep failed", row.vt_run_id, exc_info=True
                )

    def recover(self) -> None:
        """Converge rows a previous process left ``running``; never re-execute."""
        now = self._now_ms()
        for row in self._store.list_running():
            try:
                self._recover_row(row, now)
            except Exception:  # noqa: BLE001 — recovery is best effort per row
                logger.error(
                    "strategy run %s recovery failed", row.vt_run_id, exc_info=True
                )

    async def start(self) -> None:
        """Run startup recovery once, then start exactly one sweeper task.

        Idempotent while the task is alive, so the lifespan and a manual caller
        cannot double the polling cadence.
        """
        if self._task is not None and not self._task.done():
            return
        self.recover()
        self._task = asyncio.create_task(
            self._sweep_forever(), name=f"strategy-run-sweeper-{id(self):x}"
        )

    async def stop(self) -> None:
        """Cancel the sweeper (if any) and wait for it to unwind."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _claim(self, command: StrategyRunCommand) -> RunRow:
        """Insert the running row, or raise with the existing claim's id."""
        result = self._store.claim(
            command.run_key,
            command.strategy_id,
            command.strategy_version,
            command.sandbox_id,
            command.upstream,
            command.timeout_s,
            now_ms=self._now_ms(),
        )
        if not result.won:
            raise DuplicateRunKeyError(result.row.vt_run_id)
        return result.row

    def _sweep_row(self, row: RunRow, now: int) -> None:
        """Poll one row, or converge it when its handle is absent/expired."""
        if row.handle_json is None:
            self._recover_missing_handle(row, now)
            return
        handle = _rehydrate(row.handle_json)
        if handle is None:
            self._fail(
                row.vt_run_id, "runner_error", "strategy run handle is unreadable"
            )
            return
        if now - handle.started_at >= row.timeout_s * 1000:
            self._fail(row.vt_run_id, "timeout", _timeout_text(row.timeout_s))
            return
        try:
            outcome = self._runner.poll(handle)
        except Exception:  # noqa: BLE001 — a poll fault is a runner failure
            logger.error("strategy run %s poll failed", row.vt_run_id, exc_info=True)
            self._fail(row.vt_run_id, "runner_error", "strategy runner poll failed")
            return
        if outcome is not None:
            self._apply_outcome(row.vt_run_id, outcome)

    def _recover_row(self, row: RunRow, now: int) -> None:
        """Expire or leave one ``running`` row during restart recovery."""
        if row.handle_json is None:
            self._recover_missing_handle(row, now)
            return
        handle = _rehydrate(row.handle_json)
        if handle is None:
            self._fail(
                row.vt_run_id, "runner_error", "strategy run handle is unreadable"
            )
            return
        if now - handle.started_at >= row.timeout_s * 1000:
            self._fail(row.vt_run_id, "timeout", _timeout_text(row.timeout_s))

    def _recover_missing_handle(self, row: RunRow, now: int) -> None:
        """Time out an expired row, else recover it after the grace window."""
        age_ms = max(0, now - row.created_at)
        if age_ms >= row.timeout_s * 1000:
            self._fail(row.vt_run_id, "timeout", _timeout_text(row.timeout_s))
        elif age_ms >= self.recovery_grace_s * 1000:
            self._fail(
                row.vt_run_id,
                "recovered_after_restart",
                "strategy run was interrupted by a process restart before it "
                "persisted a runner handle",
            )

    def _apply_outcome(self, vt_run_id: str, outcome: Any) -> None:
        """Map one runner outcome onto the ledger's three-state contract."""
        status = getattr(outcome, "status", None)
        if status == "completed":
            self._store.mark_terminal(vt_run_id, "completed", now_ms=self._now_ms())
            return
        if status == "failed":
            error = getattr(outcome, "error", "") or ""
            kind = getattr(outcome, "error_kind", "") or ""
        else:
            # Never leak the scheduling-internal status (skipped/missed) to RM.
            error = "strategy runner ended without a terminal outcome"
            kind = "runner_error"
        self._fail(vt_run_id, kind, error)

    def _fail(self, vt_run_id: str, error_kind: str, error: Any) -> None:
        """Write a bounded terminal failure; a lost CAS is a no-op."""
        self._store.mark_terminal(
            vt_run_id,
            "failed",
            error=_bounded_error(error),
            error_kind=_bounded_kind(error_kind),
            now_ms=self._now_ms(),
        )

    async def _sweep_forever(self) -> None:
        """Sleep one interval, sweep, repeat; cancellable at every await."""
        while True:
            await asyncio.sleep(self.sweep_interval_s)
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a pass must never kill the task
                logger.error("strategy-run sweeper pass failed", exc_info=True)


_SERVICE: StrategyRunService | None = None
_SERVICE_LOCK = threading.Lock()


def build_service() -> StrategyRunService:
    """Return the process-wide service, constructing it once under a lock.

    The lifespan and every request share this instance, so they also share one
    store and exactly one sweeper task.
    """
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = _build_default_service()
        return _SERVICE


def _build_default_service() -> StrategyRunService:
    """Wire the production ledger, F9 runner, library and clocks.

    The box identity is resolved once here, so one service instance keeps
    answering for the same box even if the process environment is mutated
    later.
    """
    raw = os.environ.get("VT_STRATEGY_RUN_SWEEP_INTERVAL_S", "5")  # noqa: env-gate
    sweep_interval_s = _clamped_seconds(
        raw, DEFAULT_SWEEP_INTERVAL_S, MIN_SWEEP_INTERVAL_S
    )
    raw = os.environ.get("VT_STRATEGY_RUN_RECOVERY_GRACE_S", "120")  # noqa: env-gate
    recovery_grace_s = _clamped_seconds(
        raw, DEFAULT_RECOVERY_GRACE_S, MIN_RECOVERY_GRACE_S
    )
    return StrategyRunService(
        store=StrategyRunStore(),
        runner=resolve_strategy_runner(),
        strategy_lookup=strategy_store.get,
        version_checker=_version_checker,
        now_ms=lambda: int(time.time() * 1000),
        target_validator=_target_validator(_rm_sandbox_id()),
        sweep_interval_s=sweep_interval_s,
        recovery_grace_s=recovery_grace_s,
    )


def _rm_sandbox_id() -> str:
    """Return the RM sandbox id this process runs inside.

    RM stores ``sandbox_id`` as ``RM_LLM_SANDBOX_ID_PREFIX + HOSTNAME`` (the
    pod hostname), so a bare hostname is never a valid target. An explicit
    ``VT_SANDBOX_ID`` wins when set.
    """
    explicit = os.environ.get("VT_SANDBOX_ID")  # noqa: env-gate
    if explicit is not None and explicit.strip():
        return explicit.strip()
    prefix = os.environ.get("RM_LLM_SANDBOX_ID_PREFIX")  # noqa: env-gate
    if prefix is None or not prefix.strip():
        prefix = DEFAULT_SANDBOX_ID_PREFIX
    host = os.environ.get("HOSTNAME")  # noqa: env-gate
    if host is None or not host.strip():
        host = socket.gethostname()
    return f"{prefix.strip()}{host.strip()}"


def _target_validator(box_id: str) -> Callable[[StrategyRunCommand], bool]:
    """Return a validator that accepts only *box_id* or no explicit target.

    A command without ``sandbox_id`` is upstream-only: the request already
    arrived on this box and ``upstream`` is a declaration, never dialled.
    """

    def _validate(command: StrategyRunCommand) -> bool:
        return command.sandbox_id is None or command.sandbox_id == box_id

    return _validate


def _version_checker(strategy_id: str, strategy_version: str, kind: str) -> str | None:
    """Check the pinned snapshot: code needs config+engine, workflow the spec.

    An unknown kind returns ``None`` and is rejected by ``runner.start`` as a
    ``runner_error``.
    """
    if kind not in _RUN_KINDS:
        return None
    root = get_runtime_root()
    try:
        if kind == "code":
            paths: tuple[Any, ...] = (
                version_config_path(strategy_id, strategy_version, runtime_root=root),
                version_code_path(strategy_id, strategy_version, runtime_root=root),
            )
        else:
            paths = (
                version_spec_path(strategy_id, strategy_version, runtime_root=root),
            )
    except (OSError, ValueError):
        return "version_not_found"
    return None if all(path.is_file() for path in paths) else "version_not_found"


def _memory_job(row: RunRow, command: StrategyRunCommand) -> ScheduledResearchJob:
    """Build the in-memory job the F9 runner dispatches; never persisted."""
    return ScheduledResearchJob(
        id=f"svc-{row.vt_run_id}",
        prompt="",
        schedule=_MEMORY_JOB_SCHEDULE_MS,
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=command.strategy_id,
        strategy_version=command.strategy_version,
        run_policy={"run_timeout_s": command.timeout_s},
    )


def _project_handle(handle: Any) -> dict[str, Any]:
    """Project a runner handle onto exactly the five persisted keys.

    Raises:
        AttributeError: When the runner returns something without
            ``to_in_flight``.
        KeyError: When a required key is missing.
        TypeError: When the projection is not a mapping.
        ValueError: When a value cannot be rehydrated by ``poll``.
    """
    raw = handle.to_in_flight()
    if not isinstance(raw, dict):
        raise TypeError("runner handle is not a mapping")
    payload = {
        "run_id": raw["run_id"],
        "kind": raw["kind"],
        "session_id": raw.get("session_id"),
        "run_dir": raw.get("run_dir"),
        "started_at": raw["started_at"],
    }
    if _payload_to_handle(payload) is None:
        raise ValueError("runner handle does not carry the five persisted keys")
    return payload


def _rehydrate(handle_json: str) -> InFlightHandle | None:
    """Rebuild a pollable handle, or ``None`` when the stored JSON is unusable."""
    try:
        payload = json.loads(handle_json)
    except (TypeError, ValueError):
        return None
    return _payload_to_handle(payload)


def _payload_to_handle(payload: Any) -> InFlightHandle | None:
    """Validate one five-key mapping and build an :class:`InFlightHandle`."""
    if not isinstance(payload, dict) or set(payload) != set(_IN_FLIGHT_KEYS):
        return None
    run_id = payload["run_id"]
    kind = payload["kind"]
    started_at = payload["started_at"]
    if not isinstance(run_id, str) or not run_id:
        return None
    if not isinstance(kind, str) or kind not in _RUN_KINDS:
        return None
    if (
        isinstance(started_at, bool)
        or not isinstance(started_at, int)
        or started_at < 0
    ):
        return None
    for name in ("session_id", "run_dir"):
        value = payload[name]
        if value is not None and not isinstance(value, str):
            return None
    return InFlightHandle(
        run_id=run_id,
        kind=kind,
        session_id=payload["session_id"],
        run_dir=payload["run_dir"],
        started_at=started_at,
    )


def _bounded_error(message: Any) -> str:
    """Return a bounded, redaction-safe, non-empty failure diagnostic."""
    text = "" if message is None else str(message)
    safe = redact_text(redact_internal_paths(text)).replace("\x00", "").strip()
    if not safe:
        safe = "strategy run failed"
    if len(safe) > MAX_ERROR_CHARS:
        safe = f"{safe[: MAX_ERROR_CHARS - 3].rstrip()}..."
    return safe


def _bounded_kind(kind: Any) -> str:
    """Return a non-empty, bounded error kind, defaulting to ``runner_error``."""
    if isinstance(kind, str):
        text = kind.strip()
        if text:
            return text[:_MAX_ERROR_KIND_CHARS]
    return "runner_error"


def _exception_text(exc: BaseException) -> str:
    """Return ``Type: message`` for one exception, bounded for the ledger."""
    return _bounded_error(f"{type(exc).__name__}: {exc}")


def _timeout_text(timeout_s: int) -> str:
    """Return the canonical timeout diagnostic."""
    return f"strategy run exceeded {timeout_s}s"


def _positive_seconds(name: str, value: Any) -> float:
    """Validate one positive, finite interval in seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number of seconds")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive, finite number of seconds")
    return number


def _clamped_seconds(raw: str, default: float, minimum: float) -> float:
    """Parse an env override, falling back on garbage and clamping to *minimum*."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(number, minimum)
