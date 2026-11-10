"""Quality aggregation and merged evidence artifacts for factor prototypes."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from src.factors.prototype_contract import write_prototype_json
from src.strategy_library.paths import atomic_write_text
from src.strategy_runs.control_contract import FactorPrototypeSpec, JsonValue

if TYPE_CHECKING:
    from src.factors.prototype_contract import _PhaseRecord

_UNAVAILABLE = "unavailable"


def quality_bounds(
    spec: FactorPrototypeSpec,
    outcomes: list[dict[str, JsonValue]],
    records: list[_PhaseRecord],
    *,
    complete: bool,
) -> dict[str, JsonValue]:
    """Aggregate quality only for a fully measured frozen batch.

    Coverage and in-sample group spread are observation-weighted means. Missing
    ratio is derived from rounded coverage. Any incomplete or unmeasured batch
    keeps those values ``unavailable`` rather than zero-filled.

    Args:
        spec: Frozen factor prototype declaration.
        outcomes: Candidate outcome documents produced so far.
        records: Ordered native phase outcomes produced so far.
        complete: Whether the orchestration loop finished every candidate.

    Returns:
        Selection/OOS windows, measured or unavailable quality, and overfit caveat.
    """
    measured = [
        record.outcome.metrics for record in records if record.outcome.status == "ok"
    ]
    in_sample = [
        record.outcome.metrics
        for record in records
        if record.phase == "in_sample" and record.outcome.status == "ok"
    ]
    complete = (
        complete
        and len(outcomes) == len(spec.candidate_ids)
        and all(outcome["status"] == "completed" for outcome in outcomes)
    )
    coverage: JsonValue = _UNAVAILABLE
    missing: JsonValue = _UNAVAILABLE
    n_obs: JsonValue = _UNAVAILABLE
    spread: JsonValue = _UNAVAILABLE
    observations = sum(int(metrics.get("n_obs") or 0) for metrics in measured)
    measurable = all(
        metrics.get("coverage") is not None and metrics.get("missing_ratio") is not None
        for metrics in measured
    )
    if complete and measured and observations and measurable:
        coverage = round(
            sum(
                float(metrics["coverage"]) * int(metrics["n_obs"])
                for metrics in measured
            )
            / observations,
            6,
        )
        missing = round(1.0 - float(coverage), 6)
        n_obs = observations
        spread_observations = sum(
            int(metrics.get("n_obs") or 0) for metrics in in_sample
        )
        if (
            len(in_sample) == len(spec.candidate_ids)
            and spread_observations
            and all(
                metrics.get("group_return_spread") is not None for metrics in in_sample
            )
        ):
            spread = round(
                sum(
                    float(metrics["group_return_spread"]) * int(metrics["n_obs"])
                    for metrics in in_sample
                )
                / spread_observations,
                6,
            )
    caveat = "selection uses only the in-sample window; out-of-sample evidence never feeds selection or ranking"
    if not complete:
        caveat += "; unmeasured or incomplete candidates are unavailable, never zero-filled or PASS"
    return {
        "selection_window": [
            spec.start_date.isoformat(),
            (spec.split_date - timedelta(days=1)).isoformat(),
        ],
        "oos_window": [spec.split_date.isoformat(), spec.end_date.isoformat()],
        "sample_coverage": coverage,
        "missing_data_ratio": missing,
        "coverage": coverage,
        "missing_ratio": missing,
        "n_obs": n_obs,
        "group_return_spread": spread,
        "overfit_caveat": caveat,
    }


def write_merged(
    run_dir: Path,
    spec: FactorPrototypeSpec,
    records: list[_PhaseRecord],
    quality: dict[str, JsonValue],
) -> dict[str, str]:
    """Write merged artifacts without changing native per-phase files.

    Args:
        run_dir: Fenced directory for this prototype run.
        spec: Frozen factor prototype declaration.
        records: Ordered, already fenced native phase outcomes.
        quality: Run-level quality document embedded in ``ic_summary.json``.

    Returns:
        Paths for merged ``ic_series``, ``ic_summary``, and ``group_equity``.
    """
    output = run_dir / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "ic_series": str(output / "ic_series.csv"),
        "ic_summary": str(output / "ic_summary.json"),
        "group_equity": str(output / "group_equity.csv"),
    }
    for key in ("ic_series", "group_equity"):
        frames = []
        for record in records:
            raw = record.outcome.artifacts.get(key)
            if raw is not None:
                frame = pd.read_csv(raw)
                frame.insert(0, "phase", record.phase)
                frame.insert(0, "candidate_id", record.candidate_id)
                frames.append(frame)
        merged = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=["candidate_id", "phase"])
        )
        atomic_write_text(Path(paths[key]), merged.to_csv(index=False))
    by_phase = {(record.candidate_id, record.phase): record for record in records}
    summaries = []
    for candidate_id in spec.candidate_ids:
        item: dict[str, JsonValue] = {"candidate_id": candidate_id}
        for phase in ("in_sample", "out_of_sample"):
            record = by_phase.get((candidate_id, phase))
            raw = record.outcome.artifacts.get("ic_summary") if record else None
            item[phase] = (
                json.loads(Path(raw).read_text(encoding="utf-8"))
                if raw
                else _UNAVAILABLE
            )
        summaries.append(item)
    write_prototype_json(
        Path(paths["ic_summary"]),
        {"candidates": summaries, "quality_bounds": quality},
    )
    return paths
