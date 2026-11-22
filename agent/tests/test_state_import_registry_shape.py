"""``vt-state-v1`` import acceptance for registry-only and mixed libraries.

The version-publish surface creates a ``strategy_store.db`` whose only table
is ``strategy_version_registrations``; no legacy ``artifacts`` row ever names
those strategy directories. These cases drive the real publisher, export and
import seams over throwaway roots so the installed library is validated in
the shape production actually exports, not only the legacy artifact shape.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.state_export._errors import ImportFailed
from src.state_export.manifest import LAYOUT_VERSION
from src.state_export.service import export_full, import_full
from src.strategy_library.store import create_draft, snapshot
from src.strategy_store import _shared
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.models import VersionState
from src.strategy_versions.publisher import (
    PublicationPayload,
    StrategyVersionPublisher,
)
from tests._strategy_version_support import make_key, make_source

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")
_SPEC_BYTES = b'{"kind":"code","name":"registry-shape"}\n'
_CONFIG_BYTES = b'{"source":"local"}\n'
_SOURCE_BYTES = b"def generate_signals(frame):\n    return frame\n"


@pytest.fixture(autouse=True)
def _isolate_store_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient store paths and a leaked process handle out of each test."""
    for name in (
        "VIBE_TRADING_HOME",
        "VIBE_TRADING_STRATEGY_STORE_DB_PATH",
        "VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    _shared.reopen_store()
    yield
    _shared.reopen_store()


def _db_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _DB_NAMES}


def _publish(root: Path, strategy_id: str, version: str = "v1"):
    """Publish one active version without creating any legacy artifact row."""
    key = make_key(strategy_id=strategy_id, version=version)
    registry = SqliteVersionRegistry(root / "strategy_store.db")
    payload = PublicationPayload(_SPEC_BYTES, _CONFIG_BYTES, _SOURCE_BYTES)
    return StrategyVersionPublisher(registry, root).publish(key, payload)


def _blob(root: Path) -> bytes:
    return export_full(root, db_paths=_db_paths(root)).body


def _import(blob: bytes, target: Path):
    return import_full(
        blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target)
    )


def _table_names(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _state_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".strategy-version-registry.lock"
    }


