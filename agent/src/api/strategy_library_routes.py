"""Authenticated REST surface for the strategy library.

Reads project the strategy store, the merged run history and the schedule
store; writes delegate to the promotion facade, the draft updater and the two
evaluators. PATCH accepts only ``spec`` so the gate never moves without an
evaluation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from src.api.security import require_auth
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.service import default_store
from src.strategy_library import gate_code, review
from src.strategy_library.demo import seed_demo_strategy
from src.strategy_library.history import RunRecord, read_runs, read_runs_for_strategy
from src.strategy_library.paths import SIGNAL_ENGINE_RELPATH, strategy_dir
from src.strategy_library.promote import DUPLICATE_NAME_UNIVERSE, promote
from src.strategy_library.scheduling import list_schedules
from src.strategy_library.spec import Gap, validate_spec
from src.strategy_library.store import EditRequiredError, StaleVerdictError, get, list_strategies, update_draft
from src.strategy_store._shared import get_store
from src.strategy_store.models import Artifact, BenchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_DISABLED_STATUSES = frozenset({JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.EXPIRED})
_GATE_FIELDS = frozenset({"readiness", "evaluator_verdict", "previous_verdict", "validation_status", "demo"})
_LIST_ALL = 10_000
_BENCH_LIMIT = 5
_SCHEDULE_LIMIT = 5
_RUN_LIMIT = 20


class CreateStrategyRequest(BaseModel):
    """Body of ``POST /strategies``."""

    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]
    run_dir: str | None = None
    demo: bool = False


class UpdateStrategyRequest(BaseModel):
    """Body of ``PATCH /strategies/{id}``; only the spec is patchable."""

    model_config = ConfigDict(extra="allow")
    spec: dict[str, Any]


def _http(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    """Build the stable ``{code, message, ...}`` error envelope."""
    return HTTPException(status_code, detail={"code": code, "message": message, **extra})


def _not_found(strategy_id: str) -> HTTPException:
    return _http(404, "not_found", f"strategy {strategy_id!r} does not exist")


def _mutation(call: Callable[[], Any], strategy_id: str | None = None) -> Any:
    """Map facade and evaluator refusals onto their documented HTTP codes."""
    try:
        return call()
    except LookupError as exc:
        if strategy_id is None:
            raise
        raise _not_found(strategy_id) from exc
    except EditRequiredError as exc:
        raise _http(409, "edit_required", str(exc)) from exc
    except StaleVerdictError as exc:
        raise _http(409, "stale_verdict", str(exc)) from exc
    except ValueError as exc:
        raise _http(422, "invalid_spec", str(exc)) from exc


def _gap_payload(gap: Gap | Mapping[str, Any]) -> dict[str, str]:
    """Normalise a dataclass gap and a verdict gap onto one shape."""
    if isinstance(gap, Mapping):
        return {"code": str(gap.get("code") or ""), "field": str(gap.get("field") or ""),
                "message": str(gap.get("message") or ""), "fix_hint": str(gap.get("fix_hint") or "")}
    return {"code": gap.code, "field": gap.field, "message": gap.message, "fix_hint": gap.fix_hint}


def _verdict_time(verdict: Mapping[str, Any] | None) -> datetime | None:
    """Read the evaluation timestamp a verdict carries."""
    if not isinstance(verdict, Mapping):
        return None
    raw = verdict.get("evaluated_at") or verdict.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def _evidence(artifact: Artifact, bench: list[BenchResult], now: datetime) -> dict[str, Any]:
    """Derive ``has_bench``, ``last_verified`` and the freshness quality."""
    verdict = artifact.evaluator_verdict
    stamp = _verdict_time(verdict)
    if stamp is None:
        return {"has_bench": bool(bench), "last_verified": None,
                "quality": "low" if isinstance(verdict, Mapping) else None}
    age = now - stamp
    if age > timedelta(days=30):
        quality = "stale"
    elif artifact.kind == "code" and verdict.get("passed") is True and bench:
        quality = "high" if age <= timedelta(days=7) else "medium"
    else:
        quality = "low"
    return {"has_bench": bool(bench), "last_verified": stamp.isoformat(), "quality": quality}


def _last_run(runs: list[RunRecord]) -> dict[str, Any] | None:
    if not runs:
        return None
    return {"status": runs[0].status, "finished_at": runs[0].finished_at, "run_id": runs[0].run_id}


def _schedule_payload(job: ScheduledResearchJob, runs: list[RunRecord]) -> dict[str, Any]:
    return {"job_id": job.id, "strategy_id": job.strategy_id, "strategy_version": job.strategy_version,
            "schedule": job.schedule, "timezone": job.timezone, "status": job.status.value,
            "next_run_at": job.next_run_at,
            "last_runs": [r.to_dict() for r in runs if r.job_id == job.id][:_SCHEDULE_LIMIT]}


def _summary_payload(artifact: Artifact, *, schedules: list[ScheduledResearchJob],
                     runs: list[RunRecord], bench: list[BenchResult], now: datetime) -> dict[str, Any]:
    return {
        "id": artifact.id, "name": artifact.name, "kind": artifact.kind,
        "readiness": artifact.readiness, "version": artifact.artifact_version or "",
        "universe": artifact.universe, "demo": bool(artifact.demo),
        "evidence": _evidence(artifact, bench, now),
        "schedule": {"count": len(schedules),
                     "next_run_at": min((job.next_run_at for job in schedules), default=None)},
        "last_run": _last_run(runs),
    }


def _gaps(artifact: Artifact) -> list[dict[str, str]]:
    verdict = artifact.evaluator_verdict
    if isinstance(verdict, Mapping) and artifact.kind == "workflow":
        return [_gap_payload(gap) for gap in verdict.get("gaps") or []]
    if isinstance(verdict, Mapping) and artifact.kind == "code":
        detail = str(verdict.get("detail") or "")
        return [{"code": str(reason), "field": "run_dir", "message": detail,
                 "fix_hint": "fix the backtest run, then re-evaluate"}
                for reason in verdict.get("reasons") or []]
    return [_gap_payload(gap) for gap in validate_spec(artifact.spec)]


def _bench_payload(row: BenchResult) -> dict[str, Any]:
    return {"id": row.id, "bench_type": row.bench_type, "sharpe": row.sharpe,
            "annual_return": row.annual_return, "max_drawdown": row.max_drawdown,
            "calmar": row.calmar, "category": row.category.value if row.category else None,
            "run_dir": row.run_dir, "created_at": row.created_at}


def _config_summary(artifact: Artifact) -> dict[str, Any]:
    """A tiny projection of the copied backtest config, never the whole file."""
    path = strategy_dir(artifact.id) / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    return {"keys": len(config), "source": config.get("source"),
            "universe": config.get("universe") or artifact.universe}


def _signal_engine_path(artifact: Artifact) -> str:
    if artifact.kind != "code":
        return ""
    path = strategy_dir(artifact.id).joinpath(*SIGNAL_ENGINE_RELPATH.split("/"))
    return str(path) if path.is_file() else ""


def _strategy_jobs() -> list[ScheduledResearchJob]:
    return [job for job in default_store().load().values()
            if job.executor_kind == "strategy" and job.strategy_id]


def _runs_by_strategy(jobs: list[ScheduledResearchJob] | None = None) -> dict[str, list[RunRecord]]:
    """Group retained history by strategy, newest first, across every job."""
    grouped: dict[str, list[RunRecord]] = {}
    for job in _strategy_jobs() if jobs is None else jobs:
        try:
            records = read_runs(job.id)
        except ValueError:
            continue
        for record in records:
            if record.strategy_id == job.strategy_id:
                grouped.setdefault(record.strategy_id, []).append(record)
    for records in grouped.values():
        records.sort(key=lambda record: (record.started_at, record.run_id), reverse=True)
    return grouped


def _schedules_by_strategy() -> dict[str, list[ScheduledResearchJob]]:
    # Enabled schedules only: a cancelled or failed job is not in service and
    # must not count toward the per-strategy badge or its next run time.
    grouped: dict[str, list[ScheduledResearchJob]] = {}
    for job in list_schedules():
        if job.strategy_id:
            grouped.setdefault(job.strategy_id, []).append(job)
    return grouped


def _bench(strategy_id: str) -> list[BenchResult]:
    return list(get_store().get_bench_history(strategy_id, limit=_BENCH_LIMIT))


def _detail(artifact: Artifact) -> dict[str, Any]:
    schedules = _schedules_by_strategy().get(artifact.id, [])
    runs = _runs_by_strategy().get(artifact.id, [])
    bench = _bench(artifact.id)
    payload = _summary_payload(
        artifact, schedules=schedules, runs=runs, bench=bench, now=datetime.now(timezone.utc)
    )
    payload.update({
        "spec": artifact.spec or {}, "evaluator_verdict": artifact.evaluator_verdict,
        "previous_verdict": artifact.previous_verdict, "gaps": _gaps(artifact),
        "bench_history": [_bench_payload(row) for row in bench],
        "schedules": [_schedule_payload(job, runs) for job in schedules[:_SCHEDULE_LIMIT]],
        "run_history": [record.to_dict() for record in runs[:_RUN_LIMIT]],
        "config_summary": _config_summary(artifact), "signal_engine_path": _signal_engine_path(artifact),
    })
    return payload


# Static paths stay above ``/{strategy_id}``; FastAPI matches in registration
# order when two routes can serve the same request.
@router.get("", dependencies=[Depends(require_auth)])
def list_library(
    kind: Literal["code", "workflow"] | None = None,
    readiness: Literal["draft", "needs_work", "reviewing", "ready", "rejected"] | None = None,
    q: str | None = None,
    include_demo: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List strategies newest first with filters and pagination."""
    matched = list_strategies(kind=kind, readiness=readiness, q=q, limit=_LIST_ALL,
                              offset=0, include_demo=include_demo)
    now = datetime.now(timezone.utc)
    schedules, runs = _schedules_by_strategy(), _runs_by_strategy()
    items = [_summary_payload(artifact, schedules=schedules.get(artifact.id, []),
                              runs=runs.get(artifact.id, []), bench=_bench(artifact.id), now=now)
             for artifact in matched[offset : offset + limit]]
    return {"items": items, "total": len(matched), "limit": limit, "offset": offset}


