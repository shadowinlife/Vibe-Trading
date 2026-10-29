"""Offline contract tests for the ``vt-state-v1`` full-state export.

Every test seeds a throwaway runtime root and never resolves the developer's
real home: the database paths are always passed explicitly, so an exported
environment variable in the developer's shell cannot leak live state into a
test. The assertions are behavioural — tar membership, manifest digests, SQLite
integrity, and the absence of temp directories after a failed export — rather
than a restatement of the implementation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

from src.state_export import _export as export_module
from src.state_export import manifest as manifest_module
from src.state_export._errors import ExportUnstable
from src.state_export.manifest import (
    EXCLUDED_PATHS,
    LAYOUT_VERSION,
    BlobTooLarge,
    PathEscape,
    StateFile,
    compute_content_sha256,
)
from src.state_export.service import export_full, import_full
from src.strategy_library.store import create_draft, snapshot
from src.strategy_store.sqlite_store import SqliteStrategyStore

_DB_NAMES = ("strategy_store.db", "strategy_discovery.db")


@pytest.fixture(autouse=True)
def _no_ambient_store_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's exported store paths out of every test."""
    for name in (
        "VIBE_TRADING_HOME",
        "VIBE_TRADING_STRATEGY_STORE_DB_PATH",
        "VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def _db_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _DB_NAMES}


def _spec(name: str = "Momentum One") -> dict[str, object]:
    return {
        "name": name,
        "kind": "code",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _seed_strategy(root: Path, name: str = "Momentum One") -> tuple[str, str]:
    """Create one draft, its code sources and one immutable version snapshot."""
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


def _seed_discovery_db(root: Path) -> None:
    connection = sqlite3.connect(str(root / "strategy_discovery.db"))
    try:
        connection.execute("CREATE TABLE evidence (strategy_id TEXT, sharpe REAL)")
        connection.execute("INSERT INTO evidence VALUES ('art_seed', 1.25)")
        connection.commit()
    finally:
        connection.close()


def _tar_entries(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def _manifest(blob: bytes) -> dict[str, object]:
    return json.loads(_tar_entries(blob)["manifest.json"])


def test_full_export_is_a_gzip_tar_described_by_its_manifest(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    strategy_id, version = _seed_strategy(root)
    _seed_discovery_db(root)

    bundle = export_full(root, db_paths=_db_paths(root))

    assert bundle.body[:2] == b"\x1f\x8b"
    entries = _tar_entries(bundle.body)
    manifest = _manifest(bundle.body)
    assert manifest["layout_version"] == LAYOUT_VERSION
    assert set(entries) == {"manifest.json"} | {
        f"data/{entry['path']}" for entry in (*manifest["files"], *manifest["databases"])  # type: ignore[index]
    }
    assert {entry["path"] for entry in manifest["databases"]} == set(_DB_NAMES)  # type: ignore[index]
    assert any(
        entry["path"] == f"strategies/{strategy_id}/versions/{version}/code/signal_engine.py"
        for entry in manifest["files"]  # type: ignore[index]
    )

    for entry in (*manifest["files"], *manifest["databases"]):  # type: ignore[index]
        data = entries[f"data/{entry['path']}"]
        assert len(data) == entry["size"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]

    declared = tuple(
        StateFile(entry["path"], entry["sha256"], entry["size"])
        for entry in (*manifest["files"], *manifest["databases"])  # type: ignore[index]
    )
    expected_sha = compute_content_sha256(declared)
    assert manifest["content_sha256"] == expected_sha
    assert bundle.content_sha256 == expected_sha

    assert not any(name.endswith(("-wal", "-shm")) for name in entries)
    snapshot_path = tmp_path / "snapshot-strategy_store.db"
    snapshot_path.write_bytes(entries["data/strategy_store.db"])
    connection = sqlite3.connect(f"{snapshot_path.as_uri()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_export_excludes_run_artifacts_sessions_and_the_agent_token(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    _seed_strategy(root)
    for name in ("runs", "sessions", "uploads", "scheduled_research"):
        (root / name).mkdir()
        (root / name / "payload.txt").write_text("not user state\n", encoding="utf-8")
    (root / "strategy_runs.sqlite3").write_bytes(b"ledger")
    token_dir = root / ".config" / "rm"
    token_dir.mkdir(parents=True)
    (token_dir / "agent-token").write_text("dummy-agent-token-never-exported", encoding="utf-8")

    bundle = export_full(root, db_paths=_db_paths(root))
    entries = _tar_entries(bundle.body)
    manifest = _manifest(bundle.body)
    recorded = [entry["path"] for entry in (*manifest["files"], *manifest["databases"])]  # type: ignore[index]

    for excluded in EXCLUDED_PATHS:
        assert excluded in manifest["excluded"]  # type: ignore[operator]
        assert not any(path == excluded or path.startswith(f"{excluded}/") for path in recorded)
        assert not any(name == f"data/{excluded}" or name.startswith(f"data/{excluded}/") for name in entries)
    assert manifest["excluded"] == list(EXCLUDED_PATHS)  # type: ignore[index]
    assert not any(".config/rm" in name for name in entries)


def test_empty_tree_and_no_database_is_a_valid_empty_state(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()

    bundle = export_full(root, db_paths={})

    manifest = _manifest(bundle.body)
    assert manifest["files"] == []
    assert manifest["databases"] == []
    assert bundle.content_sha256 == hashlib.sha256(b"").hexdigest()
    assert set(_tar_entries(bundle.body)) == {"manifest.json"}


def test_content_sha256_is_stable_across_reexport_and_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    _seed_strategy(root)

    first = export_full(root, db_paths=_db_paths(root))
    second = export_full(root, db_paths=_db_paths(root))
    assert first.content_sha256 == second.content_sha256

    target = tmp_path / "target"
    import_full(first.body, LAYOUT_VERSION, runtime_root=target, db_paths=_db_paths(target))
    third = export_full(target, db_paths=_db_paths(target))
    assert third.content_sha256 == first.content_sha256


def test_file_count_budget_aborts_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    _seed_strategy(root)
    monkeypatch.setattr(export_module, "MAX_FILE_COUNT", 1)
    monkeypatch.setattr(manifest_module, "MAX_FILE_COUNT", 1)

    with pytest.raises(BlobTooLarge):
        export_full(root, db_paths=_db_paths(root))
    assert list(root.glob(".state-export-*")) == []


def test_byte_budget_aborts_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    _seed_strategy(root)
    monkeypatch.setattr(export_module, "MAX_BLOB_BYTES", 64)
    monkeypatch.setattr(manifest_module, "MAX_BLOB_BYTES", 64)

    with pytest.raises(BlobTooLarge):
        export_full(root, db_paths=_db_paths(root))
    assert list(root.glob(".state-export-*")) == []


def test_a_tree_mutated_between_scan_and_read_is_export_unstable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    strategy_id, _version = _seed_strategy(root)
    real_scan = export_module._scan_strategy_tree
    target = root / "strategies" / strategy_id / "strategy.json"

    def scan_then_mutate(strategies_root: Path, runtime_root: Path) -> list[object]:
        entries = real_scan(strategies_root, runtime_root)
        target.write_text(target.read_text(encoding="utf-8") + "changed", encoding="utf-8")
        return entries

    monkeypatch.setattr(export_module, "_scan_strategy_tree", scan_then_mutate)

    with pytest.raises(ExportUnstable):
        export_full(root, db_paths=_db_paths(root))
    assert list(root.glob(".state-export-*")) == []


def test_a_same_name_content_change_after_the_read_is_export_unstable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    strategy_id, _version = _seed_strategy(root)
    target = root / "strategies" / strategy_id / "strategy.json"
    real_walk = export_module._walk_regular_files
    calls = 0

    def walk_then_rewrite(strategies_root: Path) -> list[Path]:
        nonlocal calls
        calls += 1
        paths = real_walk(strategies_root)
        if calls == 2:
            info = target.lstat()
            data = target.read_bytes()
            target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        return paths

    monkeypatch.setattr(export_module, "_walk_regular_files", walk_then_rewrite)

    with pytest.raises(ExportUnstable):
        export_full(root, db_paths=_db_paths(root))


def test_symlinked_strategy_paths_are_refused_not_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside the runtime root\n", encoding="utf-8")

    rooted = tmp_path / "rooted"
    rooted.mkdir()
    (rooted / "strategies").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathEscape):
        export_full(rooted, db_paths={})

    nested = tmp_path / "nested"
    strategies = nested / "strategies" / "art_evil"
    strategies.mkdir(parents=True)
    (strategies / "link.txt").symlink_to(outside / "secret.txt")
    with pytest.raises(PathEscape):
        export_full(nested, db_paths={})
