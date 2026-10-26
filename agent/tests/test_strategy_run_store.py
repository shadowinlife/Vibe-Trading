"""Tests for the strategy-run execution ledger.

Covers the idempotency contract the entrypoint depends on: one row per
``run_key`` across threads and restarts, verbatim key storage, and a
compare-and-swap terminal write. Every test uses an isolated ``tmp_path`` (or a
``VIBE_TRADING_HOME`` override); none touches the real runtime root.
"""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.strategy_runs import ClaimResult, RunRow, StrategyRunStore

_NOW = 1_700_000_000_000

_EXPECTED_COLUMNS = {
    "vt_run_id",
    "run_key",
    "status",
    "error",
    "error_kind",
    "strategy_id",
    "strategy_version",
    "sandbox_id",
    "upstream",
    "timeout_s",
    "handle_json",
    "created_at",
    "updated_at",
}

_RAW_INSERT = """
INSERT INTO strategy_runs (
    vt_run_id, run_key, status, error, error_kind, strategy_id,
    strategy_version, sandbox_id, upstream, timeout_s, handle_json,
    created_at, updated_at
) VALUES (?, ?, ?, NULL, NULL, 'strat-1', 'v1-abc', NULL, NULL, 900, NULL, 1, 1)
"""


def _claim(
    store: StrategyRunStore,
    run_key: str,
    *,
    strategy_id: str = "strat-1",
    strategy_version: str = "v1-abc",
    sandbox_id: str | None = "sbx-1",
    upstream: str | None = "rm",
    timeout_s: int = 900,
    now_ms: int = _NOW,
) -> ClaimResult:
    """Claim *run_key* with the ledger's documented defaults."""
    return store.claim(
        run_key,
        strategy_id,
        strategy_version,
        sandbox_id,
        upstream,
        timeout_s,
        now_ms=now_ms,
    )


def _row_count(db_path: Path) -> int:
    """Count ledger rows through an independent connection."""
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema and file hygiene
# ---------------------------------------------------------------------------


def test_fresh_store_creates_private_wal_schema(tmp_path: Path, monkeypatch) -> None:
    newer = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(newer))

    store = StrategyRunStore()

    assert store.path == newer / "strategy_runs.sqlite3"
    assert store.path.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    conn = sqlite3.connect(store.path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_runs)")}
    finally:
        conn.close()
    assert columns >= _EXPECTED_COLUMNS

    # The default path is resolved per instance, so a redirected environment
    # never falls back to the developer's real ~/.vibe-trading.
    assert "strategy_runs.sqlite3" in str(store.path)


def test_ensure_schema_is_idempotent_and_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite3"
    store = StrategyRunStore(db_path)
    claimed = _claim(store, "u1:s1:2026-10-26T00:00:00Z:1")

    store.ensure_schema()
    reopened = StrategyRunStore(db_path)
    reopened.ensure_schema()

    assert _row_count(db_path) == 1
    assert reopened.get(claimed.row.vt_run_id) == claimed.row


