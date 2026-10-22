"""Session-phase gate and deterministic session events (P1, GAP-6).

The per-bar engine may only price and match an order on a **continuous** bar.
This module is the single gate for that rule and the translation from
:mod:`~.timeline` rows to the frozen :class:`~.schema.SessionEvent` vocabulary.
It is PURE: no clock, no I/O, no cache, no environment.

Matching gate (:func:`is_matchable` / :func:`require_matchable`) — fail closed:
``True`` only for the literal ``"continuous"`` phase.  ``""`` (unclassified),
``"opening_auction_print"``, ``"auction_window"``, ``"outside"`` and any
unknown token are all non-matchable, never promoted to continuous.

Phase -> event table (:func:`phase_events`).  ``BarRow.status`` selects the
event; ``phase`` guards the one silent row:

===============  ==================  ==========================================
row status       row phase            emitted event
===============  ==================  ==========================================
``auction``      opening print       none — ``09:30`` is expected and silent
``auction``      anything else       ``auction_window`` / ``"auction window at L"``
``outside``      opening print       none (defensive: cannot occur on the grid)
``outside``      anything else       ``auction_window`` / ``"outside session at L"``
``present``      any                 none
``missing``      any                 none
===============  ==================  ==========================================

The closing-auction window (``14:57``-``14:59``) and an off-grid row
(``11:35``, ``15:05``, ...) share the single ``auction_window`` event token
because :data:`~.schema.SESSION_EVENT_TYPES` is a frozen four-value vocabulary
(``non_trading_day``, ``halted``, ``price_limit``, ``auction_window``).  The
distinction is carried by ``detail``, whose strings are deterministic and
pinned by tests — no new event type is ever introduced here.

:func:`auction_only_halted` reports a symbol that traded no continuous bar but
has auction rows (one ``halted`` event per symbol).

Per-bar events (:func:`bar_events`, T18).  One call describes ONE label, using
:class:`SessionEventContext` for the session-static facts:

=================  ==========================================================
condition          event
=================  ==========================================================
closed day         ONE ``non_trading_day`` (symbol ``""``); no halted/price_limit
auction/outside    the :func:`phase_events` rows for that label, unchanged
missing + peer     ``halted`` / ``"halted at L"`` per missing symbol, ONLY when
                   at least one other symbol has a ``present`` row at that L
present at band    ``price_limit`` / ``"limit_up at L"`` or ``"limit_down at L"``
                   (per-symbol band; side carried by ``detail``)
``15:00``          the day-level :func:`auction_only_halted` summary is added;
                   :func:`order_events` collapses it with a per-bar halted for
                   the same ``(event_type, date, symbol, label)``
=================  ==========================================================

A whole-bar gap (no ``present`` row at all) is never a per-symbol halt, and a
missing row never emits ``price_limit`` — it has no reference price.  Bands are
the day's ``symbol -> (lower, upper)`` table; an absent entry is UNKNOWN and the
check is skipped (it is never widened to ``±inf``).  Every row is inside the
frozen four-value vocabulary, so the checks are exhaustive by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from . import bar_clock
from .schema import PATH_MODELS, SessionEvent
from .timeline import (
    STATUS_AUCTION,
    STATUS_MISSING,
    STATUS_OUTSIDE,
    STATUS_PRESENT,
    BarRow,
    BarTimeline,
)

__all__ = [
    "SessionEventContext",
    "PhaseGateError",
    "auction_only_halted",
    "bar_events",
    "is_matchable",
    "matching_bar_labels",
    "order_events",
    "phase_events",
    "require_matchable",
]

#: ``events[].event_type`` values used here; every one is a member of the
#: frozen :data:`~.schema.SESSION_EVENT_TYPES` vocabulary.
AUCTION_WINDOW_EVENT = "auction_window"
HALTED_EVENT = "halted"
NON_TRADING_DAY_EVENT = "non_trading_day"
PRICE_LIMIT_EVENT = "price_limit"


class PhaseGateError(ValueError):
    """Raised when a non-continuous phase reaches the matching gate."""


def is_matchable(phase: str) -> bool:
    """Whether ``phase`` may be priced/matched; only ``"continuous"`` may.

    Fail closed: an empty string, an auction phase, ``"outside"`` or any
    unknown token returns ``False`` — there is no default-to-continuous path.
    """
    return phase == bar_clock.SESSION_CONTINUOUS


def require_matchable(phase: str, *, label: str = "") -> None:
    """Raise :class:`PhaseGateError` unless ``phase`` is ``"continuous"``.

    The engine calls this BEFORE pricing or matching any order on a bar.  The
    message names both the phase and, when supplied, the bar label, so a
    rejected auction/outside/unknown bar is traceable without extra context.
    """
    if is_matchable(phase):
        return
    where = f" for bar {label}" if label else ""
    raise PhaseGateError(
        f"session phase {phase!r}{where} is not matchable; "
        "only 'continuous' bars may be priced or matched"
    )


def order_events(labeled_events: Iterable[tuple[str, SessionEvent]]) -> tuple[SessionEvent, ...]:
    """Sort ``(bar_label, event)`` pairs and drop duplicate events.

    Order is ``(trade_date, bar label, symbol)``; the label travels beside the
    event because the frozen :class:`SessionEvent` row has no label field.
    A duplicate is the same ``(event_type, trade_date, symbol, bar_label)``;
    the lexicographically smallest ``detail`` wins, so the result never depends
    on the caller's input order.  T18 reuses this for its per-bar events.
    """
    ordered = sorted(
        labeled_events,
        key=lambda pair: (
            pair[1].trade_date,
            pair[0],
            pair[1].symbol,
            pair[1].event_type,
            pair[1].detail,
        ),
    )
    seen: set[tuple[str, str, str, str]] = set()
    events: list[SessionEvent] = []
    for label, event in ordered:
        key = (event.event_type, event.trade_date, event.symbol, label)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    return tuple(events)


def phase_events(rows: Sequence[BarRow]) -> tuple[SessionEvent, ...]:
    """Translate timeline rows into deterministic ``auction_window`` events.

    One event per ``(trade_date, symbol, label)``; the opening-auction print
    (``09:30``) is silent, and ``present``/``missing`` rows emit nothing.  See
    the module table for the status -> event mapping and for why ``outside``
    rows also use the ``auction_window`` token.
    """
    labeled: list[tuple[str, SessionEvent]] = []
    for row in rows:
        if row.status not in (STATUS_AUCTION, STATUS_OUTSIDE):
            continue
        if row.phase == bar_clock.SESSION_OPENING_AUCTION_PRINT:
            # The 09:30 print is expected: never an event.
            continue
        detail = (
            f"auction window at {row.bar_label}"
            if row.status == STATUS_AUCTION
            else f"outside session at {row.bar_label}"
        )
        labeled.append((
            row.bar_label,
            SessionEvent(
                trade_date=row.trade_date,
                symbol=row.symbol,
                event_type=AUCTION_WINDOW_EVENT,
                detail=detail,
            ),
        ))
    return order_events(labeled)


def auction_only_halted(timeline: BarTimeline) -> tuple[SessionEvent, ...]:
    """One ``halted`` event per symbol that has auction rows but no present bar.

    "Auction only" means the symbol has at least one :meth:`BarTimeline.auction_rows`
    entry on the day and ZERO ``present`` rows in ``rows_by_bar``.  The detail
    pins both ranges (the symbol's auction labels and the continuous session
    grid) so the report is self-describing.  T18's per-bar halted rule is a
    separate, finer-grained layer that reuses this module's ordering helper.
    """
    auction_labels: dict[str, list[str]] = {}
    for row in timeline.auction_rows():
        auction_labels.setdefault(row.symbol, []).append(row.bar_label)

    present: set[str] = set()
    for rows in timeline.rows_by_bar.values():
        present.update(row.symbol for row in rows if row.status == STATUS_PRESENT)

    grid = f"{timeline.bar_labels[0]}-{timeline.bar_labels[-1]}" if timeline.bar_labels else "the session"
    events = []
    for symbol in sorted(auction_labels):
        if symbol in present:
            continue
        labels = sorted(auction_labels[symbol])
        events.append(SessionEvent(
            trade_date=timeline.trade_date,
            symbol=symbol,
            event_type=HALTED_EVENT,
            detail=f"auction only at {labels[0]}-{labels[-1]}; no present continuous bar in {grid}",
        ))
    return tuple(events)


@dataclass(frozen=True, slots=True)
class SessionEventContext:
    """Session-static facts :func:`bar_events` needs; built once, reused per bar.

    ``trading_day`` is the calendar truth (a closed day emits only
    ``non_trading_day``), ``bands`` is the day's ``symbol -> (lower, upper)``
    cn_a band table (a missing entry means UNKNOWN and is skipped), and
    ``path_model`` selects which bar reference the band comparison reads — the
    same choice :func:`~.fill_rules.fill_decision` and the rejection ladder
    make, so an event and a fill never price off different fields.

    The band table is copied at construction: a caller mutating its dict later
    cannot change an already-built event stream.
    """

    trading_day: bool = True
    bands: Mapping[str, tuple[float, float]] | None = None
    path_model: str = "bar_median"

    def __post_init__(self) -> None:
        if not isinstance(self.trading_day, bool):
            raise ValueError(
                f"trading_day must be a bool, got {type(self.trading_day).__name__}"
            )
        if self.path_model not in PATH_MODELS:
            raise ValueError(
                f"path_model must be one of {PATH_MODELS}, got {self.path_model!r}"
            )
        if self.bands is not None:
            if not isinstance(self.bands, Mapping):
                raise ValueError(
                    f"bands must be a mapping or None, got {type(self.bands).__name__}"
                )
            object.__setattr__(self, "bands", MappingProxyType(dict(self.bands)))


def bar_events(
    timeline: BarTimeline,
    label: str,
    *,
    context: SessionEventContext | None = None,
) -> tuple[SessionEvent, ...]:
    """Every session event for ONE bar ``label``, deterministically ordered.

    ``context`` defaults to an open session with no band table, so a caller
    that does not inject one gets phase events plus the same-bar halt rule.
    The emitted rows are sorted and deduped by :func:`order_events` on
    ``(trade_date, label, symbol, event_type, detail)``; a repeated row (or the
    15:00 per-bar halted colliding with the day-level summary) collapses to the
    lexicographically smallest detail.  Pure: no clock, I/O or cache.
    """
    if context is None:
        context = SessionEventContext()
    if not isinstance(context, SessionEventContext):
        raise ValueError(
            f"context must be a SessionEventContext, got {type(context).__name__}"
        )

    is_grid_label = bar_clock.classify(label) == bar_clock.SESSION_CONTINUOUS
    phase_rows = tuple(
        row
        for row in (*timeline.auction_rows(), *timeline.outside_rows())
        if row.bar_label == label
    )
    labeled: list[tuple[str, SessionEvent]] = [
        (label, event) for event in phase_events(phase_rows)
    ]

    if not context.trading_day:
        labeled.append((label, SessionEvent(
            trade_date=timeline.trade_date,
            symbol="",
            event_type=NON_TRADING_DAY_EVENT,
            detail=f"non_trading_day at {timeline.trade_date}",
        )))
    else:
        rows = timeline.rows_for(label) if is_grid_label else ()
        labeled += [(label, event) for event in _halted_events(rows, label)]
        labeled += [(label, event) for event in _price_limit_events(rows, label, context)]
        if is_grid_label and bar_clock.is_last_bar(label):
            # Day-level diagnostic, emitted once on the closing bar.  Its label
            # is the closing bar, so a per-bar halted for the same symbol
            # collapses with it through ``order_events`` below.
            labeled += [(label, event) for event in auction_only_halted(timeline)]

    return order_events(labeled)


def _halted_events(rows: Sequence[BarRow], label: str) -> tuple[SessionEvent, ...]:
    """``halted`` per missing symbol, only while another symbol is present.

    A whole-bar gap (no ``present`` row at this label) is a data/session fact,
    not a per-symbol halt, so it emits nothing here; an all-day halt is covered
    by :func:`auction_only_halted`.
    """
    present = {row.symbol for row in rows if row.status == STATUS_PRESENT}
    if not present:
        return ()
    return tuple(
        SessionEvent(
            trade_date=row.trade_date,
            symbol=row.symbol,
            event_type=HALTED_EVENT,
            detail=f"halted at {label}",
        )
        for row in rows
        if row.status == STATUS_MISSING
    )


def _price_limit_events(
    rows: Sequence[BarRow], label: str, context: SessionEventContext
) -> tuple[SessionEvent, ...]:
    """``price_limit`` per present symbol whose reference sits at its band.

    The side lives in ``detail`` (one event per symbol/label); a missing row has
    no reference and a symbol with no band entry is UNKNOWN, so both skip.
    """
    bands = context.bands or {}
    events: list[SessionEvent] = []
    for row in rows:
        if row.status != STATUS_PRESENT:
            continue
        band = bands.get(row.symbol)
        if band is None:
            continue
        reference = (
            row.reference_close
            if context.path_model == "bar_close"
            else row.reference_median
        )
        if reference is None:
            continue
        lower, upper = band
        if reference >= upper:
            detail = f"limit_up at {label}"
        elif reference <= lower:
            detail = f"limit_down at {label}"
        else:
            continue
        events.append(SessionEvent(
            trade_date=row.trade_date,
            symbol=row.symbol,
            event_type=PRICE_LIMIT_EVENT,
            detail=detail,
        ))
    return tuple(events)


def matching_bar_labels(timeline: BarTimeline) -> tuple[str, ...]:
    """Every label in ``timeline`` whose phase is continuous, in label order.

    The candidate set includes the grid, auction rows and outside rows, so a
    test (or the engine) can prove an off-grid auction label never reaches
    ``match_order``: it is classified here and excluded.  A malformed label
    raises through :func:`bar_clock.classify` rather than being skipped.
    """
    labels = set(timeline.rows_by_bar)
    labels.update(row.bar_label for row in timeline.auction_rows())
    labels.update(row.bar_label for row in timeline.outside_rows())
    return tuple(
        label
        for label in sorted(labels)
        if bar_clock.classify(label) == bar_clock.SESSION_CONTINUOUS
    )
