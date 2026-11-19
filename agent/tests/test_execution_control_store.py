"""Behavioral tests for the shared execution dispatch/stop SQLite ledger."""

from __future__ import annotations

import sqlite3
import stat
import sys
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
    ControlStoreError,
    DispatchConflict,
    DispatchNotFound,
    DispatchTransitionError,
    StopConflict,
    StopTransitionError,
)
from src.strategy_runs.store import DEFAULT_DB_FILENAME, StrategyRunStore

_NOW = 1_700_000_000_000
_DECLARATION: dict[str, JsonValue] = {
    "prompt": "fixed prompt",
    "run_dir": "/runs/remote-a",
    "input_digest": "a" * 64,
    "config_sha256": "b" * 64,
    "source_sha256": "c" * 64,
}
_DISPATCH_COLUMNS = {
    "dispatch_key",
    "kind",
    "remote_id",
    "declared_digest",
    "declaration_json",
    "input_digest",
    "config_sha256",
    "source_sha256",
    "run_dir",
    "result_path",
    "status",
    "error_kind",
    "worker_pid",
    "worker_pgid",
    "worker_meta_json",
    "created_at",
    "updated_at",
    "started_at",
    "terminal_at",
    "contract_version",
}
_STOP_COLUMNS = {
    "stop_key",
    "dispatch_key",
    "remote_id",
    "state",
    "requested_at",
    "deadline_at",
    "resolved_at",
    "evidence_kind",
    "evidence_json",
    "updated_at",
    "contract_version",
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


def _declaration(**changes: JsonValue) -> dict[str, JsonValue]:
    declaration = dict(_DECLARATION)
    declaration.update(changes)
    return declaration


def _claim(
    store: ControlStore,
    dispatch_key: str = "dispatch-a",
    remote_id: str = "remote-a",
    declaration: Mapping[str, JsonValue] | None = None,
):
    return store.claim_dispatch(
        dispatch_key,
        "backtest",
        _DECLARATION if declaration is None else declaration,
        remote_id,
        CONTRACT_VERSION,
        now_ms=_NOW,
    )


def _begin_stop(
    store: ControlStore,
    dispatch_key: str = "dispatch-a",
    remote_id: str = "remote-a",
    stop_key: str = "stop-a",
):
    return store.begin_stop(
        dispatch_key,
        remote_id,
        stop_key,
        _NOW + 10_000,
        CONTRACT_VERSION,
        now_ms=_NOW + 1,
    )


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_shared_schema_has_required_columns_indexes_and_private_wal(
    tmp_path: Path,
) -> None:
    # Given a control store on the canonical filename
    path = tmp_path / DEFAULT_DB_FILENAME
    ControlStore(path)
    # When the schema is inspected through an independent connection
    with sqlite3.connect(path) as conn:
        dispatch_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_dispatches)")
        }
        stop_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_stops)")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(execution_dispatches)")
        }
        stop_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(execution_stops)")
        }
        foreign_keys = list(conn.execute("PRAGMA foreign_key_list(execution_stops)"))
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    # Then both tables share the legacy file and its hygiene conventions
    assert dispatch_columns >= _DISPATCH_COLUMNS
    assert stop_columns >= _STOP_COLUMNS
    assert "idx_execution_dispatches_remote_id" in indexes
    assert "idx_execution_stops_dispatch_remote" in stop_indexes
    assert any(row[2] == "execution_dispatches" for row in foreign_keys)
    assert journal_mode.lower() == "wal"
    assert list(tmp_path.glob("*.sqlite3")) == [path]
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_legacy_schema_upgrade_preserves_rows_and_lookup_is_read_only(
    tmp_path: Path,
) -> None:
    # Given a database created with only the legacy schema and one terminal row
    path = tmp_path / DEFAULT_DB_FILENAME
    with sqlite3.connect(path) as conn:
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO strategy_runs VALUES (?, ?, 'completed', NULL, NULL, ?, ?, NULL, NULL, 900, NULL, 1, 2)",
            ("legacy-remote", "legacy-key", "strategy", "v1"),
        )
    # When the new shared schema is ensured and reopened
    store = ControlStore(path)
    by_key = store.lookup_legacy(run_key="legacy-key")
    by_id = store.lookup_legacy(vt_run_id="legacy-remote")
    StrategyRunStore(path).ensure_schema()
    # Then the old row is unchanged, no dispatch is invented, and both reads agree
    assert by_key == by_id
    assert by_key is not None
    assert (by_key.status, by_key.created_at, by_key.updated_at) == ("completed", 1, 2)
    assert _count(path, "strategy_runs") == 1
    assert _count(path, "execution_dispatches") == 0


