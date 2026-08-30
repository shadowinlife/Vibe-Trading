"""Eval-local quant scorecard gate for the deterministic eval runner.

Ports the evidence/claims gate that ``runner.py`` consumed before revert
035673b0 removed ``src/reliability/quant/scorecard.py``. The gate logic in
:func:`build_scorecard` is a verbatim port of the pre-revert implementation
(commit 035673b0^); the result model is slimmed to exactly the fields the
eval runner reads (``scorecard_id`` / ``score`` / ``score_breakdown`` /
``conclusion_cap`` / ``warnings`` / ``hard_failures``) — the analytics
sections of the original ``BacktestReliabilityScorecard`` (crowding, regime
IC, walk-forward, capacity, ...) were artifact-rendering concerns the runner
never consumes. Like :mod:`src.evals.agent_eval._policy`, this module is
deterministic offline test infrastructure: it gates synthesized eval traces,
not live backtests.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCORECARD_SCHEMA_VERSION = "1.0.0"

SCORECARD_DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "pit_clean",
        "oos_split",
        "cost_model",
        "benchmark",
        "trial_count",
        "execution_realism",
        "universe_pit",
        "capacity",
        "cost_sensitivity",
        "ic_stability",
        "regime_stability",
        "crowding_risk",
        "random_control",
    }
)

HARD_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "PIT_FUTURE_DATA",
        "QUANT_NO_COST_MODEL_TRADABLE_CLAIM",
        "QUANT_NO_BENCHMARK_ALPHA_CLAIM",
        "QUANT_NO_OOS_GENERALIZATION_CLAIM",
        "QUANT_HISTORICAL_UNIVERSE_MISSING",
        "QUANT_EXECUTION_TIMESTAMPS_MISSING",
        "QUANT_TRIAL_COUNT_MISSING_BEST_TRIAL",
        "QUANT_ASHARE_MARKET_RULES_MISSING",
        "POLICY_DENY_IGNORED",
        "QUANT_SCORECARD_LLM_OVERRIDE_ATTEMPT",
        "QUANT_HIGH_CROWDING_NO_STRESS_TEST",
        "QUANT_REGIME_NEGATIVE_IC_NO_ACTIVATION",
        "QUANT_IS_IC_NEAR_ZERO",
    }
)

ConclusionCap = Literal[
    "exploratory",
    "research_candidate",
    "paper_trade_candidate",
    "not_reliable",
]


class QuantIssue(BaseModel):
    """Structured warning or hard failure for scorecard gates."""

    model_config = ConfigDict(allow_inf_nan=False)

    code: str
    severity: Literal["info", "warning", "hard_failure"] = "warning"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionTimestampSet(BaseModel):
    """Execution realism timestamps required for tradability claims."""

    model_config = ConfigDict(allow_inf_nan=False)

    signal_time: bool = True
    decision_time: bool = True
    order_time: bool = True
    fill_time: bool = True
    price_time: bool = True

    def missing_fields(self) -> list[str]:
        """Return timestamp labels that are not available."""
        return [
            name
            for name in (
                "signal_time",
                "decision_time",
                "order_time",
                "fill_time",
                "price_time",
            )
            if not bool(getattr(self, name))
        ]

    def all_present(self) -> bool:
        """Return whether every required execution timestamp is present."""
        return not self.missing_fields()


class ClaimSet(BaseModel):
    """Claims made by a result/report that require evidence gates."""

    model_config = ConfigDict(allow_inf_nan=False)

    tradable: bool = False
    paper_tradable: bool = False
    live_tradable: bool = False
    generalization: bool = False
    alpha: bool = False
    best_trial: bool = False

    def claims_tradability(self) -> bool:
        """Return whether any claim implies paper/live/tradable readiness."""
        return self.tradable or self.paper_tradable or self.live_tradable


class EvidenceSet(BaseModel):
    """Evidence available to the scorecard gate."""

    model_config = ConfigDict(allow_inf_nan=False)

    cost_model_present: bool = True
    oos_present: bool = True
    benchmark_present: bool = True
    trial_count: int | None = 1
    execution_timestamps: ExecutionTimestampSet = Field(
        default_factory=ExecutionTimestampSet
    )
    pit_violation_codes: list[str] = Field(default_factory=list)
    historical_universe_present: bool = True
    ashare_market_rules_present: bool = True
    policy_denies_ignored: bool = False
    llm_override_attempt: bool = False
    high_crowding_without_stress: bool = False
    regime_negative_ic_without_activation: bool = False
    random_control_present: bool = True


class ScorecardInputs(BaseModel):
    """Inputs used to derive a scorecard from existing artifacts/metadata."""

    model_config = ConfigDict(allow_inf_nan=False)

    scorecard_id: str
    protocol_ref: str | None = None
    data_audit_refs: list[str] = Field(default_factory=list)
    backtest_refs: list[str] = Field(default_factory=list)
    alpha_bench_refs: list[str] = Field(default_factory=list)
    claims: ClaimSet = Field(default_factory=ClaimSet)
    evidence: EvidenceSet = Field(default_factory=EvidenceSet)
    score_breakdown: dict[str, float] | None = None
    warnings: list[QuantIssue] = Field(default_factory=list)
    hard_failures: list[QuantIssue] = Field(default_factory=list)
    experimental_metrics: dict[str, Any] = Field(default_factory=dict)


class EvalScorecard(BaseModel):
    """Slimmed deterministic scorecard consumed by the eval runner."""

    model_config = ConfigDict(allow_inf_nan=False)

    scorecard_id: str
    schema_version: str = SCORECARD_SCHEMA_VERSION
    score: float
    score_breakdown: dict[str, float]
    conclusion_cap: ConclusionCap
    warnings: list[QuantIssue] = Field(default_factory=list)
    hard_failures: list[QuantIssue] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _score_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("score must be finite")
        return float(value)

    @field_validator("score_breakdown")
    @classmethod
    def _breakdown_keys_are_known(cls, value: dict[str, float]) -> dict[str, float]:
        for key, item in value.items():
            if key not in SCORECARD_DIMENSION_KEYS:
                raise ValueError(f"unknown score_breakdown key: {key}")
            if not math.isfinite(float(item)):
                raise ValueError(f"score_breakdown[{key}] must be finite")
        return value


def build_scorecard(inputs: ScorecardInputs) -> EvalScorecard:
    """Derive a scorecard from evidence and claims."""
    breakdown = {key: 1.0 for key in SCORECARD_DIMENSION_KEYS}
    if inputs.score_breakdown is not None:
        breakdown.update(inputs.score_breakdown)

    warnings = list(inputs.warnings)
    hard_failures = list(inputs.hard_failures)
    evidence = inputs.evidence
    claims = inputs.claims
    cap: ConclusionCap = "paper_trade_candidate"

    if evidence.pit_violation_codes:
        breakdown["pit_clean"] = 0.0
        for code in evidence.pit_violation_codes:
            hard_failures.append(_hard_failure(code, "PIT violation detected"))

    if not evidence.cost_model_present:
        breakdown["cost_model"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(_warning("QUANT_COST_MODEL_MISSING", "cost model is missing"))
        if claims.claims_tradability():
            hard_failures.append(
                _hard_failure(
                    "QUANT_NO_COST_MODEL_TRADABLE_CLAIM",
                    "tradability claim requires a cost model",
                )
            )

    if not evidence.oos_present:
        breakdown["oos_split"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(
            _warning("QUANT_OOS_MISSING", "OOS or walk-forward evidence is missing")
        )
        if claims.generalization:
            hard_failures.append(
                _hard_failure(
                    "QUANT_NO_OOS_GENERALIZATION_CLAIM",
                    "generalization claim requires OOS or walk-forward evidence",
                )
            )

    if not evidence.benchmark_present:
        breakdown["benchmark"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(
            _warning("QUANT_BENCHMARK_MISSING", "benchmark evidence is missing")
        )
        if claims.alpha:
            hard_failures.append(
                _hard_failure(
                    "QUANT_NO_BENCHMARK_ALPHA_CLAIM",
                    "alpha claim requires benchmark evidence",
                )
            )

    if evidence.trial_count is None:
        breakdown["trial_count"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(_warning("QUANT_TRIAL_COUNT_MISSING", "trial_count is missing"))
        if claims.best_trial:
            hard_failures.append(
                _hard_failure(
                    "QUANT_TRIAL_COUNT_MISSING_BEST_TRIAL",
                    "best trial display requires trial_count",
                )
            )
    elif evidence.trial_count <= 0:
        breakdown["trial_count"] = 0.0
        warnings.append(
            _warning("QUANT_TRIAL_COUNT_NONPOSITIVE", "trial_count must be positive")
        )

    missing_timestamps = evidence.execution_timestamps.missing_fields()
    if missing_timestamps:
        breakdown["execution_realism"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(
            _warning(
                "QUANT_EXECUTION_TIMESTAMPS_MISSING",
                "execution timestamp evidence is incomplete",
                metadata={"missing": missing_timestamps},
            )
        )
        if claims.claims_tradability():
            hard_failures.append(
                _hard_failure(
                    "QUANT_EXECUTION_TIMESTAMPS_MISSING",
                    "tradability claim requires signal/decision/order/fill/price timestamps",
                    metadata={"missing": missing_timestamps},
                )
            )

    if not evidence.historical_universe_present:
        breakdown["universe_pit"] = 0.0
        hard_failures.append(
            _hard_failure(
                "QUANT_HISTORICAL_UNIVERSE_MISSING",
                "historical universe membership is missing",
            )
        )

    if not evidence.ashare_market_rules_present and claims.claims_tradability():
        hard_failures.append(
            _hard_failure(
                "QUANT_ASHARE_MARKET_RULES_MISSING",
                "A-share tradability claim requires market-rule coverage",
            )
        )

    if evidence.policy_denies_ignored:
        hard_failures.append(
            _hard_failure("POLICY_DENY_IGNORED", "policy deny was ignored")
        )

    if evidence.llm_override_attempt:
        hard_failures.append(
            _hard_failure(
                "QUANT_SCORECARD_LLM_OVERRIDE_ATTEMPT",
                "scorecard or conclusion gate override was attempted",
            )
        )

    if evidence.high_crowding_without_stress:
        breakdown["crowding_risk"] = 0.0
        hard_failures.append(
            _hard_failure(
                "QUANT_HIGH_CROWDING_NO_STRESS_TEST",
                "high crowding risk requires stress testing",
            )
        )

    if evidence.regime_negative_ic_without_activation:
        breakdown["regime_stability"] = 0.0
        hard_failures.append(
            _hard_failure(
                "QUANT_REGIME_NEGATIVE_IC_NO_ACTIVATION",
                "negative regime IC requires regime-conditional activation",
            )
        )

    if not evidence.random_control_present:
        breakdown["random_control"] = 0.0
        cap = _cap_at_research_candidate(cap)
        warnings.append(
            _warning(
                "QUANT_RANDOM_CONTROL_MISSING", "random control evidence is missing"
            )
        )

    if hard_failures:
        cap = "not_reliable"

    score = sum(breakdown.values()) / len(SCORECARD_DIMENSION_KEYS)
    return EvalScorecard(
        scorecard_id=inputs.scorecard_id,
        schema_version=SCORECARD_SCHEMA_VERSION,
        score=round(score, 6),
        score_breakdown=breakdown,
        conclusion_cap=cap,
        warnings=warnings,
        hard_failures=hard_failures,
    )


def _cap_at_research_candidate(current: ConclusionCap) -> ConclusionCap:
    if current in {"not_reliable", "exploratory", "research_candidate"}:
        return current
    return "research_candidate"


def _warning(
    code: str, message: str, *, metadata: dict[str, Any] | None = None
) -> QuantIssue:
    return QuantIssue(
        code=code,
        severity="warning",
        message=message,
        metadata=dict(metadata or {}),
    )


def _hard_failure(
    code: str, message: str, *, metadata: dict[str, Any] | None = None
) -> QuantIssue:
    return QuantIssue(
        code=code,
        severity="hard_failure",
        message=message,
        metadata=dict(metadata or {}),
    )
