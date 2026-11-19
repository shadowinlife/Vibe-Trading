"""Behavioral contract tests for bounded factor-analysis quality metrics."""

# allow: SIZE_OK — task 14 requires one focused test module below 400 total lines.

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd
import pytest

from src.tools.factor_analysis_contract import run_factor_analysis_contract

_ARTIFACT_NAMES = ("ic_series.csv", "ic_summary.json", "group_equity.csv")


class ArtifactPaths(TypedDict):
    ic_series: str | None
    ic_summary: str | None
    group_equity: str | None


class ContractResult(TypedDict, total=False):
    status: str
    error: str
    error_kind: str
    ic_mean: float | None
    ic_ir: float | None
    ir: float | None
    ic_count: int | None
    n_obs: int | None
    group_return_spread: float | None
    long_short_spread: float | None
    coverage: float | None
    missing_ratio: float | None
    sample_coverage: float | None
    missing_data_ratio: float | None
    artifacts: ArtifactPaths
    files: list[str]


def _allow_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


def _write_known_panel(root: Path) -> tuple[Path, Path]:
    """Write a panel whose independent hand calculations are documented below."""
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    codes = [f"C{i}" for i in range(6)]
    factor = pd.DataFrame(
        [[float(column + 1) for column in range(6)] for _ in range(3)],
        index=dates,
        columns=codes,
    )
    returns = pd.DataFrame(
        [
            [0.00, 0.01, 0.02, 0.03, 0.04, 0.05],
            [0.01, 0.02, 0.03, 0.04, 0.05, np.nan],
            [-0.01, 0.00, 0.01, 0.02, 0.04, np.nan],
        ],
        index=dates,
        columns=codes,
    )
    factor.loc[dates[2], "C4"] = np.nan
    # Extra rows/columns are outside the common-date/common-symbol denominator.
    returns["C6"] = 0.5
    returns.loc[dates[-1] + pd.Timedelta(days=1)] = 0.5
    factor_csv = inputs / "factor.csv"
    return_csv = inputs / "returns.csv"
    factor.to_csv(factor_csv)
    returns.to_csv(return_csv)
    return factor_csv, return_csv


def _run_known_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ContractResult, Path]:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    output_dir = root / "runs" / "analysis"
    result: ContractResult = json.loads(
        run_factor_analysis_contract(
            str(factor_csv), str(return_csv), str(output_dir), n_groups=2
        )
    )
    return result, output_dir


def test_contract_preserves_native_ic_and_final_equity_spread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two retained ICs are +1; final group NAVs are 1.025049 and 1.103102."""
    result, _ = _run_known_analysis(tmp_path, monkeypatch)

    assert result["status"] == "ok"
    assert result["ic_mean"] == 1.0
    assert result["ic_ir"] == result["ir"]
    assert result["group_return_spread"] == result["long_short_spread"] == 0.0781


def test_contract_counts_pairs_only_on_retained_ic_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six pairs on day 1 plus five on day 2; day 3 has four and is not retained."""
    result, _ = _run_known_analysis(tmp_path, monkeypatch)

    assert result["ic_count"] == 2
    assert result["n_obs"] == 11


