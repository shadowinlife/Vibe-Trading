"""Todo 14 gate — slippage integrated with the limit cap and cn_a bands.

What this module pins (P1 intraday plan todo 14; closes GAP-3):

* the marketability test runs on the UNSLIPPED reference — the key regression:
  a buy whose limit equals the reference is marketable even when 10 bps of
  adverse slippage would lift the slipped price above that limit;
* the fill price is the adverse slipped reference, capped so a buy never pays
  above its limit and a sell never receives below its limit;
* a cn_a price band clamps the fill into ``[lower, upper]`` and the clamp is
  reported through ``band_applied`` (never silently applied);
* an unknown band is ``None`` (never ``±inf``) and leaves the fill unclamped;
* non-positive/non-finite/reversed band or price input raises
  :class:`FillRuleError`, while an unmatchable limit order is a normal ``None``;
* the slippage model and the order-model version are exposed as
  ``engine_fingerprint`` kwargs, so the model is part of the audited digest.

The rules are pure: no clock, no I/O, no mutation, no module state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import pytest

from src.tools.paper_trade import fill_rules, state_hash
from src.tools.paper_trade.schema import REASON_CODES, OrderState, SlippageSpec, parse_slippage
from src.tools.paper_trade.slippage import SlippageError, apply_slippage
from src.tools.paper_trade.timeline import BarRow as TimelineBarRow

_REFERENCE = Decimal("10.00")


# ── fixtures ─────────────────────────────────────────────────────────────


def _spec(model: str, *, bps: str | None = None, amount: str | None = None) -> SlippageSpec:
    """Build a spec through the boundary parser (the only legal constructor)."""
    raw: dict[str, str] = {"model": model}
    if bps is not None:
        raw["bps"] = bps
    if amount is not None:
        raw["amount"] = amount
    return parse_slippage(raw)


def _order(
    side: str = "buy",
    limit: float | Decimal | None = None,
    *,
    symbol: str = "600519.SH",
) -> OrderState:
    return OrderState(
        order_id="pa_fill:2026-10-20:09:35:00000001",
        account_id="pa_fill",
        trade_date="2026-10-20",
        symbol=symbol,
        side=side,
        qty=100.0,
        remaining_qty=100.0,
        limit=limit,
        tif="day",
        status="new",
        bar_label="09:35",
    )


@dataclass(frozen=True)
class _BarRow:
    """The two timeline scalars ``band_for`` consumes (the adapter surface)."""

    symbol: str
    trade_date: str


class _FakeLimits:
    """Minimal ``CnALimitsClient`` fake: only ``stk_limit``/ST rows are populated."""

    def __init__(self, *, up: float | None = None, down: float | None = None, st: bool = False) -> None:
        self._up = up
        self._down = down
        self._st = st

    def get_stk_limit(self, trade_date: str, ts_code: str) -> pd.DataFrame:
        if self._up is None or self._down is None:
            return pd.DataFrame()
        return pd.DataFrame([{"up_limit": self._up, "down_limit": self._down}])

    def get_stk_st_daily(self, trade_date: str, ts_code: str) -> pd.DataFrame:
        return pd.DataFrame([{"type": "ST", "type_name": "ST"}]) if self._st else pd.DataFrame()

    def get_stk_suspend(self, trade_date: str, ts_code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_stk_limit_list(self, trade_date: str, ts_code: str) -> pd.DataFrame:
        return pd.DataFrame()


# ── acceptance table ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("side", "limit", "expected_price", "expected_reason"),
    [
        # buy limit exactly at the reference: the slip is 10.01, the limit caps
        # it back to 10.00 — the order must NOT be rejected.
        ("buy", Decimal("10.00"), Decimal("10.00"), None),
        # buy limit below the reference: unmatchable, a normal None.
        ("buy", Decimal("9.99"), None, "limit_not_marketable"),
        # sell limit above the reference: unmatchable.
        ("sell", Decimal("10.01"), None, "limit_not_marketable"),
        # sell limit exactly at the reference is marketable and capped at 10.00.
        ("sell", Decimal("10.00"), Decimal("10.00"), None),
        # no limit: the full adverse slip is booked.
        ("buy", None, Decimal("10.01"), None),
        ("sell", None, Decimal("9.99"), None),
    ],
)
def test_acceptance_table(
    side: str, limit: Decimal | None, expected_price: Decimal | None, expected_reason: str | None
) -> None:
    order = _order(side, limit)
    decision = fill_rules.fill_decision(order, _REFERENCE, _spec("fixed_bps"))

    assert decision.marketable is (expected_reason is None)
    assert decision.price == expected_price
    assert decision.reason_code == expected_reason
    assert decision.band_applied is False
    assert decision.slippage == "fixed_bps bps=10"
    if decision.reason_code is not None:
        assert decision.reason_code in REASON_CODES
    assert fill_rules.fill_price(order, _REFERENCE, _spec("fixed_bps")) == expected_price


def test_marketability_uses_the_unslipped_reference() -> None:
    """The regression: slipping first would reject a fill the limit allows."""
    order = _order("buy", Decimal("10.00"))
    spec = _spec("fixed_bps")

    slipped = apply_slippage(_REFERENCE, "buy", spec)
    assert slipped == Decimal("10.01")
    assert slipped > Decimal("10.00"), "10 bps lifts the slipped price above the limit"

    assert fill_rules.is_marketable(order, _REFERENCE) is True
    decision = fill_rules.fill_decision(order, _REFERENCE, spec)

    assert decision.marketable is True
    assert decision.reason_code is None
    assert decision.price == Decimal("10.00"), "the limit caps the adverse slip"


def test_marketability_is_symmetric_on_the_unslipped_reference() -> None:
    spec = _spec("fixed_bps")

    assert fill_rules.is_marketable(_order("buy", Decimal("10.00")), _REFERENCE) is True
    assert fill_rules.is_marketable(_order("sell", Decimal("10.00")), _REFERENCE) is True
    assert fill_rules.is_marketable(_order("buy", Decimal("10.01")), _REFERENCE) is True
    assert fill_rules.is_marketable(_order("sell", Decimal("9.99")), _REFERENCE) is True
    assert fill_rules.is_marketable(_order("buy", Decimal("9.99")), _REFERENCE) is False
    assert fill_rules.is_marketable(_order("sell", Decimal("10.01")), _REFERENCE) is False

    # A market order (limit None) is always marketable, both sides.
    for side in ("buy", "sell"):
        decision = fill_rules.fill_decision(_order(side), _REFERENCE, spec)
        assert decision.marketable is True and decision.reason_code is None


# ── the hard limit invariant: never worse than the stated limit ──────────


@pytest.mark.parametrize(
    ("side", "limit"),
    [
        ("buy", Decimal("10.00")),
        ("buy", Decimal("10.05")),
        ("buy", Decimal("10.005")),  # sub-cent: the cap floors to the cent
        ("buy", 10.001),
        ("sell", Decimal("10.00")),
        ("sell", Decimal("9.95")),
        ("sell", Decimal("9.995")),  # sub-cent: the cap ceils to the cent
        ("sell", 9.999),
    ],
)
def test_fill_never_lands_on_the_wrong_side_of_the_limit(side: str, limit: Decimal) -> None:
    decision = fill_rules.fill_decision(_order(side, limit), _REFERENCE, _spec("fixed_bps"))

    assert decision.price is not None
    assert decision.reason_code is None
    if side == "buy":
        assert decision.price <= Decimal(str(limit)), "a buy can never fill above its limit"
    else:
        assert decision.price >= Decimal(str(limit)), "a sell can never fill below its limit"


@pytest.mark.parametrize(
    ("side", "limit", "expected"),
    [
        ("buy", Decimal("10.005"), Decimal("10.00")),
        ("buy", Decimal("10.004"), Decimal("10.00")),
        ("sell", Decimal("9.995"), Decimal("10.00")),
        ("sell", Decimal("9.996"), Decimal("10.00")),
    ],
)
def test_sub_cent_limits_cap_to_the_stated_limit_not_a_rounded_limit(
    side: str, limit: Decimal, expected: Decimal
) -> None:
    """The cap rounds AWAY from the limit, so quantization can never breach it."""
    decision = fill_rules.fill_decision(_order(side, limit), _REFERENCE, _spec("fixed_bps"))

    assert decision.price == expected


# ── cn_a band clamp ──────────────────────────────────────────────────────


def test_limit_up_bar_clamps_a_buy_fill_to_the_upper_band() -> None:
    band = (Decimal("9.00"), Decimal("10.00"))
    decision = fill_rules.fill_decision(_order("buy"), _REFERENCE, _spec("fixed_bps"), band=band)

    assert apply_slippage(_REFERENCE, "buy", _spec("fixed_bps")) == Decimal("10.01")
    assert decision.price == Decimal("10.00")
    assert decision.band_applied is True, "the clamp must be reported, not silent"
    assert band[0] <= decision.price <= band[1]
    assert decision.reason_code is None


def test_limit_down_bar_clamps_a_sell_fill_to_the_lower_band() -> None:
    band = (Decimal("10.00"), Decimal("11.00"))
    decision = fill_rules.fill_decision(_order("sell"), _REFERENCE, _spec("fixed_bps"), band=band)

    assert decision.price == Decimal("10.00")
    assert decision.band_applied is True
    assert band[0] <= decision.price <= band[1]


def test_band_is_reported_unapplied_when_the_slipped_price_is_inside() -> None:
    decision = fill_rules.fill_decision(
        _order("buy"), _REFERENCE, _spec("fixed_bps"), band=(Decimal("9.00"), Decimal("11.00"))
    )

    assert decision.price == Decimal("10.01")
    assert decision.band_applied is False


def test_band_for_derives_the_heuristic_cn_a_band_from_the_previous_close() -> None:
    row = _BarRow(symbol="600519.SH", trade_date="2026-10-20")

    assert fill_rules.band_for(row, prev_close=10.0) == (Decimal("9.00"), Decimal("11.00"))


def test_band_for_prefers_the_injected_table_client() -> None:
    row = _BarRow(symbol="600519.SH", trade_date="2026-10-20")
    limits = _FakeLimits(up=11.0, down=9.0)

    assert fill_rules.band_for(row, limits, prev_close=10.0) == (Decimal("9.00"), Decimal("11.00"))
    # The table path needs no previous close at all.
    assert fill_rules.band_for(row, limits) == (Decimal("9.00"), Decimal("11.00"))


def test_band_for_uses_the_st_ratio_from_the_table() -> None:
    row = _BarRow(symbol="600519.SH", trade_date="2026-10-20")

    assert fill_rules.band_for(row, _FakeLimits(st=True), prev_close=10.0) == (Decimal("9.50"), Decimal("10.50"))
    assert fill_rules.band_for(row, _FakeLimits(up=10.5, down=9.5, st=True)) == (
        Decimal("9.50"),
        Decimal("10.50"),
    )


def test_band_for_accepts_the_real_timeline_row() -> None:
    row = TimelineBarRow(
        trade_date="2026-10-20",
        bar_label="09:35",
        bar_time="2026-10-20T09:35:00+08:00",
        symbol="600519.SH",
        phase="continuous",
        status="present",
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        volume=100.0,
        reference_median=10.0,
        reference_close=10.0,
    )

    assert fill_rules.band_for(row, prev_close=10.0) == (Decimal("9.00"), Decimal("11.00"))
    assert fill_rules.band_for(row) is None, "a BarRow alone carries no daily previous close"
    assert fill_rules.band_for(row, _FakeLimits(up=11.0, down=9.0)) == (Decimal("9.00"), Decimal("11.00"))


def test_unknown_band_is_none_never_infinity() -> None:
    row = _BarRow(symbol="600519.SH", trade_date="2026-10-20")

    assert fill_rules.band_for(row) is None
    assert fill_rules.band_for(row, _FakeLimits()) is None

    # With no band the slipped price stands unclamped: no fictional ±inf bound.
    decision = fill_rules.fill_decision(_order("buy"), _REFERENCE, _spec("fixed_bps"), band=None)
    assert decision.price == Decimal("10.01")
    assert decision.band_applied is False


# ── malformed input: raise; unmatchable order: normal None ───────────────


@pytest.mark.parametrize(
    ("band", "reason"),
    [
        ((Decimal("0"), Decimal("10.00")), "zero lower bound"),
        ((Decimal("-1.00"), Decimal("10.00")), "negative lower bound"),
        ((Decimal("9.00"), Decimal("0")), "zero upper bound"),
        ((Decimal("11.00"), Decimal("9.00")), "lower > upper"),
        ((Decimal("9.00"),), "not a pair"),
        (None, "not applicable"),
    ],
)
def test_malformed_band_raises_except_none(band: tuple[Decimal, ...] | None, reason: str) -> None:
    if band is None:
        # ``None`` is the documented "unknown band", not malformed input.
        assert fill_rules.fill_decision(_order("buy"), _REFERENCE, _spec("fixed_bps"), band=None).price
        return
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.fill_price(_order("buy"), _REFERENCE, _spec("fixed_bps"), band=band)


def test_a_string_band_is_not_a_pair() -> None:
    # A 2-char string would unpack into two "values"; the type gate refuses it.
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.fill_price(_order("buy"), _REFERENCE, _spec("fixed_bps"), band="99")  # type: ignore[arg-type]


@pytest.mark.parametrize("reference", [0, -1, -0.01, float("nan"), float("inf"), float("-inf"), True])
def test_non_positive_or_non_finite_reference_raises(reference: object) -> None:
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.is_marketable(_order("buy"), reference)


@pytest.mark.parametrize("limit", [0, -1, -0.01, float("nan"), float("inf")])
def test_non_positive_or_non_finite_limit_raises(limit: float) -> None:
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.is_marketable(_order("buy", limit), _REFERENCE)


def test_a_reference_outside_the_band_is_malformed_input() -> None:
    """A reference the exchange band excludes cannot price a fill honestly."""
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.fill_price(_order("buy"), Decimal("12.00"), _spec("fixed_bps"), band=(Decimal("9.00"), Decimal("11.00")))
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.fill_price(_order("sell"), Decimal("8.00"), _spec("fixed_bps"), band=(Decimal("9.00"), Decimal("11.00")))


def test_unmatchable_order_is_a_normal_none_not_an_exception() -> None:
    """Even with a band present, a non-marketable limit stays a plain ``None``."""
    decision = fill_rules.fill_decision(
        _order("buy", Decimal("9.99")),
        _REFERENCE,
        _spec("fixed_bps"),
        band=(Decimal("9.00"), Decimal("11.00")),
    )

    assert decision.marketable is False
    assert decision.price is None
    assert decision.reason_code == "limit_not_marketable"
    assert fill_rules.fill_price(
        _order("buy", Decimal("9.99")), _REFERENCE, _spec("fixed_bps"), band=(Decimal("9.00"), Decimal("11.00"))
    ) is None


def test_malformed_order_or_side_raises() -> None:
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.is_marketable(object(), _REFERENCE)  # type: ignore[arg-type]
    with pytest.raises(fill_rules.FillRuleError):
        fill_rules.is_marketable(_order("hold"), _REFERENCE)


# ── fingerprint: the slippage model is audited ───────────────────────────


def test_fingerprint_kwargs_exposes_the_model_and_order_version() -> None:
    kwargs = fill_rules.fingerprint_kwargs(_spec("fixed_bps"))

    assert kwargs == {
        "slippage_model": "fixed_bps",
        "order_model_version": state_hash.ORDER_MODEL_VERSION,
    }


def test_fingerprint_differs_between_slippage_models() -> None:
    fixed = state_hash.engine_fingerprint(**fill_rules.fingerprint_kwargs(_spec("fixed_bps")))
    none = state_hash.engine_fingerprint(**fill_rules.fingerprint_kwargs(_spec("none")))

    assert fixed != none
    assert fixed == state_hash.engine_fingerprint(
        slippage_model="fixed_bps", order_model_version=state_hash.ORDER_MODEL_VERSION
    )
    assert none == state_hash.engine_fingerprint(
        slippage_model="none", order_model_version=state_hash.ORDER_MODEL_VERSION
    )


def test_fingerprint_refuses_a_self_contradictory_spec() -> None:
    # ``SlippageSpec`` has no __post_init__: model="none" still carries bps=10.
    with pytest.raises(SlippageError):
        fill_rules.fingerprint_kwargs(SlippageSpec(model="none"))
