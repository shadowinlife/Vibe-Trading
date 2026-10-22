"""T7 gate — deterministic cn_a 5-minute session bar clock.

Scope: ``bar_clock`` in isolation — the 48-label grid, aware ``+08:00``
timestamps, the phase table, grid navigation and the purity contract.  The
per-bar timeline / request-contract coverage belongs to the todo-10 suite;
nothing here reads a fixture or exercises the matching engine.

Every assertion is a clock fact, so a regression (a shifted label, a naive
datetime, an auction label promoted to ``continuous``) fails here even when
the rest of the P1 suite is still green.
"""

from __future__ import annotations

import ast
import datetime as dt
import os
import re
import time
from pathlib import Path
from typing import Iterator

import pytest

from src.tools.paper_trade import bar_clock
from src.tools.paper_trade.schema_v13 import SESSION_PHASES

CLOCK_SOURCE = Path(bar_clock.__file__).read_text(encoding="utf-8")

_ZZ_LABEL_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: Labels that are well formed ``HH:MM`` but are not a bar or an auction row.
_MALFORMED_LABELS = (
    "9:35",
    "09:35:00",
    "25:00",
    "09:60",
    "0935",
    "",
    " 09:35",
    "09:35\n",
)

#: Phase table, in decision order.  ``09:31`` / ``14:56`` / ``11:31`` are the
#: grid-strictness probes: inside a continuous window but NOT on the 5-minute
#: grid, so they must never be promoted to ``continuous``.
_PHASE_TABLE = (
    ("09:00", "outside"),
    ("09:15", "outside"),
    ("09:25", "outside"),
    ("09:30", "opening_auction_print"),
    ("09:31", "outside"),
    ("09:35", "continuous"),
    ("10:00", "continuous"),
    ("11:30", "continuous"),
    ("11:31", "outside"),
    ("11:35", "outside"),
    ("12:30", "outside"),
    ("13:00", "outside"),
    ("13:05", "continuous"),
    ("14:55", "continuous"),
    ("14:56", "outside"),
    ("14:57", "auction_window"),
    ("14:58", "auction_window"),
    ("14:59", "auction_window"),
    ("15:00", "continuous"),
    ("15:05", "outside"),
)


def _minutes_of_day(label: str) -> int:
    hour, minute = label.split(":")
    return int(hour) * 60 + int(minute)


# ── the grid ────────────────────────────────────────────────────────────────


def test_bar_labels_are_the_48_continuous_bars_in_session_order() -> None:
    labels = bar_clock.BAR_LABELS

    assert isinstance(labels, tuple)
    assert len(labels) == 48
    assert len(set(labels)) == 48
    assert all(_ZZ_LABEL_RE.fullmatch(label) for label in labels)

    # 09:35..11:30 = 24 labels, 13:05..15:00 = 24 labels, morning first.
    assert labels[0] == "09:35"
    assert labels[23] == "11:30"
    assert labels[24] == "13:05"
    assert labels[-1] == "15:00"
    assert labels == tuple(sorted(labels))


def test_bar_labels_step_five_minutes_within_each_window_and_jump_at_lunch() -> None:
    morning = bar_clock.BAR_LABELS[:24]
    afternoon = bar_clock.BAR_LABELS[24:]

    for window in (morning, afternoon):
        steps = [
            _minutes_of_day(later) - _minutes_of_day(earlier)
            for earlier, later in zip(window, window[1:])
        ]
        assert steps == [5] * 23

    # The lunch break is a discontinuity, not a bar: 11:30 -> 13:05 is 95 min.
    assert _minutes_of_day(afternoon[0]) - _minutes_of_day(morning[-1]) == 95


def test_bar_labels_exclude_the_opening_print_and_the_auction_window() -> None:
    for auction_label in ("09:30", "14:57", "14:58", "14:59"):
        assert auction_label not in bar_clock.BAR_LABELS


# ── bar_time ────────────────────────────────────────────────────────────────


def test_bar_time_returns_an_aware_plus_08_instant() -> None:
    moment = bar_clock.bar_time("2026-10-20", "09:35")

    assert moment.isoformat() == "2026-10-20T09:35:00+08:00"
    assert moment.tzinfo is not None
    assert moment.utcoffset() == dt.timedelta(hours=8)
    assert (moment.hour, moment.minute, moment.second, moment.microsecond) == (9, 35, 0, 0)


def test_bar_time_returns_a_new_immutable_object_on_every_call() -> None:
    first = bar_clock.bar_time("2026-10-20", "10:00")
    second = bar_clock.bar_time("2026-10-20", "10:00")

    assert first == second
    assert first is not second


