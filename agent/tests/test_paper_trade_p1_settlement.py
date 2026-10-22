"""Todo 19 gate — end-of-session expiry and settlement (pt-v1.3).

* a ``day`` order resting all day is ``expired`` on the 15:00 bar, emits ONE
  ``expired`` event, leaves ``open_orders()`` and is terminal thereafter;
* an expired order is NOT a ``CanceledOrder`` (that array is the IOC/FOK
  emergency-cancel channel);
* IOC/FOK are already ``canceled`` by ``end_of_bar`` and are untouched by the
  expiry;
* a 09:35 buy leaves ``bought_today`` populated, the 15:00 bar clears it, and
  the NEXT session's sell is therefore not blocked by T+1;
* expiry moves no cash and no position;
* the hook contract: 15:00 only, the state's own book only, idempotent, and a
  second 15:00 bar on the same state is refused by the watermark gate.

Offline, deterministic: hand-built timelines, no clock, no network, no files.
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from src.tools.paper_trade import wire
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.bar_engine import BarEngineError, BarEngineState, apply_bar
from src.tools.paper_trade.order_ids import assign_order_id
from src.tools.paper_trade.orders import InvalidTransition, OrderBook
from src.tools.paper_trade.schema import (
    SCHEMA_VERSION_V1_3,
    AccountState,
    BarStepRequest,
    OrderAction,
    OrderState,
    Position,
    load_contract_schema,
    parse_slippage,
    validate_payload,
)
from src.tools.paper_trade.session_settlement import (
    SettlementError,
    make_session_end_hook,
    settle_session,
)
from src.tools.paper_trade.timeline import BarRow, BarTimeline

_ACCOUNT = "pa_p1_settle"
_DAY = "2026-10-20"
_NEXT_DAY = "2026-10-21"
_SYM = "600519.SH"
_OPEN_BAR = "09:35"
_LAST_BAR = "15:00"
_FIRST_ID = assign_order_id(_ACCOUNT, _DAY, _OPEN_BAR, 0)


# ── fixtures/helpers ──────────────────────────────────────────────────────


def _bar_time(label: str, day: str = _DAY) -> str:
    return f"{day}T{label}:00+08:00"


def _present_row(label: str, *, day: str = _DAY, close: float = 10.0) -> BarRow:
    return BarRow(
        trade_date=day,
        bar_label=label,
        bar_time=_bar_time(label, day),
        symbol=_SYM,
        phase="continuous",
        status="present",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
        reference_median=close,
        reference_close=close,
    )


def _timeline(day: str = _DAY) -> BarTimeline:
    """The opening and the closing bar of one session."""
    return BarTimeline(
        trade_date=day,
        bar_labels=(_OPEN_BAR, _LAST_BAR),
        rows_by_bar=MappingProxyType({
            _OPEN_BAR: (_present_row(_OPEN_BAR, day=day),),
            _LAST_BAR: (_present_row(_LAST_BAR, day=day),),
        }),
        symbols=(_SYM,),
    )


def _place(
    *,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
) -> OrderAction:
    return OrderAction(
        action="place", symbol=_SYM, side=side, qty=qty, limit=limit, tif=tif
    )


def _request(
    *,
    actions: tuple[OrderAction, ...] = (),
    bar_time: str = _bar_time(_OPEN_BAR),
    cash: float = 100_000.0,
    day: str = _DAY,
) -> BarStepRequest:
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=cash, positions=()),
        from_date=day,
        to_date=day,
        bar_time=bar_time,
        order_actions=actions,
        slippage=parse_slippage(None),
    )


def _order_row(
    *,
    seq: int = 0,
    tif: str = "day",
    status: str = "new",
    order_id: str = "",
) -> OrderState:
    filled = 100.0 if status == "filled" else 0.0
    return OrderState(
        order_id=order_id or assign_order_id(_ACCOUNT, _DAY, _OPEN_BAR, seq),
        account_id=_ACCOUNT,
        trade_date=_DAY,
        symbol=_SYM,
        side="buy",
        qty=100.0,
        remaining_qty=100.0 - filled,
        limit=None,
        tif=tif,
        status=status,
        bar_label=_OPEN_BAR,
        client_order_id="",
        filled_qty=filled,
        avg_fill_price=10.0 if filled else 0.0,
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


def _resting_day_order_settled() -> tuple[BarEngineState, Any]:
    """Place a non-marketable day buy at 09:35, then close the session at 15:00."""
    opened = apply_bar(_state(), _request(actions=(_place(limit=9.0),)), _timeline())
    closed = apply_bar(opened.state, _request(bar_time=_bar_time(_LAST_BAR)), _timeline())
    return opened.state, closed


def _event_types(outcome: Any) -> list[str]:
    return [event["event_type"] for event in outcome.order_events]


# ── 1. the resting day order expires at 15:00 ─────────────────────────────


def test_a_day_order_resting_all_day_expires_on_the_closing_bar() -> None:
    opened, closed = _resting_day_order_settled()

    assert [row.status for row in opened.book.rows()] == ["new"]
    assert opened.bought_today == {}

    expired, = closed.state.book.rows()
    assert expired.status == "expired"
    assert expired.order_id == _FIRST_ID
    assert expired.seq == 0
    assert expired.remaining_qty == 100.0
    assert closed.state.book.open_orders() == ()
    assert closed.bar_result.open_orders == ()
    assert closed.bar_result.result.canceled_orders == ()
    assert closed.session_complete is True


def test_expiry_emits_exactly_one_expired_event_with_the_reason() -> None:
    _, closed = _resting_day_order_settled()

    assert _event_types(closed) == ["expired"]
    event, = closed.order_events
    assert event["event_type"] == "expired"
    assert event["order_id"] == _FIRST_ID
    assert event["symbol"] == _SYM
    assert event["side"] == "buy"
    assert event["qty"] == 100.0
    assert event["detail"] == "day order expired at 15:00"
    assert event["bar_time"] == _bar_time(_LAST_BAR)
    assert "price" not in event
    assert "reason_code" not in event


def test_an_expired_order_cannot_be_cancelled_or_replaced() -> None:
    _, closed = _resting_day_order_settled()

    with pytest.raises(InvalidTransition, match="terminal"):
        closed.state.book.cancel(order_id=_FIRST_ID)
    with pytest.raises(InvalidTransition, match="terminal"):
        closed.state.book.replace(order_id=_FIRST_ID, replace_qty=50.0)


def test_the_expired_event_passes_the_v13_contract_validator() -> None:
    _, closed = _resting_day_order_settled()
    payload = wire.build_success_payload(closed.bar_result.result, closed.bar_result)

    assert payload["schema_version"] == SCHEMA_VERSION_V1_3
    assert [row["event_type"] for row in payload["order_events"]] == ["expired"]
    assert payload["order_events"][0]["detail"] == "day order expired at 15:00"
    schema = load_contract_schema("result", version=SCHEMA_VERSION_V1_3)
    assert validate_payload(payload, schema) == []


# ── 2. IOC/FOK already terminal, expiry does not touch them ───────────────


def test_ioc_and_fok_are_canceled_by_the_bar_and_untouched_by_expiry() -> None:
    outcome = apply_bar(
        _state(),
        _request(
            bar_time=_bar_time(_LAST_BAR),
            actions=(
                _place(limit=9.0),
                _place(limit=9.0, tif="ioc"),
                _place(limit=9.0, tif="fok"),
            ),
        ),
        _timeline(),
    )

    assert [row.status for row in outcome.state.book.rows()] == [
        "expired",
        "canceled",
        "canceled",
    ]
    canceled = [row for row in outcome.bar_result.result.canceled_orders]
    assert len(canceled) == 2
    canceled_events = [
        event for event in outcome.order_events if event["event_type"] == "canceled"
    ]
    assert [event["reason_code"] for event in canceled_events] == [
        "ioc_unfilled",
        "fok_unfilled",
    ]
    assert _event_types(outcome) == [
        "placed",
        "placed",
        "placed",
        "canceled",
        "canceled",
        "expired",
    ]
    # The expiry is reported as an event, never as an emergency cancel.
    assert len(canceled) == 2


# ── 3. T+1 reset carries into the next session ────────────────────────────


def test_the_next_session_sell_of_yesterdays_holding_is_not_t_plus_1() -> None:
    account = create_account(
        account_id=_ACCOUNT,
        market="cn_a",
        start_date=_DAY,
        initial_cash=100_000.0,
        today=_DAY,
        next_trading_day=_NEXT_DAY,
        now=_bar_time("08:00"),
    )
    day_one = BarEngineState.from_account(account, "cn_a", trade_date=_DAY)

    bought = apply_bar(day_one, _request(actions=(_place(),)), _timeline())
    assert bought.state.bought_today == {_SYM: Decimal("100.0")}
    assert bought.state.positions == (Position(symbol=_SYM, qty=100.0, avg_cost=10.01),)

    closed = apply_bar(
        bought.state, _request(bar_time=_bar_time(_LAST_BAR)), _timeline()
    )
    assert closed.state.bought_today == {}

    next_account = closed.state.to_account(account)
    # Opening the next session resets the per-session watermark (todo 24's roll).
    next_open = dataclass_replace(next_account, watermark_bar="")
    next_day = BarEngineState.from_account(next_open, "cn_a", trade_date=_NEXT_DAY)
    assert next_day.bought_today == {}
    assert next_day.positions == (Position(symbol=_SYM, qty=100.0, avg_cost=10.01),)

    # A carried ``bought_today`` would size this sell at 100 - 100 = 0 and the
    # order would REST; a fresh session fills it.
    sold = apply_bar(
        next_day,
        _request(
            bar_time=_bar_time(_OPEN_BAR, _NEXT_DAY),
            actions=(_place(side="sell"),),
            day=_NEXT_DAY,
        ),
        _timeline(_NEXT_DAY),
    )
    assert [fill.side for fill in sold.fills] == ["sell"]
    assert [fill.symbol for fill in sold.fills] == [_SYM]
    assert sold.rejected == ()
    assert sold.state.book.open_orders() == ()
    assert sold.state.cash > closed.state.cash


# ── 4. no cash / position movement at expiry ──────────────────────────────


def test_expiry_moves_no_cash_and_no_position() -> None:
    opened, closed = _resting_day_order_settled()

    assert closed.fills == ()
    assert closed.state.cash == opened.cash == Decimal("100000.00")
    assert closed.state.positions == opened.positions == ()
    assert closed.state.order_seq == opened.order_seq == 1
    nav, = closed.bar_result.result.nav_series
    assert (nav.cash, nav.nav) == (100_000.0, 100_000.0)


# ── 5. hook contract ──────────────────────────────────────────────────────


def test_settle_session_refuses_a_non_15_00_label() -> None:
    state = _state(rows=(_order_row(),), order_seq=1)

    with pytest.raises(SettlementError, match="15:00"):
        settle_session(state, state.book, "14:55")


def test_settle_session_refuses_a_foreign_book() -> None:
    state = _state(rows=(_order_row(),), order_seq=1)
    foreign = OrderBook(_ACCOUNT, _DAY)

    with pytest.raises(SettlementError, match="own book"):
        settle_session(state, foreign, _LAST_BAR)


def test_settle_session_preserves_identity_and_only_clears_bought_today() -> None:
    holding = (Position(symbol=_SYM, qty=100.0, avg_cost=9.5),)
    state = _state(
        positions=holding,
        rows=(_order_row(),),
        order_seq=1,
        watermark=_LAST_BAR,
        bought_today={_SYM: Decimal("100.0")},
    )

    settled = settle_session(state, state.book, _LAST_BAR)

    assert settled.book is state.book
    assert (settled.account_id, settled.market, settled.trade_date) == (
        state.account_id,
        state.market,
        state.trade_date,
    )
    assert (settled.cash, settled.positions) == (state.cash, state.positions)
    assert (settled.order_seq, settled.watermark_bar) == (
        state.order_seq,
        state.watermark_bar,
    )
    assert settled.bought_today == {}
    assert [row.status for row in settled.book.rows()] == ["expired"]


def test_expire_day_orders_is_idempotent_and_leaves_terminal_rows_alone() -> None:
    rows = (
        _order_row(seq=0),                     # new day  -> expires
        _order_row(seq=1, status="filled"),     # terminal -> untouched
        _order_row(seq=2, status="canceled"),   # terminal -> untouched
        _order_row(seq=3, status="rejected"),   # terminal -> untouched
        _order_row(seq=4, status="expired"),    # terminal -> untouched
        _order_row(seq=5, tif="ioc"),           # IOC -> untouched by expiry
        _order_row(seq=6, tif="fok"),           # FOK -> untouched by expiry
    )
    book = OrderBook(_ACCOUNT, _DAY, rows=rows)

    changed = book.expire_day_orders(reason="session end")
    assert [row.seq for row in changed] == [0]
    assert changed[0].status == "expired"
    assert changed[0].remaining_qty == 100.0

    assert book.expire_day_orders(reason="session end") == ()
    assert [(row.seq, row.status) for row in book.rows()] == [
        (0, "expired"),
        (1, "filled"),
        (2, "canceled"),
        (3, "rejected"),
        (4, "expired"),
        (5, "new"),
        (6, "new"),
    ]


def test_make_session_end_hook_returns_the_settlement_hook() -> None:
    assert make_session_end_hook() is settle_session


def test_an_explicit_hook_replaces_the_default_settlement() -> None:
    def leave_everything_open(
        state: BarEngineState, book: OrderBook, label: str
    ) -> BarEngineState:
        assert book is state.book
        assert label == _LAST_BAR
        return state

    outcome = apply_bar(
        _state(bought_today={_SYM: Decimal("100.0")}),
        _request(bar_time=_bar_time(_LAST_BAR), actions=(_place(limit=9.0),)),
        _timeline(),
        session_end_hook=leave_everything_open,
    )

    assert [row.status for row in outcome.state.book.rows()] == ["new"]
    assert outcome.state.bought_today == {_SYM: Decimal("100.0")}
    assert _event_types(outcome) == ["placed"]


def test_a_second_15_00_bar_on_the_same_state_is_refused_by_the_watermark() -> None:
    _, closed = _resting_day_order_settled()
    assert closed.state.watermark_bar == _LAST_BAR

    with pytest.raises(BarEngineError, match="watermark_bar"):
        apply_bar(closed.state, _request(bar_time=_bar_time(_LAST_BAR)), _timeline())


def test_settlement_does_not_run_before_the_last_bar() -> None:
    outcome = apply_bar(
        _state(bought_today={_SYM: Decimal("100.0")}),
        _request(actions=(_place(limit=9.0),)),
        _timeline(),
    )

    assert [row.status for row in outcome.state.book.rows()] == ["new"]
    assert outcome.state.bought_today == {_SYM: Decimal("100.0")}
