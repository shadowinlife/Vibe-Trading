"""Todo 18 gate — per-bar session events (:func:`session_events.bar_events`).

Scope: the per-bar translation from a :class:`BarTimeline` to the frozen
four-value ``SESSION_EVENT_TYPES`` vocabulary, and its wiring through
:func:`bar_engine.apply_bar`.  ``session_events`` in isolation is fed by
hand-built timelines here; the engine test proves the two agree exactly.

Locks:

* a mixed bar (one symbol present, one missing) emits ONE ``halted`` for the
  missing symbol — and a whole-bar gap emits NONE (a provider-wide gap is not a
  per-symbol halt);
* a present bar at/over its band emits ONE ``price_limit`` with the side in
  ``detail``; a missing bar (no reference) or a bar with no band emits NONE;
* a present bar's band reference follows ``context.path_model``;
* an auction label emits its ``auction_window`` rows unchanged, once;
* a closed day emits ONE ``non_trading_day`` and suppresses halted/price_limit
  (and the 15:00 day-level halted summary);
* duplicate input rows collapse, and a per-bar halted plus the day-level
  ``auction_only_halted`` for the same symbol/label collapse to one;
* repeated calls are equal (no cache) and every emitted type is frozen;
* ``apply_bar``'s ``events`` array carries exactly ``bar_events`` for the label.
"""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

import pytest

from src.tools.paper_trade.bar_engine import BarEngineState, apply_bar
from src.tools.paper_trade.orders import OrderBook
from src.tools.paper_trade.schema import (
    SESSION_EVENT_TYPES,
    AccountState,
    BarStepRequest,
    SessionEvent,
    parse_slippage,
)
from src.tools.paper_trade.session_events import (
    SessionEventContext,
    auction_only_halted,
    bar_events,
)
from src.tools.paper_trade.timeline import BarRow, BarTimeline

_DAY = "2026-10-20"
_ACCOUNT = "pa_p1_events"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_LABEL = "09:35"


def _bar_time(label: str, day: str = _DAY) -> str:
    return f"{day}T{label}:00+08:00"


def _present_row(
    label: str,
    symbol: str = _SYM,
    *,
    open_: float = 10.0,
    high: float | None = None,
    low: float | None = None,
    close: float = 10.0,
) -> BarRow:
    highest = close if high is None else high
    lowest = close if low is None else low
    prices = sorted([open_, highest, lowest, close])
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="continuous",
        status="present",
        open=open_,
        high=highest,
        low=lowest,
        close=close,
        volume=1_000.0,
        reference_median=(prices[1] + prices[2]) / 2,
        reference_close=close,
    )


def _missing_row(label: str, symbol: str = _SYM) -> BarRow:
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="continuous",
        status="missing",
    )


def _auction_row(label: str, symbol: str = _SYM) -> BarRow:
    return BarRow(
        trade_date=_DAY,
        bar_label=label,
        bar_time=_bar_time(label),
        symbol=symbol,
        phase="auction_window",
        status="auction",
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


def _tuples(events: tuple[SessionEvent, ...]) -> list[tuple[str, str, str, str]]:
    return [(e.trade_date, e.symbol, e.event_type, e.detail) for e in events]


def _mixed_timeline() -> BarTimeline:
    return _timeline(
        {_LABEL: (_present_row(_LABEL, close=11.0), _missing_row(_LABEL, _OTHER))},
        symbols=(_SYM, _OTHER),
    )


def _state() -> BarEngineState:
    return BarEngineState(
        account_id=_ACCOUNT,
        market="cn_a",
        trade_date=_DAY,
        cash=Decimal("100000.00"),
        positions=(),
        book=OrderBook(_ACCOUNT, _DAY, rows=()),
        order_seq=0,
        bought_today={},
        watermark_bar="",
    )


def _request(*, path_model: str = "bar_median", label: str = _LABEL) -> BarStepRequest:
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=100_000.0, positions=()),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=_bar_time(label),
        path_model=path_model,
        slippage=parse_slippage(None),
    )


# ── halted ─────────────────────────────────────────────────────────────────


def test_a_mixed_bar_emits_one_halted_for_the_missing_symbol() -> None:
    events = bar_events(_mixed_timeline(), _LABEL)

    assert _tuples(events) == [(_DAY, _OTHER, "halted", "halted at 09:35")]


def test_a_whole_bar_gap_emits_no_per_symbol_halted() -> None:
    timeline = _timeline(
        {_LABEL: (_missing_row(_LABEL, _SYM), _missing_row(_LABEL, _OTHER))},
        symbols=(_SYM, _OTHER),
    )

    assert bar_events(timeline, _LABEL) == ()


def test_a_missing_row_with_no_present_peer_never_emits_halted_and_no_event_twice() -> None:
    timeline = _timeline(
        {
            "09:35": (_missing_row("09:35"),),
            "09:40": (_present_row("09:40"), _missing_row("09:40", _OTHER)),
        },
        symbols=(_SYM, _OTHER),
    )

    first = bar_events(timeline, "09:35")
    second = bar_events(timeline, "09:35")

    assert first == second == ()
    assert _tuples(bar_events(timeline, "09:40")) == [
        (_DAY, _OTHER, "halted", "halted at 09:40")
    ]