@pytest.mark.parametrize(
    "bad_date",
    ("2026-10-2", "20261020", "2026-13-01", "2026-02-30", "2026/10/20", ""),
)
def test_bar_time_rejects_a_malformed_trade_date(bad_date: str) -> None:
    with pytest.raises(ValueError, match="trade_date"):
        bar_clock.bar_time(bad_date, "09:35")


def test_bar_time_rejects_a_non_string_trade_date() -> None:
    with pytest.raises(ValueError, match="trade_date"):
        bar_clock.bar_time(20261020, "09:35")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_label", _MALFORMED_LABELS)
def test_bar_time_rejects_a_malformed_label(bad_label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        bar_clock.bar_time("2026-10-20", bad_label)


def test_bar_time_rejects_a_non_string_label() -> None:
    with pytest.raises(ValueError, match="label"):
        bar_clock.bar_time("2026-10-20", 935)  # type: ignore[arg-type]


# ── classify ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("label", "phase"), _PHASE_TABLE)
def test_classify_maps_the_session_boundaries(label: str, phase: str) -> None:
    assert bar_clock.classify(label) == phase


def test_classify_returns_continuous_for_exactly_the_48_bars() -> None:
    assert {bar_clock.classify(label) for label in bar_clock.BAR_LABELS} == {"continuous"}


def test_classify_only_returns_schema_v13_phase_tokens() -> None:
    probed = (
        *bar_clock.BAR_LABELS,
        "09:30",
        "14:57",
        "14:58",
        "14:59",
        "09:00",
        "14:56",
        "15:05",
    )

    assert {bar_clock.classify(label) for label in probed} <= set(SESSION_PHASES)


@pytest.mark.parametrize("bad_label", _MALFORMED_LABELS)
def test_classify_rejects_a_malformed_label(bad_label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        bar_clock.classify(bad_label)


def test_classify_rejects_a_non_string_label() -> None:
    with pytest.raises(ValueError, match="label"):
        bar_clock.classify(None)  # type: ignore[arg-type]


# ── grid navigation ─────────────────────────────────────────────────────────


def test_next_bar_crosses_lunch_and_stops_after_the_close() -> None:
    assert bar_clock.next_bar("09:35") == "09:40"
    assert bar_clock.next_bar("11:25") == "11:30"
    assert bar_clock.next_bar("11:30") == "13:05"
    assert bar_clock.next_bar("14:55") == "15:00"
    assert bar_clock.next_bar("15:00") is None


def test_previous_bar_crosses_lunch_and_stops_at_the_open() -> None:
    assert bar_clock.previous_bar("09:35") is None
    assert bar_clock.previous_bar("09:40") == "09:35"
    assert bar_clock.previous_bar("13:05") == "11:30"
    assert bar_clock.previous_bar("15:00") == "14:55"


@pytest.mark.parametrize("off_grid", ("09:30", "14:57", "11:35", "25:00"))
def test_grid_navigation_rejects_a_label_outside_the_48(off_grid: str) -> None:
    with pytest.raises(ValueError, match="label"):
        bar_clock.next_bar(off_grid)
    with pytest.raises(ValueError, match="label"):
        bar_clock.previous_bar(off_grid)
    with pytest.raises(ValueError, match="label"):
        bar_clock.is_first_bar(off_grid)
    with pytest.raises(ValueError, match="label"):
        bar_clock.is_last_bar(off_grid)


def test_first_and_last_bar_predicates() -> None:
    assert bar_clock.is_first_bar("09:35") is True
    assert bar_clock.is_first_bar("13:05") is False
    assert bar_clock.is_last_bar("15:00") is True
    assert bar_clock.is_last_bar("11:30") is False


# ── session bounds ──────────────────────────────────────────────────────────


def test_session_bounds_span_the_first_and_last_bar() -> None:
    start, end = bar_clock.session_bounds("2026-10-20")

    assert start.isoformat() == "2026-10-20T09:35:00+08:00"
    assert end.isoformat() == "2026-10-20T15:00:00+08:00"
    assert start < end
    assert start.utcoffset() == end.utcoffset() == dt.timedelta(hours=8)


def test_session_bounds_reject_a_malformed_trade_date() -> None:
    with pytest.raises(ValueError, match="trade_date"):
        bar_clock.session_bounds("20-10-2026")


def test_session_instants_are_aware_and_strictly_increasing() -> None:
    instants = [bar_clock.bar_time("2026-10-20", label) for label in bar_clock.BAR_LABELS]

    assert all(moment.tzinfo is not None for moment in instants)
    assert all(later > earlier for earlier, later in zip(instants, instants[1:]))


# ── purity ──────────────────────────────────────────────────────────────────


def test_module_imports_only_stdlib_datetime_re_and_the_phase_vocabulary() -> None:
    tree = ast.parse(CLOCK_SOURCE)
    absolute_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            absolute_imports.add(node.module.split(".")[0])

    assert absolute_imports <= {"__future__", "datetime", "re", "typing"}


def test_module_never_reads_a_wall_clock() -> None:
    tree = ast.parse(CLOCK_SOURCE)
    clock_attributes = {"now", "utcnow", "today"}
    offenders = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in clock_attributes
    ]

    # ``time.time`` is the other wall-clock door; the text form also catches an
    # aliased receiver that the AST walk cannot resolve statically.
    assert offenders == []
    assert "time.time(" not in CLOCK_SOURCE


def test_module_imports_are_exactly_the_pure_clock_allowlist() -> None:
    """The exact import set, not merely a subset: no stdlib clock can sneak in."""
    tree = ast.parse(CLOCK_SOURCE)
    absolute: set[str] = set()
    relative: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if node.level:
                relative.append((node.level, module))
            elif module:
                absolute.add(module)

    assert absolute == {"__future__", "datetime", "re", "typing"}
    assert relative == [(1, "schema_v13")]


# ── host timezone independence ──────────────────────────────────────────────


@pytest.fixture
def berlin_host_timezone() -> Iterator[None]:
    """Move the HOST timezone to Europe/Berlin for one test, then restore it.

    ``bar_clock`` must be a pure function of its arguments, so a host whose
    local clock is UTC+01:00 (January) must observe exactly the same ``+08:00``
    instants as a UTC host.  ``time.tzset`` is POSIX-only; Windows has no way to
    simulate a second host in-process, so the test is skipped there rather than
    silently asserting nothing.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is unavailable on this platform; cannot simulate a host timezone")

    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def test_host_timezone_cannot_change_any_clock_value(berlin_host_timezone: None) -> None:
    # Sanity-check the fixture itself: the host really is UTC+01:00 in January.
    assert time.strftime("%z", time.localtime(0)) == "+0100"

    assert [bar_clock.bar_time("2026-10-20", label).isoformat() for label in bar_clock.BAR_LABELS] == [
        f"2026-10-20T{label}:00+08:00" for label in bar_clock.BAR_LABELS
    ]
    assert [bar_clock.bar_time("2026-10-20", label).utcoffset() for label in bar_clock.BAR_LABELS] == [
        dt.timedelta(hours=8)
    ] * len(bar_clock.BAR_LABELS)

    start, end = bar_clock.session_bounds("2026-10-20")
    assert (start.isoformat(), end.isoformat()) == (
        "2026-10-20T09:35:00+08:00",
        "2026-10-20T15:00:00+08:00",
    )


# ── every off-grid boundary neighbour ───────────────────────────────────────


@pytest.mark.parametrize("label", ("09:29", "12:59", "15:01"))
def test_off_grid_minute_neighbours_of_the_boundaries_are_outside(label: str) -> None:
    """The three boundary probes absent from ``_PHASE_TABLE`` above.

    ``bar_time`` still constructs the instant (it is a constructor, not a gate)
    while ``classify`` refuses to promote it to ``continuous``.
    """
    assert bar_clock.classify(label) == "outside"
    assert label not in bar_clock.BAR_LABELS
    assert bar_clock.bar_time("2026-10-20", label).isoformat() == f"2026-10-20T{label}:00+08:00"


# ── exhaustive grid navigation ──────────────────────────────────────────────


def test_next_bar_matches_the_canonical_successor_for_all_47_links() -> None:
    assert [bar_clock.next_bar(label) for label in bar_clock.BAR_LABELS[:-1]] == list(
        bar_clock.BAR_LABELS[1:]
    )
    assert bar_clock.next_bar(bar_clock.BAR_LABELS[-1]) is None


def test_previous_bar_matches_the_canonical_predecessor_for_all_47_links() -> None:
    assert [bar_clock.previous_bar(label) for label in bar_clock.BAR_LABELS[1:]] == list(
        bar_clock.BAR_LABELS[:-1]
    )
    assert bar_clock.previous_bar(bar_clock.BAR_LABELS[0]) is None


def test_next_and_previous_are_inverse_and_the_ends_are_singular() -> None:
    for label in bar_clock.BAR_LABELS[1:-1]:
        successor = bar_clock.next_bar(label)
        assert successor is not None
        assert bar_clock.previous_bar(successor) == label

    assert (bar_clock.next_bar("11:30"), bar_clock.previous_bar("13:05")) == ("13:05", "11:30")

    assert [label for label in bar_clock.BAR_LABELS if bar_clock.is_first_bar(label)] == ["09:35"]
    assert [label for label in bar_clock.BAR_LABELS if bar_clock.is_last_bar(label)] == ["15:00"]
