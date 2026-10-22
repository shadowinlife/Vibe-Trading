"""Todo 21 gate — the deterministic 48-bar session golden (pt-v1.3).

One full cn_a session (``2026-10-20``, ``600519.SH``) replayed bar by bar
through the real :func:`~src.tools.paper_trade.bar_engine.apply_bar` loop from
the committed 5m fixture, and compared byte for byte against the stored
document set at ``fixtures/paper_trade/golden/p1_intraday_session-600519.SH-
2026-10-20.json``.

Action script (one line per exercised surface)::

    09:35  market buy 100                 -> fill @ 1421.01 (fixed_bps 10)
    09:40  day limit buy 100 @ 1421.00    -> fill @ 1421.00 (slip capped by limit)
    09:45  day limit buy 100 @ 1300.00    -> rests, expires at 15:00
    09:50  day limit buy 100 @ 1300.00    -> rests
    09:55  cancel that order              -> canceled
    10:00  day limit buy 100 @ 1300.00    -> rests
    10:05  replace its limit -> 1300.50   -> replaced, rests, expires at 15:00
    10:10  IOC limit buy 100 @ 1300.00    -> rejected (limit_not_marketable)
    10:15  FOK limit buy 100 @ 1300.00    -> rejected (limit_not_marketable)
    10:20  sell 200                       -> rejected (t_plus_1: 200 bought today)
    10:25  sell 100                       -> fill (the seeded prior-day holding)
    10:30  sell 100                       -> rejected (t_plus_1: 0 sellable left)
    10:35  buy 50                         -> rejected (lot_size, cn_a board lot 100)
    10:40  sell 400                       -> rejected (insufficient_position)
    10:45  buy 1000                       -> rejected (insufficient_cash)

The committed ``2026-10-20`` fixture is flat at ``1421.00`` (``1422.00`` only
on the closing bar), so a *buy* limit that rests at 09:40 cannot become
marketable on a later bar without a price drop the fixture does not contain.
The 09:40 order therefore fills on its placement bar at its limit (proving the
limit cap over the 10 bps slip) and the "rests then terminates" surface is
carried by the 09:45/10:05 day orders, which expire at 15:00.  The opening
auction print (``09:30``) is carried by the timeline and deliberately excluded
from the 48 continuous labels; a second symbol with no fixture on the day is
the halted witness behind the ``halted`` session events.

Determinism: committed CSV fixtures only, no clock, no network, no randomness.
The test file writes no file (the golden is produced by running this module as
a script, see :func:`_write_golden`).
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.tools.paper_trade import wire
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.bar_clock import BAR_LABELS
from src.tools.paper_trade.bar_engine import BarEngineState, BarOutcome, apply_bar
from src.tools.paper_trade.data_5m import FiveMinuteFacts
from src.tools.paper_trade.order_ids import parse_order_id
from src.tools.paper_trade.rejections import RejectionContext, make_reject_hook
from src.tools.paper_trade.rules import MarketRules
from src.tools.paper_trade.schema import AccountState, Position, parse_slippage
from src.tools.paper_trade.schema_v13 import BarStepRequest, OrderAction
from src.tools.paper_trade.timeline import BarTimeline, build_timeline
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_ACCOUNT = "pa_t21_golden"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_HALTED = "000001.SZ"
_CASH = Decimal("500000.00")
_POSITIONS = (Position(symbol=_SYM, qty=100.0, avg_cost=1420.0),)
_GOLDEN_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "paper_trade"
    / "golden"
    / "p1_intraday_session-600519.SH-2026-10-20.json"
)
_CENT = Decimal("0.01")
_V13_KEYS = ("bar_time", "order_events", "open_orders", "order_seq", "slippage")


def _place(
    *,
    side: str = "buy",
    qty: float = 100.0,
    limit: float | None = None,
    tif: str = "day",
    client_order_id: str = "",
) -> OrderAction:
    return OrderAction(
        action="place",
        symbol=_SYM,
        side=side,
        qty=qty,
        limit=limit,
        tif=tif,
        client_order_id=client_order_id,
    )


#: The replay schedule; every other bar carries no action.
_ACTIONS: dict[str, tuple[OrderAction, ...]] = {
    "09:35": (_place(),),
    "09:40": (_place(limit=1421.00, client_order_id="limit-fill-1"),),
    "09:45": (_place(limit=1300.00, client_order_id="rest-expire-1"),),
    "09:50": (_place(limit=1300.00, client_order_id="rest-cancel-1"),),
    "09:55": (
        OrderAction(action="cancel", symbol="", client_order_id="rest-cancel-1"),
    ),
    "10:00": (_place(limit=1300.00, client_order_id="rest-replace-1"),),
    "10:05": (
        OrderAction(
            action="replace",
            symbol="",
            client_order_id="rest-replace-1",
            replace_limit=1300.50,
        ),
    ),
    "10:10": (_place(limit=1300.00, tif="ioc"),),
    "10:15": (_place(limit=1300.00, tif="fok"),),
    "10:20": (_place(side="sell", qty=200.0),),
    "10:25": (_place(side="sell", qty=100.0),),
    "10:30": (_place(side="sell", qty=100.0),),
    "10:35": (_place(qty=50.0),),
    "10:40": (_place(side="sell", qty=400.0),),
    "10:45": (_place(qty=1000.0),),
}


# ── fixtures: committed CSVs -> the 48-label timeline ─────────────────────


def _timeline() -> BarTimeline:
    """The 48-bar replay timeline plus the excluded opening auction print."""
    raw = load_5m(_SYM, _DAY)
    auction_labels = tuple(
        sorted({label for label in raw.index.strftime("%H:%M") if label == "09:30"})
    )
    facts = FiveMinuteFacts(
        bars_by_symbol={_SYM: {_DAY: continuous_bars(raw)}},
        auction_by_symbol={_SYM: {_DAY: auction_labels}},
    )
    timeline = build_timeline(facts, _DAY, symbols=(_HALTED, _SYM))
    assert len(timeline.bar_labels) == 48
    assert timeline.symbols == (_HALTED, _SYM)
    return timeline


def _initial_state() -> BarEngineState:
    """A fresh pre-session state built through the paper-trade account bridge."""
    account = create_account(
        account_id=_ACCOUNT,
        market="cn_a",
        start_date=_DAY,
        initial_cash=float(_CASH),
        initial_positions=_POSITIONS,
        today=_DAY,
        next_trading_day="2026-10-21",
        now=f"{_DAY}T08:00:00+08:00",
    )
    return BarEngineState.from_account(account, "cn_a", trade_date=_DAY)


def _request(label: str, state: BarEngineState) -> BarStepRequest:
    """One bar request; the ledger authority stays the carried engine state."""
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=float(state.cash), positions=state.positions),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=f"{_DAY}T{label}:00+08:00",
        order_actions=_ACTIONS.get(label, ()),
        slippage=parse_slippage(None),
    )


def _reject_hook():
    return make_reject_hook(
        RejectionContext(
            trading_day=True,
            suspended_symbols=frozenset(),
            corporate_action_unresolved=frozenset(),
            bands={},
            rules=MarketRules("cn_a"),
        )
    )


# ── the driver ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Session:
    """One full 48-bar run: outcomes, canonical document bytes and timeline."""

    outcomes: tuple[BarOutcome, ...]
    documents: tuple[str, ...]
    timeline: BarTimeline


def _document(document: dict[str, Any]) -> str:
    """The canonical pt-v1.3 bytes for one wire document."""
    return wire.dumps_payload(document)


def _build_session() -> _Session:
    """Apply all 48 labels in order, feeding each outcome's state into the next."""
    timeline = _timeline()
    current = _initial_state()
    hook = _reject_hook()
    outcomes: list[BarOutcome] = []
    for label in BAR_LABELS:
        outcome = apply_bar(current, _request(label, current), timeline, reject_hook=hook)
        outcomes.append(outcome)
        current = outcome.state
    documents = tuple(
        _document(wire.build_success_payload(outcome.bar_result.result, bar=outcome.bar_result))
        for outcome in outcomes
    )
    return _Session(outcomes=tuple(outcomes), documents=documents, timeline=timeline)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _envelope(session: _Session) -> dict[str, Any]:
    """The stored golden: provenance, the 48 documents and the final session facts."""
    final = session.outcomes[-1].state
    event_types = Counter(
        event["event_type"] for outcome in session.outcomes for event in outcome.order_events
    )
    assert final.book.open_orders() == ()
    return {
        "fixture": "p1_intraday_session",
        "schema_version": "pt-v1.3",
        "trade_date": _DAY,
        "market": "cn_a",
        "symbols": [_SYM],
        "witness_symbols": [_HALTED],
        "bar_labels": list(BAR_LABELS),
        "bars": [
            {
                "bar_label": label,
                "document": wire.build_success_payload(
                    outcome.bar_result.result, bar=outcome.bar_result
                ),
            }
            for label, outcome in zip(BAR_LABELS, session.outcomes)
        ],
        "session": {
            "final_cash": float(final.cash),
            "final_positions": [
                {"symbol": position.symbol, "qty": position.qty, "avg_cost": position.avg_cost}
                for position in final.positions
            ],
            "open_orders": [],
            "order_seq": final.order_seq,
            "fill_count": sum(len(outcome.fills) for outcome in session.outcomes),
            "rejected_count": sum(len(outcome.rejected) for outcome in session.outcomes),
            "canceled_count": event_types["canceled"],
            "expired_count": event_types["expired"],
            "final_document_sha256": _sha256(session.documents[-1]),
        },
    }


