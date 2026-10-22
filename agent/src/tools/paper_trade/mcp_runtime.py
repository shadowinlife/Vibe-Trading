"""pt-v1.3 MCP runtime: the live single-writer bar and the pure bar replay.

This module is the runtime installed on :mod:`~.tool_v13`'s bar/status seams,
and it never imports ``tool_v13`` (that module imports this one), so the entry
points and the runtime stay acyclic.

:func:`bar` dispatches on ``request.mode``:

* ``replay`` is PURE.  It builds the engine state from the carried
  ``account_state`` / ``open_orders`` / ``order_seq``, the timeline from the
  injected ``bars``, derives the day's band table from the request's explicit
  ``prev_closes`` (a symbol with no close keeps an UNKNOWN band) and calls
  :func:`~.bar_engine.apply_bar` once.  It constructs no store, reads no
  clock, performs no network I/O and writes nothing.
* ``live`` is the single-writer path.  It loads the STORED account, refuses
  the bar when the carried snapshot disagrees with it
  (``account_state_stale``), then hands the bar to
  :func:`~.session_runner.bar_step`.  A bar that is already applied is
  answered ``bar_already_applied`` and is never applied twice.  The band
  table comes from the request's explicit ``prev_closes``; when that is
  absent the feed is asked only if it exposes the optional
  ``previous_close(symbol=..., trade_date=...)`` seam, and otherwise the band
  stays UNKNOWN (never fabricated).

The idempotency answer comes BEFORE the stale check on purpose: a repeat of an
applied bar necessarily carries the pre-bar snapshot, so a stale-first order
would mask ``bar_already_applied`` with ``account_state_stale``.  The runner
re-checks the progress gate under its account lock, so this early gate fixes
the answer ORDER only — it is never the write authority.

:func:`status` is a read-only projection of the stored account plus the number
of durable bar directories for its current session day.  It reads no clock and
echoes no store path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import pandas as pd

from . import bar_clock, session_runner, wire
from .account import PaperTradeAccount
from .bar_engine import BarEngineError, BarEngineState, apply_bar
from .data_5m import FiveMinuteFacts
from .fill_rules import FillRuleError, band_for
from .feeds import (
    CANONICAL_COLUMNS,
    DATA_MISSING,
    EastmoneyFeed,
    FeedError,
    FiveMinuteFeed,
    ReplayFeed,
    TushareFeed,
)
from .orders import InvalidTransition, OrderBook
from .rejections import RejectionContext, make_reject_hook
from .rules import MarketRules
from .schema import DateRange, ErrorEnvelope, PaperTradeSchemaError
from .schema_v13 import BarStepRequest, BarStepResult, parse_bar_time
from .session_documents import (
    CODE_ACCOUNT_NOT_FOUND,
    CODE_BAR_ALREADY_APPLIED,
    CODE_BAR_OUT_OF_ORDER,
    CODE_INVALID_REQUEST,
    SessionRunnerError,
)
from .store import AccountNotFound, AccountStore, StoreError
from .timeline import BarTimeline, TimelineError, build_timeline

__all__ = ["CODE_ACCOUNT_STATE_STALE", "bar", "install", "status"]

CODE_ACCOUNT_STATE_STALE = "account_state_stale"


# ── the two bar modes ────────────────────────────────────────────────────


def bar(request: BarStepRequest, *, store: AccountStore | None = None) -> BarStepResult:
    """Apply ONE pt-v1.3 bar and return its typed result.

    ``request.mode="replay"`` runs the pure engine over the carried ledger and
    injected bars; ``store`` is ignored on that path.  ``mode="live"`` (the
    default) advances the stored account through the session runner, and
    ``store`` overrides the runtime-root account directory for tests.
    """
    if not isinstance(request, BarStepRequest):
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"request must be a BarStepRequest, got {type(request).__name__}",
        )
    if request.mode == "replay":
        return _replay(request)
    if request.mode == "live":
        return _live(request, store)
    raise SessionRunnerError(
        CODE_INVALID_REQUEST,
        f"mode must be one of ('live', 'replay'), got {request.mode!r}",
        **_identity(request),
    )


def _replay(request: BarStepRequest) -> BarStepResult:
    """The pure path: carried ledger + injected bars, no store, no I/O."""
    state = _carried_state(request)
    timeline = _replay_timeline(request, state, _frames_from_bars(request))
    rules = MarketRules(request.market, request.fee_model)
    bands = _bands_from_prev_closes(request, timeline)
    context = RejectionContext(
        trading_day=True,
        suspended_symbols=frozenset(),
        corporate_action_unresolved=frozenset(),
        bands=bands,
        rules=rules,
        path_model=request.path_model,
    )
    try:
        outcome = apply_bar(
            state,
            request,
            timeline,
            bands=bands,
            reject_hook=make_reject_hook(context),
            rules=rules,
        )
    except BarEngineError as exc:
        raise SessionRunnerError(CODE_INVALID_REQUEST, str(exc), **_identity(request)) from exc
    return outcome.bar_result


def _live(request: BarStepRequest, store: AccountStore | None) -> BarStepResult:
    """The single-writer path: stale guard, progress gate, then ``bar_step``."""
    target = store if store is not None else AccountStore()
    identity = _identity(request)
    try:
        stored = target.load(request.account_id)
    except AccountNotFound as exc:
        raise SessionRunnerError(
            CODE_ACCOUNT_NOT_FOUND,
            f"no paper-trade account {request.account_id!r}",
            **identity,
        ) from exc
    except StoreError as exc:
        raise SessionRunnerError(exc.code, exc.message, **identity) from exc

    _require_cn_a_5m(request, stored)
    label = _label_of(request)
    refusal = _progress_refusal(stored, request, label)
    if refusal is not None:
        raise refusal
    stale = _stale_detail(request, stored)
    if stale:
        raise SessionRunnerError(
            CODE_ACCOUNT_STATE_STALE,
            f"{stale}; re-read the account before sending a live bar",
            **identity,
        )

    feed = _feed_for(request)
    run = session_runner.bar_step(
        target, request, feed, prev_closes=_prev_closes_for(request, feed)
    )
    if run.outcome is None:
        raise RuntimeError("session_runner.bar_step returned no outcome")
    return run.outcome.bar_result


# ── the read-only status projection ──────────────────────────────────────


def status(payload: dict[str, Any], *, store: AccountStore | None = None) -> str:
    """Return ONE in-band pt-v1.3 status document for the stored account.

    The document always carries ``account`` (``to_dict(include_v13=True)``),
    ``open_orders``, ``watermark``, ``watermark_bar``, ``applied_bars``,
    ``next_bar`` and ``session_status``.  An unknown account, an unreadable
    account or a market that disagrees with it is the in-band
    ``INVALID_REQUEST`` envelope with the stable code as the message prefix.
    """
    raw = payload if isinstance(payload, dict) else {}
    account_id = _text(raw.get("account_id"))
    market = _text(raw.get("market"))
    target = store if store is not None else AccountStore()
    try:
        account = target.load(account_id)
    except AccountNotFound:
        return _error_document(
            raw, f"{CODE_ACCOUNT_NOT_FOUND}: no paper-trade account {account_id!r}"
        )
    except StoreError as exc:
        return _error_document(raw, f"{exc.code}: {exc.message}")
    if market and market != account.market:
        return _error_document(
            raw,
            f"{CODE_INVALID_REQUEST}: market {market!r} disagrees with the "
            f"account's {account.market!r}",
        )

    session_date = account.watermark or account.start_date
    closed = account.watermark == session_date
    summary = account.to_dict(include_v13=True)
    return wire.dumps_payload(
        {
            "schema_version": "pt-v1.3",
            "tool": "paper_trade_account_status",
            "status": "ok",
            "account": summary,
            "open_orders": summary["open_orders"],
            "watermark": account.watermark,
            "watermark_bar": account.watermark_bar,
            "applied_bars": _applied_bars(target, account, session_date),
            "next_bar": "" if closed else _next_bar(account.watermark_bar),
            "session_status": "closed" if closed else "open",
        }
    )


def install() -> None:
    """Install :func:`bar` and :func:`status` on ``tool_v13``'s runtime seams."""
    from . import tool_v13

    tool_v13._V13_BAR_RUNTIME = bar
    tool_v13._V13_STATUS_RUNTIME = status


