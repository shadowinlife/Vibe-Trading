"""REST contracts for strategy schedules and legacy-surface isolation.

Every test drives the real ``api_server`` app with the auth dependency
overridden, while the strategy store, the job store and the runtime root are
redirected to ``tmp_path``. The legacy proposal paths are exercised by calling
``proposals`` directly, which is exactly how the IM confirmation flow reaches
them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.strategy_store._shared as shared
from src.api import scheduled_routes
from src.api.security import require_auth
from src.scheduled_research import proposals, strategy_policy
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library.history import (
    RunRecord,
    append_run,
    history_path,
    serialize_run_line,
)
from src.strategy_library.paths import strategy_dir
from src.strategy_library.store import create_draft, record_verdict
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.tools import scheduled_research_tool
from src.tools.scheduled_research_tool import ScheduledResearchTool

_SCHEDULE = "0 9 * * *"
_TIMEZONE = "Asia/Shanghai"
_VERSION = "v1-aaaaaaaa"


@pytest.fixture(autouse=True)
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


def _workflow_spec(name: str = "Momentum Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength after a breakout confirmed by volume.",
        "universe": "cn_a",
        "steps": [
            {
                "id": "s1",
                "action": "load prices",
                "tool": "get_market_data",
                "output": "price table",
            }
        ],
    }


def _code_spec(name: str = "Momentum Code") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Cross-sectional momentum on liquid names.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _make_ready(store: SqliteStrategyStore, runtime_root: Path, spec: dict):
    """Create a draft, supply code sources when needed, and mark it ready."""
    draft = create_draft(spec, store=store, runtime_root=runtime_root)
    if spec["kind"] == "code":
        base = strategy_dir(draft.id, runtime_root=runtime_root)
        (base / "code").mkdir(parents=True, exist_ok=True)
        (base / "config.json").write_text('{"source":"local"}\n', encoding="utf-8")
        (base / "code" / "signal_engine.py").write_text(
            "def generate_signals(frame):\n    return frame\n", encoding="utf-8"
        )
    return record_verdict(
        draft.id,
        {"verdict": "pass", "summary": "executable"},
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )


def _strategy_job(
    job_id: str = "sr-seeded",
    *,
    strategy_id: str = "strat-1",
    next_run_at: int = 3_000_000_000_000,
    created_at: int = 1_700_000_000_000,
    status: JobStatus = JobStatus.PENDING,
) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        title="Scheduled momentum",
        prompt="",
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=strategy_id,
        strategy_version=_VERSION,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        next_run_at=next_run_at,
        status=status,
        created_at=created_at,
        run_policy={
            "overlap": "skip",
            "retention": 20,
            "run_timeout_s": 3600,
            "primary": False,
        },
    )


def _run(job_id: str, index: int, *, status: str = "completed") -> RunRecord:
    return RunRecord(
        run_id=f"run-{index:04d}",
        job_id=job_id,
        strategy_id="strat-1",
        strategy_version=_VERSION,
        started_at=1_700_000_000_000 + index,
        finished_at=1_700_000_001_000 + index,
        status=status,
    )


def _proposal_payload(operation: str, *, job_id: str | None, internal_job: dict | None):
    return {
        "proposal_id": f"srp_{'a' * 32}",
        "operation": operation,
        "status": "pending",
        "session_id": "s-im",
        "created_at": 1,
        "expires_at": 2**62,
        "job_id": job_id,
        "job": None,
        "internal_job": internal_job,
    }


def _write_legacy_proposal(runtime_root: Path, payload: dict) -> str:
    directory = runtime_root / "scheduled_research" / "proposals"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{payload['proposal_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload["proposal_id"]


def _patch_proposal_runtime(
    monkeypatch: pytest.MonkeyPatch, jobs: ScheduledResearchJobStore
) -> None:
    monkeypatch.setattr(proposals, "default_store", lambda: jobs)
    monkeypatch.setattr(proposals, "scheduler_status", lambda: {"executable": True})


# ---------------------------------------------------------------------------
# attach / commit / list / delete
# ---------------------------------------------------------------------------


def test_attach_code_strategy_returns_a_job_and_no_token(
    client: TestClient,
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
) -> None:
    artifact = _make_ready(store, runtime_root, _code_spec())

    response = client.post(
        f"/strategies/{artifact.id}/schedule",
        json={"schedule": _SCHEDULE, "timezone": _TIMEZONE},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_confirmation"] is False
    assert body["proposal_id"] is None
    assert body["confirm_token"] is None
    job = jobs.get(body["job_id"])
    assert job is not None
    assert (job.source_type, job.executor_kind) == ("strategy", "strategy")
    assert job.strategy_version == artifact.artifact_version

    rows = client.get("/strategy-schedules").json()
    assert [row["job_id"] for row in rows] == [job.id]
    assert rows[0]["strategy_id"] == artifact.id
    assert rows[0]["strategy_version"] == artifact.artifact_version
    assert rows[0]["last_runs"] == []


def test_workflow_attach_discloses_the_token_only_in_its_response(
    client: TestClient,
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
) -> None:
    artifact = _make_ready(store, runtime_root, _workflow_spec())

    response = client.post(
        f"/strategies/{artifact.id}/schedule",
        json={"schedule": _SCHEDULE, "timezone": _TIMEZONE},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_confirmation"] is True
    assert body["job_id"] is None
    assert body["proposal_id"].startswith("ssp_")
    assert body["confirm_token"].startswith("scp_")
    assert jobs.load() == {}
    assert body["confirm_token"] not in client.get("/strategy-schedules").text


def test_commit_requires_the_token_and_is_single_use(
    client: TestClient,
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
) -> None:
    artifact = _make_ready(store, runtime_root, _workflow_spec())
    attached = client.post(
        f"/strategies/{artifact.id}/schedule",
        json={"schedule": _SCHEDULE, "timezone": _TIMEZONE},
    ).json()
    url = f"/strategy-schedule-proposals/{attached['proposal_id']}/commit"

    assert client.post(url, json={}).status_code == 403
    assert client.post(url, json={"confirm_token": f"scp_{'0' * 32}"}).status_code == 403
    assert jobs.load() == {}

    committed = client.post(url, json={"confirm_token": attached["confirm_token"]})
    assert committed.status_code == 200, committed.text
    job = jobs.get(committed.json()["job_id"])
    assert job is not None
    assert job.strategy_version == attached["strategy_version"]
    assert committed.json()["last_runs"] == []

    replay = client.post(url, json={"confirm_token": attached["confirm_token"]})
    assert replay.status_code == 403
    assert replay.json()["detail"]["code"] == "token_used"


def test_attach_maps_facade_refusals_onto_their_status(
    client: TestClient, store: SqliteStrategyStore, runtime_root: Path
) -> None:
    draft = create_draft(_workflow_spec(), store=store, runtime_root=runtime_root)
    not_ready = client.post(
        f"/strategies/{draft.id}/schedule",
        json={"schedule": _SCHEDULE, "timezone": _TIMEZONE},
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["code"] == "not_ready"

    unknown = client.post(
        "/strategies/no-such-strategy/schedule",
        json={"schedule": _SCHEDULE, "timezone": _TIMEZONE},
    )
    assert unknown.status_code == 404

    artifact = _make_ready(store, runtime_root, _code_spec())
    bad_schedule = client.post(
        f"/strategies/{artifact.id}/schedule",
        json={"schedule": "0 99 * * *", "timezone": _TIMEZONE},
    )
    assert bad_schedule.status_code == 400
    assert bad_schedule.json()["detail"]["code"] == "invalid_schedule"


def test_schedule_rows_carry_at_most_five_newest_runs(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job("sr-earlier", next_run_at=2_000_000_000_000))
    jobs.upsert(
        _strategy_job(
            "sr-later", next_run_at=2_000_000_100_000, created_at=1_700_000_000_001
        )
    )
    for index in range(7):
        append_run(_run("sr-later", index), retention=20)

    rows = client.get("/strategy-schedules").json()

    assert [row["job_id"] for row in rows] == ["sr-earlier", "sr-later"]
    assert rows[0]["last_runs"] == []
    assert len(rows[1]["last_runs"]) == 5
    assert [run["run_id"] for run in rows[1]["last_runs"]] == [
        "run-0006",
        "run-0005",
        "run-0004",
        "run-0003",
        "run-0002",
    ]


def test_runs_limit_is_clamped_to_5_through_100(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job())
    path = history_path("sr-seeded")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for index in range(120):
            handle.write(serialize_run_line(_run("sr-seeded", index)))

    over = client.get("/strategy-schedules/sr-seeded/runs", params={"limit": 1000})
    assert over.status_code == 200
    assert len(over.json()) == 100

    under = client.get("/strategy-schedules/sr-seeded/runs", params={"limit": 1})
    assert len(under.json()) == 5

    default = client.get("/strategy-schedules/sr-seeded/runs")
    assert len(default.json()) == 20
    assert default.json()[0]["run_id"] == "run-0119"

    assert client.get("/strategy-schedules/sr-missing/runs").status_code == 404


def test_delete_removes_the_job_and_cascades_history(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job())
    append_run(_run("sr-seeded", 0), retention=20)

    assert client.delete("/strategy-schedules/sr-seeded").status_code == 204
    assert jobs.get("sr-seeded") is None
    assert not history_path("sr-seeded").exists()
    assert client.delete("/strategy-schedules/sr-seeded").status_code == 404


def test_run_now_marks_due_and_refuses_a_running_job(
    client: TestClient, jobs: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs.upsert(_strategy_job())
    wakes: list[int] = []
    monkeypatch.setattr(strategy_policy, "_WAKE_TARGET", lambda: wakes.append(1))

    response = client.post("/strategy-schedules/sr-seeded/run-now")

    assert response.status_code == 200, response.text
    assert wakes == [1]
    stored = jobs.get("sr-seeded")
    assert stored is not None
    assert stored.next_run_at < 3_000_000_000_000
    assert stored.status is JobStatus.PENDING

    jobs.upsert(_strategy_job(status=JobStatus.RUNNING))
    before = jobs.get("sr-seeded")
    assert before is not None
    conflict = client.post("/strategy-schedules/sr-seeded/run-now")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "run_in_flight"
    assert jobs.get("sr-seeded").next_run_at == before.next_run_at
    assert client.post("/strategy-schedules/sr-missing/run-now").status_code == 404


# ---------------------------------------------------------------------------
# Legacy surface isolation
# ---------------------------------------------------------------------------


def test_legacy_list_filters_strategy_jobs_before_the_limit(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    for index in range(5):
        jobs.upsert(
            _strategy_job(
                f"sr-mix-{index}",
                created_at=1_700_000_000_100 + index,
                next_run_at=2_000_000_000_000 + index,
            )
        )
    for index in range(5):
        jobs.upsert(
            ScheduledResearchJob(
                id=f"legacy-{index}",
                prompt="summarise the market",
                schedule="60000",
                next_run_at=2_000_000_000_000,
                created_at=1_700_000_000_000 + index,
            )
        )

    response = client.get("/scheduled-runs", params={"limit": 5})

    assert response.status_code == 200
    ids = [job["id"] for job in response.json()]
    assert len(ids) == 5
    assert set(ids) == {f"legacy-{index}" for index in range(5)}


def test_legacy_create_cannot_reuse_a_strategy_job_id(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job())
    before = jobs.path.read_bytes()

    response = client.post(
        "/scheduled-runs",
        json={"id": "sr-seeded", "prompt": "hijack it", "schedule": "60000"},
    )

    assert response.status_code == 409
    assert jobs.path.read_bytes() == before
    assert jobs.get("sr-seeded").executor_kind == "strategy"


def test_legacy_playbook_create_cannot_reuse_a_strategy_job_id(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job())

    response = client.post(
        "/scheduled-runs/playbooks/premarket-brief", json={"id": "sr-seeded"}
    )

    assert response.status_code == 409
    assert jobs.get("sr-seeded").executor_kind == "strategy"


def test_legacy_patch_and_delete_reject_a_strategy_job(
    client: TestClient, jobs: ScheduledResearchJobStore
) -> None:
    jobs.upsert(_strategy_job())

    patched = client.patch("/scheduled-runs/sr-seeded", json={"title": "hijacked"})
    deleted = client.delete("/scheduled-runs/sr-seeded")

    assert patched.status_code == 409
    assert deleted.status_code == 409
    stored = jobs.get("sr-seeded")
    assert stored is not None
    assert stored.title == "Scheduled momentum"


def test_direct_commit_and_propose_cancel_refuse_a_strategy_job(
    client: TestClient,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs.upsert(_strategy_job())
    jobs.upsert(_strategy_job("sr-nohist"))
    append_run(_run("sr-seeded", 0), retention=20)
    history_before = history_path("sr-seeded").read_bytes()
    files_before = sorted(path.name for path in history_path("sr-seeded").parent.glob("*.jsonl"))
    _patch_proposal_runtime(monkeypatch, jobs)

    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        proposals.propose_cancel("sr-seeded")
    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        proposals.propose_cancel("sr-nohist")
    proposal_dir = runtime_root / "scheduled_research" / "proposals"
    assert not proposal_dir.exists() or not list(proposal_dir.glob("srp_*.json"))
    assert not history_path("sr-nohist").exists()

    proposal_id = _write_legacy_proposal(
        runtime_root, _proposal_payload("cancel", job_id="sr-seeded", internal_job=None)
    )
    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        proposals.commit_proposal(proposal_id)

    assert jobs.get("sr-seeded") is not None
    assert history_path("sr-seeded").read_bytes() == history_before
    assert sorted(path.name for path in history_path("sr-seeded").parent.glob("*.jsonl")) == files_before
    assert client.post(f"/scheduled-runs/proposals/{proposal_id}/commit").status_code == 409


def test_legacy_create_proposal_carrying_a_strategy_job_is_refused(
    jobs: ScheduledResearchJobStore, runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _strategy_job()
    internal = job.to_dict()
    internal.pop("in_flight", None)
    _patch_proposal_runtime(monkeypatch, jobs)
    proposal_id = _write_legacy_proposal(
        runtime_root,
        _proposal_payload("create", job_id=None, internal_job=internal),
    )

    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        proposals.commit_proposal(proposal_id)

    assert jobs.load() == {}
    assert not jobs.path.exists()


def test_prompt_draft_reusing_a_strategy_job_id_keeps_job_and_history(
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs.upsert(_strategy_job())
    append_run(_run("sr-seeded", 0), retention=20)
    store_before = jobs.path.read_bytes()
    history_before = history_path("sr-seeded").read_bytes()
    _patch_proposal_runtime(monkeypatch, jobs)

    draft = {
        "id": "sr-seeded",
        "title": "Replacement",
        "source": {"kind": "prompt", "prompt": "replace the strategy schedule"},
        "schedule": {"expression": "60000"},
        "delivery": {"mode": "in_app"},
    }
    proposal = proposals.propose_create(draft)

    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        proposals.commit_proposal(proposal["proposal_id"])

    assert jobs.path.read_bytes() == store_before
    assert history_path("sr-seeded").read_bytes() == history_before
    assert jobs.get("sr-seeded").title == "Scheduled momentum"


def test_agent_tool_hides_strategy_jobs_and_refuses_their_cancel(
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduled_research_tool, "default_store", lambda: jobs)
    _patch_proposal_runtime(monkeypatch, jobs)
    jobs.upsert(
        ScheduledResearchJob(
            id="legacy-visible", prompt="summarise", schedule="60000"
        )
    )
    jobs.upsert(_strategy_job())

    payload = json.loads(ScheduledResearchTool().execute(action="list_jobs"))

    assert [job["id"] for job in payload["jobs"]] == ["legacy-visible"]
    with pytest.raises(proposals.ProposalError, match="strategy schedule"):
        ScheduledResearchTool().execute(action="propose_cancel", job_id="sr-seeded")
    proposal_dir = runtime_root / "scheduled_research" / "proposals"
    assert not proposal_dir.exists() or not list(proposal_dir.glob("srp_*.json"))


def test_store_list_jobs_excludes_before_the_limit(
    jobs: ScheduledResearchJobStore,
) -> None:
    for index in range(5):
        jobs.upsert(
            _strategy_job(
                f"sr-mix-{index}", created_at=1_700_000_000_100 + index
            )
        )
    for index in range(5):
        jobs.upsert(
            ScheduledResearchJob(
                id=f"legacy-{index}",
                prompt="summarise",
                schedule="60000",
                created_at=1_700_000_000_000 + index,
            )
        )

    page = jobs.list_jobs(limit=5, exclude_source_type="strategy")

    assert len(page) == 5
    assert all(job.source_type != "strategy" for job in page)
