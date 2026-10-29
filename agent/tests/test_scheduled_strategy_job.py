"""Contract tests for the ``strategy`` executor kind on scheduled jobs.

A single bad row quarantines the WHOLE job store as ``.corrupt-*``, so the
model must reject an unusable strategy record at construction time and the
store must keep loading every legacy file unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.service import build_job_from_draft, public_job
from src.scheduled_research.store import CorruptStoreError, ScheduledResearchJobStore
from src.scheduling.types import EXECUTOR_KINDS, is_executor_kind

# A verbatim record as the model wrote it before strategy jobs existed: no
# executor_kind, no strategy fields, no run policy, no in-flight handle.
_LEGACY_JOB_JSON = """{
  "id": "job-legacy-001",
  "prompt": "analyse AAPL momentum",
  "schedule": "0 */6 * * *",
  "title": "Legacy momentum briefing",
  "source_type": "prompt",
  "playbook_slug": null,
  "end_at": null,
  "next_run_at": 1760000000000,
  "status": "pending",
  "created_at": 1759900000000,
  "last_run_at": null,
  "consecutive_failures": 0,
  "last_error": null,
  "failure_kind": null,
  "config": {},
  "timezone": "Asia/Shanghai",
  "delivery_channel": null,
  "delivery_target": null,
  "delivery_target_ref": null,
  "delivery_target_label": null,
  "delivery_format": null,
  "protect_pdf": false,
  "delivery": {
    "status": "none",
    "session_id": null,
    "key": null,
    "error": null,
    "attempts": 0,
    "updated_at": null,
    "provider_message_id": null
  },
  "last_verdict": null
}"""

_NOW_MS = 1_760_000_000_000


def _strategy_job(**overrides: object) -> ScheduledResearchJob:
    fields: dict[str, object] = {
        "id": "strategy-job-1",
        "prompt": "",
        "schedule": "0 */6 * * *",
        "title": "Momentum schedule",
        "source_type": "strategy",
        "executor_kind": "strategy",
        "strategy_id": "momentum-1",
        "strategy_version": "v3-abcd1234",
        "next_run_at": _NOW_MS + 60_000,
        "created_at": _NOW_MS,
    }
    fields.update(overrides)
    return ScheduledResearchJob(**fields)  # type: ignore[arg-type]


def _in_flight(**overrides: object) -> dict[str, object]:
    handle: dict[str, object] = {
        "run_id": "run-abc",
        "kind": "code",
        "session_id": None,
        "run_dir": "/runs/scheduled/strategy-job-1/run-abc",
        "started_at": _NOW_MS,
    }
    handle.update(overrides)
    return handle


class TestExecutorKindDomain:
    def test_strategy_joins_the_closed_domain(self) -> None:
        assert "strategy" in EXECUTOR_KINDS
        assert is_executor_kind("strategy")
        assert is_executor_kind("prompt")
        assert is_executor_kind("internal")
        assert not is_executor_kind("strategy_runner")


class TestLegacyRecords:
    def test_old_record_roundtrips_with_defaulted_new_fields(self) -> None:
        job = ScheduledResearchJob.from_dict(json.loads(_LEGACY_JOB_JSON))
        raw = job.to_dict()

        assert raw["strategy_id"] is None
        assert raw["strategy_version"] is None
        assert raw["in_flight"] is None
        assert raw["run_policy"] == {
            "overlap": "skip",
            "retention": 20,
            "run_timeout_s": 3600,
            "primary": False,
        }
        assert ScheduledResearchJob.from_dict(raw) == job

    def test_old_envelope_still_loads_without_quarantine(self, tmp_path: Path) -> None:
        store_path = tmp_path / "scheduled_research_jobs.json"
        store_path.write_text(
            '{"schema_version": 1, "jobs": [' + _LEGACY_JOB_JSON + "]}",
            encoding="utf-8",
        )

        jobs = ScheduledResearchJobStore(path=store_path).load()

        assert set(jobs) == {"job-legacy-001"}
        assert jobs["job-legacy-001"].executor_kind == "prompt"
        assert jobs["job-legacy-001"].run_policy["retention"] == 20
        assert not list(tmp_path.glob("*.corrupt-*"))


class TestStrategyJobModel:
    def test_job_with_handle_roundtrips_through_dict(self) -> None:
        job = _strategy_job(in_flight=_in_flight())

        raw = job.to_dict()

        assert raw["strategy_id"] == "momentum-1"
        assert raw["strategy_version"] == "v3-abcd1234"
        assert raw["in_flight"] == _in_flight()
        assert ScheduledResearchJob.from_dict(raw) == job

    def test_strategy_job_persists_and_reloads(self, tmp_path: Path) -> None:
        store_path = tmp_path / "scheduled_research_jobs.json"
        job = _strategy_job(in_flight=_in_flight(kind="workflow", session_id="sess-1"))
        ScheduledResearchJobStore(path=store_path).save({job.id: job})

        reloaded = ScheduledResearchJobStore(path=store_path).load()

        assert reloaded["strategy-job-1"] == job
        assert reloaded["strategy-job-1"].in_flight == job.in_flight
        assert store_path.exists()
        assert not list(tmp_path.glob("*.corrupt-*"))

    def test_strategy_source_and_failure_kind_roundtrip(self) -> None:
        job = _strategy_job(failure_kind="strategy")
        raw = job.to_dict()

        assert raw["source_type"] == "strategy"
        assert raw["failure_kind"] == "strategy"
        reloaded = ScheduledResearchJob.from_dict(raw)
        assert reloaded.source_type == "strategy"
        assert reloaded.failure_kind == "strategy"

    def test_strategy_source_requires_strategy_executor(self) -> None:
        with pytest.raises(ValueError, match="must be set together"):
            _strategy_job(executor_kind="prompt")

    def test_strategy_executor_requires_strategy_source(self) -> None:
        with pytest.raises(ValueError, match="must be set together"):
            ScheduledResearchJob(
                id="bad",
                prompt="analyse",
                schedule="60000",
                source_type="prompt",
                executor_kind="strategy",
                strategy_id="momentum-1",
                strategy_version="v3-abcd1234",
                next_run_at=_NOW_MS,
                created_at=_NOW_MS,
            )

    def test_strategy_job_requires_a_strategy_id(self) -> None:
        with pytest.raises(ValueError, match="strategy_id"):
            _strategy_job(strategy_id=None)

    def test_strategy_job_requires_a_strategy_version(self) -> None:
        with pytest.raises(ValueError, match="strategy_version"):
            _strategy_job(strategy_version="  ")

    def test_prompt_job_must_not_carry_strategy_fields(self) -> None:
        with pytest.raises(ValueError, match="only allowed on strategy jobs"):
            ScheduledResearchJob(
                id="bad",
                prompt="analyse",
                schedule="60000",
                strategy_id="momentum-1",
                next_run_at=_NOW_MS,
                created_at=_NOW_MS,
            )

    def test_prompt_job_must_not_carry_an_in_flight_handle(self) -> None:
        with pytest.raises(ValueError, match="only allowed on strategy jobs"):
            ScheduledResearchJob(
                id="bad",
                prompt="analyse",
                schedule="60000",
                in_flight=_in_flight(),
                next_run_at=_NOW_MS,
                created_at=_NOW_MS,
            )

    def test_in_flight_is_not_exposed_in_the_public_job(self) -> None:
        job = _strategy_job(in_flight=_in_flight())

        assert "in_flight" not in public_job(job)
        assert "in_flight" in job.to_dict()


class TestInFlightHandle:
    def test_missing_started_at_is_rejected(self) -> None:
        handle = _in_flight()
        handle.pop("started_at")
        with pytest.raises(ValueError, match="in_flight"):
            _strategy_job(in_flight=handle)

    def test_extra_pid_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="in_flight"):
            _strategy_job(in_flight=_in_flight(pid=4242))

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="in_flight.kind"):
            _strategy_job(in_flight=_in_flight(kind="session"))

    @pytest.mark.parametrize("started_at", ["1760000000000", 1.76e12, True])
    def test_started_at_must_be_an_epoch_ms_integer(self, started_at: object) -> None:
        with pytest.raises(ValueError, match="started_at"):
            _strategy_job(in_flight=_in_flight(started_at=started_at))

    def test_session_id_and_run_dir_may_be_null(self) -> None:
        job = _strategy_job(in_flight=_in_flight(session_id=None, run_dir=None))
        assert job.in_flight is not None
        assert job.in_flight["run_dir"] is None


class TestRunPolicy:
    def test_defaults_are_per_job_and_complete(self) -> None:
        first = _strategy_job()
        second = _strategy_job(id="strategy-job-2")

        assert first.run_policy == {
            "overlap": "skip",
            "retention": 20,
            "run_timeout_s": 3600,
            "primary": False,
        }
        first.run_policy["retention"] = 50
        assert second.run_policy["retention"] == 20

    def test_partial_policy_is_filled_from_defaults(self) -> None:
        job = _strategy_job(run_policy={"retention": 7, "primary": True})
        assert job.run_policy == {
            "overlap": "skip",
            "retention": 7,
            "run_timeout_s": 3600,
            "primary": True,
        }

    def test_overlap_must_be_skip(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            _strategy_job(run_policy={"overlap": "allow"})

    @pytest.mark.parametrize("retention", [4, 101, True, "20"])
    def test_retention_bounds(self, retention: object) -> None:
        with pytest.raises(ValueError, match="retention"):
            _strategy_job(run_policy={"retention": retention})

    @pytest.mark.parametrize("timeout", [59, 86401, True, "3600"])
    def test_run_timeout_bounds(self, timeout: object) -> None:
        with pytest.raises(ValueError, match="run_timeout_s"):
            _strategy_job(run_policy={"run_timeout_s": timeout})

    def test_primary_must_be_boolean(self) -> None:
        with pytest.raises(ValueError, match="primary"):
            _strategy_job(run_policy={"primary": 1})


class TestFromDictRejectsAndQuarantines:
    def test_invalid_coupling_raises_value_error(self) -> None:
        raw = _strategy_job().to_dict()
        raw["executor_kind"] = "prompt"
        with pytest.raises(ValueError, match="must be set together"):
            ScheduledResearchJob.from_dict(raw)

    def test_missing_version_raises_value_error(self) -> None:
        raw = _strategy_job().to_dict()
        raw["strategy_version"] = None
        with pytest.raises(ValueError, match="strategy_version"):
            ScheduledResearchJob.from_dict(raw)

    def test_unknown_source_type_still_rejected(self) -> None:
        raw = _strategy_job().to_dict()
        raw["source_type"] = "ad_hoc"
        with pytest.raises(ValueError, match="source_type"):
            ScheduledResearchJob.from_dict(raw)

    def test_unknown_failure_kind_still_rejected(self) -> None:
        raw = _strategy_job().to_dict()
        raw["failure_kind"] = "timeout"
        with pytest.raises(ValueError, match="failure_kind"):
            ScheduledResearchJob.from_dict(raw)

    def test_corrupt_strategy_row_quarantines_the_store(self, tmp_path: Path) -> None:
        store_path = tmp_path / "scheduled_research_jobs.json"
        broken = _strategy_job().to_dict()
        broken["in_flight"] = _in_flight(pid=4242)
        store_path.write_text(
            json.dumps({"schema_version": 1, "jobs": [broken]}), encoding="utf-8"
        )

        with pytest.raises(CorruptStoreError) as exc_info:
            ScheduledResearchJobStore(path=store_path).load()

        assert not store_path.exists()
        assert ".corrupt-" in exc_info.value.quarantined.name
        assert exc_info.value.quarantined.exists()
        assert list(tmp_path.glob("*.corrupt-*"))


class TestBuildJobFromDraft:
    def _draft(self, **source: object) -> dict[str, object]:
        return {
            "title": "Momentum schedule",
            "source": {"kind": "strategy", **source},
            "schedule": {"expression": "60000"},
        }

    def test_builds_a_pinned_strategy_job(self) -> None:
        job = build_job_from_draft(
            self._draft(strategy_id="momentum-1", strategy_version="v3-abcd1234"),
            now_ms=_NOW_MS,
        )

        assert job.executor_kind == "strategy"
        assert job.source_type == "strategy"
        assert job.strategy_id == "momentum-1"
        assert job.strategy_version == "v3-abcd1234"
        assert job.prompt == ""
        assert job.run_policy["overlap"] == "skip"

    def test_rejects_a_missing_version(self) -> None:
        with pytest.raises(ValueError, match="source.strategy_version is required"):
            build_job_from_draft(self._draft(strategy_id="momentum-1"), now_ms=_NOW_MS)

    def test_rejects_a_missing_strategy_id(self) -> None:
        with pytest.raises(ValueError, match="source.strategy_id is required"):
            build_job_from_draft(
                self._draft(strategy_version="v3-abcd1234"), now_ms=_NOW_MS
            )

    def test_unknown_source_kind_names_strategy(self) -> None:
        draft = self._draft()
        draft["source"] = {"kind": "shell"}
        with pytest.raises(ValueError, match="'prompt', 'playbook', or 'strategy'"):
            build_job_from_draft(draft, now_ms=_NOW_MS)
