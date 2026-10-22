"""Ordered per-bar timeline assembled from :class:`FiveMinuteFacts` (P1, GAP-1/GAP-6).

One row per ``(bar_label, symbol)``, grouped by the 48 canonical bar-END labels
of :mod:`bar_clock` in :data:`bar_clock.BAR_LABELS` order, symbols sorted.  The
builder only reads the facts and copies scalars -- no I/O, refetch, aggregation,
reordering, mutation or pandas object escapes.  Same facts -> equal timeline.

``BarRow.status`` (``BAR_STATUSES``):

* ``present`` -- a continuous 5m bar exists; ``open/high/low/close/volume`` and
  ``reference_median`` (median of OHLC) / ``reference_close`` (the bar close)
  are finite floats.  Every other status leaves all seven fields ``None`` -- a
  non-traded bar never carries a fabricated ``0.0``.
* ``missing`` -- no continuous bar at that label.  ``provider_omitted`` splits
  the cause: ``True`` when the symbol is in ``facts.missing_symbols`` (no 5m row
  at all for the fetched window), ``False`` when the symbol has rows that day
  but not at this label (halt / liquidity gap, a market fact).
  :meth:`BarTimeline.missing_by_symbol` exposes the per-symbol labels.
* ``auction`` -- a label recorded in ``facts.auction_by_symbol``.  Auction rows
  are NOT in the 48-label grid: :meth:`BarTimeline.auction_rows` is their
  accessor.  ``phase`` remains the grid-strict :func:`bar_clock.classify`
  (``14:57``-``14:59`` -> ``auction_window``; ``09:15``/``09:25`` -> ``outside``).
* ``outside`` -- an off-grid label found in a day frame (``09:31``/``14:56``/
  ``12:30``); removed from the grid and exposed via
  :meth:`BarTimeline.outside_rows` / :meth:`BarTimeline.outside_labels`.

A malformed shape raises :class:`TimelineError` (a ``ValueError``): absent
trade date, non-datetime day index, duplicate ``(label, symbol)``, non-finite or
non-numeric OHLCV, or a bars symbol outside an explicit ``symbols`` universe.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import pandas as pd

from . import bar_clock
from .bar_clock import SessionPhase
from .data_5m import FiveMinuteFacts
from .schema_v13 import SESSION_PHASES

__all__ = [
    "BAR_STATUSES",
    "BarRow",
    "BarTimeline",
    "TimelineError",
    "build_timeline",
    "tradable",
]

#: The complete ``BarRow.status`` vocabulary, in decision order.
BAR_STATUSES: tuple[str, ...] = ("present", "missing", "auction", "outside")

STATUS_PRESENT = "present"
STATUS_MISSING = "missing"
STATUS_AUCTION = "auction"
STATUS_OUTSIDE = "outside"

_OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
_ROW_VALUE_FIELDS: tuple[str, ...] = (*_OHLCV_COLUMNS, "reference_median", "reference_close")


class TimelineError(ValueError):
    """A deterministic data-shape error raised while assembling the timeline."""


@dataclass(frozen=True, slots=True)
class BarRow:
    """One ``(bar_label, symbol)`` cell; ``bar_time`` is the aware bar-END stamp.

    A ``present`` row carries a finite OHLCV/both-reference tuple; every other
    status leaves all seven value fields ``None``.
    """

    trade_date: str
    bar_label: str
    bar_time: str
    symbol: str
    phase: SessionPhase
    status: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    reference_median: float | None = None
    reference_close: float | None = None
    provider_omitted: bool = False

    def __post_init__(self) -> None:
        if self.status not in BAR_STATUSES:
            raise TimelineError(f"{self.symbol} {self.bar_label}: unknown bar status {self.status!r}")
        if self.phase not in SESSION_PHASES:
            raise TimelineError(f"{self.symbol} {self.bar_label}: unknown session phase {self.phase!r}")
        values = tuple(getattr(self, field) for field in _ROW_VALUE_FIELDS)
        if self.status == STATUS_PRESENT:
            if any(value is None for value in values):
                raise TimelineError(
                    f"{self.symbol} {self.bar_label}: a present bar needs OHLCV and both references"
                )
            if not all(math.isfinite(value) for value in values):
                raise TimelineError(f"{self.symbol} {self.bar_label}: a present bar has a non-finite value")
        elif any(value is not None for value in values):
            raise TimelineError(f"{self.symbol} {self.bar_label}: a {self.status} row must not carry OHLCV")
        if self.provider_omitted and self.status != STATUS_MISSING:
            raise TimelineError(
                f"{self.symbol} {self.bar_label}: provider_omitted is only valid on a missing row"
            )


def tradable(row: BarRow | None) -> bool:
    """Whether a bar row carries a print an order may be priced against.

    ``present`` with a POSITIVE volume is the only tradable state.  A missing
    row (no frame, a gap, a provider omission) and a zero-volume label are
    NO-TRADE: an order rests, and the next traded bar prices it.  A hand-built
    present row without a volume is treated as traded; every canonical row
    carries the column.
    """
    if row is None or row.status != STATUS_PRESENT:
        return False
    return row.volume is None or row.volume > 0


@dataclass(frozen=True, slots=True)
class BarTimeline:
    """One day of rows: the 48 grid labels, then the out-of-grid accessors."""

    trade_date: str
    bar_labels: tuple[str, ...]
    rows_by_bar: Mapping[str, tuple[BarRow, ...]]
    symbols: tuple[str, ...]
    auction: tuple[BarRow, ...] = ()
    outside: tuple[BarRow, ...] = ()

    def rows_for(self, label: str) -> tuple[BarRow, ...]:
        """Every symbol's row for ``label``, in sorted symbol order."""
        rows = self.rows_by_bar.get(label)
        if rows is None:
            raise TimelineError(f"bar label {label!r} is not part of the {self.trade_date} timeline")
        return rows

    def row(self, label: str, symbol: str) -> BarRow:
        """The single row for ``(label, symbol)``."""
        for row in self.rows_for(label):
            if row.symbol == symbol:
                return row
        raise TimelineError(
            f"symbol {symbol!r} has no row for bar label {label!r} on {self.trade_date}"
        )

    def labels_with_status(self, status: str) -> tuple[str, ...]:
        """Bar labels carrying at least one row with ``status``, in canonical order."""
        if status not in BAR_STATUSES:
            raise TimelineError(f"unknown bar status {status!r} (expected one of {', '.join(BAR_STATUSES)})")
        if status == STATUS_AUCTION:
            return tuple(sorted({row.bar_label for row in self.auction}))
        if status == STATUS_OUTSIDE:
            return tuple(sorted({row.bar_label for row in self.outside}))
        return tuple(
            label for label in self.bar_labels
            if any(row.status == status for row in self.rows_by_bar[label])
        )

    def missing_by_symbol(self) -> Mapping[str, tuple[str, ...]]:
        """Missing labels per symbol in grid order; read ``provider_omitted`` for the cause."""
        missing: dict[str, list[str]] = {}
        for label in self.bar_labels:
            for row in self.rows_by_bar[label]:
                if row.status == STATUS_MISSING:
                    missing.setdefault(row.symbol, []).append(label)
        return MappingProxyType({symbol: tuple(missing[symbol]) for symbol in sorted(missing)})

    def auction_rows(self) -> tuple[BarRow, ...]:
        """Auction labels recorded by the facts, outside the 48-label grid."""
        return self.auction

    def outside_rows(self) -> tuple[BarRow, ...]:
        """Off-grid labels found in a day frame, outside the 48-label grid."""
        return self.outside

    def outside_labels(self) -> tuple[str, ...]:
        """Distinct off-grid labels found in a day frame, in label order."""
        return tuple(sorted({row.bar_label for row in self.outside}))


