"""T8 gate — deterministic per-bar timeline built from ``FiveMinuteFacts``.

Scope: the data-shape transformation only.  The 48-label grid and phase table
are owned by ``test_paper_trade_p1_clock.py``; nothing here re-tests the clock
or fetches the network — the facts come from the committed 5m fixtures through
the offline ``FixtureLoader`` or from in-test frames.

Locks the P1 timeline invariants: one row per ``(label, symbol)`` in canonical
bar-END order, explicit ``present``/``missing``/``auction``/``outside``
markers, precomputed reference prices, deterministic rebuilds, source-frame
mutation isolation, and the deterministic ``TimelineError`` ladder.
"""

from __future__ import annotations

from dataclasses import replace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.tools.paper_trade import bar_clock, timeline
from src.tools.paper_trade.data_5m import FiveMinuteFacts, fetch_five_minute
from src.tools.paper_trade.schema_v13 import SESSION_PHASES
from src.tools.paper_trade.timeline import BarRow, BarTimeline, TimelineError, build_timeline
from tests.paper_trade_5m_fixtures import FixtureLoader, continuous_bars, load_5m

_DAY = "2026-10-20"
_HALTED_DAY = "2026-10-19"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_ABSENT = "300750.SZ"


def _facts(symbols: tuple[str, ...], day: str) -> FiveMinuteFacts:
    """Real offline facts for ``day`` through the fixture loader (no network)."""
    warnings: list[str] = []
    facts = fetch_five_minute(
        symbols, day, day, loader=FixtureLoader(), warnings=warnings
    )
    assert facts.fetch_failed is False
    return facts


def _manual(
    frame: pd.DataFrame, *, day: str = _DAY, symbol: str = _SYM
) -> FiveMinuteFacts:
    """Facts with an exact day frame, so a malformed shape can be injected."""
    return FiveMinuteFacts(
        bars_by_symbol={symbol: {day: frame}},
        observed_days_by_symbol={symbol: (day,)},
    )


def _two_symbol_facts() -> FiveMinuteFacts:
    frame = continuous_bars(load_5m(_SYM, _DAY))
    return FiveMinuteFacts(
        bars_by_symbol={_SYM: {_DAY: frame.copy()}, _OTHER: {_DAY: frame.copy()}},
        observed_days_by_symbol={_SYM: (_DAY,), _OTHER: (_DAY,)},
    )


# ── present grid ───────────────────────────────────────────────────────────


def test_normal_fixture_yields_one_present_row_per_bar_in_grid_order() -> None:
    facts = _facts((_SYM,), _DAY)
    result = build_timeline(facts, _DAY)

    assert isinstance(result, BarTimeline)
    assert result.trade_date == _DAY
    assert result.symbols == (_SYM,)
    assert result.bar_labels == bar_clock.BAR_LABELS
    assert tuple(result.rows_by_bar) == bar_clock.BAR_LABELS
    assert len(result.rows_by_bar) == 48
    assert sum(len(rows) for rows in result.rows_by_bar.values()) == 48
    assert all(len(rows) == 1 for rows in result.rows_by_bar.values())
    assert result.labels_with_status("present") == bar_clock.BAR_LABELS
    assert result.labels_with_status("missing") == ()
    assert result.missing_by_symbol() == {}
    assert result.auction_rows() == result.outside_rows() == ()

    first = result.row("09:35", _SYM)
    assert (first.status, first.phase) == ("present", "continuous")
    assert first.bar_label == "09:35"
    assert first.bar_time == "2026-10-20T09:35:00+08:00"
    assert (first.open, first.high, first.low, first.close, first.volume) == (
        1421.0,
        1421.0,
        1421.0,
        1421.0,
        120000.0,
    )
    assert (first.reference_median, first.reference_close) == (1421.0, 1421.0)
    assert first.provider_omitted is False

    last = result.row("15:00", _SYM)
    assert (last.close, last.reference_close) == (1422.0, 1422.0)
    assert (last.open, last.high, last.low) == (1422.0, 1422.0, 1422.0)

    # 09:30 is the opening-auction print, not a bar-end on the 48-label grid.
    with pytest.raises(TimelineError, match=r"not part of the 2026-10-20 timeline"):
        result.rows_for("09:30")


