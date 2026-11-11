"""Review regressions for stale evidence, count consistency, and worker errors."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.factors import prototype_native, prototype_worker
from src.factors.prototype import FactorPrototypeRunner
from src.factors.prototype_result import (
    PrototypeResultIdentity,
    valid_prototype_progress,
    valid_prototype_result,
)
from tests.test_factor_prototype import (
    _Registry,
    _request,
    _run_happy,
    _spec,
    _write_spec_document,
)


@pytest.fixture(autouse=True)
def _allowed_run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))


def test_insufficient_analysis_does_not_merge_stale_native_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    run_dir = tmp_path / "run"
    phase_dir = run_dir / "candidates" / "academic_done" / "in_sample"
    phase_dir.mkdir(parents=True)
    for name in ("ic_series.csv", "group_equity.csv", "ic_summary.json"):
        (phase_dir / name).write_text("stale-evidence\n", encoding="utf-8")

    def bench(payload):
        output = Path(payload["output_dir"])
        frame = pd.DataFrame(
            {"TEST0": [1.0, 2.0]},
            index=pd.DatetimeIndex(["2020-01-01", "2020-01-02"]),
        )
        frame.to_csv(output / "factor.csv", index_label="date")
        frame.to_csv(output / "returns.csv", index_label="date")
        (output / "summary.json").write_text("{}\n", encoding="utf-8")
        return {"status": "ok", "reasons": []}

    unavailable = {"ic_series": None, "ic_summary": None, "group_equity": None}
    monkeypatch.setattr(prototype_native, "run_frozen_bench", bench)
    monkeypatch.setattr(
        prototype_native,
        "run_factor_analysis_contract",
        lambda **_: json.dumps(
            {
                "status": "insufficient_data",
                "reason": "no_paired_observations",
                "artifacts": unavailable,
            }
        ),
    )

    result = FactorPrototypeRunner(registry=registry).run(
        _request(_spec(registry, ("academic_done",)), run_dir)
    )

    candidate = result.document["candidates"][0]
    assert candidate["status"] == "insufficient_data"
    assert set(candidate["artifacts"]["in_sample"]) == {
        "summary",
        "factor_csv",
        "returns_csv",
    }
    assert (run_dir / "artifacts" / "ic_series.csv").read_text(
        encoding="utf-8"
    ) == "candidate_id,phase\n"
    assert (run_dir / "artifacts" / "group_equity.csv").read_text(
        encoding="utf-8"
    ) == "candidate_id,phase\n"
    assert (phase_dir / "ic_series.csv").read_text(
        encoding="utf-8"
    ) == "stale-evidence\n"


def test_result_and_progress_reject_inconsistent_candidate_counts(
    tmp_path: Path,
) -> None:
    result, _, _ = _run_happy(tmp_path / "run")
    document = dict(result.document)
    identity = PrototypeResultIdentity(
        document["remote_id"],
        document["dispatch_key"],
        document["registry_digest"],
    )
    document["completed"] = 3
    document["candidates"] = []

    assert not valid_prototype_result(document, identity)
    progress = result.progress.document()
    progress["processed"] = 99
    assert not valid_prototype_progress(progress, result.document)


def test_worker_maps_native_value_error_to_runner_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _Registry()
    run_dir = tmp_path / "run"
    _write_spec_document(run_dir, _spec(registry, ("academic_done",)))

    class _BrokenRunner:
        def run(self, *_args, **_kwargs):
            raise ValueError("native dataframe failure")

    monkeypatch.setattr(prototype_worker, "FactorPrototypeRunner", _BrokenRunner)

    assert prototype_worker.main([str(run_dir)]) == 1
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["error_kind"] == "runner_error"
