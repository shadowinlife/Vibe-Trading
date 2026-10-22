"""Five-minute day-frame feeds for the pt-v1.3 engine (P1 intraday, GAP-8).

The feed layer is the only door through which a 5m day frame enters the engine:
one canonical shape and one closure rule shared by the replay session
(:mod:`~.session_replay`) and the scheduler (:mod:`~.scheduler`).

Canonical frame
---------------
``day_bars`` / ``closed_bars`` return a fresh ``pandas.DataFrame``: a
``DatetimeIndex`` named ``bar_time`` (ascending, unique, Asia/Shanghai wall
clock, tz-naive — a tz-aware input is converted), exactly
``open/high/low/close/volume`` as ``float64``, and the day frame as the source
serves it.  Labels are bar ENDS (``09:35`` covers 09:30-09:35; ``15:00`` is
the last continuous bar).  The feed does not classify phases or drop the 09:30
opening-auction print — the timeline owns that; a ``FiveMinuteFacts`` replay is
continuous-only because the facts were already split.  Only ``closed_bars``
removes rows, and only in-progress ones.  Every call returns a deep copy, so a
caller cannot mutate the feed or its source mapping.

The caller injects ``trade_date`` and ``as_of``.  The module never reads a wall
clock, never sleeps, and performs no network I/O at import time — provider
imports live inside the adapter methods.

Closed bars
-----------
A bar labelled ``HH:MM`` is closed when ``label <= as_of``.  The bar ending at
``as_of`` IS closed; a later label is never returned.  ``as_of`` must be a
strict ``HH:MM`` (on or off the 48-bar grid); otherwise closure is undecidable
and the feed raises ``FeedError("not_closed")``.

Errors
------
``data_missing``
    No frame for the symbol/day (unknown key, unresolvable symbol, empty
    provider response).  A present-but-empty frame is NOT an error: it is the
    authoritative zero-bar (halted) answer.
``feed_unavailable``
    The source cannot serve the contract: malformed frame (missing column,
    non-datetime index, duplicate label, non-finite value), provider fault, or
    a Tushare call without a configured token.
``not_closed``
    ``as_of`` is not a valid ``HH:MM`` label.

Previous close
--------------
:class:`PreviousCloseFeed` is an OPTIONAL extension a feed may implement: the
prior session's daily close per symbol, the input the cn_a band heuristic
needs.  It is deliberately optional — no shipped adapter implements it, and a
feed that returns ``None`` (or ``data_missing``) leaves the band UNKNOWN rather
than fabricating a limit.

Token policy
------------
No adapter embeds, echoes, logs, or persists a credential.  ``TushareFeed``
asks the existing config layer whether the token is configured and keeps only
the boolean; a provider exception is reported by TYPE NAME only (its message
is never copied into a ``FeedError`` or log record) and the chain is
suppressed.  The token value is read by ``data_5m`` / the loader, never here.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from . import bar_clock

__all__ = [
    "CANONICAL_COLUMNS",
    "FEED_ERROR_CODES",
    "EastmoneyFeed",
    "FeedError",
    "FiveMinuteFeed",
    "PreviousCloseFeed",
    "ReplayFeed",
    "TushareFeed",
]

logger = logging.getLogger(__name__)

#: The canonical frame columns, in order.
CANONICAL_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

DATA_MISSING = "data_missing"
FEED_UNAVAILABLE = "feed_unavailable"
NOT_CLOSED = "not_closed"

#: The stable ``FeedError.code`` vocabulary.
FEED_ERROR_CODES: frozenset[str] = frozenset({DATA_MISSING, FEED_UNAVAILABLE, NOT_CLOSED})

_CN_A_TIMEZONE = "Asia/Shanghai"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class FeedError(RuntimeError):
    """A feed failure with a stable machine-readable ``code``.

    ``message`` is safe to log: it never carries a provider credential.  A
    provider's own exception text is deliberately not copied here (only its
    type name), so an upstream error that echoes a token cannot leak through
    this object.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in FEED_ERROR_CODES:
            raise ValueError(f"unknown feed error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@runtime_checkable
class FiveMinuteFeed(Protocol):
    """The engine's 5m data seam (see the module docstring for the contract)."""

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        """Every bar the source holds for ``symbol`` on ``trade_date``.

        Raises:
            FeedError: ``data_missing`` when no frame exists for the day,
                ``feed_unavailable`` when the frame is malformed.
        """
        ...

    def closed_bars(self, *, symbol: str, trade_date: str, as_of: str) -> pd.DataFrame:
        """Only the bars CLOSED by ``as_of`` (a bar-END ``HH:MM`` label).

        Raises:
            FeedError: as :meth:`day_bars`, plus ``not_closed`` when ``as_of``
                is not a valid ``HH:MM`` label.
        """
        ...


@runtime_checkable
class PreviousCloseFeed(Protocol):
    """Optional band input: the previous session's daily close for a symbol.

    A feed that exposes :meth:`previous_close` lets a live bar derive the cn_a
    price band when the request carries no ``prev_closes``.  ``None`` means the
    close is unknown; the band then stays UNKNOWN and the band slots are
    skipped.  No shipped adapter implements this seam.
    """

    def previous_close(self, *, symbol: str, trade_date: str) -> float | None:
        """The prior session's daily close for ``symbol``, or ``None``."""
        ...


def _require_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise FeedError(DATA_MISSING, "symbol must be a non-empty string")
    return symbol


def _require_trade_date(trade_date: Any) -> str:
    if not isinstance(trade_date, str) or _DATE_RE.fullmatch(trade_date) is None:
        raise FeedError(DATA_MISSING, f"trade_date must be a YYYY-MM-DD day, got {trade_date!r}")
    try:
        dt.date.fromisoformat(trade_date)
    except ValueError:
        raise FeedError(DATA_MISSING, f"trade_date must be a real calendar day, got {trade_date!r}") from None
    return trade_date


def _require_as_of(as_of: Any) -> str:
    if not isinstance(as_of, str):
        raise FeedError(NOT_CLOSED, f"as_of must be an HH:MM label, got {type(as_of).__name__}")
    try:
        bar_clock.classify(as_of)
    except ValueError:
        raise FeedError(NOT_CLOSED, f"as_of must be an HH:MM label, got {as_of!r}") from None
    return as_of


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {column: pd.Series(dtype="float64") for column in CANONICAL_COLUMNS},
        index=pd.DatetimeIndex([], name="bar_time"),
    )


