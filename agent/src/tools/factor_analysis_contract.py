"""Honest quality bounds and filesystem fencing for native factor analysis.

``coverage`` uses every cell in the intersection of the two input panels:
``len(common_dates) * len(common_symbols)`` is the denominator and cells where
both factor and forward return are non-null are the numerator. ``n_obs`` is a
different measurement: it counts those pairs only on dates retained by the
native IC computation. ``ic_count`` remains the number of retained dates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

import pandas as pd

from src.tools.factor_analysis_tool import _run_native_factor_analysis
from src.tools.path_utils import safe_run_dir

_ARTIFACTS: Final = {
    "ic_series": "ic_series.csv",
    "ic_summary": "ic_summary.json",
    "group_equity": "group_equity.csv",
}


class ArtifactPaths(TypedDict):
    """Absolute paths for artifacts the native writer actually produced."""

    ic_series: str | None
    ic_summary: str | None
    group_equity: str | None


class FactorAnalysisResult(TypedDict, total=False):
    """JSON-compatible native result plus the additive contract fields."""

    status: str
    error: str
    error_kind: str
    reason: str
    ic_mean: float | None
    ic_std: float | None
    ic_ir: float | None
    ir: float | None
    ic_positive_ratio: float | None
    ic_count: int | None
    n_obs: int | None
    n_groups: int
    long_short_spread: float | None
    group_return_spread: float | None
    group_final_equity: dict[str, float]
    coverage: float | None
    missing_ratio: float | None
    sample_coverage: float | None
    missing_data_ratio: float | None
    output_dir: str
    files: list[str]
    artifacts: ArtifactPaths
    _ic_dates: list[str]


class FactorAnalysisPathError(ValueError):
    """A caller-supplied factor input or output path escaped its fence."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


@dataclass(frozen=True, slots=True)
class FactorAnalysisPaths:
    """Resolved absolute paths confined to allowed run roots."""

    factor_csv: Path
    return_csv: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """Measured pair count and grid coverage; None means unavailable."""

    n_obs: int | None
    coverage: float | None


