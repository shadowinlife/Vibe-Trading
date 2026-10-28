"""Offline tests for the live ``state-generation`` handle discipline (A3).

The entry process may hold ``strategy_store.db`` open when an import replaces
the file. These tests drive the real singleton, so the assertion covers the
actual close-and-reopen path rather than a mocked comparison: the old handle
must reject queries and the next ``get_store()`` must read the imported inode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config.accessor import reset_env_config
from src.state_export.manifest import LAYOUT_VERSION
from src.state_export.service import export_full, import_full
from src.strategy_library.store import create_draft
from src.strategy_store import _shared
from src.strategy_store.sqlite_store import SqliteStrategyStore

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")


@pytest.fixture(autouse=True)
def _clean_singleton() -> None:
    """Start and end each case with no leaked process-wide connection."""
    _shared.reopen_store()
    yield
    _shared.reopen_store()


@pytest.fixture()
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every path-resolution seam at one throwaway runtime root."""
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
    monkeypatch.setenv("VIBE_TRADING_STRATEGY_STORE_DB_PATH", str(root / "strategy_store.db"))
    monkeypatch.setenv("VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH", str(root / "strategy_discovery.db"))
    reset_env_config()
    return root


def _db_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _DB_NAMES}


def _spec(name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "steps": [{"id": "s1", "action": "load prices", "tool": "get_market_data", "output": "prices"}],
    }


def _register(root: Path, store: SqliteStrategyStore, name: str) -> str:
    return create_draft(_spec(name), store=store, runtime_root=root).id


def test_import_after_an_open_store_forces_a_reopen_that_reads_the_new_inode(
    runtime_root: Path, tmp_path: Path
) -> None:
    first_store = _shared.get_store()
    assert Path(first_store.db_path) == runtime_root / "strategy_store.db"
    first_id = _register(runtime_root, first_store, "First Strategy")
    first_bundle = export_full(runtime_root, db_paths=_db_paths(runtime_root))

    source = tmp_path / "source"
    source.mkdir()
    source_store = SqliteStrategyStore(source / "strategy_store.db")
    try:
        second_id = _register(source, source_store, "Imported Strategy")
    finally:
        source_store.close()
    second_bundle = export_full(source, db_paths=_db_paths(source))

    result = import_full(
        second_bundle.body,
        LAYOUT_VERSION,
        runtime_root=runtime_root,
        db_paths=_db_paths(runtime_root),
    )

    assert result.reopened is True
    assert first_bundle.content_sha256 != second_bundle.content_sha256
    reopened = _shared.get_store()
    assert reopened is not first_store
    assert reopened.get_artifact(second_id) is not None
    assert reopened.get_artifact(first_id) is None
    with pytest.raises(sqlite3.ProgrammingError):
        first_store.get_artifact(first_id)


def test_an_unchanged_generation_keeps_the_same_instance(runtime_root: Path) -> None:  # noqa: ARG001
    first = _shared.get_store()
    assert _shared.get_store() is first
    assert _shared.get_store() is first


def test_generation_write_reopens_and_reopen_store_reports_existence(runtime_root: Path) -> None:  # noqa: ARG001
    store = _shared.get_store()

    _shared.write_state_generation("generation-one", runtime_root=runtime_root)
    assert _shared.read_state_generation(runtime_root) == "generation-one"

    reopened = _shared.get_store()
    assert reopened is not store
    assert _shared.get_store() is reopened
    with pytest.raises(sqlite3.ProgrammingError):
        store.list_artifacts()

    assert _shared.reopen_store() is True
    assert _shared.reopen_store() is False
