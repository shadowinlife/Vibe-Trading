"""pt-v1.3 deterministic order identity (P1 todo 12; closes GAP-2, id half).

An order id is a pure function of four inputs already present in a replay::

    <account_id>:<trade_date>:<bar_label>:<seq:08d>
    pa_20261020:2026-10-20:09:35:00000001

No ``uuid``, no ``random``, no clock and no payload hash: two identical replays
of the same inputs must produce byte-identical ids, and a reader must be able
to recover the four segments from the string alone.  :func:`parse_order_id`
is that reader — the round trip ``parse(assign(...))`` is pinned by tests.

Segment rules (each one is the rule its owning module already enforces, so this
module invents no second vocabulary):

* ``account_id`` — :func:`~.account.validate_account_id`.  Its charset is
  ``[A-Za-z0-9][A-Za-z0-9._-]*`` and therefore **cannot contain ``:``**, which
  is what makes the separator unambiguous: there is exactly one way to split a
  well-formed id into its four segments.  An account segment that carries a
  colon is refused here rather than parsed one way and read another.
* ``trade_date`` — canonical ``YYYY-MM-DD`` (regex **and** a real calendar day;
  ``2026-02-30`` and ``2026-1-2`` are both refused).
* ``bar_label`` — ``HH:MM`` in 24-hour form, the label grammar
  :class:`~.orders.OrderBook` validates rows with.
* ``seq`` — an integer in ``[0, 99999999]``; the upper bound is the width of
  the field, so :func:`assign_order_id` can never emit a nine-digit sequence
  that :func:`parse_order_id` would reject.

The account's ``order_seq`` counter is the caller's state (the session runner
persists it); this module only formats one id and advances one counter.  It touches no
account, store, clock or file.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .account import AccountError, validate_account_id

__all__ = [
    "ORDER_ID_PATTERN",
    "SEQ_MAX",
    "SEQ_WIDTH",
    "OrderIdError",
    "allocate_order_id",
    "assign_order_id",
    "next_seq",
    "parse_order_id",
]

#: Width of the zero-padded ``seq`` field.
SEQ_WIDTH = 8

#: Largest value the ``seq`` field can hold (``99999999``).
SEQ_MAX = 10**SEQ_WIDTH - 1

#: The whole grammar.  ``\Z`` (not ``$``) so a trailing newline is malformed.
ORDER_ID_PATTERN = re.compile(
    r"^(?P<account_id>[A-Za-z0-9][A-Za-z0-9._-]{0,63})"
    r":(?P<trade_date>\d{4}-\d{2}-\d{2})"
    r":(?P<bar_label>(?:[01]\d|2[0-3]):[0-5]\d)"
    r":(?P<seq>\d{8})\Z"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_BAR_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d\Z")


def _malformed(value: Any) -> str:
    return (
        "order_id must be <account_id>:<YYYY-MM-DD>:<HH:MM>:<8-digit seq> "
        f"(seq zero-padded to {SEQ_WIDTH} digits), got {value!r}"
    )


class OrderIdError(ValueError):
    """A malformed or ambiguous order identity.

    In-package signal, not a wire error: the engine maps it onto a rejection
    or an ``INVALID_REQUEST`` envelope, exactly like
    :class:`~.orders.InvalidTransition`.
    """


def _account_segment(value: Any) -> str:
    """Reuse the account rule instead of keeping a second copy of its regex."""
    if not isinstance(value, str) or not value:
        raise OrderIdError("account_id must be a non-empty string")
    try:
        return validate_account_id(value)
    except AccountError as exc:
        raise OrderIdError(str(exc)) from None


def _trade_date_segment(value: Any) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise OrderIdError(f"trade_date must be YYYY-MM-DD, got {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise OrderIdError(f"trade_date is not a calendar date: {value!r}") from None
    if parsed.isoformat() != value:
        raise OrderIdError(f"trade_date must be canonical YYYY-MM-DD, got {value!r}")
    return value


def _bar_label_segment(value: Any) -> str:
    if not isinstance(value, str) or not _BAR_LABEL_RE.match(value):
        raise OrderIdError(f"bar_label must be HH:MM, got {value!r}")
    return value


def _seq_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OrderIdError(f"seq must be an integer, got {type(value).__name__}")
    if not 0 <= value <= SEQ_MAX:
        raise OrderIdError(
            f"seq must be in [0, {SEQ_MAX}] (a {SEQ_WIDTH}-digit field), got {value}"
        )
    return value


def assign_order_id(account_id: str, trade_date: str, bar_label: str, seq: int) -> str:
    """Return ``account_id:trade_date:bar_label:seq:08d`` — a pure function.

    Every segment is validated first, so a returned id always satisfies
    :data:`ORDER_ID_PATTERN` and always round-trips through
    :func:`parse_order_id`.
    """
    return (
        f"{_account_segment(account_id)}:"
        f"{_trade_date_segment(trade_date)}:"
        f"{_bar_label_segment(bar_label)}:"
        f"{_seq_value(seq):0{SEQ_WIDTH}d}"
    )


def parse_order_id(order_id: str) -> tuple[str, str, str, int]:
    """Split a canonical id into ``(account_id, trade_date, bar_label, seq)``.

    Raises :class:`OrderIdError` for a malformed shape, a segment the builder
    would refuse (an ambiguous account, ``2026-02-30``, ``24:00``) or a
    non-canonical ``seq`` such as ``1`` instead of ``00000001`` — a reader must
    never accept an id :func:`assign_order_id` cannot produce.
    """
    if not isinstance(order_id, str):
        raise OrderIdError(f"order_id must be a string, got {type(order_id).__name__}")
    match = ORDER_ID_PATTERN.match(order_id)
    if match is None:
        raise OrderIdError(_malformed(order_id))
    account_id, trade_date, bar_label, seq_text = match.group(
        "account_id", "trade_date", "bar_label", "seq"
    )
    return (
        _account_segment(account_id),
        _trade_date_segment(trade_date),
        _bar_label_segment(bar_label),
        _seq_value(int(seq_text)),
    )


def next_seq(order_seq: int) -> int:
    """The seq after ``order_seq``, refusing to leave the 8-digit field.

    Total over the counter's domain: it always returns an ``int`` for an
    assignable ``order_seq`` and raises :class:`OrderIdError` at the ceiling
    rather than emitting an id the grammar cannot parse back.
    """
    seq = _seq_value(order_seq)
    if seq == SEQ_MAX:
        raise OrderIdError(
            f"order_seq {SEQ_MAX} is the last assignable seq; the {SEQ_WIDTH}-digit "
            f"field has no room for {SEQ_MAX + 1}"
        )
    return seq + 1


def allocate_order_id(
    account_id: str, trade_date: str, bar_label: str, order_seq: int
) -> tuple[str, int]:
    """Format one id and advance the caller's counter: ``(order_id, seq + 1)``.

    Pure convenience over :func:`assign_order_id` + :func:`next_seq`; it reads
    and writes nothing outside its arguments (no account, no store, no clock).
    """
    return assign_order_id(account_id, trade_date, bar_label, order_seq), next_seq(order_seq)