@router.get("/summary", dependencies=[Depends(require_auth)])
def library_summary() -> dict[str, Any]:
    """Return the sidebar KPI summary; skipped/missed runs never enter the rate."""
    jobs = _strategy_jobs()
    active = [job for job in jobs if job.status not in _DISABLED_STATUSES]
    runs = [record for records in _runs_by_strategy(jobs).values() for record in records]
    now = datetime.now(timezone.utc)
    today = int(now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    week = int((now - timedelta(days=7)).timestamp() * 1000)
    completed = sum(1 for r in runs if r.status == "completed" and r.finished_at is not None
                    and r.finished_at >= week)
    failed = sum(1 for r in runs if r.status == "failed" and r.finished_at is not None
                 and r.finished_at >= week)
    return {
        "active_count": len(active),
        "runs_today": sum(1 for r in runs if r.started_at >= today and r.finished_at is not None),
        "failed_today": sum(1 for r in runs if r.started_at >= today and r.finished_at is not None
                            and r.status == "failed"),
        "next_run_at": min((job.next_run_at for job in active), default=None),
        "success_rate_7d": round(completed / (completed + failed), 4) if completed + failed else None,
    }


@router.post("", status_code=201, dependencies=[Depends(require_auth)])
def create_strategy(payload: CreateStrategyRequest) -> dict[str, Any]:
    """Promote a draft and evaluate it immediately; returns its detail."""
    result = _mutation(lambda: promote(payload.spec, session_id="", run_dir=payload.run_dir,
                                       demo=payload.demo))
    gaps = [_gap_payload(gap) for gap in result.gaps]
    duplicate = next((gap for gap in gaps if gap["code"] == DUPLICATE_NAME_UNIVERSE), None)
    if duplicate is not None:
        raise _http(409, DUPLICATE_NAME_UNIVERSE, duplicate["message"], gaps=gaps)
    if result.strategy_id is None:
        raise _http(422, "invalid_strategy", gaps[0]["message"] if gaps else "not registered", gaps=gaps)
    artifact = result.artifact or get(result.strategy_id)
    if artifact is None:  # pragma: no cover - promotion just created it
        raise _not_found(result.strategy_id)
    return _detail(artifact)


# POST /strategies/demo is registered here, before GET /strategies/{id}, so a
# static demo path can never be read as a strategy id.
@router.post("/demo", dependencies=[Depends(require_auth)])
def seed_demo() -> dict[str, Any]:
    """Load the example strategy and its disabled schedule; idempotent."""
    return seed_demo_strategy()


@router.post("/{strategy_id}/evaluate", dependencies=[Depends(require_auth)])
def evaluate_strategy(strategy_id: str) -> dict[str, Any]:
    """Evaluate synchronously in the threadpool; the rubric may take up to 90s."""
    artifact = get(strategy_id)
    if artifact is None:
        raise _not_found(strategy_id)
    evaluate = gate_code.evaluate_code if artifact.kind == "code" else review.evaluate_workflow
    try:
        _mutation(lambda: evaluate(strategy_id), strategy_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("strategy evaluation failed for %s", strategy_id)
        raise _http(503, "evaluator_unavailable", "strategy evaluation is temporarily unavailable")
    refreshed = get(strategy_id)
    if refreshed is None:  # pragma: no cover - the write cannot delete the row
        raise _not_found(strategy_id)
    return _detail(refreshed)


@router.patch("/{strategy_id}", dependencies=[Depends(require_auth)])
def patch_strategy(strategy_id: str, payload: UpdateStrategyRequest) -> dict[str, Any]:
    """Replace a strategy's spec; readiness and verdicts are never patchable."""
    extras = set(payload.model_extra or {})
    rejected = sorted(extras & _GATE_FIELDS)
    if rejected:
        raise _http(422, "gate_field_not_patchable",
                    "readiness and verdicts are written by the evaluator, not by PATCH", fields=rejected)
    if extras:
        raise _http(422, "unsupported_field", "PATCH accepts only the spec", fields=sorted(extras))
    return _detail(_mutation(lambda: update_draft(strategy_id, payload.spec), strategy_id))


@router.get("/{strategy_id}/runs", dependencies=[Depends(require_auth)])
def strategy_runs(strategy_id: str, limit: int = Query(_RUN_LIMIT, ge=1, le=_RUN_LIMIT)) -> list[dict[str, Any]]:
    """Return the newest runs for a strategy, merged across every job."""
    if get(strategy_id) is None:
        raise _not_found(strategy_id)
    return [record.to_dict() for record in read_runs_for_strategy(strategy_id, limit)]


@router.get("/{strategy_id}", dependencies=[Depends(require_auth)])
def strategy_detail(strategy_id: str) -> dict[str, Any]:
    """Return one strategy with its spec, verdicts, evidence and schedules."""
    artifact = get(strategy_id)
    if artifact is None:
        raise _not_found(strategy_id)
    return _detail(artifact)


def register_strategy_library_routes(app: FastAPI) -> None:
    """Mount the strategy library router on *app*."""
    app.include_router(router)


__all__ = ["register_strategy_library_routes", "router"]
