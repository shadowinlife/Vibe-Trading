"""SQLite execution ledger that makes a duplicate POST execute at most once.

``run_key`` is opaque. The caller builds it (RM convention:
``user_id:schedule_id:scheduled_for:attempt``) and this store never splits,
trims, normalises, parses or hashes it — two identical strings are the same
requested execution, and two different strings (a new ``attempt``) are two.

Idempotency is therefore a property of the file, not of any process's memory:

* ``run_key`` is ``NOT NULL UNIQUE``, so a second row for the same key cannot
  exist no matter who inserts it.
* :meth:`StrategyRunStore.claim` inserts inside ``BEGIN IMMEDIATE``, which takes
  SQLite's write lock before the read-modify-write, so two claimants are
  serialised rather than racing between a ``SELECT`` and an ``INSERT``. The
  loser's ``INSERT`` raises ``IntegrityError``; it then reads the winner's row
  and returns that ``vt_run_id`` with ``won=False``.
* Terminal writes are compare-and-swap on ``status='running'``, so a sweeper and
  a recovery pass cannot overwrite each other or a run that already finished.

Consequence for callers: only a ``won=True`` result may start a runner. A
``won=False`` result must not start anything, must not write a handle, and
should be answered with the existing ``vt_run_id`` (HTTP 409 upstream).

Rows are never deleted in P1: deleting a row would make an old ``run_key``
claimable again, re-running a schedule the ledger already settled.

The default path is ``<runtime root>/strategy_runs.sqlite3`` where the runtime
root comes from :func:`src.config.paths.get_runtime_root` — ``VIBE_TRADING_HOME``
when set, otherwise ``~/.vibe-trading``. It is resolved per instance (never at
import time) so a test or a second deployment can redirect it by environment.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.config.paths import get_runtime_root

__all__ = [
    "DEFAULT_DB_FILENAME",
    "STATUSES",
    "ClaimResult",
    "RunRow",
    "StrategyRunStore",
]

#: Filename of the ledger inside the runtime root.
DEFAULT_DB_FILENAME = "strategy_runs.sqlite3"

#: Terminal + in-flight states a row may hold.
STATUSES = ("running", "completed", "failed")

#: How long a writer waits for SQLite's lock before giving up, in milliseconds.
_DEFAULT_BUSY_TIMEOUT_MS = 5000

#: File mode for the ledger, the WAL and the SHM files.
_PRIVATE_MODE = 0o600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_runs (
    vt_run_id        TEXT PRIMARY KEY,
    run_key          TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    error            TEXT,
    error_kind       TEXT,
    strategy_id      TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    sandbox_id       TEXT,
    upstream         TEXT,
    timeout_s        INTEGER NOT NULL,
    handle_json      TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_status
    ON strategy_runs(status);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_created_at
    ON strategy_runs(created_at);
"""

_INSERT_SQL = """
INSERT INTO strategy_runs (
    vt_run_id, run_key, status, error, error_kind, strategy_id,
    strategy_version, sandbox_id, upstream, timeout_s, handle_json,
    created_at, updated_at
) VALUES (?, ?, 'running', NULL, NULL, ?, ?, ?, ?, ?, NULL, ?, ?)
"""

_SELECT_BY_RUN_KEY_SQL = "SELECT * FROM strategy_runs WHERE run_key = ?"
_SELECT_BY_ID_SQL = "SELECT * FROM strategy_runs WHERE vt_run_id = ?"
_SELECT_RUNNING_SQL = "SELECT * FROM strategy_runs WHERE status = 'running' ORDER BY created_at ASC, vt_run_id ASC"


