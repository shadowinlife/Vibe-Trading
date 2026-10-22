"""T9 gate — session phases, auction events and the matching gate.

Scope: ``session_events`` in isolation, fed by real ``BarTimeline`` objects
built from the committed 5m fixtures or from in-test frames.  The 48-label grid
is the clock's contract (T7) and the row shape is the timeline's (T8); this
module locks the P1 rule that only ``continuous`` bars may be priced/matched,
that the opening-auction print is silent, and that auction/outside rows emit
deterministic, inside-vocabulary events.

Locks also prove the negative: an auction-only day yields ``auction_window`` +
``halted`` and ZERO fills, and an auction label never reaches a ``match_order``
spy because :func:`require_matchable` raises first.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from src.tools.paper_trade import bar_clock
from src.tools.paper_trade.data_5m import FiveMinuteFacts, fetch_five_minute
from src.tools.paper_trade.schema import SESSION_EVENT_TYPES, SessionEvent
from src.tools.paper_trade.session_events import (
    PhaseGateError,
    auction_only_halted,
    is_matchable,
    matching_bar_labels,
    phase_events,
    require_matchable,
)
from src.tools.paper_trade.timeline import BarRow, BarTimeline, build_timeline
from tests.paper_trade_5m_fixtures import FixtureLoader

_DAY = "2026-10-20"
_SYM = "600519.SH"
_OTHER = "000001.SZ"

#: Every well-formed non-continuous token the gate must reject.  The two
#: trailing entries are unknown/unclassified tokens: fail closed, never assume
#: continuous.
_NON_MATCHABLE = (
    "",
    "opening_auction_print",
    "auction_window",
    "outside",
    "CONTINUOUS",
    "continuous ",
    "unknown_phase",
)


def _facts(symbols: tuple[str, ...], day: str = _DAY) -> FiveMinuteFacts:
    """Real offline facts for ``day`` through the fixture loader (no network)."""
    warnings: list[str] = []
    facts = fetch_five_minute(symbols, day, day, loader=FixtureLoader(), warnings=warnings)
    assert facts.fetch_failed is False
    return facts


def _auction_only(labels: tuple[str, ...]) -> FiveMinuteFacts:
    """One symbol with auction labels and NO continuous bar of any kind."""
    return FiveMinuteFacts(
        auction_by_symbol={_SYM: {_DAY: labels}},
        observed_days_by_symbol={_SYM: (_DAY,)},
    )


def _frame(labels: tuple[str, ...], price: float = 10.0) -> pd.DataFrame:
    """A day frame carrying exactly ``labels`` (used to inject outside rows)."""
    index = pd.DatetimeIndex(
        [bar_clock.bar_time(_DAY, label) for label in labels], name="bar_time"
    )
    return pd.DataFrame(
        {
            "open": [price] * len(labels),
            "high": [price] * len(labels),
            "low": [price] * len(labels),
            "close": [price] * len(labels),
            "volume": [1000.0] * len(labels),
        },
        index=index,
    )


def _outside_timeline(labels: tuple[str, ...]) -> BarTimeline:
    facts = FiveMinuteFacts(
        bars_by_symbol={_SYM: {_DAY: _frame(labels)}},
        observed_days_by_symbol={_SYM: (_DAY,)},
    )
    return build_timeline(facts, _DAY)


def _event_tuples(events: tuple[SessionEvent, ...]) -> list[tuple[str, str, str, str]]:
    return [(e.trade_date, e.symbol, e.event_type, e.detail) for e in events]


# ── matching gate ──────────────────────────────────────────────────────────


def test_every_one_of_the_48_labels_is_continuous_and_matchable() -> None:
    assert len(bar_clock.BAR_LABELS) == 48
    for label in bar_clock.BAR_LABELS:
        phase = bar_clock.classify(label)
        assert phase == "continuous", label
        assert is_matchable(phase) is True, label
        require_matchable(phase, label=label)  # must not raise


@pytest.mark.parametrize("phase", _NON_MATCHABLE)
def test_non_continuous_and_unknown_phases_fail_closed(phase: str) -> None:
    assert is_matchable(phase) is False
    with pytest.raises(PhaseGateError) as excinfo:
        require_matchable(phase, label="14:57")
    message = str(excinfo.value)
    assert repr(phase) in message
    assert "14:57" in message
    assert "not matchable" in message


def test_phase_gate_error_is_a_value_error_and_names_the_phase_without_a_label() -> None:
    assert issubclass(PhaseGateError, ValueError)
    with pytest.raises(PhaseGateError) as excinfo:
        require_matchable("auction_window")
    assert "'auction_window'" in str(excinfo.value)
    assert "for bar" not in str(excinfo.value)


# ── phase -> event table ───────────────────────────────────────────────────


def test_closing_auction_labels_emit_one_auction_window_each() -> None:
    facts = replace(
        _facts((_SYM,)),
        auction_by_symbol={_SYM: {_DAY: ("14:57", "14:58", "14:59")}},
    )
    timeline = build_timeline(facts, _DAY)

    events = phase_events(timeline.auction_rows())

    assert _event_tuples(events) == [
        (_DAY, _SYM, "auction_window", "auction window at 14:57"),
        (_DAY, _SYM, "auction_window", "auction window at 14:58"),
        (_DAY, _SYM, "auction_window", "auction window at 14:59"),
    ]


def test_opening_auction_print_emits_no_event() -> None:
    facts = replace(_facts((_SYM,)), auction_by_symbol={_SYM: {_DAY: ("09:30",)}})
    timeline = build_timeline(facts, _DAY)

    assert [row.phase for row in timeline.auction_rows()] == ["opening_auction_print"]
    assert phase_events(timeline.auction_rows()) == ()


def test_outside_rows_emit_auction_window_events_with_the_outside_detail() -> None:
    timeline = _outside_timeline(("11:35", "12:00", "15:05"))
    assert timeline.outside_labels() == ("11:35", "12:00", "15:05")

    events = phase_events(timeline.outside_rows())

    assert _event_tuples(events) == [
        (_DAY, _SYM, "auction_window", "outside session at 11:35"),
        (_DAY, _SYM, "auction_window", "outside session at 12:00"),
        (_DAY, _SYM, "auction_window", "outside session at 15:05"),
    ]


def test_continuous_day_produces_zero_auction_events() -> None:
    timeline = build_timeline(_facts((_SYM,)), _DAY)
    all_rows = [
        row for label in timeline.bar_labels for row in timeline.rows_for(label)
    ] + list(timeline.auction_rows()) + list(timeline.outside_rows())

    assert phase_events(all_rows) == ()
    assert auction_only_halted(timeline) == ()


def test_auction_only_day_emits_auction_window_and_halted_with_zero_fills() -> None:
    timeline = build_timeline(_auction_only(("09:30", "14:57", "14:58", "14:59")), _DAY)

    events = phase_events(
        (*timeline.auction_rows(), *timeline.outside_rows())
    )
    halted = auction_only_halted(timeline)

    # 09:30 is silent; the three closing-auction labels are not.
    assert _event_tuples(events) == [
        (_DAY, _SYM, "auction_window", "auction window at 14:57"),
        (_DAY, _SYM, "auction_window", "auction window at 14:58"),
        (_DAY, _SYM, "auction_window", "auction window at 14:59"),
    ]
    assert _event_tuples(halted) == [
        (
            _DAY,
            _SYM,
            "halted",
            "auction only at 09:30-14:59; no present continuous bar in 09:35-15:00",
        )
    ]
    assert all(row.status == "missing" for rows in timeline.rows_by_bar.values() for row in rows)

    # The engine's matching loop: only continuous labels pass the gate, and no
    # present row exists, so the spy is never called -> zero fills.
    fills: list[tuple[str, str]] = []

    def match_order(symbol: str, label: str) -> None:  # tiny test spy, no framework
        fills.append((symbol, label))

    for label in matching_bar_labels(timeline):
        require_matchable(bar_clock.classify(label), label=label)
        for row in timeline.rows_for(label):
            if row.status == "present":
                match_order(row.symbol, label)

    assert fills == []


def test_continuous_day_reaches_the_match_spy_once_per_present_bar() -> None:
    timeline = build_timeline(_facts((_SYM,)), _DAY)
    fills: list[tuple[str, str]] = []

    def match_order(symbol: str, label: str) -> None:
        fills.append((symbol, label))

    for label in matching_bar_labels(timeline):
        require_matchable(bar_clock.classify(label), label=label)
        for row in timeline.rows_for(label):
            if row.status == "present":
                match_order(row.symbol, label)

    assert len(fills) == 48
    assert {label for _, label in fills} == set(bar_clock.BAR_LABELS)


def test_auction_and_outside_labels_never_appear_in_matching_bar_labels() -> None:
    facts = replace(
        _facts((_SYM,)),
        auction_by_symbol={_SYM: {_DAY: ("09:30", "14:57")}},
    )
    timeline = build_timeline(facts, _DAY)
    outside = _outside_timeline(("11:35", "12:00", "15:05"))
    combined = BarTimeline(
        trade_date=timeline.trade_date,
        bar_labels=timeline.bar_labels,
        rows_by_bar=timeline.rows_by_bar,
        symbols=timeline.symbols,
        auction=timeline.auction,
        outside=outside.outside,
    )

    matching = matching_bar_labels(combined)

    assert matching == bar_clock.BAR_LABELS
    for label in ("09:30", "14:57", "11:35", "12:00", "15:05"):
        assert label not in matching
        assert is_matchable(bar_clock.classify(label)) is False
        with pytest.raises(PhaseGateError):
            require_matchable(bar_clock.classify(label), label=label)


def test_malformed_label_is_rejected_by_the_matching_gate() -> None:
    corrupt = BarRow(
        trade_date=_DAY,
        bar_label="09:7x",
        bar_time="2026-10-20T09:70:00+08:00",
        symbol=_SYM,
        phase="outside",
        status="outside",
    )
    timeline = BarTimeline(
        trade_date=_DAY,
        bar_labels=(),
        rows_by_bar={},
        symbols=(_SYM,),
        outside=(corrupt,),
    )
    with pytest.raises(ValueError, match="HH:MM"):
        matching_bar_labels(timeline)


# ── determinism, vocabulary and dedupe ─────────────────────────────────────


def test_events_are_sorted_by_label_then_symbol_and_deduped() -> None:
    facts = FiveMinuteFacts(
        auction_by_symbol={
            _SYM: {_DAY: ("14:58", "14:57")},
            _OTHER: {_DAY: ("14:57",)},
        },
        observed_days_by_symbol={_SYM: (_DAY,), _OTHER: (_DAY,)},
    )
    timeline = build_timeline(facts, _DAY)
    rows = list(timeline.auction_rows())

    events = phase_events([*rows, *reversed(rows), *rows])

    assert _event_tuples(events) == [
        (_DAY, _OTHER, "auction_window", "auction window at 14:57"),
        (_DAY, _SYM, "auction_window", "auction window at 14:57"),
        (_DAY, _SYM, "auction_window", "auction window at 14:58"),
    ]


def test_repeated_calls_are_equal_and_emit_no_cached_events() -> None:
    timeline = build_timeline(_auction_only(("14:57",)), _DAY)
    rows = tuple(timeline.auction_rows())

    first = phase_events(rows)
    second = phase_events(rows)

    assert first == second
    assert first is not second
    assert auction_only_halted(timeline) == auction_only_halted(timeline)


def test_emitted_event_types_stay_inside_the_frozen_vocabulary() -> None:
    auction_events = phase_events(
        build_timeline(_auction_only(("14:57",)), _DAY).auction_rows()
    )
    halted_events = auction_only_halted(build_timeline(_auction_only(("14:57",)), _DAY))
    outside_events = phase_events(_outside_timeline(("11:35",)).outside_rows())

    for event in (*auction_events, *halted_events, *outside_events):
        assert isinstance(event, SessionEvent)
        assert event.event_type in SESSION_EVENT_TYPES
    assert {event.event_type for event in (*auction_events, *halted_events, *outside_events)} == {
        "auction_window",
        "halted",
    }
