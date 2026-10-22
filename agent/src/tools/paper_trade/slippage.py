"""Adverse slippage models for pt-v1.3 paper fills (P1 intraday plan todo 13).

Every fill reference is moved AGAINST the trader before the fill is booked: a
buy pays more, a sell receives less.  The module is pure money arithmetic — no
I/O, no clock, no randomness — and it owns no rounding convention of its own:

* the reference is canonicalized and every result is quantized through
  :func:`~.rules.quantize_money` (2dp ``ROUND_HALF_UP``), the package's single
  money tick (VD-9);
* floats may cross the boundary because the wire speaks JSON numbers, but the
  first operation converts them with :func:`~.rules.money`
  (``Decimal(str(value))``) and every intermediate value is a ``Decimal``;
* a model/knob contradiction is REJECTED rather than normalized:
  :class:`~.schema.SlippageSpec` has no ``__post_init__``, so a hand-built
  ``SlippageSpec(model="none")`` still carries ``bps=Decimal("10")`` and must
  never be read as zero cost (the open risk recorded by todo 1).

Models (:data:`~.schema.SLIPPAGE_MODELS`), with ``ref`` the quantized bar
reference:

===============  =============================  ==============================
model            buy                            sell
===============  =============================  ==============================
``none``         ``ref``                        ``ref``
``fixed_bps``    ``ref * (1 + bps / 10_000)``   ``ref * (1 - bps / 10_000)``
``fixed_amount`` ``ref + amount``               ``ref - amount``
===============  =============================  ==============================

The 10 bps default is a decision (Q3): a ``10.00`` reference fills at ``10.01``
on a buy and ``9.99`` on a sell.  ``none`` is the zero-cost control used by
baselines and by tests that need the unslipped fill; it is still quantized to
the cent.

This module returns the slipped price ONLY.  Limit caps and cn_a price-band
clamps are the engine's job (todo 14), applied on this value — a clamp that
would move the price back in the trader's favor is a rejection there, not a
silent correction here.
"""

from __future__ import annotations

from decimal import Decimal

from .rules import money, quantize_money
from .schema import SIDES, SLIPPAGE_MODELS, SlippageSpec

__all__ = [
    "SlippageError",
    "apply_slippage",
    "describe",
    "slippage_amount",
    "spec_for_model",
]

_ONE = Decimal("1")
_BPS_PER_UNIT = Decimal("10000")


class SlippageError(ValueError):
    """Invalid slippage input (package-local; never clamped or normalized)."""


def apply_slippage(reference: float | Decimal, side: str, spec: SlippageSpec) -> Decimal:
    """Return the adverse execution price for ``reference`` on ``side``.

    ``reference`` may be a JSON float; it is converted with
    :func:`~.rules.money` before any arithmetic, quantized to the cent, and
    then moved against the trader by ``spec``.  The result is quantized again,
    so it is always a 2dp ``Decimal`` on :func:`~.rules.quantize_money`.

    Raises :class:`SlippageError` for an unknown side or model, a non-finite
    or non-positive reference, a negative/non-finite knob, a ``spec`` whose
    knob contradicts its model, or a result that is not positive.
    """
    _require_side(side)
    model, bps, amount = _validated(spec)
    reference_tick = _reference_tick(reference)

    match model:
        case "none":
            slipped = reference_tick
        case "fixed_bps":
            rate = bps / _BPS_PER_UNIT
            slipped = reference_tick * (_ONE + rate) if side == "buy" else reference_tick * (_ONE - rate)
        case "fixed_amount":
            slipped = reference_tick + amount if side == "buy" else reference_tick - amount
        case _:  # pragma: no cover - _validated narrowed model to SLIPPAGE_MODELS
            raise SlippageError(f"unknown slippage model {model!r}")

    price = quantize_money(slipped)
    if price <= 0:
        raise SlippageError(
            f"slipped price {price} is not positive (reference {reference_tick}, {describe(spec)})"
        )
    _require_adverse(price, reference_tick, side, spec)
    return price


def slippage_amount(reference: float | Decimal, side: str, spec: SlippageSpec) -> Decimal:
    """Signed adverse delta: ``apply_slippage(...) - reference``.

    Positive on a buy (extra paid) and negative on a sell (proceeds reduced);
    both operands are on the cent tick, so the delta is too.
    """
    return apply_slippage(reference, side, spec) - _reference_tick(reference)


