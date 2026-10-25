"""Async execution hooks for ``executor_kind="strategy"`` scheduled jobs.

Runs start without waiting: :func:`start_strategy_job` persists RUNNING plus
the runner's five-key in-flight handle, and the synchronous
:func:`sweep_strategy_jobs` polls later from the tick's tail. Terminal writes
append one bounded history record and advance ``next_run_at`` past ``now``
(``max(next_due(...), now + retry backoff)`` on failure), so nothing re-fires
on the next tick. The sweep re-reads the job after every poll and drops the
write for a deleted or replaced record; a run past its timeout is failed
before polling. :func:`recover_strategy_running` converges a job a previous
process left RUNNING with one ``recovered_after_restart`` record and no
dispatch: recovery runs before any missed-slot check, so one job never
produces both.

A firing that must not become a run is recorded as ``skipped`` (the previous
run still owns the slot) or ``missed`` (the scheduler overslept a whole slot);
both advance ``next_run_at`` through the same terminal writer and leave the
failure budget and the in-flight handle untouched. Run-now marks a job due and
wakes the scheduler; see :func:`run_now`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.strategy_policy import (
    InFlightHandle as InFlightHandle,
    StrategyRunConflict as StrategyRunConflict,
    _error_text,
    _failed,
    _in_flight_payload,
    _rehydrate,
    _same_record,
    is_missed,
    is_overlapped,
    missed_outcome,
    run_now as run_now,
    set_wake_target as set_wake_target,
    skipped_outcome,
)
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduling.triggers import next_due
from src.strategy_library import history
from src.strategy_library.history import RunOutcome, RunRecord

logger = logging.getLogger(__name__)

#: Floor before a failed attempt may fire again; the job's own schedule
#: usually sits far later, so this is a minimum, not a replacement.
DEFAULT_RETRY_BACKOFF_MS = 60 * 1000

#: Statuses that describe a firing denied before it ran; they advance the
#: schedule without becoming a failure of the job.
_NON_RUN_STATUSES = frozenset({"skipped", "missed"})


class StrategyHandle(Protocol):
    """One in-flight strategy run; only these five values are persisted."""

    run_id: str
    kind: str
    session_id: str | None
    run_dir: str | None
    started_at: int

    def to_in_flight(self) -> dict[str, Any]:
        """Return the five-key dict persisted as ``in_flight``."""


class StrategyRunner(Protocol):
    """Structural seam to the concrete code/workflow runners."""

    async def start(self, job: ScheduledResearchJob) -> StrategyHandle:
        """Begin a run and return its handle without waiting for it."""

    def poll(self, handle: StrategyHandle) -> RunOutcome | None:
        """Return the terminal outcome, or ``None`` while the run is live."""


def _finish_attempt(
    store: ScheduledResearchJobStore, job: ScheduledResearchJob, *,
    now_ms: int, max_failures: int,
    outcome: RunOutcome, require_current: bool = False,
) -> bool:
    """Append one history record and persist the attempt's terminal state.

    The single terminal path for completed, failed, timeout, restart
    recovery, and the two denied firings (``skipped``, ``missed``). Run
    identity comes from the persisted handle; a start failure has none, so it
    generates one, and a denied firing always gets a fresh id because it is
    not part of the run that still owns the handle. With *require_current* the
    store is re-read and a deleted or replaced record drops the write. An
    append failure is logged, never allowed to strand the job RUNNING.
    """
    current = store.get(job.id) if require_current else job
    if current is None or not _same_record(current, job):
        logger.info("strategy job %s gone or replaced; dropping write", job.id)
        return False
    job = current
    denied = outcome.status in _NON_RUN_STATUSES
    handle = None if denied else job.in_flight
    run_id = handle["run_id"] if handle else uuid.uuid4().hex
    started_at = handle["started_at"] if handle else now_ms
    try:
        history.append_run(
            RunRecord(
                run_id=run_id,
                job_id=job.id,
                strategy_id=job.strategy_id,
                strategy_version=job.strategy_version,
                started_at=started_at,
                finished_at=now_ms,
                status=outcome.status,
                session_id=outcome.session_id,
                run_dir=outcome.run_dir,
                summary=outcome.summary,
                error=outcome.error,
                error_kind=outcome.error_kind,
            ),
            retention=job.run_policy["retention"],
        )
    except Exception:
        logger.error("strategy job %s history append failed", job.id, exc_info=True)

    try:
        job.next_run_at = next_due(job.schedule, now_ms, job.timezone)
    except Exception:
        logger.error("strategy job %s schedule advance failed", job.id, exc_info=True)
        job.next_run_at = now_ms + DEFAULT_RETRY_BACKOFF_MS
    assert job.next_run_at > now_ms
    if denied:
        # Nothing ran: status, run identity and the failure budget stay as
        # they were, so a skipped slot cannot fail an in-flight job.
        store.upsert(job, validate=False)
        return True
    job.last_run_at = now_ms
    job.in_flight = None
    if outcome.status == "completed":
        job.status = JobStatus.COMPLETED
        job.failure_kind = None
        job.last_error = None
        job.consecutive_failures = 0
    else:
        job.consecutive_failures += 1
        job.failure_kind = "strategy"
        job.last_error = _error_text(
            outcome.error or outcome.summary or outcome.error_kind or "strategy run failed"
        )
        if job.consecutive_failures >= max_failures:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.PENDING
            job.next_run_at = max(job.next_run_at, now_ms + DEFAULT_RETRY_BACKOFF_MS)
    store.upsert(job, validate=False)
    return True


async def start_strategy_job(
    store: ScheduledResearchJobStore, job: ScheduledResearchJob,
    runner: StrategyRunner | None, now_ms: int, max_failures: int,
) -> None:
    """Start one due strategy job without waiting for the run to finish.

    A firing that arrives more than one whole slot late becomes a single
    ``missed`` record and is advanced past ``now``; it never runs, so a
    restart cannot backfill the slots it slept through. A missing runner fails
    closed as a ``runner_error``. RUNNING is persisted before the runner is
    awaited, so a crash mid-start is recovered on the next process start
    rather than re-dispatched as a fresh run.
    """
    current = store.get(job.id)
    if current is None or not _same_record(current, job):
        logger.info("strategy job %s disappeared before start; skipping", job.id)
        return

    if is_missed(current, now_ms):
        logger.info("strategy job %s overslept its slot; recording one missed firing", job.id)
        _finish_attempt(
            store,
            current,
            now_ms=now_ms,
            max_failures=max_failures,
            outcome=missed_outcome(),
        )
        return

    if runner is None:
        _finish_attempt(
            store,
            current,
            now_ms=now_ms,
            max_failures=max_failures,
            outcome=_failed("strategy runner is not configured", "runner_error"),
        )
        return

    current.status = JobStatus.RUNNING
    store.upsert(current, validate=False)

    try:
        handle = await runner.start(current)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("strategy job %s failed to start", job.id, exc_info=True)
        _finish_attempt(
            store,
            current,
            now_ms=now_ms,
            max_failures=max_failures,
            outcome=_failed(
                _error_text(f"{type(exc).__name__}: {exc}"), "runner_error"
            ),
            require_current=True,
        )
        return

    latest = store.get(job.id)
    if latest is None or not _same_record(latest, current):
        logger.info("strategy job %s replaced during start; dropping handle", job.id)
        return
    try:
        latest.in_flight = _in_flight_payload(handle)
    except (KeyError, TypeError, ValueError):
        logger.error("strategy job %s got an unusable handle", job.id, exc_info=True)
        _finish_attempt(
            store,
            latest,
            now_ms=now_ms,
            max_failures=max_failures,
            outcome=_failed(
                "strategy runner returned an unusable handle", "runner_error"
            ),
        )
        return
    store.upsert(latest, validate=False)


def sweep_strategy_jobs(
    store: ScheduledResearchJobStore, runner: StrategyRunner | None,
    now_ms: int, max_failures: int,
) -> None:
    """Poll every in-flight strategy run once and write terminal outcomes.

    A slot that came due while a run was already in flight is recorded as one
    ``skipped`` firing and advanced past ``now`` instead of being dispatched,
    so overlapping firings are visible without ever running twice in parallel.
    Only RUNNING jobs carrying an ``in_flight`` handle are polled, so a
    finished run is never polled twice. The job is re-read after every poll
    (even a ``None`` result) and the write is dropped when the record is
    gone, its ``strategy_version`` moved, or another run owns the attempt.
    """
    for job in list(store.load().values()):
        if job.executor_kind != "strategy":
            continue
        if job.status is JobStatus.RUNNING and is_overlapped(job, now_ms):
            _finish_attempt(
                store,
                job,
                now_ms=now_ms,
                max_failures=max_failures,
                outcome=skipped_outcome(),
                require_current=True,
            )
            continue
        if runner is None or job.status is not JobStatus.RUNNING:
            continue
        payload = job.in_flight
        if payload is None:
            continue
        handle = _rehydrate(payload)

        timeout_ms = job.run_policy["run_timeout_s"] * 1000
        if now_ms - handle.started_at >= timeout_ms:
            _finish_attempt(
                store,
                job,
                now_ms=now_ms,
                max_failures=max_failures,
                outcome=_failed(
                    f"strategy run exceeded {job.run_policy['run_timeout_s']}s",
                    "timeout",
                    handle,
                ),
                require_current=True,
            )
            continue

        try:
            outcome = runner.poll(handle)
        except Exception as exc:
            logger.error("strategy job %s poll failed", job.id, exc_info=True)
            outcome = _failed(
                _error_text(f"{type(exc).__name__}: {exc}"), "runner_error", handle
            )

        # A DELETE, a replacement POST or another writer can land during the
        # poll; the stored record is the only authority on what may be written.
        current = store.get(job.id)
        if current is None or not _same_record(current, job, handle.run_id):
            logger.info("strategy job %s changed during poll; dropping write", job.id)
            continue
        if outcome is None:
            continue
        _finish_attempt(
            store,
            current,
            now_ms=now_ms,
            max_failures=max_failures,
            outcome=outcome,
        )


def recover_strategy_running(
    store: ScheduledResearchJobStore, job: ScheduledResearchJob,
    now_ms: int, max_failures: int,
) -> ScheduledResearchJob:
    """Converge one strategy job a previous process left RUNNING.

    Exactly one ``recovered_after_restart`` record is appended and the job is
    never dispatched here: the next run is advanced past ``now`` so the
    current tick cannot immediately re-fire it. A RUNNING job with no handle
    (a crash between the RUNNING write and the handle write) still gets one
    record, with a generated run id.
    """
    payload = job.in_flight
    handle = _rehydrate(payload) if payload is not None else None
    _finish_attempt(
        store,
        job,
        now_ms=now_ms,
        max_failures=max_failures,
        outcome=_failed(
            "strategy run interrupted by a process restart",
            "recovered_after_restart",
            handle,
        ),
    )
    return job


class StrategyExecution:
    """Executor-bound adapter for the strategy start/sweep/recover hooks.

    One short method per insertion site keeps the store, runner and failure
    budget out of every call; the host is read at call time, so a runner
    installed after construction is visible to the next hook.
    """

    def __init__(self, host: Any) -> None:
        """Bind the adapter to *host*."""
        self._host = host

    async def start(self, job: ScheduledResearchJob, now_ms: int) -> None:
        """Start one due job; see :func:`start_strategy_job`."""
        await start_strategy_job(
            self._host._store,
            job,
            self._host._strategy_runner,
            now_ms,
            self._host._max_consecutive_failures,
        )

    def sweep(self, now_ms: int) -> None:
        """Poll in-flight runs; see :func:`sweep_strategy_jobs`."""
        sweep_strategy_jobs(
            self._host._store,
            self._host._strategy_runner,
            now_ms,
            self._host._max_consecutive_failures,
        )

    def recover(self, job: ScheduledResearchJob, now_ms: int) -> int:
        """Converge one RUNNING job; returns its recovery count (always one)."""
        recover_strategy_running(
            self._host._store, job, now_ms, self._host._max_consecutive_failures
        )
        return 1
