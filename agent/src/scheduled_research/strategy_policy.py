"""Run policy for ``executor_kind="strategy"`` scheduled jobs.

Answers the two questions a due firing asks when it must not become a new
run — an in-flight run already owns the slot (``skipped``), and the scheduler
woke up more than a whole slot late (``missed``) — and owns the synchronous
mark-due block behind ``POST /strategy-schedules/{job_id}/run-now``.

:mod:`src.scheduled_research.strategy_execution` re-exports every public name
defined here and keeps using the shared internal helpers; the split exists so
the run-lifecycle module stays inside its 400-line budget.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduling.triggers import next_due
from src.strategy_library.history import RunOutcome
from src.tools.redaction import redact_internal_paths, redact_text

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 1000

#: How ``run_now`` asks a live scheduler to look at the store again.
WakeFn = Callable[[], None]

_WAKE_TARGET: WakeFn | None = None

__all__ = [
    "InFlightHandle",
    "StrategyRunConflict",
    "WakeFn",
    "is_missed",
    "is_overlapped",
    "missed_outcome",
    "run_now",
    "set_wake_target",
    "skipped_outcome",
    "slot_span_ms",
]


@dataclass(frozen=True)
class InFlightHandle:
    """A persisted in-flight handle rehydrated for the runner's ``poll``."""

    run_id: str
    kind: str
    session_id: str | None
    run_dir: str | None
    started_at: int

    def to_in_flight(self) -> dict[str, Any]:
        """Return exactly the five keys the job model validates."""
        return asdict(self)


def _rehydrate(payload: dict[str, Any]) -> InFlightHandle:
    """Rebuild a pollable handle from its persisted five-key form."""
    return InFlightHandle(
        run_id=payload["run_id"],
        kind=payload["kind"],
        session_id=payload.get("session_id"),
        run_dir=payload.get("run_dir"),
        started_at=payload["started_at"],
    )


def _in_flight_payload(handle: Any) -> dict[str, Any]:
    """Project a handle onto the five keys, dropping anything else (a pid)."""
    raw = handle.to_in_flight()
    return {
        "run_id": raw["run_id"],
        "kind": raw["kind"],
        "session_id": raw.get("session_id"),
        "run_dir": raw.get("run_dir"),
        "started_at": raw["started_at"],
    }


class StrategyRunConflict(RuntimeError):
    """Raised when run-now targets a job whose run is already in flight.

    Routers map this onto ``409``; nothing is written to the store or to run
    history before it is raised.
    """


def _error_text(message: str) -> str:
    """Return a bounded, redaction-safe diagnostic for durable state."""
    safe = redact_text(redact_internal_paths(message)).replace("\x00", "")
    if len(safe) <= _MAX_ERROR_CHARS:
        return safe
    return f"{safe[: _MAX_ERROR_CHARS - 3]}..."


def _failed(error: str, error_kind: str, handle: Any | None = None) -> RunOutcome:
    """Return a failure outcome, carrying the handle's refs when available."""
    return RunOutcome(
        status="failed",
        error=error,
        error_kind=error_kind,
        run_dir=handle.run_dir if handle else None,
        session_id=handle.session_id if handle else None,
    )


def _same_record(
    current: ScheduledResearchJob, job: ScheduledResearchJob, run_id: str | None = None
) -> bool:
    """Return whether *current* is still the run *job* names (and attempt)."""
    if current.created_at != job.created_at:
        return False
    if current.strategy_version != job.strategy_version:
        return False
    if run_id is None:
        return True
    handle = current.in_flight
    return handle is not None and handle.get("run_id") == run_id


def slot_span_ms(job: ScheduledResearchJob) -> int | None:
    """Return the length of the slot starting at ``next_run_at``.

    An interval schedule yields its period; a cron schedule yields the gap to
    its following occurrence, which is the local meaning of one full slot.
    ``None`` means the schedule cannot project a following slot, so no lateness
    verdict can be justified.
    """
    try:
        following = next_due(job.schedule, job.next_run_at, job.timezone)
    except Exception:
        return None
    return following - job.next_run_at