# ── carried state + timeline (the pure replay pieces) ────────────────────


def _carried_state(request: BarStepRequest) -> BarEngineState:
    """Build the engine state from the request's RM-injected ledger snapshot."""
    try:
        book = OrderBook(request.account_id, request.trade_date, rows=request.open_orders)
        return BarEngineState(
            account_id=request.account_id,
            market=request.market,
            trade_date=request.trade_date,
            cash=Decimal(str(request.account_state.cash)),
            positions=request.account_state.positions,
            book=book,
            order_seq=request.order_seq,
            bought_today={},
            watermark_bar="",
        )
    except (BarEngineError, InvalidTransition) as exc:
        raise SessionRunnerError(CODE_INVALID_REQUEST, str(exc), **_identity(request)) from exc


def _frames_from_bars(request: BarStepRequest) -> dict[str, pd.DataFrame]:
    """One canonical day frame per symbol from the injected ``bars`` array."""
    return {
        str(entry["symbol"]): _frame_from_rows(request, str(entry["symbol"]), entry["bars"])
        for entry in request.bars
    }


def _frame_from_rows(
    request: BarStepRequest, symbol: str, rows: Any
) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(list(rows))
    try:
        stamps = pd.to_datetime(frame.pop("bar_time"), errors="coerce")
    except (TypeError, ValueError) as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"bars[{symbol!r}] carries bar_time values that cannot share one frame: {exc}",
            **_identity(request),
        ) from exc
    if stamps.isna().any():
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"bars[{symbol!r}] carries an unparseable bar_time",
            **_identity(request),
        )
    frame.index = pd.DatetimeIndex(stamps, name="bar_time")
    return frame[list(CANONICAL_COLUMNS)].astype("float64")


