"""Schema, row types and shared helpers for the user-auth store (plan D3/D4).

Companion to ``user_store.py`` (the DAO), mirroring the repo's existing
``strategy_store.models`` + ``strategy_store.sqlite_store`` split. Lives
apart so the DAO stays inside the 400-line file budget (CONTRIBUTING.md).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    display_name  TEXT,
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_sha256 TEXT    PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    last_seen_at TEXT,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS invites (
    code_sha256 TEXT    PRIMARY KEY,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT    NOT NULL,
    expires_at  TEXT,
    max_uses    INTEGER NOT NULL DEFAULT 1,
    used_count  INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    """UTC ISO-8601, fixed format — lexicographic comparison stays valid."""
    return _now().isoformat()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_username(username: str) -> str:
    """Canonical storage/lookup form; EVERY insert/lookup must use it.

    Plan D4 / Oracle NB7: SQLite ``UNIQUE`` compares bytes, so ``Alice`` and
    ``alice`` would otherwise be two accounts — a visual-impersonation vector.
    The original casing is preserved separately in ``display_name``.
    """
    return username.strip().lower()


def _session_ttl_days() -> int:
    from src.config.accessor import get_env_config

    return get_env_config().api.vibe_trading_session_ttl_days


def _default_db_path() -> Path:
    """``VIBE_TRADING_USERS_DB_PATH`` override, else ``<runtime_root>/users.db``."""
    from src.config.accessor import get_env_config
    from src.config.paths import get_runtime_root

    raw = get_env_config().api.vibe_trading_users_db_path.strip()
    if raw:
        return Path(raw).expanduser()
    return get_runtime_root() / "users.db"


@dataclass(frozen=True)
class UserRecord:
    """One ``users`` row. ``password_hash`` never leaves the DAO in responses."""

    id: int
    username: str
    password_hash: str
    role: str
    is_active: bool
    display_name: Optional[str]
    created_at: str
    last_login_at: Optional[str]


@dataclass(frozen=True)
class SessionUser:
    """Identity carried by a validated session token (from the D1.1 JOIN)."""

    username: str
    role: str
    display_name: Optional[str]


class UsernameExistsError(Exception):
    """A user with the normalized username already exists."""


class UserNotFoundError(Exception):
    """No user matches the normalized username."""


class InviteInvalidError(Exception):
    """The invite code is unknown, expired, or exhausted."""


def _user_from_row(row: sqlite3.Row) -> UserRecord:
    data = dict(row)
    data["is_active"] = bool(data["is_active"])
    return UserRecord(**data)