def test_claim_replays_canonical_declaration_with_stored_remote_id(
    tmp_path: Path,
) -> None:
    # Given an initial claim
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    first = _claim(store)
    reordered = dict(reversed(list(_declaration().items())))
    # When the same normalized declaration is replayed with another remote id
    replay = _claim(store, remote_id="caller-must-not-win", declaration=reordered)
    # Then the original identity and canonical declaration are returned
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.row.remote_id == "remote-a"
    assert replay.row.declared_digest == sha256_hex(canonical_json_bytes(_DECLARATION))
    assert replay.row.declaration_json == canonical_json_bytes(_DECLARATION).decode(
        "utf-8"
    )
    assert replay.row.input_digest == "a" * 64
    assert replay.row.config_sha256 == "b" * 64
    assert replay.row.source_sha256 == "c" * 64
    assert store.get_dispatch("dispatch-a") == first.row
    assert store.get_dispatch_by_remote_id("remote-a") == first.row
    assert _count(store.path, "execution_dispatches") == 1


@pytest.mark.parametrize(
    ("kind", "declaration"),
    [("factor", _DECLARATION), ("backtest", _declaration(prompt="changed"))],
)
def test_claim_conflict_reports_original_remote_id(
    tmp_path: Path, kind: str, declaration: Mapping[str, JsonValue]
) -> None:
    # Given a claimed dispatch key
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    # When the key is reused with another kind or digest
    with pytest.raises(DispatchConflict) as exc_info:
        store.claim_dispatch(
            "dispatch-a", kind, declaration, "remote-b", CONTRACT_VERSION
        )
    # Then the conflict names the original id and leaves the row intact
    assert exc_info.value.original_remote_id == "remote-a"
    assert exc_info.value.operation == "claim_dispatch"
    assert store.get_dispatch("dispatch-a").remote_id == "remote-a"
    assert _count(store.path, "execution_dispatches") == 1


def test_sixteen_concurrent_claims_create_exactly_one_row_and_winner(
    tmp_path: Path,
) -> None:
    # Given sixteen independent store instances claiming one declaration
    path = tmp_path / DEFAULT_DB_FILENAME
    stores = [ControlStore(path) for _ in range(16)]
    barrier = threading.Barrier(16, timeout=10)

    def attempt(index: int):
        barrier.wait()
        return _claim(stores[index], remote_id=f"remote-{index}")

    # When all claimants enter BEGIN IMMEDIATE concurrently
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = [
            future.result(timeout=15)
            for future in [pool.submit(attempt, i) for i in range(16)]
        ]
    # Then exactly one insert wins and every replay returns its identity
    winners = [result for result in results if not result.replayed]
    assert len(winners) == 1
    assert {result.row.remote_id for result in results} == {winners[0].row.remote_id}
    assert _count(path, "execution_dispatches") == 1


