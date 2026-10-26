"""Contract tests for attaching, confirming and detaching strategy schedules.

Every test runs offline: the strategy library and the scheduled-job store are
both pointed at ``tmp_path`` through their explicit seams, and the legacy
proposal surface gets its runtime root from ``VIBE_TRADING_HOME``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.scheduled_research import proposals as legacy_proposals
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library import paths as library_paths
from src.strategy_library import scheduling
from src.strategy_library.history import RunRecord, append_run, history_path
from src.strategy_library.scheduling import (
    PROPOSAL_TTL_MS,
    AttachResult,
    ScheduleError,
    attach,
    commit,
    detach,
    list_schedules,
    request_schedule,
)
from src.strategy_library.store import (
    create_draft,
    record_verdict,
    snapshot,
    update_draft,
)
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.tools.strategy_library_tool import ScheduleStrategyTool

_SCHEDULE = "0 9 * * *"
_TIMEZONE = "Asia/Shanghai"


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


def _make_store(tmp_path: Path) -> SqliteStrategyStore:
    return SqliteStrategyStore(tmp_path / "strategy_store.db")


def _make_ready(
    store: SqliteStrategyStore, runtime_root: Path, spec: dict
):
    """Create a draft, supply code sources when needed, and mark it ready."""
    draft = create_draft(spec, store=store, runtime_root=runtime_root)
    if spec["kind"] == "code":
        base = library_paths.strategy_dir(draft.id, runtime_root=runtime_root)
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


def _proposal_path(runtime_root: Path, proposal_id: str) -> Path:
    return (
        runtime_root
        / "scheduled_research"
        / "strategy_proposals"
        / f"{proposal_id}.json"
    )


def _attach_workflow(
    store: SqliteStrategyStore,
    jobs: ScheduledResearchJobStore,
    runtime_root: Path,
    *,
    name: str = "Momentum Workflow",
):
    artifact = _make_ready(store, runtime_root, _workflow_spec(name))
    return artifact, attach(
        artifact.id,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )


# ---------------------------------------------------------------------------
# attach: readiness gate and version pinning
# ---------------------------------------------------------------------------


def test_attach_rejects_a_non_ready_strategy(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    draft = create_draft(_workflow_spec(), store=store, runtime_root=runtime_root)

    with pytest.raises(ScheduleError) as excinfo:
        attach(
            draft.id,
            schedule=_SCHEDULE,
            timezone=_TIMEZONE,
            store=store,
            job_store=jobs,
            runtime_root=runtime_root,
        )

    assert (excinfo.value.status, excinfo.value.code) == (409, "not_ready")
    assert jobs.load() == {}
    assert not scheduling.strategy_proposals_dir(runtime_root=runtime_root).exists()


def test_attach_rejects_an_unknown_strategy(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")

    with pytest.raises(LookupError):
        attach(
            "no-such-strategy",
            schedule=_SCHEDULE,
            timezone=_TIMEZONE,
            store=store,
            job_store=jobs,
            runtime_root=tmp_path / "runtime",
        )
    assert jobs.load() == {}


def test_code_attach_pins_the_snapshot_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())

    result = attach(
        artifact.id,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        run_timeout_s=120,
        retention=7,
        primary=True,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )

    assert isinstance(result, AttachResult)
    assert result.requires_confirmation is False
    assert result.proposal_id is None
    assert result.confirm_token is None
    assert result.job_id is not None
    job = jobs.get(result.job_id)
    assert job is not None
    pinned = snapshot(artifact.id, store=store, runtime_root=runtime_root)
    assert job.strategy_version == pinned == result.strategy_version
    assert library_paths.version_dir(artifact.id, pinned, runtime_root=runtime_root).is_dir()
    assert (job.source_type, job.executor_kind) == ("strategy", "strategy")
    assert job.strategy_id == artifact.id
    assert job.run_policy == {
        "overlap": "skip",
        "retention": 7,
        "run_timeout_s": 120,
        "primary": True,
    }
    assert "primary" not in job.config
    assert job.next_run_at > job.created_at
    assert not scheduling.strategy_proposals_dir(runtime_root=runtime_root).exists()


def test_second_primary_schedule_conflicts_until_it_is_disabled(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())
    kwargs = dict(store=store, job_store=jobs, runtime_root=runtime_root)

    first = attach(
        artifact.id, schedule=_SCHEDULE, timezone=_TIMEZONE, primary=True, **kwargs
    )
    with pytest.raises(ScheduleError) as excinfo:
        attach(
            artifact.id, schedule=_SCHEDULE, timezone=_TIMEZONE, primary=True, **kwargs
        )
    assert (excinfo.value.status, excinfo.value.code) == (409, "primary_exists")
    assert len(jobs.load()) == 1

    secondary = attach(
        artifact.id, schedule="3600000", timezone=None, primary=False, **kwargs
    )
    assert jobs.get(secondary.job_id) is not None

    cancelled = jobs.get(first.job_id)
    assert cancelled is not None
    cancelled.status = JobStatus.CANCELLED
    jobs.upsert(cancelled, validate=False)

    replacement = attach(
        artifact.id, schedule=_SCHEDULE, timezone=_TIMEZONE, primary=True, **kwargs
    )
    assert jobs.get(replacement.job_id).run_policy["primary"] is True


# ---------------------------------------------------------------------------
# Workflow proposals: token hashing, directory and namespace isolation
# ---------------------------------------------------------------------------


def test_workflow_attach_writes_a_hashed_proposal_only(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact, result = _attach_workflow(store, jobs, runtime_root)

    assert result.requires_confirmation is True
    assert result.job_id is None
    assert jobs.load() == {}
    assert scheduling.PROPOSAL_ID_RE.fullmatch(result.proposal_id)
    assert scheduling.CONFIRM_TOKEN_RE.fullmatch(result.confirm_token)

    proposal_file = _proposal_path(runtime_root, result.proposal_id)
    assert proposal_file.is_file()
    raw = proposal_file.read_text(encoding="utf-8")
    assert result.confirm_token not in raw
    payload = json.loads(raw)
    assert payload["operation"] == "strategy_schedule"
    assert payload["status"] == "pending"
    assert payload["used_at"] is None
    assert payload["strategy_id"] == artifact.id
    assert payload["strategy_version"] == artifact.artifact_version == result.strategy_version
    assert payload["spec_sha256"] == artifact.spec_sha256
    assert payload["expires_at"] - payload["created_at"] == PROPOSAL_TTL_MS
    assert payload["confirm_token_sha256"] == hashlib.sha256(
        result.confirm_token.encode("utf-8")
    ).hexdigest()
    assert "in_flight" not in payload["job"]
    assert payload["job"]["strategy_version"] == artifact.artifact_version
    assert not (runtime_root / "scheduled_research" / "proposals").exists()


def test_latest_pending_for_session_ignores_strategy_proposals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    _artifact, result = _attach_workflow(store, jobs, tmp_path)

    # Make the strategy proposal look like a legacy pending proposal bound to
    # the same session; only the directory/namespace keeps it invisible.
    proposal_file = _proposal_path(tmp_path, result.proposal_id)
    payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    payload["session_id"] = "session-1"
    proposal_file.write_text(json.dumps(payload), encoding="utf-8")

    legacy_id = f"srp_{'b' * 32}"
    legacy_dir = tmp_path / "scheduled_research" / "proposals"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / f"{legacy_id}.json").write_text(
        json.dumps(
            {
                "proposal_id": legacy_id,
                "operation": "create",
                "status": "pending",
                "session_id": "session-1",
                "created_at": 1,
                "expires_at": 2**62,
                "job": None,
            }
        ),
        encoding="utf-8",
    )

    found = legacy_proposals.latest_pending_for_session("session-1")
    assert found is not None
    assert found["proposal_id"] == legacy_id


def test_legacy_commit_proposal_rejects_a_strategy_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    strategy_proposal_id = f"ssp_{'a' * 32}"

    assert legacy_proposals._ID_RE.fullmatch(strategy_proposal_id) is None
    with pytest.raises(legacy_proposals.ProposalError, match="invalid"):
        legacy_proposals.commit_proposal(strategy_proposal_id)

    # A legacy id does reach the operation dispatch, which has no strategy case.
    monkeypatch.setattr(
        legacy_proposals, "scheduler_status", lambda: {"executable": True}
    )
    legacy_id = f"srp_{'c' * 32}"
    legacy_dir = tmp_path / "scheduled_research" / "proposals"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / f"{legacy_id}.json").write_text(
        json.dumps(
            {
                "proposal_id": legacy_id,
                "operation": "strategy_schedule",
                "status": "pending",
                "created_at": 1,
                "expires_at": 2**62,
                "job": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        legacy_proposals.ProposalError, match="unsupported proposal operation"
    ):
        legacy_proposals.commit_proposal(legacy_id)


# ---------------------------------------------------------------------------
# commit: token validation, expiry, replay and version binding
# ---------------------------------------------------------------------------


def test_workflow_commit_without_a_token_is_forbidden(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    _artifact, result = _attach_workflow(store, jobs, runtime_root)
    kwargs = dict(store=store, job_store=jobs, runtime_root=runtime_root)

    for bad_token in ("", None, f"scp_{'0' * 32}"):
        with pytest.raises(ScheduleError) as excinfo:
            commit(result.proposal_id, bad_token, **kwargs)
        assert (excinfo.value.status, excinfo.value.code) == (403, "invalid_token")
    assert jobs.load() == {}

    job = commit(result.proposal_id, result.confirm_token, **kwargs)
    assert jobs.get(job.id) is not None
    assert job.strategy_version == result.strategy_version


def test_commit_is_single_use(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    _artifact, result = _attach_workflow(store, jobs, runtime_root)
    kwargs = dict(store=store, job_store=jobs, runtime_root=runtime_root)

    job = commit(result.proposal_id, result.confirm_token, **kwargs)
    payload = json.loads(
        _proposal_path(runtime_root, result.proposal_id).read_text(encoding="utf-8")
    )
    assert payload["status"] == "committed"
    assert payload["committed_job_id"] == job.id
    assert isinstance(payload["used_at"], int)

    with pytest.raises(ScheduleError) as excinfo:
        commit(result.proposal_id, result.confirm_token, **kwargs)
    assert (excinfo.value.status, excinfo.value.code) == (403, "token_used")
    assert len(jobs.load()) == 1


def test_commit_rejects_an_expired_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    _artifact, result = _attach_workflow(store, jobs, runtime_root)
    payload = json.loads(
        _proposal_path(runtime_root, result.proposal_id).read_text(encoding="utf-8")
    )
    monkeypatch.setattr(scheduling, "_now_ms", lambda: payload["expires_at"] + 1)

    with pytest.raises(ScheduleError) as excinfo:
        commit(
            result.proposal_id,
            result.confirm_token,
            store=store,
            job_store=jobs,
            runtime_root=runtime_root,
        )
    assert (excinfo.value.status, excinfo.value.code) == (409, "expired")
    assert jobs.load() == {}


def test_commit_rejects_a_changed_strategy_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact, result = _attach_workflow(store, jobs, runtime_root)

    edited = _workflow_spec()
    edited["thesis"] = "A different thesis that bumps the version."
    update_draft(artifact.id, edited, store=store, runtime_root=runtime_root)

    with pytest.raises(ScheduleError) as excinfo:
        commit(
            result.proposal_id,
            result.confirm_token,
            store=store,
            job_store=jobs,
            runtime_root=runtime_root,
        )
    assert (excinfo.value.status, excinfo.value.code) == (409, "version_changed")
    payload = json.loads(
        _proposal_path(runtime_root, result.proposal_id).read_text(encoding="utf-8")
    )
    assert payload["status"] == "pending"
    assert payload["used_at"] is None
    assert jobs.load() == {}


def test_commit_rejects_a_tampered_spec_digest(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    _artifact, result = _attach_workflow(store, jobs, runtime_root)
    proposal_file = _proposal_path(runtime_root, result.proposal_id)
    payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    payload["spec_sha256"] = "0" * 64
    proposal_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ScheduleError) as excinfo:
        commit(
            result.proposal_id,
            result.confirm_token,
            store=store,
            job_store=jobs,
            runtime_root=runtime_root,
        )
    assert (excinfo.value.status, excinfo.value.code) == (409, "version_changed")
    assert jobs.load() == {}


# ---------------------------------------------------------------------------
# detach and the schedule listing
# ---------------------------------------------------------------------------


def test_detach_deletes_the_job_and_cascades_run_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())
    result = attach(
        artifact.id,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )
    append_run(
        RunRecord(
            run_id="run-1",
            job_id=result.job_id,
            strategy_id=artifact.id,
            strategy_version=result.strategy_version,
            started_at=1,
            finished_at=2,
            status="completed",
        ),
        runtime_root=runtime_root,
    )
    assert history_path(result.job_id, runtime_root=runtime_root).is_file()

    assert detach(result.job_id, job_store=jobs, runtime_root=runtime_root) is True
    assert jobs.get(result.job_id) is None
    assert not history_path(result.job_id, runtime_root=runtime_root).exists()
    assert detach(result.job_id, job_store=jobs, runtime_root=runtime_root) is False


def test_list_schedules_returns_enabled_strategy_jobs_ordered_by_next_run(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    first_artifact = _make_ready(store, runtime_root, _code_spec("First"))
    second_artifact = _make_ready(store, runtime_root, _code_spec("Second"))
    first = attach(
        first_artifact.id,
        schedule="3600000",
        timezone=None,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )
    second = attach(
        second_artifact.id,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )
    jobs.upsert(
        ScheduledResearchJob(id="sr-prompt-job", prompt="analyse", schedule="3600000"),
        validate=False,
    )
    later = jobs.get(first.job_id)
    assert later is not None
    later.next_run_at = 200
    jobs.upsert(later, validate=False)
    earlier = jobs.get(second.job_id)
    assert earlier is not None
    earlier.next_run_at = 100
    jobs.upsert(earlier, validate=False)

    listed = list_schedules(job_store=jobs)
    assert [job.id for job in listed] == [second.job_id, first.job_id]
    assert list_schedules(
        strategy_id=first_artifact.id, job_store=jobs
    )[0].id == first.job_id

    earlier.status = JobStatus.CANCELLED
    jobs.upsert(earlier, validate=False)
    assert [job.id for job in list_schedules(job_store=jobs)] == [first.job_id]


def test_runtime_root_alone_derives_the_job_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())

    result = attach(
        artifact.id,
        schedule=_SCHEDULE,
        timezone=_TIMEZONE,
        store=store,
        runtime_root=runtime_root,
    )

    derived = ScheduledResearchJobStore(
        runtime_root / "scheduled_research" / "scheduled_research_jobs.json"
    )
    assert derived.get(result.job_id) is not None


# ---------------------------------------------------------------------------
# request_schedule: the token-free entry point used by the schedule_strategy tool
# ---------------------------------------------------------------------------


def test_request_schedule_returns_no_token_for_a_code_strategy(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())

    result = request_schedule(
        artifact.id,
        _SCHEDULE,
        _TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )

    assert set(result) == {
        "proposal_id",
        "job_id",
        "strategy_version",
        "requires_confirmation",
    }
    assert not any("confirm_token" in key for key in result)
    assert result["requires_confirmation"] is False
    assert result["job_id"] is not None
    assert jobs.get(result["job_id"]) is not None
    assert "scp_" not in json.dumps(result)


def test_request_schedule_returns_a_tokenless_proposal_for_a_workflow(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _workflow_spec())

    result = request_schedule(
        artifact.id,
        _SCHEDULE,
        _TIMEZONE,
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )

    assert result["proposal_id"] is not None
    assert result["requires_confirmation"] is True
    assert result["job_id"] is None
    assert jobs.load() == {}
    blob = json.dumps(result)
    assert "confirm_token" not in blob
    assert "scp_" not in blob


def test_schedule_strategy_tool_delegates_to_the_rm_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = {
        "ok": True,
        "schedule_id": "ss_0001",
        "next_run_at": "2026-10-27T01:00:00Z",
        "pinned_version": "v1-aaaaaaaa",
    }
    calls: list[dict] = []

    def fake_schedule(**kwargs):
        calls.append(kwargs)
        return envelope

    def refuse(*_args, **_kwargs):
        raise AssertionError("the retired local request_schedule bridge was called")

    monkeypatch.setattr("src.agent_schedules.tools.schedule_strategy", fake_schedule)
    monkeypatch.setattr("src.strategy_library.scheduling.request_schedule", refuse)

    text = ScheduleStrategyTool().execute(
        strategy_id="stg-001", kind="cron", cron_spec=_SCHEDULE
    )

    assert json.loads(text) == envelope
    assert calls == [
        {
            "strategy_id": "stg-001",
            "kind": "cron",
            "strategy_version": None,
            "name": None,
            "cron_spec": _SCHEDULE,
            "interval_s": None,
            "time_of_day": None,
            "tz": None,
        }
    ]


def test_request_schedule_treats_a_blank_timezone_as_utc(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    jobs = ScheduledResearchJobStore(tmp_path / "jobs.json")
    runtime_root = tmp_path / "runtime"
    artifact = _make_ready(store, runtime_root, _code_spec())

    result = request_schedule(
        artifact.id,
        _SCHEDULE,
        "",
        store=store,
        job_store=jobs,
        runtime_root=runtime_root,
    )

    job = jobs.get(result["job_id"])
    assert job is not None
    assert job.timezone == "UTC"
    assert job.next_run_at > job.created_at
