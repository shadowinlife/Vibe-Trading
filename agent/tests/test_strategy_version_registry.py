"""Persistent owner-bound version registrations and legacy-store isolation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionTombstonedError,
)
from src.strategy_versions.models import VersionState
from tests._strategy_version_support import make_key, make_source


def _schema_and_user_version(db_path: Path) -> tuple[list[tuple[str, str]], int]:
    with sqlite3.connect(db_path) as connection:
        schema = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    return schema, user_version


def test_random_id_is_persisted_and_independent_of_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given two identical source payloads and a deterministic random source.
    registry = SqliteVersionRegistry(tmp_path / "strategy_store.db")
    tokens = iter(("01" * 16, "02" * 16))

    def token_hex(size: int) -> str:
        assert size == 16
        return next(tokens)

    monkeypatch.setattr("src.strategy_versions.models.secrets.token_hex", token_hex)

    # When one key is registered twice and another key has the same bytes.
    first = registry.register_pending(make_key(), make_source())
    resumed = registry.register_pending(make_key(), make_source())
    other = registry.register_pending(make_key(version="v2"), make_source())

    # Then identity is random, stable for the key, and never content-derived.
    assert first.version_id == "ver_" + "01" * 16
    assert resumed == first
    assert other.version_id == "ver_" + "02" * 16
    assert other.source == first.source


def test_random_collision_is_retried_for_a_different_registry_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a generator whose first two values collide.
    registry = SqliteVersionRegistry(tmp_path / "strategy_store.db")
    tokens = iter(("03" * 16, "03" * 16, "04" * 16))
    monkeypatch.setattr("src.strategy_versions.models.secrets.token_hex", lambda _: next(tokens))
    first = registry.register_pending(make_key(), make_source())

    # When another key needs an identity.
    second = registry.register_pending(make_key(version="v2"), make_source())

    # Then the persisted version IDs remain globally unique.
    assert first.version_id == "ver_" + "03" * 16
    assert second.version_id == "ver_" + "04" * 16


def test_changed_digest_cannot_resume_a_pending_identity(tmp_path: Path) -> None:
    # Given a pending registration.
    registry = SqliteVersionRegistry(tmp_path / "strategy_store.db")
    original = registry.register_pending(make_key(), make_source(b"one"))

    # When the same key is retried with different bytes.
    with pytest.raises(VersionConflictError):
        registry.register_pending(make_key(), make_source(b"two"))

    # Then the original identity and digest remain authoritative.
    assert registry.get(make_key()) == original


def test_missing_database_read_is_empty_and_creates_nothing(tmp_path: Path) -> None:
    # Given a path whose parent and database do not exist.
    db_path = tmp_path / "missing" / "strategy_store.db"
    registry = SqliteVersionRegistry(db_path, read_only=True)

    # When a registration is read.
    result = registry.get(make_key())

    # Then legacy absence is empty and the read has no filesystem side effects.
    assert result is None
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_missing_registry_table_reads_empty_without_ddl(tmp_path: Path) -> None:
    # Given a legacy strategy store without the independent registry table.
    db_path = tmp_path / "strategy_store.db"
    SqliteStrategyStore(db_path).close()
    before = _schema_and_user_version(db_path)
    files_before = {path.name for path in tmp_path.iterdir()}
    registry = SqliteVersionRegistry(db_path, read_only=True)

    # When the registry is read.
    result = registry.get(make_key())

    # Then no table, WAL sibling, or schema version is created or changed.
    assert result is None
    assert _schema_and_user_version(db_path) == before
    assert {path.name for path in tmp_path.iterdir()} == files_before


def test_writer_preserves_legacy_artifacts_and_user_version(tmp_path: Path) -> None:
    # Given an initialized legacy store.
    db_path = tmp_path / "strategy_store.db"
    SqliteStrategyStore(db_path).close()
    schema_before, version_before = _schema_and_user_version(db_path)
    registry = SqliteVersionRegistry(db_path)

    # When the independent registry initializes and writes.
    registration = registry.register_pending(make_key(), make_source())

    # Then only its own table is added; legacy DDL and user_version are intact.
    schema_after, version_after = _schema_and_user_version(db_path)
    assert registration.state is VersionState.PENDING
    assert version_after == version_before
    assert [item for item in schema_after if item[0] != "strategy_version_registrations"] == schema_before
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_identity_state_and_tombstone_survive_restarts(tmp_path: Path) -> None:
    # Given a pending registration that is activated and tombstoned.
    db_path = tmp_path / "strategy_store.db"
    registry = SqliteVersionRegistry(db_path)
    pending = registry.register_pending(make_key(), make_source())

    # When state transitions are observed through fresh adapter instances.
    active = registry.activate(make_key())
    active_after_restart = SqliteVersionRegistry(db_path, read_only=True).get(make_key())
    tombstoned = registry.tombstone(make_key())
    tombstoned_after_restart = SqliteVersionRegistry(db_path, read_only=True).get(make_key())

    # Then identity is stable and tombstone is an idempotent terminal state.
    assert active.version_id == pending.version_id
    assert active.state is VersionState.ACTIVE
    assert active_after_restart == active
    assert tombstoned.state is VersionState.TOMBSTONED
    assert tombstoned.source == pending.source
    assert tombstoned_after_restart == tombstoned
    assert registry.tombstone(make_key()) == tombstoned
    with pytest.raises(VersionTombstonedError):
        registry.register_pending(make_key(), make_source())
    with pytest.raises(VersionTombstonedError):
        registry.activate(make_key())


def test_has_strategy_is_owner_exact_and_covers_every_state(tmp_path: Path) -> None:
    # Given a key whose identity passes through every lifecycle state.
    registry = SqliteVersionRegistry(tmp_path / "strategy_store.db")
    key = make_key()
    assert registry.has_strategy(key.owner_user_id, key.strategy_id) is False

    # When pending, active and tombstoned identities are observed.
    registry.register_pending(key, make_source())
    pending = registry.has_strategy(key.owner_user_id, key.strategy_id)
    registry.activate(key)
    active = registry.has_strategy(key.owner_user_id, key.strategy_id)
    registry.tombstone(key)
    tombstoned = registry.has_strategy(key.owner_user_id, key.strategy_id)

    # Then any persisted state is visible only under the exact owner and
    # strategy.
    assert (pending, active, tombstoned) == (True, True, True)
    assert registry.has_strategy("other-owner", key.strategy_id) is False
    assert registry.has_strategy(key.owner_user_id, "other-strategy") is False


def test_has_strategy_missing_database_creates_nothing(tmp_path: Path) -> None:
    # Given a path whose parent and database do not exist.
    db_path = tmp_path / "missing" / "strategy_store.db"
    registry = SqliteVersionRegistry(db_path, read_only=True)

    # When strategy visibility is checked.
    result = registry.has_strategy("owner-a", "strategy-a")

    # Then absence is false and the read has no filesystem side effects.
    assert result is False
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_has_strategy_missing_registry_table_creates_nothing(tmp_path: Path) -> None:
    # Given a legacy strategy store without the independent registry table.
    db_path = tmp_path / "strategy_store.db"
    SqliteStrategyStore(db_path).close()
    before = _schema_and_user_version(db_path)
    files_before = {path.name for path in tmp_path.iterdir()}
    registry = SqliteVersionRegistry(db_path, read_only=True)

    # When strategy visibility is checked.
    result = registry.has_strategy("owner-a", "strategy-a")

    # Then the legacy database stays empty and no WAL or table appears.
    assert result is False
    assert _schema_and_user_version(db_path) == before
    assert {path.name for path in tmp_path.iterdir()} == files_before
