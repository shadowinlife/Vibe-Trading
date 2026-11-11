"""Focused behavior tests for the prepared-to-started ledger transition."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    JsonValue,
    canonical_json_bytes,
    sha256_hex,
)
from src.strategy_runs.control_store import (
    ControlStore,
    DispatchConflict,
    DispatchNotFound,
)
from src.strategy_runs.store import DEFAULT_DB_FILENAME

_NOW = 1_700_000_000_000
_PREPARE_DECLARATION: dict[str, JsonValue] = {
    "prompt": "prepare declaration must stay unchanged",
    "run_dir": "/runs/remote-a",
    "input_digest": "a" * 64,
    "config_sha256": "b" * 64,
    "source_sha256": "c" * 64,
}
_START_DECLARATION: dict[str, JsonValue] = {
    "dispatch_key": "dispatch-a",
    "kind": "backtest",
    "run_id": "remote-a",
    "run_dir": "/runs/remote-a",
    "input_digest": "a" * 64,
    "config_sha256": "b" * 64,
    "source_sha256": "c" * 64,
    "build_fingerprint": "d" * 64,
    "prompt": "fixed execution prompt",
}
_LEGACY_SCHEMA = """
CREATE TABLE strategy_runs (
    vt_run_id TEXT PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    error TEXT, error_kind TEXT, strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL, sandbox_id TEXT, upstream TEXT,
    timeout_s INTEGER NOT NULL, handle_json TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""
_OLD_DISPATCH_SCHEMA = """
CREATE TABLE execution_dispatches (
    dispatch_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('backtest', 'factor')),
    remote_id TEXT NOT NULL UNIQUE,
    declared_digest TEXT NOT NULL,
    declaration_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'dispatched', 'completed', 'failed', 'cancelled'
    )),
    input_digest TEXT,
    config_sha256 TEXT,
    source_sha256 TEXT,
    run_dir TEXT,
    result_path TEXT,
    error_kind TEXT,
    worker_pid INTEGER,
    worker_pgid INTEGER,
    worker_meta_json TEXT,
    started_at INTEGER,
    terminal_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    contract_version TEXT NOT NULL,
    CHECK(
        (status = 'dispatched' AND terminal_at IS NULL AND error_kind IS NULL)
        OR (status IN ('completed', 'cancelled')
            AND terminal_at IS NOT NULL AND error_kind IS NULL)
        OR (status = 'failed' AND terminal_at IS NOT NULL
            AND error_kind IS NOT NULL AND length(error_kind) > 0)
    )
);
"""


def _claim(store: ControlStore):
    return store.claim_dispatch(
        "dispatch-a",
        "backtest",
        _PREPARE_DECLARATION,
        "remote-a",
        CONTRACT_VERSION,
        now_ms=_NOW,
    )


def _begin_start(
    store: ControlStore,
    *,
    dispatch_key: str = "dispatch-a",
    remote_id: str = "remote-a",
    declaration: Mapping[str, JsonValue] | None = None,
    contract_version: str = CONTRACT_VERSION,
    now_ms: int = _NOW + 10,
):
    return store.begin_start(
        dispatch_key,
        remote_id,
        _START_DECLARATION if declaration is None else declaration,
        contract_version,
        now_ms=now_ms,
    )


def _start_bytes() -> bytes:
    declaration = dict(_START_DECLARATION)
    declaration["contract_version"] = CONTRACT_VERSION
    return canonical_json_bytes(declaration)


def test_first_start_binds_declaration_and_sets_started_at(tmp_path: Path) -> None:
    # Given a prepared dispatch with no start declaration
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    prepared = _claim(store).row

    # When the first start is accepted
    first = _begin_start(store)

    # Then only the start fields and first-start timestamps are added
    assert first.replayed is False
    assert first.start_digest == sha256_hex(_start_bytes())
    assert first.row.start_digest == first.start_digest
    assert first.row.start_declaration_json == _start_bytes().decode("utf-8")
    assert first.row.started_at == first.row.updated_at == _NOW + 10
    assert first.row.created_at == prepared.created_at
    assert first.row.declaration_json == prepared.declaration_json
    assert first.row.declared_digest == prepared.declared_digest
    assert first.row.status == "dispatched"
    assert store.get_dispatch("dispatch-a") == first.row


def test_same_start_replays_without_touching_bytes_or_timestamps(
    tmp_path: Path,
) -> None:
    # Given an accepted start
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    first = _begin_start(store)
    reordered = dict(reversed(list(_START_DECLARATION.items())))

    # When the canonical-equivalent declaration is replayed later
    replay = _begin_start(store, declaration=reordered, now_ms=_NOW + 20)

    # Then the original stored row is returned byte-for-byte
    assert replay.replayed is True
    assert replay.start_digest == first.start_digest
    assert replay.row == first.row
    assert store.get_dispatch("dispatch-a") == first.row


def test_start_preserves_an_existing_worker_observation_time(tmp_path: Path) -> None:
    # Given worker metadata observed before the start declaration
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    running = store.record_worker(
        "dispatch-a", 123, 456, {"attempt": 1}, now_ms=_NOW + 2
    )

    # When the start declaration is accepted afterward
    first = _begin_start(store, now_ms=_NOW + 10)

    # Then COALESCE retains the first observation and all worker identity
    assert first.row.started_at == running.started_at == _NOW + 2
    assert first.row.updated_at == _NOW + 10
    assert (first.row.worker_pid, first.row.worker_pgid) == (123, 456)
    assert first.row.worker_meta_json == running.worker_meta_json


def test_changed_start_declaration_conflicts_without_partial_update(
    tmp_path: Path,
) -> None:
    # Given an accepted start
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    first = _begin_start(store)
    changed = dict(_START_DECLARATION)
    changed["prompt"] = "changed execution prompt"

    # When the same dispatch is started with a different declaration
    with pytest.raises(DispatchConflict) as exc_info:
        _begin_start(store, declaration=changed, now_ms=_NOW + 20)

    # Then the conflict names the original id and no start bytes are replaced
    assert exc_info.value.original_remote_id == "remote-a"
    assert store.get_dispatch("dispatch-a") == first.row


def test_changed_contract_version_conflicts_without_partial_update(
    tmp_path: Path,
) -> None:
    # Given an accepted versioned start
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    first = _begin_start(store)

    # When only the contract version changes
    with pytest.raises(DispatchConflict) as exc_info:
        _begin_start(
            store,
            contract_version="mcp-execution-control-v2",
            now_ms=_NOW + 20,
        )

    # Then the original versioned declaration remains authoritative
    assert exc_info.value.original_remote_id == "remote-a"
    assert store.get_dispatch("dispatch-a") == first.row


def test_wrong_remote_conflicts_before_any_start_update(tmp_path: Path) -> None:
    # Given a prepared dispatch
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    prepared = _claim(store).row

    # When another remote identity attempts to start it
    with pytest.raises(DispatchConflict) as exc_info:
        _begin_start(store, remote_id="remote-b")

    # Then no declaration or timestamp is persisted
    assert exc_info.value.original_remote_id == "remote-a"
    assert store.get_dispatch("dispatch-a") == prepared
    assert prepared.start_declaration_json is None
    assert prepared.start_digest is None


def test_missing_start_dispatch_raises_not_found(tmp_path: Path) -> None:
    # Given an empty ledger
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)

    # When a start names no prepared dispatch
    with pytest.raises(DispatchNotFound) as exc_info:
        _begin_start(store, dispatch_key="missing")

    # Then absence is typed and no row is invented
    assert exc_info.value.dispatch_key == "missing"
    assert store.get_dispatch("missing") is None
    assert store.get_dispatch("dispatch-a") is None


