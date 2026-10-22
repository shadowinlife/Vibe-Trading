"""Idempotent per-bar session runner — the ONLY writer of a bar step (P1-24).

One :func:`bar_step` call applies exactly ONE 5-minute bar to a persistent
account.  It is the orchestration half of the v1.3 engine (:mod:`bar_engine`
is the pure half): it owns the account lock, the session roll, the progress
gate, the timeline/band assembly, the immutable bar step, the atomic account
write and the day summary.  It reads no clock, fetches no network itself, and
synthesizes no ``bar_time`` — the caller injects both the instant (through
``request.bar_time``) and the data (through the feed).

Write order (the crash contract)::

    plan → bar step durable → account.json atomic → session.json replace

A crash before the step write leaves no evidence and the call is a clean
retry.  A crash AFTER it leaves the step as the record of what the bar did;
the next call recomputes the same bar from the same pre-bar account and
ADOPTS the orphan because the request/result hashes match — one step, one
set of fills, never a second application.  The account is never advanced
before its step is durable, and ``session.json`` (a re-derivable summary) is
written last so it can never claim a close the account has not made.

Progress gate (no writes on refusal)::

    request.trade_date <  account.watermark          -> bar_already_applied
    label            <= state.watermark_bar          -> bar_already_applied
    label            != the next session label       -> bar_out_of_order

The last rule rejects both a skipped bar and a bar ahead of the session, so a
session is always applied contiguously from ``09:35``.  The day-level
``watermark`` moves only on the ``15:00`` bar, together with the account's
bar state, which is what closes the session.

Session roll: when the account watermark names an EARLIER day, the engine
state is rebuilt for the requested day and ``watermark_bar`` / ``bought_today``
are cleared — a new session never inherits the previous session's bar
progress or its T+1 counter.  The resting book is still restored from the
account (the settlement at 15:00 expires day orders, so a closed session
starts flat).

Determinism: the same pre-bar account, request and feed reproduce the same
v1.3 document byte for byte, which is exactly what makes hash-equality a
sound adoption proof.  Nothing here reads a wall clock, a random source or
the environment.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from . import bar_clock, bar_store, session_summary
from .account import PaperTradeAccount
from .bar_engine import BarEngineError, BarEngineState, BarOutcome, apply_bar
from .bar_layout import BarStepNotFound, BarStoreError
from .bar_store import BarStep, BarStepContent
from .calendar import is_trading_day
from .data_5m import FiveMinuteFacts
from .feeds import DATA_MISSING, FeedError, FiveMinuteFeed
from .fill_rules import FillRuleError, band_for
from .limits_cn_a import CnALimitsClient
from .rejections import RejectionContext, make_reject_hook
from .rules import MarketRules
from .schema import (
    BarStepRequest,
    PaperTradeSchemaError,
    SlippageSpec,
    parse_bar_step_request,
    parse_bar_time,
)
from .session_documents import (
    CODE_ACCOUNT_LOCKED,
    CODE_ACCOUNT_NOT_FOUND,
    CODE_BAR_ALREADY_APPLIED,
    CODE_BAR_OUT_OF_ORDER,
    CODE_BAR_STEP_CONFLICT,
    CODE_INVALID_REQUEST,
    CODE_SKIPPED_NON_TRADING_DAY,
    SessionRunnerError,
    bar_document,
    request_hash,
)
from .slippage import SlippageError, spec_for_model
from .store import AccountLocked, AccountNotFound, AccountStore
from .timeline import BarTimeline, TimelineError, build_timeline

__all__ = [
    "BAR_STEP_STATUSES",
    "CODE_ACCOUNT_LOCKED",
    "CODE_ACCOUNT_NOT_FOUND",
    "CODE_BAR_ALREADY_APPLIED",
    "CODE_BAR_OUT_OF_ORDER",
    "CODE_BAR_STEP_CONFLICT",
    "CODE_INVALID_REQUEST",
    "CODE_SKIPPED_NON_TRADING_DAY",
    "STATUS_ADOPTED",
    "STATUS_ALREADY_APPLIED",
    "STATUS_APPLIED",
    "STATUS_DRY_RUN",
    "BarStepRun",
    "SessionRunnerError",
    "bar_request",
    "bar_step",
]

# ── stable status / code vocabulary ──

STATUS_APPLIED = "applied"
STATUS_ADOPTED = "adopted"
STATUS_DRY_RUN = "dry_run"
STATUS_ALREADY_APPLIED = "already_applied"

#: ``BarStepRun.status`` vocabulary.  ``already_applied`` is the value a caller
#: reports when it converts :data:`CODE_BAR_ALREADY_APPLIED` into a no-op
#: answer; :func:`bar_step` itself REFUSES the duplicate (no writes at all).
BAR_STEP_STATUSES: tuple[str, ...] = (
    STATUS_APPLIED,
    STATUS_ADOPTED,
    STATUS_DRY_RUN,
    STATUS_ALREADY_APPLIED,
)

_BAR_INDEX = {label: index for index, label in enumerate(bar_clock.BAR_LABELS)}

#: Header of a step bar file; the same convention ``engine_adapter`` uses for
#: the v1.2 step, so one reader understands both ledgers.
_BARS_HEADER = "bar_time,open,high,low,close,volume\n"


@dataclass(frozen=True, slots=True)
class BarStepRun:
    """The result of one :func:`bar_step` call.

    ``document`` is the pt-v1.3 success document text (the same bytes the
    ``paper_trade_step`` tool ships), so an MCP caller never re-serializes it.
    ``step_path`` is the bar-step directory that now holds the bar (written or
    adopted) and is ``None`` for a dry run.  ``outcome`` is the pure engine
    result for callers that need the typed rows (replay, evidence); ``account``
    is the post-call account for an apply/adopt and the unchanged input account
    for a dry run.
    """

    status: str
    outcome: BarOutcome | None
    account: PaperTradeAccount | None
    step_path: Path | None
    document: str

    def __post_init__(self) -> None:
        if self.status not in BAR_STEP_STATUSES:
            raise ValueError(
                f"status must be one of {BAR_STEP_STATUSES}, got {self.status!r}"
            )
        if not isinstance(self.document, str) or not self.document:
            raise ValueError("document must be the non-empty in-band JSON text")


# ── the entry point ──


def bar_step(
    store: AccountStore,
    request: BarStepRequest,
    feed: FiveMinuteFeed,
    *,
    limits: CnALimitsClient | None = None,
    prev_closes: Mapping[str, float] | None = None,
    calendar_client: Any | None = None,
    rules: MarketRules | None = None,
    dry_run: bool = False,
) -> BarStepRun:
    """Apply ONE bar to ``request.account_id``, idempotently.

    ``limits`` / ``prev_closes`` supply the day's cn_a band table (an absent
    band stays UNKNOWN — the documented fail-closed policy).  ``calendar_client``
    is the optional trading-calendar truth: a closed day is refused with
    :data:`CODE_SKIPPED_NON_TRADING_DAY` and nothing is written.  ``rules``
    overrides the market rule matrix (defaults to the request's market/fees).
    ``dry_run=True`` computes and returns the document without writing a step,
    an account or a summary.

    The account lock is NON-BLOCKING: a second writer gets
    :data:`CODE_ACCOUNT_LOCKED` immediately instead of queueing.  A calendar
    transport failure propagates unchanged — it is not a domain refusal.
    """
    identity = _identity(request)
    try:
        # Preflight outside the lock so a missing account is reported as such
        # (and not as a lock failure) before any lock file is touched.
        store.load(request.account_id)
    except AccountNotFound as exc:
        raise SessionRunnerError(CODE_ACCOUNT_NOT_FOUND, str(exc), **identity) from exc
    try:
        with store.lock(request.account_id):
            return _bar_step_locked(
                store,
                request,
                feed,
                limits=limits,
                prev_closes=prev_closes,
                calendar_client=calendar_client,
                rules=rules,
                dry_run=dry_run,
            )
    except AccountLocked as exc:
        raise SessionRunnerError(CODE_ACCOUNT_LOCKED, str(exc), **identity) from exc
    except AccountNotFound as exc:  # the directory vanished between load and lock
        raise SessionRunnerError(CODE_ACCOUNT_NOT_FOUND, str(exc), **identity) from exc


def _bar_step_locked(
    store: AccountStore,
    request: BarStepRequest,
    feed: FiveMinuteFeed,
    *,
    limits: CnALimitsClient | None,
    prev_closes: Mapping[str, float] | None,
    calendar_client: Any | None,
    rules: MarketRules | None,
    dry_run: bool,
) -> BarStepRun:
    """The locked load → compute → write → save sequence (see module docstring)."""
    identity = _identity(request)
    account = store.load(request.account_id)  # authoritative read under the lock
    if request.market != account.market:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"request market {request.market!r} disagrees with the account's "
            f"{account.market!r}",
            **identity,
        )
    # The account's stored execution model is the live default, so a
    # ``slippage_model="none"`` account really fills unslipped: the request's
    # default spec (fixed_bps 10 bps) must never silently win.  A request that
    # carries a DIFFERENT spec overrides it for this bar; an unresolvable
    # account model (fixed_amount stores no amount) is refused, never replaced.
    stored_model = account.slippage_model
    if stored_model and request.slippage == SlippageSpec():
        try:
            request = replace(request, slippage=spec_for_model(stored_model))
        except SlippageError as exc:
            raise SessionRunnerError(
                CODE_INVALID_REQUEST,
                f"account slippage_model {stored_model!r} cannot price a bar: {exc}",
                **identity,
            ) from exc
    bar_time = _canonical_bar_time(request)
    label = _label_of(request, bar_time)
    state = _session_state(account, request)
    _require_next_bar(account, state, label, request.trade_date, identity)
    if calendar_client is not None:
        _require_open_day(calendar_client, account.market, request.trade_date, identity)

    source = _timeline_source(feed, request, state, label, identity)
    bands = _bands(source.timeline, label, limits, prev_closes, identity)
    market_rules = rules if rules is not None else MarketRules(request.market, request.fee_model)
    outcome = _apply(state, request, source.timeline, bands, market_rules, identity)

    rendered = bar_document(outcome)
    document = rendered.text
    request_id = request_hash(request, bar_time)
    result_hash = rendered.result_hash

    root = store.account_dir(account.account_id)
    step = _read_step(root, request, label, identity)
    if step is not None:
        _require_same_hashes(step, request_id, result_hash, request.trade_date, label, identity)

    if dry_run:
        return BarStepRun(
            status=STATUS_DRY_RUN,
            outcome=outcome,
            account=account,
            step_path=None,
            document=document,
        )

    if step is None:
        step_path = _write_step(
            root, request, outcome, label, source, request_id, result_hash, identity
        )
        status = STATUS_APPLIED
    else:
        step_path = step.directory
        status = STATUS_ADOPTED

    # The step is durable: only now may the account carry the bar.  This save
    # is the only place the day watermark moves, and it happens last.
    advanced = _advanced_account(account, outcome, request, label, source.timeline)
    store.save_account_atomic(advanced)
    _write_summary(root, request, outcome, label, result_hash)
    return BarStepRun(
        status=status,
        outcome=outcome,
        account=advanced,
        step_path=step_path,
        document=document,
    )


# ── step 1/2: identity, session state, progress gate ──────────────────────


def _identity(request: BarStepRequest) -> dict[str, str]:
    return {
        "account_id": request.account_id,
        "market": request.market,
        "from_date": request.from_date,
        "to_date": request.to_date,
    }


def _canonical_bar_time(request: BarStepRequest) -> str:
    """The canonical ``+08:00`` spelling of ``request.bar_time``."""
    try:
        return parse_bar_time(
            request.bar_time, "bar_time", trade_date=request.trade_date
        )
    except PaperTradeSchemaError as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST, str(exc), **_identity(request)
        ) from exc


def _label_of(request: BarStepRequest, bar_time: str) -> str:
    """The session label of a canonical bar time (grid-checked)."""
    # ``parse_bar_time`` returns ``YYYY-MM-DDTHH:MM:SS+08:00``; slice the label
    # instead of re-parsing, so the runner and the engine cannot disagree.
    label = bar_time[11:16]
    if label not in _BAR_INDEX:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"bar_time {request.bar_time!r} names {label}, which is not one of the "
            "48 continuous session bars",
            **_identity(request),
        )
    return label


def _session_state(
    account: PaperTradeAccount, request: BarStepRequest
) -> BarEngineState:
    """The engine state for this day (``from_account`` owns the day decision).

    ``from_account`` restores the carried bar progress and T+1 counter only
    for the session day they belong to, so a NEW session starts empty here
    without a second reset that could also wipe a same-day resume.
    """
    try:
        return BarEngineState.from_account(account, trade_date=request.trade_date)
    except BarEngineError as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"account {account.account_id!r} cannot open a session on "
            f"{request.trade_date}: {exc}",
            **_identity(request),
        ) from exc


def _require_next_bar(
    account: PaperTradeAccount,
    state: BarEngineState,
    label: str,
    trade_date: str,
    identity: Mapping[str, str],
) -> None:
    """Refuse a duplicate, a skipped bar or a bar ahead of the session."""
    if account.watermark is not None and trade_date < account.watermark:
        raise SessionRunnerError(
            CODE_BAR_ALREADY_APPLIED,
            f"{trade_date} precedes the account watermark {account.watermark}; "
            "a closed day is never re-applied",
            **identity,
        )
    if state.watermark_bar and _BAR_INDEX[label] <= _BAR_INDEX[state.watermark_bar]:
        raise SessionRunnerError(
            CODE_BAR_ALREADY_APPLIED,
            f"bar {label} is at or before the account watermark_bar "
            f"{state.watermark_bar!r}; an applied bar is never re-applied",
            **identity,
        )
    expected = (
        bar_clock.BAR_LABELS[0]
        if not state.watermark_bar
        else (bar_clock.next_bar(state.watermark_bar) or bar_clock.BAR_LABELS[0])
    )
    if label != expected:
        raise SessionRunnerError(
            CODE_BAR_OUT_OF_ORDER,
            f"bar {label} is not the next session bar; expected {expected} after "
            f"{state.watermark_bar or 'the session open'} on {trade_date}",
            **identity,
        )


def _require_open_day(
    calendar_client: Any, market: str, trade_date: str, identity: Mapping[str, str]
) -> None:
    """Refuse a calendar-closed day without touching a single file."""
    try:
        open_day = is_trading_day(calendar_client, market, trade_date)
    except (NotImplementedError, ValueError) as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST, f"trading calendar: {exc}", **identity
        ) from exc
    if not open_day:
        raise SessionRunnerError(
            CODE_SKIPPED_NON_TRADING_DAY,
            f"{trade_date} is not a trading day for {market}; no bar is applied",
            **identity,
        )


# ── step 6/7: timeline, bands, the pure engine call ──────────────────────


@dataclass(frozen=True, slots=True)
class _TimelineSource:
    """The day's timeline plus the exact frames the step records."""

    timeline: BarTimeline
    frames: Mapping[str, Any]


def _timeline_source(
    feed: FiveMinuteFeed,
    request: BarStepRequest,
    state: BarEngineState,
    label: str,
    identity: Mapping[str, str],
) -> _TimelineSource:
    """Build the timeline from CLOSED bars only (a future bar is never read).

    A symbol whose frame is absent (``data_missing``) is carried as a MISSING
    row rather than failing the whole bar: that is exactly the halted /
    provider-omission state the timeline and the rejection ladder already
    model.  Any other feed fault refuses the bar.
    """
    symbols = _symbol_universe(state, request)
    if not symbols:
        # A flat account with no order surface has nothing to price; the
        # engine needs a timeline, and a zero-row one is the honest shape.
        return _TimelineSource(_empty_timeline(request.trade_date), {})

    frames: dict[str, Any] = {}
    missing: list[str] = []
    for symbol in symbols:
        try:
            frame = feed.closed_bars(
                symbol=symbol, trade_date=request.trade_date, as_of=label
            )
        except FeedError as exc:
            if exc.code == DATA_MISSING:
                missing.append(symbol)
                continue
            raise SessionRunnerError(
                CODE_INVALID_REQUEST,
                f"5m feed for {symbol} on {request.trade_date}: {exc}",
                **identity,
            ) from exc
        frames[symbol] = frame

    facts = FiveMinuteFacts(
        bars_by_symbol={
            symbol: {request.trade_date: frame} for symbol, frame in frames.items()
        },
        observed_days_by_symbol={symbol: (request.trade_date,) for symbol in frames},
        missing_symbols=tuple(missing),
    )
    try:
        timeline = build_timeline(facts, request.trade_date, symbols=symbols)
    except TimelineError as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"5m data for {request.trade_date} cannot form a session timeline: {exc}",
            **identity,
        ) from exc
    return _TimelineSource(timeline, frames)


