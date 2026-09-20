"""SQLite DAO for the opt-in user-auth layer: users / sessions / invites.

Schema/row types: ``user_store_schema.py`` (the repo's ``strategy_store``
models/DAO split); connection posture copies ``sqlite_store.py`` (plan D3).
Zero new dependencies. Security invariants:

* Sessions store ``sha256(token)`` only, invites ``sha256(code)`` only (D1/D9).
* Validation JOINs ``users`` on ``is_active = 1`` (D1.1, Oracle B5): CASCADE
  misses ``deactivate`` (row survives, ``is_active=0``) — without the JOIN a
  deactivated user's sessions would live out the full TTL. ``role`` rides the
  same query: per-request fresh, zero extra queries, no stale window.
* Sliding renewal writes at most once per 60s per session (Oracle NB#2).
* Invite consumption + user INSERT run in ONE ``BEGIN IMMEDIATE`` transaction
  with a conditional UPDATE (D9): race-proof single-use codes; a failed
  registration rolls the consumption back.

Pure DAO: never reads the auth flag, never builds HTTP responses. Callers
gate on ``security.user_auth_enabled()`` so flag=0 issues zero SQLite queries.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.api.user_store_schema import (
    InviteInvalidError,
    SessionUser,
    UsernameExistsError,
    UserNotFoundError,
    UserRecord,
    _SCHEMA_SQL,
    _SCHEMA_VERSION,
    _default_db_path,
    _now,
    _now_iso,
    _session_ttl_days,
    _sha256_hex,
    _user_from_row,
    normalize_username,
)
from src.session.models import AuthMethod, Principal

#: Minimum seconds between sliding-renewal writes for one session (D1.1/NB#2).
_RENEWAL_THROTTLE_SECONDS = 60.0

#: The exact validation query frozen by plan D1.1 (rationale in the module
#: docstring): ``u.is_active = 1`` makes ``deactivate`` immediate, and ``role``
#: comes from the SAME query so authorization never reads a stale copy.
_SESSION_VALIDATE_SQL = """
    SELECT u.username, u.role, u.display_name, s.expires_at, s.last_seen_at
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token_sha256 = ?
      AND s.expires_at > ?
      AND u.is_active = 1
