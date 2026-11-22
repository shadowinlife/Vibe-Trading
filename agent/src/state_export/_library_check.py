"""Shape-aware validation of the installed strategy library.

``strategy_store.db`` exists in two schema shapes: the legacy artifact
library (an ``artifacts`` table whose strategy rows name the strategy
directories) and the version registry (a ``strategy_version_registrations``
table whose rows name published version trees). One library may carry both.
The shape is decided from the tables that actually exist, never from the
package layout or a caller declaration.

Invariant: every strategy directory is explained by at least one artifact
row or registration; every artifact strategy row names an installed
directory; every active registration names an installed version directory
and verifies against the installed tree. Pending and tombstoned
registrations carry identity without requiring a tree, and one directory
may carry both record kinds when they name the same strategy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.state_export._errors import _SelfCheckFailed
from src.state_export._paths import _STRATEGY_DIRNAME, _read_only_uri
from src.strategy_store._version_registry_schema import _TABLE
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import VersionState
from src.strategy_versions.projection import verify_source

__all__ = [
    "check_strategy_library",
    "strategy_directories",
    "version_directory_pairs",
]


def check_strategy_library(db_path: Path, root: Path) -> None:
    """Require the installed library records to explain the installed tree.

    Args:
        db_path: Installed ``strategy_store.db`` in either schema shape.
        root: Runtime root holding the installed ``strategies`` tree.

    Raises:
        _SelfCheckFailed: If a directory has no record, an artifact row has
            no directory, or an active registration cannot be verified.
        sqlite3.Error: If the library cannot be read; the caller maps it.
    """
    connection = sqlite3.connect(_read_only_uri(db_path), uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        artifact_ids = (
            _artifact_strategy_ids(connection) if "artifacts" in tables else set()
        )
    finally:
        connection.close()
    has_registry = _TABLE in tables
    if not has_registry and "artifacts" not in tables:
        raise _SelfCheckFailed("strategy library exposes no strategy records")

    directories = {path.name for path in strategy_directories(root)}
    records = (
        SqliteVersionRegistry(db_path, read_only=True).all_records()
        if has_registry
        else ()
    )
    registry_ids = {record.key.strategy_id for record in records}

    unexplained = directories - (artifact_ids | registry_ids)
    if unexplained:
        raise _SelfCheckFailed(
            f"{len(unexplained)} strategy directories have no library record"
        )
    orphaned = artifact_ids - directories
    if orphaned:
        raise _SelfCheckFailed(
            f"{len(orphaned)} artifact rows have no strategy directory"
        )

    pairs = version_directory_pairs(root)
    for record in records:
        if record.state is not VersionState.ACTIVE:
            continue
        pair = (record.key.strategy_id, record.key.strategy_version)
        if pair not in pairs:
            raise _SelfCheckFailed(
                "active registration has no installed version directory"
            )
        try:
            verify_source(record, root)
        except VersionNotFoundError as exc:
            raise _SelfCheckFailed(
                "active registration has no verifiable installed lineage"
            ) from exc
        except VersionDigestMismatchError as exc:
            raise _SelfCheckFailed(
                "active registration contradicts its installed digest"
            ) from exc


def _artifact_strategy_ids(connection: sqlite3.Connection) -> set[str]:
    """Return the ids of every ``type='strategy'`` artifact row."""
    rows = connection.execute(
        "SELECT id FROM artifacts WHERE type = 'strategy'"
    ).fetchall()
    return {row[0] for row in rows}


def strategy_directories(root: Path) -> list[Path]:
    """Return the real direct-child directories of ``<root>/strategies``."""
    strategies = root / _STRATEGY_DIRNAME
    if strategies.is_symlink() or not strategies.is_dir():
        return []
    return [
        entry
        for entry in strategies.iterdir()
        if entry.is_dir() and not entry.is_symlink()
    ]


def version_directory_pairs(root: Path) -> set[tuple[str, str]]:
    """Return every ``(strategy_id, version)`` directory pair on disk."""
    pairs: set[tuple[str, str]] = set()
    for strategy_dir in strategy_directories(root):
        versions_dir = strategy_dir / "versions"
        if versions_dir.is_symlink() or not versions_dir.is_dir():
            continue
        for version_dir in versions_dir.iterdir():
            if version_dir.is_dir() and not version_dir.is_symlink():
                pairs.add((strategy_dir.name, version_dir.name))
    return pairs