def _symbol_universe(state: BarEngineState, request: BarStepRequest) -> tuple[str, ...]:
    """Every symbol whose bar this step may need to price.

    The ledger authority is ``state``; the request's carrier rows are included
    too so a caller that sent them still gets a timeline row for each symbol it
    named (an absent row would be read as ``unknown_symbol``).
    """
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


def _empty_timeline(trade_date: str) -> BarTimeline:
    """A zero-symbol timeline: the 48 labels, no rows, no data read."""
    labels = bar_clock.BAR_LABELS
    return BarTimeline(
        trade_date=trade_date,
        bar_labels=labels,
        rows_by_bar=MappingProxyType({label: () for label in labels}),
        symbols=(),
    )


def _bands(
    timeline: BarTimeline,
    label: str,
    limits: CnALimitsClient | None,
    prev_closes: Mapping[str, float] | None,
    identity: Mapping[str, str],
) -> dict[str, tuple[float, float]]:
    """The day's ``symbol -> (lower, upper)`` table; an unknown band stays absent."""
    closes = prev_closes or {}
    bands: dict[str, tuple[float, float]] = {}
    for symbol in timeline.symbols:
        try:
            band = band_for(
                timeline.row(label, symbol), limits, prev_close=closes.get(symbol)
            )
        except FillRuleError as exc:
            raise SessionRunnerError(
                CODE_INVALID_REQUEST, f"price band for {symbol}: {exc}", **identity
            ) from exc
        if band is not None:
            bands[symbol] = band
    return bands