"""


class UserStore:
    """DAO for ``users`` / ``sessions`` / ``invites`` (``user_version=1``)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.RLock()
        self._init_db()
        self._harden_file_permissions()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < _SCHEMA_VERSION:
                self._conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._conn.commit()

    def _harden_file_permissions(self) -> None:
        """Best-effort 0600 (plan D3): the file holds password/token hashes.

        On platforms where chmod is meaningless (Windows) this is a no-op —
        the platform-guarded posture used elsewhere in the repo.
        """
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:  # pragma: no cover — non-POSIX / exotic filesystem
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        role: str = "user",
        display_name: Optional[str] = None,
    ) -> UserRecord:
        """Insert a user directly (admin CLI path — no invite required)."""
        if role not in {"user", "admin"}:
            raise ValueError(f"role must be 'user' or 'admin', got {role!r}")
        normalized = normalize_username(username)
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO users (username, password_hash, role, "
                    "is_active, display_name, created_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (normalized, password_hash, role, display_name, _now_iso()),
                )
            except sqlite3.IntegrityError as exc:
                raise UsernameExistsError(normalized) from exc
            self._conn.commit()
        return self._require_user(normalized)

    def register_with_invite(
        self,
        username: str,
        password_hash: str,
        display_name: Optional[str],
        invite_code: str,
    ) -> UserRecord:
        """Consume an invite and create the user in ONE transaction (plan D9).

        The conditional UPDATE (``used_count < max_uses`` + expiry) makes a
        single-use code race-proof; a failing user INSERT (duplicate name)
        rolls the consumption back, so the invite stays usable.

        Raises:
            InviteInvalidError: Code unknown, expired, or exhausted.
            UsernameExistsError: Normalized username already taken.
        """
        normalized = normalize_username(username)
        now_iso = _now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                consumed = self._conn.execute(
                    "UPDATE invites SET used_count = used_count + 1 "
                    "WHERE code_sha256 = ? AND used_count < max_uses "
                    "AND (expires_at IS NULL OR expires_at > ?)",
                    (_sha256_hex(invite_code), now_iso),
                )
                if consumed.rowcount != 1:
                    raise InviteInvalidError(
                        "invite code is invalid, expired, or exhausted"
                    )
                try:
                    self._conn.execute(
                        "INSERT INTO users (username, password_hash, role, "
                        "is_active, display_name, created_at) "
                        "VALUES (?, ?, 'user', 1, ?, ?)",
                        (normalized, password_hash, display_name, now_iso),
                    )
                except sqlite3.IntegrityError as exc:
                    raise UsernameExistsError(normalized) from exc
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
        return self._require_user(normalized)

    def _require_user(self, normalized: str) -> UserRecord:
        user = self.get_user(normalized)
        if user is None:  # pragma: no cover — the INSERT just committed
            raise UserNotFoundError(normalized)
        return user

    def get_user(self, username: str) -> Optional[UserRecord]:
        normalized = normalize_username(username)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ?", (normalized,)
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [_user_from_row(row) for row in rows]

    def set_password(self, username: str, password_hash: str) -> None:
        """Raises UserNotFoundError when no such user exists."""
        self._update_user_or_raise(username, "password_hash", password_hash)

    def set_active(self, username: str, is_active: bool) -> None:
        """Deactivation kills outstanding sessions via the D1.1 JOIN — no
        separate purge needed. Raises UserNotFoundError when unknown."""
        self._update_user_or_raise(username, "is_active", 1 if is_active else 0)

    def _update_user_or_raise(self, username: str, column: str, value: object) -> None:
        # Whitelist-guarded identifier interpolation: only these two literal
        # column names can ever reach the SQL string.
        if column not in {"password_hash", "is_active"}:  # pragma: no cover
            raise ValueError(f"refusing to update unexpected column {column!r}")
        normalized = normalize_username(username)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE users SET {column} = ? WHERE username = ?",
                (value, normalized),
            )
            if cursor.rowcount != 1:
                raise UserNotFoundError(normalized)
            self._conn.commit()

    def touch_last_login(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (_now_iso(), user_id),
            )
            self._conn.commit()

    def create_session(self, user_id: int, *, user_agent: Optional[str] = None) -> str:
        """Mint a session; return the PLAINTEXT token (shown exactly once).

        Only ``sha256(token)`` is persisted; expired rows are swept in the
        same call (opportunistic hygiene, no background job).
        """
        token = secrets.token_urlsafe(32)
        now = _now()
        expires_at = (now + timedelta(days=_session_ttl_days())).isoformat()
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            self._conn.execute(
                "INSERT INTO sessions (token_sha256, user_id, created_at, "
                "expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
                (_sha256_hex(token), user_id, now.isoformat(), expires_at, user_agent),
            )
            self._conn.commit()
        return token

    def validate_session_token(self, token: str) -> Optional[SessionUser]:
        """Validate via the D1.1 JOIN; renew when past the throttle window.

        An empty token returns None WITHOUT touching the DB, so the
        flag-gated auth branch stays query-free for credential-less requests.
        """
        if not token:
            return None
        now = _now()
        with self._lock:
            row = self._conn.execute(
                _SESSION_VALIDATE_SQL, (_sha256_hex(token), now.isoformat())
            ).fetchone()
            if row is None:
                return None
            self._maybe_renew_session(token, row, now)
            return SessionUser(
                username=row["username"],
                role=row["role"],
                display_name=row["display_name"],
            )

    def _maybe_renew_session(self, token: str, row: sqlite3.Row, now: datetime) -> None:
        last_seen = row["last_seen_at"]
        if last_seen is not None:
            try:
                elapsed = (now - datetime.fromisoformat(last_seen)).total_seconds()
            except ValueError:
                elapsed = _RENEWAL_THROTTLE_SECONDS + 1.0  # corrupt stamp ⇒ renew
            if elapsed <= _RENEWAL_THROTTLE_SECONDS:
                return
        expires_at = (now + timedelta(days=_session_ttl_days())).isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? "
            "WHERE token_sha256 = ?",
            (now.isoformat(), expires_at, _sha256_hex(token)),
        )
        self._conn.commit()

    def revoke_session(self, token: str) -> None:
        """Revoke one session by plaintext token (logout). Idempotent."""
        if not token:
            return
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE token_sha256 = ?", (_sha256_hex(token),)
            )
            self._conn.commit()

    def revoke_other_sessions(self, user_id: int, *, keep_token: str) -> int:
        """Revoke all of a user's sessions except *keep_token* (pw change)."""
        keep_sha = _sha256_hex(keep_token) if keep_token else ""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_sha256 != ?",
                (user_id, keep_sha),
            )
            self._conn.commit()
            return cursor.rowcount

    def revoke_sessions(self, username: Optional[str] = None) -> int:
        """Revoke every session (None) or one user's (CLI revoke-sessions)."""
        with self._lock:
            if username is None:
                cursor = self._conn.execute("DELETE FROM sessions")
            else:
                cursor = self._conn.execute(
                    "DELETE FROM sessions WHERE user_id = "
                    "(SELECT id FROM users WHERE username = ?)",
                    (normalize_username(username),),
                )
            self._conn.commit()
            return cursor.rowcount

    def create_invite(
        self,
        *,
        created_by: Optional[int] = None,
        max_uses: int = 1,
        expires_at: Optional[str] = None,
        note: Optional[str] = None,
    ) -> str:
        """Mint an invite; return the PLAINTEXT code (shown exactly once).

        Only ``sha256(code)`` is persisted, so a DB leak cannot hand out
        registration capacity.
        """
        if max_uses < 1:
            raise ValueError("max_uses must be >= 1")
        code = secrets.token_urlsafe(16)
        with self._lock:
            self._conn.execute(
                "INSERT INTO invites (code_sha256, created_by, created_at, "
                "expires_at, max_uses, used_count, note) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (_sha256_hex(code), created_by, _now_iso(), expires_at, max_uses, note),
            )
            self._conn.commit()
        return code


# ---------------------------------------------------------------------------
# Process-wide singleton + auth-layer bridge
# ---------------------------------------------------------------------------

_store: Optional[UserStore] = None
_store_lock = threading.Lock()


def get_user_store() -> UserStore:
    """Lazily-built singleton — constructed on first USE, never at import or
    app-assembly time, so a flag=0 deployment issues zero SQLite queries and
    creates no database file."""
    global _store  # noqa: PLW0603
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            _store = UserStore()
    return _store


def reset_user_store() -> None:
    """Close and drop the singleton (tests, or after a DB-path change)."""
    global _store  # noqa: PLW0603
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None


def principal_for_session_token(token: str) -> Optional[Principal]:
    """The single DAO→security bridge: ``USER_SESSION`` principal or None.

    Called by the flag-gated step-2 branch of ``_validate_api_auth``; a None
    result falls through to the unchanged shared-key/loopback precedence
    (GHSA-7wgj). ``tenant=username`` is the Phase-2 isolation seam — nothing
    filters on it this round (shared-workspace decision).
    """
    user = get_user_store().validate_session_token(token)
    if user is None:
        return None
    return Principal(
        subject=user.username,
        auth_method=AuthMethod.USER_SESSION,
        tenant=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
    )