def _fenced_absolute_path(raw: str, field: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise FactorAnalysisPathError(field, "path must be absolute")
    try:
        return safe_run_dir(raw)
    except ValueError as exc:
        raise FactorAnalysisPathError(field, str(exc)) from exc


def _resolve_paths(
    factor_csv: str, return_csv: str, output_dir: str
) -> FactorAnalysisPaths:
    return FactorAnalysisPaths(
        factor_csv=_fenced_absolute_path(factor_csv, "factor_csv"),
        return_csv=_fenced_absolute_path(return_csv, "return_csv"),
        output_dir=_fenced_absolute_path(output_dir, "output_dir"),
    )


def _check_artifact_targets(paths: FactorAnalysisPaths) -> None:
    """Reject escaping or input-aliasing targets before the native writer opens them."""
    for name in _ARTIFACTS.values():
        candidate = paths.output_dir / name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(paths.output_dir):
            raise FactorAnalysisPathError(
                "output_dir",
                f"artifact {name!r} resolves outside the fenced output directory",
            )
        if resolved in {paths.factor_csv, paths.return_csv}:
            raise FactorAnalysisPathError(
                "output_dir", f"artifact {name!r} must not alias an input CSV"
            )


def _read_panels(
    paths: FactorAnalysisPaths,
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    try:
        factor_df = pd.read_csv(paths.factor_csv, index_col=0, parse_dates=True)
        return_df = pd.read_csv(paths.return_csv, index_col=0, parse_dates=True)
    except (OSError, UnicodeError, pd.errors.ParserError, TypeError, ValueError):
        return None
    return factor_df, return_df


def _measure_quality(
    factor_df: pd.DataFrame, return_df: pd.DataFrame, ic_dates: pd.Index
) -> QualityMetrics:
    common_dates = factor_df.index.intersection(return_df.index)
    common_symbols = factor_df.columns.intersection(return_df.columns)
    denominator = len(common_dates) * len(common_symbols)
    if denominator == 0:
        return QualityMetrics(n_obs=None, coverage=None)

    factor_aligned = factor_df.loc[common_dates, common_symbols]
    return_aligned = return_df.loc[common_dates, common_symbols]
    pair_mask = factor_aligned.notna() & return_aligned.notna()
    paired_total = int(pair_mask.to_numpy().sum())
    n_obs = int(pair_mask.loc[ic_dates].to_numpy().sum()) if len(ic_dates) else None
    coverage = paired_total / denominator if paired_total else None
    return QualityMetrics(n_obs=n_obs, coverage=coverage)


def _empty_artifacts() -> ArtifactPaths:
    return {"ic_series": None, "ic_summary": None, "group_equity": None}


def _written_artifacts(result: FactorAnalysisResult, output_dir: Path) -> ArtifactPaths:
    artifacts = _empty_artifacts()
    claimed_files = result.get("files", [])
    for key, name in _ARTIFACTS.items():
        candidate = output_dir / name
        if (
            name in claimed_files
            and candidate.resolve().is_relative_to(output_dir)
            and candidate.is_file()
        ):
            artifacts[key] = str(candidate)
    return artifacts


def _clear_metrics(result: FactorAnalysisResult) -> None:
    result["ic_mean"] = None
    result["ic_std"] = None
    result["ic_ir"] = None
    result["ir"] = None
    result["ic_positive_ratio"] = None
    result["ic_count"] = None
    result["n_obs"] = None
    result["long_short_spread"] = None
    result["group_return_spread"] = None
    result["coverage"] = None
    result["missing_ratio"] = None
    result["sample_coverage"] = None
    result["missing_data_ratio"] = None


def _path_error(exc: FactorAnalysisPathError) -> str:
    result = FactorAnalysisResult(
        status="error", error=str(exc), error_kind="invalid_request"
    )
    _clear_metrics(result)
    result["artifacts"] = _empty_artifacts()
    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)


def run_factor_analysis_contract(
    factor_csv: str, return_csv: str, output_dir: str, n_groups: int = 5
) -> str:
    """Run native analysis and add measured quality bounds and fenced artifacts.

    The four arguments intentionally mirror the legacy public signature. Native
    IC values and ``long_short_spread`` are preserved; ``group_return_spread``
    aliases that final-equity delta and is not a per-period return. Any metric
    the native implementation did not measure is JSON null, never a zero.

    Args:
        factor_csv: Absolute factor-panel CSV inside an allowed run root.
        return_csv: Absolute forward-return-panel CSV inside an allowed run root.
        output_dir: Absolute artifact directory inside an allowed run root.
        n_groups: Number of native quantile groups.

    Returns:
        JSON containing every native response key plus measured quality bounds,
        spread aliases, and only the artifacts actually written by native code.
    """
    try:
        paths = _resolve_paths(factor_csv, return_csv, output_dir)
        _check_artifact_targets(paths)
    except FactorAnalysisPathError as exc:
        return _path_error(exc)

    native_raw = _run_native_factor_analysis(
        str(paths.factor_csv),
        str(paths.return_csv),
        str(paths.output_dir),
        n_groups=n_groups,
    )
    result: FactorAnalysisResult = json.loads(native_raw)
    ic_dates = pd.DatetimeIndex(result.pop("_ic_dates", []))
    result["artifacts"] = _written_artifacts(result, paths.output_dir)

    panels = _read_panels(paths)
    native_status = result.get("status")
    if native_status == "error" and result.get("error_kind") == "insufficient_data":
        result["status"] = "insufficient_data"
        result["reason"] = result.get("error", "insufficient valid factor/return pairs")

    if result.get("status") == "ok" and panels is not None:
        factor_df, return_df = panels
        quality = _measure_quality(factor_df, return_df, ic_dates)
        result["ic_ir"] = result.get("ir")
        result["group_return_spread"] = result.get("long_short_spread")
        result["n_obs"] = quality.n_obs
        coverage = round(quality.coverage, 6) if quality.coverage is not None else None
        result["coverage"] = coverage
        result["missing_ratio"] = (
            round(1.0 - coverage, 6) if coverage is not None else None
        )
        result["sample_coverage"] = coverage
        result["missing_data_ratio"] = result["missing_ratio"]
    else:
        _clear_metrics(result)

    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
