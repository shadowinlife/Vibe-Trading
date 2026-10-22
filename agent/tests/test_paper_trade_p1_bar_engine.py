"""Todo 16 gate — the pure per-bar engine loop (pt-v1.3 pt-v1.3).

* a marketable order fills at the adverse slipped bar reference and settles
  cash / position / fee on the ledger conventions the v1.2 engine froze;
* a resting limit does not fill or reject, and fills on the later bar that
  makes it marketable;
* cancel / replace actions apply on the bar that carries them;
* an unfilled IOC/FOK terminates at bar end as a ``CanceledOrder``;
* a non-continuous bar refuses actions but still reports its phase event;
* the engine never reads a label after the applied one (spy timeline);
* equal inputs never mutate the state and return equal JCS bytes;
* an invalid order action raises ``BarEngineError`` (``INVALID_REQUEST``);
* cash / position gates keep the ledger non-negative instead of rejecting;
* the two documented seams: the pre-pricing reject hook and the 15:00 end hook;
* the composed v1.3 document passes the real contract validator.

Offline, deterministic: hand-built timelines, no clock, no network, no files.
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from src.tools.paper_trade import state_hash, wire
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.bar_engine import (
    BarEngineError,
    BarEngineState,
    apply_bar,
)
from src.tools.paper_trade.order_ids import assign_order_id
from src.tools.paper_trade.orders import OrderBook
from src.tools.paper_trade.schema import (
    CANCELED_REASON_CODE,
    SCHEMA_VERSION_V1_3,
    AccountState,
    BarStepRequest,
    OrderAction,
    NavPoint,
    OrderState,
    Position,
    SlippageSpec,
    load_contract_schema,
    parse_slippage,
    validate_payload,
)
from src.tools.paper_trade.timeline import BarRow, BarTimeline

_ACCOUNT = "pa_p1_bar"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_ONE_BAR = "09:35"
_NEXT_BAR = "09:40"
_FIRST_ID = assign_order_id(_ACCOUNT, _DAY, _ONE_BAR, 0)
_SECOND_ID = assign_order_id(_ACCOUNT, _DAY, _ONE_BAR, 1)


def _bar_time(label: str, day: str = _DAY) -> str:
    return f"{day}T{label}:00+08:00"


def _present_row(
    label: str,
    *,
    symbol: str = _SYM,
    close: float = 10.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000.0,
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
        volume=volume,
        reference_median=(prices[1] + prices[2]) / 2,
        reference_close=close,
    )


def _auction_row(label: str, *, symbol: str = _SYM) -> BarRow:
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="auction_window",
        status="auction",
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


def _timeline(
    rows_by_bar: dict[str, tuple[BarRow, ...]],
    *,
    symbols: tuple[str, ...] = (_SYM,),
    auction: tuple[BarRow, ...] = (),
    outside: tuple[BarRow, ...] = (),
) -> BarTimeline:
    return BarTimeline(
        trade_date=_DAY,
        bar_labels=tuple(rows_by_bar),
        rows_by_bar=MappingProxyType(dict(rows_by_bar)),
        symbols=symbols,
        auction=auction,
        outside=outside,
    )


def _one_bar(close: float = 10.0, *, symbol: str = _SYM) -> BarTimeline:
    return _timeline({_ONE_BAR: (_present_row(_ONE_BAR, symbol=symbol, close=close),)}, symbols=(symbol,))


def _two_bars(first: float, second: float) -> BarTimeline:
    return _timeline(
        {
            _ONE_BAR: (_present_row(_ONE_BAR, close=first),),
            _NEXT_BAR: (_present_row(_NEXT_BAR, close=second),),
        }
    )


def _place(
    *,
    symbol: str = _SYM,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
    client_order_id: str = "",
) -> OrderAction:
    return OrderAction(
        action="place",
        symbol=symbol,
        side=side,
        qty=qty,
        limit=limit,
        tif=tif,
        client_order_id=client_order_id,
    )


def _cancel(order_id: str = "", client_order_id: str = "") -> OrderAction:
    return OrderAction(action="cancel", symbol="", order_id=order_id, client_order_id=client_order_id)


def _replace(
    order_id: str = "",
    *,
    replace_qty: float | None = None,
    replace_limit: float | None = None,
) -> OrderAction:
    return OrderAction(
        action="replace",
        symbol="",
        order_id=order_id,
        replace_qty=replace_qty,
        replace_limit=replace_limit,
    )


def _order_row(
    order_id: str = _FIRST_ID,
    *,
    seq: int = 0,
    symbol: str = _SYM,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
    status: str = "new",
    client_order_id: str = "",
) -> OrderState:
    filled = qty if status == "filled" else 0.0
    return OrderState(
        order_id=order_id,
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
        client_order_id=client_order_id,
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
    watermark: str = "",
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
        watermark_bar=watermark,
    )


def _request(
    *,
    actions: tuple[OrderAction, ...] = (),
    cancels: tuple[str, ...] = (),
    replaces: tuple[OrderAction, ...] = (),
    bar_time: str = _bar_time(_ONE_BAR),
    cash: float = 100_000.0,
    positions: tuple[Position, ...] = (),
    slippage: SlippageSpec | None = None,
    open_orders: tuple[OrderState, ...] = (),
    order_seq: int = 0,
) -> BarStepRequest:
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=cash, positions=positions),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=bar_time,
        order_actions=actions,
        cancel_order_ids=cancels,
        replace_orders=replaces,
        slippage=parse_slippage(None) if slippage is None else slippage,
        open_orders=open_orders,
        order_seq=order_seq,
    )


def _event_types(outcome: Any) -> list[str]:
    return [event["event_type"] for event in outcome.order_events]


# ── 1. fill, settlement, nav ───────────────────────────────────────────────


def test_a_market_buy_fills_at_the_slipped_reference_and_settles_the_ledger() -> None:
    outcome = apply_bar(_state(), _request(actions=(_place(),)), _one_bar())

    assert len(outcome.fills) == 1
    fill = outcome.fills[0]
    assert (fill.trade_date, fill.symbol, fill.side, fill.qty) == (_DAY, _SYM, "buy", 100.0)
    assert fill.price == 10.01  # 10.00 + 10 bps, cent tick
    assert fill.fee == 5.01  # min commission 5 + transfer fee
    assert outcome.state.cash == Decimal("98993.99")
    assert [(p.symbol, p.qty, p.avg_cost) for p in outcome.state.positions] == [(_SYM, 100.0, 10.01)]
    assert outcome.state.bought_today == {_SYM: Decimal("100.0")}
    assert outcome.state.order_seq == 1
    assert outcome.state.watermark_bar == _ONE_BAR
    assert _event_types(outcome) == ["placed", "filled"]
    assert outcome.order_events[1]["price"] == 10.01
    assert [event["order_id"] for event in outcome.order_events] == [_FIRST_ID, _FIRST_ID]
    assert outcome.bar_result.open_orders == ()
    assert outcome.bar_result.order_seq == 1


def test_the_bar_emits_exactly_one_nav_point_at_the_bar_reference_close() -> None:
    outcome = apply_bar(_state(), _request(actions=(_place(),)), _one_bar())

    assert outcome.bar_result.result.nav_series == (
        NavPoint(date=_DAY, nav=99_993.99, cash=98_993.99, position_value=1_000.0),
    )


# ── 2. resting limits ──────────────────────────────────────────────────────


def test_a_limit_that_is_not_marketable_rests_without_a_fill_or_rejection() -> None:
    outcome = apply_bar(_state(), _request(actions=(_place(limit=9.50),)), _one_bar())

    assert outcome.fills == ()
    assert outcome.rejected == ()
    assert [row.status for row in outcome.bar_result.open_orders] == ["new"]
    assert outcome.bar_result.open_orders[0].limit == 9.50
    assert _event_types(outcome) == ["placed"]
    assert outcome.state.cash == Decimal("100000.00")


def test_a_resting_limit_fills_on_the_later_bar_that_makes_it_marketable() -> None:
    first = apply_bar(_state(), _request(actions=(_place(limit=10.00),)), _two_bars(10.10, 9.90))
    assert first.fills == ()

    second = apply_bar(
        first.state,
        _request(bar_time=_bar_time(_NEXT_BAR)),
        _two_bars(10.10, 9.90),
    )

    assert len(second.fills) == 1
    assert second.fills[0].price == 9.91  # 9.90 + 10 bps, limit 10.00 not binding
    assert second.state.watermark_bar == _NEXT_BAR
    assert second.bar_result.open_orders == ()


def test_a_no_trade_bar_rests_a_day_order_and_the_next_traded_bar_fills_it() -> None:
    timeline = _timeline(
        {
            _ONE_BAR: (_missing_row(_ONE_BAR),),
            _NEXT_BAR: (_present_row(_NEXT_BAR, close=10.0),),
        }
    )
    first = apply_bar(_state(), _request(actions=(_place(),)), timeline)

    assert first.fills == ()
    assert first.rejected == ()
    assert [row.status for row in first.bar_result.open_orders] == ["new"]
    assert any("data_missing" in warning for warning in first.bar_result.result.warnings)

    second = apply_bar(first.state, _request(bar_time=_bar_time(_NEXT_BAR)), timeline)

    assert len(second.fills) == 1
    assert second.fills[0].price == 10.01
    assert second.bar_result.open_orders == ()


# ── 3. actions on the bar that carries them ────────────────────────────────


def test_cancel_and_replace_apply_on_the_bar_they_are_submitted() -> None:
    request = _request(
        actions=(
            _place(limit=9.00),  # id 0: canceled below
            _place(limit=9.00),  # id 1: replaced below
        ),
        cancels=(_FIRST_ID,),
        replaces=(_replace(_SECOND_ID, replace_limit=9.50),),
    )

    outcome = apply_bar(_state(), request, _one_bar())

    assert outcome.fills == ()
    assert _event_types(outcome) == ["placed", "placed", "canceled", "replaced"]
    assert [(row.order_id, row.status, row.limit) for row in outcome.state.book.rows()] == [
        (_FIRST_ID, "canceled", 9.00),
        (_SECOND_ID, "new", 9.50),
    ]
    assert [row.order_id for row in outcome.bar_result.open_orders] == [_SECOND_ID]
    assert outcome.state.order_seq == 2


def test_a_place_that_carries_a_client_id_dedupes_without_a_second_order() -> None:
    first = apply_bar(
        _state(),
        _request(actions=(_place(limit=9.00, client_order_id="c-1"),)),
        _one_bar(),
    )
    second = apply_bar(
        first.state,
        _request(
            actions=(_place(limit=9.00, client_order_id="c-1"),),
            bar_time=_bar_time(_NEXT_BAR),
        ),
        _two_bars(10.0, 10.0),
    )

    assert second.state.order_seq == 1
    assert len(second.state.book.rows()) == 1
    assert second.order_events == ()
    assert [row.order_id for row in second.bar_result.open_orders] == [_FIRST_ID]


# ── 4. TIF termination ─────────────────────────────────────────────────────


def test_unfilled_ioc_and_fok_are_canceled_at_bar_end() -> None:
    request = _request(
        actions=(
            _place(limit=9.00, tif="ioc"),
            _place(limit=9.00, tif="fok"),
        )
    )

    outcome = apply_bar(_state(), request, _one_bar())

    assert outcome.fills == ()
    assert [row.status for row in outcome.state.book.rows()] == ["canceled", "canceled"]
    assert [row.reason_code for row in outcome.bar_result.result.canceled_orders] == [
        CANCELED_REASON_CODE,
        CANCELED_REASON_CODE,
    ]
    assert [row.detail for row in outcome.bar_result.result.canceled_orders] == [
        "ioc_unfilled at 09:35",
        "fok_unfilled at 09:35",
    ]
    canceled = [event for event in outcome.order_events if event["event_type"] == "canceled"]
    assert [event["reason_code"] for event in canceled] == ["ioc_unfilled", "fok_unfilled"]
    assert outcome.bar_result.open_orders == ()


# ── 5. phase gate ──────────────────────────────────────────────────────────


def test_a_non_continuous_bar_refuses_every_order_action() -> None:
    auction = _auction_row(_ONE_BAR)
    timeline = _timeline({_ONE_BAR: (auction,)}, auction=(auction,))

    for request in (
        _request(actions=(_place(),)),
        _request(cancels=(_FIRST_ID,)),
        _request(replaces=(_replace(_FIRST_ID, replace_limit=9.5),)),
    ):
        with pytest.raises(BarEngineError, match="not matchable"):
            apply_bar(_state(), request, timeline)


def test_a_non_continuous_bar_without_actions_returns_phase_events_only() -> None:
    auction = _auction_row(_ONE_BAR)
    timeline = _timeline({_ONE_BAR: (auction,)}, auction=(auction,))

    outcome = apply_bar(_state(cash=500.0), _request(), timeline)

    assert outcome.fills == ()
    assert outcome.rejected == ()
    assert outcome.order_events == ()
    assert len(outcome.session_events) == 1
    assert (outcome.session_events[0].event_type, outcome.session_events[0].detail) == (
        "auction_window",
        "auction window at 09:35",
    )
    assert outcome.state.cash == Decimal("500.00")
    assert outcome.session_complete is False


# ── 6. lookahead / purity ──────────────────────────────────────────────────


class _SpyTimeline:
    """Delegates to a timeline but fails on any label AFTER the applied one."""

    def __init__(self, timeline: BarTimeline, applied: str) -> None:
        self._timeline = timeline
        self._applied = applied
        self.reads: list[str] = []
        self._order = {label: index for index, label in enumerate(timeline.bar_labels)}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._timeline, name)

    def _guard(self, label: str) -> None:
        if self._order[label] > self._order[self._applied]:
            raise AssertionError(f"future bar {label} was read while applying {self._applied}")

    def rows_for(self, label: str) -> tuple[BarRow, ...]:
        self._guard(label)
        self.reads.append(label)
        return self._timeline.rows_for(label)

    def row(self, label: str, symbol: str) -> BarRow:
        self._guard(label)
        self.reads.append(label)
        return self._timeline.row(label, symbol)


def test_the_engine_never_reads_a_bar_after_the_applied_label() -> None:
    spy = _SpyTimeline(_two_bars(10.0, 10.0), _ONE_BAR)

    outcome = apply_bar(_state(), _request(actions=(_place(),)), spy)  # type: ignore[arg-type]

    assert outcome.fills
    assert set(spy.reads) == {_ONE_BAR}


def test_the_input_state_is_never_mutated() -> None:
    state = _state(cash=1_000.0)
    before = (state.book.rows(), state.cash, state.positions, state.order_seq, dict(state.bought_today))

    outcome = apply_bar(state, _request(cash=1_000.0, actions=(_place(),)), _one_bar())

    assert (state.book.rows(), state.cash, state.positions, state.order_seq, dict(state.bought_today)) == before
    assert state.watermark_bar == ""
    assert outcome.state.book is not state.book
    assert outcome.state.book.rows() != state.book.rows()


def test_two_calls_with_the_same_inputs_are_equal_and_byte_identical() -> None:
    state = _state()
    request = _request(actions=(_place(), _place(limit=9.0)))
    timeline = _one_bar()

    first = apply_bar(state, request, timeline)
    second = apply_bar(state, request, timeline)

    assert first == second
    first_payload = wire.build_success_payload(first.bar_result.result, first.bar_result)
    second_payload = wire.build_success_payload(second.bar_result.result, second.bar_result)
    assert state_hash.canonicalize(first_payload) == state_hash.canonicalize(second_payload)
    assert first.bar_result.schema_version == SCHEMA_VERSION_V1_3


# ── 7. cash / position gates keep the ledger non-negative ──────────────────


def test_a_buy_over_cash_rests_instead_of_going_negative() -> None:
    outcome = apply_bar(
        _state(cash=500.0),
        _request(cash=500.0, actions=(_place(),)),
        _one_bar(),
    )

    assert outcome.fills == ()
    assert outcome.rejected == ()
    assert outcome.state.cash == Decimal("500.00")
    assert [row.status for row in outcome.state.book.rows()] == ["new"]


def test_the_cash_gate_accounts_for_earlier_fills_on_the_same_bar() -> None:
    # Each 100-share buy fills at its 9.00 limit (the cap binds the slip) and
    # costs 905.01 (900.00 + 5.01); cash 1_000 fits exactly one.
    outcome = apply_bar(
        _state(cash=1_000.0),
        _request(cash=1_000.0, actions=(_place(limit=9.0), _place(limit=9.0))),
        _one_bar(close=9.0),
    )

    assert len(outcome.fills) == 1
    assert outcome.state.cash == Decimal("94.99")
    assert [row.status for row in outcome.state.book.rows()] == ["filled", "new"]


def test_a_sell_beyond_the_sellable_quantity_rests() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    outcome = apply_bar(
        _state(positions=holding),
        _request(positions=holding, actions=(_place(side="sell", qty=200.0),)),
        _one_bar(close=10.0),
    )

    assert outcome.fills == ()
    assert [(p.symbol, p.qty) for p in outcome.state.positions] == [(_SYM, 100.0)]


def test_t_plus_one_keeps_today_s_buys_unsellable() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    outcome = apply_bar(
        _state(positions=holding, bought_today={_SYM: Decimal("100.0")}),
        _request(positions=holding, actions=(_place(side="sell", qty=100.0),)),
        _one_bar(close=10.0),
    )

    assert outcome.fills == ()
    assert [p.qty for p in outcome.state.positions] == [100.0]


def test_a_sell_whose_fee_exceeds_its_notional_cannot_drive_cash_negative() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=1.0),)
    outcome = apply_bar(
        _state(cash=0.0, positions=holding),
        _request(cash=0.0, positions=holding, actions=(_place(side="sell", qty=100.0),)),
        _one_bar(close=0.01),
    )

    assert outcome.fills == ()
    assert outcome.state.cash == Decimal("0.00")
    assert [p.qty for p in outcome.state.positions] == [100.0]


def test_a_prior_day_sell_fills_and_keeps_the_cost_basis() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.0),)
    outcome = apply_bar(
        _state(positions=holding),
        _request(positions=holding, actions=(_place(side="sell", qty=100.0),)),
        _one_bar(close=10.0),
    )

    # 1000.00 notional - (max(0.25, 5) + 0.01 transfer + 0.50 stamp) = 994.49
    assert outcome.state.cash == Decimal("100993.49")
    assert outcome.state.positions == ()
    assert outcome.fills[0].price == 9.99  # sell pays the adverse slip


def test_bar_close_prices_off_the_close_while_bar_median_uses_the_median() -> None:
    row = _present_row(_ONE_BAR, close=10.20, open_=10.0, high=10.5, low=9.5)
    timeline = _timeline({_ONE_BAR: (row,)})

    by_close = apply_bar(
        _state(),
        dataclass_replace(_request(actions=(_place(),)), path_model="bar_close"),
        timeline,
    )
    by_median = apply_bar(_state(), _request(actions=(_place(),)), timeline)

    assert by_close.fills[0].price == 10.21  # 10.20 + 10 bps
    assert by_median.fills[0].price == 10.11  # median 10.10 + 10 bps


# ── 8. an invalid action aborts the bar (INVALID_REQUEST) ─────────────────
# Never a RejectedOrder: the frozen vocabulary has no token, and the engine
# copies the book, so a raise leaves the input state untouched.


def _assert_invalid_action(
    state: BarEngineState, bar_request: BarStepRequest, message: str
) -> None:
    before = dataclass_replace(
        state, book=OrderBook(_ACCOUNT, _DAY, rows=state.book.rows()))
    with pytest.raises(BarEngineError, match=message):
        apply_bar(state, bar_request, _one_bar())
    assert state == before


def test_a_duplicate_order_id_place_aborts_the_bar() -> None:
    colliding_id = assign_order_id(_ACCOUNT, _DAY, _ONE_BAR, 1)
    _assert_invalid_action(
        _state(rows=(_order_row(colliding_id, seq=0),), order_seq=1),
        _request(actions=(_place(),), order_seq=1),
        r"place action on bar 09:35 .*duplicate order_id",
    )


def test_a_cancel_of_an_unknown_order_aborts_the_bar() -> None:
    _assert_invalid_action(
        _state(),
        _request(actions=(_place(),), cancels=("missing-order",)),
        r"cancel action on bar 09:35 .*unknown order_id 'missing-order'",
    )


def test_a_replace_of_a_terminal_order_aborts_the_bar() -> None:
    _assert_invalid_action(
        _state(rows=(_order_row(status="canceled"),), order_seq=1),
        _request(replaces=(_replace(_FIRST_ID, replace_limit=9.5),)),
        r"replace action on bar 09:35 .*is terminal",
    )


def test_a_cancel_of_a_filled_order_aborts_the_bar() -> None:
    _assert_invalid_action(
        _state(rows=(_order_row(status="filled"),), order_seq=1),
        _request(cancels=(_FIRST_ID,)),
        r"cancel action on bar 09:35 .*status 'filled' is terminal",
    )


@pytest.mark.parametrize("bad_label", ["09:30", "14:57", "11:35", "15:05"])
def test_a_label_outside_the_48_bar_grid_is_refused(bad_label: str) -> None:
    with pytest.raises(BarEngineError, match="48-label"):
        apply_bar(_state(), _request(bar_time=_bar_time(bad_label)), _one_bar())


def test_a_timeline_or_state_for_another_day_is_refused() -> None:
    other_day = BarTimeline(
        trade_date="2026-10-19",
        bar_labels=(_ONE_BAR,),
        rows_by_bar=_one_bar().rows_by_bar,
        symbols=(_SYM,),
    )
    with pytest.raises(BarEngineError, match="trade_date"):
        apply_bar(_state(), _request(), other_day)

    with pytest.raises(BarEngineError, match="account_id"):
        apply_bar(_state(), dataclass_replace(_request(), account_id="pa_other"), _one_bar())


def test_an_already_applied_bar_is_refused_at_engine_level() -> None:
    with pytest.raises(BarEngineError, match="watermark_bar"):
        apply_bar(_state(watermark=_ONE_BAR), _request(), _one_bar())


# ── 9. the two documented seams ────────────────────────────────────────────


def test_the_reject_hook_terminates_an_order_before_pricing() -> None:
    seen: list[tuple[str, str]] = []

    def reject_hook(order: OrderState, row: BarRow, state: BarEngineState) -> str | None:
        seen.append((order.order_id, row.bar_label))
        assert state.book.rows() == (order,)
        return "lot_size"

    outcome = apply_bar(
        _state(),
        _request(actions=(_place(),)),
        _one_bar(),
        reject_hook=reject_hook,
    )

    assert seen == [(_FIRST_ID, _ONE_BAR)]
    assert outcome.fills == ()
    assert [(row.symbol, row.reason_code) for row in outcome.rejected] == [(_SYM, "lot_size")]
    assert [row.status for row in outcome.state.book.rows()] == ["rejected"]
    rejected_events = [event for event in outcome.order_events if event["event_type"] == "rejected"]
    assert rejected_events[0]["reason_code"] == "lot_size"
    assert outcome.state.cash == Decimal("100000.00")


def test_the_session_end_hook_runs_only_on_the_last_bar_and_its_rows_are_events() -> None:
    calls: list[tuple[str, str]] = []

    def session_end_hook(state: BarEngineState, book: OrderBook, label: str) -> BarEngineState:
        calls.append((state.watermark_bar, label))
        assert book is state.book
        for row in book.open_orders():
            book.cancel(order_id=row.order_id, reason="session_end")
        return state

    first = apply_bar(
        _state(),
        _request(actions=(_place(),)),
        _one_bar(),
        session_end_hook=session_end_hook,
    )
    assert calls == []
    assert first.session_complete is False

    last_timeline = _timeline({"15:00": (_present_row("15:00"),)})
    last = apply_bar(
        _state(),
        _request(bar_time=_bar_time("15:00"), actions=(_place(limit=9.0),)),
        last_timeline,
        session_end_hook=session_end_hook,
    )
    assert calls == [("15:00", "15:00")]
    assert last.session_complete is True
    assert [row.status for row in last.state.book.rows()] == ["canceled"]
    canceled = [event for event in last.order_events if event["event_type"] == "canceled"]
    assert [(event["reason_code"], event["bar_time"]) for event in canceled] == [
        ("session_end", _bar_time("15:00"))
    ]


# ── 10. account bridge + contract composition ──────────────────────────────


def test_from_account_and_to_account_carry_the_session_state() -> None:
    account = create_account(
        account_id=_ACCOUNT,
        market="cn_a",
        start_date=_DAY,
        initial_cash=100_000.0,
        initial_positions=(Position(symbol=_SYM, qty=100.0, avg_cost=9.5),),
        today=_DAY,
        next_trading_day="2026-10-21",
        now=_bar_time("08:00"),
    )
    state = BarEngineState.from_account(account, "cn_a", trade_date=_DAY)
    assert (state.account_id, state.market, state.trade_date) == (_ACCOUNT, "cn_a", _DAY)
    assert state.cash == Decimal("100000.00")
    assert state.positions == account.positions
    assert state.book.rows() == ()

    outcome = apply_bar(state, _request(actions=(_place(),)), _one_bar())
    restored = outcome.state.to_account(account, session_phase="continuous")

    assert restored.cash == 98_993.99
    assert restored.positions == outcome.state.positions
    assert restored.open_orders == ()
    assert restored.order_seq == 1
    assert restored.watermark_bar == _ONE_BAR
    assert restored.session_phase == "continuous"


def test_the_composed_document_passes_the_v13_contract_validator() -> None:
    outcome = apply_bar(
        _state(),
        _request(actions=(_place(), _place(limit=9.0))),
        _one_bar(),
    )
    payload = wire.build_success_payload(outcome.bar_result.result, outcome.bar_result)

    assert payload["schema_version"] == SCHEMA_VERSION_V1_3
    assert payload["bar_time"] == _bar_time(_ONE_BAR)
    assert [row["event_type"] for row in payload["order_events"]] == ["placed", "placed", "filled"]
    assert payload["order_seq"] == 2
    assert payload["slippage"] == {"model": "fixed_bps", "bps": "10", "amount": "0"}
    schema = load_contract_schema("result", version=SCHEMA_VERSION_V1_3)
    assert validate_payload(payload, schema) == []


def test_a_missing_valuation_row_falls_back_to_avg_cost_with_a_warning() -> None:
    holding = (Position(symbol="000002.SZ", qty=100.0, avg_cost=7.5),)
    outcome = apply_bar(
        _state(positions=holding),
        _request(positions=holding),
        _one_bar(),
    )

    assert outcome.bar_result.result.nav_series[0].position_value == 750.0
    assert any("valued at avg_cost" in warning for warning in outcome.bar_result.result.warnings)
