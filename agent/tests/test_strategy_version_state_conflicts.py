"""Official import refuses registry conflicts before it touches live state.

Each case seeds a real live runtime root, captures every file byte, then feeds
the official :func:`import_full` a package that contradicts the live registry or
its own lineage.  The import must fail through the existing ``ImportFailed``
contract with the live tree, database and registry byte-identical.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.state_export._errors import ImportFailed
from src.state_export.manifest import LAYOUT_VERSION
from src.state_export.service import export_full, import_full
from src.strategy_library import paths
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.models import RegistrationKey
from src.strategy_versions.projection import LINEAGE_FILENAME
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from src.strategy_versions.reader import StrategyVersionSourceReader
from tests._strategy_version_support import make_key

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")
SPEC_BYTES = b'{"kind":"code","name":"conflicts"}\n'
CONFIG_BYTES = b'{"source":"local"}\n'
SOURCE_BYTES = b"def generate_signals(frame):\n    return frame\n"


def _db_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _DB_NAMES}


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
    payload = PublicationPayload(SPEC_BYTES, CONFIG_BYTES, source)
    registry = SqliteVersionRegistry(root / "strategy_store.db")
    return StrategyVersionPublisher(registry, root).publish(key, payload)


def _blob(root: Path) -> bytes:
    return export_full(root, db_paths=_db_paths(root)).body


def _import(blob: bytes, target: Path) -> None:
    import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _write_rows(root: Path, records: tuple) -> None:
    SqliteVersionRegistry(root / "strategy_store.db").replace_all_for_import(records)


@pytest.mark.parametrize("mutation", ["version_id", "source"])
def test_changed_identity_or_source_facts_are_refused_before_install(tmp_path: Path, mutation: str) -> None:
    # Given a live active version and a package claiming the same key.
    live = tmp_path / "live"
    live.mkdir()
    key = make_key(strategy_id="guarded", version="v1")
    live_record = _publish(live, key)
    package = tmp_path / "package"
    package.mkdir()
    if mutation == "version_id":
        _publish(package, key)
    else:
        incoming = _publish(package, key, source=b"print('drifted')\n")
        _write_rows(package, (replace(incoming, version_id=live_record.version_id),))
    before = _snapshot(live)

    # When the contradictory package is imported.
    with pytest.raises(ImportFailed):
        _import(_blob(package), live)

    # Then the live state, registry and files are untouched.
    assert _snapshot(live) == before
    assert list(live.glob(".state-import-*")) == []
    installed = SqliteVersionRegistry(live / "strategy_store.db", read_only=True)
    assert installed.get(key) == live_record
    reader = StrategyVersionSourceReader(installed, live)
    assert reader.read(key).source_bytes == SOURCE_BYTES


def test_duplicate_version_id_across_keys_is_refused_before_install(
    tmp_path: Path,
) -> None:
    # Given a live identity reused by a different incoming key.
    live = tmp_path / "live"
    live.mkdir()
    live_key = make_key(strategy_id="live-strategy", version="v1")
    live_record = _publish(live, live_key)
    package = tmp_path / "package"
    package.mkdir()
    incoming_key = make_key(strategy_id="incoming-strategy", version="v1")
    incoming = _publish(package, incoming_key)
    _write_rows(package, (replace(incoming, version_id=live_record.version_id),))
    before = _snapshot(live)

    # When the package reuses one version_id under two keys.
    with pytest.raises(ImportFailed):
        _import(_blob(package), live)

    # Then the duplicate is refused without an install or a backup left behind.
    assert _snapshot(live) == before
    assert list(live.glob(".state-import-*")) == []
    assert SqliteVersionRegistry(live / "strategy_store.db", read_only=True).all_records() == (live_record,)


def test_registry_rows_behind_unreadable_sidecars_fail_closed(tmp_path: Path) -> None:
    # Given a live registry with rows and poisoned SQLite sidecars.
    live = tmp_path / "live"
    live.mkdir()
    _publish(live, make_key(strategy_id="sidecar", version="v1"))
    database = live / "strategy_store.db"
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").write_bytes(b"poisoned sidecar")
    empty = tmp_path / "empty"
    empty.mkdir()
    before = _snapshot(live)

    # When an authoritative legacy package is imported.
    with pytest.raises(ImportFailed):
        _import(_blob(empty), live)

    # Then the unreadable registry is not treated as empty and live is intact.
    assert _snapshot(live) == before


@pytest.mark.parametrize("damage", ["missing-lineage", "digest-drift"])
def test_damaged_active_projection_is_refused_before_install(tmp_path: Path, damage: str) -> None:
    # Given a live active version and a package with an unverifiable projection.
    live = tmp_path / "live"
    live.mkdir()
    _publish(live, make_key(strategy_id="guard", version="v1"))
    package = tmp_path / "package"
    package.mkdir()
    key = make_key(strategy_id="projected", version="v1")
    _publish(package, key)
    version_root = paths.version_dir(key.strategy_id, key.strategy_version, runtime_root=package)
    if damage == "missing-lineage":
        (version_root / LINEAGE_FILENAME).unlink()
    else:
        (version_root / "code" / "signal_engine.py").write_bytes(SOURCE_BYTES + b"# drift\n")
    before = _snapshot(live)

    # When the active row cannot prove its staged bytes.
    with pytest.raises(ImportFailed):
        _import(_blob(package), live)

    # Then nothing live was replaced.
    assert _snapshot(live) == before
    assert list(live.glob(".state-import-*")) == []