def test_contract_measures_coverage_over_the_complete_common_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The denominator is 3 common dates x 6 common symbols, not retained IC rows."""
    result, _ = _run_known_analysis(tmp_path, monkeypatch)

    assert result["coverage"] == result["sample_coverage"] == 0.833333
    assert result["missing_ratio"] == result["missing_data_ratio"] == 0.166667


def test_contract_derives_missing_ratio_from_published_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    codes = [f"C{i}" for i in range(32)]
    factor = pd.DataFrame(np.nan, index=dates, columns=codes)
    returns = pd.DataFrame(np.nan, index=dates, columns=codes)
    factor.iloc[0, :5] = [1.0, 2.0, 3.0, 4.0, 5.0]
    returns.iloc[0, :5] = [0.01, 0.02, 0.03, 0.04, 0.05]
    factor.iloc[1, :2] = [1.0, 2.0]
    returns.iloc[1, :2] = [0.01, 0.02]
    factor_csv = inputs / "factor.csv"
    return_csv = inputs / "returns.csv"
    factor.to_csv(factor_csv)
    returns.to_csv(return_csv)

    result: ContractResult = json.loads(
        run_factor_analysis_contract(
            str(factor_csv),
            str(return_csv),
            str(root / "runs" / "rounding"),
            n_groups=2,
        )
    )

    assert result["status"] == "ok"
    assert result["n_obs"] == 5
    assert result["coverage"] == 0.010937
    assert result["missing_ratio"] == 0.989063


def test_contract_publishes_real_bounded_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, output_dir = _run_known_analysis(tmp_path, monkeypatch)

    assert result["artifacts"] == {
        "ic_series": str(output_dir / "ic_series.csv"),
        "ic_summary": str(output_dir / "ic_summary.json"),
        "group_equity": str(output_dir / "group_equity.csv"),
    }
    assert result["files"] == list(_ARTIFACT_NAMES)
    for name in _ARTIFACT_NAMES:
        assert (output_dir / name).is_file()


def test_contract_artifact_paths_and_bytes_are_stable_on_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, output_dir = _run_known_analysis(tmp_path, monkeypatch)
    before = {name: (output_dir / name).read_bytes() for name in _ARTIFACT_NAMES}

    second, _ = _run_known_analysis(tmp_path, monkeypatch)

    assert second["artifacts"] == first["artifacts"]
    assert {
        name: (output_dir / name).read_bytes() for name in _ARTIFACT_NAMES
    } == before


@pytest.mark.parametrize("valid_pairs", [0, 4])
def test_contract_marks_insufficient_pairs_unavailable_instead_of_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_pairs: int
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    date = pd.Timestamp("2026-01-01")
    codes = [f"C{i}" for i in range(6)]
    factor = pd.DataFrame(
        [[float(i + 1) for i in range(6)]], index=[date], columns=codes
    )
    returns = pd.DataFrame(
        [[0.01 * (i + 1) if i < valid_pairs else np.nan for i in range(6)]],
        index=[date],
        columns=codes,
    )
    factor_csv = inputs / "factor.csv"
    return_csv = inputs / "returns.csv"
    factor.to_csv(factor_csv)
    returns.to_csv(return_csv)
    output_dir = root / "runs" / "insufficient"

    result: ContractResult = json.loads(
        run_factor_analysis_contract(
            str(factor_csv), str(return_csv), str(output_dir), n_groups=2
        )
    )

    assert result["status"] == "insufficient_data"
    for key in (
        "ic_mean",
        "ic_ir",
        "ic_count",
        "n_obs",
        "group_return_spread",
        "coverage",
        "missing_ratio",
        "sample_coverage",
        "missing_data_ratio",
    ):
        assert result[key] is None
    assert result["artifacts"] == {
        "ic_series": None,
        "ic_summary": None,
        "group_equity": None,
    }
    for name in _ARTIFACT_NAMES:
        assert not (output_dir / name).exists()


def test_contract_preserves_partial_artifacts_when_group_equity_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    returns = pd.read_csv(return_csv, index_col=0)
    returns.iloc[:] = np.nan
    returns.iloc[0, :5] = [0.00, 0.01, 0.02, 0.03, 0.04]
    returns.iloc[1, :3] = [0.01, 0.02, 0.03]
    returns.to_csv(return_csv)
    output_dir = root / "runs" / "partial"

    result: ContractResult = json.loads(
        run_factor_analysis_contract(
            str(factor_csv), str(return_csv), str(output_dir), n_groups=6
        )
    )

    assert result["status"] == "insufficient_data"
    assert result["group_return_spread"] is None
    artifacts = result["artifacts"]
    assert artifacts["ic_series"] is not None
    assert artifacts["ic_summary"] is not None
    assert artifacts["group_equity"] is None
    assert Path(artifacts["ic_series"]).is_file()
    assert Path(artifacts["ic_summary"]).is_file()
    assert not (output_dir / "group_equity.csv").exists()


def _assert_path_rejected(result: ContractResult) -> None:
    assert result["status"] == "error"
    assert result["error_kind"] == "invalid_request"
    assert result["artifacts"] == {
        "ic_series": None,
        "ic_summary": None,
        "group_equity": None,
    }


@pytest.mark.parametrize("relative", [True, False], ids=["relative", "traversal"])
def test_contract_rejects_output_path_escape_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    if relative:
        monkeypatch.chdir(tmp_path)
        output_dir = Path("relative-output")
        outside = tmp_path / output_dir
    else:
        output_dir = root / ".." / "outside"
        outside = tmp_path / "outside"

    result: ContractResult = json.loads(
        run_factor_analysis_contract(str(factor_csv), str(return_csv), str(output_dir))
    )

    _assert_path_rejected(result)
    assert not outside.exists()


def test_contract_rejects_input_artifact_alias_before_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    output_dir = root / "runs" / "alias"
    output_dir.mkdir(parents=True)
    aliased_input = output_dir / "ic_series.csv"
    original = factor_csv.read_bytes()
    aliased_input.write_bytes(original)

    result: ContractResult = json.loads(
        run_factor_analysis_contract(
            str(aliased_input), str(return_csv), str(output_dir)
        )
    )

    _assert_path_rejected(result)
    assert aliased_input.read_bytes() == original


def test_contract_rejects_input_symlink_escape_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_factor = outside / "factor.csv"
    outside_factor.write_bytes(factor_csv.read_bytes())
    link = root / "inputs" / "factor-link.csv"
    try:
        link.symlink_to(outside_factor)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    output_dir = root / "runs" / "analysis"

    result: ContractResult = json.loads(
        run_factor_analysis_contract(str(link), str(return_csv), str(output_dir))
    )

    _assert_path_rejected(result)
    assert not output_dir.exists()


def test_contract_rejects_output_symlink_escape_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    output_parent = root / "runs"
    output_parent.mkdir(parents=True)
    link = output_parent / "analysis"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result: ContractResult = json.loads(
        run_factor_analysis_contract(str(factor_csv), str(return_csv), str(link))
    )

    _assert_path_rejected(result)
    assert all(not (outside / name).exists() for name in _ARTIFACT_NAMES)


def test_contract_rejects_artifact_symlink_escape_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _allow_root(monkeypatch, tmp_path)
    factor_csv, return_csv = _write_known_panel(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    output_dir = root / "runs" / "analysis"
    output_dir.mkdir(parents=True)
    stolen = outside / "stolen.csv"
    try:
        (output_dir / "ic_series.csv").symlink_to(stolen)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result: ContractResult = json.loads(
        run_factor_analysis_contract(str(factor_csv), str(return_csv), str(output_dir))
    )

    _assert_path_rejected(result)
    assert not stolen.exists()