# ── price_limit ────────────────────────────────────────────────────────────


def test_a_bar_at_the_upper_band_emits_one_price_limit_with_limit_up() -> None:
    timeline = _timeline({_LABEL: (_present_row(_LABEL, close=11.0),)})

    events = bar_events(timeline, _LABEL, context=SessionEventContext(bands={_SYM: (9.0, 11.0)}))

    assert _tuples(events) == [(_DAY, _SYM, "price_limit", "limit_up at 09:35")]


def test_a_bar_at_the_lower_band_emits_one_price_limit_with_limit_down() -> None:
    timeline = _timeline({_LABEL: (_present_row(_LABEL, close=9.0),)})

    events = bar_events(timeline, _LABEL, context=SessionEventContext(bands={_SYM: (9.0, 11.0)}))

    assert _tuples(events) == [(_DAY, _SYM, "price_limit", "limit_down at 09:35")]


def test_a_missing_bar_never_emits_price_limit_even_with_a_band() -> None:
    timeline = _timeline({_LABEL: (_missing_row(_LABEL),)})

    events = bar_events(timeline, _LABEL, context=SessionEventContext(bands={_SYM: (9.0, 10.0)}))

    assert events == ()


@pytest.mark.parametrize("bands", [None, {}])
def test_a_present_bar_with_no_band_emits_no_price_limit(
    bands: dict[str, tuple[float, float]] | None,
) -> None:
    timeline = _timeline({_LABEL: (_present_row(_LABEL, close=11.0),)})

    assert bar_events(timeline, _LABEL, context=SessionEventContext(bands=bands)) == ()


def test_the_band_reference_follows_the_context_path_model() -> None:
    # open=9.0, high=11.0, low=9.0, close=10.0 -> median 9.5, close 10.0.
    timeline = _timeline({_LABEL: (_present_row(_LABEL, open_=9.0, high=11.0, low=9.0, close=10.0),)})
    bands = {_SYM: (9.6, 12.0)}

    median_events = bar_events(
        timeline, _LABEL, context=SessionEventContext(bands=bands, path_model="bar_median")
    )
    close_events = bar_events(
        timeline, _LABEL, context=SessionEventContext(bands=bands, path_model="bar_close")
    )

    assert _tuples(median_events) == [(_DAY, _SYM, "price_limit", "limit_down at 09:35")]
    assert close_events == ()


# ── auction_window ─────────────────────────────────────────────────────────


def test_an_auction_label_emits_one_auction_window() -> None:
    auction = _auction_row("14:57")
    timeline = _timeline({}, auction=(auction,))

    events = bar_events(timeline, "14:57")

    assert _tuples(events) == [(_DAY, _SYM, "auction_window", "auction window at 14:57")]


def test_a_duplicate_input_row_collapses_to_one_event() -> None:
    auction = _auction_row("14:57")
    timeline = _timeline({}, auction=(auction, auction))

    events = bar_events(timeline, "14:57")

    assert _tuples(events) == [(_DAY, _SYM, "auction_window", "auction window at 14:57")]


# ── non_trading_day ────────────────────────────────────────────────────────


def test_a_closed_day_emits_one_non_trading_day_and_suppresses_halted_and_price_limit() -> None:
    timeline = _timeline(
        {_LABEL: (_present_row(_LABEL, close=11.0), _missing_row(_LABEL, _OTHER))},
        symbols=(_SYM, _OTHER),
    )
    context = SessionEventContext(trading_day=False, bands={_SYM: (9.0, 11.0)})

    events = bar_events(timeline, _LABEL, context=context)

    assert _tuples(events) == [(_DAY, "", "non_trading_day", "non_trading_day at 2026-10-20")]
    assert [event.event_type for event in events] == ["non_trading_day"]


def test_a_closed_closing_bar_does_not_add_the_day_level_halted_summary() -> None:
    timeline = _timeline(
        {"15:00": (_missing_row("15:00"), _present_row("15:00", _OTHER))},
        symbols=(_SYM, _OTHER),
        auction=(_auction_row("14:57"),),
    )

    open_day = bar_events(timeline, "15:00")
    closed_day = bar_events(timeline, "15:00", context=SessionEventContext(trading_day=False))

    assert "halted" in {event.event_type for event in open_day}
    assert _tuples(closed_day) == [(_DAY, "", "non_trading_day", "non_trading_day at 2026-10-20")]


# ── the 15:00 day-level summary + dedupe ───────────────────────────────────


def test_the_closing_bar_adds_the_day_level_auction_only_halted_once() -> None:
    timeline = _timeline(
        {"15:00": (_missing_row("15:00"),)},
        auction=(_auction_row("14:57"), _auction_row("14:58"), _auction_row("14:59")),
    )

    events = bar_events(timeline, "15:00")

    assert _tuples(events) == _tuples(auction_only_halted(timeline))
    assert [event.event_type for event in events] == ["halted"]