def test_two_symbols_produce_48_rows_each_in_sorted_symbol_order() -> None:
    result = build_timeline(_two_symbol_facts(), _DAY)

    assert result.symbols == (_OTHER, _SYM)
    assert len(result.rows_by_bar) == 48
    assert sum(len(rows) for rows in result.rows_by_bar.values()) == 96
    for label in bar_clock.BAR_LABELS:
        rows = result.rows_for(label)
        assert [row.symbol for row in rows] == [_OTHER, _SYM]
        assert all(row.status == "present" for row in rows)
    assert result.row("15:00", _OTHER).close == 1422.0


def test_rows_are_plain_scalars_and_never_pandas_objects() -> None:
    result = build_timeline(_facts((_SYM,), _DAY), _DAY)

    assert timeline.BAR_STATUSES == ("present", "missing", "auction", "outside")
    for label in bar_clock.BAR_LABELS:
        (row,) = result.rows_for(label)
        assert isinstance(row, BarRow)
        assert row.phase in SESSION_PHASES
        assert row.status == "present"
        assert isinstance(row.bar_time, str) and row.bar_time.endswith("+08:00")
        assert isinstance(row.trade_date, str)
        assert isinstance(row.symbol, str)
        for field in ("open", "high", "low", "close", "volume",
                      "reference_median", "reference_close"):
            value = getattr(row, field)
            assert type(value) is float, (label, field)


# ── missing markers ────────────────────────────────────────────────────────


def test_halted_symbol_day_is_all_missing_and_flagged_as_provider_omitted() -> None:
    facts = _facts((_SYM, _OTHER), _HALTED_DAY)
    assert facts.missing_symbols == (_SYM,)  # 600519 has a header-only fixture
    result = build_timeline(facts, _HALTED_DAY)

    assert result.symbols == (_OTHER, _SYM)
    assert result.missing_by_symbol() == {_SYM: bar_clock.BAR_LABELS}
    assert result.labels_with_status("missing") == bar_clock.BAR_LABELS
    assert result.labels_with_status("present") == bar_clock.BAR_LABELS  # via _OTHER
    for label in bar_clock.BAR_LABELS:
        halted = result.row(label, _SYM)
        assert halted.status == "missing"
        assert halted.provider_omitted is True
        assert (
            halted.open,
            halted.high,
            halted.low,
            halted.close,
            halted.volume,
            halted.reference_median,
            halted.reference_close,
        ) == (None,) * 7
        traded = result.row(label, _OTHER)
        assert traded.status == "present"
        assert traded.provider_omitted is False
    assert result.row("15:00", _OTHER).close == 10.02


def test_partial_day_gap_is_missing_without_the_provider_flag() -> None:
    full = continuous_bars(load_5m(_SYM, _DAY))
    kept = full[full.index.strftime("%H:%M").isin(bar_clock.BAR_LABELS[:30])]
    result = build_timeline(_manual(kept), _DAY)

    assert result.missing_by_symbol() == {_SYM: bar_clock.BAR_LABELS[30:]}
    assert result.row(bar_clock.BAR_LABELS[29], _SYM).status == "present"
    gap = result.row(bar_clock.BAR_LABELS[30], _SYM)
    assert gap.status == "missing"
    assert gap.provider_omitted is False
    assert (
        gap.open,
        gap.high,
        gap.low,
        gap.close,
        gap.volume,
        gap.reference_median,
        gap.reference_close,
    ) == (None,) * 7


def test_a_missing_row_can_never_carry_ohlcv() -> None:
    with pytest.raises(TimelineError, match="must not carry OHLCV"):
        BarRow(
            trade_date=_DAY,
            bar_label="09:35",
            bar_time="2026-10-20T09:35:00+08:00",
            symbol=_SYM,
            phase="continuous",
            status="missing",
            open=1.0,
        )
    with pytest.raises(TimelineError, match="present bar needs OHLCV"):
        BarRow(
            trade_date=_DAY,
            bar_label="09:35",
            bar_time="2026-10-20T09:35:00+08:00",
            symbol=_SYM,
            phase="continuous",
            status="present",
            close=1.0,
        )


# ── auction / outside markers ──────────────────────────────────────────────


