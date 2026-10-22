"""Per-bar order-action application for the pt-v1.3 engine (P1-16).

Split out of :mod:`bar_engine` to keep the loop readable and under the
800-line cap.  This module translates ONE bar's request channels into
:class:`~.orders.OrderBook` transitions, in this documented order:

1. ``request.order_actions`` in array order (``place`` / ``cancel`` /
   ``replace``);
2. ``request.cancel_order_ids`` in array order;
3. ``request.replace_orders`` in array order.

The channels cannot target the same order twice (the contract parser rejects
duplicates), so the order above is fixed rather than left to dict ordering.

An action that cannot be applied — an unknown verb, an ``OrderIdError``, an
unknown cancel/replace target, a duplicate ``order_id``/``client_order_id``,
or a terminal target — aborts the whole bar with
:class:`~.bar_state.BarEngineError`, which the caller maps to
``INVALID_REQUEST``.  The engine acts on a COPY of the carried book, so a
raise leaves the input state untouched; no partial application is exposed.
Such a failure is never a ``RejectedOrder``: the frozen AM-14 vocabulary has
no "invalid action" token, and ``unknown_symbol`` means "the instrument
cannot be resolved", not "the caller sent a bad action".  Execution
rejections (the AM-14 ladder) come from the engine's reject hook only.

A ``place`` that the client-id dedupe resolves consumes neither a new sequence
number nor a transition.

The module depends on the book and the contract shapes only — no engine state,
no ledger, no clock, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from .bar_state import BarEngineError
from .order_ids import OrderIdError, allocate_order_id
from .orders import InvalidTransition, OrderBook
from .schema import BarStepRequest, OrderAction, OrderState, RejectedOrder

__all__ = ["ActionContext", "ActionOutcome", "apply_actions"]


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Everything one action pass needs (groups the six loop locals)."""

    account_id: str
    trade_date: str
    label: str
    order_seq: int
    book: OrderBook
    request: BarStepRequest


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """The next free sequence plus the rows this pass transitioned.

    ``rejected`` is kept for API compatibility and is ALWAYS empty: an
    execution rejection is produced by the engine's reject hook (todo 17),
    never by action application — an invalid action raises
    :class:`BarEngineError` instead.
    """

    order_seq: int
    changed: tuple[OrderState, ...] = ()
    rejected: tuple[RejectedOrder, ...] = ()


def apply_actions(context: ActionContext) -> ActionOutcome:
    """Apply every action channel of one bar; a bad action raises ``BarEngineError``."""
    order_seq = context.order_seq
    changed: list[OrderState] = []

    for action in context.request.order_actions:
        if action.action == "place":
            order_seq = _place(context, action, order_seq, changed)
        elif action.action == "cancel":
            _cancel(context, action.order_id, action.client_order_id, changed)
        elif action.action == "replace":
            _replace(context, action, changed)
        else:
            _fail_action(
                context,
                action.action,
                InvalidTransition(f"unknown action {action.action!r}"),
                action,
            )
    for order_id in context.request.cancel_order_ids:
        _cancel(context, order_id, "", changed)
    for action in context.request.replace_orders:
        if action.action != "replace":
            _fail_action(
                context,
                "replace",
                InvalidTransition(
                    f"replace_orders requires action 'replace', got {action.action!r}"
                ),
                action,
            )
        _replace(context, action, changed)
    return ActionOutcome(order_seq=order_seq, changed=tuple(changed))


def _place(
    context: ActionContext,
    action: OrderAction,
    order_seq: int,
    changed: list[OrderState],
) -> int:
    try:
        order_id, next_seq = allocate_order_id(
            context.account_id, context.trade_date, context.label, order_seq
        )
    except OrderIdError as exc:
        _fail_action(context, "place", exc, action)
    try:
        row, created = context.book.place_or_get(
            action, order_id=order_id, seq=order_seq, bar_label=context.label
        )
    except (InvalidTransition, OrderIdError) as exc:
        _fail_action(context, "place", exc, action, order_id=order_id)
    if not created:
        # Client-id dedupe: the stored row is unchanged, so the id that was
        # formatted above is not consumed and the sequence does not advance.
        return order_seq
    changed.append(row)
    return next_seq


def _cancel(
    context: ActionContext, order_id: str, client_order_id: str, changed: list[OrderState]
) -> None:
    try:
        row = context.book.cancel(order_id=order_id, client_order_id=client_order_id)
    except InvalidTransition as exc:
        _fail_action(
            context, "cancel", exc, order_id=order_id, client_order_id=client_order_id
        )
    changed.append(row)


def _replace(
    context: ActionContext, action: OrderAction, changed: list[OrderState]
) -> None:
    try:
        row = context.book.replace(
            order_id=action.order_id,
            client_order_id=action.client_order_id,
            replace_qty=action.replace_qty,
            replace_limit=action.replace_limit,
        )
    except InvalidTransition as exc:
        _fail_action(context, "replace", exc, action)
    changed.append(row)


def _fail_action(
    context: ActionContext,
    verb: str,
    exc: InvalidTransition | OrderIdError,
    action: OrderAction | None = None,
    *,
    order_id: str = "",
    client_order_id: str = "",
) -> NoReturn:
    """Raise the deterministic action-level ``BarEngineError`` for one bar."""
    identifier = order_id or (action.order_id if action is not None else "")
    client = client_order_id or (action.client_order_id if action is not None else "")
    raise BarEngineError(
        f"{verb} action on bar {context.label} failed for "
        f"order_id={identifier!r} client_order_id={client!r}: {exc}"
    )