def test_terminal_completed_cas_is_final_and_reopen_stable(tmp_path: Path) -> None:
    # Given a dispatched row that reaches completed with a result
    path = tmp_path / DEFAULT_DB_FILENAME
    store = ControlStore(path)
    _claim(store)
    completed = store.mark_dispatch_terminal(
        "dispatch-a", "completed", result_path="/results/a.json", now_ms=_NOW + 5
    )
    # When a late terminal write is attempted and the store is reopened
    with pytest.raises(DispatchTransitionError):
        store.mark_dispatch_terminal(
            "dispatch-a", "failed", error_kind="runner_error", now_ms=_NOW + 6
        )
    reopened = ControlStore(path).get_dispatch("dispatch-a")
    # Then the first terminal row remains byte-for-byte authoritative
    assert completed.status == "completed"
    assert completed.result_path == "/results/a.json"
    assert completed.terminal_at == completed.updated_at == _NOW + 5
    assert reopened == completed


@pytest.mark.parametrize(
    ("status", "error_kind"),
    [
        ("unknown", None),
        ("dispatched", None),
        ("failed", None),
        ("failed", ""),
        ("completed", "runner_error"),
    ],
)
def test_invalid_dispatch_terminal_is_rejected_without_mutation(
    tmp_path: Path, status: str, error_kind: str | None
) -> None:
    # Given a dispatched row
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    # When an invalid terminal transition is requested
    with pytest.raises(DispatchTransitionError):
        store.mark_dispatch_terminal("dispatch-a", status, error_kind=error_kind)
    # Then the row remains dispatched and unknown is never persisted
    row = store.get_dispatch("dispatch-a")
    assert row is not None
    assert (row.status, row.terminal_at, row.error_kind) == ("dispatched", None, None)


def test_failed_and_cancelled_terminal_rows_keep_their_evidence(tmp_path: Path) -> None:
    # Given two dispatched rows
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _claim(store, "dispatch-b", "remote-b")
    # When they reach failed and cancelled respectively
    failed = store.mark_dispatch_terminal(
        "dispatch-a", "failed", error_kind="timeout", now_ms=_NOW + 2
    )
    cancelled = store.mark_dispatch_terminal("dispatch-b", "cancelled", now_ms=_NOW + 3)
    # Then each terminal projection retains only its valid fields
    assert (failed.status, failed.error_kind, failed.terminal_at) == (
        "failed",
        "timeout",
        _NOW + 2,
    )
    assert (cancelled.status, cancelled.error_kind, cancelled.terminal_at) == (
        "cancelled",
        None,
        _NOW + 3,
    )


def test_record_worker_is_cas_and_never_updates_terminal_rows(tmp_path: Path) -> None:
    # Given a dispatch with worker identity persisted
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    running = store.record_worker(
        "dispatch-a", 123, 456, {"attempt": 1}, now_ms=_NOW + 2
    )
    store.mark_dispatch_terminal("dispatch-a", "cancelled", now_ms=_NOW + 3)
    # When worker metadata arrives after the terminal transition
    with pytest.raises(DispatchTransitionError):
        store.record_worker("dispatch-a", 999, 999, {"attempt": 2})
    # Then the original worker identity remains unchanged
    final = store.get_dispatch("dispatch-a")
    assert running.worker_pid == 123
    assert running.worker_pgid == 456
    assert running.worker_meta_json == '{"attempt":1}'
    assert running.started_at == _NOW + 2
    assert final is not None
    assert (final.worker_pid, final.worker_pgid, final.updated_at) == (
        123,
        456,
        _NOW + 3,
    )


def test_missing_dispatch_transition_raises_not_found(tmp_path: Path) -> None:
    # Given an empty store
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    # When a terminal or worker write names no row
    with pytest.raises(DispatchNotFound):
        store.mark_dispatch_terminal("missing", "completed")
    with pytest.raises(DispatchNotFound):
        store.record_worker("missing", 1, 1, {})
    # Then typed reads still distinguish absence from a storage failure
    assert store.get_dispatch("missing") is None
    assert store.get_dispatch_by_remote_id("missing") is None


