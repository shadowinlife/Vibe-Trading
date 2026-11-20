"""Job-ledger helpers shared by strategy attach and commit paths."""

from __future__ import annotations

import time
from pathlib import Path

from src.scheduled_research.models import JobStatus
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library.paths import resolve_runtime_root

# Not "enabled": the primary uniqueness rule and the list view both use this.
DISABLED_STATUSES = frozenset({JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.EXPIRED})

_SCHEDULED_DIRNAME = "scheduled_research"
_JOB_STORE_FILENAME = "scheduled_research_jobs.json"

__all__ = [
    "DISABLED_STATUSES",
    "has_enabled_primary",
    "now_ms",
    "resolve_job_store",
]


def now_ms() -> int:
    """Return the current epoch time in milliseconds.

    Returns:
        The current Unix time in milliseconds.
    """
    return int(time.time() * 1000)


def resolve_job_store(
    job_store: ScheduledResearchJobStore | None, runtime_root: Path | None
) -> ScheduledResearchJobStore:
    """Return the explicit job store or one rooted under *runtime_root*.

    Args:
        job_store: Explicit store supplied by a caller or test.
        runtime_root: Optional runtime root for the default store location.

    Returns:
        The store that owns the scheduling job ledger.
    """
    if job_store is not None:
        return job_store
    if runtime_root is None:
        from src.scheduled_research.service import default_store

        return default_store()
    root = resolve_runtime_root(runtime_root)
    return ScheduledResearchJobStore(root / _SCHEDULED_DIRNAME / _JOB_STORE_FILENAME)


def has_enabled_primary(job_store: ScheduledResearchJobStore, strategy_id: str) -> bool:
    """Return whether *strategy_id* already has an enabled primary job.

    Args:
        job_store: Ledger containing every scheduling job.
        strategy_id: Strategy whose primary jobs are inspected.

    Returns:
        ``True`` when a non-terminal primary job already exists.
    """
    for job in job_store.load().values():
        if job.strategy_id != strategy_id or job.status in DISABLED_STATUSES:
            continue
        if job.run_policy.get("primary") is True:
            return True
    return False