def _replay_timeline(
    request: BarStepRequest, state: BarEngineState, frames: Mapping[str, pd.DataFrame]
) -> BarTimeline:
    """The session timeline from the CLOSED injected bars only (a future bar is never read)."""
    label = _label_of(request)
    feed = ReplayFeed(
        frames={symbol: {request.trade_date: frame} for symbol, frame in frames.items()}
    )
    symbols = _symbol_universe(state, request)
    kept: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for symbol in symbols:
        try:
            kept[symbol] = feed.closed_bars(
                symbol=symbol, trade_date=request.trade_date, as_of=label
            )
        except FeedError as exc:
            if exc.code == DATA_MISSING:
                missing.append(symbol)
                continue
            raise SessionRunnerError(
                CODE_INVALID_REQUEST,
                f"5m bars for {symbol} on {request.trade_date}: {exc}",
                **_identity(request),
            ) from exc

    facts = FiveMinuteFacts(
        bars_by_symbol={symbol: {request.trade_date: frame} for symbol, frame in kept.items()},
        observed_days_by_symbol={symbol: (request.trade_date,) for symbol in kept},
        missing_symbols=tuple(missing),
    )
    try:
        return build_timeline(facts, request.trade_date, symbols=symbols)
    except (TimelineError, ValueError) as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"5m bars for {request.trade_date} cannot form a session timeline: {exc}",
            **_identity(request),
        ) from exc


def _symbol_universe(state: BarEngineState, request: BarStepRequest) -> tuple[str, ...]:
    """Every symbol this bar may price (mirrors the session runner's universe)."""
    symbols = {row.symbol for row in state.positions}
    symbols.update(row.symbol for row in state.book.rows())
    symbols.update(row.symbol for row in request.open_orders)
    symbols.update(row.symbol for row in request.account_state.positions)
    symbols.update(
        action.symbol
        for action in (*request.order_actions, *request.replace_orders)
        if action.symbol
    )
    return tuple(sorted(symbols))


# ── live guards ──────────────────────────────────────────────────────────


def _require_cn_a_5m(request: BarStepRequest, stored: PaperTradeAccount) -> None:
    identity = _identity(request)
    if request.market != stored.market:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"request market {request.market!r} disagrees with the account's "
            f"{stored.market!r}",
            **identity,
        )
    if stored.market != "cn_a" or request.interval != "5m" or request.session not in (
        "cn_a",
        "auto",
    ):
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            "the pt-v1.3 bar surface is cn_a 5m only, got "
            f"market={stored.market!r} session={request.session!r} "
            f"interval={request.interval!r}",
            **identity,
        )


