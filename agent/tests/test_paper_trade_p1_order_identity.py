"""Todo 15 split — deterministic order identity and client-id dedupe (todo 12).

Split out of ``test_paper_trade_p1_orders.py`` so the lifecycle module stays
under the 800-line practical cap.  The tests below were MOVED VERBATIM, not
rewritten; they still pin ``order_ids.py`` and ``OrderBook.place_or_get``:

* the ``<account>:<trade_date>:<HH:MM>:<seq:08d>`` grammar and its round trip;
* two identical replays producing byte-identical ids;
* dedupe returning the SAME row, a conflicting payload raising, and the map
  being scoped to one book.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.tools.paper_trade.order_ids import (
    ORDER_ID_PATTERN,
    SEQ_MAX,
    OrderIdError,
    assign_order_id,
    next_seq,
    parse_order_id,
)
from src.tools.paper_trade.orders import InvalidTransition, OrderBook, allocate_order_id
from src.tools.paper_trade.schema import OrderAction, OrderState

_ACCOUNT = "pa_p1_orders"
_DAY = "2026-10-20"
_BAR_TIME = "2026-10-20T09:35:00+08:00"


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


def _place(
    book: OrderBook, order_id: str = "ord-1", seq: int = 1, bar_label: str = "09:35", **kwargs: Any
) -> OrderState:
    return book.place(_action(**kwargs), order_id=order_id, seq=seq, bar_label=bar_label)


def _at(price: float | None) -> Any:
    """A fill-price callable returning one constant price (or never marketable)."""
    return lambda row: price


_ID = "pa_p1_orders:2026-10-20:09:35:00000001"


def _replay() -> tuple[str, ...]:
    """Run the same three intents through a fresh book, id included."""
    book = _book()
    counter = 0
    placed: list[str] = []
    for symbol, side, label in (
        ("600519.SH", "buy", "09:35"),
        ("000001.SZ", "sell", "09:40"),
        ("600519.SH", "sell", "09:45"),
    ):
        order_id, next_counter = book.allocate_order_id(label, counter)
        book.place(_action(symbol=symbol, side=side), order_id=order_id, seq=counter, bar_label=label)
        placed.append(order_id)
        counter = next_counter
    return tuple(placed)


def test_assign_order_id_matches_the_documented_grammar() -> None:
    assert assign_order_id(_ACCOUNT, _DAY, "09:35", 1) == _ID
    assert assign_order_id(_ACCOUNT, _DAY, "09:35", 0) == "pa_p1_orders:2026-10-20:09:35:00000000"
    assert assign_order_id(_ACCOUNT, _DAY, "15:00", SEQ_MAX) == (
        "pa_p1_orders:2026-10-20:15:00:99999999"
    )
    assert ORDER_ID_PATTERN.match(_ID) is not None


def test_parse_order_id_round_trips_every_segment() -> None:
    for account, label, seq in (
        (_ACCOUNT, "09:35", 0),
        ("pa-2.x", "13:05", 41),
        ("A", "15:00", SEQ_MAX),
    ):
        assert parse_order_id(assign_order_id(account, _DAY, label, seq)) == (
            account,
            _DAY,
            label,
            seq,
        )


def test_two_identical_replays_produce_identical_ids() -> None:
    first = _replay()
    second = _replay()

    assert first == second == (
        "pa_p1_orders:2026-10-20:09:35:00000000",
        "pa_p1_orders:2026-10-20:09:40:00000001",
        "pa_p1_orders:2026-10-20:09:45:00000002",
    )


@pytest.mark.parametrize(
    "order_id",
    [
        "pa_p1_orders:2026-10-20:09:35:1",
        "pa_p1_orders:2026-10-20:09:35:000000001",
        ":2026-10-20:09:35:00000000",
        "pa_p1_orders:2026-1-2:09:35:00000000",
        "pa_p1_orders:2026-02-30:09:35:00000000",
        "pa_p1_orders:2026-10-20:24:00:00000000",
        "pa_p1_orders:2026-10-20:09:35",
        "pa_p1_orders:2026-10-20:09:35:00000000:1",
        "pa:b:2026-10-20:09:35:00000000",
        "pa_p1_orders:2026-10-20:09:35:00000000\n",
        "",
    ],
)
def test_parse_order_id_refuses_a_malformed_or_ambiguous_id(order_id: str) -> None:
    with pytest.raises(OrderIdError):
        parse_order_id(order_id)


def test_parse_order_id_requires_a_string() -> None:
    not_a_string: Any = b"pa_p1_orders:2026-10-20:09:35:00000000"

    with pytest.raises(OrderIdError, match="must be a string"):
        parse_order_id(not_a_string)


@pytest.mark.parametrize(
    ("account", "trade_date", "bar_label", "seq"),
    [
        ("", _DAY, "09:35", 0),
        ("pa:x", _DAY, "09:35", 0),
        (".hidden", _DAY, "09:35", 0),
        ("..", _DAY, "09:35", 0),
        ("a" * 65, _DAY, "09:35", 0),
        (_ACCOUNT, "2026-10-20T00:00:00", "09:35", 0),
        (_ACCOUNT, "2026-1-02", "09:35", 0),
        (_ACCOUNT, "2026-02-30", "09:35", 0),
        (_ACCOUNT, _DAY, "9:35", 0),
        (_ACCOUNT, _DAY, "09:60", 0),
        (_ACCOUNT, _DAY, "24:00", 0),
        (_ACCOUNT, _DAY, "09:35", -1),
        (_ACCOUNT, _DAY, "09:35", SEQ_MAX + 1),
        (_ACCOUNT, _DAY, "09:35", True),
        (_ACCOUNT, _DAY, "09:35", 1.0),
        (_ACCOUNT, _DAY, "09:35", "1"),
    ],
)
def test_assign_order_id_validates_every_segment(
    account: Any, trade_date: Any, bar_label: Any, seq: Any
) -> None:
    with pytest.raises(OrderIdError):
        assign_order_id(account, trade_date, bar_label, seq)


def test_next_seq_advances_one_and_refuses_the_last_seq() -> None:
    assert next_seq(0) == 1
    assert next_seq(41) == 42
    with pytest.raises(OrderIdError, match="last assignable"):
        next_seq(SEQ_MAX)
    with pytest.raises(OrderIdError):
        next_seq(-1)


def test_allocate_order_id_formats_the_id_and_returns_the_next_counter() -> None:
    book = _book()

    assert book.allocate_order_id("09:35", 7) == ("pa_p1_orders:2026-10-20:09:35:00000007", 8)
    assert allocate_order_id(_ACCOUNT, _DAY, "09:35", 7) == book.allocate_order_id("09:35", 7)
    assert book.rows() == ()


def test_place_or_get_creates_once_then_returns_the_same_row() -> None:
    book = _book()
    order_id = assign_order_id(_ACCOUNT, _DAY, "09:35", 0)
    action = _action(client_order_id="cli-dedupe")

    first, created = book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")
    second, created_again = book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")

    assert created is True
    assert created_again is False
    assert second is first
    assert book.rows() == (first,)
    events = book.events_for((second,), bar_time=_BAR_TIME)
    assert [event["event_type"] for event in events] == ["placed"]


def test_place_or_get_dedupes_a_filled_order_instead_of_filling_again() -> None:
    book = _book()
    order_id = assign_order_id(_ACCOUNT, _DAY, "09:35", 0)
    action = _action(client_order_id="cli-dedupe")
    placed, _ = book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")
    filled = book.match_bar(_at(1500.0))[0]

    same, created = book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")

    assert created is False
    assert same is filled
    assert same is not placed
    assert same.status == "filled"
    assert book.rows() == (filled,)


@pytest.mark.parametrize(
    "override",
    [
        {"symbol": "000001.SZ"},
        {"side": "sell"},
        {"qty": 200.0},
        {"limit": 1500.0},
        {"tif": "ioc"},
    ],
)
def test_place_or_get_rejects_a_client_id_reused_for_a_different_intent(
    override: dict[str, Any],
) -> None:
    book = _book()
    stored, _ = book.place_or_get(
        _action(client_order_id="cli-1"),
        order_id=assign_order_id(_ACCOUNT, _DAY, "09:35", 0),
        seq=0,
        bar_label="09:35",
    )

    with pytest.raises(InvalidTransition, match="client_order_id_conflict"):
        book.place_or_get(
            _action(client_order_id="cli-1", **override),
            order_id=assign_order_id(_ACCOUNT, _DAY, "09:40", 1),
            seq=1,
            bar_label="09:40",
        )

    assert book.rows() == (stored,)


def test_dedupe_compares_against_the_current_row_not_the_first_attempt() -> None:
    book = _book()
    order_id = assign_order_id(_ACCOUNT, _DAY, "09:35", 0)
    action = _action(client_order_id="cli-1")
    book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")
    book.replace(order_id=order_id, replace_qty=50.0)

    with pytest.raises(InvalidTransition, match="client_order_id_conflict"):
        book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")


def test_place_or_get_only_accepts_place_intents() -> None:
    book = _book()
    order_id = assign_order_id(_ACCOUNT, _DAY, "09:35", 0)
    book.place_or_get(_action(client_order_id="cli-1"), order_id=order_id, seq=0, bar_label="09:35")

    for action in (
        _action(action="cancel", client_order_id="cli-1"),
        _action(action="replace", client_order_id="cli-1", replace_qty=50.0),
    ):
        with pytest.raises(InvalidTransition, match="action 'place'"):
            book.place_or_get(action, order_id=order_id, seq=0, bar_label="09:35")


def test_find_by_client_order_id_is_scoped_to_one_book() -> None:
    book = _book()
    other = OrderBook("pa_p1_orders_other", _DAY)
    first = _place(book, client_order_id="shared", order_id="ord-a")
    second = other.place(_action(client_order_id="shared"), order_id="ord-b", seq=0, bar_label="09:35")

    assert book.find_by_client_order_id("shared") is first
    assert other.find_by_client_order_id("shared") is second
    assert book.find_by_client_order_id("") is None
    assert book.find_by_client_order_id("unknown") is None


def test_the_same_client_id_places_two_independent_orders_in_two_books() -> None:
    first_book = _book()
    second_book = OrderBook("pa_p1_orders_other", _DAY)
    action = _action(client_order_id="shared")

    first, created_first = first_book.place_or_get(
        action,
        order_id=assign_order_id(_ACCOUNT, _DAY, "09:35", 0),
        seq=0,
        bar_label="09:35",
    )
    second, created_second = second_book.place_or_get(
        action,
        order_id=assign_order_id("pa_p1_orders_other", _DAY, "09:35", 0),
        seq=0,
        bar_label="09:35",
    )

    assert (created_first, created_second) == (True, True)
    assert first.order_id != second.order_id
    assert first.account_id != second.account_id
    assert first_book.rows() == (first,)
    assert second_book.rows() == (second,)


def test_replace_keeps_the_order_id_and_seq() -> None:
    book = _book()
    order_id = assign_order_id(_ACCOUNT, _DAY, "09:35", 3)
    placed, _ = book.place_or_get(
        _action(client_order_id="cli-1"), order_id=order_id, seq=3, bar_label="09:35"
    )

    replaced = book.replace(order_id=order_id, replace_qty=50.0, replace_limit=1499.0)

    assert replaced.order_id == placed.order_id == order_id
    assert replaced.seq == placed.seq == 3
    assert replaced.qty == 50.0
    assert replaced.limit == 1499.0
    assert book.rows() == (replaced,)