def _canonical_day_frame(frame: Any, *, symbol: str, trade_date: str) -> pd.DataFrame:
    """Validate and normalize one day frame; never mutate the input."""
    where = f"{symbol} on {trade_date}"
    if not isinstance(frame, pd.DataFrame):
        raise FeedError(FEED_UNAVAILABLE, f"5m feed for {where} returned {type(frame).__name__}, not a DataFrame")
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise FeedError(FEED_UNAVAILABLE, f"5m frame for {where} is missing column(s): {', '.join(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise FeedError(
            FEED_UNAVAILABLE,
            f"5m frame for {where} is not indexed by datetime (got {type(frame.index).__name__})",
        )
    if frame.empty:
        return _empty_frame()
    if frame.index.has_duplicates:
        raise FeedError(FEED_UNAVAILABLE, f"5m frame for {where} has duplicate bar timestamps")

    canonical = frame.copy(deep=True)
    index = pd.DatetimeIndex(canonical.index)
    if index.tz is not None:
        index = index.tz_convert(_CN_A_TIMEZONE).tz_localize(None)
    canonical.index = index
    canonical.index.name = "bar_time"
    canonical = canonical.sort_index()[list(CANONICAL_COLUMNS)]
    for column in CANONICAL_COLUMNS:
        canonical[column] = pd.to_numeric(canonical[column], errors="coerce")
    if canonical.isna().any().any() or not (canonical.abs() < float("inf")).all().all():
        raise FeedError(FEED_UNAVAILABLE, f"5m frame for {where} has a non-numeric or non-finite OHLCV value")

    on_day = canonical[canonical.index.strftime("%Y-%m-%d") == trade_date]
    if on_day.empty:
        raise FeedError(DATA_MISSING, f"5m frame for {where} holds no bars on that trade date")
    return on_day.astype("float64").copy(deep=True)