def test_auction_labels_get_their_own_rows_and_never_enter_the_grid() -> None:
    base = _facts((_SYM,), _DAY)
    facts = replace(
        base,
        auction_by_symbol={_SYM: {_DAY: ("14:57", "09:15", "14:58")}},
    )
    result = build_timeline(facts, _DAY)

    auctions = result.auction_rows()
    assert [row.bar_label for row in auctions] == ["09:15", "14:57", "14:58"]
    assert all(row.status == "auction" for row in auctions)
    assert [(row.bar_label, row.phase) for row in auctions] == [
        ("09:15", "outside"),
        ("14:57", "auction_window"),
        ("14:58", "auction_window"),
    ]
    assert auctions[1].bar_time == "2026-10-20T14:57:00+08:00"
    assert all(
        (row.open, row.close, row.reference_median, row.reference_close)
        == (None, None, None, None)
        for row in auctions
    )
    assert all(row.bar_label not in result.rows_by_bar for row in auctions)
    assert result.labels_with_status("auction") == ("09:15", "14:57", "14:58")
    assert len(result.rows_by_bar) == 48
    with pytest.raises(TimelineError, match="not part of"):
        result.rows_for("14:57")


def test_off_grid_labels_are_reported_outside_and_never_enter_the_grid() -> None:
    base = load_5m(_SYM, _DAY).copy()
    extra = pd.DataFrame(
        {
            "open": [1421.0, 1421.0, 1421.0],
            "high": [1421.0, 1421.0, 1421.0],
            "low": [1421.0, 1421.0, 1421.0],
            "close": [1421.0, 1421.0, 1421.0],
            "volume": [120000.0, 120000.0, 120000.0],
        },
        # Deliberately not in label order: the accessor must canonicalise.
        index=pd.to_datetime(
            [f"{_DAY} 14:56", f"{_DAY} 09:31", f"{_DAY} 12:30"]
        ),
    )
    frame = pd.concat([extra, base])
    result = build_timeline(_manual(frame), _DAY)

    assert result.outside_labels() == ("09:31", "12:30", "14:56")
    outside = result.outside_rows()
    assert [row.status for row in outside] == ["outside", "outside", "outside"]
    assert [row.phase for row in outside] == ["outside", "outside", "outside"]
    assert all(row.open is None for row in outside)
    assert result.labels_with_status("outside") == ("09:31", "12:30", "14:56")
    assert tuple(result.rows_by_bar) == bar_clock.BAR_LABELS
    assert result.labels_with_status("present") == bar_clock.BAR_LABELS


def test_default_universe_includes_auction_only_and_omitted_symbols() -> None:
    base = _facts((_SYM,), _DAY)
    facts = replace(
        base,
        auction_by_symbol={_OTHER: {_DAY: ("14:57",)}},
        missing_symbols=(_ABSENT,),
    )
    result = build_timeline(facts, _DAY)

    assert result.symbols == (_OTHER, _ABSENT, _SYM)
    assert result.missing_by_symbol() == {
        _OTHER: bar_clock.BAR_LABELS,
        _ABSENT: bar_clock.BAR_LABELS,
    }
    assert result.row("09:35", _ABSENT).provider_omitted is True
    assert result.row("09:35", _OTHER).provider_omitted is False
    assert [(row.symbol, row.bar_label, row.status) for row in result.auction_rows()] == [
        (_OTHER, "14:57", "auction")
    ]
    assert len(result.rows_by_bar["09:35"]) == 3


# ── determinism and isolation ──────────────────────────────────────────────


def test_rebuild_is_equal_and_source_frame_mutation_cannot_leak_in() -> None:
    facts = _facts((_SYM,), _DAY)
    first = build_timeline(facts, _DAY)
    second = build_timeline(facts, _DAY)

    assert first == second
    assert first.row("09:35", _SYM) == second.row("09:35", _SYM)

    source = facts.bars_by_symbol[_SYM][_DAY]
    stamp = pd.Timestamp(f"{_DAY} 09:35")
    source.loc[stamp, "open"] = 999.0
    source.loc[stamp, "close"] = 999.0
    assert float(source.loc[stamp, "close"]) == 999.0  # mutation really happened

    assert first.row("09:35", _SYM) == second.row("09:35", _SYM)
    row = first.row("09:35", _SYM)
    assert (row.open, row.close, row.reference_median, row.reference_close) == (
        1421.0,
        1421.0,
        1421.0,
        1421.0,
    )
    assert first == second

    # A fresh build reads the mutated facts: the isolation above is a scalar
    # copy, not a cache that hides the source.
    assert build_timeline(facts, _DAY).row("09:35", _SYM).close == 999.0


# ── deterministic error ladder ────────────────────────────────────────────


