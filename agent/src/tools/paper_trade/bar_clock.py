"""Deterministic cn_a 5-minute session bar clock (P1 intraday, GAP-4 / GAP-6).

The clock is the single source of the bar grid the pt-v1.3 per-bar engine
consumes.  It is PURE: no wall clock, no network, no environment, no cached
state — the caller always supplies the trade date, and every function is a
total function of its arguments.  That is what lets a replay produce the same
bars, in the same order, on any host.

Bar grid — 5-minute bars labelled by bar END (the P0 data convention; never
shift a label):

==========  ==============================================  =====
window      labels (step 5 min)                             count
==========  ==============================================  =====
morning     ``09:35`` .. ``11:30``                            24
lunch       no bar — ``11:30`` -> ``13:05`` is a 95 min gap    0
afternoon   ``13:05`` .. ``15:00``                            24
==========  ==============================================  =====

``(11:30 - 09:35) / 5 + 1 = 23 + 1 = 24`` for the morning and likewise 24 for
the afternoon, so ``len(BAR_LABELS) == 48``.  ``09:30`` (the opening-auction
print) and the ``14:57``-``15:00`` closing auction are NOT continuous bars and
never appear in :data:`BAR_LABELS`.

Phase table (:func:`classify`) — the four tokens of
:data:`schema_v13.SESSION_PHASES`, in decision order:

===================  =========================  ==========================
label                phase                      engine behaviour
===================  =========================  ==========================
``09:30``            ``opening_auction_print``  excluded, no event
``14:57``-``14:59``  ``auction_window``         excluded, emits an event
one of the 48 bars   ``continuous``             the only matchable phase
anything else        ``outside``                excluded, emits an event
===================  =========================  ==========================

Boundary details:

* ``09:30`` is the single opening-auction print (it equals the daily open).
* The closing-auction window is half-open at the end, ``14:57 <= t < 15:00``:
  ``14:57``/``14:58``/``14:59`` are ``auction_window``, while ``15:00`` is the
  LAST CONTINUOUS BAR of the session — the auction *window* label is 14:57,
  not 15:00.
* ``continuous`` means exactly "one of the 48 bar labels": a non-grid label
  such as ``09:31``, ``14:56`` or ``11:35`` is ``outside`` and is never
  silently promoted to ``continuous``.  This is deliberately stricter than
  :func:`calendar.classify_cn_a_session_label`, which answers the different
  question "is this instant inside a continuous session *window*?"; the engine
  gates matching on THIS grid, so a stray off-grid row can never match.
* Everything else that is a valid ``HH:MM`` (pre-open, lunch, post-close) is
  ``outside``; a non-``HH:MM`` value raises :class:`ValueError` naming the
  argument.

Timezone: Asia/Shanghai is UTC+08:00 all year (no daylight saving), so a fixed
:class:`datetime.timezone` offset is exact — no tz-database lookup and no host
dependency.  Every datetime this module returns is aware and renders
``+08:00``.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from .schema_v13 import SESSION_PHASES

__all__ = [
    "BAR_LABELS",
    "CLOSING_AUCTION_WINDOW",
    "OPENING_AUCTION_PRINT_LABEL",
    "SESSION_AUCTION_WINDOW",
    "SESSION_CONTINUOUS",
    "SESSION_OPENING_AUCTION_PRINT",
    "SESSION_OUTSIDE",
    "SHANGHAI_OFFSET",
    "SHANGHAI_TZ",
    "SessionPhase",
    "bar_time",
    "classify",
    "is_first_bar",
    "is_last_bar",
    "next_bar",
    "previous_bar",
    "session_bounds",
]

#: Asia/Shanghai is UTC+08:00 year-round (no DST), so the fixed offset is exact.
SHANGHAI_OFFSET = dt.timedelta(hours=8)
SHANGHAI_TZ = dt.timezone(SHANGHAI_OFFSET, "Asia/Shanghai")

_BAR_MINUTES = 5
_MORNING_START = 9 * 60 + 35  # 09:35
_MORNING_END = 11 * 60 + 30  # 11:30
_AFTERNOON_START = 13 * 60 + 5  # 13:05
_AFTERNOON_END = 15 * 60  # 15:00


def _grid(start: int, end: int) -> tuple[str, ...]:
    """Every ``_BAR_MINUTES`` bar-END label in ``[start, end]`` minutes of day."""
    return tuple(
        f"{minute // 60:02d}:{minute % 60:02d}"
        for minute in range(start, end + 1, _BAR_MINUTES)
    )


#: The 48 continuous bar labels, in session order (morning block, afternoon block).
BAR_LABELS: tuple[str, ...] = _grid(_MORNING_START, _MORNING_END) + _grid(
    _AFTERNOON_START, _AFTERNOON_END
)

_BAR_INDEX: dict[str, int] = {label: index for index, label in enumerate(BAR_LABELS)}
_BAR_LABEL_SET = frozenset(BAR_LABELS)

#: The single opening call-auction print: excluded from matching, no event.
OPENING_AUCTION_PRINT_LABEL = "09:30"

#: Closing call-auction window, half-open at the end: ``14:57 <= t < 15:00``.
CLOSING_AUCTION_WINDOW = ("14:57", "15:00")

#: Phase token type.  The vocabulary is owned by :mod:`schema_v13`; deriving
#: the Literal from its tuple makes a drift between the two a static error
#: instead of a new, silently accepted fifth phase.
SessionPhase = Literal[*SESSION_PHASES]

SESSION_CONTINUOUS: SessionPhase = "continuous"
SESSION_OPENING_AUCTION_PRINT: SessionPhase = "opening_auction_print"
SESSION_AUCTION_WINDOW: SessionPhase = "auction_window"
SESSION_OUTSIDE: SessionPhase = "outside"

_LABEL_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _require_label(label: str) -> str:
    """Return ``label`` if it is a strict ``HH:MM`` string, else raise."""
    if not isinstance(label, str) or _LABEL_RE.fullmatch(label) is None:
        raise ValueError(f"label must be an HH:MM string, got {label!r}")
    return label


def _require_trade_date(trade_date: str) -> dt.date:
    """Return ``trade_date`` as a date if it is a ``YYYY-MM-DD`` calendar day."""
    if not isinstance(trade_date, str) or _DATE_RE.fullmatch(trade_date) is None:
        raise ValueError(
            f"trade_date must be a YYYY-MM-DD string, got {trade_date!r}"
        )
    try:
        return dt.date.fromisoformat(trade_date)
    except ValueError:
        raise ValueError(
            f"trade_date must be a real calendar day, got {trade_date!r}"
        ) from None


def _bar_index(label: str) -> int:
    """Position of ``label`` in :data:`BAR_LABELS`; ``ValueError`` if off-grid."""
    _require_label(label)
    index = _BAR_INDEX.get(label)
    if index is None:
        raise ValueError(f"label {label!r} is not one of the 48 continuous session bars")
    return index


def bar_time(trade_date: str, label: str) -> dt.datetime:
    """Return the aware ``+08:00`` instant of ``label`` on ``trade_date``.

    The constructor is grid-agnostic on purpose: it validates the ``HH:MM``
    grammar and the calendar date, then returns exactly that instant — grid
    membership is :func:`next_bar` / :func:`previous_bar`'s job and the phase
    is :func:`classify`'s.  A brand-new immutable ``datetime`` is built on
    every call, so no caller can observe or mutate shared state.

    ``bar_time("2026-10-20", "09:35").isoformat()`` is
    ``"2026-10-20T09:35:00+08:00"``.  A malformed date or label raises
    :class:`ValueError` naming the argument.
    """
    day = _require_trade_date(trade_date)
    hour_text, minute_text = _require_label(label).split(":")
    return dt.datetime(
        day.year,
        day.month,
        day.day,
        int(hour_text),
        int(minute_text),
        tzinfo=SHANGHAI_TZ,
    )


def classify(label: str) -> SessionPhase:
    """Return the session phase of one ``HH:MM`` bar label.

    See the module docstring for the full boundary table.  Raises
    :class:`ValueError` for anything that is not a strict ``HH:MM`` string.
    """
    _require_label(label)
    if label == OPENING_AUCTION_PRINT_LABEL:
        return SESSION_OPENING_AUCTION_PRINT
    if CLOSING_AUCTION_WINDOW[0] <= label < CLOSING_AUCTION_WINDOW[1]:
        return SESSION_AUCTION_WINDOW
    if label in _BAR_LABEL_SET:
        return SESSION_CONTINUOUS
    return SESSION_OUTSIDE


def next_bar(label: str) -> str | None:
    """The next continuous bar in session order, or ``None`` after ``15:00``.

    The lunch break holds no bar but the session continues, so
    ``next_bar("11:30") == "13:05"``.  A label outside :data:`BAR_LABELS`
    raises :class:`ValueError`.
    """
    index = _bar_index(label)
    return BAR_LABELS[index + 1] if index + 1 < len(BAR_LABELS) else None


def previous_bar(label: str) -> str | None:
    """The previous continuous bar, or ``None`` before ``09:35``.

    ``previous_bar("13:05") == "11:30"``.  A label outside :data:`BAR_LABELS`
    raises :class:`ValueError`.
    """
    index = _bar_index(label)
    return BAR_LABELS[index - 1] if index > 0 else None


def session_bounds(trade_date: str) -> tuple[dt.datetime, dt.datetime]:
    """The ``(first_bar, last_bar)`` instants of the session on ``trade_date``."""
    return bar_time(trade_date, BAR_LABELS[0]), bar_time(trade_date, BAR_LABELS[-1])


def is_first_bar(label: str) -> bool:
    """Whether ``label`` is the first bar of the session (``09:35``)."""
    return _bar_index(label) == 0


def is_last_bar(label: str) -> bool:
    """Whether ``label`` is the last bar of the session (``15:00``)."""
    return _bar_index(label) == len(BAR_LABELS) - 1