class _DayFrameFeed:
    """Shared ``closed_bars`` over a concrete ``day_bars``."""

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        raise NotImplementedError

    def closed_bars(self, *, symbol: str, trade_date: str, as_of: str) -> pd.DataFrame:
        _require_as_of(as_of)
        frame = self.day_bars(symbol=symbol, trade_date=trade_date)
        closed = frame[frame.index.strftime("%H:%M") <= as_of]
        return closed.copy(deep=True)


class ReplayFeed(_DayFrameFeed):
    """A feed served from caller-injected cached day frames (no I/O at all).

    ``frames`` maps ``symbol -> trade_date -> DataFrame``.  The mapping is
    copied defensively, each frame is validated and copied on read, and an
    absent key is ``data_missing`` while a present-but-empty frame is the
    authoritative zero-bar (halted) answer.
    """

    def __init__(self, *, frames: Mapping[str, Mapping[str, pd.DataFrame]]) -> None:
        if not isinstance(frames, Mapping):
            raise TypeError("frames must map symbol -> trade_date -> DataFrame")
        for symbol, days in frames.items():
            if not isinstance(symbol, str) or not isinstance(days, Mapping):
                raise TypeError("frames must map symbol -> trade_date -> DataFrame")
        self._frames = {symbol: dict(days) for symbol, days in frames.items()}

    @classmethod
    def from_facts(cls, facts: Any) -> ReplayFeed:
        """Serve the per-day continuous bars held by :class:`FiveMinuteFacts`."""
        bars = getattr(facts, "bars_by_symbol", None)
        if not isinstance(bars, Mapping):
            raise TypeError("facts must expose a bars_by_symbol mapping")
        return cls(frames=bars)

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        _require_symbol(symbol)
        _require_trade_date(trade_date)
        days = self._frames.get(symbol)
        if days is None or trade_date not in days:
            raise FeedError(DATA_MISSING, f"no cached 5m frame for {symbol} on {trade_date}")
        return _canonical_day_frame(days[trade_date], symbol=symbol, trade_date=trade_date)


def _tushare_token_configured() -> bool:
    """Whether the existing config layer sees a usable provider token.

    Only a boolean leaves this function.  The loader owns the token value and
    the placeholder list; this package never stores or renders either.
    """
    from backtest.loaders.tushare import TUSHARE_TOKEN_PLACEHOLDERS
    from src.config.accessor import get_env_config

    token = get_env_config().data.tushare_token.strip()
    return token not in TUSHARE_TOKEN_PLACEHOLDERS


class TushareFeed(_DayFrameFeed):
    """The live Tushare ``stk_mins`` adapter (one symbol, one day, one call).

    Delegates the provider call to :func:`data_5m.fetch_5m_frame`; the lazy
    import keeps the provider stack out of the engine's import path.  Without
    a configured token the adapter SKIPS with ``feed_unavailable`` instead of
    issuing a request, so a missing credential is a clear skip rather than a
    provider error.  The provider's ``end_date`` is exclusive at midnight
    (``data_5m``), so day ``D`` is requested as ``D .. D+1``.
    """

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        _require_symbol(symbol)
        _require_trade_date(trade_date)
        if not _tushare_token_configured():
            logger.warning(
                "Tushare 5m feed skipped for %s on %s: token is not configured",
                symbol,
                trade_date,
            )
            raise FeedError(FEED_UNAVAILABLE, "Tushare token is not configured; the 5m feed is skipped")

        from . import data_5m

        end_exclusive = (dt.date.fromisoformat(trade_date) + dt.timedelta(days=1)).isoformat()
        try:
            frame, _loader = data_5m.fetch_5m_frame(symbol, trade_date, end_exclusive)
        except Exception as exc:  # noqa: BLE001 - provider boundary; only the type name escapes
            logger.warning(
                "Tushare 5m fetch failed for %s on %s: %s",
                symbol,
                trade_date,
                type(exc).__name__,
            )
            raise FeedError(FEED_UNAVAILABLE, f"Tushare 5m fetch failed: {type(exc).__name__}") from None
        if frame is None or len(frame) == 0:
            raise FeedError(DATA_MISSING, f"no 5m bars for {symbol} on {trade_date}")
        canonical = _canonical_day_frame(frame, symbol=symbol, trade_date=trade_date)
        if canonical.empty:
            raise FeedError(DATA_MISSING, f"no 5m bars for {symbol} on {trade_date}")
        return canonical


