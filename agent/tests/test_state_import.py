"""Offline contract tests for ``vt-state-v1`` import, rollback and authority.

A target root in every test is seeded with its own strategy and database, so
each failure assertion can prove the target was left byte-identical rather than
merely "still openable". No test reads the developer's runtime root: store
database paths are passed explicitly and the process-wide store singleton is
reset around each case.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from src.state_export import _install as install_module
from src.state_export import service as service_module
from src.state_export._errors import (
    BlobCorrupt,
    ImportFailed,
    ImportInProgress,
)
from src.state_export.manifest import (
    LAYOUT_VERSION,
    LayoutUnsupported,
    ManifestInvalid,
    PathEscape,
)
from src.state_export.service import export_full, import_full
from src.strategy_library.store import create_draft, snapshot
from src.strategy_store import _shared
from src.strategy_store.models import ArtifactType
from src.strategy_store.sqlite_store import SqliteStrategyStore

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")


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


def _spec(name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _seed_strategy(root: Path, name: str) -> tuple[str, str]:
    """Create one strategy row, its code sources and one version snapshot."""
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


def _tree_bytes(root: Path) -> dict[str, bytes]:
    strategies = root / "strategies"
    if not strategies.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(strategies.rglob("*"))
        if path.is_file()
    }


def _state_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _tar_items(blob: bytes) -> list[tuple[str, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        return [(member.name, archive.extractfile(member).read()) for member in archive.getmembers()]


def _pack(items: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gzip_handle:
        with tarfile.open(fileobj=gzip_handle, mode="w") as archive:
            for name, data in items:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _replace_member(blob: bytes, name: str, data: bytes) -> bytes:
    return _pack([(member, data if member == name else member_data) for member, member_data in _tar_items(blob)])


def _edit_manifest(blob: bytes, edit) -> bytes:  # noqa: ANN001 — test-local mutator
    items = _tar_items(blob)
    manifest = json.loads(dict(items)["manifest.json"])
    edit(manifest)
    return _replace_member(blob, "manifest.json", json.dumps(manifest).encode("utf-8"))


def _append_member(blob: bytes, name: str, data: bytes = b"extra") -> bytes:
    return _pack([*_tar_items(blob), (name, data)])


def _drop_excluded(manifest: dict[str, object]) -> None:
    manifest.pop("excluded")


def _escape_first_path(manifest: dict[str, object]) -> None:
    manifest["files"][0]["path"] = "../escape"  # type: ignore[index]


def _seeded_export(tmp_path: Path, name: str = "Source Strategy") -> tuple[Path, bytes, str, str]:
    root = tmp_path / "source"
    root.mkdir()
    strategy_id, version = _seed_strategy(root, name)
    return root, export_full(root, db_paths=_db_paths(root)).body, strategy_id, version


def test_round_trip_imports_ids_versions_bytes_and_database_rows(tmp_path: Path) -> None:
    source, blob, strategy_id, version = _seeded_export(tmp_path)
    target = tmp_path / "target"

    result = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert result.layout_version == LAYOUT_VERSION
    assert result.strategies == 1
    assert result.versions == 1
    assert result.reopened is False
    assert result.content_sha256
    assert _tree_bytes(target) == _tree_bytes(source)

    store = SqliteStrategyStore(target / "strategy_store.db")
    try:
        artifacts = store.list_artifacts(type=ArtifactType.STRATEGY)
        assert [artifact.id for artifact in artifacts] == [strategy_id]
        assert artifacts[0].artifact_version == version
    finally:
        store.close()
    connection = sqlite3.connect(target / "strategy_store.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    finally:
        connection.close()

    source_bundle = export_full(source, db_paths=_db_paths(source))
    reexport = export_full(target, db_paths=_db_paths(target))
    assert reexport.content_sha256 == source_bundle.content_sha256


def test_unsupported_layout_leaves_the_target_untouched(tmp_path: Path) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    before = _state_snapshot(target)

    with pytest.raises(LayoutUnsupported):
        import_full(blob, "vt-state-v2", runtime_root=target, db_paths=_db_paths(target))

    assert _state_snapshot(target) == before
    assert list(target.glob(".state-import-*")) == []


def test_a_tampered_data_byte_is_blob_corrupt_and_leaves_the_target_alone(tmp_path: Path) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    before = _state_snapshot(target)
    items = _tar_items(blob)
    name, data = next((member, content) for member, content in items if member.startswith("data/"))
    tampered = _replace_member(blob, name, data[:-1] + bytes([data[-1] ^ 0xFF]))

    with pytest.raises(BlobCorrupt):
        import_full(tampered, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert _state_snapshot(target) == before
    assert list(target.glob(".state-import-*")) == []


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        pytest.param(lambda blob: _edit_manifest(blob, _drop_excluded), ManifestInvalid, id="manifest_invalid"),
        pytest.param(lambda blob: _edit_manifest(blob, _escape_first_path), PathEscape, id="path_escape"),
        pytest.param(lambda blob: _append_member(blob, "data/strategy_store.db"), ManifestInvalid, id="duplicate_member"),
        pytest.param(
            lambda blob: _append_member(blob, "data/strategies/undeclared.txt"),
            ManifestInvalid,
            id="undeclared_file",
        ),
    ],
)
def test_malformed_blobs_are_refused_without_touching_the_target(
    tmp_path: Path, mutator, expected: type[Exception]  # noqa: ANN001 — parametrized
) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    before = _state_snapshot(target)

    with pytest.raises(expected):
        import_full(mutator(blob), LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert _state_snapshot(target) == before
    assert list(target.glob(".state-import-*")) == []


def test_install_failure_rolls_back_tree_databases_and_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    old_generation = "generation-before-import"
    _shared.write_state_generation(old_generation, runtime_root=target)
    before = _state_snapshot(target)
    real_install = install_module._install_state

    def install_then_fail(*args, **kwargs):  # noqa: ANN002, ANN003 — test hook
        real_install(*args, **kwargs)
        raise RuntimeError("injected install failure")

    monkeypatch.setattr(install_module, "_install_state", install_then_fail)

    with pytest.raises(ImportFailed):
        import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert _state_snapshot(target) == before
    assert _shared.read_state_generation(target) == old_generation
    assert list(target.glob(".state-import-*")) == []
    assert list(target.glob(".state-import-backup-*")) == []


def test_failed_rollback_keeps_the_backup_directory_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")

    def fail_install(*args, **kwargs):  # noqa: ANN002, ANN003 — test hook
        raise RuntimeError("injected install failure")

    monkeypatch.setattr(install_module, "_install_state", fail_install)
    monkeypatch.setattr(install_module, "_restore_state", lambda *args, **kwargs: OSError("rollback stuck"))

    with pytest.raises(ImportFailed):
        import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    backups = list(target.glob(".state-import-backup-*"))
    assert len(backups) == 1
    assert backups[0].is_dir()
    assert (backups[0] / "strategies").is_dir()


def test_stale_wal_shm_and_journal_are_removed_and_the_import_wins(tmp_path: Path) -> None:
    _source, blob, imported_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    database = target / "strategy_store.db"
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").write_bytes(b"stale sidecar")

    result = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert result.strategies == 1
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{database}{suffix}").exists()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE id = ?", (imported_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE name = 'Existing Strategy'").fetchone()[0] == 0
    finally:
        connection.close()


def test_import_is_authoritative_so_an_empty_blob_removes_old_state(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    empty_blob = export_full(empty, db_paths={})
    target = tmp_path / "target"
    target.mkdir()
    old_id, _version = _seed_strategy(target, "Existing Strategy")

    result = import_full(empty_blob.body, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert result.strategies == 0
    assert result.versions == 0
    assert (target / "strategies").is_dir()
    assert list((target / "strategies").iterdir()) == []
    assert not (target / "strategies" / old_id).exists()
    assert not (target / "strategy_store.db").exists()
    assert _shared.read_state_generation(target).startswith(f"{empty_blob.content_sha256}:")


def test_reimporting_the_same_blob_rotates_the_generation_marker(tmp_path: Path) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"

    first = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))
    first_marker = _shared.read_state_generation(target)
    second = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))
    second_marker = _shared.read_state_generation(target)

    assert first.content_sha256 == second.content_sha256
    assert first_marker.startswith(f"{first.content_sha256}:")
    assert second_marker.startswith(f"{second.content_sha256}:")
    assert first_marker != second_marker


def test_import_reports_no_reopen_when_no_store_was_open(tmp_path: Path) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    _shared.reopen_store()

    result = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert result.reopened is False


def test_import_reports_a_reopened_live_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    live = SqliteStrategyStore(tmp_path / "live.db")
    monkeypatch.setattr(_shared, "_store", live)
    monkeypatch.setattr(_shared, "_bound_store", live)
    monkeypatch.setattr(_shared, "_store_generation", "before-import")

    result = import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert result.reopened is True
    assert _shared._store is None


def test_a_held_import_lock_answers_import_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, blob, _strategy_id, _version = _seeded_export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _seed_strategy(target, "Existing Strategy")
    before = _state_snapshot(target)

    class _DeniedLock:
        def acquire(self, blocking: bool = False) -> bool:  # noqa: ARG002 — lock protocol
            return False

        def release(self) -> None:
            raise AssertionError("a denied acquire must not be released")

    monkeypatch.setattr(service_module, "_IMPORT_LOCK", _DeniedLock())

    with pytest.raises(ImportInProgress):
        import_full(blob, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))

    assert _state_snapshot(target) == before
