"""AM-14 rejection ladder, evaluated per open order and per bar (P1-17).

:func:`rejection_for` walks :data:`~.rules.REJECTION_ORDER` EXACTLY once, in
order, and returns the FIRST applicable frozen token (``None`` when the order
may be priced).  The ladder mirrors the v1.2 day engine's implementation
(``engine.py`` ``match_order``) — by re-implementing the walk over the frozen
tuple, never by importing the day engine — and every slot delegates its
arithmetic to :class:`~.rules.MarketRules` / :mod:`fill_rules`.  The
documentation table (:data:`REJECTION_TABLE`) carries each slot's trigger and
the test that pins it.

Frozen details this module must never "improve":

* **A day limit order is not a rejection.**  ``limit_not_marketable`` applies
  to ``ioc``/``fok`` only; as in v1.2, a marketable test is a *fill* test, so
  a resting ``day`` limit simply waits for a later bar.  Rejecting it would
  delete the v1.3 resting book.
* **``t_plus_1`` cannot steal an oversell.**  ``MarketRules
  .sell_position_rejection`` keeps the two slots disjoint (``qty > held`` is
  ``insufficient_position`` even when today's buys would also make it
  unsellable); this module compares the primitive's answer per slot rather
  than re-deriving the arithmetic.
* **An unknown band skips the band slots** (``continue``, not "reject"):
  ``MarketRules.band_rejection`` returns ``None`` when a band is absent, so a
  missing table row never fabricates a limit hit.
* **A no-trade bar is not a suspension.**  ``suspended`` fires ONLY for a
  symbol in the injected ``RejectionContext.suspended_symbols`` truth.  A bar
  with no continuous print (a missing frame, a liquidity gap or a zero-volume
  label) leaves the order resting so the next traded bar can price it; the
  engine reports that fact as a ``data_missing`` warning, never as a terminal
  rejection.  Non-tradable rows also yield no reference price, so the
  price-dependent slots (band, marketability, cash) skip rather than infer a
  price the market did not print.
* **An unknown symbol arrives as ``bar_row is None``.**  ``apply_bar`` passes
  ``None`` for a symbol with no row in this bar's timeline (the interface
  change that makes this slot reachable at all).

The dynamic ledger — cash, positions, ``bought_today`` — is read from the
authoritative :class:`~.bar_state.BarEngineState` on every call.  A frozen
context is built ONCE per session (the session runner supplies the real
calendar/bands/CA flags), so snapshotting cash or positions into it would go
stale after the first fill; only the session-static facts live in
:class:`RejectionContext`.

Deterministic: no clock, no I/O, no randomness, no module-level mutable state,
and no argument is mutated.  :func:`make_reject_hook` closes over an immutable
context, so the returned callable is safe to reuse for every bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, NoReturn

from .bar_state import BarEngineState
from .fill_rules import is_marketable
from .rules import REJECTION_ORDER, MarketRules, money
from .schema import PATH_MODELS, OrderState
from .timeline import BarRow, tradable

if TYPE_CHECKING:  # pragma: no cover - typing only; no runtime cycle
    from .bar_engine import RejectHook

__all__ = [
    "REJECTION_TABLE",
    "RejectionContext",
    "RejectionError",
    "make_reject_hook",
    "rejection_for",
]

_ZERO = Decimal("0")
#: TIFs whose unfilled remainder is terminal at bar end.  A ``day`` order rests.
_IOC_FOK = frozenset({"ioc", "fok"})


class RejectionError(ValueError):
    """Malformed ladder input: a bad context, order, state or bar row.

    In-package signal (never a wire rejection): the frozen vocabulary has no
    "bad hook input" token, so a malformed call is reported as an error the
    caller maps to ``INVALID_REQUEST`` rather than as a plausible reason code.
    """


#: ``(reason_code, trigger, test id)`` — the documentation table the evidence
#: cites.  Its first column MUST equal :data:`~.rules.REJECTION_ORDER` (the
#: ladder walk is driven by the frozen tuple, this table only documents it).
REJECTION_TABLE: tuple[tuple[str, str, str], ...] = (
    (
        "unknown_symbol",
        "no bar row for the symbol at this label (outside the timeline universe)",
        "test_unknown_symbol_when_the_symbol_has_no_bar_row",
    ),
    (
        "non_trading_day",
        "the session calendar marks the day closed",
        "test_non_trading_day_when_the_session_calendar_is_closed",
    ),
    (
        "suspended",
        "the symbol is flagged suspended by the injected session truth "
        "(a bar with no print is a no-trade, never a suspension)",
        "test_suspended_only_when_the_symbol_is_flagged",
    ),
    (
        "corporate_action_unresolved",
        "the symbol carries an unresolved corporate action",
        "test_corporate_action_unresolved_when_the_symbol_is_flagged",
    ),
    (
        "lot_size",
        "qty violates the market's board lot (never silently rounded)",
        "test_lot_size_when_the_quantity_breaks_the_board_lot",
    ),
    (
        "limit_up",
        "BUY bar reference at or above the day's upper band",
        "test_limit_up_when_a_buy_reference_sits_at_the_upper_band",
    ),
    (
        "limit_down",
        "SELL bar reference at or below the day's lower band",
        "test_limit_down_when_a_sell_reference_sits_at_the_lower_band",
    ),
    (
        "limit_not_marketable",
        "ioc/fok whose limit cannot match the unslipped reference (a day order rests)",
        "test_limit_not_marketable_only_for_ioc_and_fok",
    ),
    (
        "t_plus_1",
        "cn_a SELL within the holding but above the sellable quantity",
        "test_t_plus_one_blocks_a_same_session_sell",
    ),
    (
        "insufficient_position",
        "SELL above the held quantity",
        "test_insufficient_position_when_the_sell_exceeds_the_holding",
    ),
    (
        "insufficient_cash",
        "BUY whose notional plus fee exceeds the ledger cash",
        "test_insufficient_cash_when_the_buy_cost_exceeds_cash",
    ),
)


def _fail(message: str) -> NoReturn:
    raise RejectionError(message)


# ── the per-session (static) context ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RejectionContext:
    """Session-static facts the ladder needs; built once, never per bar.

    ``trading_day`` is the calendar truth (AM-6), ``suspended_symbols`` /
    ``corporate_action_unresolved`` are the injected flags (the session runner
    wires the real sources), ``bands`` is the day's ``symbol -> (lower, upper)`` cn_a
    band table (an absent entry is UNKNOWN, so the band slots are skipped), and
    ``rules`` is the market rule matrix.  ``path_model`` selects which reference
    the band slots compare, so the ladder and :func:`~.fill_rules.fill_decision`
    read the SAME price.

    The ledger is deliberately NOT here: read it from ``state``
    (:meth:`held` / :meth:`sellable` are thin, non-snapshotting accessors).
    """

    trading_day: bool
    suspended_symbols: frozenset[str]
    corporate_action_unresolved: frozenset[str]
    bands: Mapping[str, tuple[float, float]]
    rules: MarketRules
    path_model: str = "bar_median"

    def __post_init__(self) -> None:
        if not isinstance(self.trading_day, bool):
            _fail(f"trading_day must be a bool, got {type(self.trading_day).__name__}")
        object.__setattr__(
            self, "suspended_symbols", _symbol_set(self.suspended_symbols, "suspended_symbols")
        )
        object.__setattr__(
            self,
            "corporate_action_unresolved",
            _symbol_set(self.corporate_action_unresolved, "corporate_action_unresolved"),
        )
        object.__setattr__(self, "bands", _band_table(self.bands))
        if not isinstance(self.rules, MarketRules):
            _fail(f"rules must be a MarketRules, got {type(self.rules).__name__}")
        if self.path_model not in PATH_MODELS:
            _fail(f"path_model must be one of {PATH_MODELS}, got {self.path_model!r}")

    def held(self, state: BarEngineState, symbol: str) -> Decimal:
        """Shares held for ``symbol`` in ``state`` (0 when there is no row)."""
        for position in state.positions:
            if position.symbol == symbol:
                return money(position.qty)
        return _ZERO

    def sellable(self, state: BarEngineState, symbol: str) -> Decimal:
        """Shares sellable today: held − today's buys when the market is T+1."""
        today = money(state.bought_today.get(symbol, _ZERO)) if self.rules.t_plus_1 else _ZERO
        return self.held(state, symbol) - today


