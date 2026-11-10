"""Real offline integration for frozen factor prototype execution.

# noqa: SIZE_OK - one mandated module covers happy, worker, stop and missing-data paths.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd  # noqa: PANDAS_OK
import pytest
import yaml

import backtest.loaders.local_loader as local_loader
from src.factors import prototype_worker
from src.factors.prototype import FactorPrototypeRunner, PrototypeRunRequest
from src.factors.prototype_contract import ARTIFACT_FILENAMES, factor_remote_id
from src.factors.registry import get_default_registry
from src.factors.registry_freeze import (
    TOOL_SCHEMA_VERSION,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    JsonValue,
)

pytestmark = pytest.mark.usefixtures("socket_disabled")

_FIXTURE_DIR: Final = Path(__file__).parent / "fixtures" / "factor_prototype"
_FIXTURE_SYMBOLS: Final = tuple(f"VT{index:02d}" for index in range(6))
_LOCAL_SYMBOLS: Final = tuple(f"local:{symbol}" for symbol in _FIXTURE_SYMBOLS)
_DATES: Final = pd.bdate_range("2023-01-02", periods=420)
_CANDIDATE_ARTIFACT_NAMES: Final = tuple(ARTIFACT_FILENAMES.values())
_MERGED_ARTIFACT_NAMES: Final = ("ic_series.csv", "ic_summary.json", "group_equity.csv")
_UNAVAILABLE_QUALITY_KEYS: Final = (
    "sample_coverage",
    "missing_data_ratio",
    "n_obs",
    "group_return_spread",
)


@dataclass(frozen=True, slots=True)
class _Window:
    start: int
    split: int
    end: int
    horizon: int = 1
    groups: int = 3


@dataclass(frozen=True, slots=True)
class _Measured:
    ic_mean: float
    ic_ir: float
    spread: float
    n_obs: int
    coverage: float
    ic_count: int


def _configure_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_dir: Path = _FIXTURE_DIR
) -> None:
    config = tmp_path / "data-bridge" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    sources = [
        {
            "symbol": symbol,
            "type": "csv",
            "path": str(fixture_dir / f"{symbol}.csv"),
            "date_format": "%Y-%m-%d",
        }
        for symbol in _FIXTURE_SYMBOLS
    ]
    config.write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
    monkeypatch.setattr(local_loader, "_CONFIG_PATH", config)
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "runs"))


def _make_spec(
    candidate_ids: tuple[str, ...],
    window: _Window,
    symbols: tuple[str, ...] = _LOCAL_SYMBOLS,
) -> FactorPrototypeSpec:
    registry = get_default_registry()
    frozen = freeze_registry(rows_from_registry(registry, candidate_ids))
    return FactorPrototypeSpec(
        registry_digest=frozen.registry_digest,
        candidate_ids=candidate_ids,
        symbols=symbols,
        start_date=_DATES[window.start].date(),
        split_date=_DATES[window.split].date(),
        end_date=_DATES[window.end].date(),
        source="alpha_zoo",
        as_of=_DATES[window.end].date(),
        forward_return_horizon_days=window.horizon,
        n_groups=window.groups,
    )


def _request(spec: FactorPrototypeSpec, run_dir: Path) -> PrototypeRunRequest:
    return PrototypeRunRequest(
        spec=spec,
        run_dir=run_dir,
        remote_id=factor_remote_id("dispatch-local-factor", spec),
        dispatch_key="dispatch-local-factor",
    )


def _run_worker(
    spec: FactorPrototypeSpec, run_dir: Path
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "remote_id": factor_remote_id("dispatch-local-factor", spec),
        "dispatch_key": "dispatch-local-factor",
        "registry_digest": spec.registry_digest,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "spec": spec.model_dump(mode="json", by_alias=True),
    }
    (run_dir / "spec.json").write_text(json.dumps(document), encoding="utf-8")
    assert prototype_worker.main([str(run_dir)]) == 0
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    return result, progress


def _read_phase_inputs(phase_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    input_dir = phase_dir / "analysis_inputs"
    factor = pd.read_csv(input_dir / "factor.csv", index_col=0, parse_dates=True)
    returns = pd.read_csv(input_dir / "returns.csv", index_col=0, parse_dates=True)
    return factor, returns


def _independent_ic(
    factor: pd.DataFrame, returns: pd.DataFrame
) -> tuple[pd.Series, pd.DataFrame]:
    dates = factor.index.intersection(returns.index)
    symbols = factor.columns.intersection(returns.columns)
    factor = factor.loc[dates, symbols]
    returns = returns.loc[dates, symbols]
    pairs = factor.notna() & returns.notna()
    values: dict[pd.Timestamp, float] = {}
    for timestamp in dates:
        valid = pairs.loc[timestamp]
        if int(valid.sum()) < 5:
            continue
        factor_ranks = factor.loc[timestamp, valid].rank(method="average").to_numpy()
        return_ranks = returns.loc[timestamp, valid].rank(method="average").to_numpy()
        correlation = float(np.corrcoef(factor_ranks, return_ranks)[0, 1])
        if np.isfinite(correlation):
            values[pd.Timestamp(timestamp)] = correlation
    return pd.Series(values, dtype=float), pairs


def _independent_equity(
    factor: pd.DataFrame, returns: pd.DataFrame, groups: int
) -> pd.DataFrame:
    group_returns: dict[str, list[float]] = {
        f"Group_{index + 1}": [] for index in range(groups)
    }
    valid_dates = []
    for timestamp in factor.index.intersection(returns.index):
        factors = factor.loc[timestamp].dropna()
        observed = returns.loc[timestamp].dropna()
        shared = factors.index.intersection(observed.index)
        if len(shared) < groups:
            continue
        valid_dates.append(timestamp)
        bins = pd.qcut(factors[shared].rank(method="first"), groups, labels=False)
        for index in range(groups):
            members = bins[bins == index].index
            group_returns[f"Group_{index + 1}"].append(
                float(observed[members].mean()) if len(members) else 0.0
            )
    return (1 + pd.DataFrame(group_returns, index=valid_dates)).cumprod()


def _measure(phase_dir: Path, groups: int) -> _Measured:
    factor, returns = _read_phase_inputs(phase_dir)
    expected_ic, pairs = _independent_ic(factor, returns)
    emitted_ic = pd.read_csv(phase_dir / "ic_series.csv", index_col=0, parse_dates=True)
    assert emitted_ic.index.equals(expected_ic.index)
    np.testing.assert_allclose(emitted_ic["IC"], expected_ic, rtol=0, atol=1e-12)
    expected_equity = _independent_equity(factor, returns, groups)
    emitted_equity = pd.read_csv(
        phase_dir / "group_equity.csv", index_col=0, parse_dates=True
    )
    pd.testing.assert_frame_equal(emitted_equity, expected_equity, check_freq=False)
    raw_mean = float(expected_ic.mean())
    raw_std = float(expected_ic.std()) if len(expected_ic) > 1 else 0.0
    n_obs = int(pairs.loc[expected_ic.index].to_numpy().sum())
    assert n_obs > 0
    return _Measured(
        ic_mean=round(raw_mean, 6),
        ic_ir=round(raw_mean / raw_std, 4) if raw_std > 0 else 0.0,
        spread=round(
            float(expected_equity.iloc[-1, -1] - expected_equity.iloc[-1, 0]), 4
        ),
        n_obs=n_obs,
        coverage=round(float(pairs.to_numpy().sum()) / pairs.size, 6),
        ic_count=len(expected_ic),
    )


def _assert_candidate_metrics(
    candidate: dict[str, JsonValue], in_sample: _Measured, out_sample: _Measured
) -> None:
    metrics = candidate["metrics"]
    assert metrics["ic_mean"] == in_sample.ic_mean
    assert metrics["ic_ir"] == in_sample.ic_ir
    assert metrics["group_return_spread"] == in_sample.spread
    assert metrics["n_obs"] == in_sample.n_obs
    assert metrics["sample_coverage"] == in_sample.coverage
    assert metrics["missing_data_ratio"] == round(1.0 - in_sample.coverage, 6)
    assert metrics["oos_ic_mean"] == out_sample.ic_mean
    assert metrics["oos_n_obs"] == out_sample.n_obs
    assert metrics["oos_sample_coverage"] == out_sample.coverage
    assert metrics["oos_missing_data_ratio"] == round(1.0 - out_sample.coverage, 6)


def _assert_oos_boundaries(
    spec: FactorPrototypeSpec, run_dir: Path, candidate_id: str
) -> None:
    base = run_dir / "candidates" / candidate_id
    in_factor, _ = _read_phase_inputs(base / "in_sample")
    out_factor, _ = _read_phase_inputs(base / "out_of_sample")
    assert in_factor.index.max() < pd.Timestamp(spec.split_date)
    assert out_factor.index.min() >= pd.Timestamp(spec.split_date)
    assert out_factor.index.max() <= pd.Timestamp(spec.end_date)
    assert in_factor.index.intersection(out_factor.index).empty
    in_returns = pd.read_csv(base / "in_sample" / "returns.csv", index_col=0)
    out_returns = pd.read_csv(base / "out_of_sample" / "returns.csv", index_col=0)
    split_position = _DATES.get_loc(pd.Timestamp(spec.split_date))
    cross_split = _DATES[split_position - spec.horizon_days]
    before_split = _DATES[split_position - spec.horizon_days - 1]
    assert in_returns.loc[str(before_split.date())].notna().all()
    assert in_returns.loc[str(cross_split.date())].isna().all()
    end_position = _DATES.get_loc(pd.Timestamp(spec.end_date))
    last_realized = _DATES[end_position - spec.horizon_days]
    assert out_returns.loc[str(last_realized.date())].notna().all()
    assert out_returns.loc[str(spec.end_date)].isna().all()
    assert spec.as_of == spec.end_date


def _artifact_paths(run_dir: Path, candidate_ids: tuple[str, ...]) -> list[str]:
    merged = [str(run_dir / "artifacts" / name) for name in _MERGED_ARTIFACT_NAMES]
    native = [
        str(run_dir / "candidates" / candidate / phase / name)
        for candidate in candidate_ids
        for phase in ("in_sample", "out_of_sample")
        for name in _CANDIDATE_ARTIFACT_NAMES
    ]
    return [*merged, *native]


def test_real_three_candidate_batch_recomputes_metrics_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given six real local CSV series and completed/skipped/insufficient registry candidates.
    _configure_local(monkeypatch, tmp_path)
    candidate_ids = ("academic_strev", "academic_bab", "alpha101_089")
    spec = _make_spec(candidate_ids, _Window(start=240, split=320, end=419))
    run_dir = tmp_path / "runs" / "happy"
    snapshots: list[dict[str, int]] = []

    def observe(progress) -> None:
        document = progress.document()
        snapshots.append(document)
        actual = sum(
            all(
                (
                    run_dir / "candidates" / candidate / phase / "ic_summary.json"
                ).is_file()
                for phase in ("in_sample", "out_of_sample")
            )
            for candidate in candidate_ids
        )
        assert document["completed"] == actual <= document["processed"]

    # When the unmodified registry, frozen bench, analysis and orchestrator execute.
    result = FactorPrototypeRunner().run(_request(spec, run_dir), on_progress=observe)

    # Then outcomes, progress and independently recomputed native evidence agree.
    document = result.document
    assert document["status"] == "completed"
    assert [row["status"] for row in document["candidates"]] == [
        "completed",
        "skipped",
        "insufficient_data",
    ]
    assert snapshots == [
        {"completed": 1, "total": 3, "skipped": 0, "insufficient": 0, "processed": 1},
        {"completed": 1, "total": 3, "skipped": 1, "insufficient": 0, "processed": 2},
        {"completed": 1, "total": 3, "skipped": 1, "insufficient": 1, "processed": 3},
    ]
    completed, skipped, insufficient = document["candidates"]
    assert "warmup" in skipped["reasons"][0]
    assert "panel missing required columns ['vwap']" in insufficient["reasons"][0]
    assert set(insufficient["metrics"].values()) == {"unavailable"}
    base = run_dir / "candidates" / "academic_strev"
    in_sample = _measure(base / "in_sample", spec.n_groups)
    out_sample = _measure(base / "out_of_sample", spec.n_groups)
    _assert_candidate_metrics(completed, in_sample, out_sample)
    for phase, measured in (("in_sample", in_sample), ("out_of_sample", out_sample)):
        native_summary = json.loads(
            (base / phase / "ic_summary.json").read_text(encoding="utf-8")
        )
        assert native_summary["ic_count"] == measured.ic_count
        assert native_summary["ic_mean"] == measured.ic_mean
    assert in_sample.ic_mean != 0 and in_sample.ic_ir != 0 and in_sample.spread != 0
    _assert_oos_boundaries(spec, run_dir, "academic_strev")
    assert completed["metrics"]["ic_mean"] == in_sample.ic_mean
    assert document["quality_bounds"]["selection_window"] == [
        str(spec.start_date),
        str(spec.split_date - timedelta(days=1)),
    ]
    assert document["quality_bounds"]["sample_coverage"] == "unavailable"
    expected_paths = _artifact_paths(run_dir, ("academic_strev",))
    expected_paths.append(
        str(run_dir / "candidates" / "alpha101_089" / "in_sample" / "summary.json")
    )
    assert document["artifact_paths"] == expected_paths
    for path in expected_paths:
        assert Path(path).is_file()
    merged = pd.read_csv(run_dir / "artifacts" / "group_equity.csv")
    assert list(merged.columns[:2]) == ["candidate_id", "phase"]
    assert set(merged["candidate_id"]) == {"academic_strev"}
    assert not list(run_dir.rglob("*.html"))
    print(
        "HAPPY_EVIDENCE",
        json.dumps(
            {
                "progress": snapshots,
                "metrics": completed["metrics"],
                "artifact_paths": expected_paths,
            }
        ),
    )


def test_worker_publishes_independently_measured_quality_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a long window that satisfies both real 253-bar warmups.
    _configure_local(monkeypatch, tmp_path)
    candidate_ids = ("academic_bab", "academic_carhart_mom")
    spec = _make_spec(candidate_ids, _Window(start=0, split=300, end=419))
    run_dir = tmp_path / "runs" / "worker"

    # When the actual worker consumes spec.json with no injected evaluator or loader.
    document, progress = _run_worker(spec, run_dir)

    # Then both candidates and the aggregate quality are measured from real CSV observations.
    assert document["status"] == "completed"
    assert progress == {
        "completed": 2,
        "total": 2,
        "skipped": 0,
        "insufficient": 0,
        "processed": 2,
    }
    measured = {
        (candidate, phase): _measure(
            run_dir / "candidates" / candidate / phase, spec.n_groups
        )
        for candidate in candidate_ids
        for phase in ("in_sample", "out_of_sample")
    }
    for candidate, row in zip(candidate_ids, document["candidates"], strict=True):
        _assert_candidate_metrics(
            row, measured[candidate, "in_sample"], measured[candidate, "out_of_sample"]
        )
    observations = sum(item.n_obs for item in measured.values())
    coverage = round(
        sum(item.coverage * item.n_obs for item in measured.values()) / observations, 6
    )
    in_sample = [measured[candidate, "in_sample"] for candidate in candidate_ids]
    spread_observations = sum(item.n_obs for item in in_sample)
    spread = round(
        sum(item.spread * item.n_obs for item in in_sample) / spread_observations, 6
    )
    quality = document["quality_bounds"]
    assert quality["n_obs"] == observations > 0
    assert quality["sample_coverage"] == coverage != 0
    assert quality["missing_data_ratio"] == round(1.0 - coverage, 6)
    assert quality["group_return_spread"] == spread != 0
    _assert_oos_boundaries(spec, run_dir, "academic_carhart_mom")
    assert document["artifact_paths"] == _artifact_paths(run_dir, candidate_ids)
    assert all(Path(path).is_file() for path in document["artifact_paths"])
    print("WORKER_QUALITY", json.dumps(quality))


def test_stop_after_first_candidate_preserves_only_real_partial_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given two warmable real candidates and a stop requested by the first outcome callback.
    _configure_local(monkeypatch, tmp_path)
    candidate_ids = ("academic_carhart_mom", "academic_bab")
    spec = _make_spec(candidate_ids, _Window(start=0, split=300, end=419))
    run_dir = tmp_path / "runs" / "partial"
    stopped = False
    snapshots = []

    def observe(progress) -> None:
        nonlocal stopped
        snapshots.append(progress.document())
        stopped = True

    # When the real orchestrator checks stop before the second candidate, without sleeps.
    result = FactorPrototypeRunner().run(
        _request(spec, run_dir), should_stop=lambda: stopped, on_progress=observe
    )

    # Then Carhart's native evidence survives and BAB has no completion or invented quality.
    document = result.document
    assert document["status"] == "partial"
    assert (document["completed"], document["total"]) == (1, 2)
    assert [row["candidate_id"] for row in document["candidates"]] == [
        "academic_carhart_mom"
    ]
    assert document["candidates"][0]["status"] == "completed"
    assert snapshots == [
        {"completed": 1, "total": 2, "skipped": 0, "insufficient": 0, "processed": 1}
    ]
    assert not (run_dir / "candidates" / "academic_bab").exists()
    expected_paths = _artifact_paths(run_dir, ("academic_carhart_mom",))
    assert document["artifact_paths"] == expected_paths
    assert all(Path(path).is_file() for path in expected_paths)
    quality = document["quality_bounds"]
    for key in _UNAVAILABLE_QUALITY_KEYS:
        assert quality[key] == "unavailable"
    merged_summary = json.loads(
        (run_dir / "artifacts" / "ic_summary.json").read_text(encoding="utf-8")
    )
    assert merged_summary["candidates"][1] == {
        "candidate_id": "academic_bab",
        "in_sample": "unavailable",
        "out_of_sample": "unavailable",
    }
    base = run_dir / "candidates" / "academic_carhart_mom"
    in_sample = _measure(base / "in_sample", spec.n_groups)
    out_sample = _measure(base / "out_of_sample", spec.n_groups)
    _assert_candidate_metrics(document["candidates"][0], in_sample, out_sample)
    print(
        "PARTIAL_EVIDENCE",
        json.dumps(
            {
                "progress": snapshots,
                "quality_bounds": quality,
                "artifact_paths": expected_paths,
            }
        ),
    )


def test_removed_symbol_becomes_insufficient_without_fabricated_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given five requested local symbols with one real CSV removed from a private copy.
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(_FIXTURE_DIR, fixture_dir)
    _configure_local(monkeypatch, tmp_path, fixture_dir)
    (fixture_dir / "VT04.csv").unlink()
    spec = _make_spec(
        ("academic_carhart_mom",),
        _Window(start=0, split=300, end=419),
        symbols=_LOCAL_SYMBOLS[:5],
    )
    run_dir = tmp_path / "runs" / "missing"

    # When the real worker has only four usable cross-section members for native IC.
    document, progress = _run_worker(spec, run_dir)

    # Then native insufficiency is explicit and no successful analysis CSV is invented.
    assert document["status"] == "completed"
    assert (document["completed"], document["total"]) == (0, 1)
    assert progress == {
        "completed": 0,
        "total": 1,
        "skipped": 0,
        "insufficient": 1,
        "processed": 1,
    }
    candidate = document["candidates"][0]
    assert candidate["status"] == "insufficient_data"
    assert "insufficient shared dates/assets" in candidate["reasons"][0]
    assert set(candidate["metrics"].values()) == {"unavailable"}
    phase_dir = run_dir / "candidates" / "academic_carhart_mom" / "in_sample"
    factor, returns = _read_phase_inputs(phase_dir)
    assert factor.shape[1] == returns.shape[1] == 4
    expected_paths = [
        str(run_dir / "artifacts" / name) for name in _MERGED_ARTIFACT_NAMES
    ] + [
        str(phase_dir / name) for name in ("summary.json", "factor.csv", "returns.csv")
    ]
    assert document["artifact_paths"] == expected_paths
    assert all(Path(path).is_file() for path in expected_paths)
    for name in ("ic_series.csv", "ic_summary.json", "group_equity.csv"):
        assert not (phase_dir / name).exists()
    assert not (
        run_dir / "candidates" / "academic_carhart_mom" / "out_of_sample"
    ).exists()
    assert pd.read_csv(run_dir / "artifacts" / "ic_series.csv").empty
    assert pd.read_csv(run_dir / "artifacts" / "group_equity.csv").empty
    for key in _UNAVAILABLE_QUALITY_KEYS:
        assert document["quality_bounds"][key] == "unavailable"
    print(
        "MISSING_EVIDENCE",
        json.dumps(
            {
                "progress": progress,
                "candidate": candidate,
                "artifact_paths": expected_paths,
            }
        ),
    )