def test_unknown_trade_date_is_a_deterministic_error() -> None:
    facts = _facts((_SYM,), _DAY)
    with pytest.raises(TimelineError, match="not present in the 5m facts"):
        build_timeline(facts, "2026-10-21")
    with pytest.raises(TimelineError, match="not present in the 5m facts"):
        build_timeline(facts, "2026-10-21", symbols=(_SYM,))


def test_day_frame_without_a_datetime_index_is_rejected() -> None:
    frame = continuous_bars(load_5m(_SYM, _DAY)).copy()
    frame.index = [f"row-{index}" for index in range(len(frame))]
    with pytest.raises(TimelineError, match="not indexed by datetime"):
        build_timeline(_manual(frame), _DAY)


def test_duplicate_label_collision_is_rejected() -> None:
    frame = continuous_bars(load_5m(_SYM, _DAY)).copy()
    duplicate = frame.iloc[[0]].copy()
    duplicate.index = duplicate.index + pd.Timedelta(seconds=30)  # still 09:35
    collided = pd.concat([frame, duplicate]).sort_index()

    with pytest.raises(
        TimelineError,
        match=r"duplicate bar label '09:35' for 600519\.SH on 2026-10-20",
    ):
        build_timeline(_manual(collided), _DAY)


def test_bars_symbol_outside_the_explicit_universe_is_rejected() -> None:
    with pytest.raises(TimelineError, match="outside the requested universe"):
        build_timeline(_two_symbol_facts(), _DAY, symbols=(_SYM,))
    with pytest.raises(TimelineError, match="at least one symbol"):
        build_timeline(_two_symbol_facts(), _DAY, symbols=())
    with pytest.raises(TimelineError, match="not a bare string"):
        build_timeline(_two_symbol_facts(), _DAY, symbols=_SYM)


def test_accessors_reject_labels_and_statuses_outside_the_vocabulary() -> None:
    result = build_timeline(_facts((_SYM,), _DAY), _DAY)
    with pytest.raises(TimelineError, match="no row for bar label"):
        result.row("09:35", _OTHER)
    with pytest.raises(TimelineError, match="unknown bar status"):
        result.labels_with_status("halted")
    with pytest.raises(TimelineError, match="not part of"):
        result.rows_for("13:00")


# ── three-symbol canonical order ───────────────────────────────────────────

_THIRD = "601318.SH"
_VALUE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "reference_median",
    "reference_close",
)


def _three_symbol_facts() -> FiveMinuteFacts:
    frame = continuous_bars(load_5m(_SYM, _DAY))
    symbols = (_SYM, _OTHER, _THIRD)
    return FiveMinuteFacts(
        bars_by_symbol={symbol: {_DAY: frame.copy()} for symbol in symbols},
        observed_days_by_symbol={symbol: (_DAY,) for symbol in symbols},
    )


def test_three_symbols_flatten_to_label_then_sorted_symbol_order() -> None:
    result = build_timeline(_three_symbol_facts(), _DAY)
    expected_symbols = tuple(sorted((_SYM, _OTHER, _THIRD)))

    assert result.symbols == expected_symbols
    assert len(result.rows_by_bar) == 48
    flattened = [
        (label, row.symbol)
        for label in result.bar_labels
        for row in result.rows_for(label)
    ]
    assert flattened == [
        (label, symbol) for label in bar_clock.BAR_LABELS for symbol in expected_symbols
    ]
    assert len(flattened) == 48 * 3
    assert all(row.status == "present" for label in bar_clock.BAR_LABELS for row in result.rows_for(label))


# ── non-present rows never fabricate a price ────────────────────────────────


def test_auction_and_outside_rows_leave_every_value_field_none() -> None:
    frame = continuous_bars(load_5m(_SYM, _DAY)).copy()
    extra = pd.DataFrame(
        {"open": [1421.0], "high": [1421.0], "low": [1421.0], "close": [1421.0], "volume": [120000.0]},
        index=pd.to_datetime([f"{_DAY} 09:31"]),
    )
    facts = replace(
        _manual(pd.concat([extra, frame])),
        auction_by_symbol={_SYM: {_DAY: ("14:57", "09:15")}},
    )
    result = build_timeline(facts, _DAY)

    rows = [*result.auction_rows(), *result.outside_rows()]
    assert [(row.bar_label, row.status) for row in rows] == [
        ("09:15", "auction"),
        ("14:57", "auction"),
        ("09:31", "outside"),
    ]
    assert [
        (row.bar_label, tuple(getattr(row, field) for field in _VALUE_FIELDS))
        for row in rows
    ] == [
        ("09:15", (None,) * 7),
        ("14:57", (None,) * 7),
        ("09:31", (None,) * 7),
    ]