def test_terminal_row_with_same_start_digest_replays(tmp_path: Path) -> None:
    # Given a started dispatch that has since completed
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    first = _begin_start(store)
    terminal = store.mark_dispatch_terminal(
        "dispatch-a", "completed", result_path="/results/a.json", now_ms=_NOW + 20
    )

    # When the identical start arrives after the terminal transition
    replay = _begin_start(store, now_ms=_NOW + 30)

    # Then it replays the terminal row without reviving or modifying it
    assert replay.replayed is True
    assert replay.start_digest == first.start_digest
    assert replay.row == terminal
    assert store.get_dispatch("dispatch-a") == terminal


def test_terminal_row_without_start_digest_conflicts(tmp_path: Path) -> None:
    # Given a terminal prepare-only dispatch
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    terminal = store.mark_dispatch_terminal("dispatch-a", "cancelled", now_ms=_NOW + 20)

    # When a first start arrives too late
    with pytest.raises(DispatchConflict) as exc_info:
        _begin_start(store, now_ms=_NOW + 30)

    # Then the terminal row remains immutable
    assert exc_info.value.original_remote_id == "remote-a"
    assert store.get_dispatch("dispatch-a") == terminal


def test_sixteen_concurrent_starts_have_one_new_claim_and_fifteen_replays(
    tmp_path: Path,
) -> None:
    # Given one prepared row and sixteen independent store connections
    path = tmp_path / DEFAULT_DB_FILENAME
    _claim(ControlStore(path))
    stores = [ControlStore(path) for _ in range(16)]
    barrier = threading.Barrier(16, timeout=10)

    def attempt(index: int):
        barrier.wait()
        return _begin_start(stores[index], now_ms=_NOW + 100 + index)

    # When all callers race through BEGIN IMMEDIATE and the start CAS
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = [
            future.result(timeout=15)
            for future in [pool.submit(attempt, index) for index in range(16)]
        ]

    # Then exactly one caller is new and every replay returns the same durable row
    winners = [result for result in results if not result.replayed]
    assert len(winners) == 1
    assert len(results) - len(winners) == 15
    assert {result.start_digest for result in results} == {winners[0].start_digest}
    assert {result.row for result in results} == {winners[0].row}
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*), COUNT(start_digest), COUNT(DISTINCT start_digest) "
            "FROM execution_dispatches"
        ).fetchone() == (1, 1, 1)