def _apply(
    state: BarEngineState,
    request: BarStepRequest,
    timeline: BarTimeline,
    bands: Mapping[str, tuple[float, float]],
    rules: MarketRules,
    identity: Mapping[str, str],
) -> BarOutcome:
    """Run the pure engine for exactly one bar, mapping its refusals."""
    context = RejectionContext(
        trading_day=True,
        suspended_symbols=frozenset(),
        corporate_action_unresolved=frozenset(),
        bands=bands or {},
        rules=rules,
        path_model=request.path_model,
    )
    try:
        return apply_bar(
            state,
            request,
            timeline,
            bands=bands,
            reject_hook=make_reject_hook(context),
            rules=rules,
        )
    except BarEngineError as exc:
        raise SessionRunnerError(CODE_INVALID_REQUEST, str(exc), **identity) from exc


# ── step 8: the identity hashes ──────────────────────────────────────────


def _require_same_hashes(
    step: BarStep,
    request_hash: str,
    result_hash: str,
    trade_date: str,
    label: str,
    identity: Mapping[str, str],
) -> None:
    """Adopt only an EXACT match; a differing hash is a refused conflict.

    A stored step is evidence, never a draft: rewriting it would disconnect the
    ledger from its own history, and applying it as-is would apply a different
    bar than the caller asked for.
    """
    differing = [
        name
        for name, stored, recomputed in (
            ("request_hash", step.request_hash, request_hash),
            ("result_hash", step.result_hash, result_hash),
        )
        if stored != recomputed
    ]
    if differing:
        raise SessionRunnerError(
            CODE_BAR_STEP_CONFLICT,
            f"bar step {label} on {trade_date} already exists but "
            f"{' and '.join(differing)} differ(s) from this call; a bar step is "
            "never overwritten",
            **identity,
        )


