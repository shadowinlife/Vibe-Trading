"""pt-v1.3 session-end settlement — the default 15:00 ``SessionEndHook`` (P1-19).

The bar engine runs exactly ONE session-end hook on the last continuous bar of
a cn_a session (``15:00``), after matching and after ``end_of_bar``.
:func:`settle_session` is that default:

1. expire every resting ``day`` order on the passed book — same
   ``order_id``/``seq``, ``remaining_qty`` kept, one ``expired`` transition
   each, idempotent (``OrderBook.expire_day_orders``);
2. clear ``bought_today`` so the NEXT session starts with the T+1 counter
   reset: today's buys become sellable tomorrow.

The hook is PURE: no I/O, no clock, no randomness, no environment.  It never
fills an order, never moves cash or positions, and leaves the account identity,
book, ``order_seq`` and ``watermark_bar`` alone.  Expiries are reported through
``order_events`` (``event_type="expired"``) only; they do NOT produce a
``CanceledOrder`` — that array is the emergency-cancel channel for IOC/FOK, and
a scheduled session-end expiry is not a cancellation.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .bar_state import BarEngineState
from .orders import OrderBook

if TYPE_CHECKING:  # the runtime alias lives in bar_engine; import it for typing only
    from .bar_engine import SessionEndHook

__all__ = ["SettlementError", "make_session_end_hook", "settle_session"]

#: The only label a session-end settlement may run on (the grid's last bar).
SETTLEMENT_LABEL = "15:00"


class SettlementError(ValueError):
    """A session-end settlement call the engine cannot honour.

    In-package signal, never a wire error: malformed input (a non-15:00 label,
    a book that is not the state's own book) or a wrong argument type.
    """


def settle_session(state: BarEngineState, book: OrderBook, label: str) -> BarEngineState:
    """Expire resting ``day`` orders and reset ``bought_today`` at ``15:00``.

    ``book`` MUST be ``state.book``: the engine captures transitions by row
    identity on that exact object.  The returned state keeps the same
    account/market/trade_date identity, the same book object, and unchanged
    ``cash`` / ``positions`` / ``order_seq`` / ``watermark_bar``; only
    ``bought_today`` becomes empty.  A second call on the settled state is
    harmless (expiry is idempotent and the mapping is already empty).
    """
    if not isinstance(state, BarEngineState):
        raise SettlementError(
            f"state must be a BarEngineState, got {type(state).__name__}"
        )
    if not isinstance(book, OrderBook):
        raise SettlementError(f"book must be an OrderBook, got {type(book).__name__}")
    if label != SETTLEMENT_LABEL:
        raise SettlementError(
            f"session settlement runs at {SETTLEMENT_LABEL!r}, got {label!r}"
        )
    if book is not state.book:
        raise SettlementError("settle_session must apply its transitions to the state's own book")
    book.expire_day_orders(reason=f"day order expired at {label}")
    return replace(state, bought_today={})


def make_session_end_hook() -> "SessionEndHook":
    """The engine's default session-end hook: :func:`settle_session`."""
    return settle_session
