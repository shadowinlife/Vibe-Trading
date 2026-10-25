"""Bounded run-history checks for scheduled strategies."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from src.strategy_library import history
from src.strategy_library.history import RunOutcome, RunRecord

JOB_ID = "sr-abc"
STRATEGY_ID = "strat-a"
VERSION = "v1-abcdef12"


def _record(
    run_id: str,
    *,
    job_id: str = JOB_ID,
    strategy_id: str = STRATEGY_ID,
    started_at: int = 1_000,
    finished_at: int | None = 1_010,
    status: str = "completed",
    **extra: object,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        job_id=job_id,
        strategy_id=strategy_id,
        strategy_version=VERSION,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        **extra,  # type: ignore[arg-type]
    )


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_append_25_keeps_newest_20_in_descending_order(tmp_path: Path) -> None:
    for index in range(25):
        history.append_run(
            _record(f"r{index:02d}", started_at=1_000 + index * 1_000),
            runtime_root=tmp_path,
        )

    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    assert _line_count(path) == 20

    records = history.read_runs(JOB_ID, runtime_root=tmp_path)
    assert len(records) == 20
    assert [record.run_id for record in records] == [f"r{index:02d}" for index in range(24, 4, -1)]
    assert records[0].started_at == 25_000
    assert records[-1].started_at == 6_000


def test_append_clamps_retention_to_the_supported_range(tmp_path: Path) -> None:
    for index in range(10):
        history.append_run(
            _record(f"r{index:02d}", started_at=index),
            retention=1,
            runtime_root=tmp_path,
        )

    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    assert _line_count(path) == history.MIN_RETENTION
    assert [record.run_id for record in history.read_runs(JOB_ID, runtime_root=tmp_path)] == [
        "r09",
        "r08",
        "r07",
        "r06",
        "r05",
    ]


def test_clamp_retention_defaults_and_bounds() -> None:
    assert history.clamp_retention(None) == 20
    assert history.clamp_retention(20) == 20
    assert history.clamp_retention(1) == 5
    assert history.clamp_retention(500) == 100
    with pytest.raises(ValueError):
        history.clamp_retention("20")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        history.clamp_retention(True)  # type: ignore[arg-type]


def test_missing_history_reads_as_empty(tmp_path: Path) -> None:
    assert history.read_runs(JOB_ID, runtime_root=tmp_path) == []
    assert history.read_runs_for_strategy(STRATEGY_ID, runtime_root=tmp_path) == []


def test_corrupt_and_partial_lines_are_skipped(tmp_path: Path) -> None:
    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    path.parent.mkdir(parents=True)
    good = history.serialize_run_line(_record("r1", started_at=2_000)).decode("utf-8")
    good_second = history.serialize_run_line(_record("r2", started_at=3_000)).decode("utf-8")
    path.write_text(
        good
        + '{"run_id": "torn", "job_id": "sr-abc", "strat\n'
        + '{"run_id": "wrong-shape"}\n'
        + "\n"
        + good_second,
        encoding="utf-8",
    )

    records = history.read_runs(JOB_ID, runtime_root=tmp_path)

    assert [record.run_id for record in records] == ["r2", "r1"]


def test_oversized_summary_and_error_are_truncated_to_the_line_cap(tmp_path: Path) -> None:
    history.append_run(
        _record("r1", summary="s" * 50_000, error="中" * 50_000, error_kind="runner_error"),
        runtime_root=tmp_path,
    )

    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    lines = path.read_bytes().splitlines()
    assert len(lines) == 1
    assert len(lines[0]) <= history.MAX_LINE_BYTES

    payload = json.loads(lines[0])
    assert payload["run_id"] == "r1"
    assert payload["error_kind"] == "runner_error"
    assert len(payload["summary"]) < 50_000
    assert len(payload["error"]) < 50_000
    assert len(payload["summary"]) + len(payload["error"]) > 0

    records = history.read_runs(JOB_ID, runtime_root=tmp_path)
    assert len(records) == 1
    assert records[0].summary.startswith("s")


def test_serialize_run_line_bounds_multibyte_summaries() -> None:
    line = history.serialize_run_line(_record("r1", summary="中" * 20_000))
    assert len(line) <= history.MAX_LINE_BYTES + 1
    assert json.loads(line)["summary"]


def test_oversized_run_dir_is_dropped_before_the_structure() -> None:
    line = history.serialize_run_line(_record("r1", run_dir="x" * 20_000))
    payload = json.loads(line)

    assert len(line) <= history.MAX_LINE_BYTES + 1
    assert payload["run_dir"] is None
    assert payload["run_id"] == "r1"


EXPECTED_FIXED_LINE = (
    b'{"run_id":"r1","job_id":"sr-abc","strategy_id":"strat-a",'
    b'"strategy_version":"v1-abcdef12","started_at":1000,"finished_at":1010,'
    b'"status":"completed","session_id":null,"run_dir":null,"summary":"",'
    b'"error":"","error_kind":""}\n'
)


def test_serialize_run_line_bytes_are_stable() -> None:
    assert history.serialize_run_line(_record("r1")) == EXPECTED_FIXED_LINE


def test_public_api_names_are_exported_from_history() -> None:
    expected = {
        "DEFAULT_RETENTION",
        "MAX_LINE_BYTES",
        "MAX_RETENTION",
        "MIN_RETENTION",
        "RUN_STATUSES",
        "RunOutcome",
        "RunRecord",
        "append_run",
        "clamp_retention",
        "delete_runs",
        "history_path",
        "read_runs",
        "read_runs_for_strategy",
        "runs_dir",
        "serialize_run_line",
    }
    assert set(history.__all__) == expected
    for name in expected:
        assert hasattr(history, name), name


def test_read_runs_ties_break_by_run_id_descending(tmp_path: Path) -> None:
    history.append_run(_record("r-a", started_at=5_000), runtime_root=tmp_path)
    history.append_run(_record("r-b", started_at=5_000), runtime_root=tmp_path)

    assert [record.run_id for record in history.read_runs(JOB_ID, runtime_root=tmp_path)] == [
        "r-b",
        "r-a",
    ]


def test_read_runs_limit_and_negative_limit(tmp_path: Path) -> None:
    for index in range(6):
        history.append_run(
            _record(f"r{index:02d}", started_at=index),
            runtime_root=tmp_path,
        )

    assert [record.run_id for record in history.read_runs(JOB_ID, limit=2, runtime_root=tmp_path)] == [
        "r05",
        "r04",
    ]
    with pytest.raises(ValueError):
        history.read_runs(JOB_ID, limit=-1, runtime_root=tmp_path)


def test_read_runs_for_strategy_merges_two_jobs_newest_20(tmp_path: Path) -> None:
    for index in range(12):
        history.append_run(
            _record(f"a{index:02d}", job_id="sr-a", started_at=1_000 + index * 2),
            runtime_root=tmp_path,
        )
    for index in range(12):
        history.append_run(
            _record(f"b{index:02d}", job_id="sr-b", started_at=2_000 + index * 2),
            runtime_root=tmp_path,
        )
    history.append_run(
        _record("c00", job_id="sr-b", strategy_id="other-strategy", started_at=9_999),
        runtime_root=tmp_path,
    )

    records = history.read_runs_for_strategy(STRATEGY_ID, 20, runtime_root=tmp_path)

    assert len(records) == 20
    assert all(record.strategy_id == STRATEGY_ID for record in records)
    assert records[0].run_id == "b11"
    assert "c00" not in [record.run_id for record in records]
    timestamps = [record.started_at for record in records]
    assert timestamps == sorted(timestamps, reverse=True)


def test_delete_runs_removes_history_and_cascades_run_dirs(tmp_path: Path) -> None:
    job_dir = tmp_path / "runs" / "scheduled" / JOB_ID
    (job_dir / "r1").mkdir(parents=True)
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()

    history.append_run(
        _record("r1", run_dir=str(job_dir / "r1")),
        runtime_root=tmp_path,
    )
    history.append_run(
        _record("r2", started_at=2_000, run_dir=str(outside)),
        runtime_root=tmp_path,
    )

    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    assert path.exists()

    assert history.delete_runs(JOB_ID, runtime_root=tmp_path) is True
    assert not path.exists()
    assert not job_dir.exists()
    assert outside.exists()
    assert history.delete_runs(JOB_ID, runtime_root=tmp_path) is False


def test_concurrent_appends_never_produce_half_lines(tmp_path: Path) -> None:
    def worker(worker_index: int) -> None:
        for item_index in range(10):
            history.append_run(
                _record(
                    f"w{worker_index}{item_index:02d}",
                    started_at=worker_index * 100 + item_index,
                ),
                retention=100,
                runtime_root=tmp_path,
            )

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    path = history.history_path(JOB_ID, runtime_root=tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 80
    payloads = [json.loads(line) for line in lines]
    assert {payload["run_id"] for payload in payloads} == {
        f"w{worker_index}{item_index:02d}"
        for worker_index in range(8)
        for item_index in range(10)
    }


def test_history_path_validates_the_job_id(tmp_path: Path) -> None:
    for bad in ("../escape", "/tmp/evil", "a/b", "a:b", ""):
        with pytest.raises(ValueError):
            history.history_path(bad, runtime_root=tmp_path)


def test_record_roundtrips_through_dict() -> None:
    record = _record(
        "r1",
        session_id="sess-1",
        run_dir="/runs/r1",
        summary="ok",
        error="",
        error_kind="",
    )
    payload = record.to_dict()
    assert payload["run_id"] == "r1"
    assert payload["started_at"] == 1_000
    assert payload["finished_at"] == 1_010
    assert payload["session_id"] == "sess-1"

    restored = RunRecord.from_dict(json.loads(json.dumps(payload)))
    assert restored == record


def test_record_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        _record("r1", status="running")


def test_record_rejects_unsafe_job_or_strategy_ids() -> None:
    with pytest.raises(ValueError):
        _record("r1", job_id="../escape")
    with pytest.raises(ValueError):
        _record("r1", strategy_id="Upper")


def test_from_dict_rejects_missing_and_mistyped_fields() -> None:
    payload = _record("r1").to_dict()

    for field in ("run_id", "job_id", "strategy_id", "strategy_version", "started_at", "status"):
        broken = dict(payload)
        broken.pop(field)
        with pytest.raises(ValueError):
            RunRecord.from_dict(broken)

    broken = dict(payload)
    broken["started_at"] = "not-a-timestamp"
    with pytest.raises(ValueError):
        RunRecord.from_dict(broken)


def test_run_outcome_carries_the_six_shared_fields() -> None:
    record = _record(
        "r1",
        session_id="sess-1",
        run_dir="/runs/r1",
        summary="done",
        error="boom",
        error_kind="runner_error",
    )

    outcome = RunOutcome.from_record(record)

    assert outcome.to_dict() == {
        "status": "completed",
        "summary": "done",
        "error": "boom",
        "error_kind": "runner_error",
        "run_dir": "/runs/r1",
        "session_id": "sess-1",
    }
    assert record.outcome() == outcome
    with pytest.raises(ValueError):
        RunOutcome(status="bogus")
