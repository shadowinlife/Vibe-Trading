"""Backup, atomic install, self-check and rollback for a validated state blob.

The live state is never touched until :func:`_extract_blob` has accepted the
manifest and every digest. Installation is a same-filesystem sequence of
renames: strategies first, then each database without stale sidecars, then the
generation marker. A failure after the backup reverses every recorded move; if
the reversal itself cannot finish, the backup directory is retained and named
in a warning so an operator can recover the old state.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from src.state_export._archive import _extract_blob
from src.state_export._errors import ImportFailed, StateExportError, _SelfCheckFailed
from src.state_export._models import ImportResult
from src.state_export._paths import (
    _DB_SIDECARS,
    _IMPORT_BACKUP_PREFIX,
    _IMPORT_TMP_PREFIX,
    _STRATEGY_DIRNAME,
    _make_temp_dir,
    _move,
    _read_only_uri,
    _remove_path,
    _remove_tree,
    _resolved_db_paths,
    _resolved_root,
    _unlink,
)
from src.state_export.manifest import DATA_DIRNAME, ManifestInvalid, PathEscape, StateManifest
from src.strategy_store import _shared

__all__ = ["import_locked"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _BackupRecord:
    """Everything the import must undo when installation fails."""

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    generation_existed: bool = False
    generation: str = ""
    strategies_installed: bool = False
    installed_databases: list[Path] = field(default_factory=list)


def import_locked(
    blob: bytes,
    runtime_root: Path | None,
    db_paths: Mapping[str, Path] | None,
) -> ImportResult:
    """Import one already-fenced blob; the caller holds the process lock."""
    root = _resolved_root(runtime_root)
    databases = _resolved_db_paths(root, db_paths)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_same_device(root, databases)
    staging = _make_temp_dir(root, _IMPORT_TMP_PREFIX)
    backup: Path | None = None
    record = _BackupRecord()
    keep_backup = False
    try:
        manifest = _extract_blob(blob, staging, set(databases))
        backup = _make_temp_dir(root, _IMPORT_BACKUP_PREFIX)
        try:
            _take_backup(root, databases, backup, record)
            _install_state(root, databases, staging, manifest, record)
            _shared.write_state_generation(
                f"{manifest.content_sha256}:{uuid.uuid4().hex}", runtime_root=root
            )
            reopened = _shared.reopen_store()
            strategies, versions = _self_check(root, databases, manifest)
        except BaseException as exc:
            try:
                problem = _restore_state(root, databases, record)
            except BaseException:
                keep_backup = True
                logger.warning("state import rollback raised; backup retained at %s", backup)
                raise ImportFailed("state import failed and rollback raised") from exc
            if problem is not None:
                keep_backup = True
                logger.warning("state import rollback incomplete; backup retained at %s", backup)
                raise ImportFailed(
                    "state import failed and the previous state could not be fully restored"
                ) from exc
            if isinstance(exc, StateExportError):
                raise
            if isinstance(exc, _SelfCheckFailed):
                raise ImportFailed(f"state import self-check failed: {exc}") from exc
            raise ImportFailed(f"state import failed: {type(exc).__name__}") from exc
        return ImportResult(
            layout_version=manifest.layout_version,
            content_sha256=manifest.content_sha256,
            strategies=strategies,
            versions=versions,
            reopened=reopened,
        )
    finally:
        _remove_tree(staging)
        if not keep_backup:
            _remove_tree(backup)


def _ensure_same_device(root: Path, databases: Mapping[str, Path]) -> None:
    """Refuse the import before any change when a rename would cross devices."""
    root_device = _existing_device(root)
    for name, target in databases.items():
        if _existing_device(target.parent) != root_device:
            raise ImportFailed(f"database target for {name} is on a different filesystem than the runtime root")


def _existing_device(path: Path) -> int:
    """Return the device of *path* or its nearest existing ancestor."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.stat(probe).st_dev


def _take_backup(
    root: Path,
    databases: Mapping[str, Path],
    backup: Path,
    record: _BackupRecord,
) -> None:
    """Move the live strategies tree, databases and sidecars into *backup*.

    Every successful rename is appended to *record*, so a failure part-way is
    still fully reversible.
    """
    generation_path = _shared.state_generation_path(root)
    record.generation_existed = generation_path.is_file()
    record.generation = _shared.read_state_generation(root)

    strategies = root / _STRATEGY_DIRNAME
    if strategies.is_symlink():
        raise PathEscape(f"live strategies path is a symlink: {strategies}")
    if strategies.exists():
        _move(strategies, backup / _STRATEGY_DIRNAME, record.moves)

    for index, (name, target) in enumerate(databases.items()):
        if target.is_symlink():
            raise PathEscape(f"live database path is a symlink: {name}")
        if target.exists():
            if not target.is_file():
                raise ManifestInvalid(f"live database path is not a regular file: {name}")
            _move(target, backup / f"db-{index}", record.moves)
        for suffix in _DB_SIDECARS:
            sidecar = target.with_name(f"{target.name}{suffix}")
            if sidecar.is_symlink():
                raise PathEscape(f"live database sidecar is a symlink: {sidecar}")
            if sidecar.exists():
                _move(sidecar, backup / f"db-{index}{suffix}", record.moves)