def test_begin_stop_replays_only_the_same_full_tuple(tmp_path: Path) -> None:
    # Given a requested stop
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    first = _begin_stop(store)
    # When the identical tuple is replayed and then changed at the deadline
    replay = _begin_stop(store)
    with pytest.raises(StopConflict) as exc_info:
        store.begin_stop(
            "dispatch-a", "remote-a", "stop-a", _NOW + 11_000, CONTRACT_VERSION
        )
    # Then the replay is idempotent while the changed tuple conflicts
    assert first.replayed is False
    assert first.row.state == "requested"
    assert first.row.requested_at == _NOW + 1
    assert first.row.deadline_at == _NOW + 10_000
    assert replay.replayed is True
    assert replay.row == first.row
    assert exc_info.value.original_remote_id == "remote-a"
    assert store.get_stop("stop-a") == first.row
    assert store.list_stops("remote-a") == [first.row]


def test_stop_identity_mismatch_precedes_reused_key_handling(tmp_path: Path) -> None:
    # Given two dispatches and a stop already bound to the first
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _claim(store, "dispatch-b", "remote-b")
    _begin_stop(store, stop_key="shared-stop")
    # When remote-b is paired with dispatch-a using the existing stop key
    with pytest.raises(StopConflict) as mismatch:
        store.begin_stop(
            "dispatch-a", "remote-b", "shared-stop", _NOW + 10_000, CONTRACT_VERSION
        )
    # Then dispatch identity is rejected before the key can be replayed
    assert mismatch.value.original_remote_id == "remote-a"
    assert _count(store.path, "execution_stops") == 1


def test_stop_key_reuse_on_another_remote_conflicts(tmp_path: Path) -> None:
    # Given two dispatches and a stop bound to remote-a
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _claim(store, "dispatch-b", "remote-b")
    _begin_stop(store, stop_key="shared-stop")
    # When the same stop key is submitted for remote-b
    with pytest.raises(StopConflict) as exc_info:
        store.begin_stop(
            "dispatch-b", "remote-b", "shared-stop", _NOW + 10_000, CONTRACT_VERSION
        )
    # Then the conflict identifies the original remote and inserts nothing
    assert exc_info.value.original_remote_id == "remote-a"
    assert _count(store.path, "execution_stops") == 1


def test_begin_stop_unknown_remote_raises_dispatch_not_found(tmp_path: Path) -> None:
    # Given no dispatch for the requested remote id
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    # When a stop is begun
    with pytest.raises(DispatchNotFound):
        _begin_stop(store)
    # Then no stop evidence can be invented
    assert store.get_stop("stop-a") is None
    assert store.list_stops("remote-a") == []


@pytest.mark.parametrize(
    ("evidence_kind", "payload"),
    [
        (None, {"exit_code": 0}),
        ("not-evidence", {"exit_code": 0}),
        ("process_exit", None),
        ("process_exit", {}),
        ("process_exit", ""),
        ("process_exit", 0),
        ("process_exit", False),
    ],
)
def test_confirmed_stop_requires_valid_nonempty_evidence(
    tmp_path: Path, evidence_kind: str | None, payload: JsonValue
) -> None:
    # Given a requested stop
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    # When confirmation lacks an allowed kind or non-empty payload
    with pytest.raises(StopTransitionError):
        store.resolve_stop("stop-a", "confirmed", evidence_kind, payload, _NOW + 2)
    # Then it remains requested with no resolution or evidence
    row = store.get_stop("stop-a")
    assert row is not None
    assert (row.state, row.resolved_at, row.evidence_json) == ("requested", None, None)


def test_confirmed_stop_persists_evidence_and_never_downgrades(tmp_path: Path) -> None:
    # Given a requested stop resolved with process-exit evidence
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    confirmed = store.resolve_stop(
        "stop-a", "confirmed", "process_exit", {"exit_code": 0}, _NOW + 2
    )
    # When any later resolution is attempted, including a downgrade to unknown
    with pytest.raises(StopTransitionError):
        store.resolve_stop("stop-a", "unknown", resolved_at=_NOW + 3)
    # Then confirmed evidence is immutable
    assert confirmed.state == "confirmed"
    assert confirmed.evidence_kind == "process_exit"
    assert confirmed.evidence_json == '{"exit_code":0}'
    assert confirmed.resolved_at == confirmed.updated_at == _NOW + 2
    assert store.get_stop("stop-a") == confirmed


