"""Authenticated REST surface for strategy schedules.

Reads project the scheduled-job store and each job's bounded run history;
writes delegate to the version-pinning attach/commit/detach facade and to the
scheduler's mark-due path, so no handler mutates the store itself. The
one-time ``confirm_token`` for a workflow schedule is returned here and
nowhere else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from src.api.security import require_auth
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.service import default_store
from src.scheduled_research.strategy_execution import StrategyRunConflict, run_now
from src.strategy_library import scheduling
from src.strategy_library.history import RunRecord, read_runs

router = APIRouter(tags=["strategy-schedules"])

_SCHEDULE_RUN_LIMIT = 5
_RUNS_DEFAULT_LIMIT = 20
_RUNS_MIN_LIMIT = 5
_RUNS_MAX_LIMIT = 100


class AttachScheduleRequest(BaseModel):
    """Body of ``POST /strategies/{id}/schedule``."""

    model_config = ConfigDict(extra="forbid")
    schedule: str
    timezone: str
    run_timeout_s: int | None = None


class CommitScheduleRequest(BaseModel):
    """Body of ``POST /strategy-schedule-proposals/{pid}/commit``.

    The token is optional in the schema so a missing one is refused with the
    documented 403 instead of Pydantic's generic 422.
    """

    model_config = ConfigDict(extra="forbid")
    confirm_token: str | None = None


def _http(status_code: int, code: str, message: str) -> HTTPException:
    """Build the stable ``{code, message}`` error envelope."""
    return HTTPException(status_code, detail={"code": code, "message": message})


def _schedule_row(job: ScheduledResearchJob, runs: list[RunRecord]) -> dict[str, Any]:
    """Project one strategy job plus at most five runs onto the wire shape."""
    return {
        "job_id": job.id,
        "strategy_id": job.strategy_id,
        "strategy_version": job.strategy_version,
        "schedule": job.schedule,
        "timezone": job.timezone,
        "status": job.status.value,
        "next_run_at": job.next_run_at,
        "last_runs": [record.to_dict() for record in runs[:_SCHEDULE_RUN_LIMIT]],
    }


def _strategy_schedule(job_id: str) -> ScheduledResearchJob:
    """Return the strategy job *job_id*; unknown or legacy ids are 404.

    Raises:
        HTTPException: 404 when no strategy job has *job_id*.
    """
    job = default_store().get(job_id)
    if job is None or job.executor_kind != "strategy":
        raise _http(404, "not_found", f"strategy schedule {job_id!r} does not exist")
    return job


@router.get("/strategy-schedules", dependencies=[Depends(require_auth)])
def list_strategy_schedules() -> list[dict[str, Any]]:
    """List enabled strategy schedules with their five newest runs."""
    rows: list[dict[str, Any]] = []
    for job in scheduling.list_schedules():
        rows.append(_schedule_row(job, read_runs(job.id, limit=_SCHEDULE_RUN_LIMIT)))
    return rows


@router.post(
    "/strategies/{strategy_id}/schedule",
    dependencies=[Depends(require_auth)],
)
def attach_strategy_schedule(
    strategy_id: str, body: AttachScheduleRequest
) -> dict[str, Any]:
    """Schedule a ready strategy against a freshly pinned version.

    A code strategy creates the job immediately. A workflow strategy returns a
    pending proposal plus the one-time ``confirm_token`` the commit route
    requires; this response is the only place the token is disclosed.
    """
    try:
        result = scheduling.attach(
            strategy_id,
            schedule=body.schedule,
            timezone=body.timezone,
            run_timeout_s=body.run_timeout_s,
        )
    except LookupError as exc:
        raise _http(404, "not_found", str(exc)) from exc
    except scheduling.ScheduleError as exc:
        raise _http(exc.status, exc.code, str(exc)) from exc
    return {
        "proposal_id": result.proposal_id,
        "confirm_token": result.confirm_token,
        "job_id": result.job_id,
        "strategy_version": result.strategy_version,
        "requires_confirmation": result.requires_confirmation,
    }


@router.post(
    "/strategy-schedule-proposals/{proposal_id}/commit",
    dependencies=[Depends(require_auth)],
)
def commit_strategy_schedule(
    proposal_id: str, body: CommitScheduleRequest
) -> dict[str, Any]:
    """Consume the one-time token and create the proposed schedule."""
    token = (body.confirm_token or "").strip()
    if not token:
        raise _http(403, "invalid_token", "a confirmation token is required")
    try:
        job = scheduling.commit(proposal_id, token)
    except scheduling.ScheduleError as exc:
        raise _http(exc.status, exc.code, str(exc)) from exc
    return _schedule_row(job, [])


@router.delete(
    "/strategy-schedules/{job_id}",
    status_code=204,
    dependencies=[Depends(require_auth)],
)
def delete_strategy_schedule(job_id: str) -> Response:
    """Delete one strategy schedule and cascade its run history."""
    _strategy_schedule(job_id)
    if not scheduling.detach(job_id):
        raise _http(404, "not_found", f"strategy schedule {job_id!r} does not exist")
    return Response(status_code=204)


@router.get(
    "/strategy-schedules/{job_id}/runs",
    dependencies=[Depends(require_auth)],
)
def list_strategy_schedule_runs(
    job_id: str, limit: int = Query(_RUNS_DEFAULT_LIMIT)
) -> list[dict[str, Any]]:
    """Return a job's run history, newest first.

    ``limit`` is clamped to 5..100 so a caller mistake can neither hide the
    page minimum nor request an unbounded read.
    """
    _strategy_schedule(job_id)
    bounded = max(_RUNS_MIN_LIMIT, min(_RUNS_MAX_LIMIT, limit))
    return [record.to_dict() for record in read_runs(job_id, limit=bounded)]


@router.post(
    "/strategy-schedules/{job_id}/run-now",
    dependencies=[Depends(require_auth)],
)
async def run_strategy_schedule_now(job_id: str) -> dict[str, Any]:
    """Mark a schedule due now and wake the scheduler.

    The mark-due write and the wake both belong to
    :func:`~src.scheduled_research.strategy_execution.run_now`; this handler
    never writes the store. The synchronous call contains no ``await`` so a
    tick cannot observe the job between the mark and the wake.
    """
    _strategy_schedule(job_id)
    try:
        job = run_now(job_id)
    except LookupError as exc:
        raise _http(404, "not_found", str(exc)) from exc
    except StrategyRunConflict as exc:
        raise _http(409, "run_in_flight", str(exc)) from exc
    return _schedule_row(job, read_runs(job_id, limit=_SCHEDULE_RUN_LIMIT))


def register_strategy_schedule_routes(app: FastAPI) -> None:
    """Mount the strategy schedule router on *app*."""
    app.include_router(router)


__all__ = ["register_strategy_schedule_routes", "router"]
