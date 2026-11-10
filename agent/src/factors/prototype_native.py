"""Native frozen-tool evaluation for factor prototypes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from src.factors.prototype_contract import (
    ARTIFACT_FILENAMES,
    PhaseOutcome,
    PrototypeValidationError,
)
from src.strategy_library.paths import atomic_write_text
from src.strategy_runs.control_contract import FactorPrototypeSpec, JsonValue
from src.tools.alpha_bench_frozen import run_frozen_bench
from src.tools.factor_analysis_contract import run_factor_analysis_contract

if TYPE_CHECKING:
    from src.factors.prototype_contract import PhaseRequest


def _existing_artifacts(output_dir: Path, names: tuple[str, ...]) -> dict[str, str]:
    artifacts = {}
    for key in names:
        candidate = output_dir / ARTIFACT_FILENAMES[key]
        if candidate.is_file() and candidate.resolve().parent == output_dir.resolve():
            artifacts[key] = str(candidate.resolve())
    return artifacts


def _declared_analysis_artifacts(
    output_dir: Path, analysis: Mapping[str, JsonValue]
) -> dict[str, str]:
    claimed = analysis.get("artifacts")
    if not isinstance(claimed, dict):
        return {}
    artifacts = {}
    for key in ("ic_series", "group_equity", "ic_summary"):
        raw = claimed.get(key)
        if not isinstance(raw, str):
            continue
        expected = (output_dir / ARTIFACT_FILENAMES[key]).resolve()
        candidate = Path(raw).resolve()
        if (
            candidate == expected
            and candidate.is_file()
            and candidate.parent == output_dir.resolve()
        ):
            artifacts[key] = str(candidate)
    return artifacts


def _write_analysis_inputs(request: PhaseRequest) -> tuple[Path, Path]:
    """Write atomic phase-local inputs without modifying frozen bench CSVs."""
    input_dir = request.output_dir / "analysis_inputs"
    frames: dict[str, pd.DataFrame] = {}
    for filename in ("factor.csv", "returns.csv"):
        frame = pd.read_csv(
            request.output_dir / filename, index_col=0, parse_dates=True
        )
        selected = (frame.index >= pd.Timestamp(request.start_date)) & (
            frame.index <= pd.Timestamp(request.end_date)
        )
        frames[filename] = frame.loc[selected]
    # Label-crossing rows are masked to NaN by the frozen bench. They are
    # excluded from observations, IC and coverage, so trim the unusable tail
    # instead of counting it as missing input.
    usable = frames["returns.csv"].notna().any(axis=1)
    if not bool(usable.all()):
        positions = usable.to_numpy().nonzero()[0]
        cutoff = int(positions[-1]) if positions.size else -1
        frames = {name: frame.iloc[: cutoff + 1] for name, frame in frames.items()}
    paths = []
    for filename, frame in frames.items():
        path = input_dir / filename
        atomic_write_text(
            path,
            frame.to_csv(index_label="date", date_format="%Y-%m-%d"),
        )
        paths.append(path)
    return paths[0], paths[1]


def fence_run_outputs(run_dir: Path, spec: FactorPrototypeSpec) -> None:
    """Reject existing output symlinks that escape the fenced run directory.

    Args:
        run_dir: Resolved server-owned directory for this run.
        spec: Frozen candidates whose complete output layout is preflighted.

    Returns:
        None when every current or future target remains inside ``run_dir``.

    Raises:
        PrototypeValidationError: If any output target resolves outside run_dir.
    """
    targets = [run_dir / "artifacts"]
    targets.extend(
        run_dir / "artifacts" / name
        for name in ("ic_series.csv", "ic_summary.json", "group_equity.csv")
    )
    for candidate_id in spec.candidate_ids:
        for phase in ("in_sample", "out_of_sample"):
            base = run_dir / "candidates" / candidate_id / phase
            targets.extend((base, base / "analysis_inputs"))
            targets.extend(base / name for name in ARTIFACT_FILENAMES.values())
            targets.extend(
                base / "analysis_inputs" / name
                for name in ("factor.csv", "returns.csv")
            )
    for target in targets:
        if not target.resolve().is_relative_to(run_dir):
            raise PrototypeValidationError("invalid_request")


def fenced_outcome(request: PhaseRequest, outcome: PhaseOutcome) -> PhaseOutcome:
    """Retain only real direct-child artifacts and reject incomplete success.

    Args:
        request: Candidate/phase request whose output directory owns the files.
        outcome: Native or injected phase outcome to validate.

    Returns:
        The outcome with fenced paths, or an error when mandatory files are absent.
    """
    output_dir = request.output_dir.resolve()
    artifacts = {}
    for key, raw in outcome.artifacts.items():
        path = Path(raw).resolve()
        if path.is_file() and path.parent == output_dir:
            artifacts[key] = str(path)
    if outcome.status == "ok" and not ARTIFACT_FILENAMES.keys() <= artifacts.keys():
        return PhaseOutcome(
            "error",
            (*outcome.reasons, "missing_required_artifacts"),
            {},
            artifacts,
        )
    return PhaseOutcome(outcome.status, outcome.reasons, outcome.metrics, artifacts)


class FrozenFactorEvaluator:
    """Adapter over todo 13's frozen bench and todo 14's native analysis."""

    def evaluate(self, request: PhaseRequest) -> PhaseOutcome:
        """Run one candidate/phase through the frozen native tool path.

        The frozen bench requires the full parent chronology. Its label-end mask
        selects the phase; atomic ``analysis_inputs`` copies then restrict the
        native coverage denominator to that same phase without rewriting bench CSVs.

        Args:
            request: Typed candidate/phase declaration and duplicate-work ledger.

        Returns:
            Native metrics, reasons, and direct-child artifact paths.
        """
        spec = request.spec
        payload = {
            "candidate_id": request.candidate.candidate_id,
            "phase": request.phase,
            "symbols": list(spec.symbols),
            # The frozen tool needs the full parent window; its mask owns each phase.
            "start_date": spec.start_date.isoformat(),
            "end_date": spec.end_date.isoformat(),
            "horizon": spec.horizon_days,
            "source": spec.source,
            "split_date": spec.split_date.isoformat(),
            "as_of": spec.as_of.isoformat(),
            "output_dir": str(request.output_dir),
        }
        request.ledger.admit("alpha_bench", payload)
        bench = run_frozen_bench(payload)
        artifacts = _existing_artifacts(
            request.output_dir, ("summary", "factor_csv", "returns_csv")
        )
        if bench.get("status") != "ok":
            reasons = tuple(str(value) for value in bench.get("reasons", ())) or (
                str(bench.get("error", "frozen bench failed")),
            )
            status = "error" if bench.get("status") == "error" else "insufficient_data"
            return PhaseOutcome(status, reasons, {}, artifacts)

        factor_csv, return_csv = _write_analysis_inputs(request)
        arguments = {
            "factor_csv": str(factor_csv),
            "return_csv": str(return_csv),
            "output_dir": str(request.output_dir),
            "n_groups": spec.n_groups,
        }
        request.ledger.admit("factor_analysis", arguments)
        analysis = json.loads(run_factor_analysis_contract(**arguments))
        artifacts.update(_declared_analysis_artifacts(request.output_dir, analysis))
        status = analysis.get("status")
        reasons = tuple(
            str(analysis[key]) for key in ("reason", "error") if analysis.get(key)
        )
        metrics = {
            key: analysis.get(key)
            for key in (
                "ic_mean",
                "ic_ir",
                "group_return_spread",
                "n_obs",
                "coverage",
                "missing_ratio",
                "ic_count",
                "n_groups",
            )
        }
        if status == "error":
            return PhaseOutcome("error", reasons, {}, artifacts)
        if status != "ok":
            return PhaseOutcome("insufficient_data", reasons, {}, artifacts)
        return PhaseOutcome("ok", reasons, metrics, artifacts)