def _spec(name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _seed_legacy(root: Path, name: str) -> str:
    """Create one legacy strategy row, its code sources and one snapshot."""
    store = SqliteStrategyStore(root / "strategy_store.db")
    try:
        draft = create_draft(_spec(name), store=store, runtime_root=root)
        base = root / "strategies" / draft.id
        (base / "code").mkdir(parents=True, exist_ok=True)
        (base / "config.json").write_text('{"source": "local"}\n', encoding="utf-8")
        (base / "code" / "signal_engine.py").write_text(
            "def generate_signals(frame):\n    return frame\n", encoding="utf-8"
        )
        snapshot(draft.id, store=store, runtime_root=root)
    finally:
        store.close()
    return draft.id


def test_registry_only_package_imports_into_a_fresh_root(tmp_path: Path) -> None:
    # Given a library published through the version surface, with no artifacts table.
    source = tmp_path / "source"
    source.mkdir()
    published = _publish(source, "registry-only")
    assert "artifacts" not in _table_names(source / "strategy_store.db")

    # When the official export is imported into a fresh target.
    result = _import(_blob(source), tmp_path / "target")

    # Then the registry shape installs unchanged and enumerates one strategy.
    assert (result.strategies, result.versions) == (1, 1)
    assert result.layout_version == LAYOUT_VERSION
    target = tmp_path / "target"
    assert "artifacts" not in _table_names(target / "strategy_store.db")
    installed = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert installed.all_records() == (published,)


def test_registry_only_round_trip_is_byte_identical(tmp_path: Path) -> None:
    # Given a registry-only package imported into a fresh root.
    source = tmp_path / "source"
    source.mkdir()
    _publish(source, "roundtrip-shape")
    first = export_full(source, db_paths=_db_paths(source))
    target = tmp_path / "target"
    _import(first.body, target)

    # When the installed state is exported again.
    second = export_full(target, db_paths=_db_paths(target))

    # Then the content digest and the registry-only shape are preserved.
    assert second.content_sha256 == first.content_sha256
    assert "artifacts" not in _table_names(target / "strategy_store.db")


def test_multiple_versions_of_one_strategy_import(tmp_path: Path) -> None:
    # Given two active versions registered under a single strategy directory.
    source = tmp_path / "source"
    source.mkdir()
    _publish(source, "multi-version", version="v1")
    _publish(source, "multi-version", version="v2")

    # When the package is imported.
    result = _import(_blob(source), tmp_path / "target")

    # Then one directory carries two versions, not two strategies.
    assert (result.strategies, result.versions) == (1, 2)


def test_pending_registration_without_a_tree_imports(tmp_path: Path) -> None:
    # Given a pending registration that never published a version tree.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="pending-only")
    SqliteVersionRegistry(source / "strategy_store.db").register_pending(
        key, make_source(_SOURCE_BYTES)
    )

    # When the package is imported.
    result = _import(_blob(source), tmp_path / "target")

    # Then the pending identity survives without requiring a directory.
    assert (result.strategies, result.versions) == (0, 0)
    target = tmp_path / "target"
    installed = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert installed.get(key).state is VersionState.PENDING


def test_tombstoned_registry_only_library_round_trips(tmp_path: Path) -> None:
    # Given a published version that is tombstoned while its tree stays on disk.
    source = tmp_path / "source"
    source.mkdir()
    key = make_key(strategy_id="retired-shape")
    _publish(source, "retired-shape")
    registry = SqliteVersionRegistry(source / "strategy_store.db")
    tombstoned = StrategyVersionPublisher(registry, source).tombstone(key)

    # When the package is imported.
    result = _import(_blob(source), tmp_path / "target")

    # Then the tombstoned record still explains its retained directory.
    assert (result.strategies, result.versions) == (1, 1)
    target = tmp_path / "target"
    installed = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert installed.all_records() == (tombstoned,)


def test_mixed_legacy_and_registry_strategies_import(tmp_path: Path) -> None:
    # Given one legacy draft strategy and one registry-published strategy.
    source = tmp_path / "source"
    source.mkdir()
    legacy_id = _seed_legacy(source, "Legacy Draft")
    published = _publish(source, "published-sibling")

    # When the mixed package is imported.
    result = _import(_blob(source), tmp_path / "target")

    # Then every directory is explained by its own record kind.
    assert (result.strategies, result.versions) == (2, 2)
    target = tmp_path / "target"
    store = SqliteStrategyStore(target / "strategy_store.db")
    try:
        assert store.get_artifact(legacy_id) is not None
    finally:
        store.close()
    installed = SqliteVersionRegistry(target / "strategy_store.db", read_only=True)
    assert installed.get(published.key) == published


def test_orphan_artifact_row_is_rejected_and_the_target_is_untouched(
    tmp_path: Path,
) -> None:
    # Given a legacy package whose artifact rows name a directory that does not exist.
    source = tmp_path / "source"
    source.mkdir()
    _seed_legacy(source, "Real Strategy")
    connection = sqlite3.connect(source / "strategy_store.db")
    try:
        connection.execute(
            "INSERT INTO artifacts (id, type, name, universe, created_at, updated_at)"
            " VALUES ('ghost', 'strategy', 'Ghost', 'cn_a', 't', 't')"
        )
        connection.commit()
    finally:
        connection.close()
    target = tmp_path / "target"
    target.mkdir()
    _seed_legacy(target, "Existing Strategy")
    before = _state_snapshot(target)

    # When the contradictory package is imported.
    with pytest.raises(ImportFailed):
        _import(_blob(source), target)

    # Then the import fails closed and the target keeps its exact prior state.
    assert _state_snapshot(target) == before
    assert list(target.glob(".state-import-*")) == []


def test_active_registration_without_a_version_tree_is_rejected(tmp_path: Path) -> None:
    # Given an active registration whose version tree was removed before export.
    source = tmp_path / "source"
    source.mkdir()
    published = _publish(source, "broken-tree")
    version_dir = source / "strategies" / "broken-tree" / "versions" / "v1"
    for path in sorted(version_dir.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    version_dir.rmdir()
    target = tmp_path / "target"
    target.mkdir()
    _seed_legacy(target, "Existing Strategy")
    before = _state_snapshot(target)

    # When the package claiming an active version is imported.
    with pytest.raises(ImportFailed):
        _import(_blob(source), target)

    # Then the active identity cannot install without its verifiable tree.
    assert _state_snapshot(target) == before
    assert list(target.glob(".state-import-*")) == []
    assert published.state is VersionState.ACTIVE
