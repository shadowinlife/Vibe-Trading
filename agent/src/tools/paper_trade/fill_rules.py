"""Fill-price rules for pt-v1.3 paper fills (P1 intraday plan todo 14).

This module owns the intersection of three price rules, in this exact order:

1. **Marketability on the UNSLIPPED reference** — a ``None`` limit is a market
   order; a ``buy`` is marketable when ``limit >= reference`` and a ``sell``
   when ``limit <= reference`` (:func:`is_marketable`).  Slippage is NEVER
   applied here: a buy whose limit equals the reference stays marketable even
   though 10 bps of slippage would lift the slipped price above that limit.
2. **Adverse slippage** — :func:`~.slippage.apply_slippage` moves the reference
   against the trader (buy pays more, sell receives less) on the cent tick.
3. **Limit cap** — a buy fills at ``min(slipped, limit)``, a sell at
   ``max(slipped, limit)``.  The cap is quantized TOWARD the limit
   (``ROUND_DOWN`` for a buy, ``ROUND_UP`` for a sell), so a sub-cent limit can
   never be breached by the cent rounding.
4. **cn_a band clamp** — with a day band ``(lower, upper)`` the price is
   clamped to ``min(max(price, lower), upper)``.  A binding clamp is reported
   through ``FillDecision.band_applied``; it is a hard exchange rule, but it
   can only move the price toward the band and never past the unslipped
   reference while that reference is inside the band.
5. **Reject** — ``None`` when the resulting price is non-positive or outside
   the band.  The band rejection reuses the frozen code the v1.2 cn_a engine
   emits (``limit_up`` for a buy, ``limit_down`` for a sell,
   ``rules.MarketRules.band_rejection``), and an unmatchable limit order keeps
   the frozen ``limit_not_marketable`` token.  No new wire code is invented.

The reference and every band value are canonicalized to the package money tick
(:func:`~.rules.quantize_money`, 2dp ``ROUND_HALF_UP``) before use, matching
:mod:`slippage`.  A reference outside a supplied band is malformed input and
raises: clamping it would let a sell receive MORE than the reference (a
misleading success), and the exchange band by definition excludes it.

Price bands: :func:`band_for` reuses :mod:`limits_cn_a` (table first, heuristic
board ratio fallback) and returns ``(lower, upper)`` or ``None`` when no table
row and no previous close are available.  ``None`` means UNKNOWN and the band
check fails closed: the bound is never widened to ``±inf``.  The v1.2 engine
skips the check and raises a warning for an unknown band (the reused
``_blocked_by_limit`` unknown-band semantics); a v1.3 caller uses that same
explicit policy rather than fabricating a range.

The fingerprint surface (:func:`fingerprint_kwargs`) feeds
``state_hash.engine_fingerprint(**kwargs)``, so the selected model and the
order-book semantics are part of the audited digest.  The fingerprint carries
the model token only, as the v1.3 contract specifies; the knob itself
(``bps``/``amount``) is covered by ``compute_bar_state_hash``'s full
``SlippageSpec`` preimage.

``SlippageSpec`` has no ``__post_init__`` (todo 1's recorded risk), so every
spec-touching path validates through :func:`~.slippage.describe`; a hand-built
``SlippageSpec(model="none")`` that still carries ``bps=10`` is rejected rather
than fingerprinted as free.

Deterministic: no clock, no I/O, no randomness, no module-level mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Protocol

from .limits_cn_a import CnALimitsClient, get_limit_status, heuristic_limit_prices
from .rules import money, quantize_money
from .schema import OrderState, SlippageSpec
from .slippage import apply_slippage, describe
from .state_hash import ORDER_MODEL_VERSION

__all__ = [
    "BarRow",
    "FillDecision",
    "FillRuleError",
    "band_for",
    "fill_decision",
    "fill_price",
    "fingerprint_kwargs",
    "is_marketable",
]

_CENT = Decimal("0.01")
_SIDES = ("buy", "sell")
_LIMIT_NOT_MARKETABLE = "limit_not_marketable"
#: Mirrors ``rules.MarketRules.band_rejection``: the frozen cn_a band token is
#: selected by the order side, never by the clamp direction.
_BAND_REASON = {"buy": "limit_up", "sell": "limit_down"}


class FillRuleError(ValueError):
    """Malformed fill input (price, limit, band or order row)."""


class BarRow(Protocol):
    """The timeline fields ``band_for`` consumes — the documented adapter.

    ``timeline.BarRow`` is structurally compatible.  It deliberately carries no
    daily previous close, so ``band_for`` takes that scalar explicitly
    (``prev_close=``): the heuristic cn_a band is derived from the previous
    day's close, which is daily data, not a 5-minute bar field.
    """

    symbol: str
    trade_date: str


@dataclass(frozen=True, slots=True)
class FillDecision:
    """One fill decision: the price plus every fact needed to audit it."""

    marketable: bool
    price: Decimal | None
    reason_code: str | None
    band_applied: bool
    slippage: str


def is_marketable(order: OrderState, reference: float | Decimal) -> bool:
    """Whether ``order`` may match at the UNSLIPPED ``reference``.

    Returns ``True`` for a market order regardless of side.  Slippage is not
    applied here, and a non-marketable order is a normal ``False`` — malformed
    prices/limits raise :class:`FillRuleError` instead.
    """
    side, limit = _order_and_limit(order)
    return _marketable(side, limit, _reference_tick(reference))


def band_for(
    bar_row: BarRow,
    limits: CnALimitsClient | None = None,
    *,
    prev_close: float | Decimal | None = None,
) -> tuple[Decimal, Decimal] | None:
    """The day's cn_a ``(lower, upper)`` band for ``bar_row``, or ``None``.

    Reuses :mod:`limits_cn_a`: an injected table client is the truth source
    (ST ratio included), otherwise the board-ratio heuristic is derived from
    ``prev_close`` exactly as the v1.2 engine does.  ``prev_close`` is explicit
    because a 5-minute ``BarRow`` carries no daily close; without a table row
    and without it the band is unknown.  A half band (either side missing) is
    reported as unknown rather than fabricated, and a malformed or reversed
    band raises :class:`FillRuleError`.
    """
    symbol = _text(getattr(bar_row, "symbol", None), "bar_row.symbol")
    trade_date = _text(getattr(bar_row, "trade_date", None), "bar_row.trade_date")
    prior = None if prev_close is None else float(_positive_tick(prev_close, "prev_close"))

    if limits is not None:
        status = get_limit_status(limits, symbol, trade_date, prior)
        upper, lower = status.up_limit, status.down_limit
    elif prior is not None:
        upper, lower = heuristic_limit_prices(symbol, prior, is_st=False)
    else:
        return None
    if upper is None or lower is None:
        return None
    return _band_ticks((lower, upper))


def fill_price(
    order: OrderState,
    reference: float | Decimal,
    spec: SlippageSpec,
    *,
    band: tuple[float | Decimal, float | Decimal] | None = None,
) -> Decimal | None:
    """The execution price for one order/bar, or ``None`` when it cannot fill.

    Rule order: marketability on the unslipped reference → adverse slip →
    limit cap → band clamp → reject.  The returned price is a 2dp ``Decimal``
    on :func:`~.rules.quantize_money`.
    """
    price, _, _ = _resolve(order, reference, spec, band)
    return price


def fill_decision(
    order: OrderState,
    reference: float | Decimal,
    spec: SlippageSpec,
    *,
    band: tuple[float | Decimal, float | Decimal] | None = None,
) -> FillDecision:
    """The full decision behind :func:`fill_price` (price, reason, band echo).

    ``marketable`` stays ``True`` for a band rejection: the order was matchable,
    the band forbade the price.  ``slippage`` is the validated one-line echo of
    the model (``fixed_bps bps=10``).
    """
    echo = describe(spec)
    price, reason, band_applied = _resolve(order, reference, spec, band)
    return FillDecision(
        marketable=reason != _LIMIT_NOT_MARKETABLE,
        price=price,
        reason_code=reason,
        band_applied=band_applied,
        slippage=echo,
    )


def fingerprint_kwargs(spec: SlippageSpec) -> dict[str, str]:
    """``engine_fingerprint(**kwargs)`` inputs that make the model audited.

    Returns exactly ``{"slippage_model": spec.model, "order_model_version":
    ORDER_MODEL_VERSION}``; the spec is validated first, so a self-contradictory
    hand-built spec can never be fingerprinted as a model that would not run.
    """
    describe(spec)
    return {"slippage_model": spec.model, "order_model_version": ORDER_MODEL_VERSION}


# ── internals ────────────────────────────────────────────────────────────


def _resolve(
    order: OrderState,
    reference: float | Decimal,
    spec: SlippageSpec,
    band: tuple[float | Decimal, float | Decimal] | None,
) -> tuple[Decimal | None, str | None, bool]:
    """Apply the rule ladder once; both public entry points share this."""
    side, limit = _order_and_limit(order)
    reference_tick = _reference_tick(reference)
    if not _marketable(side, limit, reference_tick):
        return None, _LIMIT_NOT_MARKETABLE, False

    bounds = _band_ticks(band)
    if bounds is not None and not bounds[0] <= reference_tick <= bounds[1]:
        raise FillRuleError(
            f"reference {reference_tick} is outside the band [{bounds[0]}, {bounds[1]}]; "
            "a fill cannot be priced from a reference the exchange band excludes"
        )

    price = apply_slippage(reference_tick, side, spec)
    if limit is not None:
        price = _cap_at_limit(price, limit, side)

    band_applied = False
    if bounds is not None:
        lower, upper = bounds
        clamped = min(max(price, lower), upper)
        band_applied = clamped != price
        price = clamped

    if price <= 0 or (bounds is not None and not bounds[0] <= price <= bounds[1]):
        return None, _BAND_REASON[side], band_applied
    return quantize_money(price), None, band_applied


def _marketable(side: str, limit: Decimal | None, reference: Decimal) -> bool:
    if limit is None:
        return True
    return limit >= reference if side == "buy" else limit <= reference


def _cap_at_limit(price: Decimal, limit: Decimal, side: str) -> Decimal:
    # Round the cap AWAY from the limit so the cent tick can never breach it:
    # a buy cap floors (10.005 -> 10.00), a sell cap ceils (9.995 -> 10.00).
    rounding = ROUND_DOWN if side == "buy" else ROUND_UP
    cap = limit.quantize(_CENT, rounding=rounding)
    return min(price, cap) if side == "buy" else max(price, cap)


def _order_and_limit(order: object) -> tuple[str, Decimal | None]:
    if not isinstance(order, OrderState):
        raise FillRuleError(f"order must be an OrderState, got {type(order).__name__}")
    if order.side not in _SIDES:
        raise FillRuleError(f"order side must be one of {_SIDES}, got {order.side!r}")
    if order.limit is None:
        return order.side, None
    limit = _decimal(order.limit, "limit")
    if limit <= 0:
        raise FillRuleError(f"limit must be > 0, got {order.limit!r}")
    return order.side, limit


def _band_ticks(
    band: tuple[float | Decimal, float | Decimal] | None,
) -> tuple[Decimal, Decimal] | None:
    if band is None:
        return None
    if not isinstance(band, tuple) or len(band) != 2:
        raise FillRuleError(f"band must be a (lower, upper) tuple, got {band!r}")
    lower_raw, upper_raw = band
    lower = _positive_tick(lower_raw, "band lower")
    upper = _positive_tick(upper_raw, "band upper")
    if lower > upper:
        raise FillRuleError(f"band lower {lower} exceeds upper {upper}")
    return lower, upper


def _reference_tick(reference: object) -> Decimal:
    raw = _decimal(reference, "reference price")
    if raw <= 0:
        raise FillRuleError(f"reference price must be > 0, got {reference!r}")
    tick = quantize_money(raw)
    if tick <= 0:
        raise FillRuleError(f"reference price {raw} is below one cent")
    return tick


def _positive_tick(value: object, label: str) -> Decimal:
    raw = _decimal(value, label)
    if raw <= 0:
        raise FillRuleError(f"{label} must be > 0, got {value!r}")
    tick = quantize_money(raw)
    if tick <= 0:
        raise FillRuleError(f"{label} {raw} is below one cent")
    return tick


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise FillRuleError(f"{label} must be a number, got {type(value).__name__}")
    try:
        parsed = money(value)
    except ArithmeticError as exc:
        raise FillRuleError(f"{label} must be a plain decimal, got {value!r}") from exc
    if not parsed.is_finite():
        raise FillRuleError(f"{label} must be finite, got {value!r}")
    return parsed


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FillRuleError(f"{label} must be a non-empty string, got {value!r}")
    return value