def _label_of(request: BarStepRequest) -> str:
    try:
        canonical = parse_bar_time(request.bar_time, trade_date=request.trade_date)
    except PaperTradeSchemaError as exc:
        raise SessionRunnerError(CODE_INVALID_REQUEST, str(exc), **_identity(request)) from exc
    label = canonical[11:16]
    if label not in bar_clock.BAR_LABELS:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"bar_time {request.bar_time!r} names {label}, which is not one of the "
            "48 continuous session bars",
            **_identity(request),
        )
    return label


def _progress_refusal(
    stored: PaperTradeAccount, request: BarStepRequest, label: str
) -> SessionRunnerError | None:
    """The idempotency answer, computed before the stale-carrier check."""
    identity = _identity(request)
    if stored.watermark is not None and request.trade_date < stored.watermark:
        return SessionRunnerError(
            CODE_BAR_ALREADY_APPLIED,
            f"{request.trade_date} precedes the account watermark {stored.watermark}; "
            "a closed day is never re-applied",
            **identity,
        )
    watermark_bar = stored.watermark_bar
    if stored.watermark is not None and stored.watermark != request.trade_date:
        watermark_bar = ""
    index = bar_clock.BAR_LABELS.index(label)
    if watermark_bar and index <= bar_clock.BAR_LABELS.index(watermark_bar):
        return SessionRunnerError(
            CODE_BAR_ALREADY_APPLIED,
            f"bar {label} is at or before the account watermark_bar {watermark_bar!r}; "
            "an applied bar is never re-applied",
            **identity,
        )
    expected = (
        bar_clock.BAR_LABELS[0]
        if not watermark_bar
        else (bar_clock.next_bar(watermark_bar) or bar_clock.BAR_LABELS[0])
    )
    if label != expected:
        return SessionRunnerError(
            CODE_BAR_OUT_OF_ORDER,
            f"bar {label} is not the next session bar; expected {expected} after "
            f"{watermark_bar or 'the session open'} on {request.trade_date}",
            **identity,
        )
    return None


def _stale_detail(request: BarStepRequest, stored: PaperTradeAccount) -> str:
    """The first carried field that disagrees with the store, or ``""``."""
    carried_cash = _money(request.account_state.cash)
    stored_cash = _money(stored.cash)
    if carried_cash != stored_cash:
        return (
            f"account_state.cash carried {carried_cash} but the store holds {stored_cash}"
        )
    carried_positions = _position_tuples(request.account_state.positions)
    stored_positions = _position_tuples(stored.positions)
    if carried_positions != stored_positions:
        return (
            f"account_state.positions carried {carried_positions!r} but the store "
            f"holds {stored_positions!r}"
        )
    if tuple(request.open_orders) != tuple(stored.open_orders):
        return "open_orders disagrees with the store's resting book"
    if request.order_seq != stored.order_seq:
        return (
            f"order_seq carried {request.order_seq} but the store holds {stored.order_seq}"
        )
    return ""


def _position_tuples(positions: Any) -> tuple[tuple[str, Decimal, Decimal], ...]:
    return tuple(
        (row.symbol, _money(row.qty), _money(row.avg_cost))
        for row in sorted(positions, key=lambda item: item.symbol)
    )


def _money(value: Any) -> Decimal:
    return Decimal(str(value))


# ── price bands (the optional caller input) ──────────────────────────────


def _bands_from_prev_closes(
    request: BarStepRequest, timeline: BarTimeline
) -> dict[str, tuple[float, float]]:
    """The day's band table from the request's closes; an absent entry is UNKNOWN.

    ``prev_closes`` is the ONLY band input a replay can carry (it has no store
    and no limits client), so a symbol it does not name keeps an unknown band
    and the band slots are skipped rather than fabricated.
    """
    closes = request.prev_close_yuan()
    if not closes:
        return {}
    label = _label_of(request)
    bands: dict[str, tuple[float, float]] = {}
    for symbol in timeline.symbols:
        prev_close = closes.get(symbol)
        if prev_close is None:
            continue
        try:
            band = band_for(timeline.row(label, symbol), prev_close=prev_close)
        except (FillRuleError, TimelineError) as exc:
            raise SessionRunnerError(
                CODE_INVALID_REQUEST, f"price band for {symbol}: {exc}", **_identity(request)
            ) from exc
        if band is not None:
            bands[symbol] = (float(band[0]), float(band[1]))
    return bands


