"""State-export merge for the durable strategy-version registry."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from src.state_export._version_registry import (
    RegistryMergeConflict,
    RegistryMergeResult,
    merge_registry_for_import,
)
from src.strategy_library import paths
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionRegistryReadOnlyError,
)
from src.strategy_versions.models import (
    RegistrationKey,
    VersionRegistration,
    VersionState,
)
from src.strategy_versions.projection import LINEAGE_FILENAME
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from tests._strategy_version_support import (
    make_key,
    make_record,
    make_source,
    make_version_id,
    record_sort_key,
)

SPEC_BYTES = '{"kind":"code","name":"state merge"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local"}\n'.encode("utf-8")
SOURCE_BYTES = b"def generate_signals(frame):\n    return frame\n"


def _write(db_path: Path, records: tuple[VersionRegistration, ...]) -> None:
    SqliteVersionRegistry(db_path).replace_all_for_import(records)


def _read(db_path: Path) -> tuple[VersionRegistration, ...]:
    return SqliteVersionRegistry(db_path, read_only=True).all_records()


def _staging(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "staging"
    root.mkdir()
    return root, root / "strategy_store.db"


def _publish(staged_root: Path, scratch: Path, key: RegistrationKey) -> VersionRegistration:
    builder = SqliteVersionRegistry(scratch / f"builder-{uuid.uuid4().hex}.db")
    publisher = StrategyVersionPublisher(builder, staged_root)
    return publisher.publish(key, PublicationPayload(SPEC_BYTES, CONFIG_BYTES, SOURCE_BYTES))


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}


def _user_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        return connection.execute("PRAGMA user_version").fetchone()[0]


def _artifact_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]


def test_merge_round_trip_preserves_identity_state_and_facts(tmp_path: Path) -> None:
    # Given a staged package carrying active, pending and tombstoned rows.
    root, staged_db = _staging(tmp_path)
    active = _publish(root, tmp_path, make_key(strategy_id="momentum", version="v2"))
    records = (
        active,
        make_record(strategy_id="mean-reversion", state=VersionState.PENDING),
        make_record(strategy_id="retired", version="v9", state=VersionState.TOMBSTONED),
    )
    _write(staged_db, records)

    # When the package is merged into an absent live side.
    result = merge_registry_for_import(None, staged_db, root)

    # Then every identity, fact and state survives verbatim.
    assert result == RegistryMergeResult(
        requires_strategy_store=True, active_count=1, tombstone_count=1, pending_count=1
    )
    assert _read(staged_db) == tuple(sorted(records, key=record_sort_key))


def test_omitted_live_pending_and_active_become_tombstones_without_files(
    tmp_path: Path,
) -> None:
    # Given live pending and active rows whose strategy files are absent.
    live_db = tmp_path / "live" / "strategy_store.db"
    pending = make_record(strategy_id="pending-one")
    active = make_record(strategy_id="active-one", version="v2", state=VersionState.ACTIVE)
    _write(live_db, (pending, active))
    root, staged_db = _staging(tmp_path)

    # When an authoritative package omits both keys and ships no database.
    result = merge_registry_for_import(live_db, staged_db, root)

    # Then both become tombstones with their original identity, no files needed.
    expected = (
        replace(active, state=VersionState.TOMBSTONED),
        replace(pending, state=VersionState.TOMBSTONED),
    )
    assert result.active_count == 0
    assert result.tombstone_count == 2
    assert _read(staged_db) == tuple(sorted(expected, key=record_sort_key))
    assert _artifact_count(staged_db) == 0


def test_incoming_tombstone_wins_over_live_active(tmp_path: Path) -> None:
    # Given a live active row that the package retires.
    live_db = tmp_path / "live" / "strategy_store.db"
    active = make_record(state=VersionState.ACTIVE)
    _write(live_db, (active,))
    root, staged_db = _staging(tmp_path)
    incoming = replace(active, state=VersionState.TOMBSTONED)
    _write(staged_db, (incoming,))

    # When the tombstone is merged.
    result = merge_registry_for_import(live_db, staged_db, root)

    # Then the tombstone is terminal and the live active state cannot win.
    assert _read(staged_db) == (incoming,)
    assert (result.active_count, result.tombstone_count) == (0, 1)


def test_incoming_active_cannot_revive_live_tombstone(tmp_path: Path) -> None:
    # Given a live tombstone and an older package that still says active.
    live_db = tmp_path / "live" / "strategy_store.db"
    tombstone = make_record(state=VersionState.TOMBSTONED)
    _write(live_db, (tombstone,))
    root, staged_db = _staging(tmp_path)
    _write(staged_db, (replace(tombstone, state=VersionState.ACTIVE),))

    # When the older package is merged.
    result = merge_registry_for_import(live_db, staged_db, root)

    # Then the tombstone stays terminal with its original identity.
    assert _read(staged_db) == (tombstone,)
    assert result.tombstone_count == 1
    assert result.active_count == 0


@pytest.mark.parametrize("mutation", ["version_id", "source"])
def test_changed_identity_rejects_before_staged_mutation(tmp_path: Path, mutation: str) -> None:
    # Given a live pending row and a package that rewrites its identity.
    live_db = tmp_path / "live" / "strategy_store.db"
    live = make_record()
    _write(live_db, (live,))
    incoming = (
        replace(live, version_id=make_version_id("different"))
        if mutation == "version_id"
        else replace(live, source=make_source(SOURCE_BYTES + b"# drift\n"))
    )
    root, staged_db = _staging(tmp_path)
    _write(staged_db, (incoming,))
    before = staged_db.read_bytes()

    # When the conflicting package is merged.
    with pytest.raises(RegistryMergeConflict):
        merge_registry_for_import(live_db, staged_db, root)

    # Then nothing in the staged database was rewritten.
    assert staged_db.read_bytes() == before
    assert _read(staged_db) == (incoming,)


def test_duplicate_version_id_across_keys_rejects(tmp_path: Path) -> None:
    # Given one version_id shared by a live key and a different incoming key.
    shared = make_version_id("shared")
    live_db = tmp_path / "live" / "strategy_store.db"
    _write(live_db, (make_record(strategy_id="live-strategy", version_id=shared),))
    root, staged_db = _staging(tmp_path)
    _write(staged_db, (make_record(strategy_id="incoming-strategy", version_id=shared),))
    before = staged_db.read_bytes()

    # When the package is merged.
    with pytest.raises(RegistryMergeConflict):
        merge_registry_for_import(live_db, staged_db, root)

    # Then the duplicate is rejected before any merged row is written.
    assert staged_db.read_bytes() == before


def test_other_owner_active_rows_are_preserved_unchanged(tmp_path: Path) -> None:
    # Given an incoming package with rows from two owners and valid projections.
    root, staged_db = _staging(tmp_path)
    foreign = _publish(root, tmp_path, make_key(owner="owner-b", strategy_id="foreign", version="v9"))
    own = _publish(root, tmp_path, make_key(owner="owner-a", strategy_id="mine", version="v1"))
    _write(staged_db, (foreign, own))

    # When the package is merged.
    result = merge_registry_for_import(None, staged_db, root)

    # Then the other-owner row is preserved exactly and remains active.
    merged = _read(staged_db)
    assert merged == tuple(sorted((foreign, own), key=record_sort_key))
    assert result.active_count == 2


@pytest.mark.parametrize("damage", ["missing-lineage", "digest-drift", "missing-tree"])
def test_incoming_active_requires_a_valid_staged_projection(tmp_path: Path, damage: str) -> None:
    # Given an incoming active row whose staged F9 tree is damaged.
    root, staged_db = _staging(tmp_path)
    key = make_key(strategy_id="projected", version="v1")
    active = _publish(root, tmp_path, key)
    tree = paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=root)
    if damage == "missing-lineage":
        (tree / LINEAGE_FILENAME).unlink()
    elif damage == "digest-drift":
        (tree / "code/signal_engine.py").write_bytes(SOURCE_BYTES + b"# drift\n")
    else:
        shutil.rmtree(tree)
    _write(staged_db, (active,))
    before = staged_db.read_bytes()

    # When the package is merged.
    with pytest.raises(RegistryMergeConflict):
        merge_registry_for_import(None, staged_db, root)

    # Then the package is rejected without touching the staged registry.
    assert staged_db.read_bytes() == before
    assert _read(staged_db) == (active,)


def test_omitted_staged_db_recreates_registry_with_live_tombstones(
    tmp_path: Path,
) -> None:
    # Given a live tombstone and a package that omitted strategy_store.db.
    live_db = tmp_path / "live" / "strategy_store.db"
    tombstone = make_record(strategy_id="retired", version="v3", state=VersionState.TOMBSTONED)
    _write(live_db, (tombstone,))
    root, staged_db = _staging(tmp_path)

    # When the package is merged.
    result = merge_registry_for_import(live_db, staged_db, root)

    # Then a fresh legacy store schema is created with only the tombstone row.
    reference = tmp_path / "reference.db"
    SqliteStrategyStore(reference).close()
    assert staged_db.is_file()
    assert _user_version(staged_db) == _user_version(reference)
    assert _read(staged_db) == (tombstone,)
    assert _artifact_count(staged_db) == 0
    assert result == RegistryMergeResult(
        requires_strategy_store=True,
        active_count=0,
        tombstone_count=1,
        pending_count=0,
    )


def test_empty_merge_leaves_the_staged_package_untouched(tmp_path: Path) -> None:
    # Given no live database, no staged database and an empty staging root.
    root, staged_db = _staging(tmp_path)
    before = {path.name for path in root.iterdir()}

    # When both sides read as empty.
    result = merge_registry_for_import(None, staged_db, root)

    # Then no database appears and the package is not marked required.
    assert result == RegistryMergeResult(False, 0, 0, 0)
    assert not staged_db.exists()
    assert {path.name for path in root.iterdir()} == before

    # And a legacy package database without a registry table stays untouched.
    SqliteStrategyStore(staged_db).close()
    before_bytes = staged_db.read_bytes()
    before_tables = _table_names(staged_db)
    result = merge_registry_for_import(None, staged_db, root)
    assert result.requires_strategy_store is False
    assert staged_db.read_bytes() == before_bytes
    assert _table_names(staged_db) == before_tables


def test_all_records_missing_storage_is_empty_and_side_effect_free(
    tmp_path: Path,
) -> None:
    # Given a database path whose parent does not exist.
    missing = tmp_path / "missing" / "strategy_store.db"
    assert SqliteVersionRegistry(missing, read_only=True).all_records() == ()
    assert not missing.parent.exists()

    # And a legacy store without the independent registry table.
    db_path = tmp_path / "strategy_store.db"
    SqliteStrategyStore(db_path).close()
    tables_before = _table_names(db_path)
    version_before = _user_version(db_path)
    files_before = {path.name for path in tmp_path.iterdir()}
    assert SqliteVersionRegistry(db_path, read_only=True).all_records() == ()
    assert _table_names(db_path) == tables_before
    assert _user_version(db_path) == version_before
    assert {path.name for path in tmp_path.iterdir()} == files_before


def test_replace_all_persists_exact_rows_and_preserves_the_legacy_store(
    tmp_path: Path,
) -> None:
    # Given an initialized legacy store with one artifact row.
    db_path = tmp_path / "strategy_store.db"
    SqliteStrategyStore(db_path).close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO artifacts (id, type, name, universe, created_at, updated_at)"
            " VALUES ('art_1', 'strategy', 'kept', 'a-share', 't', 't')"
        )
        connection.commit()
        version_before = connection.execute("PRAGMA user_version").fetchone()[0]
    tables_before = _table_names(db_path)
    records = (
        make_record(strategy_id="pending-one"),
        make_record(strategy_id="active-one", version="v2", state=VersionState.ACTIVE),
        make_record(strategy_id="retired", version="v3", state=VersionState.TOMBSTONED),
    )

    # When the exact rows are persisted for import.
    SqliteVersionRegistry(db_path).replace_all_for_import(records)

    # Then only the registry table changes; artifacts and user_version survive.
    assert _read(db_path) == tuple(sorted(records, key=record_sort_key))
    assert _artifact_count(db_path) == 1
    assert _user_version(db_path) == version_before
    assert _table_names(db_path) == tables_before | {"strategy_version_registrations"}


def test_replace_all_rejects_invalid_batches_before_deleting_existing_rows(
    tmp_path: Path,
) -> None:
    # Given one persisted authoritative row.
    db_path = tmp_path / "strategy_store.db"
    original = make_record(state=VersionState.ACTIVE)
    registry = SqliteVersionRegistry(db_path)
    registry.replace_all_for_import((original,))
    duplicate_key = (
        make_record(strategy_id="dup", version="v1", version_id=make_version_id("a")),
        make_record(strategy_id="dup", version="v1", version_id=make_version_id("b")),
    )
    duplicate_version = (
        make_record(strategy_id="one", version_id=make_version_id("shared")),
        make_record(strategy_id="two", version_id=make_version_id("shared")),
    )
    invalid = (
        VersionRegistration(
            key=RegistrationKey("", "strategy", "v1"),
            version_id=make_version_id("invalid"),
            source=make_source(),
            state=VersionState.PENDING,
        ),
    )

    # When invalid batches are persisted.
    for batch in (duplicate_key, duplicate_version, invalid):
        with pytest.raises(VersionConflictError):
            registry.replace_all_for_import(batch)
        assert registry.all_records() == (original,)

    # Then a read-only adapter refuses the write without changing the rows.
    with pytest.raises(VersionRegistryReadOnlyError):
        SqliteVersionRegistry(db_path, read_only=True).replace_all_for_import((make_record(strategy_id="new"),))
    assert registry.all_records() == (original,)
