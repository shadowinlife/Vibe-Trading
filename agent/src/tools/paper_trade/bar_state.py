"""Carried session state + input validation for the pt-v1.3 bar engine (P1-16).

Split out of :mod:`bar_engine` to keep the loop inside the repository's
800-line cap.  This module owns the immutable :class:`BarEngineState` carried
from bar to bar (including the ``PaperTradeAccount`` bridge) and the fail-
closed canonicalization of every field it accepts.  It imports no loop, no
ledger and no clock; ``bar_engine`` re-exports :class:`BarEngineState` and
:class:`BarEngineError`, so consumers keep one entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Sequence

from . import bar_clock
from .account import PaperTradeAccount
from .order_ids import OrderIdError, parse_order_id
from .orders import InvalidTransition, OrderBook
from .rules import money, quantize_money
from .schema import MARKETS, SESSION_PHASES, Position

__all__ = ["BarEngineError", "BarEngineState"]

_BAR_INDEX = {label: index for index, label in enumerate(bar_clock.BAR_LABELS)}


class BarEngineError(ValueError):
    """A malformed bar/state the caller must map to ``INVALID_REQUEST``.

    In-package signal (never a wire error): the bar/time/account/band inputs
    are inconsistent, an individual order *action* cannot be applied (see
    :mod:`bar_actions`), or an injected hook violated its contract.
    """


def _fail(message: str) -> NoReturn:
    raise BarEngineError(message)


# ── the carried state ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, eq=False)
class BarEngineState:
    """The authoritative ledger + order book carried from bar to bar.

    ``cash`` is a 2dp ``Decimal`` and ``positions`` are the schema rows (float
    qty/avg_cost, ``avg_cost`` preserved exactly as given).  ``book`` is the
    mutable :class:`~.orders.OrderBook` for this account/day; ``apply_bar``
    COPIES it before every transition, so a state handed in is never mutated.
    ``order_seq`` is the next free deterministic sequence, ``bought_today``
    carries the T+1 availability across bars, and ``watermark_bar`` is the
    last applied ``HH:MM`` label of the current session (``""`` before the
    first bar).  Both of the last two survive a durable bar only through
    :meth:`from_account`, which restores them for the session day they belong
    to and starts a new day empty.
    """

    account_id: str
    market: str
    trade_date: str
    cash: Decimal
    positions: tuple[Position, ...]
    book: OrderBook
    order_seq: int
    bought_today: Mapping[str, Decimal]
    watermark_bar: str

    def __post_init__(self) -> None:
        _text(self.account_id, "account_id")
        if self.market not in MARKETS:
            _fail(f"market must be one of {MARKETS}, got {self.market!r}")
        _canonical_date(self.trade_date)
        cash = _decimal(self.cash, "cash")
        if cash < 0:
            _fail(f"cash must be >= 0, got {cash}")
        object.__setattr__(self, "cash", quantize_money(cash))
        object.__setattr__(self, "positions", _canonical_positions(self.positions))

        if not isinstance(self.book, OrderBook):
            _fail(f"book must be an OrderBook, got {type(self.book).__name__}")
        book_account, book_date = _book_identity(self.book)
        if book_account != self.account_id or book_date != self.trade_date:
            _fail(
                f"book belongs to {book_account!r}/{book_date!r}, not this state's "
                f"{self.account_id!r}/{self.trade_date!r}"
            )
        if isinstance(self.order_seq, bool) or not isinstance(self.order_seq, int) or self.order_seq < 0:
            _fail(f"order_seq must be an integer >= 0, got {self.order_seq!r}")
        highest = max((row.seq for row in self.book.rows()), default=-1)
        if self.order_seq <= highest:
            _fail(
                f"order_seq {self.order_seq} must be greater than every carried order seq "
                f"({highest}); the next id would collide"
            )
        if self.watermark_bar and self.watermark_bar not in _BAR_INDEX:
            _fail(
                f"watermark_bar must be '' or one of the 48 bar labels, got {self.watermark_bar!r}"
            )
        object.__setattr__(self, "bought_today", _canonical_bought_today(self.bought_today))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BarEngineState):
            return False
        return self._comparison_key() == other._comparison_key()

    def _comparison_key(self) -> tuple[Any, ...]:
        # The book is a mutable collaborator with identity equality; comparing
        # its rows makes two independently copied states deep-equal without
        # editing orders.py (todo 11's frozen surface).
        return (
            self.account_id,
            self.market,
            self.trade_date,
            self.cash,
            self.positions,
            self.book.rows(),
            self.order_seq,
            tuple(sorted(self.bought_today.items())),
            self.watermark_bar,
        )

    @classmethod
    def from_account(
        cls,
        account: PaperTradeAccount,
        market: str = "",
        *,
        trade_date: str = "",
    ) -> "BarEngineState":
        """The session state for ``account`` on ``trade_date`` (or its watermark).

        ``market=""`` reuses the account's market; a different supplied market
        is refused.  ``trade_date=""`` falls back to the account watermark,
        then the account start date — a caller opening a NEW session (the
        session runner) must pass the day explicitly, because the watermark
        still names the last CLOSED day.

        The carried intraday progress (``watermark_bar`` and the T+1 counter)
        is restored ONLY when it belongs to ``day``: a new session must start
        with an empty counter, because yesterday's purchases are sellable and
        today's purchases are not.
        """
        if not isinstance(account, PaperTradeAccount):
            _fail(f"account must be a PaperTradeAccount, got {type(account).__name__}")
        chosen_market = market or account.market
        if chosen_market != account.market:
            _fail(f"market {chosen_market!r} disagrees with the account's {account.market!r}")
        day = trade_date or account.watermark or account.start_date
        try:
            book = OrderBook(account.account_id, day, rows=account.open_orders)
        except InvalidTransition as exc:
            _fail(f"carried open orders cannot be restored for {day}: {exc}")
        carried = _carries_session_for(account, day)
        return cls(
            account_id=account.account_id,
            market=chosen_market,
            trade_date=day,
            cash=Decimal(str(account.cash)),
            positions=tuple(account.positions),
            book=book,
            order_seq=account.order_seq,
            bought_today=account.bought_today if carried else {},
            watermark_bar=account.watermark_bar if carried else "",
        )

    def to_account(
        self,
        account: PaperTradeAccount,
        *,
        session_phase: str = "",
        slippage_model: str = "",
        engine_fingerprint: str | None = None,
    ) -> PaperTradeAccount:
        """Return ``account`` advanced to this bar's ledger and resting book.

        Only the RESTING rows are carried (``book.open_orders()``): terminal
        rows are per-bar records (``orders.jsonl``), not session state.  The
        day-level ``watermark`` is deliberately untouched — it moves when the
        session closes; ``bought_today`` is carried as whole shares so the
        next bar's T+1 gate sees the same counter this bar did (the 15:00
        settlement has already emptied it on the closing bar).  Empty
        ``session_phase`` / ``slippage_model`` keep the account's current
        values.
        """
        if not isinstance(account, PaperTradeAccount):
            _fail(f"account must be a PaperTradeAccount, got {type(account).__name__}")
        if account.account_id != self.account_id or account.market != self.market:
            _fail("account identity does not match this engine state")
        if session_phase and session_phase not in SESSION_PHASES:
            _fail(f"unknown session_phase {session_phase!r}")
        return replace(
            account,
            cash=float(self.cash),
            positions=tuple(self.positions),
            open_orders=tuple(self.book.open_orders()),
            order_seq=self.order_seq,
            watermark_bar=self.watermark_bar,
            bought_today=_carried_share_counts(self.bought_today),
            session_phase=session_phase or account.session_phase,
            slippage_model=slippage_model or account.slippage_model,
            engine_fingerprint=(
                account.engine_fingerprint if engine_fingerprint is None else engine_fingerprint
            ),
        )


def _carries_session_for(account: PaperTradeAccount, day: str) -> bool:
    """Whether the account's carried intraday session is ``day``'s own.

    ``watermark`` is the last CLOSED day, so an in-progress session opened
    after it.  The decidable rules:

    * ``watermark == day`` — the requested day's own session (already closed):
      carry, so a re-open shows the day it closed with;
    * no session is in progress when ``watermark_bar`` is empty or already the
      last continuous label — a finished session leaves no counter to carry;
    * before any close (``watermark is None``) the in-progress session opened
      on ``start_date``, so only that day carries — a later day starts empty;
    * after a close, any later day is the in-progress session's day (an
      earlier one is refused by the caller's monotone label gate).
    """
    if account.watermark == day:
        return True
    if not account.watermark_bar or account.watermark_bar == bar_clock.BAR_LABELS[-1]:
        return False
    if account.watermark is None:
        return day == account.start_date
    return day > account.watermark


def _carried_share_counts(counter: Mapping[str, Decimal]) -> Mapping[str, int]:
    """The T+1 counter as whole shares, zero rows dropped (the account shape)."""
    counts: dict[str, int] = {}
    for symbol, qty in counter.items():
        value = _decimal(qty, f"bought_today[{symbol!r}]")
        if value != value.to_integral_value():
            _fail(f"bought_today[{symbol!r}] must be a whole number of shares, got {value}")
        if value > 0:
            counts[symbol] = int(value)
    return MappingProxyType(dict(sorted(counts.items())))


def _book_identity(book: OrderBook) -> tuple[str, str]:
    """Recover a book's account/day through its PUBLIC id allocator.

    ``OrderBook`` exposes no account/date accessors; ``allocate_order_id``
    formats them into the deterministic id grammar, and ``parse_order_id``
    reads the same segments back.  Purely functional: seq 0 is assignable and
    no row is written.
    """
    try:
        probe, _ = book.allocate_order_id("09:35", 0)
        account_id, trade_date, _, _ = parse_order_id(probe)
    except (InvalidTransition, OrderIdError) as exc:
        _fail(f"order book identity cannot be resolved: {exc}")
    return account_id, trade_date


def _canonical_positions(positions: Sequence[Position]) -> tuple[Position, ...]:
    if isinstance(positions, (str, bytes)) or not isinstance(positions, Sequence):
        _fail(f"positions must be a sequence, got {type(positions).__name__}")
    seen: set[str] = set()
    rows: list[Position] = []
    for index, position in enumerate(positions):
        if not isinstance(position, Position):
            _fail(f"positions[{index}] must be a Position, got {type(position).__name__}")
        _text(position.symbol, f"positions[{index}].symbol")
        if position.symbol in seen:
            _fail(f"positions[{index}] duplicates symbol {position.symbol!r}")
        seen.add(position.symbol)
        qty = _decimal(position.qty, f"positions[{index}].qty")
        if qty < 0:
            _fail(f"positions[{index}].qty must be >= 0, got {qty}")
        cost = _decimal(position.avg_cost, f"positions[{index}].avg_cost")
        if cost < 0:
            _fail(f"positions[{index}].avg_cost must be >= 0, got {cost}")
        if qty > 0:
            rows.append(Position(symbol=position.symbol, qty=float(qty), avg_cost=float(cost)))
    return tuple(sorted(rows, key=lambda item: item.symbol))


def _canonical_bought_today(raw: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    if not isinstance(raw, Mapping):
        _fail(f"bought_today must be a mapping, got {type(raw).__name__}")
    result: dict[str, Decimal] = {}
    for symbol, qty in raw.items():
        _text(symbol, "bought_today key")
        value = _decimal(qty, f"bought_today[{symbol!r}]")
        if value < 0:
            _fail(f"bought_today[{symbol!r}] must be >= 0, got {value}")
        result[symbol] = value
    return MappingProxyType(dict(sorted(result.items())))


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        _fail(f"{path} must be a number, got {type(value).__name__}")
    try:
        parsed = money(value)
    except ArithmeticError:
        _fail(f"{path} must be a plain decimal, got {value!r}")
    if not parsed.is_finite():
        _fail(f"{path} must be finite, got {value!r}")
    return parsed


def _canonical_date(value: Any) -> str:
    _text(value, "trade_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail(f"trade_date must be YYYY-MM-DD, got {value!r}")
    if parsed.isoformat() != value:
        _fail(f"trade_date must be canonical YYYY-MM-DD, got {value!r}")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{path} must be a non-empty string, got {value!r}")
    return value