def test_unknown_stop_short_circuits_a_new_stop_key(tmp_path: Path) -> None:
    # Given a dispatch whose only stop is final unknown
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    unknown = store.resolve_stop("stop-a", "unknown", resolved_at=_NOW + 2)
    # When another stop key is begun for the same dispatch
    replay = store.begin_stop(
        "dispatch-a", "remote-a", "stop-b", _NOW + 20_000, CONTRACT_VERSION
    )
    # Then the final unknown row is reused and cannot become a new confirmation
    assert replay.replayed is True
    assert replay.row == unknown
    assert _count(store.path, "execution_stops") == 1


def test_unknown_stop_never_upgrades_to_confirmed(tmp_path: Path) -> None:
    # Given a stop resolved as unknown without evidence
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    unknown = store.resolve_stop("stop-a", "unknown", resolved_at=_NOW + 2)
    # When later evidence attempts to upgrade it
    with pytest.raises(StopTransitionError):
        store.resolve_stop(
            "stop-a", "confirmed", "process_exit", {"exit_code": 0}, _NOW + 3
        )
    # Then unknown remains the final stop projection, not a dispatch terminal
    assert unknown.state == "unknown"
    assert unknown.evidence_kind is None
    assert unknown.evidence_json is None
    assert store.get_stop("stop-a") == unknown
    assert store.get_dispatch("dispatch-a").status == "dispatched"


def test_confirmed_stop_short_circuits_a_new_stop_key(tmp_path: Path) -> None:
    # Given a dispatch with one confirmed stop
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    confirmed = store.resolve_stop(
        "stop-a", "confirmed", "terminal_record", {"status": "cancelled"}, _NOW + 2
    )
    # When another stop key is begun for the same dispatch
    replay = store.begin_stop(
        "dispatch-a", "remote-a", "stop-b", _NOW + 20_000, CONTRACT_VERSION
    )
    # Then the confirmed result is reused without a downgrade or second row
    assert replay.replayed is True
    assert replay.row == confirmed
    assert _count(store.path, "execution_stops") == 1


def test_unknown_resolution_rejects_evidence(tmp_path: Path) -> None:
    # Given a requested stop
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    _begin_stop(store)
    # When unknown is paired with purported proof
    with pytest.raises(StopTransitionError):
        store.resolve_stop(
            "stop-a", "unknown", "process_absent", {"pid": 123}, _NOW + 2
        )
    # Then no contradictory evidence is stored
    assert store.get_stop("stop-a").state == "requested"


def test_unknown_dispatch_status_is_rejected_by_schema(tmp_path: Path) -> None:
    # Given a dispatched row
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)
    _claim(store)
    # When a raw write attempts to persist the stop-only unknown projection
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE execution_dispatches SET status = 'unknown' WHERE dispatch_key = 'dispatch-a'"
            )
    # Then the dispatch remains in its sole nonterminal state
    assert store.get_dispatch("dispatch-a").status == "dispatched"


def test_sqlite_read_and_write_failures_are_wrapped(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a store whose next real SQLite connections fail
    store = ControlStore(tmp_path / DEFAULT_DB_FILENAME)

    def fail_connect():
        raise sqlite3.OperationalError("injected database failure")

    monkeypatch.setattr(store, "_connect", fail_connect)
    # When a read and a write are attempted
    with pytest.raises(ControlStoreError) as read_error:
        store.get_dispatch("dispatch-a")
    with pytest.raises(ControlStoreError) as write_error:
        _claim(store)
    # Then callers receive typed failures with causes, never false absence
    assert read_error.value.operation == "get_dispatch"
    assert write_error.value.operation == "claim_dispatch"
    assert isinstance(read_error.value.__cause__, sqlite3.OperationalError)
    assert isinstance(write_error.value.__cause__, sqlite3.OperationalError)
