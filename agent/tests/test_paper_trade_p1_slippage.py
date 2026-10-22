"""T13 — adverse slippage models for pt-v1.3 paper fills (P1 plan todo 13).

Scope is ``slippage.py`` ONLY (todo 15 extends the P1 test surface elsewhere;
this module stays a pure unit table for the three models).  Every case is
written against the module object — not a bound function — so the whole table
can be pointed at a mutated copy to prove it discriminates (recorded in the
todo-13 evidence).

What is pinned here:

* the plan's acceptance table (buy pays more, sell receives less) for all
  three models, including the Q3 ``fixed_bps=10`` default;
* the cent is the only tick: a half-cent tie rounds HALF_UP and a float input
  can never leak ``10.010000000000002`` into the fill;
* "never in the trader's favor" over a deterministic 200-reference sweep;
* rejected input: non-finite/negative/zero references, unknown side or model,
  a hand-built ``SlippageSpec`` whose knob contradicts its model, and a
  non-positive result — all raise :class:`SlippageError`, never a silent
  clamp.

Offline and deterministic: no clock, no randomness, no data fetch.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.tools.paper_trade import rules, slippage as slippage_mod
from src.tools.paper_trade.schema import SlippageSpec, parse_slippage

# ── helpers ──────────────────────────────────────────────────────────────


def _spec(model: str, *, bps: str | None = None, amount: str | None = None) -> SlippageSpec:
    """Build a spec through the boundary parser (the only legal constructor).

    ``SlippageSpec`` has no ``__post_init__``: a hand-built
    ``SlippageSpec(model="none")`` would still carry ``bps=Decimal("10")``.
    """
    raw: dict[str, str] = {"model": model}
    if bps is not None:
        raw["bps"] = bps
    if amount is not None:
        raw["amount"] = amount
    return parse_slippage(raw)


def _apply(reference: object, side: str, spec: SlippageSpec) -> Decimal:
    return slippage_mod.apply_slippage(reference, side, spec)  # type: ignore[arg-type]


# ── acceptance table ─────────────────────────────────────────────────────

ACCEPTANCE_CASES = (
    # (reference, side, model, bps, amount, expected)
    ("10.00", "buy", "fixed_bps", "10", None, "10.01"),
    ("10.00", "sell", "fixed_bps", "10", None, "9.99"),
    ("10.00", "buy", "fixed_amount", None, "0.02", "10.02"),
    ("10.00", "sell", "fixed_amount", None, "0.02", "9.98"),
    ("10.00", "buy", "none", None, None, "10.00"),
    ("10.00", "sell", "none", None, None, "10.00"),
    # a float reference crosses the wire as a JSON number; it is canonicalized
    # to the cent BEFORE the slip (1421.005 -> 1421.01), then quantized again.
    ("1421.005", "buy", "fixed_bps", "10", None, "1422.43"),
    ("1421.005", "sell", "fixed_bps", "10", None, "1419.59"),
)


@pytest.mark.parametrize(
    ("reference", "side", "model", "bps", "amount", "expected"),
    ACCEPTANCE_CASES,
)
def test_acceptance_table(
    reference: str, side: str, model: str, bps: str | None, amount: str | None, expected: str
) -> None:
    price = _apply(Decimal(reference), side, _spec(model, bps=bps, amount=amount))

    assert isinstance(price, Decimal)
    assert price == Decimal(expected)
    assert str(price) == expected, "a money value always renders on the cent tick"


# ── the full 3 models x 2 sides x 4 scenarios matrix (todo 15) ────────────

MATRIX_MODELS = (
    ("none", None, None),
    ("fixed_bps", "10", None),
    ("fixed_amount", None, "0.02"),
)
MATRIX_NORMAL = {
    "none": ("100.00", "100.00"),
    "fixed_bps": ("100.10", "99.90"),
    "fixed_amount": ("100.02", "99.98"),
}
MATRIX_TIE = {
    "none": ("10.01", "10.01"),
    "fixed_bps": ("10.02", "10.00"),
    "fixed_amount": ("10.03", "9.99"),
}


@pytest.mark.parametrize(("model", "bps", "amount"), MATRIX_MODELS)
@pytest.mark.parametrize(
    ("scenario", "reference", "expected"),
    [
        ("normal", Decimal("100.00"), MATRIX_NORMAL),
        ("tie-breaking", Decimal("10.005"), MATRIX_TIE),
    ],
)
def test_full_model_side_scenario_matrix(
    model: str,
    bps: str | None,
    amount: str | None,
    scenario: str,
    reference: Decimal,
    expected: dict[str, tuple[str, str]],
) -> None:
    spec = _spec(model, bps=bps, amount=amount)
    buy_expected, sell_expected = expected[model]

    buy = _apply(reference, "buy", spec)
    sell = _apply(reference, "sell", spec)

    assert buy == Decimal(buy_expected), f"{scenario} {model} buy"
    assert sell == Decimal(sell_expected), f"{scenario} {model} sell"


@pytest.mark.parametrize(("model", "bps", "amount"), MATRIX_MODELS)
@pytest.mark.parametrize(
    ("reference", "match"),
    [(Decimal("0"), "must be > 0"), (Decimal("0.001"), "below one cent")],
)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_zero_and_sub_cent_references_are_rejected_for_every_model(
    model: str,
    bps: str | None,
    amount: str | None,
    reference: Decimal,
    match: str,
    side: str,
) -> None:
    with pytest.raises(slippage_mod.SlippageError, match=match):
        _apply(reference, side, _spec(model, bps=bps, amount=amount))


# ── float boundary: exact Decimal, shortest float repr ────────────────────


@pytest.mark.parametrize("reference", [10.0, 3.33, 0.07, 250.5, 1421.005])
@pytest.mark.parametrize(
    ("model", "bps", "amount"),
    [("fixed_bps", "10", None), ("fixed_amount", None, "0.02"), ("none", None, None)],
)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_float_reference_never_drifts(
    reference: float, side: str, model: str, bps: str | None, amount: str | None
) -> None:
    price = _apply(reference, side, _spec(model, bps=bps, amount=amount))

    # Two decimal places, and a float round-trip that lands on the same Decimal:
    # repr(float(Decimal("10.01"))) is "10.01", never "10.010000000000002".
    assert price.as_tuple().exponent == -2
    assert Decimal(repr(float(price))) == price

    reference_tick = rules.quantize_money(rules.money(reference))
    if side == "buy":
        assert price >= reference_tick
    else:
        assert price <= reference_tick


def test_float_artifact_is_absent_from_the_repr() -> None:
    price = _apply(10.0, "buy", _spec("fixed_bps"))

    assert price == Decimal("10.01")
    assert repr(float(price)) == "10.01"
    assert "000000" not in repr(float(price))


# ── rounding: one tick, ROUND_HALF_UP ────────────────────────────────────


def test_rounding_uses_the_rules_money_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Decimal] = []
    real = slippage_mod.quantize_money

    def spy(value: Decimal) -> Decimal:
        seen.append(value)
        return real(value)

    monkeypatch.setattr(slippage_mod, "quantize_money", spy)
    price = _apply(Decimal("10.00"), "buy", _spec("fixed_bps"))

    assert price == Decimal("10.01")
    assert slippage_mod.quantize_money is not rules.quantize_money
    assert seen == [Decimal("10.00"), Decimal("10.01000")], (
        "the reference is canonicalized and the slipped value quantized, both via rules.quantize_money"
    )


def test_half_cent_tie_rounds_half_up_not_half_even() -> None:
    # 10.00 * 1.001 == 10.005 exactly; HALF_UP -> 10.01 (HALF_EVEN -> 10.00).
    tie_buy = _apply(Decimal("10.00"), "buy", _spec("fixed_amount", amount="0.005"))
    tie_reference = _apply(Decimal("10.005"), "buy", _spec("none"))

    assert tie_buy == Decimal("10.01")
    assert tie_reference == Decimal("10.01")


@pytest.mark.parametrize(("side", "expected"), [("buy", "10.00"), ("sell", "10.00")])
def test_a_sub_cent_slip_quantizes_away_without_crossing_the_reference(
    side: str, expected: str
) -> None:
    # 10.00 * 1.0001 = 10.001 -> 10.00; 10.00 * 0.9999 = 9.999 -> 10.00.
    price = _apply(Decimal("10.00"), side, _spec("fixed_bps", bps="1"))

    assert price == Decimal(expected)


def test_large_reference_rounding_carries_no_float_residue() -> None:
    # 1421.005 -> 1421.01 on the money tick; then *1.001 = 1422.43001 and *0.999 = 1419.58899.
    buy = _apply(Decimal("1421.005"), "buy", _spec("fixed_bps"))
    sell = _apply(Decimal("1421.005"), "sell", _spec("fixed_bps"))

    assert buy == Decimal("1422.43")
    assert sell == Decimal("1419.59")
    assert Decimal(repr(float(buy))) == buy
    assert Decimal(repr(float(sell))) == sell


# ── never in the trader's favor ───────────────────────────────────────────


def test_never_favorable_over_200_references() -> None:
    references = [Decimal(1000 + index * 7) / Decimal(100) for index in range(200)]
    models = (
        ("none", None, None),
        ("fixed_bps", "10", None),
        ("fixed_amount", None, "0.02"),
    )

    for reference in references:
        for model, bps, amount in models:
            spec = _spec(model, bps=bps, amount=amount)
            for side in ("buy", "sell"):
                price = _apply(reference, side, spec)

                assert price.as_tuple().exponent == -2
                assert price > 0
                if side == "buy":
                    assert price >= reference
                    if model != "none":
                        assert price > reference
                else:
                    assert price <= reference
                    if model != "none":
                        assert price < reference


def test_repeated_calls_are_identical_and_the_module_keeps_no_cache() -> None:
    spec = _spec("fixed_bps")
    first = _apply(Decimal("10.00"), "buy", spec)
    second = _apply(Decimal("10.00"), "buy", spec)

    assert first == second
    assert str(first) == str(second)
    mutable = {
        name: type(value).__name__
        for name, value in vars(slippage_mod).items()
        if not name.startswith("__") and isinstance(value, (dict, list, set))
    }
    assert mutable == {}, "slippage must stay stateless: no module-level mutable cache"


# ── rejected input ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reference",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-1.00"),
        Decimal("0"),
        -1.0,
        0.0,
        "not-a-number",
        True,
        None,
    ],
)
def test_invalid_reference_raises(reference: object) -> None:
    with pytest.raises(slippage_mod.SlippageError):
        _apply(reference, "buy", _spec("none"))


@pytest.mark.parametrize("side", ["BUY", "Buy", "", "long", "hold", None])
def test_unknown_side_raises(side: object) -> None:
    with pytest.raises(slippage_mod.SlippageError):
        _apply(Decimal("10.00"), side, _spec("none"))  # type: ignore[arg-type]


def test_unknown_model_raises() -> None:
    spec = SlippageSpec(model="percent", bps=Decimal("10"), amount=Decimal("0"))

    with pytest.raises(slippage_mod.SlippageError, match="unknown slippage model"):
        _apply(Decimal("10.00"), "buy", spec)


@pytest.mark.parametrize(
    "spec",
    [
        SlippageSpec(model="fixed_bps", bps=Decimal("-10")),
        SlippageSpec(model="fixed_bps", bps=Decimal("NaN")),
        SlippageSpec(model="fixed_bps", bps=Decimal("Infinity")),
        SlippageSpec(model="fixed_bps", bps=float("nan")),
        SlippageSpec(model="fixed_amount", amount=Decimal("-0.02")),
        SlippageSpec(model="fixed_amount", amount=Decimal("Infinity")),
        # the dataclass default: model "none" still carries bps=10
        SlippageSpec(model="none"),
        SlippageSpec(model="none", bps=Decimal("0"), amount=Decimal("0.01")),
        SlippageSpec(model="fixed_bps", bps=Decimal("10"), amount=Decimal("0.02")),
        SlippageSpec(model="fixed_amount", bps=Decimal("10"), amount=Decimal("0.02")),
    ],
)
def test_contradictory_or_invalid_spec_raises(spec: SlippageSpec) -> None:
    with pytest.raises(slippage_mod.SlippageError):
        _apply(Decimal("10.00"), "buy", spec)


@pytest.mark.parametrize("spec", [None, {"model": "none"}, "fixed_bps", 10])
def test_spec_must_be_a_slippage_spec(spec: object) -> None:
    with pytest.raises(slippage_mod.SlippageError, match="SlippageSpec"):
        _apply(Decimal("10.00"), "buy", spec)  # type: ignore[arg-type]


def test_non_positive_result_raises_instead_of_clamping() -> None:
    with pytest.raises(slippage_mod.SlippageError, match="not positive"):
        _apply(Decimal("0.01"), "sell", _spec("fixed_amount", amount="0.02"))


def test_reference_below_one_cent_raises() -> None:
    with pytest.raises(slippage_mod.SlippageError, match="one cent"):
        _apply(Decimal("0.001"), "buy", _spec("none"))


# ── convenience surface ──────────────────────────────────────────────────


@pytest.mark.parametrize(("side", "expected"), [("buy", "0.01"), ("sell", "-0.01")])
def test_slippage_amount_is_the_signed_delta(side: str, expected: str) -> None:
    delta = slippage_mod.slippage_amount(Decimal("10.00"), side, _spec("fixed_bps"))

    assert isinstance(delta, Decimal)
    assert delta == Decimal(expected)
    assert delta.as_tuple().exponent == -2


def test_slippage_amount_is_zero_for_none() -> None:
    assert slippage_mod.slippage_amount(Decimal("10.00"), "buy", _spec("none")) == Decimal("0.00")


@pytest.mark.parametrize(("side", "expected"), [("buy", "0.02"), ("sell", "-0.02")])
def test_slippage_amount_for_fixed_amount_is_signed(side: str, expected: str) -> None:
    delta = slippage_mod.slippage_amount(
        Decimal("10.00"), side, _spec("fixed_amount", amount="0.02")
    )

    assert delta == Decimal(expected)
    assert delta.as_tuple().exponent == -2


def test_slippage_amount_is_measured_from_the_quantized_reference() -> None:
    # 10.005 canonicalizes to 10.01 before the delta, so a null slip is 0.00
    # (not the 0.005 the raw reference would suggest).
    assert slippage_mod.slippage_amount(Decimal("10.005"), "buy", _spec("none")) == Decimal("0.00")


def test_describe_is_a_deterministic_one_line_echo() -> None:
    assert slippage_mod.describe(_spec("none")) == "none"
    assert slippage_mod.describe(_spec("fixed_bps")) == "fixed_bps bps=10"
    assert slippage_mod.describe(_spec("fixed_amount", amount="0.02")) == "fixed_amount amount=0.02"
    # canonical decimal text: "10.0" and "10" describe identically
    assert slippage_mod.describe(_spec("fixed_bps", bps="10.0")) == "fixed_bps bps=10"
    assert slippage_mod.describe(_spec("fixed_bps")) == slippage_mod.describe(_spec("fixed_bps"))
    with pytest.raises(slippage_mod.SlippageError):
        slippage_mod.describe(SlippageSpec(model="none"))


@pytest.mark.parametrize(
    "spec",
    [
        SlippageSpec(model="percent", bps=Decimal("10")),
        SlippageSpec(model="fixed_bps", bps=Decimal("10"), amount=Decimal("0.02")),
        SlippageSpec(model="fixed_amount", bps=Decimal("1")),
        SlippageSpec(model="fixed_amount", amount=Decimal("-0.02")),
        SlippageSpec(model="none", bps=Decimal("NaN")),
    ],
)
def test_describe_refuses_every_invalid_spec(spec: SlippageSpec) -> None:
    with pytest.raises(slippage_mod.SlippageError):
        slippage_mod.describe(spec)


# ── the stored account model resolves to one spec ────────────────────────


def test_spec_for_model_resolves_none_to_zero_and_fixed_bps_to_the_default() -> None:
    zero = slippage_mod.spec_for_model("none")
    assert (zero.model, zero.bps, zero.amount) == ("none", Decimal("0"), Decimal("0"))
    assert slippage_mod.describe(zero) == "none"
    assert slippage_mod.apply_slippage("1421.00", "buy", zero) == Decimal("1421.00")
    assert slippage_mod.apply_slippage("1421.00", "sell", zero) == Decimal("1421.00")

    default = slippage_mod.spec_for_model("fixed_bps")
    assert (default.model, default.bps, default.amount) == (
        "fixed_bps",
        Decimal("10"),
        Decimal("0"),
    )


def test_spec_for_model_refuses_an_unresolvable_or_unknown_model() -> None:
    # The account document stores no fixed amount, so the model alone cannot
    # resolve it; substituting another model would be a silent cost change.
    with pytest.raises(slippage_mod.SlippageError, match="fixed_amount"):
        slippage_mod.spec_for_model("fixed_amount")
    with pytest.raises(slippage_mod.SlippageError, match="unknown slippage model"):
        slippage_mod.spec_for_model("vwap")
