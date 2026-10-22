"""pt-v1.3 per-bar engine loop — one pure 5-minute state-machine step (P1-16).

One :func:`apply_bar` call applies exactly ONE bar.  It is the cohesive
per-bar unit the later runner/idempotency/replay todos (17-27) build on: no
I/O, no clock, no network, no global mutable state, and no bar aggregation.
The caller supplies the already-assembled :class:`~.timeline.BarTimeline`
(todo 9) and the authoritative ledger (:class:`BarEngineState`); a future bar
is never read, because every lookup is for ``request.bar_time``'s label.

Algorithm order (the documented contract; later todos extend the seams, never
re-order the loop)::

    1. resolve      label from bar_time; reject a label outside the 48-label
                    grid, a timeline/state for another trade date or account,
                    or a label at/before the state's watermark_bar.
    2. phase gate   require_matchable(row.phase): a non-continuous bar refuses
                    any order action; with no actions it returns a zero-fill
                    outcome carrying only that bar's phase events.
    3. copy book    OrderBook(...rows=state.book.rows()) so the INPUT state
                    (and its book) is never mutated.
    4. actions      place -> book.place_or_get with the deterministic id;
                    cancel/replace via the book.  A bad action is a
                    BarEngineError and the whole bar is refused
                    (bar_actions.py); no partial application is exposed.
5. reject hook  (SEAM 1) runs per still-``new`` order BEFORE pricing; a
                     returned frozen REASON_CODES token rejects the order.  A
                     symbol with no row at this label reaches the hook as
                     ``bar_row=None`` so it can emit ``unknown_symbol``.
    6. match        per order: reference from the path model, then
                    fill_rules.fill_decision.  ``price is None`` rests (no
                    fill, no rejection) — including a band-blocked price;
                    todo 17 owns terminal rejections.  A buy over cash and a
                    sell over the sellable quantity ALSO rest: the ledger can
                    never go negative.  Approved orders are booked by ONE
                    OrderBook.match_bar call (all-or-none) and then settled
                    (bar_settlement.py).
    7. bar end      book.end_of_bar terminates unfilled IOC/FOK.
    8. session end  on the last bar (15:00) only: the hook (default
                    :func:`~.session_settlement.settle_session`) expires
                    resting day orders and resets ``bought_today``; its
                    transitions are reported too.
    9. events       book.events_for(changed rows) + bar_events for this label
                    (phase rows, per-bar halted/price_limit, and the day-level
                    auction_only_halted summary on the closing bar).
   10. document     compose the v1.3 BarStepResult from the v1.2 result shape.
   11. purity       equal inputs -> equal outcomes; the input state is intact.

The two documented seams (todos 17/19 supply the real behaviour)::

    RejectHook     = Callable[[OrderState, BarRow | None, BarEngineState], str | None]
    SessionEndHook = Callable[[BarEngineState, OrderBook, str], BarEngineState]

``RejectHook`` runs for every still-``new`` order, in ``(seq, order_id)``
order, after this bar's actions and before any pricing.  It receives the
order row, its bar row (``None`` when the symbol has no row at this label —
the ``unknown_symbol`` case), and a snapshot state whose ``book`` is the
post-action book and whose ledger is still pre-fill.  It must return a frozen
:data:`~.schema.REASON_CODES` token (the order is then terminated ``rejected``)
or ``None`` (price it normally); it must not mutate the arguments.  When the
hook is ``None`` (the default) nothing is rejected before pricing and a
missing symbol row stays fail-closed as a :class:`BarEngineError`.

``SessionEndHook`` runs only when ``bar_time`` names the last bar (``15:00``),
after matching and after ``end_of_bar``.  It must apply its transitions to the
PASSED book (the engine captures them by row identity) and return a state
referencing that same book; it may rewrite cash/positions/bought_today/
order_seq.  When the hook is ``None`` (the default) the engine runs
:func:`~.session_settlement.settle_session`: every resting ``day`` order
expires and ``bought_today`` is cleared so the next session starts with the
T+1 counter reset.  An explicitly supplied hook replaces that default and owns
settlement itself.  A hook that returns a different book or identity is a
:class:`BarEngineError`, never a half-applied bar.

Ledger authority: ``state`` WINS over the request's ``account_state`` /
``open_orders`` / ``order_seq``.  Those request fields are the wire carrier the
caller used to build this state and remain covered by the request-side hash;
the engine reads only the state, so a replay cannot be nudged by a stale
request snapshot.  ``received_state_hash`` still hashes the request's snapshot
(the RM audit pre-image) exactly as the v1.2 document does.

A malformed order ACTION is :class:`BarEngineError` (see :mod:`bar_actions`),
exactly like a malformed BAR (bad label/date, a non-continuous bar carrying
actions, a symbol missing from the timeline, a bad band); the caller maps both
to ``INVALID_REQUEST``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, NoReturn, Sequence

from . import bar_clock, state_hash
from .account import account_state_payload
from .bar_actions import ActionContext, apply_actions
from .bar_settlement import BarLedger, sell_proceeds
from .bar_state import BarEngineError, BarEngineState
from .fill_rules import FillRuleError, fill_decision
from .orders import InvalidTransition, OrderBook
from .rules import MarketRules, money
from .schema import (
    SCHEMA_VERSION_V1_2,
    AccountState,
    BarStepRequest,
    BarStepResult,
    CanceledOrder,
    DataProvenance,
    DateRange,
    Fill,
    NavPoint,
    OrderState,
    PaperTradeSchemaError,
    PaperTradeStepResult,
    Position,
    RejectedOrder,
    SessionEvent,
    parse_bar_time,
)
from .session_events import (
    PhaseGateError,
    SessionEventContext,
    bar_events,
    require_matchable,
)
from .session_settlement import settle_session
from .slippage import SlippageError
from .slippage import describe as describe_slippage
from .timeline import BarRow, TimelineError, tradable

__all__ = [
    "BarEngineError",
    "BarEngineState",
    "BarOutcome",
    "RejectHook",
    "SessionEndHook",
    "apply_bar",
]

RejectHook = Callable[[OrderState, BarRow | None, "BarEngineState"], str | None]
SessionEndHook = Callable[["BarEngineState", OrderBook, str], "BarEngineState"]


def _fail(message: str) -> NoReturn:
    raise BarEngineError(message)


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """Everything one bar produced: the NEW state plus its wire documents."""

    state: BarEngineState
    fills: tuple[Fill, ...]
    rejected: tuple[RejectedOrder, ...]
    order_events: tuple[dict[str, Any], ...]
    session_events: tuple[SessionEvent, ...]
    bar_result: BarStepResult
    session_complete: bool


# ── the loop ──────────────────────────────────────────────────────────────


def apply_bar(
    state: BarEngineState,
    request: BarStepRequest,
    timeline: Any,
    *,
    bands: Mapping[str, tuple[float, float]] | None = None,
    reject_hook: RejectHook | None = None,
    session_end_hook: SessionEndHook | None = None,
    rules: MarketRules | None = None,
    events_context: SessionEventContext | None = None,
) -> BarOutcome:
    """Apply ONE bar; see the module docstring for the exact algorithm order."""
    _require_inputs(state, request, reject_hook, session_end_hook)
    bar_time = _canonical_bar_time(request)
    label = _label_of(bar_time)
    if label not in bar_clock.BAR_LABELS:
        _fail(f"bar label {label!r} is not one of the 48-label session grid")
    if state.watermark_bar and (
        bar_clock.BAR_LABELS.index(label) <= bar_clock.BAR_LABELS.index(state.watermark_bar)
    ):
        _fail(
            f"bar {label} is not after the account's watermark_bar {state.watermark_bar!r}; "
            "an applied bar can never be applied again"
        )
    if getattr(timeline, "trade_date", None) != request.trade_date:
        _fail(
            f"timeline trade_date {getattr(timeline, 'trade_date', None)!r} disagrees with "
            f"the request's {request.trade_date!r}"
        )
    if bands is not None and not isinstance(bands, Mapping):
        _fail(f"bands must be a mapping, got {type(bands).__name__}")
    effective_rules = _rules_for(request, rules)
    try:
        describe_slippage(request.slippage)
    except SlippageError as exc:
        _fail(f"invalid slippage spec: {exc}")
    if events_context is None:
        events_context = SessionEventContext(bands=bands, path_model=request.path_model)
    elif not isinstance(events_context, SessionEventContext):
        _fail(
            "events_context must be a SessionEventContext, "
            f"got {type(events_context).__name__}"
        )

    phase = _phase_for(timeline, label)
    if request.session_phase and request.session_phase != phase:
        _fail(
            f"request session_phase {request.session_phase!r} disagrees with the timeline's "
            f"{phase!r} for bar {label}"
        )
    try:
        require_matchable(phase, label=label)
    except PhaseGateError as exc:
        return _zero_fill_outcome(state, request, timeline, label, exc, events_context)

    book = _copy_book(state)
    ledger = BarLedger(state.trade_date, state.cash, state.positions, state.bought_today)
    actions = apply_actions(ActionContext(
        account_id=state.account_id,
        trade_date=state.trade_date,
        label=label,
        order_seq=state.order_seq,
        book=book,
        request=request,
    ))
    run = _BarRun(
        state=state, request=request, timeline=timeline, book=book, label=label,
        ledger=ledger, rules=effective_rules, bands=bands, order_seq=actions.order_seq,
    )
    run.changed.extend(actions.changed)
    run.reject_open_orders(reject_hook)
    run.match_orders()
    run.close_ioc_fok()

    session_complete = bar_clock.is_last_bar(label)
    if session_complete:
        hook_state = _make_state(
            state, cash=ledger.cash, positions=ledger.positions_tuple(), book=book,
            order_seq=run.order_seq, bought_today=ledger.bought_today, label=label,
        )
        if session_end_hook is not None:
            hook_state = _run_session_end_hook(hook_state, book, label, session_end_hook, run.changed)
        else:
            hook_state = _run_session_end_hook(hook_state, book, label, settle_session, run.changed)
        ledger.adopt(hook_state.cash, hook_state.positions, hook_state.bought_today)
        run.order_seq = hook_state.order_seq

    final_state = _make_state(
        state, cash=ledger.cash, positions=ledger.positions_tuple(), book=book,
        order_seq=run.order_seq, bought_today=ledger.bought_today, label=label,
    )
    nav = ledger.nav_point(timeline, label)
    order_events = book.events_for(run.changed, bar_time=bar_time)
    return _outcome(request, _Pieces(
        state=final_state,
        nav=nav,
        fills=tuple(ledger.fills),
        rejected=tuple(run.rejected),
        order_events=order_events,
        session_events=_session_events_for(timeline, label, context=events_context),
        canceled=tuple(ledger.canceled),
        warnings=tuple(dict.fromkeys(ledger.warnings)),
        session_complete=session_complete,
    ))


# ── step 1/2: inputs, phase gate, zero-fill early return ──────────────────


def _require_inputs(
    state: BarEngineState,
    request: BarStepRequest,
    reject_hook: RejectHook | None,
    session_end_hook: SessionEndHook | None,
) -> None:
    if not isinstance(state, BarEngineState):
        _fail(f"state must be a BarEngineState, got {type(state).__name__}")
    if not isinstance(request, BarStepRequest):
        _fail(f"request must be a BarStepRequest, got {type(request).__name__}")
    if state.account_id != request.account_id:
        _fail(f"state account_id {state.account_id!r} disagrees with the request's {request.account_id!r}")
    if state.trade_date != request.trade_date:
        _fail(f"state trade_date {state.trade_date!r} disagrees with the request's {request.trade_date!r}")
    if state.market != request.market:
        _fail(f"state market {state.market!r} disagrees with the request's {request.market!r}")
    for name, hook in (("reject_hook", reject_hook), ("session_end_hook", session_end_hook)):
        if hook is not None and not callable(hook):
            _fail(f"{name} must be callable or None, got {type(hook).__name__}")


def _canonical_bar_time(request: BarStepRequest) -> str:
    try:
        return parse_bar_time(request.bar_time, "bar_time", trade_date=request.trade_date)
    except PaperTradeSchemaError as exc:
        _fail(str(exc))


def _label_of(bar_time: str) -> str:
    stamp = datetime.fromisoformat(bar_time)
    return f"{stamp.hour:02d}:{stamp.minute:02d}"


def _phase_for(timeline: Any, label: str) -> str:
    try:
        rows = timeline.rows_for(label)
    except TimelineError as exc:
        _fail(f"bar {label} is absent from the timeline: {exc}")
    phases = {row.phase for row in rows}
    if len(phases) > 1:
        _fail(f"timeline rows for bar {label} disagree on their phase: {sorted(phases)}")
    return next(iter(phases)) if phases else bar_clock.classify(label)


def _rules_for(request: BarStepRequest, rules: MarketRules | None) -> MarketRules:
    if rules is None:
        rules = MarketRules(request.market, request.fee_model)
    if not isinstance(rules, MarketRules):
        _fail(f"rules must be a MarketRules, got {type(rules).__name__}")
    if rules.market != request.market:
        _fail(f"rules market {rules.market!r} disagrees with the request's {request.market!r}")
    return rules


def _zero_fill_outcome(
    state: BarEngineState,
    request: BarStepRequest,
    timeline: Any,
    label: str,
    gate_error: PhaseGateError,
    events_context: SessionEventContext,
) -> BarOutcome:
    """A non-continuous bar: actions are refused, phase events still reported."""
    if request.order_actions or request.cancel_order_ids or request.replace_orders:
        _fail(f"{gate_error}; an order action may only be applied on a continuous bar")
    book = _copy_book(state)
    ledger = BarLedger(state.trade_date, state.cash, state.positions, state.bought_today)
    final_state = _make_state(
        state, cash=ledger.cash, positions=ledger.positions_tuple(), book=book,
        order_seq=state.order_seq, bought_today=ledger.bought_today, label=label,
    )
    return _outcome(request, _Pieces(
        state=final_state,
        nav=ledger.nav_point(timeline, label),
        warnings=tuple(dict.fromkeys(ledger.warnings)),
        session_events=_session_events_for(timeline, label, context=events_context),
    ))


# ── step 3: book copy ────────────────────────────────────────────────────


def _copy_book(state: BarEngineState) -> OrderBook:
    try:
        return OrderBook(state.account_id, state.trade_date, rows=tuple(state.book.rows()))
    except InvalidTransition as exc:
        _fail(f"carried order book cannot be copied: {exc}")


# ── steps 5/6/7: reject hook, pricing, gates, booking, bar end ────────────


@dataclass(slots=True)
class _BarRun:
    """Mutable per-bar loop context (book, ledger, accumulator lists).

    Grouping the loop's collaborators here keeps every helper at one argument
    and makes the algorithm order in :func:`apply_bar` read literally.
    """

    state: BarEngineState
    request: BarStepRequest
    timeline: Any
    book: OrderBook
    label: str
    ledger: BarLedger
    rules: MarketRules
    bands: Mapping[str, tuple[float, float]] | None
    order_seq: int
    changed: list[OrderState] = field(default_factory=list)
    rejected: list[RejectedOrder] = field(default_factory=list)

    def reject_open_orders(self, hook: RejectHook | None) -> None:
        """SEAM 1: let ``hook`` terminate still-new orders before any pricing.

        A symbol absent from this bar's timeline is passed to the hook as
        ``bar_row=None`` (the ``unknown_symbol`` case).  Without a hook this
        method does nothing and :meth:`match_orders` stays fail-closed.
        """
        if hook is None:
            return
        snapshot = replace(self.state, book=self.book, order_seq=self.order_seq)
        for row in self.book.open_orders():
            bar_row = _optional_order_row(self.timeline, self.label, row.symbol)
            code = hook(row, bar_row, snapshot)
            if code is None:
                continue
            try:
                terminal = self.book.rejected(order_id=row.order_id, reason_code=code)
            except InvalidTransition as exc:
                _fail(f"reject_hook returned {code!r}, which cannot terminate order "
                      f"{row.order_id!r}: {exc}")
            self.changed.append(terminal)
            self.rejected.append(RejectedOrder(
                trade_date=self.state.trade_date,
                symbol=row.symbol,
                side=row.side,
                qty=row.qty,
                reason_code=code,
                detail=f"rejected before pricing at {self.label}",
            ))

    def match_orders(self) -> None:
        """Price every open order, gate it, then book ALL fills in one call."""
        rows = self.book.open_orders()
        prices: dict[str, Decimal] = {}
        for row in rows:
            bar_row = _order_row(self.timeline, self.label, row.symbol)
            if not tradable(bar_row):
                # No print this bar: RETRYABLE, so the order keeps resting and
                # a later traded bar prices it.  A terminal `suspended` here
                # would delete the book on an ordinary liquidity gap.
                self.ledger.warnings.append(
                    f"data_missing: {row.symbol} has no tradable {self.label} print; "
                    "the resting order was left new"
                )
                continue
            reference = _reference(bar_row, self.request.path_model)
            if reference is None:
                continue
            try:
                decision = fill_decision(
                    row, reference, self.request.slippage,
                    band=None if self.bands is None else self.bands.get(row.symbol),
                )
            except FillRuleError as exc:
                _fail(f"cannot price order {row.order_id!r} on bar {self.label}: {exc}")
            if decision.price is not None:
                prices[row.order_id] = decision.price

        approved = self._approve(rows, prices)
        # OrderBook prices through the shared ``_finite_number`` guard, which
        # accepts JSON numbers only; the Decimal money value is converted at
        # this single boundary and settlement below uses the Decimal original.
        matched = self.book.match_bar(
            lambda row: None if approved.get(row.order_id) is None
            else float(approved[row.order_id])
        )
        for row in matched:
            self.ledger.fill(row, approved[row.order_id], self.rules)
            self.changed.append(row)

    def _approve(
        self, rows: Sequence[OrderState], prices: Mapping[str, Decimal]
    ) -> dict[str, Decimal]:
        """Gate every priced order against a RUNNING cash/sellable ledger.

        An order that cannot settle simply rests: the loop never rejects here
        (todo 17's hook owns terminal rejections) and the ledger can never go
        negative — including for a sell whose fees exceed its notional.
        """
        running_cash = self.ledger.cash
        sellable: dict[str, Decimal] = {}
        approved: dict[str, Decimal] = {}
        for row in rows:
            price = prices.get(row.order_id)
            if price is None:
                continue
            qty = money(row.remaining_qty)
            if row.side == "buy":
                cost = self.rules.buy_cost(row.remaining_qty, float(price))
                if cost > running_cash:
                    continue
                running_cash -= cost
            else:
                if row.symbol not in sellable:
                    sellable[row.symbol] = self.ledger.sellable(
                        row.symbol, self.rules.t_plus_1
                    )
                available = sellable[row.symbol]
                if qty > available:
                    continue
                proceeds = sell_proceeds(qty, price, self.rules)
                if running_cash + proceeds < 0:
                    continue
                sellable[row.symbol] = available - qty
                running_cash += proceeds
            approved[row.order_id] = price
        return approved

    def close_ioc_fok(self) -> None:
        """Bar end: terminate this bar's unfilled IOC/FOK as canceled rows."""
        for row in self.book.end_of_bar(self.label):
            self.changed.append(row)
            self.ledger.canceled.append(CanceledOrder(
                trade_date=row.trade_date,
                symbol=row.symbol,
                side=row.side,
                qty=row.remaining_qty,
                detail=f"{row.tif}_unfilled at {self.label}",
            ))


def _reference(row: BarRow, path_model: str) -> float | None:
    """The bar reference for ``path_model``, or ``None`` when the bar has no price.

    A non-tradable row (no continuous print, or zero volume) has no reference:
    a no-trade bar never fills an order at a price the market did not print.
    """
    if not tradable(row):
        return None
    if path_model == "bar_median":
        return row.reference_median
    if path_model == "bar_close":
        return row.reference_close
    _fail(f"unknown path_model {path_model!r}")


def _order_row(timeline: Any, label: str, symbol: str) -> BarRow:
    try:
        return timeline.row(label, symbol)
    except TimelineError as exc:
        _fail(f"bar {label} has no row for symbol {symbol!r}: {exc}")


def _optional_order_row(timeline: Any, label: str, symbol: str) -> BarRow | None:
    """The bar row for ``symbol``, or ``None`` when the timeline has none."""
    try:
        return timeline.row(label, symbol)
    except TimelineError:
        return None


# ── step 8: session end seam ─────────────────────────────────────────────


def _run_session_end_hook(
    state: BarEngineState,
    book: OrderBook,
    label: str,
    hook: SessionEndHook,
    changed: list[OrderState],
) -> BarEngineState:
    before = {id(row) for row in book.rows()}
    result = hook(state, book, label)
    if not isinstance(result, BarEngineState):
        _fail(f"session_end_hook must return a BarEngineState, got {type(result).__name__}")
    if result.book is not book:
        _fail("session_end_hook must apply its transitions to the passed book")
    if (result.account_id, result.market, result.trade_date) != (
        state.account_id, state.market, state.trade_date
    ):
        _fail("session_end_hook must not change the state identity")
    for row in book.rows():
        if id(row) not in before:
            changed.append(row)
    return result


# ── steps 9/10: events + document composition ───────────────────────────


def _session_events_for(
    timeline: Any, label: str, *, context: SessionEventContext
) -> tuple[SessionEvent, ...]:
    """This bar's session events (todo 18's single per-bar emitter)."""
    return bar_events(timeline, label, context=context)


@dataclass(frozen=True, slots=True)
class _Pieces:
    """Everything one outcome needs; keeps the composer at one argument."""

    state: BarEngineState
    nav: NavPoint
    fills: tuple[Fill, ...] = ()
    rejected: tuple[RejectedOrder, ...] = ()
    order_events: tuple[dict[str, Any], ...] = ()
    session_events: tuple[SessionEvent, ...] = ()
    canceled: tuple[CanceledOrder, ...] = ()
    warnings: tuple[str, ...] = ()
    session_complete: bool = False


def _outcome(request: BarStepRequest, pieces: _Pieces) -> BarOutcome:
    state = pieces.state
    fee_model = state_hash.effective_fee_model(request.market, request.fee_model)
    result = PaperTradeStepResult(
        account_id=state.account_id,
        market=request.market,
        range=DateRange(from_=state.trade_date, to=state.trade_date),
        received_state_hash=state_hash.compute_received_state_hash(
            account_id=request.account_id,
            market=request.market,
            # The request's raw snapshot is the audit pre-image (not the
            # authoritative state); it is the value the caller sent.
            account_state=account_state_payload(request.account_state),
            fee_model=fee_model,
            from_date=request.from_date,
            to_date=request.to_date,
            interval=request.interval,
            session=request.session,
            path_model=request.path_model,
            fill_model=request.fill_model,
        ),
        effective_fee_model=fee_model,
        fills=pieces.fills,
        rejected_orders=pieces.rejected,
        end_state=AccountState(cash=float(state.cash), positions=state.positions),
        nav_series=(pieces.nav,),
        warnings=pieces.warnings,
        data_provenance=DataProvenance(source="paper_trade_bar_engine"),
        schema_version=SCHEMA_VERSION_V1_2,
        engine_fingerprint=state_hash.engine_fingerprint(
            interval=request.interval,
            path_model=request.path_model,
            fill_model=request.fill_model,
            slippage_model=request.slippage.model,
            order_model_version=state_hash.ORDER_MODEL_VERSION,
        ),
        canceled_orders=pieces.canceled,
        events=pieces.session_events,
    )
    bar = BarStepResult(
        result=result,
        bar_time=_canonical_bar_time(request),
        order_events=pieces.order_events,
        open_orders=state.book.open_orders(),
        order_seq=state.order_seq,
        slippage=request.slippage.as_wire(),
    )
    return BarOutcome(
        state=state,
        fills=pieces.fills,
        rejected=pieces.rejected,
        order_events=pieces.order_events,
        session_events=pieces.session_events,
        bar_result=bar,
        session_complete=pieces.session_complete,
    )


# ── shared validators ────────────────────────────────────────────────────


def _make_state(
    base: BarEngineState,
    *,
    cash: Decimal,
    positions: tuple[Position, ...],
    book: OrderBook,
    order_seq: int,
    bought_today: Mapping[str, Decimal],
    label: str,
) -> BarEngineState:
    return BarEngineState(
        account_id=base.account_id,
        market=base.market,
        trade_date=base.trade_date,
        cash=cash,
        positions=positions,
        book=book,
        order_seq=order_seq,
        bought_today=bought_today,
        watermark_bar=label,
    )
