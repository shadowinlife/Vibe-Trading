"""pt-v1.3 order book — the pure, clock-free order lifecycle (P1 todo 11).

One :class:`OrderBook` is the in-memory state of every order one account placed
on one session day.  It is a state machine only: no I/O, no network, no clock,
no randomness and no price policy (todo 14 supplies ``fill_price_for``).  Order
ids are the caller's (todo 12 owns the formula, :mod:`.order_ids`); this module
adds the dedupe path around :meth:`OrderBook.place` and the per-book client-id
lookup, never a second id scheme.

Lifecycle (status x verb)::

    new       --fill-------> filled      all-or-none, terminal
    new       --cancel-----> canceled    terminal
    new       --reject-----> rejected    terminal, frozen REASON_CODES only
    new       --replace----> new         same order_id/seq, one row
    new(ioc)  --bar end----> canceled    reason ``ioc_unfilled``
    new(fok)  --bar end----> canceled    reason ``fok_unfilled``
    new(day)  --bar end----> new         rests until the session end
    new(day)  --15:00 end--> expired     :meth:`OrderBook.expire_day_orders`
    terminal  --any--------> InvalidTransition

A returned fill price fills the WHOLE ``remaining_qty`` (no partial fills), so
IOC and FOK are operationally identical inside one bar — both rest only until
``end_of_bar`` — and differ solely in the terminal reason token; no liquidity
model is implied.  ``expired`` is the 15:00 session-end terminal for ``day``
orders (todo 19); a restored ``expired`` row is terminal like any other.

Invariants (asserted at construction and after every transition):
``filled_qty + remaining_qty == qty``; ``0 <= filled/remaining <= qty``;
terminal statuses never transition again; ``filled`` implies
``remaining_qty == 0``; ``new`` implies ``remaining_qty > 0``;
``avg_fill_price >= 0``; quantities/money are finite; ``limit`` is ``None`` or
positive; ``bar_label`` is ``HH:MM``; rows belong to this book's account/date.

Every transition is recorded; :meth:`OrderBook.events_for` turns the rows a bar
changed into wire event rows, in transition order, omitting empty optional
keys.  Restored rows carry no transition history and can never be reported as
events of this bar (fail closed).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Callable, Sequence

from .order_ids import allocate_order_id
from .schema import (
    ORDER_EVENT_TYPES,
    ORDER_STATUSES,
    REASON_CODES,
    SIDES,
    TIFS,
    OrderAction,
    OrderState,
    parse_bar_time,
)

__all__ = ["InvalidTransition", "OrderBook", "allocate_order_id"]

_BAR_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TERMINAL_STATUSES = frozenset(status for status in ORDER_STATUSES if status != "new")
_UNFILLED_REASON = {"ioc": "ioc_unfilled", "fok": "fok_unfilled"}
_TOTAL_QTY_EVENTS = frozenset({"placed", "replaced", "filled"})


class InvalidTransition(ValueError):
    """A lifecycle move the order book forbids (malformed input or terminal row).

    In-package signal, not a wire error: the bar engine maps it onto a
    rejection or an ``INVALID_REQUEST`` envelope.
    """


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidTransition(message)


def _finite_number(value: Any, path: str) -> float:
    _require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        f"{path} must be a number, got {type(value).__name__}",
    )
    number = float(value)
    _require(math.isfinite(number), f"{path} must be finite, got {value!r}")
    return number


def _text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), f"{path} must be a string, got {type(value).__name__}")
    _require(allow_empty or bool(value), f"{path} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class _Transition:
    sequence: int
    event_type: str
    order_id: str
    row: OrderState
    reason_code: str = ""
    detail: str = ""


class OrderBook:
    """Per-account, per-session-day order state machine (see module docstring)."""

    def __init__(self, account_id: str, trade_date: str, rows: Sequence[OrderState] = ()) -> None:
        self._account_id = _text(account_id, "account_id")
        self._trade_date = self._parse_trade_date(trade_date)
        self._rows: dict[str, OrderState] = {}
        self._client_ids: dict[str, str] = {}
        # Each transition returns a NEW frozen row, so a row instance carries
        # exactly one transition; identity is the key, and it keeps the row alive.
        self._transitions: dict[int, _Transition] = {}
        self._sequence = 0
        for row in rows:
            self._restore(row)

    @staticmethod
    def _parse_trade_date(value: Any) -> str:
        text = _text(value, "trade_date")
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            raise InvalidTransition(f"trade_date must be YYYY-MM-DD, got {text!r}") from None
        _require(
            parsed.isoformat() == text, f"trade_date must be canonical YYYY-MM-DD, got {text!r}"
        )
        return text

    @staticmethod
    def _bar_label(value: Any, path: str = "bar_label") -> str:
        text = _text(value, path)
        _require(bool(_BAR_LABEL_RE.match(text)), f"{path} must be HH:MM, got {text!r}")
        return text

    def _restore(self, row: OrderState) -> None:
        self._assert_row(row)
        _require(
            row.order_id not in self._rows,
            f"duplicate order_id {row.order_id!r} in the restored rows",
        )
        _require(
            not (row.client_order_id and row.client_order_id in self._client_ids),
            f"duplicate client_order_id {row.client_order_id!r} in the restored rows",
        )
        self._rows[row.order_id] = row
        if row.client_order_id:
            self._client_ids[row.client_order_id] = row.order_id

    def _assert_row(self, row: Any) -> None:
        _require(isinstance(row, OrderState), f"rows must be OrderState, got {type(row).__name__}")
        _text(row.order_id, "order_id")
        _text(row.symbol, "symbol")
        _text(row.client_order_id, "client_order_id", allow_empty=True)
        self._bar_label(row.bar_label)
        _require(
            row.account_id == self._account_id,
            f"order {row.order_id!r} belongs to account {row.account_id!r}, not {self._account_id!r}",
        )
        _require(
            row.trade_date == self._trade_date,
            f"order {row.order_id!r} belongs to trade_date {row.trade_date!r}, not {self._trade_date!r}",
        )
        _require(
            row.status in ORDER_STATUSES and row.side in SIDES and row.tif in TIFS,
            f"order {row.order_id!r} has an unknown status/side/tif "
            f"({row.status!r}/{row.side!r}/{row.tif!r})",
        )
        _require(
            not isinstance(row.seq, bool) and isinstance(row.seq, int) and row.seq >= 0,
            f"order {row.order_id!r} seq must be an integer >= 0",
        )
        qty = _finite_number(row.qty, f"order {row.order_id!r} qty")
        remaining = _finite_number(row.remaining_qty, f"order {row.order_id!r} remaining_qty")
        filled = _finite_number(row.filled_qty, f"order {row.order_id!r} filled_qty")
        _require(
            qty > 0 and 0 <= remaining <= qty and 0 <= filled <= qty,
            f"order {row.order_id!r} must satisfy 0 <= filled/remaining <= qty and qty > 0, "
            f"got {filled!r}/{remaining!r}/{qty!r}",
        )
        _require(
            math.isclose(filled + remaining, qty, rel_tol=1e-9, abs_tol=1e-9),
            f"order {row.order_id!r} filled_qty + remaining_qty ({filled!r} + {remaining!r}) "
            f"must equal qty ({qty!r})",
        )
        _require(
            _finite_number(row.avg_fill_price, f"order {row.order_id!r} avg_fill_price") >= 0,
            f"order {row.order_id!r} avg_fill_price must be >= 0",
        )
        if row.limit is not None:
            limit = _finite_number(row.limit, f"order {row.order_id!r} limit")
            _require(limit > 0, f"order {row.order_id!r} limit must be > 0, got {limit!r}")
        _require(
            row.status != "filled" or remaining == 0,
            f"order {row.order_id!r} is filled but remaining_qty is {remaining!r}",
        )
        _require(
            row.status != "new" or remaining > 0,
            f"order {row.order_id!r} is new but has nothing remaining",
        )

    def place(self, action: OrderAction, *, order_id: str, seq: int, bar_label: str) -> OrderState:
        """Store a new resting order; ids are the caller's, never invented here."""
        _require(
            isinstance(action, OrderAction),
            f"place requires an OrderAction, got {type(action).__name__}",
        )
        _require(action.action == "place", f"place requires action 'place', got {action.action!r}")
        _require(
            not action.order_id,
            f"a place intent must not carry an order_id ({action.order_id!r}); "
            "the caller assigns the id",
        )
        _require(
            action.replace_qty is None and action.replace_limit is None,
            "replace_qty/replace_limit are only valid for a replace action",
        )
        identifier = _text(order_id, "order_id")
        label = self._bar_label(bar_label)
        _require(
            not isinstance(seq, bool) and isinstance(seq, int) and seq >= 0,
            f"seq must be an integer >= 0, got {seq!r}",
        )
        _require(identifier not in self._rows, f"duplicate order_id {identifier!r}")
        _require(
            action.side in SIDES and action.tif in TIFS,
            f"side/tif must be valid, got {action.side!r}/{action.tif!r}",
        )
        qty = _finite_number(action.qty, "qty")
        _require(qty > 0, f"qty must be > 0, got {qty!r}")
        limit: float | None = None
        if action.limit is not None:
            limit = _finite_number(action.limit, "limit")
            _require(limit > 0, f"limit must be > 0, got {limit!r}")
        client_id = _text(action.client_order_id, "client_order_id", allow_empty=True)
        _require(
            not (client_id and client_id in self._client_ids),
            f"duplicate client_order_id {client_id!r}",
        )
        return self._commit(
            "placed",
            OrderState(
                order_id=identifier,
                account_id=self._account_id,
                trade_date=self._trade_date,
                symbol=_text(action.symbol, "symbol"),
                side=action.side,
                qty=qty,
                remaining_qty=qty,
                limit=limit,
                tif=action.tif,
                status="new",
                bar_label=label,
                client_order_id=client_id,
                filled_qty=0.0,
                avg_fill_price=0.0,
                seq=seq,
            ),
        )

    def place_or_get(
        self, action: OrderAction, *, order_id: str, seq: int, bar_label: str
    ) -> tuple[OrderState, bool]:
        """Place unless this book's client id already names the same intent.

        Returns ``(row, created)``.  ``created`` is ``True`` when a new order
        was stored.  When ``action.client_order_id`` is non-empty and already
        rests in THIS book the stored row comes back unchanged — ``created``
        is ``False``, the returned row is the SAME object (so ``events_for``
        keeps its identity guard) and no second row, transition or fill is
        created.

        A repeated client id whose payload differs from the stored row
        (``symbol``/``side``/``tif``/``qty``/``limit``) is a conflict, not a
        dedupe: it raises :class:`InvalidTransition` carrying
        ``client_order_id_conflict`` rather than answering for a different
        intent.  :meth:`place` itself stays strict; this is the only path that
        tolerates a repeated client id, and it does so by NOT placing.
        """
        _require(
            isinstance(action, OrderAction),
            f"place_or_get requires an OrderAction, got {type(action).__name__}",
        )
        _require(
            action.action == "place",
            f"place_or_get requires action 'place', got {action.action!r}",
        )
        client_id = _text(action.client_order_id, "client_order_id", allow_empty=True)
        if client_id:
            existing = self.find_by_client_order_id(client_id)
            if existing is not None:
                self._require_same_intent(existing, action)
                return existing, False
        return self.place(action, order_id=order_id, seq=seq, bar_label=bar_label), True

    def find_by_client_order_id(self, client_order_id: str) -> OrderState | None:
        """The row carrying ``client_order_id`` in THIS book, or ``None``.

        Scope is deliberate: one book is one account on one trade date, so a
        client id used by another account's book is invisible here.  An empty
        id is not a dedupe key and always reads as ``None``.
        """
        client = _text(client_order_id, "client_order_id", allow_empty=True)
        if not client:
            return None
        return self._rows.get(self._client_ids.get(client, ""))

    def allocate_order_id(self, bar_label: str, order_seq: int) -> tuple[str, int]:
        """Format this book's next id: ``(order_id, order_seq + 1)``.

        The book already owns the account and the trade date, so the caller
        supplies only the bar and its counter.  Nothing outside the arguments
        is read or written; the grammar itself lives in
        :func:`.order_ids.allocate_order_id`.
        """
        return allocate_order_id(self._account_id, self._trade_date, bar_label, order_seq)

    def cancel(self, order_id: str = "", client_order_id: str = "", *, reason: str = "") -> OrderState:
        """Cancel a resting order; the unfilled quantity stays on the row."""
        row = self._target(order_id, client_order_id, verb="cancel")
        self._require_new(row, "cancel")
        return self._commit(
            "canceled",
            replace(row, status="canceled"),
            reason_code=_text(reason, "reason", allow_empty=True),
        )

    def replace(
        self,
        order_id: str = "",
        client_order_id: str = "",
        *,
        replace_qty: float | None = None,
        replace_limit: float | None = None,
    ) -> OrderState:
        """Replace quantity and/or limit in place — same ``order_id``/``seq``.

        ``None`` leaves a field unchanged; side/symbol never change and no
        second row is created.  Because fills are all-or-none, a ``new`` row
        has not filled anything, so the new remaining quantity is the new total.
        """
        row = self._target(order_id, client_order_id, verb="replace")
        self._require_new(row, "replace")
        _require(
            replace_qty is not None or replace_limit is not None,
            "a replace requires at least one of replace_qty/replace_limit",
        )
        qty = row.qty
        if replace_qty is not None:
            qty = _finite_number(replace_qty, "replace_qty")
            _require(qty > row.filled_qty, f"replace_qty must be > filled_qty, got {qty!r}")
        limit = row.limit
        if replace_limit is not None:
            limit = _finite_number(replace_limit, "replace_limit")
            _require(limit > 0, f"replace_limit must be > 0, got {limit!r}")
        return self._commit(
            "replaced", replace(row, qty=qty, remaining_qty=qty - row.filled_qty, limit=limit)
        )

    def rejected(
        self, order_id: str = "", client_order_id: str = "", *, reason_code: str
    ) -> OrderState:
        """Terminate a resting order with one frozen AM-14 rejection code."""
        code = _text(reason_code, "reason_code")
        _require(
            code in REASON_CODES,
            f"reason_code {code!r} is outside the frozen REASON_CODES vocabulary",
        )
        row = self._target(order_id, client_order_id, verb="reject")
        self._require_new(row, "reject")
        return self._commit("rejected", replace(row, status="rejected"), reason_code=code)

    def match_bar(
        self, fill_price_for: Callable[[OrderState], float | None]
    ) -> tuple[OrderState, ...]:
        """Fill every marketable resting order all-or-none, in ``(seq, order_id)`` order.

        ``fill_price_for`` returns the execution price or ``None`` when the
        order is not marketable on this bar.  A returned price fills the WHOLE
        remaining quantity; ``None`` leaves the order ``new`` (``ioc``/``fok``
        terminate in :meth:`end_of_bar`).  Filled rows are returned in match
        order; a second call never touches a filled row.
        """
        _require(callable(fill_price_for), "match_bar requires a fill_price_for callable")
        # Price every marketable order first: a malformed price must fail before
        # ANY row is committed, so one bad callback result cannot leave the bar
        # half-applied.
        matched: list[tuple[OrderState, float]] = []
        for row in self._ordered():
            if row.status != "new":
                continue
            raw_price = fill_price_for(row)
            if raw_price is None:
                continue
            price = _finite_number(raw_price, f"fill price for order {row.order_id!r}")
            _require(
                price > 0, f"fill price for order {row.order_id!r} must be > 0, got {price!r}"
            )
            matched.append((row, price))
        changed: list[OrderState] = []
        for row, price in matched:
            filled = replace(
                row, filled_qty=row.qty, remaining_qty=0.0, status="filled", avg_fill_price=price
            )
            changed.append(self._commit("filled", filled))
        return tuple(changed)

    def end_of_bar(self, bar_label: str) -> tuple[OrderState, ...]:
        """Terminate this bar's unfilled ``ioc``/``fok`` orders; ``day`` rests.

        Idempotent.  ``day`` orders are untouched here: they rest until the
        session end, where :meth:`expire_day_orders` terminates them.
        """
        label = self._bar_label(bar_label)
        changed: list[OrderState] = []
        for row in self._ordered():
            if row.status != "new" or row.tif not in _UNFILLED_REASON:
                continue
            changed.append(
                self._commit(
                    "canceled",
                    replace(row, status="canceled"),
                    reason_code=_UNFILLED_REASON[row.tif],
                    detail=f"unfilled at {label}",
                )
            )
        return tuple(changed)

    def expire_day_orders(self, *, reason: str = "") -> tuple[OrderState, ...]:
        """Terminate every resting ``day`` order at the session end (15:00).

        Only ``status == "new"`` and ``tif == "day"`` rows transition; an
        ``ioc``/``fok`` row is already terminal from :meth:`end_of_bar`, and
        any terminal row is never touched.  ``order_id``/``seq`` are preserved
        and ``remaining_qty`` stays on the expired row — an expiry is not a
        fill.  ``reason`` is free text recorded on the event ``detail``; there
        is no frozen expiry reason code.

        Idempotent: once no ``new`` ``day`` row remains the call returns
        ``()``.  Expiries are reported as ``expired`` events only; they never
        become a ``CanceledOrder`` (that array is the emergency-cancel channel
        for IOC/FOK).
        """
        detail = _text(reason, "reason", allow_empty=True)
        changed: list[OrderState] = []
        for row in self._ordered():
            if row.status != "new" or row.tif != "day":
                continue
            changed.append(self._commit("expired", replace(row, status="expired"), detail=detail))
        return tuple(changed)

    def open_orders(self) -> tuple[OrderState, ...]:
        """Resting (``new``) rows, deterministically ordered."""
        return tuple(row for row in self._ordered() if row.status == "new")

    def rows(self) -> tuple[OrderState, ...]:
        """Every row, open or terminal, in ``(seq, order_id)`` order."""
        return self._ordered()

    def events_for(
        self, changed: Sequence[OrderState], *, bar_time: str
    ) -> tuple[dict[str, Any], ...]:
        """Build this bar's wire event rows, one per transition, in order.

        Ordering follows the book's transition sequence (not the caller's list
        order); a repeated row is emitted once.  Optional keys are omitted when
        empty, exactly as ``wire._order_event_payload`` requires.
        """
        stamp = parse_bar_time(bar_time, "bar_time")
        matched: list[_Transition] = []
        seen: set[int] = set()
        for row in changed:
            transition = self._transitions.get(id(row))
            if transition is None or transition.row is not row:
                raise InvalidTransition(
                    f"order {getattr(row, 'order_id', '?')!r} was not transitioned by this book; "
                    "events_for describes this bar's transitions only"
                )
            if transition.sequence not in seen:
                seen.add(transition.sequence)
                matched.append(transition)
        matched.sort(key=lambda item: item.sequence)
        return tuple(self._event_payload(item, stamp) for item in matched)

    def _ordered(self) -> tuple[OrderState, ...]:
        return tuple(sorted(self._rows.values(), key=lambda row: (row.seq, row.order_id)))

    def _commit(
        self,
        event_type: str,
        row: OrderState,
        *,
        reason_code: str = "",
        detail: str = "",
    ) -> OrderState:
        _require(
            event_type in ORDER_EVENT_TYPES, f"unknown event type {event_type!r}"
        )
        self._assert_row(row)
        self._rows[row.order_id] = row
        if row.client_order_id:
            self._client_ids[row.client_order_id] = row.order_id
        self._sequence += 1
        self._transitions[id(row)] = _Transition(
            self._sequence, event_type, row.order_id, row, reason_code, detail
        )
        return row

    def _target(self, order_id: Any, client_order_id: Any, *, verb: str) -> OrderState:
        identifier = _text(order_id, "order_id", allow_empty=True)
        client = _text(client_order_id, "client_order_id", allow_empty=True)
        _require(
            bool(identifier or client), f"{verb} requires order_id or client_order_id"
        )
        if identifier:
            row = self._rows.get(identifier)
            if row is None:
                raise InvalidTransition(f"unknown order_id {identifier!r}")
            _require(
                not client or row.client_order_id == client,
                f"order_id {identifier!r} and client_order_id {client!r} name different orders",
            )
            return row
        row = self._rows.get(self._client_ids.get(client, ""))
        if row is None:
            raise InvalidTransition(f"unknown client_order_id {client!r}")
        return row

    @staticmethod
    def _require_same_intent(row: OrderState, action: OrderAction) -> None:
        differences = [
            f"{field}: stored {stored!r}, requested {requested!r}"
            for field, stored, requested in (
                ("symbol", row.symbol, action.symbol),
                ("side", row.side, action.side),
                ("tif", row.tif, action.tif),
                ("qty", row.qty, action.qty),
                ("limit", row.limit, action.limit),
            )
            if stored != requested
        ]
        if differences:
            raise InvalidTransition(
                f"client_order_id_conflict: client_order_id {row.client_order_id!r} already "
                f"names order {row.order_id!r} with a different intent "
                f"({'; '.join(differences)}); a client id identifies one intent only"
            )

    @staticmethod
    def _require_new(row: OrderState, verb: str) -> None:
        _require(
            row.status not in _TERMINAL_STATUSES,
            f"cannot {verb} order {row.order_id!r}: status {row.status!r} is terminal; "
            "only a new order can transition",
        )

    @staticmethod
    def _event_payload(transition: _Transition, bar_time: str) -> dict[str, Any]:
        row = transition.row
        payload: dict[str, Any] = {
            "event_type": transition.event_type,
            "order_id": row.order_id,
            "client_order_id": row.client_order_id,
            "symbol": row.symbol,
            "side": row.side,
            "qty": row.qty if transition.event_type in _TOTAL_QTY_EVENTS else row.remaining_qty,
            "bar_time": bar_time,
        }
        if transition.event_type == "filled":
            payload["price"] = row.avg_fill_price
        if transition.reason_code:
            payload["reason_code"] = transition.reason_code
        if transition.detail:
            payload["detail"] = transition.detail
        return payload