def test_unique_constraint_rejects_raw_duplicate_run_key(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite3"
    store = StrategyRunStore(db_path)
    _claim(store, "u1:s1:2026-10-26T00:00:00Z:1")

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as excinfo:
            conn.execute(
                _RAW_INSERT, ("other-id", "u1:s1:2026-10-26T00:00:00Z:1", "running")
            )
        assert "run_key" in str(excinfo.value) or "UNIQUE" in str(excinfo.value).upper()
    finally:
        conn.close()
    assert _row_count(db_path) == 1


def test_schema_rejects_unknown_status(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite3"
    StrategyRunStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_RAW_INSERT, ("id-1", "key-1", "skipped"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Claim: sequential duplicate, distinct keys, concurrency, restart
# ---------------------------------------------------------------------------


def test_duplicate_claim_returns_the_existing_run(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    key = "u1:s1:2026-10-26T00:00:00Z:1"

    first = _claim(store, key)
    assert first.won is True
    assert first.row.status == "running"
    assert first.row.timeout_s == 900
    assert first.row.created_at == first.row.updated_at == _NOW
    assert first.row.handle_json is None
    assert len(first.row.vt_run_id) == 32
    uuid.UUID(first.row.vt_run_id)  # 32 hex chars is a valid uuid hex form
    assert first.row.vt_run_id != hashlib.sha256(key.encode("utf-8")).hexdigest()

    second = _claim(
        store,
        key,
        sandbox_id="sbx-2",
        strategy_version="v9-other",
        now_ms=_NOW + 5,
    )
    assert second.won is False
    assert second.row.vt_run_id == first.row.vt_run_id
    # The loser observes the winner's row, not its own request parameters.
    assert second.row.sandbox_id == "sbx-1"
    assert second.row.strategy_version == "v1-abc"
    assert second.row.updated_at == _NOW

    assert store.get_by_run_key(key).vt_run_id == first.row.vt_run_id
    assert _row_count(store.path) == 1


def test_distinct_run_keys_are_distinct_runs(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")

    attempt_one = _claim(store, "u1:s1:2026-10-26T00:00:00Z:1")
    attempt_two = _claim(store, "u1:s1:2026-10-26T00:00:00Z:2", now_ms=_NOW + 1)

    assert attempt_one.won is True
    assert attempt_two.won is True
    assert attempt_one.row.vt_run_id != attempt_two.row.vt_run_id
    assert store.get_by_run_key("u1:s1:2026-10-26T00:00:00Z:1").vt_run_id == (
        attempt_one.row.vt_run_id
    )
    assert store.get_by_run_key("u1:s1:2026-10-26T00:00:00Z:2").vt_run_id == (
        attempt_two.row.vt_run_id
    )
    assert _row_count(store.path) == 2


def test_concurrent_duplicate_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite3"
    key = "u1:s1:2026-10-26T00:00:00Z:1"
    # One store instance per worker, all on the same file. No in-process lock is
    # involved: the winner is decided by SQLite's UNIQUE + BEGIN IMMEDIATE.
    workers = 8
    stores = [StrategyRunStore(db_path) for _ in range(workers)]
    barrier = threading.Barrier(workers, timeout=10.0)

    def attempt(index: int) -> ClaimResult:
        barrier.wait()
        return _claim(stores[index], key, sandbox_id=f"sbx-{index}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(attempt, index) for index in range(workers)]
        results = [future.result() for future in futures]

    winners = [result for result in results if result.won]
    assert len(winners) == 1
    winner_id = winners[0].row.vt_run_id
    assert {result.row.vt_run_id for result in results} == {winner_id}
    # Losers report the winner's routing rather than their own input.
    assert {result.row.sandbox_id for result in results} == {winners[0].row.sandbox_id}
    assert _row_count(db_path) == 1


def test_restart_still_reports_the_duplicate(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite3"
    key = "u1:s1:2026-10-26T00:00:00Z:1"
    first = _claim(StrategyRunStore(db_path), key)

    # A new instance on the same file stands in for a new process: the claim is
    # a property of the file, not of the previous store's memory.
    restarted = StrategyRunStore(Path(db_path))
    again = _claim(restarted, key, now_ms=_NOW + 60_000)

    assert again.won is False
    assert again.row.vt_run_id == first.row.vt_run_id
    assert again.row.created_at == _NOW
    assert _row_count(db_path) == 1


# ---------------------------------------------------------------------------
# Verbatim run_key handling
# ---------------------------------------------------------------------------


def test_run_key_is_stored_verbatim(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    keys = [
        "u-42:sched-7:2026-10-26T13:05:00Z:1",
        "用户:策略:2026-10-26T13:05:00Z:尝试1",
        "  padded key  ",
        "key\twith\ttabs",
    ]
    claimed = {
        key: _claim(store, key, now_ms=_NOW + index) for index, key in enumerate(keys)
    }

    for key, result in claimed.items():
        assert result.won is True
        stored = store.get_by_run_key(key)
        assert stored is not None
        assert stored.run_key == key
        assert stored.vt_run_id == result.row.vt_run_id

    # No trimming: "padded key" is a different key from "  padded key  ".
    unpadded = _claim(store, "padded key", now_ms=_NOW + 10)
    assert unpadded.won is True
    assert unpadded.row.vt_run_id != claimed["  padded key  "].row.vt_run_id
    assert _row_count(store.path) == len(keys) + 1


# ---------------------------------------------------------------------------
# Terminal CAS and handle writes
# ---------------------------------------------------------------------------


def test_terminal_write_is_compare_and_swap(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    run_id = _claim(store, "key-1").row.vt_run_id

    assert store.mark_terminal(run_id, "completed", now_ms=_NOW + 1) is True
    row = store.get(run_id)
    assert row is not None
    assert row.status == "completed"
    assert row.error is None
    assert row.error_kind is None
    assert row.updated_at == _NOW + 1

    # Second writer matches no row: the completed state is final.
    assert store.mark_terminal(run_id, "completed", now_ms=_NOW + 2) is False
    assert (
        store.mark_terminal(
            run_id, "failed", error="late", error_kind="runner_error", now_ms=_NOW + 3
        )
        is False
    )
    final = store.get(run_id)
    assert final is not None
    assert (final.status, final.error, final.error_kind) == ("completed", None, None)
    assert final.updated_at == _NOW + 1

    assert store.get("does-not-exist") is None
    assert store.mark_terminal("does-not-exist", "completed") is False


def test_failed_run_persists_error_and_kind(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    run_id = _claim(store, "key-1").row.vt_run_id

    assert (
        store.mark_terminal(
            run_id,
            "failed",
            error="runner unreachable",
            error_kind="runner_error",
            now_ms=_NOW + 7,
        )
        is True
    )
    row = store.get(run_id)
    assert row is not None
    assert (row.status, row.error, row.error_kind) == (
        "failed",
        "runner unreachable",
        "runner_error",
    )
    assert row.updated_at == _NOW + 7


@pytest.mark.parametrize(
    ("status", "error", "error_kind"),
    [
        ("running", None, None),
        ("skipped", None, None),
        ("completed", "boom", None),
        ("completed", None, "runner_error"),
        ("failed", None, None),
        ("failed", "boom", None),
        ("failed", None, "runner_error"),
    ],
)
def test_mark_terminal_rejects_contract_violations(
    tmp_path: Path, status: str, error: str | None, error_kind: str | None
) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    run_id = _claim(store, "key-1").row.vt_run_id

    with pytest.raises(ValueError):
        store.mark_terminal(run_id, status, error=error, error_kind=error_kind)

    row = store.get(run_id)
    assert row is not None
    assert row.status == "running"
    assert row.error is None
    assert row.error_kind is None


def test_set_handle_only_while_running(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    run_id = _claim(store, "key-1").row.vt_run_id
    handle = '{"run_id": "r-1", "kind": "code", "session_id": "s-1", "run_dir": "/tmp/run-1", "started_at": 1700000000000}'

    assert store.set_handle(run_id, handle, now_ms=_NOW + 1) is True
    row = store.get(run_id)
    assert row is not None
    assert row.handle_json == handle
    assert row.updated_at == _NOW + 1

    assert store.mark_terminal(run_id, "completed", now_ms=_NOW + 2) is True
    assert store.set_handle(run_id, handle, now_ms=_NOW + 3) is False
    assert store.set_handle("does-not-exist", handle) is False

    final = store.get(run_id)
    assert final is not None
    assert final.handle_json == handle
    assert final.updated_at == _NOW + 2

    with pytest.raises(ValueError):
        store.set_handle(run_id, "")


# ---------------------------------------------------------------------------
# Reads and input validation
# ---------------------------------------------------------------------------


def test_get_and_list_running(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    assert store.get("nope") is None
    assert store.get_by_run_key("nope") is None
    assert store.list_running() == []

    first = _claim(store, "key-1", now_ms=_NOW)
    second = _claim(store, "key-2", now_ms=_NOW + 1)
    assert store.get(first.row.vt_run_id) == first.row
    assert [row.vt_run_id for row in store.list_running()] == [
        first.row.vt_run_id,
        second.row.vt_run_id,
    ]

    store.mark_terminal(
        first.row.vt_run_id, "failed", error="boom", error_kind="timeout"
    )
    assert [row.vt_run_id for row in store.list_running()] == [second.row.vt_run_id]


@pytest.mark.parametrize(
    "override",
    [
        {"run_key": ""},
        {"run_key": None},
        {"strategy_id": ""},
        {"strategy_version": ""},
        {"timeout_s": 0},
        {"timeout_s": -1},
        {"timeout_s": True},
        {"timeout_s": 1.5},
        {"sandbox_id": 5},
        {"upstream": 5},
        {"now_ms": "1700000000000"},
    ],
)
def test_claim_rejects_invalid_input(tmp_path: Path, override: dict) -> None:
    db_path = tmp_path / "runs.sqlite3"
    store = StrategyRunStore(db_path)
    kwargs: dict = {
        "run_key": "key-1",
        "strategy_id": "strat-1",
        "strategy_version": "v1-abc",
        "sandbox_id": None,
        "upstream": None,
        "timeout_s": 60,
    }
    kwargs.update(override)

    with pytest.raises(ValueError):
        store.claim(**kwargs)

    assert _row_count(db_path) == 0


def test_rows_expose_the_documented_fields(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    result = _claim(store, "key-1", sandbox_id="sbx-9", upstream="rm-entry")

    assert isinstance(result, ClaimResult)
    assert isinstance(result.row, RunRow)
    row = store.get(result.row.vt_run_id)
    assert row is not None
    assert row.run_key == "key-1"
    assert row.strategy_id == "strat-1"
    assert row.strategy_version == "v1-abc"
    assert row.sandbox_id == "sbx-9"
    assert row.upstream == "rm-entry"
    assert row.timeout_s == 900
