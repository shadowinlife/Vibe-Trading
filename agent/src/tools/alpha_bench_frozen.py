"""Execute one frozen alpha-zoo candidate without legacy bench side effects."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from src.factors.registry import RegistryError, SkipAlpha, get_default_registry
from src.tools.alpha_bench_contract import (
    FrozenBenchError,
    FrozenBenchSpec,
    FrozenFrames,
    build_frozen_frames,
    validate_frozen_bench_spec,
)
from src.tools.alpha_bench_tool import (
    _compute_forward_returns,
    _prepare_bench_panel,
    _wide_from_fetched,
)


def _load_symbols(
    symbols: tuple[str, ...], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Load one explicit symbol profile through the shared market-data seam.

    Args:
        symbols: Requested symbols in their submitted order.
        start: First requested trading date in ISO format.
        end: Last requested trading date in ISO format.

    Returns:
        Wide OHLCV frames keyed by field.
    """
    from src.market_data import fetch_market_data, get_loader

    response = fetch_market_data(
        codes=list(symbols),
        start_date=start,
        end_date=end,
        source="auto",
        include_provenance=True,
        as_frames=True,
        loader_resolver=get_loader,
    )
    fetched = {
        code: frame for code, frame in response.items() if not code.startswith("_")
    }
    return _wide_from_fetched(fetched, include_amount=False)


def _insufficient_frames(reason: str) -> FrozenFrames:
    """Represent an honest empty outcome without inventing market data.

    Args:
        reason: Stable explanation for the insufficient-data outcome.

    Returns:
        Empty frames with zero counts and the supplied reason.
    """
    empty = pd.DataFrame()
    return FrozenFrames(
        factor=empty,
        forward_returns=empty,
        n_obs=0,
        n_dates=0,
        status="insufficient_data",
        reasons=(reason,),
    )


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a deterministically ordered CSV and atomically publish it.

    Args:
        frame: Factor or masked-return frame to serialize.
        path: Final CSV path inside the fenced output directory.

    Returns:
        None.
    """
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    ordered = frame.sort_index(axis=0).sort_index(axis=1)
    ordered.to_csv(temporary, index_label="date", date_format="%Y-%m-%d")
    temporary.replace(path)


def _finish(spec: FrozenBenchSpec, frames: FrozenFrames) -> dict[str, Any]:
    """Publish only the artifacts justified by the frozen-frame outcome.

    Args:
        spec: Validated frozen execution specification.
        frames: Masked frames or an honest insufficient-data outcome.

    Returns:
        The frozen execution envelope and published CSV paths, if any.
    """
    summary = {
        "status": frames.status,
        "candidate_id": spec.candidate_id,
        "phase": spec.phase,
        "horizon": spec.horizon,
        "windows": {
            "start_date": spec.start_date.isoformat(),
            "split_date": spec.split_date.isoformat(),
            "end_date": spec.end_date.isoformat(),
            "as_of": spec.as_of.isoformat(),
        },
        "n_obs": frames.n_obs,
        "n_dates": frames.n_dates,
        "reasons": list(frames.reasons),
    }
    spec.output_dir.mkdir(parents=True, exist_ok=True)

    factor_path: Path | None = None
    returns_path: Path | None = None
    if frames.status == "ok":
        factor_path = spec.output_dir / "factor.csv"
        returns_path = spec.output_dir / "returns.csv"
        _atomic_csv(frames.factor, factor_path)
        _atomic_csv(frames.forward_returns, returns_path)
    else:
        (spec.output_dir / "factor.csv").unlink(missing_ok=True)
        (spec.output_dir / "returns.csv").unlink(missing_ok=True)

    summary_path = spec.output_dir / "summary.json"
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(summary_path)

    result = {
        "status": frames.status,
        "candidate_id": spec.candidate_id,
        "phase": spec.phase,
        "horizon": spec.horizon,
        "factor_csv": str(factor_path) if factor_path is not None else None,
        "returns_csv": str(returns_path) if returns_path is not None else None,
        "n_obs": frames.n_obs,
        "n_dates": frames.n_dates,
        "reasons": list(frames.reasons),
        "output_dir": str(spec.output_dir),
    }
    if frames.reasons:
        result["reason"] = frames.reasons[0]
    return result


def run_frozen_bench(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run a validated candidate and emit masked factor/return CSV artifacts.

    Args:
        payload: Frozen candidate, explicit profile, window, and output fields.

    Returns:
        A success, insufficient-data, or validation/registry error envelope.
    """
    try:
        spec = validate_frozen_bench_spec(payload)
    except FrozenBenchError as exc:
        return {"status": "error", "error": str(exc), "field": exc.field}

    panel = _load_symbols(spec.symbols, str(spec.start_date), str(spec.end_date))
    close = panel.get("close")
    if close is None or close.empty:
        return _finish(spec, _insufficient_frames("no_market_data"))

    registry = get_default_registry()
    try:
        registry.get(spec.candidate_id)
    except KeyError as exc:
        return {"status": "error", "error": str(exc)}

    try:
        panel = _prepare_bench_panel(
            registry,
            [spec.candidate_id],
            panel,
            universe="explicit",
            period=f"{spec.start_date}:{spec.end_date}",
        )
        forward = _compute_forward_returns(panel, horizon=spec.horizon)
        factor = registry.compute(spec.candidate_id, panel)
    except (SkipAlpha, RegistryError, RuntimeError, ValueError) as exc:
        return _finish(spec, _insufficient_frames(str(exc)))

    frames = build_frozen_frames(
        factor=factor,
        forward_returns=forward,
        phase=spec.phase,
        horizon=spec.horizon,
        split_date=spec.split_date,
        end_date=spec.end_date,
        as_of=spec.as_of,
    )
    return _finish(spec, frames)
