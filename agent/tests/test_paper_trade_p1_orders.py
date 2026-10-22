"""Todo 11 gate — the pt-v1.3 order book lifecycle (all-or-none).

What this module pins (P1 intraday plan todo 11; closes GAP-2):

* every legal transition: place → rest → fill, cancel, replace, reject;
* every illegal one: a terminal row can never cancel/replace/reject/fill
  again, duplicate ids are refused, and a non-positive quantity or fill price
  never enters the book;
* TIF semantics: ``day`` rests across bars, ``ioc``/``fok`` terminate in the
  bar that received them with the stable ``ioc_unfilled``/``fok_unfilled``
  tokens (operationally identical because fills are all-or-none), and
  ``end_of_bar`` is idempotent;
* the all-or-none rule: a returned price fills the WHOLE remaining quantity,
  so a fill can never leave a fractional ``remaining_qty``;
* deterministic ``(seq, order_id)`` ordering for ``open_orders``/``rows``;
* the event rows the v1.3 wire will validate: exactly one per transition, in
  transition order, with the optional keys omitted when empty.

Deterministic ids and client-id dedupe live in
``test_paper_trade_p1_order_identity.py``: those tests were split out on todo
15 to keep this lifecycle module under the 800-line practical cap.

The book is pure: no clock, no I/O, no network, and it assigns no ids — the
caller passes ``order_id``/``seq`` (todo 12 owns the deterministic formula).

Lifecycle table (status × verb):

    new      --fill------> filled     (all-or-none, terminal)
    new      --cancel----> canceled   (terminal)
    new      --reject----> rejected   (terminal, frozen REASON_CODES only)
    new      --replace---> new        (same order_id/seq, one row)
    new(ioc) --bar end---> canceled   (reason ioc_unfilled)
    new(fok) --bar end---> canceled   (reason fok_unfilled)
    new(day) --bar end---> new        (rests; 15:00 expiry is todo 19)
    terminal --any-------> InvalidTransition
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from typing import Any

import pytest

from src.tools.paper_trade.orders import InvalidTransition, OrderBook
from src.tools.paper_trade.schema import OrderAction, OrderState

_ACCOUNT = "pa_p1_orders"
_DAY = "2026-10-20"
_BAR_TIME = "2026-10-20T09:35:00+08:00"
_REQUIRED_EVENT_KEYS = (
    "event_type",
    "order_id",
    "client_order_id",
    "symbol",
    "side",
    "qty",
    "bar_time",
)


def _action(
    *,
    symbol: str = "600519.SH",
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
    client_order_id: str = "",
    order_id: str = "",
    replace_qty: float | None = None,
    replace_limit: float | None = None,
    action: str = "place",
) -> OrderAction:
    return OrderAction(
        action=action,
        symbol=symbol,
        side=side,
        qty=qty,
        limit=limit,
        order_id=order_id,
        client_order_id=client_order_id,
        tif=tif,
        replace_qty=replace_qty,
        replace_limit=replace_limit,
    )


def _book(rows: tuple[OrderState, ...] = ()) -> OrderBook:
    return OrderBook(_ACCOUNT, _DAY, rows)


def _place(book: OrderBook, order_id: str = "ord-1", seq: int = 1, bar_label: str = "09:35", **kwargs: Any) -> OrderState:
    return book.place(_action(**kwargs), order_id=order_id, seq=seq, bar_label=bar_label)


def _row(**overrides: Any) -> OrderState:
    """A carried row, e.g. restored from persisted state (no transition history)."""
    fields: dict[str, Any] = {
        "order_id": "ord-restored",
        "account_id": _ACCOUNT,
        "trade_date": _DAY,
        "symbol": "600519.SH",
        "side": "buy",
        "qty": 100.0,
        "remaining_qty": 100.0,
        "limit": None,
        "tif": "day",
        "status": "new",
        "bar_label": "09:35",
        "client_order_id": "",
        "filled_qty": 0.0,
        "avg_fill_price": 0.0,
        "seq": 1,
    }
    fields.update(overrides)
    return OrderState(**fields)


def _at(price: float | None) -> Any:
    """A fill-price callable returning one constant price (or never marketable)."""
    return lambda row: price


def _assert_conserved(row: OrderState) -> None:
    assert row.filled_qty + row.remaining_qty == pytest.approx(row.qty)
    assert 0 <= row.remaining_qty <= row.qty
    if row.status == "filled":
        assert row.remaining_qty == 0
    if row.status == "new":
        assert row.remaining_qty > 0
    assert row.avg_fill_price >= 0


# ── Legal transitions ────────────────────────────────────────────────────────


def test_place_rests_then_fills_the_whole_order() -> None:
    book = _book()

    placed = _place(book, client_order_id="cli-1", limit=1500.0, seq=3)

    assert placed.status == "new"
    assert (placed.order_id, placed.account_id, placed.trade_date) == ("ord-1", _ACCOUNT, _DAY)
    assert (placed.qty, placed.remaining_qty, placed.filled_qty) == (100.0, 100.0, 0.0)
    assert placed.avg_fill_price == 0.0
    assert placed.bar_label == "09:35"
    assert placed.seq == 3
    assert placed.client_order_id == "cli-1"
    assert book.open_orders() == (placed,)
    assert book.rows() == (placed,)
    _assert_conserved(placed)

    fetched = book.match_bar(_at(1500.0))

    assert len(fetched) == 1
    filled = fetched[0]
    assert filled.status == "filled"
    assert (filled.qty, filled.filled_qty, filled.remaining_qty) == (100.0, 100.0, 0.0)
    assert filled.avg_fill_price == 1500.0
    assert filled.order_id == placed.order_id
    assert book.open_orders() == ()
    assert book.rows() == (filled,)
    _assert_conserved(filled)


def test_cancel_only_a_new_order() -> None:
    book = _book()
    _place(book, client_order_id="cli-cancel")

    canceled = book.cancel(client_order_id="cli-cancel", reason="client_request")

    assert canceled.status == "canceled"
    assert canceled.remaining_qty == 100.0
    assert book.open_orders() == ()
    assert book.rows() == (canceled,)

    with pytest.raises(InvalidTransition, match="canceled"):
        book.cancel(order_id="ord-1")


def test_replace_keeps_one_identity_then_fills() -> None:
    book = _book()
    placed = _place(book, limit=1500.0, seq=7)

    replaced = book.replace(order_id="ord-1", replace_qty=60.0, replace_limit=1499.5)

    assert replaced.order_id == placed.order_id
    assert replaced.seq == placed.seq
    assert replaced.status == "new"
    assert (replaced.qty, replaced.remaining_qty, replaced.filled_qty) == (60.0, 60.0, 0.0)
    assert replaced.limit == 1499.5
    assert replaced.side == "buy"
    assert replaced.symbol == "600519.SH"
    assert len(book.rows()) == 1
    assert book.open_orders() == (replaced,)
    _assert_conserved(replaced)

    filled = book.match_bar(_at(1499.5))
    assert len(filled) == 1
    assert filled[0].filled_qty == 60.0
    assert filled[0].remaining_qty == 0.0
    assert filled[0].avg_fill_price == 1499.5


def test_replace_can_target_a_client_order_id_and_change_only_the_limit() -> None:
    book = _book()
    _place(book, client_order_id="cli-r", qty=40.0)

    replaced = book.replace(client_order_id="cli-r", replace_limit=10.5)

    assert (replaced.qty, replaced.remaining_qty) == (40.0, 40.0)
    assert replaced.limit == 10.5


def test_replace_chain_keeps_one_row_and_the_last_requested_values() -> None:
    book = _book()
    placed = _place(book, qty=100.0, limit=1500.0, seq=4)

    first = book.replace(order_id="ord-1", replace_qty=80.0)
    second = book.replace(order_id="ord-1", replace_qty=40.0, replace_limit=1498.0)

    assert (first.order_id, second.order_id) == (placed.order_id, placed.order_id)
    assert (first.seq, second.seq) == (placed.seq, placed.seq)
    assert (second.qty, second.remaining_qty, second.filled_qty) == (40.0, 40.0, 0.0)
    assert second.limit == 1498.0
    assert book.rows() == (second,)
    assert book.open_orders() == (second,)
    _assert_conserved(second)

    events = book.events_for((placed, first, second), bar_time=_BAR_TIME)
    assert [event["event_type"] for event in events] == ["placed", "replaced", "replaced"]
    assert [event["qty"] for event in events] == [100.0, 80.0, 40.0]


def test_replace_then_cancel_terminates_the_same_row() -> None:
    book = _book()
    placed = _place(book, client_order_id="cli-replace-cancel", limit=1500.0)

    replaced = book.replace(order_id="ord-1", replace_qty=60.0, replace_limit=1499.0)
    canceled = book.cancel(client_order_id="cli-replace-cancel", reason="client_request")

    assert (canceled.order_id, canceled.seq) == (placed.order_id, placed.seq)
    assert canceled.status == "canceled"
    assert (canceled.qty, canceled.remaining_qty) == (60.0, 60.0)
    assert canceled.limit == 1499.0
    _assert_conserved(canceled)
    assert book.rows() == (canceled,)
    assert book.open_orders() == ()
    assert [event["event_type"] for event in book.events_for((replaced, canceled), bar_time=_BAR_TIME)] == [
        "replaced",
        "canceled",
    ]


def test_replace_cannot_change_side_or_symbol() -> None:
    book = _book()
    placed = _place(book)

    replaced = book.replace(order_id="ord-1", replace_qty=50.0)

    assert (replaced.side, replaced.symbol) == (placed.side, placed.symbol)
    with pytest.raises(TypeError):
        book.replace(order_id="ord-1", replace_qty=50.0, side="sell")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        book.replace(order_id="ord-1", replace_qty=50.0, symbol="000001.SZ")  # type: ignore[call-arg]


def test_reject_records_a_frozen_reason_and_terminates() -> None:
    book = _book()
    _place(book, client_order_id="cli-reject")

    rejected = book.rejected(client_order_id="cli-reject", reason_code="insufficient_cash")

    assert rejected.status == "rejected"
    assert book.open_orders() == ()
    assert book.rows() == (rejected,)

    with pytest.raises(InvalidTransition):
        book.rejected(order_id="ord-1", reason_code="insufficient_cash")
    with pytest.raises(InvalidTransition, match="outside the frozen REASON_CODES"):
        book.rejected(order_id="nope", reason_code="made_up")


def test_reject_rejects_a_vocabulary_token_it_does_not_own() -> None:
    book = _book()
    _place(book)

    with pytest.raises(InvalidTransition, match="REASON_CODES"):
        book.rejected(order_id="ord-1", reason_code="ioc_unfilled")


# ── Terminal states never transition again ──────────────────────────────────


@pytest.mark.parametrize(
    ("terminal_status", "filled", "remaining"),
    [
        ("filled", 100.0, 0.0),
        ("canceled", 0.0, 100.0),
        ("rejected", 0.0, 100.0),
        ("expired", 0.0, 100.0),
    ],
)
def test_terminal_orders_refuse_cancel_and_replace(
    terminal_status: str, filled: float, remaining: float
) -> None:
    terminal = _row(status=terminal_status, filled_qty=filled, remaining_qty=remaining)
    book = _book((terminal,))

    with pytest.raises(InvalidTransition, match=terminal_status):
        book.cancel(order_id="ord-restored")
    with pytest.raises(InvalidTransition, match=terminal_status):
        book.replace(order_id="ord-restored", replace_qty=50.0)
    with pytest.raises(InvalidTransition, match=terminal_status):
        book.rejected(order_id="ord-restored", reason_code="insufficient_cash")

    assert book.rows() == (terminal,)
    assert book.open_orders() == ()


def test_cancel_after_fill_raises_invalid_transition() -> None:
    book = _book()
    _place(book)
    filled = book.match_bar(_at(1500.0))[0]

    with pytest.raises(InvalidTransition, match="filled"):
        book.cancel(order_id="ord-1")

    assert book.rows() == (filled,)
    assert book.open_orders() == ()


def test_a_terminal_row_can_never_be_filled_twice() -> None:
    book = _book()
    _place(book)
    first = book.match_bar(_at(1500.0))
    assert len(first) == 1

    second = book.match_bar(_at(1600.0))

    assert second == ()
    assert book.rows() == (first[0],)


def test_filled_order_ignores_a_later_bar_price() -> None:
    book = _book()
    _place(book, tif="ioc")
    book.match_bar(_at(1500.0))

    assert book.end_of_bar("09:35") == ()
    assert book.open_orders() == ()


_TERMINAL_ROWS: dict[str, dict[str, Any]] = {
    "filled": {"filled_qty": 100.0, "remaining_qty": 0.0, "avg_fill_price": 1500.0},
    "canceled": {"filled_qty": 0.0, "remaining_qty": 100.0},
    "rejected": {"filled_qty": 0.0, "remaining_qty": 100.0},
    "expired": {"filled_qty": 0.0, "remaining_qty": 100.0},
}


@pytest.mark.parametrize("status", sorted(_TERMINAL_ROWS))
@pytest.mark.parametrize("verb", ["place", "fill", "cancel", "replace", "reject", "expire"])
def test_every_terminal_status_refuses_every_verb(status: str, verb: str) -> None:
    """The status x verb half of the lifecycle table: terminal is final for ALL verbs.

    ``place`` never targets a row, so replaying it under a terminal id is
    refused as a duplicate id rather than as a transition.  ``expire`` is the
    only bar-end verb the P1 book exposes (15:00 expiry is todo 19): it must
    leave every terminal row — ``expired`` included — untouched.
    """
    terminal = _row(status=status, **_TERMINAL_ROWS[status])
    book = _book((terminal,))

    if verb == "place":
        with pytest.raises(InvalidTransition, match="duplicate order_id"):
            _place(book, order_id="ord-restored")
    elif verb == "fill":
        assert book.match_bar(_at(1500.0)) == ()
    elif verb == "expire":
        assert book.end_of_bar("09:35") == ()
    elif verb == "cancel":
        with pytest.raises(InvalidTransition, match=f"status '{status}' is terminal"):
            book.cancel(order_id="ord-restored")
    elif verb == "replace":
        with pytest.raises(InvalidTransition, match=f"status '{status}' is terminal"):
            book.replace(order_id="ord-restored", replace_qty=50.0)
    else:
        with pytest.raises(InvalidTransition, match=f"status '{status}' is terminal"):
            book.rejected(order_id="ord-restored", reason_code="insufficient_cash")

    assert book.rows() == (terminal,)
    assert book.open_orders() == ()


def test_replace_after_a_real_fill_raises_invalid_transition() -> None:
    book = _book()
    _place(book)
    filled = book.match_bar(_at(1500.0))[0]

    with pytest.raises(InvalidTransition, match="status 'filled' is terminal"):
        book.replace(order_id="ord-1", replace_qty=50.0)

    assert book.rows() == (filled,)


# ── TIF: day rests, ioc/fok terminate at the bar ────────────────────────────


def test_ioc_and_fok_terminate_at_the_bar_while_day_rests() -> None:
    book = _book()
    day = _place(book, order_id="ord-day", seq=1, tif="day")
    _place(book, order_id="ord-ioc", seq=2, tif="ioc")
    _place(book, order_id="ord-fok", seq=3, tif="fok")

    assert book.match_bar(_at(None)) == ()
    changed = book.end_of_bar("09:35")

    assert [row.order_id for row in changed] == ["ord-ioc", "ord-fok"]
    assert changed[0].status == "canceled"
    assert changed[1].status == "canceled"
    assert book.rows() == (day, changed[0], changed[1])
    assert book.open_orders() == (day,)


def test_end_of_bar_is_idempotent() -> None:
    book = _book()
    _place(book, order_id="ord-ioc", seq=1, tif="ioc")
    first = book.end_of_bar("09:35")
    assert len(first) == 1

    assert book.end_of_bar("09:35") == ()
    assert book.end_of_bar("09:40") == ()
    assert book.rows() == (first[0],)


def test_a_day_order_rests_across_bars_until_it_fills() -> None:
    book = _book()
    _place(book, tif="day")

    for label in ("09:35", "09:40"):
        assert book.match_bar(_at(None)) == ()
        assert book.open_orders()[0].status == "new"
        assert book.end_of_bar(label) == ()
        assert book.open_orders()[0].status == "new"
        _assert_conserved(book.open_orders()[0])

    filled = book.match_bar(_at(1502.0))
    assert len(filled) == 1
    assert filled[0].avg_fill_price == 1502.0
    assert book.open_orders() == ()


def test_marketable_ioc_fills_without_waiting_for_the_bar_end() -> None:
    book = _book()
    _place(book, tif="ioc")

    filled = book.match_bar(_at(1501.0))

    assert len(filled) == 1
    assert filled[0].status == "filled"
    assert book.end_of_bar("09:35") == ()


# ── All-or-none: no partial fill path ───────────────────────────────────────


def test_all_or_none_fills_the_whole_quantity_or_nothing() -> None:
    book = _book()
    _place(book, qty=100.0)

    # A price is returned: the whole 100 shares fill, never a fraction.
    filled = book.match_bar(_at(10.0))[0]
    assert (filled.filled_qty, filled.remaining_qty) == (100.0, 0.0)

    other = _book()
    _place(other, qty=100.0)
    # No price: nothing fills, and the row keeps its whole remaining quantity.
    assert other.match_bar(_at(None)) == ()
    assert (other.open_orders()[0].filled_qty, other.open_orders()[0].remaining_qty) == (0.0, 100.0)


def test_a_non_positive_or_non_finite_fill_price_is_refused() -> None:
    book = _book()
    _place(book)
    for bad in (0.0, -1.0, float("nan"), float("inf"), True, "1500"):
        with pytest.raises(InvalidTransition, match="fill price"):
            book.match_bar(_at(bad))
    assert book.open_orders()[0].status == "new"


# ── Conservation after every transition ─────────────────────────────────────


def test_conservation_holds_after_every_lifecycle_transition() -> None:
    book = _book()
    placed = _place(book, qty=100.0, limit=1500.0)
    first_replace = book.replace(order_id="ord-1", replace_qty=70.0)
    second_replace = book.replace(order_id="ord-1", replace_limit=1499.0)
    filled = book.match_bar(_at(1499.0))[0]

    for row in (placed, first_replace, second_replace, filled):
        _assert_conserved(row)
        assert row.remaining_qty >= 0.0
    assert (filled.qty, filled.filled_qty, filled.remaining_qty) == (70.0, 70.0, 0.0)

    canceled_book = _book()
    canceled_placed = _place(canceled_book)
    canceled = canceled_book.cancel(order_id="ord-1")
    for row in (canceled_placed, canceled):
        _assert_conserved(row)

    rejected_book = _book()
    rejected_placed = _place(rejected_book)
    rejected = rejected_book.rejected(order_id="ord-1", reason_code="insufficient_cash")
    for row in (rejected_placed, rejected):
        _assert_conserved(row)

    _assert_conserved(_row(status="expired"))


# ── Deterministic ordering and restore ──────────────────────────────────────


def test_open_orders_are_ordered_by_seq_then_order_id() -> None:
    book = _book()
    _place(book, order_id="ord-b", seq=2)
    _place(book, order_id="ord-z", seq=1)
    _place(book, order_id="ord-a", seq=1)

    assert [row.order_id for row in book.open_orders()] == ["ord-a", "ord-z", "ord-b"]
    assert [row.order_id for row in book.open_orders()] == ["ord-a", "ord-z", "ord-b"]
    assert [row.order_id for row in book.rows()] == ["ord-a", "ord-z", "ord-b"]


def test_restored_rows_are_complete_and_deterministic() -> None:
    fresh = _row(order_id="ord-2", seq=2)
    done = _row(order_id="ord-1", seq=1, status="filled", filled_qty=100.0, remaining_qty=0.0,
                avg_fill_price=1499.0)
    book = _book((fresh, done))

    assert [row.order_id for row in book.rows()] == ["ord-1", "ord-2"]
    assert [row.order_id for row in book.open_orders()] == ["ord-2"]

    third = _place(book, order_id="ord-3", seq=3)
    assert [row.order_id for row in book.rows()] == ["ord-1", "ord-2", "ord-3"]
    assert book.open_orders()[-1] == third


def test_restore_refuses_duplicate_ids() -> None:
    with pytest.raises(InvalidTransition, match="duplicate order_id"):
        _book((_row(order_id="dup"), _row(order_id="dup", seq=2)))
    with pytest.raises(InvalidTransition, match="duplicate client_order_id"):
        _book((
            _row(order_id="a", client_order_id="cli"),
            _row(order_id="b", client_order_id="cli", seq=2),
        ))


def test_restore_refuses_a_foreign_account_or_trade_date() -> None:
    with pytest.raises(InvalidTransition, match="account"):
        _book((_row(account_id="other"),))
    with pytest.raises(InvalidTransition, match="trade_date"):
        _book((_row(trade_date="2026-10-19"),))


def test_place_refuses_duplicate_ids() -> None:
    book = _book()
    _place(book)

    with pytest.raises(InvalidTransition, match="duplicate order_id"):
        _place(book, order_id="ord-1")

    _place(book, order_id="ord-2", seq=2, client_order_id="cli-x")
    with pytest.raises(InvalidTransition, match="duplicate client_order_id"):
        _place(book, order_id="ord-3", seq=3, client_order_id="cli-x")
    assert len(book.rows()) == 2


# ── Malformed intents ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"qty": 0.0},
        {"qty": -10.0},
        {"qty": float("nan")},
        {"limit": 0.0},
        {"limit": float("inf")},
        {"side": "hold"},
        {"tif": "gtc"},
        {"symbol": ""},
        {"action": "cancel"},
    ],
)
def test_place_refuses_a_malformed_intent(kwargs: dict[str, Any]) -> None:
    book = _book()

    with pytest.raises(InvalidTransition):
        _place(book, **kwargs)
    assert book.rows() == ()


def test_place_refuses_an_identity_it_cannot_own() -> None:
    book = _book()

    with pytest.raises(InvalidTransition, match="non-empty"):
        book.place(_action(), order_id="", seq=1, bar_label="09:35")
    with pytest.raises(InvalidTransition, match="seq"):
        _place(book, seq=-1)
    with pytest.raises(InvalidTransition, match="bar_label"):
        _place(book, bar_label="9:35")
    with pytest.raises(InvalidTransition, match="order_id"):
        book.place(_action(order_id="from-action"), order_id="ord-x", seq=1, bar_label="09:35")
    with pytest.raises(InvalidTransition, match="replace"):
        _place(book, replace_qty=10.0)


def test_replace_requires_a_field_and_positive_values() -> None:
    book = _book()
    _place(book)

    with pytest.raises(InvalidTransition, match="at least one"):
        book.replace(order_id="ord-1")
    with pytest.raises(InvalidTransition, match="replace_qty"):
        book.replace(order_id="ord-1", replace_qty=0.0)
    with pytest.raises(InvalidTransition, match="replace_limit"):
        book.replace(order_id="ord-1", replace_limit=-1.0)


def test_cancel_and_replace_refuse_an_ambiguous_or_unknown_target() -> None:
    book = _book()
    _place(book, order_id="ord-1", client_order_id="cli-1")
    _place(book, order_id="ord-2", seq=2, client_order_id="other")

    with pytest.raises(InvalidTransition, match="requires"):
        book.cancel()
    with pytest.raises(InvalidTransition, match="unknown order_id"):
        book.cancel(order_id="nope")
    with pytest.raises(InvalidTransition, match="unknown client_order_id"):
        book.cancel(client_order_id="nope")
    with pytest.raises(InvalidTransition, match="different orders"):
        book.cancel(order_id="ord-1", client_order_id="other")


# ── Event rows ──────────────────────────────────────────────────────────────


def test_events_emit_one_per_transition_with_optional_keys_omitted() -> None:
    book = _book()
    placed = _place(book, client_order_id="cli-1")
    filled = book.match_bar(_at(1500.0))[0]
    canceled = _place(book, order_id="ord-2", seq=2, client_order_id="cli-2")
    canceled = book.cancel(client_order_id="cli-2", reason="client_request")

    events = book.events_for((placed, filled, canceled), bar_time=_BAR_TIME)

    assert [event["event_type"] for event in events] == ["placed", "filled", "canceled"]
    for event in events:
        assert set(event) >= set(_REQUIRED_EVENT_KEYS)
        assert event["bar_time"] == _BAR_TIME
    assert set(events[0]) == set(_REQUIRED_EVENT_KEYS)
    assert set(events[1]) == set(_REQUIRED_EVENT_KEYS) | {"price"}
    assert events[1]["price"] == 1500.0
    assert set(events[2]) == set(_REQUIRED_EVENT_KEYS) | {"reason_code"}
    assert events[2]["reason_code"] == "client_request"
    assert events[2]["qty"] == 100.0


def test_events_follow_transition_order_even_when_changed_is_shuffled() -> None:
    book = _book()
    placed = _place(book)
    canceled = book.cancel(order_id="ord-1")

    events = book.events_for((canceled, placed), bar_time=_BAR_TIME)

    assert [event["event_type"] for event in events] == ["placed", "canceled"]
    assert events[0]["order_id"] == events[1]["order_id"] == "ord-1"


def test_end_of_bar_events_carry_the_stable_reason_tokens() -> None:
    book = _book()
    _place(book, order_id="ord-ioc", seq=1, tif="ioc")
    _place(book, order_id="ord-fok", seq=2, tif="fok")

    changed = book.end_of_bar("09:35")
    events = book.events_for(changed, bar_time=_BAR_TIME)

    assert [(event["order_id"], event["reason_code"]) for event in events] == [
        ("ord-ioc", "ioc_unfilled"),
        ("ord-fok", "fok_unfilled"),
    ]
    assert all(event["event_type"] == "canceled" for event in events)
    assert all("price" not in event for event in events)
    assert all(event["detail"] == "unfilled at 09:35" for event in events)


def test_events_refuse_a_row_this_book_did_not_transition() -> None:
    restored = _row()
    book = _book((restored,))

    with pytest.raises(InvalidTransition, match="not transitioned"):
        book.events_for((restored,), bar_time=_BAR_TIME)


def test_events_deduplicate_a_repeated_transition_row() -> None:
    book = _book()
    placed = _place(book)

    events = book.events_for((placed, placed), bar_time=_BAR_TIME)

    assert len(events) == 1
    assert events[0]["event_type"] == "placed"


def test_replaced_and_rejected_events_carry_the_expected_quantity() -> None:
    book = _book()
    placed = _place(book, client_order_id="cli-1")
    replaced = book.replace(order_id="ord-1", replace_qty=40.0)

    replaced_events = book.events_for((placed, replaced), bar_time=_BAR_TIME)

    assert [event["event_type"] for event in replaced_events] == ["placed", "replaced"]
    assert [event["qty"] for event in replaced_events] == [100.0, 40.0]
    assert "reason_code" not in replaced_events[1]

    other = _book()
    other_placed = _place(other)
    rejected = other.rejected(order_id="ord-1", reason_code="lot_size")

    rejected_events = other.events_for((other_placed, rejected), bar_time=_BAR_TIME)

    assert [event["event_type"] for event in rejected_events] == ["placed", "rejected"]
    assert rejected_events[1]["qty"] == 100.0
    assert rejected_events[1]["reason_code"] == "lot_size"
    assert "price" not in rejected_events[1]


def test_events_reject_a_rebuilt_row_equal_to_a_real_transition() -> None:
    book = _book()
    placed = _place(book)
    rebuilt = dataclass_replace(placed)

    assert rebuilt == placed
    assert rebuilt is not placed
    with pytest.raises(InvalidTransition, match="not transitioned"):
        book.events_for((rebuilt,), bar_time=_BAR_TIME)

    assert [event["event_type"] for event in book.events_for((placed,), bar_time=_BAR_TIME)] == ["placed"]