def _install_state(
    root: Path,
    databases: Mapping[str, Path],
    staging: Path,
    manifest: StateManifest,
    record: _BackupRecord,
) -> None:
    """Atomically replace strategies and databases from the validated staging."""
    staged_strategies = staging / DATA_DIRNAME / _STRATEGY_DIRNAME
    if not staged_strategies.exists():
        staged_strategies.mkdir(parents=True)
    record.strategies_installed = True
    os.replace(staged_strategies, root / _STRATEGY_DIRNAME)

    declared_databases = {entry.path for entry in manifest.databases}
    for name, target in databases.items():
        for suffix in _DB_SIDECARS:
            target.with_name(f"{target.name}{suffix}").unlink(missing_ok=True)
        staged_database = staging / DATA_DIRNAME / name
        if name in declared_databases:
            target.parent.mkdir(parents=True, exist_ok=True)
            record.installed_databases.append(target)
            os.replace(staged_database, target)
        else:
            target.unlink(missing_ok=True)


def _self_check(
    root: Path,
    databases: Mapping[str, Path],
    manifest: StateManifest,
) -> tuple[int, int]:
    """Prove the installed state is readable and agrees with the manifest."""
    declared_databases = {entry.path for entry in manifest.databases}
    for name in declared_databases:
        try:
            _integrity_check(databases[name])
        except sqlite3.Error as exc:
            raise _SelfCheckFailed(f"integrity_check failed for {name}") from exc

    strategy_dirs = _strategy_directories(root)
    row_count = 0
    if "strategy_store.db" in declared_databases:
        try:
            row_count = _strategy_row_count(databases["strategy_store.db"])
        except sqlite3.Error as exc:
            raise _SelfCheckFailed("strategy table is not enumerable") from exc
    if row_count != len(strategy_dirs):
        raise _SelfCheckFailed(
            f"strategy row count {row_count} does not match {len(strategy_dirs)} strategy directories"
        )

    on_disk = _version_directory_pairs(root)
    declared_versions = _manifest_version_pairs(manifest)
    if on_disk != declared_versions:
        raise _SelfCheckFailed(
            f"version directories do not match the manifest ({len(on_disk)} on disk, "
            f"{len(declared_versions)} declared)"
        )
    return len(strategy_dirs), len(on_disk)


def _integrity_check(path: Path) -> None:
    """Run ``PRAGMA integrity_check`` read-only and require ``ok``."""
    connection = sqlite3.connect(_read_only_uri(path), uri=True)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if row is None or str(row[0]).lower() != "ok":
        raise _SelfCheckFailed(f"integrity_check returned {row!r}")


def _strategy_row_count(path: Path) -> int:
    """Return the number of ``type='strategy'`` artifact rows."""
    connection = sqlite3.connect(_read_only_uri(path), uri=True)
    try:
        row = connection.execute("SELECT COUNT(*) FROM artifacts WHERE type = 'strategy'").fetchone()
    finally:
        connection.close()
    return int(row[0]) if row is not None else 0


def _strategy_directories(root: Path) -> list[Path]:
    """Return the real direct-child directories of ``<root>/strategies``."""
    strategies = root / _STRATEGY_DIRNAME
    if strategies.is_symlink() or not strategies.is_dir():
        return []
    return [entry for entry in strategies.iterdir() if entry.is_dir() and not entry.is_symlink()]


def _version_directory_pairs(root: Path) -> set[tuple[str, str]]:
    """Return every ``(strategy_id, version)`` directory pair on disk."""
    pairs: set[tuple[str, str]] = set()
    for strategy_dir in _strategy_directories(root):
        versions_dir = strategy_dir / "versions"
        if versions_dir.is_symlink() or not versions_dir.is_dir():
            continue
        for version_dir in versions_dir.iterdir():
            if version_dir.is_dir() and not version_dir.is_symlink():
                pairs.add((strategy_dir.name, version_dir.name))
    return pairs


def _manifest_version_pairs(manifest: StateManifest) -> set[tuple[str, str]]:
    """Return the ``(strategy_id, version)`` prefixes declared by the manifest."""
    pairs: set[tuple[str, str]] = set()
    for entry in manifest.files:
        parts = entry.path.split("/")
        if len(parts) >= 5 and parts[0] == _STRATEGY_DIRNAME and parts[2] == "versions":
            pairs.add((parts[1], parts[3]))
    return pairs


def _restore_state(
    root: Path,
    databases: Mapping[str, Path],
    record: _BackupRecord,
) -> OSError | None:
    """Undo every post-backup change; return the first failure, if any.

    New strategies and installed databases are removed first, then the recorded
    renames are reversed and the previous generation marker is restored (or
    removed when there was none).
    """
    problems: list[OSError] = []
    if record.strategies_installed:
        problems.extend(_remove_path(root / _STRATEGY_DIRNAME))
    for target in record.installed_databases:
        for path in (target, *(target.with_name(f"{target.name}{suffix}") for suffix in _DB_SIDECARS)):
            problems.extend(_unlink(path))
    for original, saved in reversed(record.moves):
        try:
            if not saved.exists() and not saved.is_symlink():
                problems.append(OSError(f"backup entry is missing: {saved}"))
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(saved, original)
        except OSError as exc:
            problems.append(exc)
    try:
        if record.generation_existed:
            _shared.write_state_generation(record.generation, runtime_root=root)
        else:
            _shared.state_generation_path(root).unlink(missing_ok=True)
    except OSError as exc:
        problems.append(exc)
    return problems[0] if problems else None