# ── step 10/11: durable step, account, summary ───────────────────────────


def _read_step(
    root: Path, request: BarStepRequest, label: str, identity: Mapping[str, str]
) -> BarStep | None:
    try:
        return bar_store.read_bar_step(root, request.trade_date, label)
    except BarStepNotFound:
        return None
    except BarStoreError as exc:
        raise SessionRunnerError(
            CODE_BAR_STEP_CONFLICT,
            f"the stored bar step {label} on {request.trade_date} cannot be "
            f"verified: {exc}",
            **identity,
        ) from exc


def _write_step(
    root: Path,
    request: BarStepRequest,
    outcome: BarOutcome,
    label: str,
    source: _TimelineSource,
    request_hash: str,
    result_hash: str,
    identity: Mapping[str, str],
) -> Path:
    """Write the immutable bar step BEFORE any account byte is replaced."""
    content = BarStepContent(
        account_id=request.account_id,
        trade_date=request.trade_date,
        bar_label=label,
        bar_time=outcome.bar_result.bar_time,
        request_hash=request_hash,
        result_hash=result_hash,
        orders=outcome.state.book.rows(),
        fills=outcome.fills,
        order_events=outcome.order_events,
        positions=outcome.state.positions,
        events=outcome.session_events,
        bars=_bar_csvs(source, label),
    )
    try:
        return bar_store.write_bar_step(root, content)
    except BarStoreError as exc:
        raise SessionRunnerError(
            CODE_BAR_STEP_CONFLICT,
            f"bar step {label} on {request.trade_date} cannot be written: {exc}",
            **identity,
        ) from exc


