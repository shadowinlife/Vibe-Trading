"""Atomic dispatch/start/stop persistence on the shared strategy-run ledger.
All writes use ``BEGIN IMMEDIATE`` plus CAS; no method spawns or signals workers."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import ClassVar, Final, TypeVar, assert_never

from src.strategy_runs.control_contract import (
    FAILURE_REASON_LIMIT,
    STOP_EVIDENCE,
    ContractValidationError,
    JsonValue,
    canonical_json_bytes,
    sha256_hex,
)
from src.strategy_runs.control_rows import (
    DispatchClaim,
    DispatchRow,
    StartClaim,
    StopClaim,
    StopRow,
)
from src.strategy_runs.store import RunRow, StrategyRunStore, _epoch_ms

__all__ = [
    "ControlStore",
    "ControlStoreError",
    "DispatchClaim",
    "DispatchConflict",
    "DispatchNotFound",
    "DispatchRow",
    "DispatchTransitionError",
    "StartClaim",
    "StopClaim",
    "StopConflict",
    "StopRow",
    "StopTransitionError",
]

_TERMINAL_STATUSES: Final = ("completed", "failed", "cancelled")
_SELECT_DISPATCH_KEY: Final = (
    "SELECT * FROM execution_dispatches WHERE dispatch_key = ?"
)
_SELECT_DISPATCH_REMOTE: Final = (
    "SELECT * FROM execution_dispatches WHERE remote_id = ?"
)
_SELECT_STOP: Final = "SELECT * FROM execution_stops WHERE stop_key = ?"
_T = TypeVar("_T")


class ControlStoreError(Exception):
    """A storage or lifecycle failure with a stable operation tag.

    Args:
        operation: Public operation that failed.
        detail: Underlying SQLite detail.
    """

    OPERATION: ClassVar[str] = "control_store"
    detail = ""

    def __init__(self, operation: str, detail: str) -> None:
        self._operation = operation
        self.detail = detail
        super().__init__(f"{operation}: {detail}")

    @property
    def operation(self) -> str:
        """Return the failing method, or the domain error's stable tag."""
        return getattr(self, "_operation", self.OPERATION)


@dataclass(slots=True)  # Exception traceback assignment requires mutation.
class DispatchConflict(ControlStoreError):
    """A dispatch key already belongs to another kind or declaration."""

    OPERATION: ClassVar[str] = "claim_dispatch"
    dispatch_key: str
    original_remote_id: str

    def __str__(self) -> str:
        return f"{self.dispatch_key}: original remote_id={self.original_remote_id}"


@dataclass(slots=True)  # Exception traceback assignment requires mutation.
class DispatchNotFound(ControlStoreError):
    """No dispatch exists for the supplied key or remote identity."""

    OPERATION: ClassVar[str] = "dispatch_lookup"
    dispatch_key: str | None = None
    remote_id: str | None = None

    def __str__(self) -> str:
        return f"dispatch not found: {self.dispatch_key or self.remote_id}"


@dataclass(slots=True)  # Exception traceback assignment requires mutation.
class DispatchTransitionError(ControlStoreError):
    """A dispatch write is invalid for the row's current lifecycle state."""

    OPERATION: ClassVar[str] = "dispatch_transition"
    dispatch_key: str
    current_status: str | None
    requested_status: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.dispatch_key}: {self.current_status} -> {self.requested_status}: {self.detail}"


@dataclass(slots=True)  # Exception traceback assignment requires mutation.
class StopConflict(ControlStoreError):
    """A stop key or dispatch/remote tuple is already bound differently."""

    OPERATION: ClassVar[str] = "begin_stop"
    stop_key: str
    dispatch_key: str
    original_remote_id: str

    def __str__(self) -> str:
        return f"{self.stop_key}: original remote_id={self.original_remote_id}"


@dataclass(slots=True)  # Exception traceback assignment requires mutation.
class StopTransitionError(ControlStoreError):
    """A stop resolution violates monotonic evidence semantics."""

    OPERATION: ClassVar[str] = "resolve_stop"
    stop_key: str
    current_state: str | None
    requested_state: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.stop_key}: {self.current_state} -> {self.requested_state}: {self.detail}"