def spec_for_model(model: str) -> SlippageSpec:
    """The spec a stored account ``slippage_model`` resolves to.

    ``none`` is the ZERO-cost control (``bps`` and ``amount`` are both 0), so an
    account created for a no-cost baseline really fills unslipped; ``fixed_bps``
    is the package default (10 bps).  ``fixed_amount`` stores no amount in the
    account document, so it cannot be resolved from the model token alone and is
    refused rather than silently substituted by another model — the bar request
    must carry the explicit spec instead.
    """
    if model not in SLIPPAGE_MODELS:
        raise SlippageError(f"unknown slippage model {model!r}; expected one of {SLIPPAGE_MODELS}")
    if model == "none":
        return SlippageSpec(model="none", bps=Decimal("0"), amount=Decimal("0"))
    if model == "fixed_bps":
        return SlippageSpec()
    raise SlippageError(
        "slippage_model 'fixed_amount' needs an explicit per-bar slippage amount; "
        "the account document stores no amount"
    )


def describe(spec: SlippageSpec) -> str:
    """Deterministic one-line echo of a VALIDATED spec (events / detail text).

    Only the knob that belongs to the model is shown, in canonical decimal
    text (``fixed_bps bps=10``, not ``bps=10.0``).  An invalid or
    self-contradictory spec raises :class:`SlippageError` instead of being
    echoed as a cost that would not be applied.
    """
    model, bps, amount = _validated(spec)
    match model:
        case "none":
            return "none"
        case "fixed_bps":
            return f"fixed_bps bps={_text(bps)}"
        case "fixed_amount":
            return f"fixed_amount amount={_text(amount)}"
        case _:  # pragma: no cover - _validated narrowed model to SLIPPAGE_MODELS
            raise SlippageError(f"unknown slippage model {model!r}")


# ── validation (the boundary for directly-constructed specs) ─────────────


def _require_side(side: object) -> None:
    if side not in SIDES:
        raise SlippageError(f"side must be one of {SIDES}, got {side!r}")


def _validated(spec: object) -> tuple[str, Decimal, Decimal]:
    """Validate ``spec`` and return ``(model, bps, amount)`` as Decimals.

    Enforces the one-knob-per-model invariant that
    :func:`~.schema.parse_slippage` applies at the wire boundary, because a
    directly-constructed :class:`SlippageSpec` bypasses that parser.
    """
    if not isinstance(spec, SlippageSpec):
        raise SlippageError(f"spec must be a SlippageSpec, got {type(spec).__name__}")
    model = spec.model
    if model not in SLIPPAGE_MODELS:
        raise SlippageError(f"unknown slippage model {model!r}; expected one of {SLIPPAGE_MODELS}")
    bps = _decimal(spec.bps, "slippage bps")
    amount = _decimal(spec.amount, "slippage amount")
    if bps < 0:
        raise SlippageError(f"slippage bps must be >= 0, got {_text(bps)}")
    if amount < 0:
        raise SlippageError(f"slippage amount must be >= 0, got {_text(amount)}")
    if model == "none" and (bps != 0 or amount != 0):
        raise SlippageError(
            f"model 'none' requires bps == 0 and amount == 0, got bps={_text(bps)}, amount={_text(amount)}"
        )
    if model == "fixed_bps" and amount != 0:
        raise SlippageError(f"model 'fixed_bps' requires amount == 0, got amount={_text(amount)}")
    if model == "fixed_amount" and bps != 0:
        raise SlippageError(f"model 'fixed_amount' requires bps == 0, got bps={_text(bps)}")
    return model, bps, amount


def _decimal(value: object, label: str) -> Decimal:
    """Parse a money value (JSON number, Decimal or decimal string) exactly."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise SlippageError(f"{label} must be a number, got {type(value).__name__}")
    try:
        parsed = money(value)
    except ArithmeticError as exc:
        raise SlippageError(f"{label} must be a plain decimal, got {value!r}") from exc
    if not parsed.is_finite():
        raise SlippageError(f"{label} must be finite, got {value!r}")
    return parsed


def _reference_tick(reference: object) -> Decimal:
    """Canonical reference price on the cent tick; rejects non-positive input."""
    raw = _decimal(reference, "reference price")
    if raw <= 0:
        raise SlippageError(f"reference price must be > 0, got {raw}")
    tick = quantize_money(raw)
    if tick <= 0:
        raise SlippageError(f"reference price {raw} is below one cent")
    return tick


def _require_adverse(price: Decimal, reference: Decimal, side: str, spec: SlippageSpec) -> None:
    """Assert the fill never lands on the trader's side of the reference."""
    if side == "buy" and price < reference:
        raise SlippageError(
            f"buy slipped below the reference ({price} < {reference}) under {describe(spec)}"
        )
    if side == "sell" and price > reference:
        raise SlippageError(
            f"sell slipped above the reference ({price} > {reference}) under {describe(spec)}"
        )


def _text(value: Decimal) -> str:
    """Canonical plain-decimal text (``Decimal('10.0')`` -> ``'10'``)."""
    if not value:
        return "0"
    return format(value.normalize(), "f")
