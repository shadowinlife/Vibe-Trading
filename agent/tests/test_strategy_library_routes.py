"""REST contracts for the strategy library surface.

Every request runs through the real ``api_server`` app so the router
registration and the auth dependency are covered, with the strategy store,
schedule store and runtime root redirected to ``tmp_path``. Code fixtures are
real-schema backtest runs, so promotion and the deterministic gate both run
end to end.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.strategy_store._shared as shared
from src.api import scheduled_routes
from src.api import strategy_library_routes
from src.api.security import require_auth
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library.history import RunRecord, append_run
from src.strategy_store.models import Artifact, ArtifactType
from src.strategy_store.sqlite_store import SqliteStrategyStore

_BARS = 40
_TRADE_BAR_INDICES = (4, 8, 12)
_BENCH_COLUMNS = (
    "final_value",
    "total_return",
    "annual_return",
    "max_drawdown",
    "sharpe",
    "calmar",
    "trade_count",
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
    """Strategy store bound to the process singleton every library call reads."""
    bound = SqliteStrategyStore(tmp_path / "strategy_store.db")
    monkeypatch.setattr(shared, "_store", bound)
    return bound


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScheduledResearchJobStore:
    """Schedule store behind the API singleton the routes resolve."""
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


def _workflow_spec(name: str, universe: str = "cn_a") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength confirmed by volume.",
        "universe": universe,
        "inputs": [
            {"name": "prices", "source": "get_market_data", "required": True, "missing_policy": "stop"}
        ],
        "preconditions": ["the exchange is open"],
        "steps": [
            {"id": "s1", "action": "load prices", "tool": "get_market_data", "output": "prices"},
            {"id": "s2", "action": "score momentum", "tool": "technical_indicators", "output": "score"},
            {"id": "s3", "action": "summarise", "tool": "web_search", "output": "markdown table"},
        ],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "score"],
            "destination": "chat",
        },
        "failure_handling": "Report the failing step and stop.",
    }


def _code_spec(name: str = "Momentum Code") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Cross-sectional momentum.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _create_workflow(client: TestClient, name: str, *, demo: bool = False) -> dict:
    response = client.post("/strategies", json={"spec": _workflow_spec(name), "demo": demo})
    assert response.status_code == 201, response.text
    return response.json()


def _bar_date(index: int) -> date:
    return date(2026, 1, 1) + timedelta(days=index)


def _write_run(root: Path, name: str = "source") -> Path:
    """One promotable backtest run directory in the real artifact layout."""
    run_dir = root / "runs" / name
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)

    values = ["1000000.0", "0.012", "0.11", "-0.05", "1.4", "2.2", "3"]
    (artifacts / "metrics.csv").write_text(
        ",".join(_BENCH_COLUMNS) + "\n" + ",".join(values) + "\n", encoding="utf-8"
    )

    equity = ["timestamp,equity,benchmark_equity"]
    for index in range(_BARS):
        equity.append(
            f"{_bar_date(index).isoformat()},{1_000_000.0 * (1.0 + 0.001 * index):.6f},100.000000"
        )
    (artifacts / "equity.csv").write_text("\n".join(equity) + "\n", encoding="utf-8")

    trades = ["timestamp,code,side,price,qty,reason,pnl,holding_days,return_pct,event"]
    for index in _TRADE_BAR_INDICES:
        day = _bar_date(index).isoformat()
        trades.append(f"{day},TEST.SH,buy,10.0,100.0,signal,0.0,0,0.0,entry")
        trades.append(f"{day},TEST.SH,sell,10.5,100.0,signal,50.0,0,0.5,exit")
    (artifacts / "trades.csv").write_text("\n".join(trades) + "\n", encoding="utf-8")

    (run_dir / "state.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "source": "local",
                "codes": ["TEST.SH"],
                "start_date": "2026-01-01",
                "end_date": "2026-02-28",
            }
        ),
        encoding="utf-8",
    )
    code = run_dir / "code"
    code.mkdir()
    (code / "signal_engine.py").write_text(
        "def generate_signals(bars):\n    return []\n", encoding="utf-8"
    )
    return run_dir


def _job(
    jobs: ScheduledResearchJobStore,
    *,
    job_id: str,
    strategy_id: str,
    strategy_version: str,
    status: JobStatus = JobStatus.PENDING,
    next_run_at: int = 1_000,
) -> ScheduledResearchJob:
    job = ScheduledResearchJob(
        id=job_id,
        prompt="",
        schedule="0 9 * * *",
        status=status,
        created_at=1,
        next_run_at=next_run_at,
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )
    jobs.upsert(job)
    return job


def _record(
    *,
    run_id: str,
    job_id: str,
    strategy_id: str,
    strategy_version: str,
    status: str,
    started_at: int,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        job_id=job_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        started_at=started_at,
        finished_at=started_at + 2_000,
        status=status,
    )


def _today_ms() -> int:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)


def test_list_pagination_and_filters(client: TestClient, runtime_root: Path, store, jobs):
    _create_workflow(client, "Momentum Workflow")
    _create_workflow(client, "Value Workflow")
    _create_workflow(client, "Demo Workflow", demo=True)

    listing = client.get("/strategies")
    assert listing.status_code == 200
    assert listing.json()["total"] == 3
    assert len(listing.json()["items"]) == 3
    item = listing.json()["items"][0]
    assert set(item) == {
        "id", "name", "kind", "readiness", "version", "universe", "demo",
        "evidence", "schedule", "last_run",
    }
    assert item["last_run"] is None
    assert item["schedule"] == {"count": 0, "next_run_at": None}

    assert client.get("/strategies", params={"kind": "workflow"}).json()["total"] == 3
    assert client.get("/strategies", params={"readiness": "needs_work"}).json()["total"] == 3
    assert client.get("/strategies", params={"q": "momentum"}).json()["total"] == 1
    assert client.get("/strategies", params={"include_demo": "false"}).json()["total"] == 2

    page = client.get("/strategies", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["offset"] == 1


def test_post_code_strategy_returns_ready_detail(
    client: TestClient, runtime_root: Path, store, jobs
):
    run_dir = _write_run(runtime_root)

    response = client.post(
        "/strategies", json={"spec": _code_spec(), "run_dir": str(run_dir)}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "code"
    assert body["readiness"] == "ready"
    assert body["version"]
    assert body["evaluator_verdict"]["passed"] is True
    assert body["gaps"] == []
    assert body["evidence"]["has_bench"] is True
    assert body["evidence"]["quality"] == "high"
    assert body["evidence"]["last_verified"]
    assert body["config_summary"]["keys"] >= 1
    assert body["config_summary"]["source"] == "local"
    assert body["signal_engine_path"].endswith("code/signal_engine.py")
    assert (store.get_artifact(body["id"]) or None) is not None

    detail = client.get(f"/strategies/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["readiness"] == "ready"
    assert detail.json()["bench_history"][0]["sharpe"] == 1.4


@pytest.mark.parametrize(
    "field", ["readiness", "evaluator_verdict", "previous_verdict", "validation_status", "demo"]
)
def test_patch_rejects_gate_fields_before_touching_the_store(
    client: TestClient, runtime_root: Path, store, jobs, field: str
):
    created = _create_workflow(client, "Guarded Workflow")
    before = store.get_artifact(created["id"])
    assert before is not None

    response = client.patch(
        f"/strategies/{created['id']}",
        json={"spec": _workflow_spec("Guarded Workflow"), field: "ready"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "gate_field_not_patchable"
    after = store.get_artifact(created["id"])
    assert after is not None
    assert after.readiness == before.readiness == "needs_work"
    assert after.artifact_version == before.artifact_version


def test_patch_spec_bumps_version_and_clears_verdict(
    client: TestClient, runtime_root: Path, store, jobs
):
    run_dir = _write_run(runtime_root)
    created = client.post(
        "/strategies", json={"spec": _code_spec(), "run_dir": str(run_dir)}
    ).json()
    old_version = created["version"]
    assert created["readiness"] == "ready"

    changed = _code_spec("Momentum Code")
    changed["thesis"] = "A revised, testable momentum thesis."
    response = client.patch(f"/strategies/{created['id']}", json={"spec": changed})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] != old_version
    assert body["readiness"] == "needs_work"
    assert body["evaluator_verdict"] is None
    assert body["previous_verdict"]["passed"] is True
    assert body["spec"]["thesis"] == changed["thesis"]


def test_patch_unknown_strategy_is_404(client: TestClient, runtime_root: Path, store, jobs):
    response = client.patch("/strategies/art_missing", json={"spec": _code_spec()})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_summary_route_is_not_shadowed_by_detail(client: TestClient, runtime_root: Path, store, jobs):
    response = client.get("/strategies/summary")
    assert response.status_code == 200
    assert set(response.json()) == {
        "active_count", "runs_today", "failed_today", "next_run_at", "success_rate_7d"
    }


def test_summary_formulas_are_deterministic(client: TestClient, runtime_root: Path, store, jobs):
    first = _create_workflow(client, "First Workflow")
    second = _create_workflow(client, "Second Workflow")
    third = _create_workflow(client, "Third Workflow")
    _job(jobs, job_id="job-a", strategy_id=first["id"], strategy_version=first["version"], next_run_at=1_000)
    _job(jobs, job_id="job-b", strategy_id=second["id"], strategy_version=second["version"], next_run_at=500)
    _job(
        jobs,
        job_id="job-c",
        strategy_id=third["id"],
        strategy_version=third["version"],
        status=JobStatus.CANCELLED,
        next_run_at=100,
    )

    today = _today_ms()
    yesterday = today - 86_400_000
    eight_days = today - 8 * 86_400_000
    append_run(
        _record(run_id="run-a", job_id="job-a", strategy_id=first["id"], strategy_version=first["version"], status="completed", started_at=today + 1_000),
        runtime_root=runtime_root,
    )
    append_run(
        _record(run_id="run-b", job_id="job-b", strategy_id=second["id"], strategy_version=second["version"], status="failed", started_at=today + 2_000),
        runtime_root=runtime_root,
    )
    append_run(
        _record(run_id="run-c", job_id="job-b", strategy_id=second["id"], strategy_version=second["version"], status="skipped", started_at=today + 3_000),
        runtime_root=runtime_root,
    )
    append_run(
        _record(run_id="run-d", job_id="job-a", strategy_id=first["id"], strategy_version=first["version"], status="completed", started_at=yesterday + 1_000),
        runtime_root=runtime_root,
    )
    append_run(
        _record(run_id="run-e", job_id="job-a", strategy_id=first["id"], strategy_version=first["version"], status="failed", started_at=eight_days + 1_000),
        runtime_root=runtime_root,
    )

    body = client.get("/strategies/summary").json()

    assert body["active_count"] == 2
    assert body["next_run_at"] == 500
    assert body["runs_today"] == 3
    assert body["failed_today"] == 1
    assert body["success_rate_7d"] == pytest.approx(2 / 3, abs=1e-4)


def test_runs_merge_two_jobs_newest_first(client: TestClient, runtime_root: Path, store, jobs):
    created = _create_workflow(client, "History Workflow")
    for index, job_id in enumerate(("job-a", "job-a", "job-b", "job-b")):
        append_run(
            _record(
                run_id=f"run-{index}",
                job_id=job_id,
                strategy_id=created["id"],
                strategy_version=created["version"],
                status="completed" if index % 2 == 0 else "failed",
                started_at=_today_ms() + index * 1_000,
            ),
            runtime_root=runtime_root,
        )

    response = client.get(f"/strategies/{created['id']}/runs")

    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 4
    assert {run["job_id"] for run in runs} == {"job-a", "job-b"}
    assert [run["started_at"] for run in runs] == sorted(
        (run["started_at"] for run in runs), reverse=True
    )
    assert len(client.get(f"/strategies/{created['id']}/runs", params={"limit": 2}).json()) == 2
    assert client.get("/strategies/art_missing/runs").status_code == 404


def test_legacy_row_evaluate_is_edit_required_without_verdict(
    client: TestClient, runtime_root: Path, store, jobs
):
    legacy = Artifact(
        id="",
        type=ArtifactType.STRATEGY,
        name="Legacy Code",
        universe="cn_a",
        kind="code",
        spec=_code_spec("Legacy Code"),
        artifact_version=None,
        spec_sha256=None,
    )
    strategy_id = store.register_artifact(legacy)

    response = client.post(f"/strategies/{strategy_id}/evaluate")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "edit_required"
    stored = store.get_artifact(strategy_id)
    assert stored is not None
    assert stored.evaluator_verdict is None
    assert stored.readiness == "draft"


def test_duplicate_promotion_is_409(client: TestClient, runtime_root: Path, store, jobs):
    _create_workflow(client, "Only Once")
    response = client.post("/strategies", json={"spec": _workflow_spec("Only Once")})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate_name_universe"


def test_invalid_spec_is_422_with_gaps(client: TestClient, runtime_root: Path, store, jobs):
    broken = _workflow_spec("Broken Workflow")
    broken["steps"] = broken["steps"][:1]

    response = client.post("/strategies", json={"spec": broken})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_strategy"
    assert any(gap["code"] == "too_few_steps" for gap in detail["gaps"])
    assert store.list_artifacts() == []


def test_provider_failure_never_leaks_to_the_client(
    client: TestClient, runtime_root: Path, store, jobs, monkeypatch: pytest.MonkeyPatch
):
    created = _create_workflow(client, "Provider Workflow")

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret-provider-token")

    monkeypatch.setattr(strategy_library_routes.review, "evaluate_workflow", boom)

    response = client.post(f"/strategies/{created['id']}/evaluate")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "evaluator_unavailable"
    assert "secret-provider-token" not in response.text


def test_summary_route_is_registered_before_the_id_route():
    paths = list(api_server.app.openapi()["paths"])
    assert paths.index("/strategies/summary") < paths.index("/strategies/{strategy_id}")