def _symbol_set(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, frozenset):
        _fail(f"{label} must be a frozenset, got {type(value).__name__}")
    for symbol in value:
        if not isinstance(symbol, str) or not symbol:
            _fail(f"{label} members must be non-empty strings, got {symbol!r}")
    return value


def _band_table(value: Any) -> Mapping[str, tuple[float, float]]:
    if not isinstance(value, Mapping):
        _fail(f"bands must be a mapping, got {type(value).__name__}")
    bands: dict[str, tuple[float, float]] = {}
    for symbol, band in value.items():
        if not isinstance(symbol, str) or not symbol:
            _fail(f"bands keys must be non-empty strings, got {symbol!r}")
        if not isinstance(band, tuple) or len(band) != 2:
            _fail(f"bands[{symbol!r}] must be a (lower, upper) tuple, got {band!r}")
        lower = _positive_band(band[0], f"bands[{symbol!r}]")
        upper = _positive_band(band[1], f"bands[{symbol!r}]")
        if lower > upper:
            _fail(f"bands[{symbol!r}] lower {lower} exceeds upper {upper}")
        bands[symbol] = (float(lower), float(upper))
    return MappingProxyType(bands)


def _positive_band(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        _fail(f"{label} must be a number, got {type(value).__name__}")
    try:
        parsed = money(value)
    except ArithmeticError:
        _fail(f"{label} must be a plain decimal, got {value!r}")
    if not parsed.is_finite() or parsed <= _ZERO:
        _fail(f"{label} must be finite and > 0, got {value!r}")
    return parsed


# ── the ladder ───────────────────────────────────────────────────────────


def rejection_for(
    order: OrderState,
    bar_row: BarRow | None,
    state: BarEngineState,
    ctx: RejectionContext,
) -> str | None:
    """The first frozen AM-14 token that applies to ``order``, else ``None``.

    Walks :data:`~.rules.REJECTION_ORDER` in its declared order; the returned
    token is therefore the highest-priority violated rule, exactly as the v1.2
    engine reports it.  A code present in the frozen order but missing from
    this dispatcher is a :class:`RejectionError` (fail closed — never a silent
    "allowed").
    """
    _require(order, bar_row, state, ctx)
    facts = _Facts(order=order, bar_row=bar_row, state=state, ctx=ctx)
    for code in REJECTION_ORDER:
        if _slot_applies(code, facts):
            return code
    return None


def make_reject_hook(ctx: RejectionContext) -> "RejectHook":
    """A pure :data:`~.bar_engine.RejectHook` closing over the frozen ``ctx``.

    The hook neither reads a clock nor touches I/O, and it never mutates its
    arguments (``RejectionContext`` is frozen; ``state`` is read-only here).
    """
    if not isinstance(ctx, RejectionContext):
        _fail(f"ctx must be a RejectionContext, got {type(ctx).__name__}")

    def reject_hook(
        order: OrderState, bar_row: BarRow | None, state: BarEngineState
    ) -> str | None:
        return rejection_for(order, bar_row, state, ctx)

    return reject_hook


@dataclass(frozen=True, slots=True)
class _Facts:
    """One ladder evaluation's inputs plus derived, side-effect-free reads."""

    order: OrderState
    bar_row: BarRow | None
    state: BarEngineState
    ctx: RejectionContext

    def reference(self) -> Decimal | None:
        """The bar's path-model reference, or ``None`` when it has no price.

        A bar that did not trade (no continuous print, or zero volume) has no
        reference: pricing an order off a price the market did not print would
        let a no-trade interval reject or fill it.
        """
        row = self.bar_row
        if not tradable(row):
            return None
        value = row.reference_close if self.ctx.path_model == "bar_close" else row.reference_median
        return None if value is None else money(value)

    def band_code(self, side: str) -> str | None:
        """``MarketRules.band_rejection`` for the day band, or ``None`` unknown."""
        band = self.ctx.bands.get(self.order.symbol)
        reference = self.reference()
        if band is None or reference is None:
            return None
        lower, upper = band
        return self.ctx.rules.band_rejection(side, float(reference), upper, lower)

    def sell_rejection(self) -> str | None:
        """The frozen ``t_plus_1`` / ``insufficient_position`` discrimination."""
        return self.ctx.rules.sell_position_rejection(
            self.order.side,
            self.order.qty,
            self.ctx.held(self.state, self.order.symbol),
            money(self.state.bought_today.get(self.order.symbol, _ZERO)),
        )


def _slot_applies(code: str, facts: _Facts) -> bool:
    """Whether ONE frozen slot fires; the caller enforces the slot order."""
    order, row, ctx = facts.order, facts.bar_row, facts.ctx
    match code:
        case "unknown_symbol":
            return row is None or row.symbol != order.symbol
        case "non_trading_day":
            return ctx.trading_day is False
        case "suspended":
            return order.symbol in ctx.suspended_symbols
        case "corporate_action_unresolved":
            return order.symbol in ctx.corporate_action_unresolved
        case "lot_size":
            return ctx.rules.lot_violation(order.symbol, order.qty)
        case "limit_up":
            return order.side == "buy" and facts.band_code("buy") == "limit_up"
        case "limit_down":
            return order.side == "sell" and facts.band_code("sell") == "limit_down"
        case "limit_not_marketable":
            if order.tif not in _IOC_FOK:
                return False  # a day order rests (the v1.3 resting-book rule)
            reference = facts.reference()
            return reference is not None and not is_marketable(order, reference)
        case "t_plus_1":
            return facts.sell_rejection() == "t_plus_1"
        case "insufficient_position":
            return facts.sell_rejection() == "insufficient_position"
        case "insufficient_cash":
            reference = facts.reference()
            if reference is None:
                return False
            return (
                ctx.rules.cash_rejection(
                    order.side, order.qty, float(reference), facts.state.cash
                )
                == "insufficient_cash"
            )
        case _:
            _fail(
                f"REJECTION_ORDER slot {code!r} has no implementation; the frozen "
                f"ladder is {REJECTION_ORDER}"
            )


def _require(
    order: OrderState,
    bar_row: BarRow | None,
    state: BarEngineState,
    ctx: RejectionContext,
) -> None:
    if not isinstance(order, OrderState):
        _fail(f"order must be an OrderState, got {type(order).__name__}")
    if not isinstance(state, BarEngineState):
        _fail(f"state must be a BarEngineState, got {type(state).__name__}")
    if not isinstance(ctx, RejectionContext):
        _fail(f"ctx must be a RejectionContext, got {type(ctx).__name__}")
    if bar_row is not None:
        if not isinstance(bar_row, BarRow):
            _fail(f"bar_row must be a BarRow or None, got {type(bar_row).__name__}")
        if bar_row.trade_date != state.trade_date:
            _fail(
                f"bar_row trade_date {bar_row.trade_date!r} disagrees with the state's "
                f"{state.trade_date!r}"
            )
    if order.status != "new":
        _fail(f"rejection_for applies to a resting (new) order, got {order.status!r}")
