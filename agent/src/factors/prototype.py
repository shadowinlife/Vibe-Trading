"""Ordered, stop-aware execution of frozen factor prototype batches."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from src.factors.prototype_artifacts import quality_bounds, write_merged
from src.factors.prototype_contract import (
    ARTIFACT_FILENAMES,
    PhaseEvaluator,
    PhaseOutcome,
    PhaseRequest,
    PrototypeProgress,
    PrototypeResult,
    PrototypeRunRequest,
    PrototypeValidationError,
    PrototypeWorkError,
    PrototypeWorkLedger,
    _PhaseRecord,
    write_prototype_json,
)
from src.factors.prototype_native import (
    FrozenFactorEvaluator,
    fence_run_outputs,
    fenced_outcome,
)
from src.factors.registry import Registry, get_default_registry
from src.factors.registry_freeze import (
    RegistryFreeze,
    RegistryFreezeError,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    JsonValue,
)
from src.tools.path_utils import safe_run_dir

__all__ = [
    "FactorPrototypeRunner",
    "FrozenFactorEvaluator",
    "PhaseOutcome",
    "PhaseRequest",
    "PrototypeProgress",
    "PrototypeResult",
    "PrototypeRunRequest",
    "PrototypeValidationError",
    "PrototypeWorkError",
    "PrototypeWorkLedger",
    "write_prototype_json",
]

_PHASES: Final = ("in_sample", "out_of_sample")
_ARTIFACT_ORDER: Final = tuple(ARTIFACT_FILENAMES)
_METRIC_KEYS: Final = (
    "ic_mean",
    "ic_ir",
    "group_return_spread",
    "n_obs",
    "oos_ic_mean",
    "oos_n_obs",
    "sample_coverage",
    "missing_data_ratio",
    "oos_sample_coverage",
    "oos_missing_data_ratio",
)
_UNAVAILABLE: Final = "unavailable"


class FactorPrototypeRunner:
    """Evaluate declaration-order candidates without OOS selection or retries."""

    def __init__(
        self, registry: Registry | None = None, evaluator: PhaseEvaluator | None = None
    ) -> None:
        """Initialize the frozen registry and phase evaluator.

        Args:
            registry: Optional explicit registry; production uses the default registry.
            evaluator: Optional native-compatible evaluator used by focused tests.
        """
        self._registry = registry or get_default_registry()
        self._evaluator = evaluator or FrozenFactorEvaluator()

    def _freeze(self, spec: FactorPrototypeSpec) -> RegistryFreeze:
        try:
            frozen = freeze_registry(
                rows_from_registry(self._registry, spec.candidate_ids)
            )
        except RegistryFreezeError as exc:
            raise PrototypeValidationError("invalid_request") from exc
        if frozen.registry_digest != spec.registry_digest:
            raise PrototypeValidationError("input_digest_mismatch")
        return frozen

    def run(
        self,
        request: PrototypeRunRequest,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[PrototypeProgress], None] | None = None,
    ) -> PrototypeResult:
        """Run a validated batch; stop checks occur only between candidates.

        Args:
            request: Frozen spec, fenced run directory, and execution identities.
            should_stop: Optional stop predicate evaluated before each candidate.
            on_progress: Optional observer invoked after each candidate outcome.

        Returns:
            The 0013 result document and completed-only progress counts.

        Raises:
            PrototypeValidationError: If candidates or registry digest are invalid.
        """
        frozen = self._freeze(request.spec)
        run_dir = safe_run_dir(str(request.run_dir))
        fence_run_outputs(run_dir, request.spec)
        rows = {row.candidate_id: row for row in frozen.candidates}
        ledger = PrototypeWorkLedger()
        outcomes: list[dict[str, JsonValue]] = []
        records: list[_PhaseRecord] = []
        progress = PrototypeProgress(0, len(rows), 0, 0, 0)
        stopped = False
        failed = False
        for candidate_id in request.spec.candidate_ids:
            if should_stop is not None and should_stop():
                stopped = True
                break
            candidate = rows[candidate_id]
            span_days = (request.spec.split_date - request.spec.start_date).days
            candidate_records: list[_PhaseRecord] = []
            if candidate.min_warmup_bars > span_days:
                outcomes.append(
                    _candidate_document(
                        candidate_id,
                        "skipped",
                        (
                            f"warmup needs {candidate.min_warmup_bars} bars but the selection window spans {span_days} calendar days",
                        ),
                        candidate_records,
                    )
                )
            else:
                for phase in _PHASES:
                    phase_request = PhaseRequest(
                        request.spec,
                        candidate,
                        phase,
                        run_dir / "candidates" / candidate_id / phase,
                        ledger,
                    )
                    outcome = fenced_outcome(
                        phase_request, self._evaluator.evaluate(phase_request)
                    )
                    record = _PhaseRecord(candidate_id, phase, outcome)
                    records.append(record)
                    candidate_records.append(record)
                    if outcome.status != "ok":
                        break
                terminal = candidate_records[-1].outcome
                if terminal.status == "error":
                    failed = True
                    break
                status = (
                    "completed"
                    if terminal.status == "ok" and len(candidate_records) == 2
                    else "insufficient_data"
                )
                outcomes.append(
                    _candidate_document(
                        candidate_id, status, terminal.reasons, candidate_records
                    )
                )
            statuses = [str(outcome["status"]) for outcome in outcomes]
            progress = PrototypeProgress(
                statuses.count("completed"),
                len(request.spec.candidate_ids),
                statuses.count("skipped"),
                statuses.count("insufficient_data"),
                len(outcomes),
            )
            if on_progress is not None:
                on_progress(progress)
        status = "failed" if failed else "completed"
        if stopped:
            status = "partial" if outcomes else "cancelled"
        quality = quality_bounds(
            request.spec, outcomes, records, complete=status == "completed"
        )
        merged = write_merged(run_dir, request.spec, records, quality)
        paths = list(merged.values()) + [
            record.outcome.artifacts[key]
            for record in records
            for key in _ARTIFACT_ORDER
            if key in record.outcome.artifacts
        ]
        document: dict[str, JsonValue] = {
            "remote_id": request.remote_id,
            "dispatch_key": request.dispatch_key,
            "registry_digest": frozen.registry_digest,
            "tool_schema_version": frozen.tool_schema_version,
            "contract_version": CONTRACT_VERSION,
            "status": status,
            "completed": progress.completed,
            "total": progress.total,
            "candidates": outcomes,
            "quality_bounds": quality,
            "artifacts": merged,
            "artifact_paths": paths,
        }
        if failed:
            document["error_kind"] = "runner_error"
        return PrototypeResult(document, progress)


def _measured(metrics: dict[str, JsonValue], key: str) -> JsonValue:
    value = metrics.get(key)
    return value if value is not None else _UNAVAILABLE


def _candidate_document(
    candidate_id: str,
    status: str,
    reasons: tuple[str, ...],
    records: list[_PhaseRecord],
) -> dict[str, JsonValue]:
    metrics: dict[str, JsonValue] = {key: _UNAVAILABLE for key in _METRIC_KEYS}
    if status == "completed":
        in_sample, out_sample = (record.outcome.metrics for record in records)
        metrics.update(
            {
                "ic_mean": _measured(in_sample, "ic_mean"),
                "ic_ir": _measured(in_sample, "ic_ir"),
                "group_return_spread": _measured(in_sample, "group_return_spread"),
                "n_obs": _measured(in_sample, "n_obs"),
                "oos_ic_mean": _measured(out_sample, "ic_mean"),
                "oos_n_obs": _measured(out_sample, "n_obs"),
                "sample_coverage": _measured(in_sample, "coverage"),
                "missing_data_ratio": _measured(in_sample, "missing_ratio"),
                "oos_sample_coverage": _measured(out_sample, "coverage"),
                "oos_missing_data_ratio": _measured(out_sample, "missing_ratio"),
            }
        )
    return {
        "candidate_id": candidate_id,
        "status": status,
        "reasons": list(reasons),
        "metrics": metrics,
        "artifacts": {
            record.phase: {
                key: record.outcome.artifacts[key]
                for key in _ARTIFACT_ORDER
                if key in record.outcome.artifacts
            }
            for record in records
        },
    }
