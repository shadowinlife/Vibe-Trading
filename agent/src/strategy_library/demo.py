"""Seed a labelled demo strategy with a pinned version and a disabled schedule.

The empty library offers one click to load an example. The example must be
honest: a ``workflow`` promoted with ``demo=True``, left at ``needs_work``
because nothing evaluated it, with no run history and no performance figures.
Its schedule exists to show what an attached schedule looks like and is
created ``CANCELLED``, so nothing fires until a human attaches a real one.

That combination is the single documented exception to the ``ready`` gate in
:func:`src.strategy_library.scheduling.attach`, and it lives here rather than
there: the job row is written directly, after :func:`snapshot` pins the exact
version it names, so a demo schedule can never point at an unpinned strategy
and ``attach`` keeps refusing every non-ready strategy unchanged.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.service import default_store as default_job_store
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library.paths import resolve_runtime_root
from src.strategy_library.promote import DUPLICATE_NAME_UNIVERSE, promote
from src.strategy_library.store import get, list_strategies, snapshot
from src.strategy_store.models import Artifact
from src.strategy_store.sqlite_store import SqliteStrategyStore

#: Stable seed name. The store mints the artifact row id (``art_<hex>``), so
#: this is the handle every surface resolves the demo by.
DEMO_STRATEGY_ID = "demo-concept-upstream-map"
DEMO_STRATEGY_NAME = DEMO_STRATEGY_ID
DEMO_UNIVERSE = "cn_a"
DEMO_SESSION_ID = "demo-seed"
DEMO_JOB_ID = DEMO_STRATEGY_ID

#: Weekly, Monday 09:00 in the market's timezone. The job ships cancelled, so
#: the expression is illustrative until a human enables a real schedule.
DEMO_SCHEDULE = "0 9 * * 1"
DEMO_TIMEZONE = "Asia/Shanghai"
DEMO_RUN_POLICY: dict[str, Any] = {
    "overlap": "skip",
    "retention": 20,
    "run_timeout_s": 3600,
    "primary": True,
}

#: A concept is only investable once its upstream suppliers are named, so the
#: plan maps the concept first and backtests last. Every tool is in
#: ``ALLOWED_STRATEGY_TOOLS``; swarm orchestration is deliberately absent
#: because a strategy may never fan out into unlogged sub-runs.
DEMO_SPEC: dict[str, Any] = {
    "name": DEMO_STRATEGY_NAME,
    "kind": "workflow",
    "thesis": (
        "A theme is testable only after its upstream suppliers are named: map "
        "the concept to the companies that supply it, keep the tradable "
        "candidates, screen their price and volume state, then verify the idea "
        "with a backtest before trusting it."
    ),
    "universe": DEMO_UNIVERSE,
    "inputs": [
        {
            "name": "concept",
            "source": "get_sector_info",
            "required": True,
            "missing_policy": "stop",
        },
        {
            "name": "bars",
            "source": "get_market_data",
            "required": True,
            "missing_policy": "stop",
        },
    ],
    "preconditions": [
        "the concept resolves to a sector or theme board",
        "price history covers the whole review window",
    ],
    "steps": [
        {
            "id": "concept-upstream-map",
            "action": "Map the concept to its board and name the upstream suppliers",
            "tool": "get_sector_info",
            "output": "concept board plus upstream supplier names",
        },
        {
            "id": "candidate-pool",
            "action": "Resolve each supplier name to a tradable symbol",
            "tool": "search_symbol",
            "output": "candidate symbol pool",
        },
        {
            "id": "technical-screen",
            "action": "Screen the candidates on their price and volume state",
            "tool": "technical_indicators",
            "output": "screen result per candidate",
        },
        {
            "id": "backtest-verify",
            "action": "Verify each surviving idea on its historical bars",
            "tool": "backtest",
            "output": "backtest verdict per candidate",
        },
    ],
    "output_contract": {
        "format": "markdown_table",
        "required_fields": [
            "symbol",
            "supplier_role",
            "screen_result",
            "backtest_verdict",
        ],
        "destination": "chat",
    },
    "failure_handling": (
        "Report the failing step and the tool that failed, then stop. A missing "
        "data point stays missing; it is never replaced by an estimate."
    ),
    "schedule_hint": "weekly, after the market closes",
}

_MAX_SUFFIX = 20
_LIST_LIMIT = 10_000
_JOBS_DIRNAME = "scheduled_research"
_JOBS_FILENAME = "scheduled_research_jobs.json"


def _candidate_names() -> list[str]:
    """Return the base seed name followed by its ``-<n>`` fallbacks."""
    return [DEMO_STRATEGY_NAME] + [
        f"{DEMO_STRATEGY_NAME}-{n}" for n in range(2, _MAX_SUFFIX + 1)
    ]


def _spec(name: str) -> dict[str, Any]:
    """Return a copy of :data:`DEMO_SPEC` carrying *name*."""
    spec = copy.deepcopy(DEMO_SPEC)
    spec["name"] = name
    return spec


def _existing_demo(rows: list[Artifact]) -> Artifact | None:
    """Return the already-seeded demo row, or ``None`` when there is none.

    Matching on the candidate names keeps a second call from seeding a
    duplicate under the next suffix after the first one took the base name.
    """
    candidates = set(_candidate_names())
    for row in rows:
        if row.demo and (row.universe or "") == DEMO_UNIVERSE and row.name in candidates:
            return row
    return None


def _promote_demo(
    rows: list[Artifact], store: SqliteStrategyStore | None, runtime_root: Path | None
) -> str:
    """Promote the demo under the first free name and return its row id.

    A real strategy holding the base name is never touched: that name is
    skipped and the ``-2`` form (or the next free suffix) is used instead. A
    duplicate reported by promotion — a concurrent writer won the race between
    the scan and the insert — is handled the same way.
    """
    taken = {(row.name, row.universe or "") for row in rows}
    for name in _candidate_names():
        if (name, DEMO_UNIVERSE) in taken:
            continue
        result = promote(
            _spec(name),
            session_id=DEMO_SESSION_ID,
            demo=True,
            store=store,
            runtime_root=runtime_root,
        )
        if result.strategy_id is not None:
            return result.strategy_id
        if not any(gap.code == DUPLICATE_NAME_UNIVERSE for gap in result.gaps):
            detail = result.gaps[0].message if result.gaps else "not registered"
            raise ValueError(f"demo strategy could not be seeded: {detail}")
    raise ValueError(
        f"every demo name up to {DEMO_STRATEGY_NAME}-{_MAX_SUFFIX} is already taken"
    )


def _resolve_jobs(
    job_store: ScheduledResearchJobStore | None, runtime_root: Path | None
) -> ScheduledResearchJobStore:
    """Return the explicit store, the API singleton, or the runtime-root store."""
    if job_store is not None:
        return job_store
    if runtime_root is None:
        return default_job_store()
    root = resolve_runtime_root(runtime_root)
    return ScheduledResearchJobStore(root / _JOBS_DIRNAME / _JOBS_FILENAME)


def _disabled_job(artifact: Artifact, version: str) -> ScheduledResearchJob:
    """Build the demo's cancelled, version-pinned strategy schedule."""
    return ScheduledResearchJob(
        id=artifact.name,
        title=f"Demo schedule: {artifact.name}",
        prompt="",
        schedule=DEMO_SCHEDULE,
        timezone=DEMO_TIMEZONE,
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=artifact.id,
        strategy_version=version,
        status=JobStatus.CANCELLED,
        config={"demo": True},
        run_policy=dict(DEMO_RUN_POLICY),
    )