def _require_datetime_index(frame: Any, symbol: str, trade_date: str) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TimelineError(
            f"5m frame for {symbol} on {trade_date} is not indexed by datetime "
            f"(got {type(frame.index).__name__})"
        )


def _finite_float(value: Any, *, symbol: str, trade_date: str, label: str, column: str) -> float:
    """Copy one OHLCV cell to a finite ``float``; a bad cell is a shape error."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TimelineError(f"{symbol} {trade_date} {label}: {column} is not numeric ({value!r})") from None
    if not math.isfinite(number):
        raise TimelineError(f"{symbol} {trade_date} {label}: {column} is not finite ({number!r})")
    return number


def _scan_day_frame(
    symbol: str, trade_date: str, frame: Any
) -> tuple[dict[str, dict[str, float]], tuple[str, ...]]:
    """Split a day frame into continuous bars and sorted off-grid labels.

    A second row resolving to the same ``(label, symbol)`` is a collision.  An
    auction-phase label inside a bars frame is ignored: ``facts.auction_by_symbol``
    is the single auction source.
    """
    _require_datetime_index(frame, symbol, trade_date)
    absent_columns = [column for column in _OHLCV_COLUMNS if column not in frame.columns]
    if absent_columns:
        raise TimelineError(
            f"5m frame for {symbol} on {trade_date} is missing column(s): {', '.join(absent_columns)}"
        )
    bars: dict[str, dict[str, float]] = {}
    outside: list[str] = []
    for timestamp, row in frame.iterrows():
        label = timestamp.strftime("%H:%M")
        phase = bar_clock.classify(label)
        if phase == bar_clock.SESSION_CONTINUOUS:
            if label in bars:
                raise TimelineError(
                    f"duplicate bar label {label!r} for {symbol} on {trade_date}: "
                    "two rows resolve to the same (label, symbol)"
                )
            bars[label] = {
                column: _finite_float(
                    row[column], symbol=symbol, trade_date=trade_date, label=label, column=column
                )
                for column in _OHLCV_COLUMNS
            }
        elif phase == bar_clock.SESSION_OUTSIDE:
            outside.append(label)
    return bars, tuple(sorted(set(outside)))


def _row(
    trade_date: str,
    label: str,
    symbol: str,
    *,
    status: str,
    values: Mapping[str, float] | None = None,
    provider_omitted: bool = False,
) -> BarRow:
    """Build one canonical row; ``values`` is present only for a traded bar."""
    prices = values or {}
    return BarRow(
        trade_date=trade_date,
        bar_label=label,
        bar_time=bar_clock.bar_time(trade_date, label).isoformat(),
        symbol=symbol,
        phase=bar_clock.classify(label),
        status=status,
        open=prices.get("open"),
        high=prices.get("high"),
        low=prices.get("low"),
        close=prices.get("close"),
        volume=prices.get("volume"),
        reference_median=(
            float(statistics.median([prices[column] for column in _PRICE_COLUMNS])) if prices else None
        ),
        reference_close=prices.get("close"),
        provider_omitted=provider_omitted,
    )


def _symbol_universe(facts: FiveMinuteFacts, symbols: Sequence[str] | None) -> tuple[str, ...]:
    """The sorted symbol set, defaulting to every symbol the facts mention."""
    if symbols is None:
        return tuple(sorted(
            set(facts.bars_by_symbol) | set(facts.auction_by_symbol) | set(facts.missing_symbols)
        ))
    if isinstance(symbols, str):
        raise TimelineError("symbols must be a sequence of symbols, not a bare string")
    requested = tuple(symbols)
    for symbol in requested:
        if not isinstance(symbol, str) or not symbol:
            raise TimelineError(f"symbols must be non-empty strings, got {symbol!r}")
    universe = tuple(sorted(set(requested)))
    if not universe:
        raise TimelineError("symbols must contain at least one symbol")
    extra = sorted(set(facts.bars_by_symbol) - set(universe))
    if extra:
        raise TimelineError(
            f"facts.bars_by_symbol contains symbol(s) outside the requested universe: {', '.join(extra)}"
        )
    return universe


def _require_day_present(facts: FiveMinuteFacts, trade_date: str) -> None:
    """The requested day must be observable in the facts (bars/auction/observed)."""
    if any(trade_date in days for days in facts.observed_days_by_symbol.values()):
        return
    if any(trade_date in days for days in facts.auction_by_symbol.values()):
        return
    if any(trade_date in frames for frames in facts.bars_by_symbol.values()):
        return
    raise TimelineError(
        f"trade_date {trade_date!r} is not present in the 5m facts "
        "(no bars, auction labels or observed rows on that day)"
    )


def _auction_rows(facts: FiveMinuteFacts, symbol: str, trade_date: str) -> tuple[BarRow, ...]:
    """Auction rows for one symbol-day, in label order (label uniqueness enforced)."""
    labels = facts.auction_by_symbol.get(symbol, {}).get(trade_date, ())
    if len(set(labels)) != len(labels):
        raise TimelineError(f"duplicate auction label for {symbol} on {trade_date}: {labels!r}")
    rows: list[BarRow] = []
    for label in sorted(labels):
        try:
            phase = bar_clock.classify(label)
        except ValueError as exc:
            raise TimelineError(f"{symbol} {trade_date}: malformed auction label {label!r}: {exc}") from None
        if phase == bar_clock.SESSION_CONTINUOUS:
            raise TimelineError(f"{symbol} {trade_date}: auction label {label!r} is a continuous bar")
        rows.append(_row(trade_date, label, symbol, status=STATUS_AUCTION))
    return tuple(rows)


def build_timeline(
    facts: FiveMinuteFacts,
    trade_date: str,
    *,
    symbols: Sequence[str] | None = None,
) -> BarTimeline:
    """Build the ordered per-bar timeline for ``trade_date`` from ``facts``.

    Emits exactly one row per ``(label, symbol)`` in
    :data:`bar_clock.BAR_LABELS` order with sorted symbols.  ``symbols=None``
    uses every symbol the facts mention; an explicit universe must contain every
    bars symbol.  No data is fetched, aggregated or reordered.
    """
    # Validate the date through the clock's own grammar (raises ValueError).
    bar_clock.bar_time(trade_date, bar_clock.BAR_LABELS[0])
    _require_day_present(facts, trade_date)
    universe = _symbol_universe(facts, symbols)
    omitted = frozenset(facts.missing_symbols)

    rows_by_label: dict[str, list[BarRow]] = {label: [] for label in bar_clock.BAR_LABELS}
    auction_rows: list[BarRow] = []
    outside_rows: list[BarRow] = []

    for symbol in universe:
        day_frame = facts.bars_by_symbol.get(symbol, {}).get(trade_date)
        bars: dict[str, dict[str, float]] = {}
        outside: tuple[str, ...] = ()
        if day_frame is not None:
            bars, outside = _scan_day_frame(symbol, trade_date, day_frame)
        outside_rows.extend(_row(trade_date, label, symbol, status=STATUS_OUTSIDE) for label in outside)
        for label in bar_clock.BAR_LABELS:
            values = bars.get(label)
            rows_by_label[label].append(_row(
                trade_date, label, symbol,
                status=STATUS_PRESENT if values is not None else STATUS_MISSING,
                values=values,
                provider_omitted=symbol in omitted and values is None,
            ))
        auction_rows.extend(_auction_rows(facts, symbol, trade_date))

    return BarTimeline(
        trade_date=trade_date,
        bar_labels=bar_clock.BAR_LABELS,
        rows_by_bar=MappingProxyType({label: tuple(rows) for label, rows in rows_by_label.items()}),
        symbols=universe,
        auction=tuple(auction_rows),
        outside=tuple(outside_rows),
    )
