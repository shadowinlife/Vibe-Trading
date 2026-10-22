"""Shared layout, refusals and path guards for the per-bar intraday store (P1).

``bar_layout`` is the package-internal half of the v1.3 bar store: it owns the
canonical file names, the refusal codes, the identity validators (account id,
trade date, bar label, injected bar time, sha256 digests) and every path guard
used by :mod:`~.bar_store` and :mod:`~.session_summary`.  It writes no ledger
document itself; one set of guards means a symlink or an escape attempt is
refused identically on the write and the read path.

``<account_root>`` is the account directory itself
(``AccountStore.account_dir(account_id)``), so every path is scoped by
construction; each is re-checked after ``Path.resolve()`` anyway, because a
symlink can move a descendant elsewhere.

Nothing in this module reads a clock: ``bar_time`` is the caller's injected
instant, normalized to the bar's ``+08:00`` spelling, and every document this
package writes carries that instant plus content hashes only.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import bar_clock
from .account import AccountError, require_date, validate_account_id
from .storage_io import (
    BARS_DIRNAME,
    MANIFEST_FILE,
    STEP_FILE,
    STEPS_DIRNAME,
    StoreError,
    _fsync_dir,
)

__all__ = [
    "BAR_STEP_FORMAT",
    "BARS_DIRNAME",
    "EVENTS_FILE",
    "FILLS_FILE",
    "MANIFEST_FILE",
    "ORDERS_FILE",
    "ORDER_EVENTS_FILE",
    "POSITIONS_FILE",
    "SESSION_FILE",
    "SESSION_FORMAT",
    "SESSION_STATUSES",
    "STEP_FILE",
    "BarStepCorrupt",
    "BarStepExists",
    "BarStepNotFound",
    "BarStepPaths",
    "BarStoreError",
    "InvalidBarLabel",
    "SessionCorrupt",
    "SessionNotFound",
    "UnsafeBarPath",
    "bar_step_paths",
    "ensure_dir",
    "guard_existing_dir",
    "normalize_bar_time",
    "require_bar_label",
    "require_containment",
    "require_counts",
    "require_hash",
    "resolve_safely",
    "session_summary_path",
    "validated_account",
]

# The store-level file names are re-exported so every layout token has ONE home.
#: Version of the per-bar step document == the manifest's ``format`` field.
#: A reader refuses anything else, so a future layout cannot be misread as
#: this one.
BAR_STEP_FORMAT = "paper-trade-bar-step-v1"
#: Version of the ``session.json`` summary document.
SESSION_FORMAT = "paper-trade-session-v1"

ORDERS_FILE = "orders.jsonl"
FILLS_FILE = "fills.jsonl"
ORDER_EVENTS_FILE = "order_events.jsonl"
POSITIONS_FILE = "positions.jsonl"
EVENTS_FILE = "events.jsonl"
SESSION_FILE = "session.json"

#: ``session.json`` status vocabulary: ``open`` while bars remain, ``closed``
#: once the session is finished (the replay writes the final value).
SESSION_STATUSES: tuple[str, ...] = ("open", "closed")

_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COUNT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Keys that would smuggle a wall clock into an artefact.
CLOCK_KEYS = frozenset({"applied_at", "created_at", "now", "timestamp", "updated_at"})


# ── refusals ──


class BarStoreError(StoreError):
    """Base refusal for the per-bar layout; the subclass ``code`` is the contract.

    Codes: ``invalid_account`` / ``invalid_trade_date`` (mirrors the account
    guards), ``invalid_bar_label``, ``invalid_bar_time``, ``invalid_hash``,
    ``invalid_bar_symbol``, ``invalid_session``, ``account_not_found``,
    ``bar_step_exists``, ``bar_step_not_found``, ``bar_step_corrupt``,
    ``session_not_found``, ``session_corrupt``, ``bar_store_unsafe_path``.
    """


class BarStepExists(BarStoreError):
    def __init__(self, trade_date: str, bar_label: str) -> None:
        super().__init__(
            "bar_step_exists",
            f"bar step {bar_label} on {trade_date} already exists; a bar step is evidence "
            "and is never overwritten",
        )


class BarStepNotFound(BarStoreError):
    def __init__(self, trade_date: str, bar_label: str) -> None:
        super().__init__(
            "bar_step_not_found", f"no bar step {bar_label} on {trade_date} in this account"
        )


class BarStepCorrupt(BarStoreError):
    def __init__(self, message: str) -> None:
        super().__init__("bar_step_corrupt", message)


class SessionNotFound(BarStoreError):
    def __init__(self, trade_date: str) -> None:
        super().__init__("session_not_found", f"no session summary for {trade_date}")


class SessionCorrupt(BarStoreError):
    def __init__(self, message: str) -> None:
        super().__init__("session_corrupt", message)


class UnsafeBarPath(BarStoreError):
    def __init__(self, message: str) -> None:
        super().__init__("bar_store_unsafe_path", message)


class InvalidBarLabel(BarStoreError):
    def __init__(self, bar_label: Any) -> None:
        super().__init__(
            "invalid_bar_label",
            f"bar_label must be one of the 48 continuous HH:MM session bars, got {bar_label!r}",
        )


# ── identity validators ──


def require_bar_label(bar_label: Any) -> str:
    """Return ``bar_label`` if it is one of :data:`bar_clock.BAR_LABELS`."""
    if not isinstance(bar_label, str) or _LABEL_RE.fullmatch(bar_label) is None:
        raise InvalidBarLabel(bar_label)
    if bar_label not in bar_clock.BAR_LABELS:
        raise InvalidBarLabel(bar_label)
    return bar_label


def normalize_bar_time(value: Any, trade_date: str, bar_label: str) -> str:
    """Return the ``+08:00`` spelling of ``value`` after checking it names this bar.

    A naive stamp names no instant (the host timezone would silently decide
    which bar it means), and a stamp whose Shanghai wall clock is not the
    step's ``bar_label`` would let one bar's rows be filed under another.
    """
    if not isinstance(value, str) or not value:
        raise BarStoreError("invalid_bar_time", f"bar_time must be a non-empty string, got {value!r}")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise BarStoreError("invalid_bar_time", f"bar_time must be ISO-8601, got {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BarStoreError(
            "invalid_bar_time", f"bar_time must carry an explicit UTC offset, got {value!r}"
        )
    local = parsed.astimezone(bar_clock.SHANGHAI_TZ)
    if local.date().isoformat() != trade_date or local.strftime("%H:%M") != bar_label:
        raise BarStoreError(
            "invalid_bar_time",
            f"bar_time {value!r} is not {bar_label} on {trade_date} in Asia/Shanghai",
        )
    return local.isoformat(timespec="seconds")


def require_hash(value: Any, path: str) -> str:
    """Return ``value`` if it is a lowercase sha256 hex digest."""
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise BarStoreError(
            "invalid_hash", f"{path} must be a lowercase sha256 hex digest, got {value!r}"
        )
    return value


def require_counts(counts: Mapping[str, int] | None) -> dict[str, int]:
    """Validate an optional ``name -> non-negative int`` counter map."""
    payload: dict[str, int] = {}
    for key, value in (counts or {}).items():
        if not isinstance(key, str) or _COUNT_KEY_RE.fullmatch(key) is None:
            raise BarStoreError("invalid_session", f"counts key {key!r} is not a safe token")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BarStoreError(
                "invalid_session", f"counts[{key!r}] must be a non-negative integer, got {value!r}"
            )
        payload[key] = value
    return payload


# ── paths ──


@dataclass(frozen=True, slots=True)
class BarStepPaths:
    """The paths of one bar step, rooted at the account directory.

    Built by :func:`bar_step_paths`, which validates every segment before a
    single path is returned.  This object only NAMES locations; it never
    creates, reads or mutates them.
    """

    account_root: Path
    account_id: str
    trade_date: str
    bar_label: str

    @property
    def steps_dir(self) -> Path:
        return self.account_root / STEPS_DIRNAME

    @property
    def date_dir(self) -> Path:
        return self.steps_dir / self.trade_date

    @property
    def bars_root(self) -> Path:
        return self.date_dir / BARS_DIRNAME

    @property
    def bar_dir(self) -> Path:
        return self.bars_root / self.bar_label.replace(":", "")

    @property
    def session_path(self) -> Path:
        return self.date_dir / SESSION_FILE


def validated_account(
    account_root: str | Path, account_id: str, trade_date: str
) -> tuple[Path, str, str]:
    """Validate the three identity inputs and return ``(root, account, day)``.

    Reuses :func:`~.account.validate_account_id` / :func:`~.account.require_date`
    — the same guards the v1.2 store applies — so an account id with a
    separator, a ``..`` segment or a leading dot is refused before it can name
    a directory.  ``AccountError`` is re-raised as a :class:`BarStoreError` so
    this package has one refusal family.
    """
    try:
        account = validate_account_id(account_id)
    except AccountError as exc:
        raise BarStoreError(exc.code, exc.message) from exc
    try:
        day = require_date(trade_date, "trade_date")
    except AccountError as exc:
        raise BarStoreError("invalid_trade_date", exc.message) from exc
    root = Path(account_root).expanduser()
    if root.is_symlink():
        raise UnsafeBarPath(f"account root {root.name!r} is a symbolic link; refusing to follow it")
    if not root.is_dir():
        raise BarStoreError(
            "account_not_found", f"no paper-trade account directory {root.name!r} in this store"
        )
    if root.name != account:
        raise UnsafeBarPath(
            f"account root {root.name!r} does not belong to account {account!r}; "
            "the store keeps one directory per account id"
        )
    return root, account, day


def bar_step_paths(
    account_root: str | Path, *, account_id: str, trade_date: str, bar_label: str
) -> BarStepPaths:
    """Validate identity + label and return the step's paths (no I/O beyond stat)."""
    root, account, day = validated_account(account_root, account_id, trade_date)
    return BarStepPaths(root, account, day, require_bar_label(bar_label))


