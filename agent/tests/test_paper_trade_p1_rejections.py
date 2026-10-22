"""Todo 17 gate — the AM-14 rejection ladder applied per open order/bar.

* every frozen slot has a table row AND a test that fires it;
* the table's first column equals ``rules.REJECTION_ORDER`` in order;
* first match wins: an earlier slot hides a later one on the same order;
* a ``day`` limit below the reference RESTS (no rejection) while the same
  ``ioc`` limit is ``limit_not_marketable``;
* ``suspended`` comes ONLY from the injected ``suspended_symbols`` truth: a
  no-trade bar (missing print or zero volume) leaves a resting order ``new``
  and reports the engine's ``data_missing`` warning;
* T+1 rejects a same-session sell across two bars and allows a prior holding;
* an unknown symbol reaches the hook as ``bar_row=None`` and is rejected;
* without a hook a missing symbol row stays fail-closed (``BarEngineError``);
* the hook is pure and never mutates its arguments.

Offline, deterministic: hand-built timelines, no clock, no network, no files.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from src.tools.paper_trade.bar_engine import BarEngineError, BarEngineState, apply_bar
from src.tools.paper_trade.order_ids import assign_order_id
from src.tools.paper_trade.orders import OrderBook
from src.tools.paper_trade.rejections import (
    REJECTION_TABLE,
    RejectionContext,
    RejectionError,
    make_reject_hook,
    rejection_for,
)
from src.tools.paper_trade.rules import REJECTION_ORDER, MarketRules
from src.tools.paper_trade.schema import (
    REASON_CODES,
    AccountState,
    BarStepRequest,
    OrderAction,
    OrderState,
    Position,
    parse_slippage,
)
from src.tools.paper_trade.timeline import BarRow, BarTimeline

_ACCOUNT = "pa_p1_rejections"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_ONE_BAR = "09:35"
_NEXT_BAR = "09:40"


# ── fixtures ─────────────────────────────────────────────────────────────


def _bar_time(label: str) -> str:
    return f"{_DAY}T{label}:00+08:00"


def _present_row(
    label: str,
    *,
    symbol: str = _SYM,
    close: float = 10.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> BarRow:
    opened = close if open_ is None else open_
    highest = close if high is None else high
    lowest = close if low is None else low
    prices = sorted([opened, highest, lowest, close])
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="continuous",
        status="present",
        open=opened,
        high=highest,
        low=lowest,
        close=close,
        volume=1_000.0,
        reference_median=(prices[1] + prices[2]) / 2,
        reference_close=close,
    )


def _missing_row(label: str, *, symbol: str = _SYM) -> BarRow:
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="continuous",
        status="missing",
    )


def _ctx(
    *,
    trading_day: bool = True,
    suspended: frozenset[str] = frozenset(),
    corporate: frozenset[str] = frozenset(),
    bands: dict[str, tuple[float, float]] | None = None,
    market: str = "cn_a",
    path_model: str = "bar_median",
) -> RejectionContext:
    return RejectionContext(
        trading_day=trading_day,
        suspended_symbols=suspended,
        corporate_action_unresolved=corporate,
        bands={} if bands is None else bands,
        rules=MarketRules(market),
        path_model=path_model,
    )


def _order(
    *,
    symbol: str = _SYM,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
    status: str = "new",
    seq: int = 0,
) -> OrderState:
    filled = qty if status == "filled" else 0.0
    return OrderState(
        order_id=assign_order_id(_ACCOUNT, _DAY, _ONE_BAR, seq),
        account_id=_ACCOUNT,
        trade_date=_DAY,
        symbol=symbol,
        side=side,
        qty=qty,
        remaining_qty=qty - filled,
        limit=limit,
        tif=tif,
        status=status,
        bar_label=_ONE_BAR,
        filled_qty=filled,
        avg_fill_price=0.0,
        seq=seq,
    )


def _state(
    *,
    cash: float = 100_000.0,
    positions: tuple[Position, ...] = (),
    rows: tuple[OrderState, ...] = (),
    order_seq: int = 0,
    bought_today: dict[str, Decimal] | None = None,
) -> BarEngineState:
    return BarEngineState(
        account_id=_ACCOUNT,
        market="cn_a",
        trade_date=_DAY,
        cash=Decimal(str(cash)),
        positions=positions,
        book=OrderBook(_ACCOUNT, _DAY, rows=rows),
        order_seq=order_seq,
        bought_today=bought_today or {},
        watermark_bar="",
    )


def _place(
    *,
    symbol: str = _SYM,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
) -> OrderAction:
    return OrderAction(action="place", symbol=symbol, side=side, qty=qty, limit=limit, tif=tif)


def _request(
    *,
    actions: tuple[OrderAction, ...] = (),
    positions: tuple[Position, ...] = (),
    bar_time: str = _bar_time(_ONE_BAR),
) -> BarStepRequest:
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=100_000.0, positions=positions),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=bar_time,
        order_actions=actions,
        slippage=parse_slippage(None),
    )


def _timeline(
    rows_by_bar: dict[str, tuple[BarRow, ...]],
    *,
    symbols: tuple[str, ...] = (_SYM,),
) -> BarTimeline:
    return BarTimeline(
        trade_date=_DAY,
        bar_labels=tuple(rows_by_bar),
        rows_by_bar=MappingProxyType(dict(rows_by_bar)),
        symbols=symbols,
    )


def _one_bar(*, close: float = 10.0) -> BarTimeline:
    return _timeline({_ONE_BAR: (_present_row(_ONE_BAR, close=close),)})


def _two_bars(first: float = 10.0, second: float = 10.0) -> BarTimeline:
    return _timeline(
        {
            _ONE_BAR: (_present_row(_ONE_BAR, close=first),),
            _NEXT_BAR: (_present_row(_NEXT_BAR, close=second),),
        }
    )


# ── 1. one test per frozen reason code ───────────────────────────────────


def test_unknown_symbol_when_the_symbol_has_no_bar_row() -> None:
    assert rejection_for(_order(symbol=_OTHER), None, _state(), _ctx()) == "unknown_symbol"
    # A row that names another symbol is not this symbol's row.
    assert (
        rejection_for(_order(symbol=_OTHER), _present_row(_ONE_BAR), _state(), _ctx())
        == "unknown_symbol"
    )


def test_non_trading_day_when_the_session_calendar_is_closed() -> None:
    assert (
        rejection_for(_order(), _present_row(_ONE_BAR), _state(), _ctx(trading_day=False))
        == "non_trading_day"
    )


def test_suspended_only_when_the_symbol_is_flagged() -> None:
    # A no-print bar is a NO-TRADE, not a suspension: the order keeps resting.
    assert rejection_for(_order(), _missing_row(_ONE_BAR), _state(), _ctx()) is None
    flagged = _ctx(suspended=frozenset({_SYM}))
    assert rejection_for(_order(), _present_row(_ONE_BAR), _state(), flagged) == "suspended"
    assert rejection_for(_order(), _missing_row(_ONE_BAR), _state(), flagged) == "suspended"


def test_corporate_action_unresolved_when_the_symbol_is_flagged() -> None:
    ctx = _ctx(corporate=frozenset({_SYM}))
    assert (
        rejection_for(_order(), _present_row(_ONE_BAR), _state(), ctx)
        == "corporate_action_unresolved"
    )


def test_lot_size_when_the_quantity_breaks_the_board_lot() -> None:
    assert (
        rejection_for(_order(qty=150.0), _present_row(_ONE_BAR), _state(), _ctx()) == "lot_size"
    )


def test_limit_up_when_a_buy_reference_sits_at_the_upper_band() -> None:
    ctx = _ctx(bands={_SYM: (9.0, 10.0)})
    assert rejection_for(_order(), _present_row(_ONE_BAR), _state(), ctx) == "limit_up"


def test_limit_down_when_a_sell_reference_sits_at_the_lower_band() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    ctx = _ctx(bands={_SYM: (10.0, 11.0)})
    order = _order(side="sell")
    assert rejection_for(order, _present_row(_ONE_BAR), _state(positions=holding), ctx) == "limit_down"


def test_limit_not_marketable_only_for_ioc_and_fok() -> None:
    ctx = _ctx()
    row = _present_row(_ONE_BAR)
    assert (
        rejection_for(_order(limit=9.0, tif="ioc"), row, _state(), ctx)
        == "limit_not_marketable"
    )
    assert rejection_for(_order(limit=9.0, tif="fok"), row, _state(), ctx) == "limit_not_marketable"
    assert rejection_for(_order(limit=9.0, tif="day"), row, _state(), ctx) is None


def test_t_plus_one_blocks_a_same_session_sell() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    state = _state(positions=holding, bought_today={_SYM: Decimal("100.0")})
    assert (
        rejection_for(_order(side="sell"), _present_row(_ONE_BAR), state, _ctx()) == "t_plus_1"
    )


def test_insufficient_position_when_the_sell_exceeds_the_holding() -> None:
    assert (
        rejection_for(_order(side="sell"), _present_row(_ONE_BAR), _state(), _ctx())
        == "insufficient_position"
    )


def test_insufficient_cash_when_the_buy_cost_exceeds_cash() -> None:
    # 100 × 10.00 + 5.01 fee = 1005.01 > 1000.00
    assert (
        rejection_for(_order(), _present_row(_ONE_BAR), _state(cash=1_000.0), _ctx())
        == "insufficient_cash"
    )


# ── 2. the table covers the frozen ladder exactly ────────────────────────


def test_the_table_covers_the_frozen_ladder_in_order() -> None:
    assert tuple(code for code, _, _ in REJECTION_TABLE) == REJECTION_ORDER
    assert set(REJECTION_ORDER) == set(REASON_CODES)
    assert len(REJECTION_TABLE) == 11


def test_every_table_test_id_names_a_test_in_this_module() -> None:
    seen: set[str] = set()
    for code, trigger, test_id in REJECTION_TABLE:
        assert trigger, f"{code} has no documented trigger"
        assert test_id.startswith("test_") and test_id not in seen
        seen.add(test_id)
        test = globals().get(test_id)
        assert callable(test), f"{code}: {test_id} is not defined in this module"
        assert test.__name__ == test_id


# ── 3. first match wins ──────────────────────────────────────────────────


def test_a_suspended_symbol_that_is_also_over_cash_reports_suspended() -> None:
    order = _order()
    ctx = _ctx(suspended=frozenset({_SYM}))
    assert (
        rejection_for(order, _missing_row(_ONE_BAR), _state(cash=0.0), ctx) == "suspended"
    )


def test_a_corporate_action_that_is_also_a_bad_lot_reports_the_corporate_action() -> None:
    order = _order(qty=150.0)
    ctx = _ctx(corporate=frozenset({_SYM}))
    assert (
        rejection_for(order, _present_row(_ONE_BAR), _state(), ctx)
        == "corporate_action_unresolved"
    )


def test_a_bad_lot_at_the_upper_band_reports_lot_size() -> None:
    order = _order(qty=150.0)
    ctx = _ctx(bands={_SYM: (9.0, 10.0)})
    assert rejection_for(order, _present_row(_ONE_BAR), _state(), ctx) == "lot_size"


def test_a_buy_at_the_upper_band_with_an_unmarketable_limit_reports_limit_up() -> None:
    order = _order(limit=9.0, tif="ioc")
    ctx = _ctx(bands={_SYM: (9.0, 10.0)})
    assert rejection_for(order, _present_row(_ONE_BAR), _state(), ctx) == "limit_up"


def test_a_sell_at_the_lower_band_with_an_unmarketable_limit_reports_limit_down() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    order = _order(side="sell", limit=10.5, tif="ioc")  # limit > ref: not marketable
    ctx = _ctx(bands={_SYM: (10.0, 11.0)})
    assert rejection_for(order, _present_row(_ONE_BAR), _state(positions=holding), ctx) == "limit_down"


def test_an_unmarketable_ioc_sell_reports_before_the_position_gate() -> None:
    order = _order(side="sell", limit=10.5, tif="ioc")
    assert (
        rejection_for(order, _present_row(_ONE_BAR), _state(), _ctx()) == "limit_not_marketable"
    )


def test_an_oversell_with_today_buys_is_insufficient_position_not_t_plus_one() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    state = _state(positions=holding, bought_today={_SYM: Decimal("100.0")})
    assert (
        rejection_for(_order(side="sell", qty=200.0), _present_row(_ONE_BAR), state, _ctx())
        == "insufficient_position"
    )


# ── 4. band slots follow the pricing path model; unknown bands skip ──────


def test_the_band_slot_reads_the_context_path_model() -> None:
    # median 10.00, close 9.00: only the bar_median model sits at the upper band.
    row = _present_row(_ONE_BAR, close=9.0, open_=10.0, high=10.0, low=10.0)
    bands = {_SYM: (8.0, 10.0)}
    assert rejection_for(_order(), row, _state(), _ctx(bands=bands)) == "limit_up"
    assert (
        rejection_for(_order(), row, _state(), _ctx(bands=bands, path_model="bar_close")) is None
    )


def test_an_unknown_band_skips_the_band_slots() -> None:
    assert rejection_for(_order(), _present_row(_ONE_BAR), _state(), _ctx()) is None


def test_a_reversed_band_is_refused_when_the_context_is_built() -> None:
    with pytest.raises(RejectionError, match="lower 11.0 exceeds upper 10.0"):
        _ctx(bands={_SYM: (11.0, 10.0)})


def test_the_band_table_is_frozen_against_later_mutation() -> None:
    bands = {_SYM: (9.0, 10.0)}
    ctx = _ctx(bands=bands)
    bands[_SYM] = (1.0, 2.0)
    assert ctx.bands[_SYM] == (9.0, 10.0)


# ── 5. T+1 is cn_a only; sellable matches the gate ───────────────────────


def test_t_plus_one_does_not_apply_to_hong_kong() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    state = _state(positions=holding, bought_today={_SYM: Decimal("100.0")})
    assert (
        rejection_for(_order(side="sell"), _present_row(_ONE_BAR), state, _ctx(market="hk"))
        is None
    )


def test_sellable_subtracts_today_s_buys_on_cn_a() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    state = _state(positions=holding, bought_today={_SYM: Decimal("10.0")})
    ctx = _ctx()
    assert ctx.held(state, _SYM) == Decimal("100.0")
    assert ctx.sellable(state, _SYM) == Decimal("90.0")
    order = _order(side="sell", qty=100.0)  # 90 < 100 <= 100, and a valid 100-share lot
    assert rejection_for(order, _present_row(_ONE_BAR), state, ctx) == "t_plus_1"


# ── 6. contract edge cases ───────────────────────────────────────────────


def test_a_terminal_order_cannot_be_re_evaluated() -> None:
    with pytest.raises(RejectionError, match="resting"):
        rejection_for(_order(status="filled"), _present_row(_ONE_BAR), _state(), _ctx())


def test_a_bar_row_from_another_day_is_refused() -> None:
    row = BarRow(
        trade_date="2026-10-19",
        bar_label=_ONE_BAR,
        bar_time="2026-10-19T09:35:00+08:00",
        symbol=_SYM,
        phase="continuous",
        status="present",
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        volume=1.0,
        reference_median=10.0,
        reference_close=10.0,
    )
    with pytest.raises(RejectionError, match="trade_date"):
        rejection_for(_order(), row, _state(), _ctx())


def test_the_hook_is_pure_and_does_not_mutate_its_arguments() -> None:
    hook = make_reject_hook(_ctx(bands={_SYM: (9.0, 10.0)}))
    order = _order()
    row = _present_row(_ONE_BAR)
    state = _state()

    first = hook(order, row, state)
    second = hook(order, row, state)

    assert first == second == "limit_up"
    assert state.cash == Decimal("100000.00")
    assert state.positions == ()
    assert state.book.rows() == ()
    assert order.status == "new" and order.remaining_qty == 100.0


# ── 7. engine integration (bar_engine.apply_bar) ─────────────────────────


def test_an_unknown_symbol_reaches_the_hook_as_none() -> None:
    inner = make_reject_hook(_ctx())
    seen: list[Any] = []

    def spy(order: OrderState, bar_row: BarRow | None, state: BarEngineState) -> str | None:
        seen.append((order.symbol, bar_row))
        return inner(order, bar_row, state)

    outcome = apply_bar(
        _state(),
        _request(actions=(_place(symbol=_OTHER),)),
        _one_bar(),
        reject_hook=spy,
    )

    assert seen == [(_OTHER, None)]
    assert [(row.symbol, row.reason_code) for row in outcome.rejected] == [(_OTHER, "unknown_symbol")]
    assert [(row.order_id, row.status) for row in outcome.state.book.rows()] == [
        (assign_order_id(_ACCOUNT, _DAY, _ONE_BAR, 0), "rejected")
    ]
    assert outcome.fills == ()
    assert outcome.bar_result.open_orders == ()


def test_without_a_hook_a_missing_symbol_row_stays_fail_closed() -> None:
    with pytest.raises(BarEngineError, match="no row for symbol"):
        apply_bar(_state(), _request(actions=(_place(symbol=_OTHER),)), _one_bar())


def test_a_day_limit_below_the_reference_rests_but_the_same_ioc_limit_is_rejected() -> None:
    hook = make_reject_hook(_ctx())
    resting = apply_bar(
        _state(), _request(actions=(_place(limit=9.0),)), _one_bar(), reject_hook=hook
    )
    assert resting.rejected == ()
    assert resting.fills == ()
    assert [(row.status, row.limit) for row in resting.bar_result.open_orders] == [("new", 9.0)]

    terminal = apply_bar(
        _state(), _request(actions=(_place(limit=9.0, tif="ioc"),)), _one_bar(), reject_hook=hook
    )
    assert [(row.reason_code) for row in terminal.rejected] == ["limit_not_marketable"]
    assert [row.status for row in terminal.state.book.rows()] == ["rejected"]
    assert terminal.bar_result.open_orders == ()


def test_t_plus_one_across_two_bars_rejects_the_same_session_sell() -> None:
    hook = make_reject_hook(_ctx())
    timeline = _two_bars()

    bought = apply_bar(_state(), _request(actions=(_place(),)), timeline, reject_hook=hook)
    assert len(bought.fills) == 1
    assert bought.state.bought_today == {_SYM: Decimal("100.0")}

    sold = apply_bar(
        bought.state,
        _request(bar_time=_bar_time(_NEXT_BAR), actions=(_place(side="sell"),)),
        timeline,
        reject_hook=hook,
    )

    assert sold.fills == ()
    assert [row.reason_code for row in sold.rejected] == ["t_plus_1"]
    assert [row.status for row in sold.state.book.rows()] == ["filled", "rejected"]
    assert sold.state.book.open_orders() == ()


def test_a_prior_session_holding_fills_instead_of_being_rejected() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    outcome = apply_bar(
        _state(positions=holding),
        _request(positions=holding, actions=(_place(side="sell"),)),
        _one_bar(),
        reject_hook=make_reject_hook(_ctx()),
    )

    assert outcome.rejected == ()
    assert len(outcome.fills) == 1
    assert outcome.fills[0].side == "sell"
    assert outcome.state.positions == ()


def test_a_no_trade_bar_leaves_an_open_order_new_end_to_end() -> None:
    timeline = _timeline({_ONE_BAR: (_missing_row(_ONE_BAR),)})
    outcome = apply_bar(
        _state(),
        _request(actions=(_place(),)),
        timeline,
        reject_hook=make_reject_hook(_ctx()),
    )

    assert outcome.rejected == ()
    assert [row.status for row in outcome.state.book.rows()] == ["new"]
    assert [row.status for row in outcome.bar_result.open_orders] == ["new"]
    assert outcome.fills == ()
    assert [event["event_type"] for event in outcome.order_events] == ["placed"]
    assert any("data_missing" in warning for warning in outcome.bar_result.result.warnings)


def test_a_zero_volume_bar_is_a_no_trade_not_a_suspension() -> None:
    no_trade = replace(_present_row(_ONE_BAR), volume=0.0)
    timeline = _timeline({_ONE_BAR: (no_trade,)})
    outcome = apply_bar(
        _state(),
        _request(actions=(_place(),)),
        timeline,
        reject_hook=make_reject_hook(_ctx()),
    )

    assert outcome.rejected == ()
    assert outcome.fills == ()
    assert [row.status for row in outcome.state.book.rows()] == ["new"]
    assert any("data_missing" in warning for warning in outcome.bar_result.result.warnings)


def test_an_explicitly_suspended_symbol_rejects_an_open_order_end_to_end() -> None:
    timeline = _timeline({_ONE_BAR: (_present_row(_ONE_BAR),)})
    outcome = apply_bar(
        _state(),
        _request(actions=(_place(tif="ioc"),)),
        timeline,
        reject_hook=make_reject_hook(_ctx(suspended=frozenset({_SYM}))),
    )

    assert [row.reason_code for row in outcome.rejected] == ["suspended"]
    assert [row.status for row in outcome.state.book.rows()] == ["rejected"]
    assert outcome.fills == ()
    assert [event["event_type"] for event in outcome.order_events] == ["placed", "rejected"]
