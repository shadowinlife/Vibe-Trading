"""Atomic day summary for the per-bar intraday store (P1, GAP-5).

``steps/<date>/session.json`` is the ONLY mutable artefact of the v1.3 layout:
it advances as the session applies bars, so a write REPLACES the previous
document in place (temp file + ``os.replace`` + directory fsync) any number of
times.  The per-bar steps it summarizes under ``bars/<HHMM>/`` remain immutable
and are written by :mod:`~.bar_store`.

Determinism: the summary carries status, the first/last applied bar, the
watermark (the last applied bar), an applied-bar count, caller-supplied
counters and the head of the per-bar ``result_hash`` chain.  ``first_bar`` /
``last_bar`` / ``watermark_bar`` are DERIVED from the ordered ``applied_bars``
argument, so the document can never contradict its own list.  No wall clock is
read or stored — ``updated_at`` and friends do not exist here.

Additive v1+ ``final_state`` (P1-25)
------------------------------------
The original document only carried ledger SIZES inside ``counts`` (a position
count, an open-order count, cash in cents), which is enough for progress but
not for a caller that wants the closing ledger without opening ``account.json``.
:func:`write_session_summary` therefore accepts an OPTIONAL ``final_state``
mapping and emits it verbatim under ``"final_state"``::

    {"cash": 357434.18,
     "positions": [{"symbol": "600519.SH", "qty": 200.0, "avg_cost": 1421.14}],
     "open_orders": [{"order_id": ..., "status": "resting", ...}]}

Every existing writer keeps working (the key is simply absent), and
:func:`read_session_summary` validates the block only when it is present, so a
v1 document written before this change still reads.  The block is an
observation of the ledger, never an authority: ``account.json`` and the bar
steps stay the source of truth, and the summary remains re-derivable.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import bar_clock
from .bar_layout import (
    SESSION_FILE,
    SESSION_FORMAT,
    SESSION_STATUSES,
    STEPS_DIRNAME,
    BarStoreError,
    SessionCorrupt,
    SessionNotFound,
    UnsafeBarPath,
    ensure_dir,
    require_containment,
    require_counts,
    require_hash,
    resolve_safely,
    session_summary_path,
    validated_account,
)
from .storage_io import StoreError, atomic_write, dumps, read_json

__all__ = [
    "FINAL_STATE_KEYS",
    "SESSION_FILE",
    "SESSION_FORMAT",
    "SESSION_STATUSES",
    "SessionCorrupt",
    "SessionNotFound",
    "read_session_summary",
    "write_session_summary",
]

#: The three keys the additive ``final_state`` block always carries.
FINAL_STATE_KEYS: tuple[str, ...] = ("cash", "positions", "open_orders")

_BAR_INDEX = {label: position for position, label in enumerate(bar_clock.BAR_LABELS)}


def _final_state_payload(raw: Any) -> dict[str, Any]:
    """Validate and copy the additive closing-ledger block.

    ``cash`` must be a finite non-negative number (the engine refuses a
    negative ledger) and both row lists must hold mappings only.  The rows
    themselves are copied verbatim: the summary reports what the ledger says,
    it does not re-model a position or an order.
    """
    if not isinstance(raw, Mapping):
        raise BarStoreError(
            "invalid_session", f"final_state must be a mapping, got {type(raw).__name__}"
        )
    cash = raw.get("cash")
    if isinstance(cash, bool) or not isinstance(cash, (int, float)):
        raise BarStoreError("invalid_session", f"final_state.cash must be a number, got {cash!r}")
    if not math.isfinite(cash) or cash < 0:
        raise BarStoreError(
            "invalid_session", f"final_state.cash must be finite and >= 0, got {cash!r}"
        )
    payload: dict[str, Any] = {"cash": float(cash)}
    for key in FINAL_STATE_KEYS[1:]:
        rows = raw.get(key)
        if not isinstance(rows, (list, tuple)):
            raise BarStoreError("invalid_session", f"final_state.{key} must be an array")
        if any(not isinstance(row, Mapping) for row in rows):
            raise BarStoreError(
                "invalid_session", f"final_state.{key} rows must be JSON objects"
            )
        payload[key] = [dict(row) for row in rows]
    return payload


def _ordered_bars(applied_bars: Sequence[str]) -> tuple[str, ...]:
    """Validate an increasing run of canonical bar labels and return it as a tuple."""
    bars: list[str] = []
    for label in applied_bars:
        if not isinstance(label, str) or label not in _BAR_INDEX:
            raise BarStoreError(
                "invalid_session",
                f"applied_bars entries must be continuous session labels, got {label!r}",
            )
        bars.append(label)
    for previous, current in zip(bars, bars[1:]):
        if _BAR_INDEX[current] <= _BAR_INDEX[previous]:
            raise BarStoreError(
                "invalid_session",
                "applied_bars must be strictly increasing in session order; "
                f"{current} follows {previous}",
            )
    return tuple(bars)


def write_session_summary(
    account_root: str | Path,
    *,
    account_id: str,
    trade_date: str,
    status: str = "open",
    applied_bars: Sequence[str] = (),
    counts: Mapping[str, int] | None = None,
    result_hash_chain_head: str = "",
    final_state: Mapping[str, Any] | None = None,
) -> Path:
    """Replace ``steps/<date>/session.json`` atomically and return its path.

    The summary is re-derivable from the bar steps and may be replaced; the bar
    steps themselves are never overwritten.  ``result_hash_chain_head`` is the
    last applied bar's ``result_hash`` (empty when no bar has been applied).
    ``final_state`` is the optional additive closing-ledger block documented in
    the module docstring; omit it and the document keeps its original shape.
    """
    root, account, day = validated_account(account_root, account_id, trade_date)
    if status not in SESSION_STATUSES:
        raise BarStoreError(
            "invalid_session", f"status must be one of {SESSION_STATUSES}, got {status!r}"
        )
    bars = _ordered_bars(applied_bars)
    if not bars and result_hash_chain_head:
        raise BarStoreError(
            "invalid_session", "result_hash_chain_head must be empty when no bar has been applied"
        )
    head = require_hash(result_hash_chain_head, "result_hash_chain_head") if bars else ""
    payload_counts = require_counts(counts)
    if payload_counts.get("bars", len(bars)) != len(bars):
        raise BarStoreError(
            "invalid_session",
            f"counts['bars'] is {payload_counts['bars']}, but {len(bars)} bars were applied",
        )
    payload_counts["bars"] = len(bars)

    steps_dir = root / STEPS_DIRNAME
    date_dir = steps_dir / day
    account_root_resolved = resolve_safely(root, "account root")
    ensure_dir(steps_dir, account_root=account_root_resolved, what="steps directory")
    ensure_dir(date_dir, account_root=account_root_resolved, what=f"date directory {day}")
    target = date_dir / SESSION_FILE
    if target.is_symlink():
        raise UnsafeBarPath(f"{SESSION_FILE} is a symbolic link; refusing to replace it")

    document = {
        "format": SESSION_FORMAT,
        "account_id": account,
        "trade_date": day,
        "status": status,
        "first_bar": bars[0] if bars else "",
        "last_bar": bars[-1] if bars else "",
        "watermark_bar": bars[-1] if bars else "",
        "applied_bar_count": len(bars),
        "counts": payload_counts,
        "result_hash_chain_head": head,
    }
    if final_state is not None:
        document["final_state"] = _final_state_payload(final_state)
    atomic_write(target, (dumps(document) + "\n").encode("utf-8"))
    return target


def _session_label(value: Any, field: str) -> None:
    if value != "" and (not isinstance(value, str) or value not in _BAR_INDEX):
        raise SessionCorrupt(f"session.json {field} is not a continuous session label: {value!r}")


def read_session_summary(
    account_root: str | Path, trade_date: str, *, account_id: str | None = None
) -> Mapping[str, Any]:
    """Read and validate ``steps/<date>/session.json`` (never follows a symlink)."""
    root = Path(account_root).expanduser()
    account = root.name if account_id is None else account_id
    path = session_summary_path(root, account_id=account, trade_date=trade_date)
    account_root_resolved = resolve_safely(root, "account root")
    for directory, what in ((path.parent.parent, "steps directory"), (path.parent, "date directory")):
        if directory.is_symlink():
            raise UnsafeBarPath(f"{what} is a symbolic link; refusing to follow it")
        require_containment(resolve_safely(directory, what), account_root_resolved, what)
    if path.is_symlink():
        raise UnsafeBarPath(f"{SESSION_FILE} is a symbolic link; refusing to follow it")
    if not path.is_file():
        raise SessionNotFound(trade_date)
    try:
        document = read_json(path, "session_corrupt")
    except StoreError as exc:
        raise SessionCorrupt(exc.message) from exc
    if not isinstance(document, dict):
        raise SessionCorrupt(f"{SESSION_FILE} is not a JSON object")
    if document.get("format") != SESSION_FORMAT:
        raise SessionCorrupt(
            f"session.json carries format {document.get('format')!r}, expected {SESSION_FORMAT!r}"
        )
    if document.get("account_id") != account:
        raise SessionCorrupt(
            f"session.json carries account_id {document.get('account_id')!r}, expected {account!r}"
        )
    if document.get("trade_date") != trade_date:
        raise SessionCorrupt(
            f"session.json carries trade_date {document.get('trade_date')!r}, expected {trade_date!r}"
        )
    if document.get("status") not in SESSION_STATUSES:
        raise SessionCorrupt(f"session.json status {document.get('status')!r} is not a session status")
    applied = document.get("applied_bar_count")
    if isinstance(applied, bool) or not isinstance(applied, int) or applied < 0:
        raise SessionCorrupt(f"session.json applied_bar_count {applied!r} is not a count")
    counts = document.get("counts")
    if not isinstance(counts, Mapping) or counts.get("bars") != applied:
        raise SessionCorrupt("session.json counts do not agree with applied_bar_count")
    for field in ("first_bar", "last_bar", "watermark_bar"):
        _session_label(document.get(field), field)
    head = document.get("result_hash_chain_head")
    if applied == 0 and head != "":
        raise SessionCorrupt("an empty session summary carries a result hash chain head")
    if applied:
        try:
            require_hash(head, "result_hash_chain_head")
        except BarStoreError as exc:
            raise SessionCorrupt(exc.message) from exc
    if "final_state" in document:
        try:
            _final_state_payload(document["final_state"])
        except BarStoreError as exc:
            raise SessionCorrupt(exc.message) from exc
    return document