def _five_minute_klt(client: Any) -> int:
    mapping = getattr(client, "KLT_BY_INTERVAL", None)
    if mapping is None:
        from backtest.loaders.eastmoney_client import KLT_BY_INTERVAL

        mapping = KLT_BY_INTERVAL
    try:
        return int(mapping["5m"])
    except (KeyError, TypeError) as exc:
        raise FeedError(FEED_UNAVAILABLE, "the Eastmoney client has no 5m kline interval") from exc


def _eastmoney_day_frame(rows: Any, *, symbol: str, trade_date: str) -> pd.DataFrame:
    """Map ascending ``fetch_kline`` row dicts onto an indexable frame."""
    if rows is None or len(rows) == 0:
        raise FeedError(DATA_MISSING, f"no 5m bars for {symbol} on {trade_date}")
    records = [row for row in rows if isinstance(row, Mapping)]
    if len(records) != len(rows):
        raise FeedError(FEED_UNAVAILABLE, f"Eastmoney returned non-row entries for {symbol} on {trade_date}")
    frame = pd.DataFrame.from_records(records)
    if "trade_date" not in frame.columns:
        raise FeedError(FEED_UNAVAILABLE, f"Eastmoney rows for {symbol} on {trade_date} have no trade_date")
    timestamps = pd.to_datetime(frame["trade_date"], errors="coerce")
    if timestamps.isna().any():
        raise FeedError(
            FEED_UNAVAILABLE,
            f"Eastmoney rows for {symbol} on {trade_date} hold an unparseable trade_date",
        )
    frame = frame.drop(columns=["trade_date"])
    frame.index = pd.DatetimeIndex(timestamps)
    return frame


class EastmoneyFeed(_DayFrameFeed):
    """The free, no-auth Eastmoney interval-``5m`` adapter.

    Calls the existing ``eastmoney_client.fetch_kline`` with ``klt`` from
    ``KLT_BY_INTERVAL["5m"]`` and the requested day as both ``beg`` and
    ``end`` (Eastmoney's bounds are inclusive).  ``client`` is injectable for
    offline tests; when omitted, the loader module is imported lazily inside
    :meth:`day_bars`, never at module import.  In-progress rows are served by
    ``day_bars`` and removed by ``closed_bars``.
    """

    def __init__(self, client: Any | None = None, *, fqt: int = 1) -> None:
        self._client = client
        self._fqt = int(fqt)

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        _require_symbol(symbol)
        _require_trade_date(trade_date)
        client = self._client
        if client is None:
            from backtest.loaders import eastmoney_client as client

        compact_day = pd.Timestamp(trade_date).strftime("%Y%m%d")
        try:
            secid = client.resolve_secid(symbol)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise FeedError(
                FEED_UNAVAILABLE,
                f"Eastmoney symbol lookup failed: {type(exc).__name__}",
            ) from None
        if not secid:
            raise FeedError(DATA_MISSING, f"Eastmoney cannot resolve symbol {symbol}")

        klt = _five_minute_klt(client)
        try:
            rows = client.fetch_kline(
                secid,
                klt=klt,
                fqt=self._fqt,
                beg=compact_day,
                end=compact_day,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary; only the type name escapes
            logger.warning(
                "Eastmoney 5m fetch failed for %s on %s: %s",
                symbol,
                trade_date,
                type(exc).__name__,
            )
            raise FeedError(FEED_UNAVAILABLE, f"Eastmoney 5m fetch failed: {type(exc).__name__}") from None

        frame = _eastmoney_day_frame(rows, symbol=symbol, trade_date=trade_date)
        canonical = _canonical_day_frame(frame, symbol=symbol, trade_date=trade_date)
        if canonical.empty:
            raise FeedError(DATA_MISSING, f"no 5m bars for {symbol} on {trade_date}")
        return canonical
