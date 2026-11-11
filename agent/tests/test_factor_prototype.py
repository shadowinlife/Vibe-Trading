"""Frozen factor prototype orchestration and worker contract tests."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.factors import prototype_worker
from src.factors.prototype import (
    FactorPrototypeRunner,
    FrozenFactorEvaluator,
    PhaseOutcome,
    PrototypeValidationError,
    PrototypeWorkError,
    PrototypeWorkLedger,
)
from src.factors.registry import get_default_registry
from src.factors.registry_freeze import (
    TOOL_SCHEMA_VERSION,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs import execution_start_service
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    FactorStartRequest,
)
from src.strategy_runs.control_service import ExecutionControlService
from src.strategy_runs.control_store import ControlStore
from src.strategy_runs.execution_start_service import (
    ExecutionStartError,
    ExecutionStartService,
)
from tests._factor_prototype_support import (
    _CANDIDATES,
    _Evaluator,
    _Registry,
    _request,
    _run_happy,
    _spec,
    _terminal_row,
    _write_spec_document,
)

from tests._factor_prototype_support import _allowed_run_root as _allowed_run_root

pytestmark = pytest.mark.usefixtures("_allowed_run_root")


def test_spec_validation_rejects_extras_unknown_ids_and_digest_mismatch(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    spec = _spec(registry)
    with pytest.raises(ValidationError):
        FactorPrototypeSpec.model_validate(
            {**spec.model_dump(mode="json"), "unexpected": True}
        )

    unknown = FactorPrototypeSpec(
        **{
            **spec.model_dump(mode="json"),
            "candidate_ids": ("academic_missing",),
        }
    )
    with pytest.raises(PrototypeValidationError) as caught:
        FactorPrototypeRunner(registry=registry, evaluator=_Evaluator()).run(
            _request(unknown, tmp_path / "unknown")
        )
    assert caught.value.kind == "invalid_request"
    assert not (tmp_path / "unknown").exists()

    mismatched = _spec(registry, registry_digest="0" * 64)
    evaluator = _Evaluator()
    with pytest.raises(PrototypeValidationError) as caught:
        FactorPrototypeRunner(registry=registry, evaluator=evaluator).run(
            _request(mismatched, tmp_path / "mismatch")
        )
    assert caught.value.kind == "input_digest_mismatch"
    assert evaluator.requests == []
    assert not (tmp_path / "mismatch").exists()


def test_completed_skipped_and_insufficient_publish_honest_evidence(
    tmp_path: Path,
) -> None:
    result, evaluator, progress = _run_happy(tmp_path / "run")

    document = result.document
    assert document["status"] == "completed"
    assert (document["completed"], document["total"]) == (1, 3)
    assert (
        document["registry_digest"]
        == freeze_registry(rows_from_registry(_Registry(), _CANDIDATES)).registry_digest
    )
    assert document["tool_schema_version"] == TOOL_SCHEMA_VERSION
    assert [row["status"] for row in document["candidates"]] == [
        "completed",
        "skipped",
        "insufficient_data",
    ]
    completed, skipped, insufficient = document["candidates"]
    assert completed["reasons"] == []
    assert completed["metrics"] == {
        "ic_mean": 0.1,
        "ic_ir": 1.2,
        "group_return_spread": 0.08,
        "n_obs": 20,
        "oos_ic_mean": 0.04,
        "oos_n_obs": 10,
        "sample_coverage": 0.8,
        "missing_data_ratio": 0.2,
        "oos_sample_coverage": 0.75,
        "oos_missing_data_ratio": 0.25,
    }
    assert "warmup" in skipped["reasons"][0]
    assert skipped["metrics"]["ic_mean"] == "unavailable"
    assert insufficient["reasons"] == ["no_paired_observations"]
    assert insufficient["metrics"]["oos_ic_mean"] == "unavailable"
    assert "ranking" not in document
    assert document["quality_bounds"]["sample_coverage"] == "unavailable"
    assert document["quality_bounds"]["oos_window"] == ["2021-01-01", "2021-06-30"]
    assert [call.phase for call in evaluator.requests] == [
        "in_sample",
        "out_of_sample",
        "in_sample",
    ]
    assert [item.completed for item in progress] == [1, 1, 1]
    assert [item.processed for item in progress] == [1, 2, 3]
    assert [item.skipped for item in progress] == [0, 1, 1]
    assert [item.insufficient for item in progress] == [0, 0, 1]


def test_fully_measured_multiple_candidates_publish_run_quality(
    tmp_path: Path,
) -> None:
    candidate_ids = ("academic_done", "academic_second")
    registry = _Registry(candidate_ids)
    result = FactorPrototypeRunner(registry=registry, evaluator=_Evaluator()).run(
        _request(_spec(registry, candidate_ids), tmp_path / "run")
    )

    quality = result.document["quality_bounds"]
    assert result.document["status"] == "completed"
    assert result.document["completed"] == 2
    assert quality["n_obs"] == 60
    assert quality["sample_coverage"] == pytest.approx(0.783333)
    assert quality["missing_data_ratio"] == pytest.approx(0.216667)
    assert quality["group_return_spread"] == pytest.approx(0.08)


def test_windows_artifacts_and_progress_are_ordered_and_never_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_sleep(_: float) -> None:
        raise AssertionError("sleep is forbidden")

    monkeypatch.setattr(time, "sleep", fail_sleep)
    result, evaluator, _ = _run_happy(tmp_path / "run")
    first, second = evaluator.requests[:2]
    assert (first.start_date, first.end_date) == (date(2020, 1, 1), date(2020, 12, 31))
    assert (second.start_date, second.end_date) == (date(2021, 1, 1), date(2021, 6, 30))
    inputs = [(call.candidate.candidate_id, call.phase) for call in evaluator.requests]
    assert len(inputs) == len(set(inputs))

    run_dir = tmp_path / "run"
    merged = {
        key: str(run_dir / "artifacts" / name)
        for key, name in (
            ("ic_series", "ic_series.csv"),
            ("ic_summary", "ic_summary.json"),
            ("group_equity", "group_equity.csv"),
        )
    }
    assert result.document["artifacts"] == merged
    assert result.document["artifact_paths"][:3] == list(merged.values())
    done = run_dir / "candidates" / "academic_done"
    assert result.document["artifact_paths"][3:15] == [
        str(done / phase / name)
        for phase in ("in_sample", "out_of_sample")
        for name in (
            "summary.json",
            "factor.csv",
            "returns.csv",
            "ic_series.csv",
            "group_equity.csv",
            "ic_summary.json",
        )
    ]
    long_ic = pd.read_csv(merged["ic_series"])
    long_equity = pd.read_csv(merged["group_equity"])
    assert list(long_ic.columns[:2]) == ["candidate_id", "phase"]
    assert list(long_equity.columns[:2]) == ["candidate_id", "phase"]
    assert long_ic["candidate_id"].unique().tolist() == ["academic_done"]
    native = done / "in_sample" / "ic_series.csv"
    assert native.read_text(encoding="utf-8") == "date,IC\n2020-01-01,0.1\n"
    summary = json.loads(Path(merged["ic_summary"]).read_text(encoding="utf-8"))
    assert summary["candidates"][0]["in_sample"]["ic_mean"] == 0.1
    assert summary["candidates"][1]["in_sample"] == "unavailable"
    assert summary["quality_bounds"] == result.document["quality_bounds"]


def test_stop_between_candidates_preserves_only_completed_evidence(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    evaluator = _Evaluator()
    progress = []
    checks = iter((False, True))
    result = FactorPrototypeRunner(registry=registry, evaluator=evaluator).run(
        _request(_spec(registry), tmp_path / "run"),
        should_stop=lambda: next(checks),
        on_progress=progress.append,
    )

    assert result.document["status"] == "partial"
    assert (result.document["completed"], result.document["total"]) == (1, 3)
    assert len(result.document["candidates"]) == 1
    assert result.document["candidates"][0]["status"] == "completed"
    assert result.document["quality_bounds"]["n_obs"] == "unavailable"
    assert result.document["quality_bounds"]["group_return_spread"] == "unavailable"
    assert len(evaluator.requests) == 2
    assert len(progress) == 1
    assert (tmp_path / "run" / "candidates" / "academic_done" / "in_sample").is_dir()
    assert not (tmp_path / "run" / "candidates" / "academic_skip").exists()


def test_stop_before_first_candidate_is_cancelled_without_fake_progress(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    evaluator = _Evaluator()
    progress = []
    result = FactorPrototypeRunner(registry=registry, evaluator=evaluator).run(
        _request(_spec(registry), tmp_path / "run"),
        should_stop=lambda: True,
        on_progress=progress.append,
    )

    assert result.document["status"] == "cancelled"
    assert result.document["candidates"] == []
    assert (result.document["completed"], result.document["total"]) == (0, 3)
    assert evaluator.requests == []
    assert progress == []
    assert result.document["quality_bounds"]["sample_coverage"] == "unavailable"


def test_work_ledger_rejects_identical_tool_input_before_duplicate_work() -> None:
    ledger = PrototypeWorkLedger()
    payload = {"candidate_id": "academic_done", "phase": "in_sample"}
    ledger.admit("alpha_bench", payload)

    with pytest.raises(PrototypeWorkError):
        ledger.admit("alpha_bench", payload)


def test_missing_or_escaping_artifacts_never_complete_a_candidate(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    native = _Evaluator()
    outside = tmp_path / "outside.csv"
    outside.write_text("date,IC\n2020-01-01,9\n", encoding="utf-8")

    class _BrokenEvaluator:
        def evaluate(self, request) -> PhaseOutcome:
            outcome = native.evaluate(request)
            outcome.artifacts.pop("group_equity")
            outcome.artifacts["ic_series"] = str(outside)
            return outcome

    result = FactorPrototypeRunner(registry=registry, evaluator=_BrokenEvaluator()).run(
        _request(_spec(registry, ("academic_done",)), tmp_path / "run")
    )

    assert result.document["status"] == "failed"
    assert result.document["candidates"] == []
    assert result.progress.completed == 0
    assert all(
        Path(path).is_relative_to(tmp_path / "run")
        for path in result.document["artifact_paths"]
    )
    assert str(outside) not in result.document["artifact_paths"]


def test_existing_artifact_symlink_is_rejected_before_any_work(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (run_dir / "artifacts").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    evaluator = _Evaluator()

    with pytest.raises(PrototypeValidationError) as caught:
        FactorPrototypeRunner(registry=registry, evaluator=evaluator).run(
            _request(_spec(registry, ("academic_done",)), run_dir)
        )

    assert caught.value.kind == "invalid_request"
    assert evaluator.requests == []
    assert list(outside.iterdir()) == []


def test_real_close_only_candidate_masks_exact_label_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = get_default_registry()
    candidate_id = "academic_high52w"
    dates = pd.bdate_range("2020-01-01", periods=400)
    symbols = tuple(f"REAL{index}" for index in range(6))
    close = pd.DataFrame(index=dates, columns=symbols, dtype=float)
    for column, symbol in enumerate(symbols):
        close[symbol] = [
            (20 + column) * (1 + 0.0005 * (column + 1) * row)
            + 0.03 * ((row + column) % 7)
            for row in range(len(dates))
        ]
    split = dates[300].date()
    end = dates[380].date()
    spec = FactorPrototypeSpec(
        registry_digest=freeze_registry(
            rows_from_registry(registry, (candidate_id,))
        ).registry_digest,
        candidate_ids=(candidate_id,),
        symbols=symbols,
        start_date=dates[0].date(),
        split_date=split,
        end_date=end,
        source="alpha_zoo",
        as_of=end,
        forward_return_horizon_days=5,
        n_groups=2,
    )

    def load_symbols(_symbols, start: str, finish: str):
        return {"close": close.loc[start:finish].copy()}

    monkeypatch.setattr("src.tools.alpha_bench_frozen._load_symbols", load_symbols)
    result = FactorPrototypeRunner(
        registry=registry, evaluator=FrozenFactorEvaluator()
    ).run(_request(spec, tmp_path / "run"))

    assert result.document["status"] == "completed"
    assert result.document["candidates"][0]["status"] == "completed"
    base = tmp_path / "run" / "candidates" / candidate_id
    in_sample = pd.read_csv(base / "in_sample" / "returns.csv", index_col=0)
    out_sample = pd.read_csv(base / "out_of_sample" / "returns.csv", index_col=0)
    # h=5 native returns are end-anchored/non-overlapping: 290 and 295 are sampled.
    assert in_sample.loc[str(dates[290].date())].notna().all()
    assert in_sample.loc[str(dates[295].date())].isna().all()
    assert out_sample.loc[str(dates[300].date())].notna().all()
    assert out_sample.loc[str(dates[375].date())].notna().all()
    assert out_sample.loc[str(dates[376].date())].isna().all()
    assert pd.read_csv(
        base / "out_of_sample" / "factor.csv", index_col=0
    ).index.max() <= str(end)
    metrics = result.document["candidates"][0]["metrics"]
    for phase, first, last, coverage_key in (
        ("in_sample", dates[0], dates[299], "sample_coverage"),
        ("out_of_sample", dates[300], dates[380], "oos_sample_coverage"),
    ):
        factor = pd.read_csv(
            base / phase / "analysis_inputs" / "factor.csv", index_col=0
        )
        returns = pd.read_csv(
            base / phase / "analysis_inputs" / "returns.csv", index_col=0
        )
        paired = factor.notna() & returns.notna()
        expected = paired.to_numpy().sum() / paired.size
        assert metrics[coverage_key] == pytest.approx(round(expected, 6))
        analysis_factor = pd.read_csv(
            base / phase / "analysis_inputs" / "factor.csv", index_col=0
        )
        assert analysis_factor.index.min() >= str(first.date())
        assert analysis_factor.index.max() <= str(last.date())


def test_worker_writes_result_and_progress_but_not_supervisor_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    spec = _spec(registry, ("academic_done",))
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, spec)
    runner = FactorPrototypeRunner(registry=registry, evaluator=_Evaluator())
    monkeypatch.setattr(prototype_worker, "FactorPrototypeRunner", lambda: runner)

    exit_code = prototype_worker.main([str(run_dir)])

    assert exit_code == 0
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert (result["completed"], result["total"]) == (1, 1)
    assert progress == {
        "completed": 1,
        "total": 1,
        "skipped": 0,
        "insufficient": 0,
        "processed": 1,
    }
    assert not (run_dir / "worker_result.json").exists()


def test_worker_registry_mismatch_exits_failed_without_progress(tmp_path: Path) -> None:
    registry = get_default_registry()
    spec = _spec(registry, ("academic_bab",), registry_digest="0" * 64)
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, spec)

    assert prototype_worker.main([str(run_dir)]) == 1
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error_kind"] == "input_digest_mismatch"
    assert result["candidates"] == []
    assert not (run_dir / "progress.json").exists()
    assert not (run_dir / "worker_result.json").exists()


def test_worker_rejects_changed_spec_with_original_remote_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    spec = _spec(registry, ("academic_done",))
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, spec, remote_id="factor-original")
    evaluator = _Evaluator()
    runner = FactorPrototypeRunner(registry=registry, evaluator=evaluator)
    monkeypatch.setattr(prototype_worker, "FactorPrototypeRunner", lambda: runner)

    assert prototype_worker.main([str(run_dir)]) == 1
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["error_kind"] == "input_digest_mismatch"
    assert evaluator.requests == []


def test_worker_rejects_spec_symlink_outside_run_dir(tmp_path: Path) -> None:
    registry = _Registry()
    spec = _spec(registry, ("academic_done",))
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, spec)
    outside = tmp_path / "outside-spec.json"
    outside.write_bytes((run_dir / "spec.json").read_bytes())
    (run_dir / "spec.json").unlink()
    try:
        (run_dir / "spec.json").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert prototype_worker.main([str(run_dir)]) == 1
    assert not (run_dir / "result.json").exists()
    assert not (run_dir / "progress.json").exists()


@pytest.mark.parametrize(
    ("checks", "expected"),
    [((False, True), "partial"), ((), "cancelled")],
)
def test_worker_partial_and_cancelled_exit_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checks: tuple[bool, ...],
    expected: str,
) -> None:
    candidate_ids = ("academic_done", "academic_second")
    registry = _Registry(candidate_ids)
    spec = _spec(registry, candidate_ids)
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, spec)
    native = FactorPrototypeRunner(registry=registry, evaluator=_Evaluator())

    class _StopRunner:
        def run(self, request, should_stop, on_progress):
            decisions = iter(checks)
            return native.run(
                request,
                should_stop=lambda: next(decisions, True),
                on_progress=on_progress,
            )

    monkeypatch.setattr(prototype_worker, "FactorPrototypeRunner", _StopRunner)

    assert prototype_worker.main([str(run_dir)]) == 0
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == expected
    assert not (run_dir / "worker_result.json").exists()


def test_factor_start_persists_spec_before_one_spawn_and_never_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ControlStore(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(
        execution_start_service, "get_runtime_root", lambda: tmp_path / "runtime"
    )
    registry = get_default_registry()
    spec = _spec(registry, ("academic_bab",))
    request = FactorStartRequest(
        dispatch_key="dispatch-start-spec",
        kind="factor",
        spec=spec,
        contract_version=CONTRACT_VERSION,
    )

    class _Supervisor:
        calls = 0

        def start(self, dispatch_key, worker_spec):
            self.calls += 1
            document = json.loads(
                (worker_spec.run_dir / "spec.json").read_text(encoding="utf-8")
            )
            assert document["dispatch_key"] == dispatch_key
            return store.get_dispatch(dispatch_key)

    supervisor = _Supervisor()
    service = ExecutionStartService(store, supervisor)
    accepted = service.start(request)
    run_dir = tmp_path / "runtime" / "runs" / accepted.remote_id
    original = (run_dir / "spec.json").read_bytes()

    replay = service.start(request)
    changed = FactorStartRequest(
        dispatch_key=request.dispatch_key,
        kind="factor",
        spec=_spec(registry, ("academic_bab",), symbols=("OTHER",)),
        contract_version=CONTRACT_VERSION,
    )
    with pytest.raises(ExecutionStartError) as caught:
        service.start(changed)

    assert replay.replayed is True
    assert caught.value.kind == "idempotency_conflict"
    assert supervisor.calls == 1
    assert (run_dir / "spec.json").read_bytes() == original
    document = json.loads(original)
    assert document["spec"] == spec.model_dump(mode="json", by_alias=True)
    assert document["registry_digest"] == spec.registry_digest
    assert document["contract_version"] == CONTRACT_VERSION


def test_control_result_prefers_worker_result_and_progress(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    result, _, _ = _run_happy(run_dir)
    (run_dir / "result.json").write_text(json.dumps(result.document), encoding="utf-8")
    (run_dir / "progress.json").write_text(
        json.dumps(result.progress.document()), encoding="utf-8"
    )
    (run_dir / "worker_result.json").write_text(
        json.dumps({"status": "completed", "remote_id": "factor-test-remote"}),
        encoding="utf-8",
    )
    store = ControlStore(tmp_path / "ledger.sqlite3")
    row = _terminal_row(store, run_dir, result.document["registry_digest"])

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.result_status == "available"
    assert projection.result == result.document
    assert projection.progress == result.progress.document()
    assert projection.has_more is False


def test_control_result_rejects_incomplete_prototype_document(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    result, _, _ = _run_happy(run_dir)
    incomplete = dict(result.document)
    incomplete.pop("candidates")
    (run_dir / "result.json").write_text(json.dumps(incomplete), encoding="utf-8")
    terminal = {"status": "completed", "remote_id": "factor-test-remote"}
    (run_dir / "worker_result.json").write_text(json.dumps(terminal), encoding="utf-8")
    store = ControlStore(tmp_path / "ledger.sqlite3")
    row = _terminal_row(store, run_dir, result.document["registry_digest"])

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.result == terminal
    assert projection.progress is None


def test_control_result_reads_large_bounded_prototype_document(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    result, _, _ = _run_happy(run_dir)
    document = dict(result.document)
    document["artifact_paths"] = [
        *document["artifact_paths"],
        *(f"{run_dir}/candidates/long/{'x' * 100}-{index}" for index in range(1000)),
    ]
    encoded = json.dumps(document)
    assert len(encoded) > 50 * 1024
    (run_dir / "result.json").write_text(encoded, encoding="utf-8")
    (run_dir / "progress.json").write_text(
        json.dumps(result.progress.document()), encoding="utf-8"
    )
    (run_dir / "worker_result.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )

    store = ControlStore(tmp_path / "ledger.sqlite3")
    row = _terminal_row(store, run_dir, result.document["registry_digest"])

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.result == document
    assert projection.progress == result.progress.document()


def test_control_result_falls_back_to_supervisor_on_invalid_worker_json(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "result.json").write_text("not json", encoding="utf-8")
    terminal = {
        "status": "completed",
        "remote_id": "factor-test-remote",
        "dispatch_key": "dispatch-factor-test",
    }
    (run_dir / "worker_result.json").write_text(json.dumps(terminal), encoding="utf-8")
    store = ControlStore(tmp_path / "ledger.sqlite3")
    row = _terminal_row(store, run_dir)

    projection = ExecutionControlService(store).result(row.remote_id)

    assert projection.result_status == "available"
    assert projection.result == terminal
    assert projection.progress is None
    assert projection.has_more is False