def _bar_csvs(source: _TimelineSource, label: str) -> dict[str, str]:
    """The bar row this step consumed, one CSV per symbol (header when absent)."""
    bars: dict[str, str] = {}
    for symbol in source.timeline.symbols:
        frame = source.frames.get(symbol)
        if frame is None or frame.empty:
            bars[symbol] = _BARS_HEADER
            continue
        consumed = frame[frame.index.strftime("%H:%M") == label]
        bars[symbol] = _BARS_HEADER if consumed.empty else consumed.to_csv()
    return bars


def _advanced_account(
    account: PaperTradeAccount,
    outcome: BarOutcome,
    request: BarStepRequest,
    label: str,
    timeline: BarTimeline,
) -> PaperTradeAccount:
    """The account advanced to this bar (the day watermark closes the session).

    The 15:00 session-end settlement has already cleared ``bought_today`` on
    ``outcome.state``, so persisting this state persists the reset: the stored
    account never carries yesterday's counter into tomorrow.
    """
    advanced = outcome.state.to_account(
        account,
        session_phase=_session_phase(timeline, label),
        slippage_model=request.slippage.model,
        engine_fingerprint=outcome.bar_result.result.engine_fingerprint,
    )
    if bar_clock.is_last_bar(label):
        advanced = replace(advanced, watermark=request.trade_date)
    return advanced