def is_missed(job: ScheduledResearchJob, now_ms: int) -> bool:
    """Return whether *job* is due more than one whole slot late.

    A job that overslept by a full slot or more records one ``missed`` firing
    and is advanced past ``now``; the elapsed slots are never backfilled.
    """
    span = slot_span_ms(job)
    return span is not None and now_ms - job.next_run_at > span


def is_overlapped(job: ScheduledResearchJob, now_ms: int) -> bool:
    """Return whether a new slot came due after the in-flight run started.

    Both clauses are load-bearing: ``next_run_at`` is not advanced when a run
    starts, so the ``next_run_at`` clause alone would call the slot the run is
    executing an overlap, while the slot-after-start clause alone would
    re-report the same overlap on every tick until the run finishes.
    """
    handle = job.in_flight
    if handle is None or job.next_run_at > now_ms:
        return False
    try:
        following = next_due(job.schedule, int(handle["started_at"]), job.timezone)
    except Exception:
        return False
    return following <= now_ms


def skipped_outcome() -> RunOutcome:
    """Return the record for a due slot an in-flight run overlapped."""
    return RunOutcome(
        status="skipped",
        summary="previous run still in flight; this firing was skipped",
        error_kind="overlap",
    )


def missed_outcome() -> RunOutcome:
    """Return the record for a slot that elapsed while the scheduler was down."""
    return RunOutcome(
        status="missed",
        summary="schedule was overdue; missed slots are not backfilled",
        error_kind="missed_slot",
    )


def set_wake_target(target: WakeFn | None) -> None:
    """Install the process-wide wake hook ``run_now`` calls by default.

    ``None`` clears it and restores the lazy lookup of a live executor.
    """
    global _WAKE_TARGET
    _WAKE_TARGET = target


def resolve_wake() -> WakeFn | None:
    """Return the live scheduler's wake hook, or ``None`` when none is running.

    The executor singleton is read out of an already-imported
    :mod:`src.api.scheduled_routes` rather than through its accessor, because
    that accessor constructs an executor: a run-now request must not build a
    scheduler as a side effect. Without a running loop the periodic tick still
    picks the marked-due job up, so a missing hook is safe.
    """
    global _WAKE_TARGET
    if _WAKE_TARGET is not None:
        return _WAKE_TARGET
    module = sys.modules.get("src.api.scheduled_routes")
    executor = getattr(module, "_scheduled_research_executor", None) if module else None
    if executor is None:
        return None
    _WAKE_TARGET = executor.request_sweep
    return _WAKE_TARGET


def _now_ms() -> int:
    """Return the current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


def _default_store() -> ScheduledResearchJobStore:
    """Return the API-singleton job store, falling back to the default path."""
    from src.scheduled_research.service import default_store

    return default_store()


def run_now(
    job_id: str,
    *,
    store: ScheduledResearchJobStore | None = None,
    now_fn: Callable[[], int] | None = None,
    wake: WakeFn | None = None,
) -> ScheduledResearchJob:
    """Mark *job_id* due at the current instant, then wake the scheduler.

    One synchronous block with no suspension point: re-read the record, refuse
    a job whose run is in flight, persist ``next_run_at = now``, and only then
    wake. Waking first would let a tick read the unmarked record and miss the
    request, so the order is part of the contract.

    Args:
        job_id: Scheduled strategy job to mark due.
        store: Job store override; defaults to the API singleton.
        now_fn: Clock override returning epoch milliseconds.
        wake: Wake hook override; defaults to the live executor's.

    Returns:
        The persisted job.

    Raises:
        LookupError: When no job has *job_id*.
        StrategyRunConflict: When the job already has a run in flight.
    """
    jobs = store if store is not None else _default_store()
    current = jobs.get(job_id)
    if current is None:
        raise LookupError(f"strategy schedule {job_id!r} not found")
    if current.status is JobStatus.RUNNING or current.in_flight is not None:
        raise StrategyRunConflict(f"strategy schedule {job_id!r} is already running")
    current.next_run_at = (now_fn or _now_ms)()
    jobs.upsert(current, validate=False)
    hook = wake if wake is not None else resolve_wake()
    if hook is not None:
        hook()
    return current
