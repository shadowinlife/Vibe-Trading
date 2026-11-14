"""Merge the durable strategy-version registry during state import.

The official importer extracts and validates a package before it touches live
state.  This module is the narrow seam that combines the live registry with the
package's registry before installation: incoming rows are authoritative,
omitted live keys become terminal tombstones, and every active result must
verify against the staged F9 projection.  A staged database is checkpointed into
one self-contained main file because the installer replaces only that file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from src.strategy_store._version_registry_schema import (
    _SELECT_COLUMNS,
    _TABLE,
)
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_store.version_registry import SqliteVersionRegistry
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionDigestMismatchError,
    VersionNotFoundError,
)
from src.strategy_versions.models import (
    RegistrationKey,
    VersionRegistration,
    VersionState,
)
from src.strategy_versions.projection import verify_source

__all__ = [
    "RegistryMergeConflict",
    "RegistryMergeResult",
    "merge_registry_for_import",
]

_STATE_PRECEDENCE = {
    VersionState.PENDING: 0,
    VersionState.ACTIVE: 1,
    VersionState.TOMBSTONED: 2,
}


class RegistryMergeConflict(VersionConflictError):
    """A staged registry cannot be merged without changing identity."""


@dataclass(frozen=True, slots=True)
class RegistryMergeResult:
    """Facts about one completed registry merge.

    Attributes:
        requires_strategy_store: Whether installation must include the merged
            ``strategy_store.db`` because the effective registry has rows.
        active_count: Active registrations in the merged state.
        tombstone_count: Terminal tombstones in the merged state.
        pending_count: Pending registrations in the merged state.
    """

    requires_strategy_store: bool
    active_count: int
    tombstone_count: int
    pending_count: int


def merge_registry_for_import(
    live_db: Path | None,
    staged_db: Path | None,
    staged_runtime_root: Path,
) -> RegistryMergeResult:
    """Merge the live registry with an authoritative staged package.

    Missing databases or registry tables read as empty. When both sides are
    empty the staged package is left untouched and no database is created.
    Otherwise the merged rows replace only the registry table in ``staged_db``,
    creating the legacy store schema first when the package omitted the file.

    Args:
        live_db: Live ``strategy_store.db`` path, or ``None`` when absent.
        staged_db: Staged ``strategy_store.db`` path. The file may not exist;
            it is created when the merge has rows to persist.
        staged_runtime_root: Extracted staging data root holding
            ``strategies/``; active results are verified against it.

    Returns:
        Whether the staged database is required and the merged state counts.

    Raises:
        RegistryMergeConflict: If the package changes an existing identity or
            source fact, duplicates a ``version_id`` across keys, or an active
            result has no valid staged projection.
    """
    merged = _merged_records(_read_records(live_db), _read_records(staged_db))
    if not merged:
        return RegistryMergeResult(False, 0, 0, 0)
    if staged_db is None:
        raise RegistryMergeConflict("a staged strategy store path is required")
    _verify_active_projections(merged, staged_runtime_root)
    _ensure_store_schema(staged_db)
    SqliteVersionRegistry(staged_db).replace_all_for_import(merged)
    _checkpoint_staged_store(staged_db)
    return RegistryMergeResult(
        requires_strategy_store=True,
        active_count=sum(record.state is VersionState.ACTIVE for record in merged),
        tombstone_count=sum(record.state is VersionState.TOMBSTONED for record in merged),
        pending_count=sum(record.state is VersionState.PENDING for record in merged),
    )


def _read_records(db_path: Path | None) -> tuple[VersionRegistration, ...]:
    """Read one side's registrations; a missing file or table is empty."""
    if db_path is None:
        return ()
    try:
        return SqliteVersionRegistry(db_path, read_only=True).all_records()
    except sqlite3.DatabaseError as exc:
        return _read_immutable_records(db_path, exc)


