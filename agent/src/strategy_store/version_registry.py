"""SQLite-backed owner-bound strategy-version registration registry.

The registry owns exactly one table, ``strategy_version_registrations``, in the
same database file as the legacy strategy store.  It never touches the legacy
schema, ``PRAGMA user_version`` or any other table.

Reads are side-effect free: a missing database, missing parent or missing
registry table is an empty legacy registry and creates no file, directory,
table or WAL sibling.  Writes initialize the table lazily, switch the database
to WAL, and use ``BEGIN IMMEDIATE`` so registrations are serialized across
processes.  Identity and source facts are immutable; transitions only change
``state``.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from src.strategy_store._version_registry_schema import (
    _INSERT_SQL,
    _SCHEMA,
    _SELECT_COLUMNS,
    _TABLE,
    insert_values,
    is_version_id_collision,
    row_to_registration,
    validate_import_records,
)
from src.strategy_versions.errors import (
    VersionConflictError,
    VersionNotFoundError,
    VersionRegistryReadOnlyError,
    VersionTombstonedError,
)
from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
    VersionState,
    new_version_id,
)

_MAX_VERSION_ID_ATTEMPTS = 8
_BUSY_TIMEOUT_MS = 5000


def _missing_message(key: RegistrationKey) -> str:
    """Return a stable not-found message for *key*."""
    return f"no registration for {key.owner_user_id!r}/{key.strategy_id!r}/{key.strategy_version!r}"


class SqliteVersionRegistry:
    """Persistent registration storage sharing the legacy strategy database.

    Args:
        db_path: Path of the SQLite database that holds the registry table.
        read_only: When true, reads are allowed and every write is refused.
    """

    def __init__(self, db_path: Path | str, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only
        self._write_lock = threading.Lock()

    def get(self, key: RegistrationKey) -> VersionRegistration | None:
        """Return the registration for *key*, or ``None`` when absent.

        A missing database, parent directory or registry table reads as an
        empty legacy registry; this method never creates or mutates a file.

        Args:
            key: Owner-scoped registration key.

        Returns:
            The persisted registration, or ``None`` when the key is unknown.
        """
        with self._read_connection() as connection:
            if connection is None:
                return None
            row = self._select_one(connection, key)
        return None if row is None else row_to_registration(row)

    def get_state(self, key: RegistrationKey) -> VersionState | None:
        """Return only the persisted lifecycle state for *key*.

        This is the side-effect-free fallback for rows whose source facts
        cannot hydrate (for example a non-canonical digest): it selects only
        ``state`` and never constructs a registration.  A missing database,
        parent directory or registry table reads as an empty legacy registry;
        this method never creates or mutates a file or sidecar.

        Args:
            key: Owner-scoped registration key.

        Returns:
            The persisted state, or ``None`` when the key is unknown.
        """
        with self._read_connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                f"""
                SELECT state FROM {_TABLE}
                 WHERE owner_user_id = ? AND strategy_id = ?
                   AND strategy_version = ?
                """,
                (key.owner_user_id, key.strategy_id, key.strategy_version),
            ).fetchone()
        return None if row is None else VersionState(row["state"])

    def has_strategy(self, owner_user_id: str, strategy_id: str) -> bool:
        """Return whether any registration exists for the exact owner strategy.

        Pending, active and tombstoned rows count; missing or legacy storage
        reads as ``False`` without creating or mutating any file.
        """
        with self._read_connection() as connection:
            if connection is None:
                return False
            row = connection.execute(
                f"""
                SELECT 1 FROM {_TABLE}
                 WHERE owner_user_id = ? AND strategy_id = ?
                 LIMIT 1
                """,
                (owner_user_id, strategy_id),
            ).fetchone()
        return row is not None

    def register_pending(self, key: RegistrationKey, source: SourceFacts) -> VersionRegistration:
        """Persist a pending registration, resuming an identical retry.

        Args:
            key: Owner-scoped registration key.
            source: Source facts derived from the exact payload bytes.

        Returns:
            The authoritative pending registration.  An identical retry
            returns the stored record unchanged (same ``version_id``).

        Raises:
            VersionConflictError: If the key already stores different facts.
            VersionTombstonedError: If the key is tombstoned.
            VersionRegistryReadOnlyError: If this registry is read-only.
        """
        self._require_writable()
        with self._write_transaction() as connection:
            existing = self._select_one(connection, key)
            if existing is not None:
                record = row_to_registration(existing)
                if record.state is VersionState.TOMBSTONED:
                    raise VersionTombstonedError(f"registration {record.version_id} is tombstoned")
                if record.source != source:
                    raise VersionConflictError(f"registration {record.version_id} stores different source facts")
                return record
            for _ in range(_MAX_VERSION_ID_ATTEMPTS):
                version_id = new_version_id()
                record = VersionRegistration(key, version_id, source, VersionState.PENDING)
                try:
                    connection.execute(_INSERT_SQL, insert_values(record))
                except sqlite3.IntegrityError as exc:
                    if not is_version_id_collision(exc):
                        raise
                    continue
                return record
        raise VersionConflictError(f"could not allocate a unique version_id after {_MAX_VERSION_ID_ATTEMPTS} attempts")

    def activate(self, key: RegistrationKey) -> VersionRegistration:
        """Move a pending registration to active.

        Args:
            key: Owner-scoped registration key.

        Returns:
            The active registration; replaying on an active row returns it
            unchanged.

        Raises:
            VersionNotFoundError: If the key is unregistered.
            VersionTombstonedError: If the registration is tombstoned.
            VersionConflictError: If the row changed outside the transaction.
            VersionRegistryReadOnlyError: If this registry is read-only.
        """
        self._require_writable()
        with self._write_transaction() as connection:
            row = self._select_one(connection, key)
            if row is None:
                raise VersionNotFoundError(_missing_message(key))
            record = row_to_registration(row)
            if record.state is VersionState.TOMBSTONED:
                raise VersionTombstonedError(f"registration {record.version_id} is tombstoned")
            if record.state is VersionState.ACTIVE:
                return record
            cursor = connection.execute(
                f"""
                UPDATE {_TABLE} SET state = ?
                 WHERE owner_user_id = ? AND strategy_id = ?
                   AND strategy_version = ? AND state = ?
                """,
                (
                    VersionState.ACTIVE.value,
                    key.owner_user_id,
                    key.strategy_id,
                    key.strategy_version,
                    VersionState.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError(f"registration {record.version_id} changed concurrently")
            return replace(record, state=VersionState.ACTIVE)

    def tombstone(self, key: RegistrationKey) -> VersionRegistration:
        """Retire a pending or active registration.

        Args:
            key: Owner-scoped registration key.

        Returns:
            The tombstoned registration; replaying on an existing tombstone
            returns it unchanged.

        Raises:
            VersionNotFoundError: If the key is unregistered.
            VersionConflictError: If the row changed outside the transaction.
            VersionRegistryReadOnlyError: If this registry is read-only.
        """
        self._require_writable()
        with self._write_transaction() as connection:
            row = self._select_one(connection, key)
            if row is None:
                raise VersionNotFoundError(_missing_message(key))
            record = row_to_registration(row)
            if record.state is VersionState.TOMBSTONED:
                return record
            cursor = connection.execute(
                f"""
                UPDATE {_TABLE} SET state = ?
                 WHERE owner_user_id = ? AND strategy_id = ?
                   AND strategy_version = ? AND state IN (?, ?)
                """,
                (
                    VersionState.TOMBSTONED.value,
                    key.owner_user_id,
                    key.strategy_id,
                    key.strategy_version,
                    VersionState.PENDING.value,
                    VersionState.ACTIVE.value,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError(f"registration {record.version_id} changed concurrently")
            return replace(record, state=VersionState.TOMBSTONED)

    def all_records(self) -> tuple[VersionRegistration, ...]:
        """Return every persisted registration in key order.

        A missing database, parent directory or registry table reads as an
        empty registry; this method never creates or mutates a file.

        Returns:
            Every registration ordered by owner, strategy and version.
        """
        with self._read_connection() as connection:
            if connection is None:
                return ()
            rows = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                  FROM {_TABLE}
                 ORDER BY owner_user_id, strategy_id, strategy_version
                """
            ).fetchall()
        return tuple(row_to_registration(row) for row in rows)

    def replace_all_for_import(self, records: Iterable[VersionRegistration]) -> None:
        """Atomically replace only the registry rows for a staged import.

        Every record is validated before the existing rows are deleted, so an
        invalid or conflicting batch leaves the registry untouched.  The
        legacy artifacts schema, ``PRAGMA user_version`` and every other table
        are never modified.

        Args:
            records: Exact registrations the incoming state declares.

        Raises:
            VersionConflictError: If a record is invalid, or two records share
                a registration key or ``version_id``.
            VersionRegistryReadOnlyError: If this registry is read-only.
        """
        self._require_writable()
        validated = validate_import_records(records)
        with self._write_transaction() as connection:
            connection.execute(f"DELETE FROM {_TABLE}")
            connection.executemany(_INSERT_SQL, (insert_values(record) for record in validated))

    def _require_writable(self) -> None:
        """Reject writes when this adapter was opened read-only."""
        if self.read_only:
            raise VersionRegistryReadOnlyError("this registry adapter is read-only")

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside an immediate write transaction."""
        with self._write_lock:
            connection = self._open_writer()
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                connection.execute("COMMIT")
            finally:
                connection.close()

    def _open_writer(self) -> sqlite3.Connection:
        """Open a writer connection, creating the schema on first use."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection | None]:
        """Yield a read-only connection, or ``None`` when the registry is absent."""
        if not self.db_path.is_file():
            yield None
            return
        connection = sqlite3.connect(self._read_uri(), uri=True)
        try:
            connection.row_factory = sqlite3.Row
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_TABLE,),
            ).fetchone()
            yield connection if table is not None else None
        finally:
            connection.close()

    def _read_uri(self) -> str:
        """Return a read-only URI, avoiding sidecar creation when stable.

        ``immutable=1`` is only appended when no WAL or rollback-journal sibling
        exists: the file is stable, so SQLite needs no shared-memory or WAL
        sidecars.  With a sidecar, plain ``mode=ro`` keeps committed WAL frames
        visible.
        """
        base = self.db_path.expanduser().absolute().as_uri()
        sidecars = (
            self.db_path.with_name(f"{self.db_path.name}-wal"),
            self.db_path.with_name(f"{self.db_path.name}-journal"),
        )
        if any(sidecar.exists() for sidecar in sidecars):
            return f"{base}?mode=ro"
        return f"{base}?mode=ro&immutable=1"

    def _select_one(self, connection: sqlite3.Connection, key: RegistrationKey) -> sqlite3.Row | None:
        """Select the row for *key* on an already-open connection."""
        return connection.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
              FROM {_TABLE}
             WHERE owner_user_id = ? AND strategy_id = ?
               AND strategy_version = ?
            """,
            (key.owner_user_id, key.strategy_id, key.strategy_version),
        ).fetchone()