def test_an_earlier_bar_does_not_carry_the_day_level_halted_summary() -> None:
    timeline = _timeline(
        {"09:35": (_missing_row("09:35"), _present_row("09:35", _OTHER))},
        symbols=(_SYM, _OTHER),
        auction=(_auction_row("14:57"),),
    )

    events = bar_events(timeline, "09:35")

    assert _tuples(events) == [(_DAY, _SYM, "halted", "halted at 09:35")]


def test_a_per_bar_halted_collapses_with_the_day_level_halted_on_the_closing_bar() -> None:
    timeline = _timeline(
        {"15:00": (_missing_row("15:00"), _present_row("15:00", _OTHER))},
        symbols=(_SYM, _OTHER),
        auction=(_auction_row("14:57"),),
    )

    events = bar_events(timeline, "15:00")
    halted = [event for event in events if event.event_type == "halted"]

    # Same (type, date, symbol, label): the lexicographically smaller day-level
    # detail wins, so the per-bar "halted at 15:00" row is collapsed away.
    assert len(halted) == 1
    assert halted[0].symbol == _SYM
    assert halted[0].detail.startswith("auction only at 14:57-14:57;")


# ── determinism, vocabulary and the context contract ───────────────────────


def test_repeated_calls_are_equal_and_return_fresh_tuples() -> None:
    timeline = _mixed_timeline()

    first = bar_events(timeline, _LABEL)
    second = bar_events(timeline, _LABEL)

    assert first == second
    assert first is not second


def test_every_emitted_type_is_inside_the_frozen_vocabulary() -> None:
    closed = bar_events(
        _mixed_timeline(), _LABEL, context=SessionEventContext(trading_day=False)
    )
    halted = bar_events(_mixed_timeline(), _LABEL)
    limited = bar_events(
        _timeline({_LABEL: (_present_row(_LABEL, close=11.0),)}),
        _LABEL,
        context=SessionEventContext(bands={_SYM: (9.0, 11.0)}),
    )
    auction = bar_events(_timeline({}, auction=(_auction_row("14:57"),)), "14:57")

    everything = (*closed, *halted, *limited, *auction)
    assert {event.event_type for event in everything} == set(SESSION_EVENT_TYPES)
    for event in everything:
        assert isinstance(event, SessionEvent)
        assert event.event_type in SESSION_EVENT_TYPES


def test_an_unknown_path_model_is_rejected_and_the_frozen_ones_accepted() -> None:
    with pytest.raises(ValueError, match="path_model"):
        SessionEventContext(path_model="vwap")

    for model in ("bar_median", "bar_close"):
        assert SessionEventContext(path_model=model).path_model == model


def test_a_non_mapping_band_table_is_rejected() -> None:
    with pytest.raises(ValueError, match="bands"):
        SessionEventContext(bands=[(_SYM, (9.0, 11.0))])  # type: ignore[arg-type]


def test_the_context_copies_the_band_table_so_later_mutation_cannot_change_events() -> None:
    bands = {_SYM: (9.0, 11.0)}
    context = SessionEventContext(bands=bands)
    bands[_SYM] = (0.0, 1.0)
    bands[_OTHER] = (0.0, 1.0)

    assert dict(context.bands or {}) == {_SYM: (9.0, 11.0)}


def test_bar_events_rejects_a_non_context_argument() -> None:
    with pytest.raises(ValueError, match="SessionEventContext"):
        bar_events(_mixed_timeline(), _LABEL, context={})  # type: ignore[arg-type]


# ── engine integration ─────────────────────────────────────────────────────


def test_the_engine_events_equal_bar_events_for_that_label() -> None:
    timeline = _mixed_timeline()
    bands = {_SYM: (9.0, 11.0)}

    outcome = apply_bar(_state(), _request(), timeline, bands=bands)
    expected = bar_events(timeline, _LABEL, context=SessionEventContext(bands=bands))

    assert outcome.session_events == expected
    assert _tuples(outcome.session_events) == [
        (_DAY, _OTHER, "halted", "halted at 09:35"),
        (_DAY, _SYM, "price_limit", "limit_up at 09:35"),
    ]


def test_the_engine_accepts_an_explicit_events_context() -> None:
    timeline = _timeline({_LABEL: (_present_row(_LABEL),)})
    context = SessionEventContext(trading_day=False)

    outcome = apply_bar(_state(), _request(), timeline, events_context=context)

    assert _tuples(outcome.session_events) == [
        (_DAY, "", "non_trading_day", "non_trading_day at 2026-10-20")
    ]


def test_the_engine_default_event_context_reads_the_request_path_model() -> None:
    # open=9.0, high=11.0, low=9.0, close=10.0 -> median 9.5, close 10.0.
    timeline = _timeline(
        {_LABEL: (_present_row(_LABEL, open_=9.0, high=11.0, low=9.0, close=10.0),)}
    )
    bands = {_SYM: (9.6, 12.0)}

    by_median = apply_bar(_state(), _request(path_model="bar_median"), timeline, bands=bands)
    by_close = apply_bar(_state(), _request(path_model="bar_close"), timeline, bands=bands)

    assert _tuples(by_median.session_events) == [
        (_DAY, _SYM, "price_limit", "limit_down at 09:35")
    ]
    assert by_close.session_events == ()