@dataclass(frozen=True)
class RunRow:
    """One immutable ledger row.

    Attributes:
        vt_run_id: Opaque, unpredictable run identifier (``uuid4().hex``).
        run_key: The caller's idempotency key, stored byte-for-byte.
        status: ``running``, ``completed`` or ``failed``.
        strategy_id: Strategy the run executes.
        strategy_version: Pinned strategy version at claim time.
        timeout_s: Wall-clock budget in seconds; the sweeper decides expiry.
        created_at: Claim time, epoch milliseconds.
        updated_at: Last write time, epoch milliseconds.
        error: Bounded error text; ``None`` until a run fails.
        error_kind: Machine-readable failure class; ``None`` until a run fails.
        sandbox_id: Runner sandbox the claim was routed to, if any.
        upstream: Upstream identifier the run was requested from, if any.
        handle_json: Raw runner handle JSON (F9 five-key contract), or ``None``.
    """

    vt_run_id: str
    run_key: str
    status: str
    strategy_id: str
    strategy_version: str
    timeout_s: int
    created_at: int
    updated_at: int
    error: str | None = None
    error_kind: str | None = None
    sandbox_id: str | None = None
    upstream: str | None = None
    handle_json: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RunRow":
        """Build a :class:`RunRow` from one SQLite row."""
        return cls(
            vt_run_id=row["vt_run_id"],
            run_key=row["run_key"],
            status=row["status"],
            strategy_id=row["strategy_id"],
            strategy_version=row["strategy_version"],
            timeout_s=row["timeout_s"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error=row["error"],
            error_kind=row["error_kind"],
            sandbox_id=row["sandbox_id"],
            upstream=row["upstream"],
            handle_json=row["handle_json"],
        )


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of :meth:`StrategyRunStore.claim`.

    ``won=True`` means this caller inserted the row and may start the run.
    ``won=False`` means the key was already claimed and ``row`` is the existing
    ledger row — start nothing and report ``row.vt_run_id`` as the duplicate's
    owner.
    """

    won: bool
    row: RunRow


class StrategyRunStore:
    """SQLite ledger of ``run_key`` claims and run terminal states.

    Every operation opens its own short-lived connection, so an instance is safe
    to share between threads and a connection never outlives a request. The
    correctness guarantee comes from SQLite (``UNIQUE`` + ``BEGIN IMMEDIATE``),
    not from any in-process lock.

    Attributes:
        path: Absolute path of the ledger file.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Open (creating if needed) the ledger and ensure its schema.

        Args:
            path: Explicit ledger path. Defaults to
                ``<runtime root>/strategy_runs.sqlite3``, resolved now rather
                than at import time.
        """
        self.path: Path = (
            Path(path) if path is not None else get_runtime_root() / DEFAULT_DB_FILENAME
        )
        self.ensure_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table and indexes if they are missing.

        Idempotent: safe to call from every process and every start.
        """
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def claim(
        self,
        run_key: str,
        strategy_id: str,
        strategy_version: str,
        sandbox_id: str | None,
        upstream: str | None,
        timeout_s: int,
        now_ms: int | None = None,
    ) -> ClaimResult:
        """Claim ``run_key`` for execution, or report the existing claim.

        The winner of the ``UNIQUE`` constraint gets a fresh unpredictable
        ``vt_run_id`` and a ``running`` row whose ``created_at`` equals its
        ``updated_at``. A loser (duplicate key) inserts nothing and receives the
        winner's row.

        Args:
            run_key: Opaque idempotency key, stored verbatim. Not trimmed,
                parsed or truncated; only emptiness is rejected.
            strategy_id: Strategy to execute.
            strategy_version: Pinned version to execute.
            sandbox_id: Runner sandbox the claim is routed to, if any.
            upstream: Upstream identifier that requested the run, if any.
            timeout_s: Wall-clock budget in seconds, positive.
            now_ms: Epoch-millisecond claim time; defaults to the wall clock.
                Injectable so tests and callers can pin timestamps.

        Returns:
            ``ClaimResult(won=True)`` for the single winner, otherwise
            ``ClaimResult(won=False)`` carrying the pre-existing row.

        Raises:
            ValueError: On an empty or non-string ``run_key``/``strategy_id``/
                ``strategy_version``, a non-positive or non-integer
                ``timeout_s``, or a non-integer ``now_ms``.
        """
        _require_non_empty_str("run_key", run_key)
        _require_non_empty_str("strategy_id", strategy_id)
        _require_non_empty_str("strategy_version", strategy_version)
        _require_optional_str("sandbox_id", sandbox_id)
        _require_optional_str("upstream", upstream)
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, int)
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a positive integer")
        stamp = _epoch_ms(now_ms)

        vt_run_id = uuid.uuid4().hex
        params = (
            vt_run_id,
            run_key,
            strategy_id,
            strategy_version,
            sandbox_id,
            upstream,
            timeout_s,
            stamp,
            stamp,
        )

        conn = self._connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_INSERT_SQL, params)
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                _rollback_quietly(conn)
                existing = conn.execute(_SELECT_BY_RUN_KEY_SQL, (run_key,)).fetchone()
                if existing is None:
                    # Not the run_key uniqueness violation (e.g. a uuid4 PK
                    # collision): nothing to report as a duplicate.
                    raise
                return ClaimResult(won=False, row=RunRow.from_row(existing))
            except BaseException:
                _rollback_quietly(conn)
                raise
        finally:
            conn.close()

        return ClaimResult(
            won=True,
            row=RunRow(
                vt_run_id=vt_run_id,
                run_key=run_key,
                status="running",
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                timeout_s=timeout_s,
                created_at=stamp,
                updated_at=stamp,
                sandbox_id=sandbox_id,
                upstream=upstream,
            ),
        )

    def get(self, vt_run_id: str) -> RunRow | None:
        """Return the row for ``vt_run_id``, or ``None`` when unknown."""
        return self._fetch_one(_SELECT_BY_ID_SQL, (vt_run_id,))

    def get_by_run_key(self, run_key: str) -> RunRow | None:
        """Return the row claimed under ``run_key``, or ``None`` when unclaimed."""
        return self._fetch_one(_SELECT_BY_RUN_KEY_SQL, (run_key,))

    def list_running(self) -> list[RunRow]:
        """Return every ``running`` row, oldest claim first.

        Ordering is stable (``created_at``, then ``vt_run_id``) so a sweeper's
        pass is reproducible when several rows share a millisecond.
        """
        conn = self._connect()
        try:
            rows = conn.execute(_SELECT_RUNNING_SQL).fetchall()
        finally:
            conn.close()
        return [RunRow.from_row(row) for row in rows]

    def set_handle(
        self, vt_run_id: str, handle_json: str, now_ms: int | None = None
    ) -> bool:
        """Attach a runner handle while the row is still ``running``.

        Compare-and-swap on ``status='running'``: a handle can never be written
        onto a run that already reached a terminal state.

        Args:
            vt_run_id: Row to update.
            handle_json: Raw handle JSON; the store does not parse it.
            now_ms: Epoch-millisecond write time; defaults to the wall clock.

        Returns:
            ``True`` when exactly one ``running`` row was updated.

        Raises:
            ValueError: On a non-string or empty ``handle_json``/``vt_run_id``,
                or a non-integer ``now_ms``.
        """
        _require_non_empty_str("vt_run_id", vt_run_id)
        _require_non_empty_str("handle_json", handle_json)
        stamp = _epoch_ms(now_ms)
        return self._execute_write(
            "UPDATE strategy_runs SET handle_json = ?, updated_at = ? WHERE vt_run_id = ? AND status = 'running'",
            (handle_json, stamp, vt_run_id),
        )

    def mark_terminal(
        self,
        vt_run_id: str,
        status: str,
        error: str | None = None,
        error_kind: str | None = None,
        now_ms: int | None = None,
    ) -> bool:
        """Move a ``running`` row to ``completed`` or ``failed`` exactly once.

        Compare-and-swap on ``status='running'``: the first writer wins, every
        later writer (a second sweeper, a recovery pass, a duplicate poll)
        matches zero rows and returns ``False`` without touching the row.

        Args:
            vt_run_id: Row to finish.
            status: ``completed`` or ``failed``; ``running`` is rejected.
            error: Bounded error text. Must be ``None`` for ``completed`` and
                non-``None`` for ``failed``.
            error_kind: Machine-readable failure class. Same null-ness rule as
                ``error``.
            now_ms: Epoch-millisecond write time; defaults to the wall clock.

        Returns:
            ``True`` when this call performed the transition, ``False`` when the
            row was missing or already terminal.

        Raises:
            ValueError: On an unknown/rejected ``status`` or a mismatched
                ``error``/``error_kind`` pair.
        """
        _require_non_empty_str("vt_run_id", vt_run_id)
        if status not in ("completed", "failed"):
            raise ValueError(
                f"mark_terminal status must be 'completed' or 'failed', got {status!r}"
            )
        _require_optional_str("error", error)
        _require_optional_str("error_kind", error_kind)
        if status == "completed" and (error is not None or error_kind is not None):
            raise ValueError("a completed run must not carry error or error_kind")
        if status == "failed" and (error is None or error_kind is None):
            raise ValueError("a failed run requires both error and error_kind")
        stamp = _epoch_ms(now_ms)
        return self._execute_write(
            "UPDATE strategy_runs SET status = ?, error = ?, error_kind = ?, updated_at = ? "
            "WHERE vt_run_id = ? AND status = 'running'",
            (status, error, error_kind, stamp, vt_run_id),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived, WAL-mode, owner-only connection.

        ``busy_timeout`` is installed before ``journal_mode`` so a connection
        that has to wait for another writer's lock waits instead of failing
        immediately. The ledger file is chmod'ed to 0600 after creation; SQLite
        copies that mode onto the ``-wal``/``-shm`` siblings it creates.

        Returns:
            A connection in autocommit mode (``isolation_level=None``) with
            ``sqlite3.Row`` results, so ``BEGIN IMMEDIATE`` is explicit.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=_DEFAULT_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={_DEFAULT_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            conn.close()
            raise
        _chmod_private(self.path)
        return conn

    def _fetch_one(self, sql: str, params: tuple) -> RunRow | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        return None if row is None else RunRow.from_row(row)

    def _execute_write(self, sql: str, params: tuple) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            return cursor.rowcount == 1
        finally:
            conn.close()


def _chmod_private(path: Path) -> None:
    """Restrict *path* to its owner, ignoring platforms without POSIX modes."""
    with contextlib.suppress(OSError):
        os.chmod(path, _PRIVATE_MODE)


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back the open write transaction, tolerating an already-ended one."""
    with contextlib.suppress(sqlite3.Error):
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _epoch_ms(now_ms: int | None) -> int:
    """Return *now_ms* as an integer epoch-millisecond stamp."""
    if now_ms is None:
        return int(time.time() * 1000)
    if isinstance(now_ms, bool) or not isinstance(now_ms, int):
        raise ValueError("now_ms must be an integer epoch-millisecond timestamp")
    return now_ms


def _require_non_empty_str(name: str, value: object) -> None:
    """Reject a non-string or empty string, without altering it."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_optional_str(name: str, value: object) -> None:
    """Reject a non-string that is not ``None``; leave the value untouched."""
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or None, got {type(value).__name__}")