def seed_demo_strategy(
    *,
    store: SqliteStrategyStore | None = None,
    job_store: ScheduledResearchJobStore | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Create the demo strategy and its disabled schedule, once.

    The strategy is promoted with ``demo=True`` and stays ``needs_work`` — the
    seed never claims an evaluation it did not run. Its version is pinned with
    :func:`snapshot` before the job row is written, and the row is inserted
    directly because ``attach`` requires ``ready``.

    A second call creates nothing: the existing demo row and job are returned
    unchanged. A job id already bound to another strategy is refused rather
    than overwritten.

    Args:
        store: Strategy store; defaults to the process singleton.
        job_store: Schedule store; defaults to the API singleton so the REST
            surfaces see the seeded job.
        runtime_root: Explicit runtime-root override (tests).

    Returns:
        ``{"created", "strategy_id", "version", "job_id", "readiness"}``.
        ``created`` reports whether this call registered the strategy row.

    Raises:
        ValueError: When no demo name is free, the seed's own spec is
            rejected, or the deterministic job id belongs to another strategy.
    """
    jobs = _resolve_jobs(job_store, runtime_root)
    rows = list_strategies(limit=_LIST_LIMIT, offset=0, include_demo=True, store=store)
    existing = _existing_demo(rows)
    if existing is not None:
        created, strategy_id = False, existing.id
    else:
        created, strategy_id = True, _promote_demo(rows, store, runtime_root)

    artifact = get(strategy_id, store=store)
    if artifact is None:  # pragma: no cover - promotion or the scan just read it
        raise ValueError(f"demo strategy {strategy_id!r} disappeared during seeding")
    version = snapshot(strategy_id, store=store, runtime_root=runtime_root)

    job = _disabled_job(artifact, version)
    current = jobs.get(job.id)
    if current is None:
        jobs.upsert(job)
        job_id = job.id
    elif current.strategy_id == strategy_id:
        job_id = current.id
    else:
        raise ValueError(
            f"schedule id {job.id!r} belongs to strategy {current.strategy_id!r}"
        )

    return {
        "created": created,
        "strategy_id": strategy_id,
        "version": version,
        "job_id": job_id,
        "readiness": artifact.readiness,
    }


__all__ = [
    "DEMO_JOB_ID",
    "DEMO_RUN_POLICY",
    "DEMO_SCHEDULE",
    "DEMO_SPEC",
    "DEMO_STRATEGY_ID",
    "DEMO_STRATEGY_NAME",
    "DEMO_TIMEZONE",
    "DEMO_UNIVERSE",
    "seed_demo_strategy",
]