def _prev_closes_for(
    request: BarStepRequest, feed: FiveMinuteFeed
) -> dict[str, float] | None:
    """Yuan closes for the band heuristic: explicit request values first.

    When the request carries no ``prev_closes`` the feed is asked only if it
    exposes the optional ``previous_close(symbol=..., trade_date=...)`` seam.
    No shipped feed does, so the band stays UNKNOWN and the band slots are
    skipped (the documented fail-closed policy) — a close is never invented.
    A ``data_missing`` answer is an unknown close; any other feed fault is
    refused as ``INVALID_REQUEST``.
    """
    explicit = request.prev_close_yuan()
    if explicit:
        return explicit
    lookup = getattr(feed, "previous_close", None)
    if not callable(lookup):
        return None
    symbols = _symbol_universe(_carried_state(request), request)
    closes: dict[str, float] = {}
    for symbol in symbols:
        try:
            value = lookup(symbol=symbol, trade_date=request.trade_date)
        except FeedError as exc:
            if exc.code == DATA_MISSING:
                continue
            raise SessionRunnerError(
                CODE_INVALID_REQUEST,
                f"previous close for {symbol} on {request.trade_date}: {exc}",
                **_identity(request),
            ) from exc
        if value is not None:
            closes[symbol] = float(value)
    return closes or None


# ── the live feed ────────────────────────────────────────────────────────


def _feed_for(request: BarStepRequest) -> FiveMinuteFeed:
    if request.bars:
        frames = _frames_from_bars(request)
        return ReplayFeed(
            frames={symbol: {request.trade_date: frame} for symbol, frame in frames.items()}
        )
    return _LiveFeed((TushareFeed(), EastmoneyFeed()))


class _LiveFeed:
    """Tushare first, the free Eastmoney 5m source when it cannot serve."""

    def __init__(self, adapters: tuple[FiveMinuteFeed, ...]) -> None:
        self._adapters = adapters

    def day_bars(self, *, symbol: str, trade_date: str) -> pd.DataFrame:
        return self._first(
            lambda adapter: adapter.day_bars(symbol=symbol, trade_date=trade_date)
        )

    def closed_bars(self, *, symbol: str, trade_date: str, as_of: str) -> pd.DataFrame:
        return self._first(
            lambda adapter: adapter.closed_bars(
                symbol=symbol, trade_date=trade_date, as_of=as_of
            )
        )

    def _first(
        self, call: Callable[[FiveMinuteFeed], pd.DataFrame]
    ) -> pd.DataFrame:
        error: FeedError | None = None
        for adapter in self._adapters:
            try:
                return call(adapter)
            except FeedError as exc:
                error = exc
        if error is not None:
            raise error
        raise FeedError(DATA_MISSING, "no live 5m source is configured")


# ── status helpers + the in-band refusal ─────────────────────────────────


def _applied_bars(
    store: AccountStore, account: PaperTradeAccount, session_date: str
) -> int:
    bars_root = store.steps_dir(account.account_id) / session_date / "bars"
    if not bars_root.is_dir():
        return 0
    return sum(
        1 for entry in bars_root.iterdir() if entry.is_dir() and not entry.is_symlink()
    )


def _next_bar(watermark_bar: str) -> str:
    if not watermark_bar:
        return bar_clock.BAR_LABELS[0]
    return bar_clock.next_bar(watermark_bar) or bar_clock.BAR_LABELS[0]


def _error_document(payload: Mapping[str, Any], message: str) -> str:
    """The in-band AM-5 refusal (``INVALID_REQUEST``), code prefixed in ``message``."""
    envelope = ErrorEnvelope(
        code="INVALID_REQUEST",
        account_id=_text(payload.get("account_id")),
        market=_text(payload.get("market")),
        range=DateRange(from_="", to=""),
        message=message,
        received_state_hash=None,
    )
    return wire.dumps_payload(wire.build_error_payload(envelope))


def _identity(request: BarStepRequest) -> dict[str, str]:
    return {
        "account_id": request.account_id,
        "market": request.market,
        "from_date": request.from_date,
        "to_date": request.to_date,
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
