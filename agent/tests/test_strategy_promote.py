"""Promotion: session draft -> library row, source-run copy and auto-evaluation.

Every fixture is a real-schema run directory written under ``tmp_path`` (which
is also the runtime root), so the path guard, the copy and the gate all see the
same layout production uses. No test touches a real runtime root, and every
store is injected.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.strategy_discovery.evidence_store import EvidenceStore
from src.strategy_library import gate_code, promote as promote_module
from src.strategy_library.gate_code import REASON_HARD_GATE_FAILED, REASON_ZERO_TRADES
from src.strategy_library.promote import (
    COPY_FAILED,
    DUPLICATE_NAME_UNIVERSE,
    MISSING_CONFIG,
    MISSING_SIGNAL_ENGINE,
    RUN_DIR_NOT_FOUND,
    RUN_DIR_REJECTED,
    RUN_DIR_REQUIRED,
    PromotionResult,
    promote,
)
from src.strategy_library.store import snapshot
from src.strategy_store.sqlite_store import SqliteStrategyStore

#: Bars in the fixture equity curve; fewer than the harness's 252-bar regime
#: window, so every bar is labeled ``structural`` and the three round trips land.
_BARS = 40

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

_SIGNAL_ENGINE = (
    '"""Momentum engine used by the promotion fixtures."""\n'
    "\n"
    "def generate_signals(bars):\n"
    "    return [1 if bar['close'] > bar['open'] else 0 for bar in bars]\n"
)

_CONFIG = {
    "source": "local",
    "codes": ["TEST.SH"],
    "start_date": "2026-01-01",
    "end_date": "2026-02-28",
}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteStrategyStore:
    """Strategy store bound to the process singleton the evidence rebuild reads."""
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


def _code_spec(name: str = "Momentum Code", universe: str = "cn_a") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Cross-sectional momentum.",
        "universe": universe,
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _workflow_spec(name: str = "Breakout Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength confirmed by volume.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "get_market_data", "required": True}],
        "preconditions": ["the exchange is open"],
        "steps": [
            {"id": "s1", "action": "load prices", "tool": "get_market_data", "output": "prices"},
            {"id": "s2", "action": "score", "tool": "technical_indicators", "output": "score"},
            {"id": "s3", "action": "summarise", "tool": "web_search", "output": "table"},
        ],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "score"],
            "destination": "chat",
        },
        "failure_handling": "Report the failing step and stop.",
    }


def _bar_date(index: int) -> date:
    return date(2026, 1, 1) + timedelta(days=index)


def _write_metrics(artifacts: Path, *, trade_count: int = 3) -> None:
    values = ["1000000.0", "0.012", "0.11", "-0.05", "1.4", "2.2", str(trade_count)]
    (artifacts / "metrics.csv").write_text(
        ",".join(_BENCH_COLUMNS) + "\n" + ",".join(values) + "\n", encoding="utf-8"
    )


def _write_equity(artifacts: Path) -> None:
    lines = ["timestamp,equity,benchmark_equity"]
    for index in range(_BARS):
        equity = f"{1_000_000.0 * (1.0 + 0.001 * index):.6f}"
        lines.append(f"{_bar_date(index).isoformat()},{equity},100.000000")
    (artifacts / "equity.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trades(artifacts: Path) -> None:
    lines = ["timestamp,code,side,price,qty,reason,pnl,holding_days,return_pct,event"]
    for index in _TRADE_BAR_INDICES:
        day = _bar_date(index).isoformat()
        lines.append(f"{day},TEST.SH,buy,10.0,100.0,signal,0.0,0,0.0,entry")
        lines.append(f"{day},TEST.SH,sell,10.5,100.0,signal,50.0,0,0.5,exit")
    (artifacts / "trades.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_run(
    tmp_path: Path,
    *,
    name: str = "run",
    write_state: bool = True,
    write_metrics: bool = True,
    write_config: bool = True,
    write_engine: bool = True,
    empty_engine: bool = False,
    trade_count: int = 3,
) -> Path:
    """One promotable run directory in the real engine artifact layout."""
    run_dir = tmp_path / "runs" / name
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    if write_state:
        (run_dir / "state.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    if write_metrics:
        _write_metrics(artifacts, trade_count=trade_count)
    _write_equity(artifacts)
    _write_trades(artifacts)
    if write_config:
        (run_dir / "config.json").write_text(json.dumps(_CONFIG), encoding="utf-8")
    engine = run_dir / "code" / "signal_engine.py"
    if write_engine:
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_text("" if empty_engine else _SIGNAL_ENGINE, encoding="utf-8")
    return run_dir


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _strategy_files(strategy_id: str, tmp_path: Path) -> set[str]:
    base = tmp_path / "strategies" / strategy_id
    return {
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Happy path: register, copy the code, evaluate the SOURCE run, become ready
# ---------------------------------------------------------------------------


def test_code_promotion_lands_the_code_binds_the_source_and_becomes_ready(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path)
    before = _tree(run_dir)

    result = promote(
        _code_spec(),
        session_id="ses-42",
        run_dir=str(run_dir),
        store=store,
        runtime_root=tmp_path,
    )

    assert isinstance(result, PromotionResult)
    assert result.strategy_id is not None
    assert result.gaps == []
    assert result.readiness == "ready"
    assert result.session_id == "ses-42"
    assert result.verdict is not None
    assert result.verdict["passed"] is True
    assert result.verdict["reasons"] == []
    assert result.verdict["session_id"] == "ses-42"

    artifact = result.artifact
    assert artifact is not None
    assert artifact.kind == "code"
    assert artifact.readiness == "ready"
    assert artifact.run_dir == str(run_dir.resolve())
    assert artifact.spec_sha256
    assert artifact.artifact_version

    assert _strategy_files(artifact.id, tmp_path) == {
        "strategy.json",
        "config.json",
        "code/signal_engine.py",
    }
    base = tmp_path / "strategies" / artifact.id
    assert (base / "config.json").read_bytes() == (run_dir / "config.json").read_bytes()
    assert (base / "code" / "signal_engine.py").read_bytes() == (
        run_dir / "code" / "signal_engine.py"
    ).read_bytes()

    benches = store.get_bench_history(artifact.id)
    assert len(benches) == 1
    assert benches[0].bench_type == "initial"
    assert benches[0].run_dir == str(run_dir.resolve())

    # The gate reads the source run and never writes into it.
    assert _tree(run_dir) == before

    # The copied sources are exactly what the version snapshot later freezes.
    version = snapshot(artifact.id, store=store, runtime_root=tmp_path)
    version_dir = base / "versions" / version
    assert (version_dir / "code" / "signal_engine.py").is_file()
    assert (version_dir / "config.json").is_file()


def test_the_auto_evaluation_defaults_to_the_persisted_source_run(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.verdict is not None
    assert result.verdict["run_dir"] == str(run_dir.resolve())
    assert result.artifact is not None
    assert result.artifact.run_dir == result.verdict["run_dir"]


def test_a_fake_hard_gate_receives_the_source_path_not_the_library_copy(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _write_run(tmp_path)
    seen: list[str] = []

    def fake_gate(path: str | Path) -> tuple[bool, str]:
        seen.append(str(path))
        return False, "hard-gate:zero-trades: stubbed refusal"

    monkeypatch.setattr(gate_code, "check_run_hard_gates", fake_gate)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert seen == [str(run_dir.resolve())]
    assert result.strategy_id is not None
    assert str(tmp_path / "strategies" / result.strategy_id) not in seen
    assert result.readiness == "needs_work"
    assert [gap.code for gap in result.gaps] == [REASON_HARD_GATE_FAILED]
    assert result.verdict is not None
    assert result.verdict["passed"] is False


def test_a_zero_trade_run_lands_needs_work_and_is_never_upgraded(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path, trade_count=0)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.strategy_id is not None
    assert result.readiness == "needs_work"
    assert [gap.code for gap in result.gaps] == [REASON_ZERO_TRADES]
    assert result.artifact is not None
    assert result.artifact.readiness == "needs_work"
    assert result.artifact.evaluator_verdict is not None
    assert result.artifact.evaluator_verdict["passed"] is False
    assert store.get_bench_history(result.strategy_id) == []


# ---------------------------------------------------------------------------
# Precheck gaps: nothing is registered, nothing is created
# ---------------------------------------------------------------------------


def test_a_directory_holding_only_a_spec_is_no_state(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    run_dir = tmp_path / "runs" / "spec-only"
    run_dir.mkdir(parents=True)
    (run_dir / "strategy.json").write_text(json.dumps(_code_spec()), encoding="utf-8")

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.artifact is None
    assert result.readiness == "needs_work"
    assert [gap.code for gap in result.gaps] == ["no_state"]
    assert not (tmp_path / "strategies").exists()
    assert store.list_artifacts() == []


@pytest.mark.parametrize("present,empty", [(False, False), (True, True)])
def test_a_missing_or_empty_signal_engine_is_a_gap_and_creates_nothing(
    tmp_path: Path, store: SqliteStrategyStore, present: bool, empty: bool
) -> None:
    run_dir = _write_run(tmp_path, write_engine=present, empty_engine=empty)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.gaps[0].code == MISSING_SIGNAL_ENGINE
    assert not (tmp_path / "strategies").exists()
    assert store.list_artifacts() == []


def test_a_missing_config_is_a_gap_and_creates_nothing(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    run_dir = _write_run(tmp_path, write_config=False)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.gaps[0].code == MISSING_CONFIG
    assert not (tmp_path / "strategies").exists()


def test_a_gapped_spec_registers_nothing(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    spec = _workflow_spec()
    spec["steps"] = spec["steps"][:1]

    result = promote(spec, session_id="ses-1", run_dir=str(_write_run(tmp_path)), store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.readiness == "needs_work"
    assert [gap.code for gap in result.gaps] == ["too_few_steps"]
    assert not (tmp_path / "strategies").exists()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_a_traversing_path_is_rejected(tmp_path: Path, store: SqliteStrategyStore) -> None:
    result = promote(
        _code_spec(), session_id="ses-1", run_dir="../../../etc", store=store, runtime_root=tmp_path
    )

    assert result.strategy_id is None
    assert result.gaps[0].code == RUN_DIR_REJECTED
    assert not (tmp_path / "strategies").exists()


def test_a_path_outside_the_runtime_root_is_rejected(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime_root = tmp_path / "root"
    runtime_root.mkdir()

    result = promote(
        _code_spec(), session_id="ses-1", run_dir=str(outside), store=store, runtime_root=runtime_root
    )

    assert result.gaps[0].code == RUN_DIR_REJECTED
    assert not (runtime_root / "strategies").exists()


def test_a_symlinked_run_dir_is_rejected(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    outside = _write_run(tmp_path, name="outside")
    runtime_root = tmp_path / "root"
    (runtime_root / "runs").mkdir(parents=True)
    (runtime_root / "runs" / "link").symlink_to(outside, target_is_directory=True)

    result = promote(
        _code_spec(),
        session_id="ses-1",
        run_dir=str(runtime_root / "runs" / "link"),
        store=store,
        runtime_root=runtime_root,
    )

    assert result.gaps[0].code == RUN_DIR_REJECTED
    assert not (runtime_root / "strategies").exists()


def test_the_strategy_library_is_never_a_run_directory(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    first = promote(
        _code_spec(name="First"),
        session_id="ses-1",
        run_dir=str(_write_run(tmp_path, name="first")),
        store=store,
        runtime_root=tmp_path,
    )
    assert first.artifact is not None
    library = tmp_path / "strategies" / first.artifact.id

    result = promote(_code_spec(name="Second"), session_id="ses-1", run_dir=str(library), store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.gaps[0].code == RUN_DIR_REJECTED
    assert len(list((tmp_path / "strategies").iterdir())) == 1


def test_a_code_strategy_without_a_run_dir_is_refused(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    result = promote(_code_spec(), session_id="ses-1", store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.gaps[0].code == RUN_DIR_REQUIRED


def test_a_nonexistent_but_contained_path_is_not_found(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    result = promote(
        _code_spec(), session_id="ses-1", run_dir=str(tmp_path / "runs" / "absent"), store=store, runtime_root=tmp_path
    )

    assert result.gaps[0].code == RUN_DIR_NOT_FOUND


# ---------------------------------------------------------------------------
# Duplicates: report, never overwrite
# ---------------------------------------------------------------------------


def test_a_duplicate_name_universe_creates_no_second_row_or_directory(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    run_dir = _write_run(tmp_path)
    first = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)
    assert first.strategy_id is not None
    before = _tree(tmp_path / "strategies" / first.strategy_id)

    second = promote(_code_spec(), session_id="ses-2", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert second.strategy_id is None
    assert second.artifact is None
    assert second.readiness == "needs_work"
    assert [gap.code for gap in second.gaps] == [DUPLICATE_NAME_UNIVERSE]
    assert second.gaps[0].field == "name"
    assert len(store.list_artifacts()) == 1
    assert [path.name for path in (tmp_path / "strategies").iterdir()] == [first.strategy_id]
    assert _tree(tmp_path / "strategies" / first.strategy_id) == before


def test_the_same_name_in_another_universe_is_a_new_strategy(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore
) -> None:
    first = promote(
        _code_spec(universe="cn_a"),
        session_id="ses-1",
        run_dir=str(_write_run(tmp_path, name="cn")),
        store=store,
        runtime_root=tmp_path,
    )
    second = promote(
        _code_spec(universe="us"),
        session_id="ses-1",
        run_dir=str(_write_run(tmp_path, name="us")),
        store=store,
        runtime_root=tmp_path,
    )

    assert first.strategy_id != second.strategy_id
    assert len(store.list_artifacts()) == 2


# ---------------------------------------------------------------------------
# workflow: spec only, no gate, not ready
# ---------------------------------------------------------------------------


def test_workflow_promotion_writes_the_spec_and_waits_for_review(
    tmp_path: Path, store: SqliteStrategyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_gate(*args, **kwargs):
        raise AssertionError("a workflow promotion must not run the code gate")

    monkeypatch.setattr(promote_module, "evaluate_code", no_gate)

    result = promote(_workflow_spec(), session_id="ses-7", store=store, runtime_root=tmp_path)

    assert result.strategy_id is not None
    assert result.readiness == "needs_work"
    assert result.gaps == []
    assert result.verdict is None
    assert result.session_id == "ses-7"
    assert result.artifact is not None
    assert result.artifact.kind == "workflow"
    assert result.artifact.readiness == "needs_work"
    assert result.artifact.run_dir is None
    assert _strategy_files(result.strategy_id, tmp_path) == {"strategy.json"}


def test_a_code_spec_with_a_non_string_universe_is_a_gap_not_an_exception(
    tmp_path: Path, store: SqliteStrategyStore
) -> None:
    spec = _code_spec()
    spec["universe"] = 42

    result = promote(spec, session_id="ses-1", run_dir=str(_write_run(tmp_path)), store=store, runtime_root=tmp_path)

    assert result.strategy_id is None
    assert result.gaps[0].code == "invalid_spec"
    assert not (tmp_path / "strategies").exists()


# ---------------------------------------------------------------------------
# Copy failures keep the row out of scheduling instead of mislabelling it
# ---------------------------------------------------------------------------


def test_a_copy_failure_returns_a_gap_and_leaves_only_the_spec(
    tmp_path: Path, store: SqliteStrategyStore, evidence: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _write_run(tmp_path)
    calls: list[Path] = []
    real_write = promote_module.atomic_write_text

    def failing_write(path: Path, text: str, *, encoding: str = "utf-8") -> None:
        if str(path).endswith("signal_engine.py"):
            raise OSError("disk full")
        calls.append(path)
        real_write(path, text, encoding=encoding)

    monkeypatch.setattr(promote_module, "atomic_write_text", failing_write)

    result = promote(_code_spec(), session_id="ses-1", run_dir=str(run_dir), store=store, runtime_root=tmp_path)

    assert result.strategy_id is not None
    assert result.readiness == "needs_work"
    assert [gap.code for gap in result.gaps] == [COPY_FAILED]
    assert result.verdict is None
    assert _strategy_files(result.strategy_id, tmp_path) == {"strategy.json"}
    assert result.artifact is not None
    assert result.artifact.readiness == "draft"