def _session_phase(timeline: BarTimeline, label: str) -> str:
    phases = {row.phase for row in timeline.rows_for(label)}
    if len(phases) == 1:
        return next(iter(phases))
    return "" if phases else bar_clock.classify(label)


def _write_summary(
    root: Path,
    request: BarStepRequest,
    outcome: BarOutcome,
    label: str,
    result_hash: str,
) -> None:
    """Replace ``session.json`` with the session's progress and chain head.

    The summary carries the session's progress plus the final cash / position /
    resting-order sizes as ``cash_cents`` / ``positions`` / ``open_orders``.
    The per-bar counters are named ``bar_*`` because they describe THIS bar
    only — a per-bar step owns its own rows, and a summary that silently
    reported its last bar's fills as a session total would mislead.  The
    authoritative rows live in the bar steps and ``account.json``; this
    document is deliberately re-derivable and is written LAST, so it can never
    claim a close the account has not made.
    """
    applied = bar_clock.BAR_LABELS[: _BAR_INDEX[label] + 1]
    counts = {
        "bars": len(applied),
        "bar_fills": len(outcome.fills),
        "bar_rejections": len(outcome.rejected),
        "bar_canceled": len(outcome.bar_result.result.canceled_orders),
        "open_orders": len(outcome.state.book.open_orders()),
        "positions": len(outcome.state.positions),
        "cash_cents": int(outcome.state.cash * 100),
    }
    session_summary.write_session_summary(
        root,
        account_id=request.account_id,
        trade_date=request.trade_date,
        status="closed" if bar_clock.is_last_bar(label) else "open",
        applied_bars=applied,
        counts=counts,
        result_hash_chain_head=result_hash,
    )


# ── MCP wiring helper ────────────────────────────────────────────────────


def bar_request(payload: dict[str, Any], account: PaperTradeAccount) -> BarStepRequest:
    """Parse one pt-v1.3 payload and align its ledger carrier with ``account``.

    The wire payload is parsed by the ONE boundary parser
    (:func:`~.schema_v13.parse_bar_step_request`), then the carrier fields the
    engine ignores (``account_state`` / ``open_orders`` / ``order_seq``) are
    replaced with the stored account, so the document's ``received_state_hash``
    audits the ledger the step actually ran on.  A payload naming another
    account or market, or a surface other than the cn_a 5m one, is refused.
    """
    raw = payload if isinstance(payload, dict) else {}
    try:
        request = parse_bar_step_request(raw)
    except PaperTradeSchemaError as exc:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            str(exc),
            account_id=_raw_text(raw.get("account_id")),
            market=_raw_text(raw.get("market")),
            from_date=_raw_text(raw.get("from_date")),
            to_date=_raw_text(raw.get("to_date")),
        ) from exc
    identity = _identity(request)
    if request.account_id != account.account_id:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"request account_id {request.account_id!r} does not match the loaded "
            f"account {account.account_id!r}",
            **identity,
        )
    if request.market != account.market:
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            f"request market {request.market!r} does not match the account's "
            f"{account.market!r}",
            **identity,
        )
    if request.interval != "5m" or request.session != "cn_a":
        raise SessionRunnerError(
            CODE_INVALID_REQUEST,
            "the pt-v1.3 bar surface is cn_a 5m only, got "
            f"session={request.session!r} interval={request.interval!r}",
            **identity,
        )
    return replace(
        request,
        account_state=account.state(),
        open_orders=tuple(account.open_orders),
        order_seq=account.order_seq,
    )


def _raw_text(value: Any) -> str:
    return value if isinstance(value, str) else ""