def _golden() -> dict[str, Any]:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


# ── 1. byte-for-byte equality ─────────────────────────────────────────────


def test_the_48_bar_session_matches_the_golden_byte_for_byte() -> None:
    golden = _golden()
    session = _build_session()

    assert len(golden["bars"]) == len(session.documents) == 48
    assert [bar["bar_label"] for bar in golden["bars"]] == list(BAR_LABELS)
    expected = "\n".join(_document(bar["document"]) for bar in golden["bars"])
    assert "\n".join(session.documents) == expected
    assert _sha256(session.documents[-1]) == golden["session"]["final_document_sha256"]
    assert _sha256(expected.split("\n")[-1]) == golden["session"]["final_document_sha256"]


# ── 2. every contract surface is present ──────────────────────────────────


def test_the_golden_covers_every_contract_surface() -> None:
    golden = _golden()
    bars = golden["bars"]

    assert len(bars) == 48
    assert golden["symbols"] == [_SYM]
    assert golden["witness_symbols"] == [_HALTED]
    for bar in bars:
        document = bar["document"]
        assert document["schema_version"] == "pt-v1.3"
        assert set(_V13_KEYS) <= set(document)
        assert document["bar_time"] == f"{_DAY}T{bar['bar_label']}:00+08:00"

    order_ids = [
        event["order_id"]
        for bar in bars
        for event in bar["document"]["order_events"]
    ] + [
        order["order_id"]
        for bar in bars
        for order in bar["document"]["open_orders"]
    ]
    assert order_ids
    for order_id in order_ids:
        account_id, trade_date, label, seq = parse_order_id(order_id)
        assert (account_id, trade_date) == (_ACCOUNT, _DAY)
        assert label in BAR_LABELS
        assert 0 <= seq <= 99_999_999

    events = [event for bar in bars for event in bar["document"]["order_events"]]
    types = Counter(event["event_type"] for event in events)
    assert types["filled"] == 3
    assert types["rejected"] == 7
    assert types["canceled"] == 1
    assert types["expired"] == 2
    assert types["placed"] == 13
    assert types["replaced"] == 1

    reasons = Counter(
        event["reason_code"] for event in events if event.get("reason_code")
    )
    assert reasons == {
        "limit_not_marketable": 2,   # the IOC and the FOK on a far-below limit
        "t_plus_1": 2,               # a sell above today's un-sellable quantity
        "lot_size": 1,               # a 50-share buy on a 100-share board lot
        "insufficient_position": 1,  # a sell above the whole holding
        "insufficient_cash": 1,      # a buy whose cost exceeds free cash
    }
    assert [
        event["detail"] for event in events if event["event_type"] == "expired"
    ] == ["day order expired at 15:00", "day order expired at 15:00"]

    session_events = [event for bar in bars for event in bar["document"]["events"]]
    assert sum(event["event_type"] == "halted" for event in session_events) == 48
    assert {event["symbol"] for event in session_events} == {_HALTED}
    # The auction surface is the excluded opening print: it is part of the
    # fixture and the timeline, and never one of the 48 continuous labels.
    assert [row.bar_label for row in _timeline().auction_rows()] == ["09:30"]
    assert "09:30" not in BAR_LABELS
    assert [
        fill["side"] for bar in bars for fill in bar["document"]["fills"]
    ] == ["buy", "buy", "sell"]

    session = golden["session"]
    assert session == {
        "final_cash": 357434.18,
        "final_positions": [{"symbol": _SYM, "qty": 200.0, "avg_cost": 1421.14}],
        "open_orders": [],
        "order_seq": 13,
        "fill_count": 3,
        "rejected_count": 7,
        "canceled_count": 1,
        "expired_count": 2,
        "final_document_sha256": session["final_document_sha256"],
    }