class ControlStore(StrategyRunStore):
    """Dispatch-first persistence; every operation owns a short connection."""

    def ensure_schema(self) -> None:
        """Create tables and add missing start columns idempotently."""
        try:
            super().ensure_schema()
            with self._transaction("ensure_schema") as conn:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(execution_dispatches)")
                }
                for column in (
                    "start_declaration_json",
                    "start_digest",
                    "failure_reason",
                ):
                    if column not in columns:
                        conn.execute(
                            f"ALTER TABLE execution_dispatches ADD COLUMN {column} TEXT"
                        )
        except sqlite3.Error as exc:
            raise ControlStoreError("ensure_schema", str(exc)) from exc

    def claim_dispatch(
        self,
        dispatch_key: str,
        kind: str,
        declaration: Mapping[str, JsonValue],
        remote_id: str,
        contract_version: str,
        *,
        now_ms: int | None = None,
    ) -> DispatchClaim:
        """Insert one declaration or replay/conflict its original identity."""
        operation = "claim_dispatch"
        _text("dispatch_key", dispatch_key)
        _text("remote_id", remote_id)
        _text("contract_version", contract_version)
        if kind not in ("backtest", "factor"):
            raise ContractValidationError("kind", "must be 'backtest' or 'factor'")
        declaration_dict = dict(declaration)
        declaration_bytes = canonical_json_bytes(declaration_dict)
        digest = sha256_hex(declaration_bytes)
        stamp = _epoch_ms(now_ms)
        with self._transaction(operation) as conn:
            existing = _dispatch(conn, _SELECT_DISPATCH_KEY, dispatch_key)
            if existing is not None:
                if existing.kind == kind and existing.declared_digest == digest:
                    return DispatchClaim(replayed=True, row=existing)
                raise DispatchConflict(dispatch_key, existing.remote_id)
            row = DispatchRow(
                dispatch_key=dispatch_key,
                kind=kind,
                remote_id=remote_id,
                declared_digest=digest,
                declaration_json=declaration_bytes.decode("utf-8"),
                status="dispatched",
                created_at=stamp,
                updated_at=stamp,
                contract_version=contract_version,
                input_digest=_declaration_text(declaration_dict, "input_digest"),
                config_sha256=_declaration_text(declaration_dict, "config_sha256"),
                source_sha256=_declaration_text(declaration_dict, "source_sha256"),
                run_dir=_declaration_text(declaration_dict, "run_dir"),
            )
            conn.execute(
                "INSERT INTO execution_dispatches (dispatch_key, kind, remote_id, declared_digest, "
                "declaration_json, input_digest, config_sha256, source_sha256, run_dir, status, "
                "created_at, updated_at, contract_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'dispatched', ?, ?, ?)",
                (
                    dispatch_key,
                    kind,
                    remote_id,
                    digest,
                    row.declaration_json,
                    row.input_digest,
                    row.config_sha256,
                    row.source_sha256,
                    row.run_dir,
                    stamp,
                    stamp,
                    contract_version,
                ),
            )
            return DispatchClaim(replayed=False, row=row)

    def get_dispatch(self, dispatch_key: str) -> DispatchRow | None:
        """Return a dispatch by key, or ``None`` only for genuine absence."""
        return self._read_one(
            "get_dispatch", _SELECT_DISPATCH_KEY, (dispatch_key,), DispatchRow.from_row
        )

    def get_dispatch_by_remote_id(self, remote_id: str) -> DispatchRow | None:
        """Return a dispatch by unique remote identity."""
        return self._read_one(
            "get_dispatch_by_remote_id",
            _SELECT_DISPATCH_REMOTE,
            (remote_id,),
            DispatchRow.from_row,
        )

    def begin_start(
        self,
        dispatch_key: str,
        remote_id: str,
        start_declaration: Mapping[str, JsonValue],
        contract_version: str,
        *,
        now_ms: int | None = None,
    ) -> StartClaim:
        """Bind one complete start declaration, replaying only its exact digest."""
        operation = "begin_start"
        _text("dispatch_key", dispatch_key)
        _text("remote_id", remote_id)
        _text("contract_version", contract_version)
        declaration = dict(start_declaration)
        declaration["contract_version"] = contract_version
        declaration_bytes = canonical_json_bytes(declaration)
        start_json = declaration_bytes.decode("utf-8")
        digest = sha256_hex(declaration_bytes)
        stamp = _epoch_ms(now_ms)
        with self._transaction(operation) as conn:
            row = _required_dispatch(conn, dispatch_key)
            if row.remote_id != remote_id:
                raise DispatchConflict(dispatch_key, row.remote_id)
            if row.start_digest is not None:
                if row.start_digest == digest:
                    return StartClaim(True, row, digest)
                raise DispatchConflict(dispatch_key, row.remote_id)
            cursor = conn.execute(
                "UPDATE execution_dispatches SET start_declaration_json = ?, start_digest = ?, "
                "started_at = COALESCE(started_at, ?), updated_at = ? WHERE dispatch_key = ? "
                "AND start_digest IS NULL AND status = 'dispatched'",
                (start_json, digest, stamp, stamp, dispatch_key),
            )
            if cursor.rowcount == 1:
                row = _required_dispatch(conn, dispatch_key)
                return StartClaim(False, row, digest)
            row = _required_dispatch(conn, dispatch_key)
            if row.start_digest == digest:
                return StartClaim(True, row, digest)
            raise DispatchConflict(dispatch_key, row.remote_id)

    def record_worker(
        self,
        dispatch_key: str,
        pid: int,
        pgid: int,
        worker_meta: Mapping[str, JsonValue],
        *,
        now_ms: int | None = None,
    ) -> DispatchRow:
        """Persist worker identity only while the dispatch is nonterminal."""
        operation = "record_worker"
        _positive_int("pid", pid)
        _positive_int("pgid", pgid)
        metadata = canonical_json_bytes(dict(worker_meta)).decode("utf-8")
        stamp = _epoch_ms(now_ms)
        with self._transaction(operation) as conn:
            row = _required_dispatch(conn, dispatch_key)
            _require_dispatched(row, "record_worker")
            cursor = conn.execute(
                "UPDATE execution_dispatches SET worker_pid = ?, worker_pgid = ?, worker_meta_json = ?, "
                "started_at = COALESCE(started_at, ?), updated_at = ? "
                "WHERE dispatch_key = ? AND status = 'dispatched'",
                (pid, pgid, metadata, stamp, stamp, dispatch_key),
            )
            _require_cas(row, "record_worker", cursor.rowcount)
            return replace(
                row,
                worker_pid=pid,
                worker_pgid=pgid,
                worker_meta_json=metadata,
                started_at=row.started_at if row.started_at is not None else stamp,
                updated_at=stamp,
            )

    def mark_dispatch_terminal(
        self,
        dispatch_key: str,
        status: str,
        error_kind: str | None = None,
        result_path: str | None = None,
        *,
        failure_reason: str | None = None,
        now_ms: int | None = None,
    ) -> DispatchRow:
        """CAS ``dispatched`` into exactly one immutable terminal state."""
        operation = "mark_dispatch_terminal"
        if status not in _TERMINAL_STATUSES:
            raise DispatchTransitionError(
                dispatch_key, None, status, "unsupported terminal status"
            )
        if status == "failed" and (
            not isinstance(error_kind, str) or not error_kind.strip()
        ):
            raise DispatchTransitionError(
                dispatch_key, None, status, "failed requires non-empty error_kind"
            )
        if status != "failed" and error_kind is not None:
            raise DispatchTransitionError(
                dispatch_key, None, status, "only failed accepts error_kind"
            )
        if failure_reason is not None:
            if status != "failed":
                raise DispatchTransitionError(
                    dispatch_key, None, status, "only failed accepts failure_reason"
                )
            if not isinstance(failure_reason, str) or not failure_reason.strip():
                raise ContractValidationError(
                    "failure_reason", "must be a non-empty string"
                )
            if len(failure_reason) > FAILURE_REASON_LIMIT:
                raise ContractValidationError(
                    "failure_reason", "must be at most 500 characters"
                )
        stamp = _epoch_ms(now_ms)
        with self._transaction(operation) as conn:
            row = _required_dispatch(conn, dispatch_key)
            _require_dispatched(row, status)
            cursor = conn.execute(
                "UPDATE execution_dispatches SET status = ?, error_kind = ?, result_path = ?, "
                "failure_reason = ?, terminal_at = ?, updated_at = ? "
                "WHERE dispatch_key = ? AND status = 'dispatched'",
                (
                    status,
                    error_kind,
                    result_path,
                    failure_reason,
                    stamp,
                    stamp,
                    dispatch_key,
                ),
            )
            _require_cas(row, status, cursor.rowcount)
            return replace(
                row,
                status=status,
                error_kind=error_kind,
                result_path=result_path,
                failure_reason=failure_reason,
                terminal_at=stamp,
                updated_at=stamp,
            )

    def begin_stop(
        self,
        dispatch_key: str,
        remote_id: str,
        stop_key: str,
        deadline_at: int,
        contract_version: str,
        *,
        now_ms: int | None = None,
    ) -> StopClaim:
        """Atomically bind and replay the complete stop identity tuple."""
        operation = "begin_stop"
        for name, value in (
            ("dispatch_key", dispatch_key),
            ("remote_id", remote_id),
            ("stop_key", stop_key),
            ("contract_version", contract_version),
        ):
            _text(name, value)
        deadline = _epoch_ms(deadline_at)
        stamp = _epoch_ms(now_ms)
        with self._transaction(operation) as conn:
            dispatch = _dispatch(conn, _SELECT_DISPATCH_REMOTE, remote_id)
            if dispatch is None:
                raise DispatchNotFound(remote_id=remote_id)
            if dispatch.dispatch_key != dispatch_key:
                original = _dispatch(conn, _SELECT_DISPATCH_KEY, dispatch_key)
                original_remote_id = (
                    original.remote_id if original is not None else remote_id
                )
                raise StopConflict(stop_key, dispatch_key, original_remote_id)
            existing = _stop(conn, stop_key)
            if existing is not None:
                same = (
                    existing.dispatch_key == dispatch_key
                    and existing.remote_id == remote_id
                    and existing.deadline_at == deadline
                    and existing.contract_version == contract_version
                )
                if same:
                    return StopClaim(replayed=True, row=existing)
                raise StopConflict(stop_key, dispatch_key, existing.remote_id)
            resolved = conn.execute(
                "SELECT * FROM execution_stops WHERE dispatch_key = ? AND state != 'requested' "
                "ORDER BY state = 'confirmed' DESC, stop_key ASC LIMIT 1",
                (dispatch_key,),
            ).fetchone()
            if resolved is not None:
                return StopClaim(replayed=True, row=StopRow.from_row(resolved))
            row = StopRow(
                stop_key=stop_key,
                dispatch_key=dispatch_key,
                remote_id=remote_id,
                state="requested",
                requested_at=stamp,
                deadline_at=deadline,
                updated_at=stamp,
                contract_version=contract_version,
            )
            conn.execute(
                "INSERT INTO execution_stops (stop_key, dispatch_key, remote_id, state, requested_at, "
                "deadline_at, updated_at, contract_version) VALUES (?, ?, ?, 'requested', ?, ?, ?, ?)",
                (
                    stop_key,
                    dispatch_key,
                    remote_id,
                    stamp,
                    deadline,
                    stamp,
                    contract_version,
                ),
            )
            return StopClaim(replayed=False, row=row)

    def resolve_stop(
        self,
        stop_key: str,
        state: str,
        evidence_kind: str | None = None,
        evidence_payload: JsonValue = None,
        resolved_at: int | None = None,
    ) -> StopRow:
        """Resolve only ``requested``; confirmed is final and unknown is final."""
        operation = "resolve_stop"
        stamp = _epoch_ms(resolved_at)
        with self._transaction(operation) as conn:
            row = _stop(conn, stop_key)
            if row is None:
                raise StopTransitionError(stop_key, None, state, "stop does not exist")
            if row.state != "requested":
                raise StopTransitionError(
                    stop_key, row.state, state, "resolved stops are immutable"
                )
            if state not in ("confirmed", "unknown"):
                raise StopTransitionError(
                    stop_key, row.state, state, "unsupported resolution"
                )
            if state == "confirmed":
                if not _confirmation_evidence_ok(evidence_kind, evidence_payload):
                    raise StopTransitionError(
                        stop_key, row.state, state, "confirmed requires valid evidence"
                    )
                evidence_json = canonical_json_bytes(evidence_payload).decode("utf-8")
            else:
                if evidence_kind is not None or evidence_payload is not None:
                    raise StopTransitionError(
                        stop_key, row.state, state, "unknown carries no evidence"
                    )
                evidence_kind = None
                evidence_json = None
            cursor = conn.execute(
                "UPDATE execution_stops SET state = ?, resolved_at = ?, evidence_kind = ?, "
                "evidence_json = ?, updated_at = ? WHERE stop_key = ? AND state = 'requested'",
                (state, stamp, evidence_kind, evidence_json, stamp, stop_key),
            )
            if cursor.rowcount != 1:
                raise StopTransitionError(stop_key, row.state, state, "CAS lost")
            return replace(
                row,
                state=state,
                resolved_at=stamp,
                evidence_kind=evidence_kind,
                evidence_json=evidence_json,
                updated_at=stamp,
            )

    def get_stop(self, stop_key: str) -> StopRow | None:
        """Return one stop by its idempotency key."""
        return self._read_one("get_stop", _SELECT_STOP, (stop_key,), StopRow.from_row)

    def list_stops(self, remote_id: str) -> list[StopRow]:
        """List all stops for one remote identity in stable request order."""
        operation = "list_stops"
        conn = self._connection(operation)
        try:
            rows = conn.execute(
                "SELECT * FROM execution_stops WHERE remote_id = ? ORDER BY requested_at ASC, stop_key ASC",
                (remote_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ControlStoreError(operation, str(exc)) from exc
        finally:
            conn.close()
        return [StopRow.from_row(row) for row in rows]

    def lookup_legacy(
        self, *, run_key: str | None = None, vt_run_id: str | None = None
    ) -> RunRow | None:
        """Read, never write, one legacy strategy-run row by exactly one key."""
        if (run_key is None) == (vt_run_id is None):
            raise ContractValidationError(
                "legacy_lookup", "provide exactly one of run_key or vt_run_id"
            )
        if run_key is not None:
            return self._read_one(
                "lookup_legacy",
                "SELECT * FROM strategy_runs WHERE run_key = ?",
                (run_key,),
                RunRow.from_row,
            )
        return self._read_one(
            "lookup_legacy",
            "SELECT * FROM strategy_runs WHERE vt_run_id = ?",
            (vt_run_id,),
            RunRow.from_row,
        )

    def _connection(self, operation: str) -> sqlite3.Connection:
        try:
            return self._connect()
        except sqlite3.Error as exc:
            raise ControlStoreError(operation, str(exc)) from exc

    def _read_one(
        self,
        operation: str,
        sql: str,
        params: tuple[str, ...],
        factory: Callable[[sqlite3.Row], _T],
    ) -> _T | None:
        conn = self._connection(operation)
        try:
            row = conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise ControlStoreError(operation, str(exc)) from exc
        finally:
            conn.close()
        return None if row is None else factory(row)

    @contextlib.contextmanager
    def _transaction(self, operation: str) -> Iterator[sqlite3.Connection]:
        conn = self._connection(operation)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except ControlStoreError:
            _rollback(conn)
            raise
        except sqlite3.Error as exc:
            _rollback(conn)
            raise ControlStoreError(operation, str(exc)) from exc
        finally:
            conn.close()


def _dispatch(conn: sqlite3.Connection, sql: str, identity: str) -> DispatchRow | None:
    row = conn.execute(sql, (identity,)).fetchone()
    return None if row is None else DispatchRow.from_row(row)


def _required_dispatch(conn: sqlite3.Connection, dispatch_key: str) -> DispatchRow:
    row = _dispatch(conn, _SELECT_DISPATCH_KEY, dispatch_key)
    if row is None:
        raise DispatchNotFound(dispatch_key=dispatch_key)
    return row


def _stop(conn: sqlite3.Connection, stop_key: str) -> StopRow | None:
    row = conn.execute(_SELECT_STOP, (stop_key,)).fetchone()
    return None if row is None else StopRow.from_row(row)


def _require_dispatched(row: DispatchRow, target: str) -> None:
    if row.status != "dispatched":
        raise DispatchTransitionError(
            row.dispatch_key, row.status, target, "terminal rows are immutable"
        )


def _require_cas(row: DispatchRow, target: str, rowcount: int) -> None:
    if rowcount != 1:
        raise DispatchTransitionError(row.dispatch_key, row.status, target, "CAS lost")


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _text(field: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(field, "must be a non-empty string")


def _positive_int(field: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractValidationError(field, "must be a positive integer")


def _declaration_text(declaration: Mapping[str, JsonValue], field: str) -> str | None:
    value = declaration.get(field)
    if value is not None and not isinstance(value, str):
        raise ContractValidationError(field, "must be a string or null")
    return value


def _confirmation_evidence_ok(kind: str | None, payload: JsonValue) -> bool:
    """Return whether one confirmation carries an allowed evidence class.

    ``terminal_record`` is proven by the durable terminal row itself, so its
    metadata payload may be an empty object; every other class needs non-empty
    proof so a bare confirmation cannot claim an observation.
    """
    if kind not in STOP_EVIDENCE:
        return False
    if kind == "terminal_record":
        return payload is None or isinstance(payload, dict)
    return _has_evidence(payload)


def _has_evidence(payload: JsonValue) -> bool:
    match payload:
        case None:
            return False
        case str(text):
            return bool(text.strip())
        case list() | dict():
            return bool(payload)
        case int() | float() | bool():
            return False
        case unreachable:
            assert_never(unreachable)