def test_reference_prices_are_computed_from_the_four_prices_on_a_hand_built_bar() -> None:
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.0],
            "high": [20.0, 13.0],
            "low": [10.0, 9.0],
            "close": [10.0, 12.0],
            "volume": [100.0, 200.0],
        },
        index=pd.to_datetime([f"{_DAY} 09:35", f"{_DAY} 09:40"]),
    )
    result = build_timeline(_manual(frame), _DAY)

    first = result.row("09:35", _SYM)
    assert (first.open, first.high, first.low, first.close, first.volume) == (10.0, 20.0, 10.0, 10.0, 100.0)
    assert first.reference_median == 10.0  # median(10, 20, 10, 10); the mean would be 12.5
    assert first.reference_close == 10.0

    second = result.row("09:40", _SYM)
    assert (second.reference_median, second.reference_close) == (11.0, 12.0)  # median(10, 13, 9, 12)
    assert result.row("09:45", _SYM).status == "missing"


# ── Europe/Berlin tz-aware input is normalized, never misread ───────────────


class _StaticFrameLoader:
    """A loader that serves one caller-supplied frame for every requested code."""

    name = "static-fixture"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None):
        return {str(code): self.frame.copy() for code in codes}


def _require_berlin_tzdata() -> None:
    try:
        ZoneInfo("Europe/Berlin")
    except Exception:
        pytest.skip("Europe/Berlin tzdata is unavailable in this environment")


def _berlin_frame_for_shanghai_session() -> pd.DataFrame:
    """The fixture's Shanghai wall clock re-expressed as Europe/Berlin instants."""
    _require_berlin_tzdata()
    frame = continuous_bars(load_5m(_SYM, _DAY)).copy()
    frame.index = (
        pd.DatetimeIndex(frame.index)
        .tz_localize("Asia/Shanghai")
        .tz_convert("Europe/Berlin")
    )
    return frame


def test_europe_berlin_instants_are_normalized_back_to_shanghai_labels() -> None:
    """data_5m's documented canonicalizer converts a tz-aware index to Shanghai.

    The same instants seen from Berlin (03:35 local == 09:35 Shanghai) must
    reach the timeline as 09:35 with a ``+08:00`` stamp, not as a 03:35 label.
    """
    berlin = _berlin_frame_for_shanghai_session()
    assert berlin.index[0].isoformat() == "2026-10-20T03:35:00+02:00"

    warnings: list[str] = []
    facts = fetch_five_minute((_SYM,), _DAY, _DAY, loader=_StaticFrameLoader(berlin), warnings=warnings)
    result = build_timeline(facts, _DAY)

    assert facts.observed_days_by_symbol[_SYM] == (_DAY,)
    assert facts.bars_by_symbol[_SYM][_DAY].index[0].strftime("%H:%M") == "09:35"
    assert result.labels_with_status("present") == bar_clock.BAR_LABELS
    assert result.row("09:35", _SYM).bar_time == "2026-10-20T09:35:00+08:00"
    assert result.row("09:35", _SYM).close == 1421.0


def test_europe_berlin_wall_clock_is_not_silently_read_as_shanghai_labels() -> None:
    """A Berlin-localized 09:35 is 15:35 Shanghai, so no 09:35 bar exists."""
    _require_berlin_tzdata()
    frame = continuous_bars(load_5m(_SYM, _DAY)).copy()
    frame.index = pd.DatetimeIndex(frame.index).tz_localize("Europe/Berlin")
    assert frame.index[0].isoformat() == "2026-10-20T09:35:00+02:00"

    warnings: list[str] = []
    facts = fetch_five_minute((_SYM,), _DAY, _DAY, loader=_StaticFrameLoader(frame), warnings=warnings)
    result = build_timeline(facts, _DAY, symbols=(_SYM,))

    assert facts.bars_by_symbol == {}
    assert facts.observed_days_by_symbol[_SYM] == (_DAY,)
    assert facts.missing_symbols == ()
    assert result.row("09:35", _SYM).status == "missing"
    assert result.row("09:35", _SYM).provider_omitted is False
    assert result.labels_with_status("present") == ()
    assert result.outside_labels() == ()
    assert any(
        "48 bar label(s) outside the cn_a session windows" in warning
        and "first: 15:35" in warning
        for warning in warnings
    )
