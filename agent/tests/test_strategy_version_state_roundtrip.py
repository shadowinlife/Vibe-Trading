"""Official ``vt-state-v1`` round trips for durable strategy-version state.

Every case drives the real :func:`export_full` / :func:`import_full` seam over
throwaway runtime roots.  The registry and the F9 lineage tree must survive as
one state: exact identity, bytes, digests and terminal tombstones move with the
package, and a staged registry is checkpointed into the main file the official
installer replaces.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from src.state_export._version_registry import merge_registry_for_import
from src.state_export.manifest import LAYOUT_VERSION
from src.state_export.service import export_full, import_full
from src.strategy_library import paths
from src.strategy_library.store import create_draft, snapshot
from src.strategy_store import _shared
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import StrategyNotFoundError, VersionNotFoundError
from src.strategy_versions.models import RegistrationKey, VersionState
from src.strategy_versions.projection import LINEAGE_FILENAME
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from src.strategy_versions.reader import StrategyVersionSourceReader
from tests._strategy_version_support import make_key, make_source

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")
SPEC_BYTES = '{"kind":"code","name":"状态往返"}\n'.encode("utf-8")
CONFIG_BYTES = '{"source":"local","note":"中文"}\r\n'.encode("utf-8")
SOURCE_BYTES = ("\ufeff# 中文注释\r\ndef generate_signals(frame):\n    return frame\r\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _isolate_store_singleton() -> None:
    """Never leak a process-wide store handle across cases."""
    _shared.reopen_store()
    yield
    _shared.reopen_store()


def _db_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _DB_NAMES}


def _payload(source: bytes = SOURCE_BYTES) -> PublicationPayload:
    return PublicationPayload(SPEC_BYTES, CONFIG_BYTES, source)


def _seed_artifact(root: Path, strategy_id: str) -> None:
    """Create the legacy strategy row whose tree owns *strategy_id*."""
    SqliteStrategyStore(root / "strategy_store.db").close()
    connection = sqlite3.connect(root / "strategy_store.db")
    try:
        connection.execute(
            "INSERT INTO artifacts (id, type, name, universe, created_at, updated_at)"
            " VALUES (?, 'strategy', ?, 'cn_a', 't', 't')",
            (strategy_id, strategy_id),
        )
        connection.commit()
    finally:
        connection.close()


def _publish(root: Path, key: RegistrationKey, source: bytes = SOURCE_BYTES):
    """Publish one active version into *root* through the real publisher."""
    _seed_artifact(root, key.strategy_id)
    registry = SqliteVersionRegistry(root / "strategy_store.db")
    return StrategyVersionPublisher(registry, root).publish(key, _payload(source))


def _blob(root: Path) -> bytes:
    return export_full(root, db_paths=_db_paths(root)).body


def _import(blob: bytes, target: Path):
    return import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))


def _reader(root: Path) -> StrategyVersionSourceReader:
    registry = SqliteVersionRegistry(root / "strategy_store.db", read_only=True)
    return StrategyVersionSourceReader(registry, root)


def _version_root(root: Path, key: RegistrationKey) -> Path:
    return paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=root)


def _journal_mode(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()


def test_active_version_round_trip_keeps_identity_bytes_and_lineage(
    tmp_path: Path,
) -> None:
    # Given an active version published in the source root.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="roundtrip", version="v2")
    published = _publish(source, key)

    # When the official export is imported into a fresh target.
    result = _import(_blob(source), tmp_path / "target")

    # Then identity, exact bytes and lineage survive unchanged.
    target = tmp_path / "target"
    assert (result.strategies, result.versions) == (1, 1)
    installed = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert installed.get(key) == published
    read = _reader(target).read(key)
    assert read.registration == published
    assert read.registration.key.owner_user_id == "owner-a"
    assert read.source_bytes == SOURCE_BYTES
    assert read.source_sha256 == hashlib.sha256(SOURCE_BYTES).hexdigest()
    assert read.source_sha256 == published.source.sha256
    assert read.source_byte_length == len(SOURCE_BYTES)
    lineage = (LINEAGE_FILENAME,)
    assert (_version_root(target, key).joinpath(*lineage).read_bytes()) == (
        _version_root(source, key).joinpath(*lineage).read_bytes()
    )


def test_installed_registry_reads_immediately_without_a_wal_sidecar(
    tmp_path: Path,
) -> None:
    # Given an active version that survives the official import.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="sidecar-free")
    published = _publish(source, key)
    target = tmp_path / "target"
    _import(_blob(source), target)

    # Then the installed main file carries the rows without any sidecar.
    database = target / "strategy_store.db"
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    assert SqliteVersionRegistry(database, read_only=True).all_records() == (published,)


def test_tombstone_round_trip_survives_restart_and_reimport(tmp_path: Path) -> None:
    # Given a published version that is then tombstoned in the source.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="retired", version="v3")
    _publish(source, key)
    tombstoned = StrategyVersionPublisher(SqliteVersionRegistry(source / "strategy_store.db"), source).tombstone(key)

    # When the state is imported, re-exported and imported once more.
    first = tmp_path / "first"
    _import(_blob(source), first)
    second = tmp_path / "second"
    _import(_blob(first), second)

    # Then the terminal tombstone keeps its original identity and stays unreadable.
    assert SqliteVersionRegistry(second / "strategy_store.db", read_only=True).all_records() == (tombstoned,)
    assert tombstoned.state is VersionState.TOMBSTONED
    with pytest.raises(VersionNotFoundError):
        _reader(second).read(key)


def test_omitted_live_active_and_pending_become_unreadable_tombstones(
    tmp_path: Path,
) -> None:
    # Given a live active version and a live pending registration.
    live = tmp_path / "live"
    live.mkdir()
    active_key = make_key(strategy_id="active-one")
    published = _publish(live, active_key)
    pending_key = make_key(strategy_id="pending-one")
    pending = SqliteVersionRegistry(live / "strategy_store.db").register_pending(
        pending_key, make_source(b"print('pending')\n")
    )
    empty = tmp_path / "empty"
    empty.mkdir()

    # When an authoritative empty package omits both keys.
    result = _import(export_full(empty, db_paths={}).body, live)

    # Then both become tombstones with their identity, never active or readable.
    assert (result.strategies, result.versions) == (0, 0)
    installed = SqliteVersionRegistry(live / "strategy_store.db", read_only=True)
    assert installed.get(active_key).state is VersionState.TOMBSTONED
    assert installed.get(active_key).version_id == published.version_id
    assert installed.get(pending_key).state is VersionState.TOMBSTONED
    assert installed.get(pending_key).version_id == pending.version_id
    connection = sqlite3.connect(live / "strategy_store.db")
    try:
        artifacts = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    finally:
        connection.close()
    assert artifacts == 0
    reader = _reader(live)
    with pytest.raises(VersionNotFoundError):
        reader.read(active_key)
    with pytest.raises(VersionNotFoundError):
        reader.read(pending_key)


def test_older_package_cannot_revive_a_tombstoned_key(tmp_path: Path) -> None:
    # Given an old package captured before a tombstone and a retired live target.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="resurrect-me", version="v1")
    published = _publish(source, key)
    old_blob = _blob(source)
    publisher = StrategyVersionPublisher(SqliteVersionRegistry(source / "strategy_store.db"), source)
    publisher.tombstone(key)
    retired_blob = _blob(source)
    target = tmp_path / "target"
    _import(retired_blob, target)

    # When the older package still claiming active is imported.
    _import(old_blob, target)

    # Then the live tombstone wins and the old bytes never become readable.
    row = SqliteVersionRegistry(target / "strategy_store.db", read_only=True).get(key)
    assert row.state is VersionState.TOMBSTONED
    assert row.version_id == published.version_id
    with pytest.raises(VersionNotFoundError):
        _reader(target).read(key)


def test_foreign_owner_rows_are_preserved_but_unreadable(tmp_path: Path) -> None:
    # Given a package with active rows for two distinct owners.
    source = tmp_path / "source"
    source.mkdir()
    own_key = make_key(owner="owner-a", strategy_id="mine", version="v1")
    foreign_key = make_key(owner="owner-b", strategy_id="foreign", version="v9")
    own = _publish(source, own_key)
    foreign = _publish(source, foreign_key, source=b"print('foreign')\n")
    target = tmp_path / "target"

    # When the shared package is installed.
    _import(_blob(source), target)

    # Then both rows survive, but the bound owner cannot read the other owner.
    records = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert records.get(own_key) == own
    assert records.get(foreign_key) == foreign
    reader = _reader(target)
    assert reader.read(own_key).source_bytes == SOURCE_BYTES
    with pytest.raises(StrategyNotFoundError):
        reader.read(make_key(owner="owner-a", strategy_id="foreign", version="v9"))
    with pytest.raises(StrategyNotFoundError):
        reader.read(make_key(owner="owner-a", strategy_id="never-existed", version="v1"))


def test_staged_registry_is_checkpointed_and_installs_from_the_main_file_only(
    tmp_path: Path,
) -> None:
    # Given a staged store whose registry rows sit in WAL before installation.
    live = tmp_path / "live"
    live.mkdir()
    key = make_key(strategy_id="staged")
    published = _publish(live, key)
    data_root = tmp_path / "staging" / "data"
    data_root.mkdir(parents=True)
    shutil.copytree(live / "strategies", data_root / "strategies")
    staged_db = data_root / "strategy_store.db"
    SqliteStrategyStore(staged_db).close()
    SqliteVersionRegistry(staged_db).replace_all_for_import((published,))

    # When the registry merge finishes staging.
    result = merge_registry_for_import(live / "strategy_store.db", staged_db, data_root)

    # Then the staged main file alone holds every row, with no sidecar.
    assert result.requires_strategy_store is True
    assert not Path(f"{staged_db}-wal").exists()
    assert not Path(f"{staged_db}-shm").exists()
    assert _journal_mode(staged_db) == "delete"
    assert SqliteVersionRegistry(staged_db, read_only=True).all_records() == (published,)
    installed = tmp_path / "installed" / "strategy_store.db"
    installed.parent.mkdir()
    shutil.copy2(staged_db, installed)
    assert SqliteVersionRegistry(installed, read_only=True).all_records() == (published,)


def _spec(name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _seed_legacy(root: Path, name: str = "Legacy Strategy") -> tuple[str, str]:
    """Create one legacy strategy row and one immutable version snapshot."""
    store = SqliteStrategyStore(root / "strategy_store.db")
    try:
        draft = create_draft(_spec(name), store=store, runtime_root=root)
        base = root / "strategies" / draft.id
        (base / "code").mkdir(parents=True, exist_ok=True)
        (base / "config.json").write_text('{"source": "local"}\n', encoding="utf-8")
        (base / "code" / "signal_engine.py").write_text(
            "def generate_signals(frame):\n    return frame\n", encoding="utf-8"
        )
        version = snapshot(draft.id, store=store, runtime_root=root)
    finally:
        store.close()
    return draft.id, version


def test_legacy_package_imports_with_an_empty_registry(tmp_path: Path) -> None:
    # Given a legacy package with no version registry table.
    source = tmp_path / "source"
    source.mkdir()
    strategy_id, _version = _seed_legacy(source)

    # When it is imported into a fresh target.
    result = _import(_blob(source), tmp_path / "target")

    # Then the legacy rows import unchanged and no readable identity appears.
    assert result.strategies == 1
    target = tmp_path / "target"
    assert SqliteVersionRegistry(target / "strategy_store.db", read_only=True).all_records() == ()
    store = SqliteStrategyStore(target / "strategy_store.db")
    try:
        assert store.get_artifact(strategy_id) is not None
    finally:
        store.close()


def test_empty_package_keeps_the_existing_authoritative_deletion(tmp_path: Path) -> None:
    # Given a legacy live target and an empty package with no database entries.
    target = tmp_path / "target"
    target.mkdir()
    old_id, _version = _seed_legacy(target, "Old Strategy")
    empty = tmp_path / "empty"
    empty.mkdir()

    # When the empty package is imported.
    result = _import(export_full(empty, db_paths={}).body, target)

    # Then the declared database is deleted and no old identity survives.
    assert (result.strategies, result.versions) == (0, 0)
    assert not (target / "strategy_store.db").exists()
    assert list((target / "strategies").iterdir()) == []
    assert not (target / "strategies" / old_id).exists()
