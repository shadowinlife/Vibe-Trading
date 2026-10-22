"""Cash / position / NAV settlement for the pt-v1.3 per-bar engine (P1-16).

Split out of :mod:`bar_engine` so the loop stays inside the repository's
800-line cap; the loop owns decisions, this module owns the ledger arithmetic.
The conventions are MIRRORED from the v1.2 day engine (never imported from
``_DayEngine``):

* notional and fees are 2dp ``Decimal`` on :func:`~.rules.quantize_money`;
* ``avg_cost`` is the fee-exclusive weighted average fill price, quantized to
  4dp ``ROUND_HALF_UP`` on every mutation, so chaining equals the RM
  round-trip;
* a buy adds to ``bought_today`` (T+1 availability), a sell never rewrites the
  cost basis, and a fully closed position is dropped;
* NAV values each position at the bar's ``reference_close``, falling back to
  ``avg_cost`` with a deterministic warning when that bar has no usable price.

Pure: no I/O, no clock, no randomness, no module-level mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from .rules import MarketRules, money, quantize_money
from .schema import CanceledOrder, Fill, NavPoint, Position
from .timeline import TimelineError

__all__ = ["BarLedger", "qty_of", "sell_proceeds", "valuation_price"]

_Q4 = Decimal("0.0001")


def qty_of(position: Position) -> Decimal:
    """The position quantity as an exact ``Decimal`` (wire floats in)."""
    return money(position.qty)


def sell_proceeds(qty: Decimal, price: Decimal, rules: MarketRules) -> Decimal:
    """Net cash a sell adds: 2dp notional minus the sell fee (may be negative)."""
    notional = quantize_money(qty * money(price))
    return notional - rules.fee("sell", float(qty), float(price))


def valuation_price(timeline: Any, label: str, symbol: str) -> Decimal | None:
    """The bar's ``reference_close`` for ``symbol``, or ``None`` (no price)."""
    try:
        row = timeline.row(label, symbol)
    except TimelineError:
        return None
    if row.status != "present" or row.reference_close is None:
        return None
    return money(row.reference_close)


@dataclass(slots=True)
class _Holding:
    qty: Decimal
    avg_cost: Decimal


class BarLedger:
    """Mutable 2dp settlement accumulator for one bar (private to the loop).

    ``cash`` / ``positions`` / ``bought_today`` are the ledger; ``fills`` and
    ``canceled`` are the bar's ordered wire rows; ``warnings`` collects the
    deterministic valuation fallbacks.
    """

    __slots__ = ("trade_date", "cash", "positions", "bought_today", "fills", "canceled", "warnings")

    def __init__(
        self,
        trade_date: str,
        cash: Decimal,
        positions: Sequence[Position],
        bought_today: Mapping[str, Decimal],
    ) -> None:
        self.trade_date = trade_date
        self.cash = quantize_money(cash)
        self.positions: dict[str, _Holding] = {}
        for position in positions:
            qty = qty_of(position)
            if qty > 0:
                self.positions[position.symbol] = _Holding(qty, money(position.avg_cost))
        self.bought_today = {symbol: money(qty) for symbol, qty in bought_today.items()}
        self.fills: list[Fill] = []
        self.canceled: list[CanceledOrder] = []
        self.warnings: list[str] = []

    def sellable(self, symbol: str, t_plus_1: bool) -> Decimal:
        """Shares available to sell: held minus today's buys when T+1 applies."""
        holding = self.positions.get(symbol)
        total = holding.qty if holding is not None else Decimal("0")
        today = self.bought_today.get(symbol, Decimal("0")) if t_plus_1 else Decimal("0")
        return total - today

    def adopt(
        self, cash: Decimal, positions: Sequence[Position], bought_today: Mapping[str, Decimal]
    ) -> None:
        """Replace the ledger from a session-end hook result (fills preserved)."""
        self.cash = quantize_money(cash)
        self.positions = {}
        for position in positions:
            qty = qty_of(position)
            if qty > 0:
                self.positions[position.symbol] = _Holding(qty, money(position.avg_cost))
        self.bought_today = {symbol: money(qty) for symbol, qty in bought_today.items()}

    def fill(self, row: Any, price: Decimal, rules: MarketRules) -> None:
        """Settle one all-or-none fill (``row.qty`` is the filled quantity)."""
        qty = money(row.qty)
        price = money(price)
        notional = quantize_money(qty * price)
        fee = rules.fee(row.side, row.qty, float(price))
        if row.side == "buy":
            self.cash = quantize_money(self.cash - notional - fee)
            holding = self.positions.get(row.symbol)
            if holding is None or holding.qty <= 0:
                self.positions[row.symbol] = _Holding(
                    qty, price.quantize(_Q4, rounding=ROUND_HALF_UP)
                )
            else:
                total = holding.qty + qty
                basis = (holding.qty * holding.avg_cost + qty * price) / total
                holding.qty = total
                holding.avg_cost = basis.quantize(_Q4, rounding=ROUND_HALF_UP)
            self.bought_today[row.symbol] = self.bought_today.get(row.symbol, Decimal("0")) + qty
        else:
            self.cash = quantize_money(self.cash + notional - fee)
            holding = self.positions[row.symbol]
            holding.qty -= qty
            if holding.qty <= 0:
                del self.positions[row.symbol]
        self.fills.append(Fill(
            trade_date=self.trade_date,
            symbol=row.symbol,
            side=row.side,
            qty=row.qty,
            price=float(price),
            fee=float(fee),
        ))

    def positions_tuple(self) -> tuple[Position, ...]:
        """Open positions, symbol-sorted, zero rows dropped (the end-state shape)."""
        return tuple(
            Position(symbol=symbol, qty=float(holding.qty), avg_cost=float(holding.avg_cost))
            for symbol, holding in sorted(self.positions.items())
            if holding.qty > 0
        )

    def nav_point(self, timeline: Any, label: str) -> NavPoint:
        """One NAV row for this bar (positions marked at ``reference_close``)."""
        value = Decimal("0")
        for position in self.positions_tuple():
            price = valuation_price(timeline, label, position.symbol)
            if price is None:
                self.warnings.append(
                    f"valuation: {position.symbol} has no usable {label} close; "
                    "position valued at avg_cost"
                )
                price = money(position.avg_cost)
            value += quantize_money(qty_of(position) * price)
        value = quantize_money(value)
        return NavPoint(
            date=self.trade_date,
            nav=float(self.cash + value),
            cash=float(self.cash),
            position_value=float(value),
        )