def test_old_dispatch_schema_migrates_without_rewriting_rows(
    tmp_path: Path,
) -> None:
    # Given an old-shaped shared ledger with dispatch and legacy rows
    path = tmp_path / DEFAULT_DB_FILENAME
    with sqlite3.connect(path) as conn:
        conn.executescript(_LEGACY_SCHEMA)
        conn.executescript(_OLD_DISPATCH_SCHEMA)
        conn.execute(
            "INSERT INTO strategy_runs VALUES (?, ?, 'completed', NULL, NULL, ?, ?, "
            "NULL, NULL, 900, NULL, 1, 2)",
            ("legacy-remote", "legacy-key", "strategy", "v1"),
        )
        conn.execute(
            "INSERT INTO execution_dispatches VALUES ("
            "'dispatch-a', 'backtest', 'remote-a', 'prepare-digest', "
            "'{\"prompt\":\"legacy\"}', 'dispatched', NULL, NULL, NULL, "
            "'/runs/remote-a', NULL, NULL, NULL, NULL, NULL, 123, NULL, 100, 120, ?)",
            (CONTRACT_VERSION,),
        )
        before_dispatch = conn.execute("SELECT * FROM execution_dispatches").fetchone()
        before_legacy = conn.execute("SELECT * FROM strategy_runs").fetchone()

    # When schema initialization runs repeatedly on the old database
    store = ControlStore(path)
    store.ensure_schema()
    ControlStore(path).ensure_schema()

    # Then both nullable columns exist, all old values survive, and reads use them
    with sqlite3.connect(path) as conn:
        column_rows = {
            row[1]: row
            for row in conn.execute("PRAGMA table_info(execution_dispatches)")
        }
        after_dispatch = conn.execute("SELECT * FROM execution_dispatches").fetchone()
        after_legacy = conn.execute("SELECT * FROM strategy_runs").fetchone()
    assert column_rows["start_declaration_json"][3] == 0
    assert column_rows["start_digest"][3] == 0
    assert after_dispatch[: len(before_dispatch)] == before_dispatch
    assert after_dispatch[len(before_dispatch) :] == (None, None)
    assert after_legacy == before_legacy
    migrated = store.get_dispatch("dispatch-a")
    assert migrated is not None
    assert migrated.start_declaration_json is None
    assert migrated.start_digest is None
    assert migrated.started_at == 123
