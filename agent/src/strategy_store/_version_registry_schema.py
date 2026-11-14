"""Schema, SQL and row mapping for the strategy-version registry table.

``SqliteVersionRegistry`` owns exactly one table.  This module keeps that
table's DDL, column list, insert statement, row conversion and import-record
validation in one place, so the adapter and the state-export merge seam cannot
drift into two different contracts.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from src.strategy_versions.errors import VersionConflictError
from src.strategy_versions.models import (
    RegistrationKey,
    SourceFacts,
    VersionRegistration,
    VersionState,
)

_TABLE = "strategy_version_registrations"
_CONTRACT_VERSION = 1
_IMMUTABLE_COLUMNS = """
    owner_user_id, strategy_id, strategy_version, version_id,
    source_path, source_encoding, source_sha256, source_bytes, contract_version
"""
_SELECT_COLUMNS = """
    owner_user_id, strategy_id, strategy_version, version_id,
    source_path, source_encoding, source_sha256, source_bytes, state
"""

_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS {_TABLE} (
        owner_user_id    TEXT NOT NULL,
        strategy_id      TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        version_id       TEXT NOT NULL UNIQUE,
        source_path      TEXT NOT NULL
                         CHECK (source_path = 'code/signal_engine.py'),
        source_encoding  TEXT NOT NULL CHECK (source_encoding = 'utf-8'),
        source_sha256    TEXT NOT NULL CHECK (length(source_sha256) = 64),
        source_bytes     INTEGER NOT NULL CHECK (source_bytes >= 0),
        state            TEXT NOT NULL
                         CHECK (state IN ('pending', 'active', 'tombstoned')),
        contract_version INTEGER NOT NULL
                         CHECK (contract_version = {_CONTRACT_VERSION}),
        PRIMARY KEY (owner_user_id, strategy_id, strategy_version)
    );

    CREATE TRIGGER IF NOT EXISTS {_TABLE}_identity_immutable
    BEFORE UPDATE OF {_IMMUTABLE_COLUMNS}
    ON {_TABLE}
    BEGIN
        SELECT RAISE(ABORT, 'strategy version registration is immutable');
    END;
"""

_INSERT_SQL = f"""
    INSERT INTO {_TABLE} (
        owner_user_id, strategy_id, strategy_version,
        version_id, source_path, source_encoding,
        source_sha256, source_bytes, state, contract_version
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def is_version_id_collision(exc: sqlite3.IntegrityError) -> bool:
    """Return whether *exc* is a duplicate ``version_id`` violation."""
    return f"{_TABLE}.version_id" in str(exc)


def row_to_registration(row: sqlite3.Row) -> VersionRegistration:
    """Convert one persisted row into a domain registration."""
    return VersionRegistration(
        key=RegistrationKey.parse(row["owner_user_id"], row["strategy_id"], row["strategy_version"]),
        version_id=row["version_id"],
        source=SourceFacts(
            source_path=row["source_path"],
            source_encoding=row["source_encoding"],
            sha256=row["source_sha256"],
            byte_length=row["source_bytes"],
        ),
        state=VersionState(row["state"]),
    )


def insert_values(record: VersionRegistration) -> tuple[object, ...]:
    """Return the bind values for one registration insert.

    Args:
        record: Registration to persist exactly.

    Returns:
        Column values in schema insert order.
    """
    key = record.key
    source = record.source
    return (
        key.owner_user_id,
        key.strategy_id,
        key.strategy_version,
        record.version_id,
        source.source_path,
        source.source_encoding,
        source.sha256,
        source.byte_length,
        record.state.value,
        _CONTRACT_VERSION,
    )


def validate_import_records(
    records: Iterable[VersionRegistration],
) -> tuple[VersionRegistration, ...]:
    """Validate a complete import batch before any row is replaced.

    Args:
        records: Candidate registrations from an incoming state package.

    Returns:
        The unchanged records as a tuple, in caller order.

    Raises:
        VersionConflictError: If a record is not a registration, its key
            components are invalid, or two records share a registration key
            or ``version_id``.
    """
    validated = tuple(records)
    keys: set[RegistrationKey] = set()
    version_ids: set[str] = set()
    for record in validated:
        if not isinstance(record, VersionRegistration):
            raise VersionConflictError("import record is not a version registration")
        try:
            key = RegistrationKey.parse(
                record.key.owner_user_id,
                record.key.strategy_id,
                record.key.strategy_version,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise VersionConflictError("import record identity is invalid") from exc
        if key in keys:
            raise VersionConflictError("import records contain a duplicate registration key")
        if record.version_id in version_ids:
            raise VersionConflictError("import records contain a duplicate version_id")
        keys.add(key)
        version_ids.add(record.version_id)
    return validated