# ── 3. a one-cent move changes the bytes ──────────────────────────────────


def test_a_one_cent_price_move_breaks_the_golden() -> None:
    golden = _golden()
    session = _build_session()
    index = next(
        position for position, outcome in enumerate(session.outcomes) if outcome.fills
    )
    mutated = copy.deepcopy(golden["bars"][index]["document"])
    original = mutated["fills"][0]["price"]
    mutated["fills"][0]["price"] = float(Decimal(str(original)) + _CENT)

    assert mutated["fills"][0]["price"] != original
    assert _document(mutated) != session.documents[index]
    assert _sha256(_document(mutated)) != _sha256(session.documents[index])
    # The stored golden is untouched (the mutation lives in memory only).
    assert _golden()["bars"][index]["document"]["fills"][0]["price"] == original


# ── 4. two builds are identical ───────────────────────────────────────────


def test_two_builds_are_identical() -> None:
    first = _build_session()
    second = _build_session()

    assert first.documents == second.documents
    assert _sha256("\n".join(first.documents)) == _sha256("\n".join(second.documents))
    assert first.outcomes[-1].state == second.outcomes[-1].state
    assert first.timeline.symbols == second.timeline.symbols


# ── golden generation (run as a script, never collected) ──────────────────


def _write_golden() -> None:
    """One-off: rebuild the session and store its canonical envelope."""
    session = _build_session()
    envelope = _envelope(session)
    _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GOLDEN_PATH.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    built = _build_session()
    stored = _envelope(built)
    _write_golden()
    print("golden:", _GOLDEN_PATH)
    print("bars:", len(stored["bars"]))
    print("session:", json.dumps(stored["session"], sort_keys=True))
    types = Counter(
        event["event_type"] for outcome in built.outcomes for event in outcome.order_events
    )
    print("order events:", dict(sorted(types.items())))
    reasons = Counter(
        event.get("reason_code")
        for outcome in built.outcomes
        for event in outcome.order_events
        if event.get("reason_code")
    )
    print("reason codes:", dict(sorted(reasons.items())))
    session_types = Counter(
        event.event_type for outcome in built.outcomes for event in outcome.session_events
    )
    print("session events:", dict(sorted(session_types.items())))