def _read_immutable_records(db_path: Path, error: sqlite3.DatabaseError) -> tuple[VersionRegistration, ...]:
    """Read a registry-free main file when stale sidecars poison normal access.

    A committed WAL state reads normally through the registry adapter. The
    fallback covers legacy databases with stale garbage ``-wal``/``-shm`` files:
    when the main file has no registry table or no rows, it is safe to treat
    that side as empty. If rows exist, the unreadable sidecars may carry newer
    tombstones, so the import must fail closed instead of reviving old state.
    """
    if not db_path.is_file():
        return ()
    uri = f"{db_path.expanduser().absolute().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_TABLE,),
        ).fetchone()
        if table is None:
            return ()
        rows = connection.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
              FROM {_TABLE}
             ORDER BY owner_user_id, strategy_id, strategy_version
            """
        ).fetchall()
    finally:
        connection.close()
    if rows:
        raise RegistryMergeConflict("registry rows cannot be read safely with their sidecars") from error
    return ()


def _merged_records(
    live: tuple[VersionRegistration, ...],
    incoming: tuple[VersionRegistration, ...],
) -> tuple[VersionRegistration, ...]:
    """Combine both sides under the frozen import precedence rules."""
    live_by_key = _unique_by_key(live)
    incoming_by_key = _unique_by_key(incoming)
    merged: dict[RegistrationKey, VersionRegistration] = {}
    for key, record in incoming_by_key.items():
        existing = live_by_key.get(key)
        merged[key] = record if existing is None else _merge_same_key(existing, record)
    for key, record in live_by_key.items():
        if key not in incoming_by_key:
            merged[key] = replace(record, state=VersionState.TOMBSTONED)
    _require_unique_version_ids(merged.values())
    return tuple(
        merged[key]
        for key in sorted(
            merged,
            key=lambda key: (
                key.owner_user_id,
                key.strategy_id,
                key.strategy_version,
            ),
        )
    )


def _unique_by_key(
    records: tuple[VersionRegistration, ...],
) -> dict[RegistrationKey, VersionRegistration]:
    """Index records by key, rejecting a duplicate registration key."""
    by_key: dict[RegistrationKey, VersionRegistration] = {}
    for record in records:
        if record.key in by_key:
            raise RegistryMergeConflict("registry contains a duplicate registration key")
        by_key[record.key] = record
    return by_key


def _merge_same_key(live: VersionRegistration, incoming: VersionRegistration) -> VersionRegistration:
    """Merge one shared key, keeping immutable facts and the strongest state."""
    if live.version_id != incoming.version_id:
        raise RegistryMergeConflict("staged registry changes an existing version identity")
    if live.source != incoming.source:
        raise RegistryMergeConflict("staged registry changes immutable source facts")
    state = max((live.state, incoming.state), key=_STATE_PRECEDENCE.__getitem__)
    return replace(incoming, state=state)


def _require_unique_version_ids(records: Iterable[VersionRegistration]) -> None:
    """Reject one ``version_id`` used under two different keys."""
    seen: set[str] = set()
    for record in records:
        if record.version_id in seen:
            raise RegistryMergeConflict("a version_id is registered under two different keys")
        seen.add(record.version_id)


def _verify_active_projections(records: tuple[VersionRegistration, ...], staged_runtime_root: Path) -> None:
    """Require every active result to verify against the staged F9 tree."""
    for record in records:
        if record.state is not VersionState.ACTIVE:
            continue
        try:
            verify_source(record, staged_runtime_root)
        except VersionNotFoundError as exc:
            raise RegistryMergeConflict("active version has no verifiable staged lineage") from exc
        except VersionDigestMismatchError as exc:
            raise RegistryMergeConflict("active version contradicts its staged digest") from exc


def _ensure_store_schema(staged_db: Path) -> None:
    """Create the empty legacy store schema when the package omitted the file."""
    if staged_db.exists():
        return
    SqliteStrategyStore(staged_db).close()


def _checkpoint_staged_store(staged_db: Path) -> None:
    """Fold every staged WAL frame into the main file and drop the sidecars.

    The official installer renames only the database file into live state, so a
    ``-wal`` dependency would silently discard the merged registry rows.  A
    truncating checkpoint first, then ``journal_mode=DELETE``, leaves one
    self-contained file that reads identically after the rename.
    """
    connection = sqlite3.connect(str(staged_db))
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        staged_db.with_name(f"{staged_db.name}{suffix}").unlink(missing_ok=True)
