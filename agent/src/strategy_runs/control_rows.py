"""Immutable row and claim records for the execution-control ledger.

These dataclasses are the typed read surface of ``execution_dispatches`` and
``execution_stops``; the store owns every write, and projections consume the
rows without touching raw SQLite dictionaries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = [
    "DispatchClaim",
    "DispatchRow",
    "StartClaim",
    "StopClaim",
    "StopRow",
]


@dataclass(frozen=True, slots=True)
class DispatchRow:
    """One durable dispatch declaration and its latest execution evidence.

    Attributes:
        dispatch_key: Client idempotency identity.
        kind: Execution kind, ``backtest`` or ``factor``.
        remote_id: Original remote execution identity.
        declared_digest: SHA-256 of the canonical declaration.
        declaration_json: Canonical declaration text.
        status: Durable dispatch lifecycle state.
        created_at: Claim time in epoch milliseconds.
        updated_at: Last write time in epoch milliseconds.
        contract_version: Wire contract version.
        input_digest: Prepared-input digest, when declared.
        config_sha256: Frozen config digest, when declared.
        source_sha256: Frozen source digest, when declared.
        run_dir: Server-owned execution directory, when declared.
        result_path: Server-owned result path, when available.
        error_kind: Failure class; required only for ``failed``.
        failure_reason: Bounded redacted failure text; only for ``failed``.
        worker_pid: Observed worker process id.
        worker_pgid: Observed worker process-group id.
        worker_meta_json: Canonical worker metadata.
        started_at: First worker observation time.
        terminal_at: Terminal transition time.
    """

    dispatch_key: str
    kind: str
    remote_id: str
    declared_digest: str
    declaration_json: str
    status: str
    created_at: int
    updated_at: int
    contract_version: str
    start_declaration_json: str | None = None
    start_digest: str | None = None
    input_digest: str | None = None
    config_sha256: str | None = None
    source_sha256: str | None = None
    run_dir: str | None = None
    result_path: str | None = None
    error_kind: str | None = None
    failure_reason: str | None = None
    worker_pid: int | None = None
    worker_pgid: int | None = None
    worker_meta_json: str | None = None
    started_at: int | None = None
    terminal_at: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> DispatchRow:
        """Convert one SQLite row without exposing raw storage dictionaries.

        Args:
            row: SQLite row containing every dispatch column.

        Returns:
            An immutable typed dispatch row.
        """
        return cls(
            dispatch_key=row["dispatch_key"],
            kind=row["kind"],
            remote_id=row["remote_id"],
            declared_digest=row["declared_digest"],
            declaration_json=row["declaration_json"],
            start_declaration_json=row["start_declaration_json"],
            start_digest=row["start_digest"],
            input_digest=row["input_digest"],
            config_sha256=row["config_sha256"],
            source_sha256=row["source_sha256"],
            run_dir=row["run_dir"],
            result_path=row["result_path"],
            status=row["status"],
            error_kind=row["error_kind"],
            failure_reason=row["failure_reason"],
            worker_pid=row["worker_pid"],
            worker_pgid=row["worker_pgid"],
            worker_meta_json=row["worker_meta_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            contract_version=row["contract_version"],
        )


@dataclass(frozen=True, slots=True)
class StopRow:
    """One idempotent stop request and its monotonic evidence projection.

    Attributes:
        stop_key: Stop idempotency identity.
        dispatch_key: Owning dispatch identity.
        remote_id: Owning original remote identity.
        state: ``requested``, ``confirmed``, or ``unknown``.
        requested_at: Initial request time in epoch milliseconds.
        deadline_at: Stop-evidence deadline in epoch milliseconds.
        updated_at: Last write time in epoch milliseconds.
        contract_version: Wire contract version.
        resolved_at: Resolution time, if resolved.
        evidence_kind: Allowed confirmation evidence class.
        evidence_json: Canonical non-empty confirmation payload.
    """

    stop_key: str
    dispatch_key: str
    remote_id: str
    state: str
    requested_at: int
    deadline_at: int
    updated_at: int
    contract_version: str
    resolved_at: int | None = None
    evidence_kind: str | None = None
    evidence_json: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> StopRow:
        """Convert one SQLite stop row.

        Args:
            row: SQLite row containing every stop column.

        Returns:
            An immutable typed stop row.
        """
        return cls(
            stop_key=row["stop_key"],
            dispatch_key=row["dispatch_key"],
            remote_id=row["remote_id"],
            state=row["state"],
            requested_at=row["requested_at"],
            deadline_at=row["deadline_at"],
            resolved_at=row["resolved_at"],
            evidence_kind=row["evidence_kind"],
            evidence_json=row["evidence_json"],
            updated_at=row["updated_at"],
            contract_version=row["contract_version"],
        )


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    """A new claim (``replayed=False``) or the stored duplicate."""

    replayed: bool
    row: DispatchRow


@dataclass(frozen=True, slots=True)
class StartClaim:
    replayed: bool
    row: DispatchRow
    start_digest: str


@dataclass(frozen=True, slots=True)
class StopClaim:
    """A new stop request or an existing/confirmed stop that needs no signal."""

    replayed: bool
    row: StopRow
