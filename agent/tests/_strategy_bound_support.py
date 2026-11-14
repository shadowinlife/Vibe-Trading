"""Fixtures for the bound-owner promotion and scheduling integration tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.strategy_discovery.evidence_store import EvidenceStore
from src.strategy_library import gate_code, paths
from src.strategy_library.promote import PromotionResult, promote
from src.strategy_library.store import create_draft, record_verdict
from src.strategy_store.models import Artifact
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.models import RegistrationKey

TOKEN = "synthetic-service-token-0003-a41d"
OTHER_TOKEN = "synthetic-other-token-0003-7b2c"
OWNER = "synthetic-owner-0003"
OTHER_OWNER = "synthetic-other-owner-0003"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
BINDING_ENV = "RM_VT_STRATEGY_OWNER_BINDING_FILE"
SCHEDULE = "0 9 * * *"
TIMEZONE = "UTC"
SIGNAL_ENGINE = (
    '"""Momentum engine used by the bound integration fixtures."""\n'
    "\n"
    "def generate_signals(bars):\n"
    "    return [1 if bar['close'] > bar['open'] else 0 for bar in bars]\n"
)
_CONFIG = {"source": "local", "codes": ["TEST.SH"]}
_BARS = 40


@pytest.fixture(autouse=True)
def _clean_binding_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BINDING_ENV, raising=False)
    monkeypatch.delenv(TOKEN_ENV, raising=False)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteStrategyStore:
    import src.strategy_store._shared as shared

    bound = SqliteStrategyStore(tmp_path / "strategy_store.db")
    monkeypatch.setattr(shared, "_store", bound)
    return bound


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvidenceStore:
    cache = EvidenceStore(tmp_path / "strategy_discovery.db")
    monkeypatch.setattr(gate_code, "_open_evidence_store", lambda: cache)
    return cache


@pytest.fixture
def binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return configure_binding(tmp_path, monkeypatch)


def configure_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = TOKEN,
    digest: str | None = None,
) -> str:
    token_path = tmp_path / "service-token"
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    binding_path = tmp_path / "owner-binding.json"
    binding_path.write_text(
        json.dumps(
            {
                "schema_version": "strategy-owner-binding-v1",
                "owner_user_id": OWNER,
                "token_sha256": digest or hashlib.sha256(token.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    binding_path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(token_path))
    monkeypatch.setenv(BINDING_ENV, str(binding_path))
    return OWNER


def code_spec(name: str = "Bound Momentum") -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Cross-sectional momentum.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def workflow_spec(name: str = "Bound Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength confirmed by volume.",
        "universe": "cn_a",
        "steps": [
            {
                "id": "s1",
                "action": "load prices",
                "tool": "get_market_data",
                "output": "prices",
            },
            {
                "id": "s2",
                "action": "score",
                "tool": "technical_indicators",
                "output": "score",
            },
            {
                "id": "s3",
                "action": "summarise",
                "tool": "web_search",
                "output": "table",
            },
        ],
        "preconditions": ["the exchange is open"],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "score"],
            "destination": "chat",
        },
        "failure_handling": "Report the failing step and stop.",
    }


def write_run(tmp_path: Path, *, name: str = "run") -> Path:
    run_dir = tmp_path / "runs" / name
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text('{"status":"success"}', encoding="utf-8")
    columns = "final_value,total_return,annual_return,max_drawdown,sharpe,calmar,trade_count"
    row = "1000000.0,0.012,0.11,-0.05,1.4,2.2,3"
    (artifacts / "metrics.csv").write_text(columns + "\n" + row + "\n", encoding="utf-8")
    lines = ["timestamp,equity,benchmark_equity"]
    for index in range(_BARS):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        lines.append(f"{day},{1_000_000.0 * (1.0 + 0.001 * index):.6f},100.000000")
    (artifacts / "equity.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    trades = ["timestamp,code,side,price,qty,reason,pnl,holding_days,return_pct,event"]
    for index in (4, 8, 12):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        trades.append(f"{day},TEST.SH,buy,10.0,100.0,signal,0.0,0,0.0,entry")
        trades.append(f"{day},TEST.SH,sell,10.5,100.0,signal,50.0,0,0.5,exit")
    (artifacts / "trades.csv").write_text("\n".join(trades) + "\n", encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps(_CONFIG), encoding="utf-8")
    engine = run_dir / "code" / "signal_engine.py"
    engine.parent.mkdir(parents=True)
    engine.write_text(SIGNAL_ENGINE, encoding="utf-8")
    return run_dir


def promote_code(
    tmp_path: Path,
    store: SqliteStrategyStore,
    *,
    name: str = "Bound Momentum",
    demo: bool = False,
) -> PromotionResult:
    return promote(
        code_spec(name),
        session_id="bound-session",
        run_dir=str(write_run(tmp_path, name=name)),
        demo=demo,
        store=store,
        runtime_root=tmp_path,
    )


def manual_strategy(tmp_path: Path, store: SqliteStrategyStore, *, owner: str | None = None) -> Artifact:
    draft = create_draft(code_spec(), store=store, runtime_root=tmp_path)
    root = strategy_root(draft.id, tmp_path)
    (root / "code").mkdir(parents=True)
    (root / "config.json").write_text('{"source":"local"}\n', encoding="utf-8")
    (root / "code" / "signal_engine.py").write_text(SIGNAL_ENGINE, encoding="utf-8")
    ready = record_verdict(
        draft.id,
        {"verdict": "pass"},
        expected_version=draft.artifact_version,
        expected_spec_sha256=draft.spec_sha256,
        readiness="ready",
        validation_status="validated",
        store=store,
    )
    if owner is None:
        return ready
    return store.update_artifact(replace(ready, owner=owner))


def draft_with_version_dir(tmp_path: Path, store: SqliteStrategyStore) -> tuple[Artifact, Path]:
    draft = create_draft(code_spec(), store=store, runtime_root=tmp_path)
    root = strategy_root(draft.id, tmp_path)
    (root / "code").mkdir(parents=True)
    (root / "config.json").write_text('{"source":"local"}\n', encoding="utf-8")
    (root / "code" / "signal_engine.py").write_text(SIGNAL_ENGINE, encoding="utf-8")
    target = paths.version_dir(draft.id, draft.artifact_version, runtime_root=tmp_path)
    target.mkdir(parents=True)
    return draft, target


def write_legacy_tree(target: Path) -> dict[str, bytes]:
    (target / "code").mkdir(parents=True)
    (target / "strategy.json").write_text('{"legacy":true}', encoding="utf-8")
    (target / "config.json").write_text('{"legacy":true}', encoding="utf-8")
    (target / "code" / "signal_engine.py").write_text("legacy\n", encoding="utf-8")
    return files(target)


def registry(store: SqliteStrategyStore) -> SqliteVersionRegistry:
    return SqliteVersionRegistry(store.db_path)


def strategy_root(strategy_id: str, tmp_path: Path) -> Path:
    return paths.strategy_dir(strategy_id, runtime_root=tmp_path)


def files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def table_exists(db_path: Path) -> bool:
    with sqlite3.connect(db_path) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' " "AND name='strategy_version_registrations'"
            ).fetchone()
            is not None
        )


def registration_count(db_path: Path) -> int:
    if not table_exists(db_path):
        return 0
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM strategy_version_registrations").fetchone()[0]


def key_for(artifact: Artifact) -> RegistrationKey:
    return RegistrationKey.parse(OWNER, artifact.id, artifact.artifact_version)
