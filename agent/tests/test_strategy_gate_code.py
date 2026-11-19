"""Code-strategy gate: artifacts -> readiness, evidence and CAS verdict.

Every fixture is a real-schema run directory (``state.json``, engine
``metrics.csv`` header+value row, ``equity.csv`` with a ``timestamp`` index,
entry/exit ``trades.csv``) written under ``tmp_path``. No test touches the
strategy directory as a run directory, and every store is injected.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.strategy_discovery.evidence_store import EvidenceStore
from src.strategy_library import gate_code, paths
from src.strategy_library.gate_code import (
    REASON_HARD_GATE_FAILED,
    REASON_NON_FINITE_EQUITY,
    REASON_NO_METRICS,
    REASON_NO_STATE,
    REASON_STATE_NOT_SUCCESS,
    REASON_ZERO_TRADES,
    evaluate_code,
)
from src.strategy_library.store import (
    EditRequiredError,
    StaleVerdictError,
    create_draft,
    get,
    update_draft,
)
from src.strategy_store.models import Artifact, ArtifactType, ValidationStatus
from src.strategy_store.sqlite_store import SqliteStrategyStore

#: Bars in the fixture equity curve. Fewer than the harness's 252-bar regime
#: window, so every bar is labeled ``structural`` and every trade lands there.
_BARS = 40

#: Bar indices carrying a same-day round trip (entry + exit row pair).
_TRADE_BAR_INDICES = (4, 8, 12)

_BENCH_COLUMNS = (
    "final_value",
    "total_return",
    "annual_return",
    "max_drawdown",
    "sharpe",
    "calmar",
    "trade_count",
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteStrategyStore:
    """Strategy store whose row is the one ``get_store()`` resolves to.

    ``rebuild_evidence`` calls ``validate_adapted_run`` without a store, so
    the singleton must point at the same database or the gate would read an
    unrelated empty one.
    """
    import src.strategy_store._shared as shared

    bound = SqliteStrategyStore(tmp_path / "strategy_store.db")
    monkeypatch.setattr(shared, "_store", bound)
    return bound


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvidenceStore:
    """Evidence cache under ``tmp_path`` instead of the runtime root."""
    cache = EvidenceStore(tmp_path / "strategy_discovery.db")
    monkeypatch.setattr(gate_code, "_open_evidence_store", lambda: cache)
    return cache


def _code_spec(name: str = "Momentum Code", thesis: str = "Cross-sectional momentum.") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": thesis,
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _bar_date(index: int) -> date:
    return date(2026, 1, 1) + timedelta(days=index)


def _write_metrics(artifacts: Path, *, trade_count: int) -> None:
    values = ["1000000.0", "0.012", "0.11", "-0.05", "1.4", "2.2", str(trade_count)]
    (artifacts / "metrics.csv").write_text(
        ",".join(_BENCH_COLUMNS) + "\n" + ",".join(values) + "\n",
        encoding="utf-8",
    )


def _write_equity(
    artifacts: Path, *, bad_cell: str | None = None, bad_index: int = 7
) -> None:
    lines = ["timestamp,equity,benchmark_equity"]
    for index in range(_BARS):
        equity = f"{1_000_000.0 * (1.0 + 0.001 * index):.6f}"
        if bad_cell is not None and index == bad_index:
            equity = bad_cell
        lines.append(f"{_bar_date(index).isoformat()},{equity},100.000000")
    (artifacts / "equity.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trades(artifacts: Path, *, trade_count: int) -> None:
    lines = ["timestamp,code,side,price,qty,reason,pnl,holding_days,return_pct,event"]
    for index in _TRADE_BAR_INDICES[: max(0, min(trade_count, len(_TRADE_BAR_INDICES)))]:
        day = _bar_date(index).isoformat()
        lines.append(f"{day},TEST.SH,buy,10.0,100.0,signal,0.0,0,0.0,entry")
        lines.append(f"{day},TEST.SH,sell,10.5,100.0,signal,50.0,0,0.5,exit")
    (artifacts / "trades.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_run(
    tmp_path: Path,
    *,
    status: str = "success",
    trade_count: int = 3,
    bad_equity_cell: str | None = None,
    write_metrics: bool = True,
    write_equity: bool = True,
    write_state: bool = True,
) -> Path:
    """One run directory in the real engine artifact layout."""
    run_dir = tmp_path / "run"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    if write_state:
        (run_dir / "state.json").write_text(
            json.dumps({"status": status}), encoding="utf-8"
        )
    if write_metrics:
        _write_metrics(artifacts, trade_count=trade_count)
    if write_equity:
        _write_equity(artifacts, bad_cell=bad_equity_cell)
    _write_trades(artifacts, trade_count=trade_count)
    return run_dir


def _draft(
    tmp_path: Path, store: SqliteStrategyStore, run_dir: Path | None
) -> Artifact:
    return create_draft(
        _code_spec(),
        run_dir=str(run_dir) if run_dir is not None else None,
        store=store,
        runtime_root=tmp_path,
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_rejected(
    artifact: Artifact,
    *,
    store: SqliteStrategyStore,
    evidence: EvidenceStore,
    reason: str,
) -> None:
    """The row carries exactly one reason and nothing was posted as evidence."""
    row = get(artifact.id, store=store)
    assert row is not None
    assert row.readiness == "needs_work"
    assert row.validation_status is ValidationStatus.REJECTED
    assert row.evaluator_verdict is not None
    assert row.evaluator_verdict["passed"] is False
    assert row.evaluator_verdict["reasons"] == [reason]
    assert row.evaluator_verdict["kind"] == "code"
    assert store.get_bench_history(artifact.id) == []
    assert evidence.get_rows(strategy_id=f"sdm:{artifact.id}") == []


# ---------------------------------------------------------------------------
# The six failure reasons, one fixture each
# ---------------------------------------------------------------------------


def test_missing_run_dir_is_no_state(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, run_dir=None)

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.passed is False
    assert verdict.reasons == [REASON_NO_STATE]
    assert verdict.details["run_dir"] is None
    _assert_rejected(artifact, store=store, evidence=evidence, reason=REASON_NO_STATE)


def test_missing_state_json_is_no_state(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path, write_state=False))

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_NO_STATE]
    _assert_rejected(artifact, store=store, evidence=evidence, reason=REASON_NO_STATE)


def test_failed_run_is_state_not_success(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path, status="failed"))

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_STATE_NOT_SUCCESS]
    assert "failed" in str(verdict.details["detail"])
    _assert_rejected(
        artifact, store=store, evidence=evidence, reason=REASON_STATE_NOT_SUCCESS
    )


def test_missing_metrics_is_no_metrics(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path, write_metrics=False))

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_NO_METRICS]
    assert verdict.details["trade_count"] is None
    _assert_rejected(artifact, store=store, evidence=evidence, reason=REASON_NO_METRICS)


def test_zero_trades_is_zero_trades(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path, trade_count=0))

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_ZERO_TRADES]
    assert verdict.details["trade_count"] == 0
    _assert_rejected(artifact, store=store, evidence=evidence, reason=REASON_ZERO_TRADES)


@pytest.mark.parametrize("cell", ["nan", "NaN", "inf", "-inf", "Infinity", ""])
def test_every_non_finite_equity_cell_is_rejected(
    tmp_path: Path,
    store: SqliteStrategyStore,
    evidence: EvidenceStore,
    cell: str,
) -> None:
    artifact = _draft(
        tmp_path, store, _write_run(tmp_path, bad_equity_cell=cell)
    )

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_NON_FINITE_EQUITY]
    assert verdict.details["trade_count"] == 3
    _assert_rejected(
        artifact, store=store, evidence=evidence, reason=REASON_NON_FINITE_EQUITY
    )


def test_missing_equity_is_non_finite_equity(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path, write_equity=False))

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_NON_FINITE_EQUITY]
    _assert_rejected(
        artifact, store=store, evidence=evidence, reason=REASON_NON_FINITE_EQUITY
    )


def test_a_refusing_hard_gate_is_hard_gate_failed(
    tmp_path: Path,
    store: SqliteStrategyStore,
    evidence: EvidenceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path))
    monkeypatch.setattr(
        gate_code,
        "check_run_hard_gates",
        lambda _run_dir: (False, "hard-gate:equity-empty: stubbed refusal"),
    )

    verdict = evaluate_code(artifact.id, store=store)

    assert verdict.reasons == [REASON_HARD_GATE_FAILED]
    assert verdict.details["detail"] == "hard-gate:equity-empty: stubbed refusal"
    _assert_rejected(
        artifact, store=store, evidence=evidence, reason=REASON_HARD_GATE_FAILED
    )


# ---------------------------------------------------------------------------
# The passing path
# ---------------------------------------------------------------------------


def test_passing_run_becomes_ready_with_bench_and_evidence(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path)
    artifact = _draft(tmp_path, store, run_dir)
    before = _tree(run_dir)

    verdict = evaluate_code(artifact.id, store=store)

    assert _tree(run_dir) == before
    assert verdict.passed is True
    assert verdict.reasons == []
    assert verdict.readiness == "ready"
    assert verdict.details["kind"] == "code"
    assert verdict.details["run_dir"] == str(run_dir)
    assert verdict.details["trade_count"] == 3
    assert verdict.details["artifact_version"] == artifact.artifact_version
    assert verdict.details["spec_sha256"] == artifact.spec_sha256
    assert verdict.details["detail"] is None
    assert verdict.details["evaluated_at"]
    assert verdict.details["evidence_key"] == f"sdm:{artifact.id}"
    assert verdict.details["evidence_rows"] >= 1
    assert verdict.details["evidence_skipped"] == []
    assert verdict.details["bench_recorded"] is True
    assert verdict.details["bench_id"] is not None

    row = get(artifact.id, store=store)
    assert row is not None
    assert row.readiness == "ready"
    assert row.validation_status is ValidationStatus.VALIDATED
    assert row.evaluator_verdict == verdict.payload()
    assert row.evaluator_verdict["passed"] is True
    assert row.evaluator_verdict["reasons"] == []

    benches = store.get_bench_history(artifact.id)
    assert len(benches) == 1
    assert benches[0].bench_type == "initial"
    assert benches[0].run_dir == str(run_dir)
    assert (
        benches[0].sharpe,
        benches[0].annual_return,
        benches[0].max_drawdown,
        benches[0].calmar,
    ) == (1.4, 0.11, -0.05, 2.2)

    key = f"sdm:{artifact.id}"
    rows = evidence.get_rows(strategy_id=key)
    assert rows
    assert {row.strategy_id for row in rows} == {key}
    assert all(row.provenance == str(run_dir) for row in rows)

    from src.strategy_discovery.facade import StrategyDiscoveryFacade

    facade = StrategyDiscoveryFacade(evidence_store=evidence, sdm_store=store)
    envelope = facade.get_strategy_evidence(key)
    assert envelope["status"] == "ok"
    assert envelope["strategy_id"] == key
    assert envelope["found"] is True
    assert envelope["rows"]


def test_the_run_dir_defaults_to_the_artifact_run_dir(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path)
    artifact = _draft(tmp_path, store, run_dir)

    assert evaluate_code(artifact.id, store=store).details["run_dir"] == str(run_dir)


def test_re_evaluating_the_same_run_does_not_duplicate_the_bench_row(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path))

    first = evaluate_code(artifact.id, store=store)
    second = evaluate_code(artifact.id, store=store)

    assert first.details["bench_recorded"] is True
    assert second.passed is True
    assert second.details["bench_recorded"] is False
    assert second.details["bench_id"] is None
    assert len(store.get_bench_history(artifact.id)) == 1
    assert get(artifact.id, store=store).readiness == "ready"


# ---------------------------------------------------------------------------
# Compare-and-swap: an edit during the evaluation writes nothing
# ---------------------------------------------------------------------------


def test_an_edit_during_the_evaluation_raises_stale_and_keeps_readiness(
    tmp_path: Path,
    store: SqliteStrategyStore,
    evidence: EvidenceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _draft(tmp_path, store, _write_run(tmp_path))
    real_rebuild = gate_code.rebuild_evidence

    def edit_then_rebuild(runs, evidence_store, *, scope="all"):
        """Stand in for the slow work: the user saves a new draft halfway."""
        update_draft(
            artifact.id,
            _code_spec(thesis="Edited while the gate was running."),
            store=store,
            runtime_root=tmp_path,
        )
        return real_rebuild(runs, evidence_store, scope=scope)

    monkeypatch.setattr(gate_code, "rebuild_evidence", edit_then_rebuild)

    with pytest.raises(StaleVerdictError):
        evaluate_code(artifact.id, store=store)

    row = get(artifact.id, store=store)
    assert row is not None
    assert row.readiness == "needs_work"
    assert row.evaluator_verdict is None
    assert row.artifact_version != artifact.artifact_version


# ---------------------------------------------------------------------------
# Boundaries the gate refuses to cross
# ---------------------------------------------------------------------------


def test_the_strategy_directory_is_not_a_run_dir(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    artifact = _draft(tmp_path, store, run_dir=None)
    strategy_dir = paths.strategy_dir(artifact.id, runtime_root=tmp_path)
    assert (strategy_dir / "strategy.json").is_file()

    verdict = evaluate_code(artifact.id, run_dir=strategy_dir, store=store)

    assert verdict.reasons == [REASON_NO_STATE]
    _assert_rejected(artifact, store=store, evidence=evidence, reason=REASON_NO_STATE)


def test_unknown_strategy_raises_lookup_error(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    with pytest.raises(LookupError):
        evaluate_code("art_does_not_exist", store=store)


def test_a_workflow_strategy_is_not_gated_by_a_backtest(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    artifact = create_draft(
        {"name": "Flow", "kind": "workflow", "universe": "cn_a"},
        run_dir=str(_write_run(tmp_path)),
        store=store,
        runtime_root=tmp_path,
    )

    with pytest.raises(ValueError, match="code strategies only"):
        evaluate_code(artifact.id, store=store)

    assert get(artifact.id, store=store).readiness == "draft"


def test_a_legacy_row_without_a_version_is_refused(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    legacy_id = store.register_artifact(
        Artifact(
            id="",
            type=ArtifactType.STRATEGY,
            name="Legacy Strategy",
            universe="cn_a",
            kind="code",
        )
    )

    with pytest.raises(EditRequiredError):
        evaluate_code(legacy_id, run_dir=_write_run(tmp_path), store=store)
