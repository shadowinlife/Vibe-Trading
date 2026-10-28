"""Deterministic readiness gate for code strategies.

A code strategy becomes schedulable only when a real backtest run proves it
ran. This module judges ONE run directory and, on a pass, posts the run's
evidence to the two stores the rest of the system reads: the per-regime
evidence cache (keyed ``sdm:<artifact_id>``, the same key SDM uses) and the
artifact's bench history. No model is consulted and no metric is filled in by
hand — every number comes from the run's own ``artifacts/``.

Two invariants make the result safe to schedule from:

* Missing or unusable artifacts fail CLOSED with one stable reason code, in
  cheapest-first order, so an unhealthy run can never be downgraded into a
  pass.
* The verdict, the readiness it justifies and the validation status land in
  ONE compare-and-swap UPDATE bound to the ``artifact_version`` and
  ``spec_sha256`` snapshotted before the filesystem work. A concurrent edit
  therefore raises :class:`~src.strategy_library.store.StaleVerdictError` and
  leaves readiness untouched.

The run directory is read only. Nothing here writes into it, and the
strategy's own directory is never accepted as a run directory (it carries
``strategy.json``, never ``state.json``).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.strategy_discovery.evidence_harness import (
    check_run_hard_gates,
    rebuild_evidence,
)
from src.strategy_discovery.evidence_store import EvidenceStore
from src.strategy_discovery.run_artifacts import (
    equity_has_non_finite,
    parse_finite_float,
    read_metrics_trade_count,
    read_run_status,
)
from src.strategy_library.store import EditRequiredError, record_verdict
from src.strategy_store._shared import get_store
from src.strategy_store.models import Artifact, BenchResult, ValidationStatus
from src.strategy_store.sqlite_store import SqliteStrategyStore

__all__ = [
    "NEEDS_WORK",
    "READY",
    "REASON_HARD_GATE_FAILED",
    "REASON_NON_FINITE_EQUITY",
    "REASON_NO_METRICS",
    "REASON_NO_STATE",
    "REASON_STATE_NOT_SUCCESS",
    "REASON_ZERO_TRADES",
    "REASONS",
    "Verdict",
    "evaluate_code",
]

#: No run directory is bound to the strategy, or it holds no readable
#: ``state.json`` — the run cannot be identified as finished, so it is
#: treated as absent rather than guessed at.
REASON_NO_STATE = "no_state"

#: ``state.json`` reports a status other than ``"success"``.
REASON_STATE_NOT_SUCCESS = "state_not_success"

#: ``artifacts/metrics.csv`` is missing or empty.
REASON_NO_METRICS = "no_metrics"

#: ``artifacts/metrics.csv`` records no positive ``trade_count`` — either the
#: column is absent/unparseable or the value is zero/negative.
REASON_ZERO_TRADES = "zero_trades"

#: ``artifacts/equity.csv`` is missing, unreadable, carries no usable rows,
#: or holds a NaN/Inf/blank equity cell. One code: in every case the equity
#: curve is not a finite series a scheduler may trust.
REASON_NON_FINITE_EQUITY = "non_finite_equity"

#: The shared ingestion hard gates refused the run for a reason the checks
#: did not already name (including files they cannot read).
REASON_HARD_GATE_FAILED = "hard_gate_failed"

#: Closed set of failure reasons, in the order the gate evaluates them.
REASONS = (
    REASON_NO_STATE,
    REASON_STATE_NOT_SUCCESS,
    REASON_NO_METRICS,
    REASON_ZERO_TRADES,
    REASON_NON_FINITE_EQUITY,
    REASON_HARD_GATE_FAILED,
)

#: Scheduling readiness written on a pass and on any failure.
READY = "ready"
NEEDS_WORK = "needs_work"

#: ``metrics.csv`` columns copied into the bench row. A column the run did not
#: record stays ``None`` — a missing measurement is never estimated.
_BENCH_METRICS = ("sharpe", "annual_return", "max_drawdown", "calmar")


@dataclass(frozen=True)
class Verdict:
    """Outcome of one code-gate evaluation, already persisted.

    Attributes:
        passed: ``True`` when every artifact check succeeded.
        reasons: Failure reason codes, empty on a pass. At most one is
            produced because the gate stops at the first failing check.
        details: The stored verdict document without ``passed``/``reasons`` —
            artifact identity, the version it judged, the metrics actually
            read, and (on a pass) the evidence/bench bookkeeping.
    """

    passed: bool
    reasons: list[str]
    details: dict[str, Any]

    @property
    def readiness(self) -> str:
        """Scheduling readiness the gate wrote alongside this verdict."""
        return READY if self.passed else NEEDS_WORK

    def payload(self) -> dict[str, Any]:
        """The exact verdict document stored on the artifact row."""
        return {"passed": self.passed, "reasons": list(self.reasons), **self.details}


@dataclass(frozen=True)
class _ArtifactCheck:
    """Result of the cheap-first artifact checks."""

    reason: str | None
    detail: str | None
    trade_count: int | None


def _resolve_run_dir(run_dir: str | Path | None, artifact: Artifact) -> Path | None:
    """Explicit run directory wins; otherwise the one bound to the artifact."""
    if run_dir is not None and str(run_dir).strip():
        return Path(run_dir)
    if artifact.run_dir and artifact.run_dir.strip():
        return Path(artifact.run_dir)
    return None


def _check_artifacts(run_dir: Path | None) -> _ArtifactCheck:
    """Judge the run directory cheapest-first, stopping at the first failure.

    The readers are the same ones the evidence harness uses, so this gate and
    :func:`~src.strategy_discovery.evidence_harness.check_run_hard_gates`
    cannot disagree about what a healthy run looks like.
    """
    if run_dir is None:
        return _ArtifactCheck(
            REASON_NO_STATE,
            "no run_dir on the strategy or in the call",
            None,
        )

    status = read_run_status(run_dir)
    if status is None:
        return _ArtifactCheck(
            REASON_NO_STATE,
            f"state.json missing or unreadable under {run_dir}",
            None,
        )
    if status != "success":
        return _ArtifactCheck(
            REASON_STATE_NOT_SUCCESS,
            f"run status is {status!r}, not 'success'",
            None,
        )

    metrics_path = run_dir / "artifacts" / "metrics.csv"
    try:
        metrics_present = metrics_path.is_file() and metrics_path.stat().st_size > 0
    except OSError:
        metrics_present = False
    if not metrics_present:
        return _ArtifactCheck(
            REASON_NO_METRICS,
            "artifacts/metrics.csv missing or empty",
            None,
        )

    trade_count = read_metrics_trade_count(run_dir)
    if trade_count is None or trade_count <= 0:
        detail = (
            "trade_count missing or unparseable in artifacts/metrics.csv"
            if trade_count is None
            else f"trade_count is {trade_count}"
        )
        return _ArtifactCheck(REASON_ZERO_TRADES, detail, trade_count)

    non_finite = equity_has_non_finite(run_dir)
    if non_finite is None:
        return _ArtifactCheck(
            REASON_NON_FINITE_EQUITY,
            "artifacts/equity.csv missing, unreadable, or carries no usable rows",
            trade_count,
        )
    if non_finite:
        return _ArtifactCheck(
            REASON_NON_FINITE_EQUITY,
            "artifacts/equity.csv carries a NaN/Inf/blank equity cell",
            trade_count,
        )

    gate_ok, gate_reason = check_run_hard_gates(run_dir)
    if not gate_ok:
        return _ArtifactCheck(
            REASON_HARD_GATE_FAILED,
            gate_reason or "the ingestion hard gates refused the run",
            trade_count,
        )
    return _ArtifactCheck(None, None, trade_count)


def _read_bench_metrics(run_dir: Path) -> dict[str, float | None]:
    """Bench columns from ``artifacts/metrics.csv``, finite values only.

    The engine writes one header row of metric names plus one value row.
    Missing columns and unusable cells become ``None`` so the bench row
    records exactly what the run measured, never a substitute.
    """
    path = run_dir / "artifacts" / "metrics.csv"
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            values = next(reader, None)
    except (OSError, UnicodeDecodeError, csv.Error):
        return {}
    if not header or not values:
        return {}
    metrics: dict[str, float | None] = {}
    for index, name in enumerate(header):
        column = name.strip().lower()
        if column in _BENCH_METRICS and column not in metrics:
            cell = values[index] if index < len(values) else None
            metrics[column] = parse_finite_float(cell)
    return metrics


def _open_evidence_store() -> EvidenceStore:
    """Open the shared strategy-discovery evidence cache.

    A module-level factory keeps the cache a replaceable seam without
    widening the public signature.
    """
    return EvidenceStore()


def _details(
    *,
    strategy_id: str,
    artifact: Artifact,
    run_dir: Path | None,
    trade_count: int | None,
    detail: str | None,
) -> dict[str, Any]:
    """Artifact identity, judged version and metrics actually read."""
    return {
        "strategy_id": strategy_id,
        "kind": artifact.kind,
        "run_dir": str(run_dir) if run_dir is not None else None,
        "spec_sha256": artifact.spec_sha256,
        "artifact_version": artifact.artifact_version,
        "trade_count": trade_count,
        "detail": detail,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_code(
    strategy_id: str,
    *,
    run_dir: str | Path | None = None,
    store: SqliteStrategyStore | None = None,
) -> Verdict:
    """Gate one code strategy on a real backtest run and persist the verdict.

    Checks run cheapest-first: ``state.json`` success, ``metrics.csv``
    present, ``trade_count > 0``, ``equity.csv`` finite, then the shared
    hard gates. The first failure becomes the verdict's single reason and the
    row is written ``needs_work`` / ``REJECTED``.

    On a pass the bench row is recorded first (``bench_type="initial"``,
    metrics copied from ``metrics.csv``), then the run's per-regime evidence
    is rebuilt under ``sdm:<strategy_id>``, then the verdict is written
    ``ready`` / ``VALIDATED``. ``APPROVED`` belongs to SDM model governance
    and is never set here.

    Args:
        strategy_id: Artifact id of the code strategy.
        run_dir: Run directory to judge; defaults to the artifact's own
            ``run_dir``. A missing directory is a ``no_state`` failure, never
            a fallback to the strategy directory.
        store: Strategy store to read and write; defaults to the process
            singleton.

    Returns:
        The persisted verdict.

    Raises:
        LookupError: When the strategy does not exist.
        ValueError: When the strategy is not ``kind="code"`` — workflow
            readiness comes from the evaluator, not from backtest artifacts.
        EditRequiredError: When the row predates version pinning, so no
            compare-and-swap pair exists.
        StaleVerdictError: When the strategy changed during the evaluation;
            nothing is written and readiness is untouched.
    """
    artifact_store = get_store() if store is None else store
    artifact = artifact_store.get_artifact(strategy_id)
    if artifact is None:
        raise LookupError(f"strategy {strategy_id!r} does not exist")
    if artifact.kind != "code":
        raise ValueError(
            f"evaluate_code gates code strategies only; {strategy_id!r} is "
            f"kind {artifact.kind!r}"
        )

    # Snapshot the compare-and-swap pair BEFORE any filesystem or store work,
    # so a concurrent edit cannot attach this verdict to different bytes.
    expected_version = artifact.artifact_version
    expected_spec_sha256 = artifact.spec_sha256
    if not expected_version or not expected_spec_sha256:
        raise EditRequiredError(
            f"strategy {strategy_id!r} has no pinned version/spec hash; "
            "re-promote it before it can be evaluated"
        )

    resolved_run_dir = _resolve_run_dir(run_dir, artifact)
    check = _check_artifacts(resolved_run_dir)
    details = _details(
        strategy_id=strategy_id,
        artifact=artifact,
        run_dir=resolved_run_dir,
        trade_count=check.trade_count,
        detail=check.detail,
    )

    if check.reason is not None:
        reasons = [check.reason]
        record_verdict(
            strategy_id,
            {"passed": False, "reasons": reasons, **details},
            expected_version=expected_version,
            expected_spec_sha256=expected_spec_sha256,
            readiness=NEEDS_WORK,
            validation_status=ValidationStatus.REJECTED,
            store=artifact_store,
        )
        return Verdict(passed=False, reasons=reasons, details=details)

    assert resolved_run_dir is not None  # a pass requires a readable run dir
    evidence_key = f"sdm:{strategy_id}"
    metrics = _read_bench_metrics(resolved_run_dir)
    bench_id = artifact_store.record_bench_once(
        BenchResult(
            artifact_id=strategy_id,
            bench_type="initial",
            sharpe=metrics.get("sharpe"),
            annual_return=metrics.get("annual_return"),
            max_drawdown=metrics.get("max_drawdown"),
            calmar=metrics.get("calmar"),
            run_dir=str(resolved_run_dir),
        )
    )

    evidence_report = rebuild_evidence(
        [{"strategy_id": evidence_key, "run_dir": str(resolved_run_dir)}],
        _open_evidence_store(),
        scope="strategies",
    )
    passed_details = {
        **details,
        "evidence_key": evidence_key,
        "evidence_rows": int(evidence_report.get("rows") or 0),
        "evidence_skipped": list(evidence_report.get("skipped") or []),
        "bench_id": bench_id,
        "bench_recorded": bench_id is not None,
    }
    record_verdict(
        strategy_id,
        {"passed": True, "reasons": [], **passed_details},
        expected_version=expected_version,
        expected_spec_sha256=expected_spec_sha256,
        readiness=READY,
        validation_status=ValidationStatus.VALIDATED,
        store=artifact_store,
    )
    return Verdict(passed=True, reasons=[], details=passed_details)