def session_summary_path(
    account_root: str | Path, *, account_id: str, trade_date: str
) -> Path:
    """Return ``steps/<date>/session.json`` after validating the identity inputs."""
    root, _, day = validated_account(account_root, account_id, trade_date)
    return root / STEPS_DIRNAME / day / SESSION_FILE


# ── path guards ──


def require_containment(path: Path, account_root: Path, what: str) -> None:
    """Refuse ``path`` unless it resolves to the root or below it."""
    if path != account_root and account_root not in path.parents:
        raise UnsafeBarPath(f"{what} resolves outside the account root; refusing to use it")


def resolve_safely(path: Path, what: str) -> Path:
    """``Path.resolve()`` with a symlink-loop / permission failure reported as a refusal."""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise UnsafeBarPath(f"{what} cannot be resolved safely: {exc}") from exc


def ensure_dir(path: Path, *, account_root: Path, what: str) -> None:
    """Create ``path`` (mode 0700) or verify it is a real directory inside the root."""
    if path.is_symlink():
        raise UnsafeBarPath(f"{what} is a symbolic link; refusing to follow it")
    require_containment(resolve_safely(path, what), account_root, what)
    if path.exists():
        if not path.is_dir():
            raise BarStepCorrupt(f"{what} exists but is not a directory")
        return
    path.mkdir(mode=0o700)
    # Persist the new dirent in its parent, not just the directory itself.
    _fsync_dir(path.parent)


def guard_existing_dir(
    path: Path, *, account_root: Path, what: str, missing: BarStoreError
) -> None:
    """Guard a directory a reader is about to traverse (never creates anything)."""
    if path.is_symlink():
        raise UnsafeBarPath(f"{what} is a symbolic link; refusing to follow it")
    if not path.exists():
        raise missing
    if not path.is_dir():
        raise BarStepCorrupt(f"{what} exists but is not a directory")
    require_containment(resolve_safely(path, what), account_root, what)
