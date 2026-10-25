"""Contracts for the demo seed: one labelled strategy, one disabled schedule.

Every test points both stores and the runtime root at ``tmp_path``, so the seed
runs through the real promotion, snapshot and job-store code paths. The REST
test uses the assembled app, which is what proves the static ``/demo`` path is
not swallowed by ``GET /strategies/{id}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.strategy_store._shared as shared
from src.api import scheduled_routes
from src.api import strategy_library_routes
from src.api.security import require_auth
from src.scheduled_research.models import JobStatus
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library.demo import (
    DEMO_JOB_ID,
    DEMO_SCHEDULE,
    DEMO_SPEC,
    DEMO_STRATEGY_NAME,
    seed_demo_strategy,
)
from src.strategy_library.history import read_runs
from src.strategy_library.paths import version_spec_path
from src.strategy_library.promote import promote
from src.strategy_library.scheduling import list_schedules
from src.strategy_library.spec import ALLOWED_STRATEGY_TOOLS, validate_spec
from src.strategy_library.store import get, list_strategies
from src.strategy_store.sqlite_store import SqliteStrategyStore

_PERFORMANCE_KEYS = (
    "sharpe",
    "annual_return",
    "total_return",
    "max_drawdown",
    "calmar",
)


@pytest.fixture
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every runtime-root resolution at a per-test directory."""
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
    return root


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteStrategyStore:
    """Strategy store bound to the process singleton every facade call reads."""
    bound = SqliteStrategyStore(tmp_path / "strategy_store.db")
    monkeypatch.setattr(shared, "_store", bound)
    return bound


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScheduledResearchJobStore:
    """Schedule store behind the API singleton the seed and routes resolve."""
    isolated = ScheduledResearchJobStore(path=tmp_path / "scheduled_jobs.json")
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", isolated)
    return isolated


@pytest.fixture
def client() -> TestClient:
    """TestClient for the assembled app with the auth dependency overridden."""
    api_server.app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(api_server.app, client=("127.0.0.1", 50000))
    finally:
        api_server.app.dependency_overrides.clear()


def _seed(
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
) -> dict:
    return seed_demo_strategy(store=store, job_store=jobs, runtime_root=runtime_root)


def _all_strategies(store: SqliteStrategyStore) -> list:
    return list_strategies(limit=100, offset=0, include_demo=True, store=store)


def test_two_seeds_create_one_strategy_and_one_schedule(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    first = _seed(store, jobs, runtime_root)
    second = _seed(store, jobs, runtime_root)

    assert first["created"] is True
    assert second["created"] is False
    assert first["strategy_id"] == second["strategy_id"]
    assert first["version"] == second["version"]
    assert first["job_id"] == second["job_id"] == DEMO_JOB_ID
    assert first["readiness"] == second["readiness"] == "needs_work"

    rows = _all_strategies(store)
    assert len(rows) == 1
    assert rows[0].demo is True
    assert len(jobs.load()) == 1


def test_seed_pins_the_version_and_ships_a_cancelled_primary_schedule(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    result = _seed(store, jobs, runtime_root)
    artifact = get(result["strategy_id"], store=store)
    assert artifact is not None
    job = jobs.load()[result["job_id"]]

    assert artifact.demo is True
    assert artifact.kind == "workflow"
    assert artifact.readiness == "needs_work"
    assert artifact.evaluator_verdict is None
    assert result["version"] == artifact.artifact_version
    assert job.strategy_version == result["version"]
    assert version_spec_path(
        artifact.id, result["version"], runtime_root=runtime_root
    ).is_file()

    assert job.executor_kind == "strategy"
    assert job.source_type == "strategy"
    assert job.strategy_id == artifact.id
    assert job.status is JobStatus.CANCELLED
    assert job.run_policy["primary"] is True
    assert job.run_policy["overlap"] == "skip"
    assert job.schedule == DEMO_SCHEDULE

    # A cancelled job is not in service: the enabled-only read must skip it and
    # its id must stay out of the per-strategy schedule badge.
    assert list_schedules(job_store=jobs) == []


def test_seed_job_round_trips_through_the_store_without_quarantine(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    result = _seed(store, jobs, runtime_root)
    first_read = jobs.load()
    assert set(first_read) == {result["job_id"]}

    independent = ScheduledResearchJobStore(path=jobs.path)
    second_read = independent.load()
    assert second_read[result["job_id"]].to_dict() == first_read[result["job_id"]].to_dict()
    assert not list(jobs.path.parent.glob(f"{jobs.path.name}.corrupt-*"))


def test_seed_records_no_run_history_and_no_bench(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    result = _seed(store, jobs, runtime_root)

    assert read_runs(result["job_id"], runtime_root=runtime_root) == []
    assert list(store.get_bench_history(result["strategy_id"])) == []


def test_demo_spec_is_a_four_step_allowlisted_workflow() -> None:
    assert validate_spec(DEMO_SPEC) == []
    assert DEMO_SPEC["kind"] == "workflow"
    assert DEMO_SPEC["name"] == DEMO_STRATEGY_NAME

    steps = DEMO_SPEC["steps"]
    assert len(steps) == 4
    tools = [step["tool"] for step in steps]
    assert set(tools) <= ALLOWED_STRATEGY_TOOLS
    assert "run_swarm" not in tools
    assert [step["id"] for step in steps] == [
        "concept-upstream-map",
        "candidate-pool",
        "technical-screen",
        "backtest-verify",
    ]
    assert DEMO_SPEC["output_contract"]["format"] == "markdown_table"
    assert DEMO_SPEC["output_contract"]["required_fields"]
    assert DEMO_SPEC["preconditions"]
    assert DEMO_SPEC["failure_handling"].strip()

    blob = json.dumps(DEMO_SPEC)
    for key in _PERFORMANCE_KEYS:
        assert key not in blob


def test_seed_response_carries_exactly_the_seed_fields(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    result = _seed(store, jobs, runtime_root)

    assert set(result) == {"created", "strategy_id", "version", "job_id", "readiness"}
    blob = json.dumps(result)
    assert "sharpe" not in blob
    assert "annual_return" not in blob


def test_seed_never_overwrites_a_real_strategy_that_owns_the_name(
    store: SqliteStrategyStore, jobs: ScheduledResearchJobStore, runtime_root: Path
) -> None:
    real_spec = dict(DEMO_SPEC, name=DEMO_STRATEGY_NAME)
    promoted = promote(
        real_spec, session_id="real", demo=False, store=store, runtime_root=runtime_root
    )
    assert promoted.strategy_id is not None
    before = get(promoted.strategy_id, store=store)
    assert before is not None

    first = _seed(store, jobs, runtime_root)
    second = _seed(store, jobs, runtime_root)

    assert first["created"] is True
    assert second["created"] is False
    assert first["strategy_id"] == second["strategy_id"]
    assert first["strategy_id"] != promoted.strategy_id
    assert first["job_id"] == f"{DEMO_STRATEGY_NAME}-2"

    demo = get(first["strategy_id"], store=store)
    assert demo is not None
    assert demo.name == f"{DEMO_STRATEGY_NAME}-2"
    assert demo.demo is True

    after = get(promoted.strategy_id, store=store)
    assert after is not None
    assert after.demo is False
    assert after.artifact_version == before.artifact_version
    assert after.spec == before.spec
    assert len(jobs.load()) == 1


def test_post_strategy_demo_answers_200_and_is_not_shadowed_by_the_id_route(
    client: TestClient,
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
) -> None:
    paths = [route.path for route in strategy_library_routes.router.routes]
    assert paths.index("/strategies/demo") < paths.index("/strategies/{strategy_id}")

    response = client.post("/strategies/demo")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["readiness"] == "needs_work"
    assert body["job_id"] == DEMO_JOB_ID
    assert body["strategy_id"]
    assert "sharpe" not in response.text
    assert "annual_return" not in response.text

    again = client.post("/strategies/demo")
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["strategy_id"] == body["strategy_id"]

    listing = client.get("/strategies", params={"include_demo": True}).json()
    assert sum(1 for item in listing["items"] if item["demo"]) == 1

    detail = client.get(f"/strategies/{body['strategy_id']}").json()
    assert detail["demo"] is True
    assert detail["readiness"] == "needs_work"
    assert detail["schedule"]["count"] == 0
    assert detail["run_history"] == []
