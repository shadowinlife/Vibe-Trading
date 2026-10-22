"""Todo 20 gate — determinism and per-bar conservation for the pt-v1.3 engine.

Four independent properties over the committed synthetic 5m fixtures:

* **Double-run equality** — the same 48-label session, replayed twice from two
  fresh states and two freshly built timelines, produces deeply equal
  :class:`~src.tools.paper_trade.bar_engine.BarOutcome` states and byte-
  identical v1.3 documents (the canonical ``wire.build_success_payload`` +
  ``wire.dumps_payload`` bytes).  Nothing here compares only an aggregate NAV.
* **Per-bar conservation** — EVERY one of the 48 bars satisfies: cash >= 0,
  no non-positive position qty, ``filled_qty + remaining_qty == qty`` with
  fills always all-or-none, ``nav == cash + position_value`` to the cent with
  no float artifact, and a cash delta that equals the signed fill notionals
  minus fees.
* **No wall clock / randomness** — an AST + token scan over every module in
  ``agent/src/tools/paper_trade`` finds no ``now``/``utcnow``/``today``/
  ``time``/``monotonic``/``perf_counter`` call and no executable ``random``/
  ``uuid`` reference.  A scanner self-check proves the gate is not vacuous.
* **Symbol-order independence** — the same timeline requested with the symbol
  list reversed yields byte-identical per-label documents.

Offline and deterministic: committed CSV fixtures only, no network, no clock,
no randomness, no files written.  Product code is not modified.

Replay schedule (one action per bar, chosen so real transitions happen)::

    09:35  market buy 100     -> fills
    09:40  buy limit 1000     -> rests (open_orders == 1)
    09:45  cancel that order  -> canceled
    09:50  buy IOC limit 1000 -> canceled unfilled at bar end
    10:00  market sell 100    -> fills (the seeded prior-day holding)

The request's ``account_state`` stays the initial snapshot on every bar on
purpose: the engine treats ``BarEngineState`` as the ledger authority, so a
stale request snapshot must not influence the replay.
"""

from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import src.tools.paper_trade as paper_trade_package
from src.tools.paper_trade import wire
from src.tools.paper_trade.bar_clock import BAR_LABELS
from src.tools.paper_trade.bar_engine import BarEngineState, BarOutcome, apply_bar
from src.tools.paper_trade.data_5m import FiveMinuteFacts
from src.tools.paper_trade.orders import OrderBook
from src.tools.paper_trade.schema import AccountState, Position, parse_slippage
from src.tools.paper_trade.schema_v13 import BarStepRequest, OrderAction
from src.tools.paper_trade.timeline import BarRow, BarTimeline, build_timeline
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_ACCOUNT = "pa_t20_determinism"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_OTHER = "000001.SZ"
_CENT = Decimal("0.01")
_INITIAL_CASH = Decimal("500000.00")

#: A prior-day holding so the replay can exercise the SELL cash-flow sign.
_INITIAL_POSITIONS: tuple[Position, ...] = (Position(symbol=_SYM, qty=100.0, avg_cost=1400.0),)

#: The replay schedule; every other bar carries no action.
_ACTIONS: dict[str, tuple[OrderAction, ...]] = {
    "09:35": (OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0),),
    "09:40": (
        OrderAction(
            action="place", symbol=_SYM, side="buy", qty=100.0,
            limit=1000.0, client_order_id="rest-1",
        ),
    ),
    "09:45": (OrderAction(action="cancel", symbol="", client_order_id="rest-1"),),
    "09:50": (
        OrderAction(
            action="place", symbol=_SYM, side="buy", qty=100.0,
            limit=1000.0, tif="ioc", client_order_id="ioc-1",
        ),
    ),
    "10:00": (OrderAction(action="place", symbol=_SYM, side="sell", qty=100.0),),
}

_CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})
_FORBIDDEN_REFERENCE = re.compile(r"\b(?:random|uuid)\b")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _Replay:
    """One full 48-bar run: outcomes, canonical documents and their digests."""

    outcomes: tuple[BarOutcome, ...]
    documents: tuple[str, ...]
    digest: str
    final_digest: str


# ── fixtures: committed CSVs -> a 48-label timeline ───────────────────────


def _fixture_frame(symbol: str, day: str):
    """The committed continuous 5m bars (the 09:30 auction print is dropped)."""
    return continuous_bars(load_5m(symbol, day))


def _fixture_timeline() -> BarTimeline:
    """The 48-bar replay timeline for one symbol, built from the fixture CSV."""
    facts = FiveMinuteFacts(bars_by_symbol={_SYM: {_DAY: _fixture_frame(_SYM, _DAY)}})
    timeline = build_timeline(facts, _DAY)
    assert len(timeline.bar_labels) == 48
    assert all(row.status == "present" for label in timeline.bar_labels
               for row in timeline.rows_for(label))
    return timeline


def _two_symbol_timeline(symbols: tuple[str, ...]) -> BarTimeline:
    """A two-symbol timeline; ``symbols`` is deliberately caller-ordered.

    ``000001.SZ`` has one committed normal day (2026-10-19); it is registered
    under the replay day because ``build_timeline`` reads only the frame's
    labels and values, never the frame's own date.  Both frames are committed
    fixtures -- nothing is fetched or invented.
    """
    frames = {
        _SYM: _fixture_frame(_SYM, _DAY),
        _OTHER: _fixture_frame(_OTHER, "2026-10-19"),
    }
    facts = FiveMinuteFacts(bars_by_symbol={symbol: {_DAY: frame} for symbol, frame in frames.items()})
    return build_timeline(facts, _DAY, symbols=symbols)


def _state(
    *,
    cash: Decimal = _INITIAL_CASH,
    positions: tuple[Position, ...] = _INITIAL_POSITIONS,
) -> BarEngineState:
    """A fresh pre-session state (watermark empty, book empty)."""
    return BarEngineState(
        account_id=_ACCOUNT,
        market="cn_a",
        trade_date=_DAY,
        cash=cash,
        positions=positions,
        book=OrderBook(_ACCOUNT, _DAY),
        order_seq=0,
        bought_today={},
        watermark_bar="",
    )


def _request(
    label: str,
    *,
    actions: tuple[OrderAction, ...] | None = None,
    cash: float = float(_INITIAL_CASH),
    positions: tuple[Position, ...] = _INITIAL_POSITIONS,
) -> BarStepRequest:
    """The bar request; ``actions=None`` uses the replay schedule for ``label``."""
    return BarStepRequest(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=AccountState(cash=cash, positions=positions),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=f"{_DAY}T{label}:00+08:00",
        order_actions=_ACTIONS.get(label, ()) if actions is None else actions,
        slippage=parse_slippage(None),
    )


# ── replay + document helpers ─────────────────────────────────────────────


def _document(outcome: BarOutcome) -> str:
    """The canonical v1.3 bytes for one bar."""
    return wire.dumps_payload(
        wire.build_success_payload(outcome.bar_result.result, bar=outcome.bar_result)
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replay(timeline: BarTimeline, state: BarEngineState | None = None) -> _Replay:
    """Apply all 48 labels in order, feeding each outcome's state into the next."""
    current = _state() if state is None else state
    outcomes: list[BarOutcome] = []
    for label in BAR_LABELS:
        outcome = apply_bar(current, _request(label), timeline)
        outcomes.append(outcome)
        current = outcome.state
    documents = tuple(_document(outcome) for outcome in outcomes)
    return _Replay(
        outcomes=tuple(outcomes),
        documents=documents,
        digest=_sha256("\n".join(documents)),
        final_digest=_sha256(documents[-1]),
    )


def _state_snapshot(state: BarEngineState) -> tuple[object, ...]:
    """The six carried fields, compared explicitly (never via ``__eq__`` alone)."""
    return (
        state.cash,
        state.positions,
        state.book.rows(),
        state.order_seq,
        tuple(sorted(state.bought_today.items())),
        state.watermark_bar,
    )


# ── property 1: double-run equality ───────────────────────────────────────


def test_the_two_48_bar_replays_are_byte_identical() -> None:
    first_state = _state()
    second_state = _state()
    assert first_state == second_state
    assert first_state is not second_state  # never reuse the first run's state

    first = _replay(_fixture_timeline(), first_state)
    second = _replay(_fixture_timeline(), second_state)

    assert len(first.outcomes) == len(second.outcomes) == len(BAR_LABELS) == 48
    for index, (label, left, right) in enumerate(zip(BAR_LABELS, first.outcomes, second.outcomes)):
        assert left.state == right.state, f"state diverged at {label}"
        assert _state_snapshot(left.state) == _state_snapshot(right.state), label
        assert left.fills == right.fills, label
        assert left.bar_result == right.bar_result, label
        assert left.bar_result.bar_time == f"{_DAY}T{label}:00+08:00"
        assert left.state.watermark_bar == label
        assert first.documents[index] == second.documents[index], f"bytes diverged at {label}"
        assert _sha256(first.documents[index]) == _sha256(second.documents[index])

    assert first.digest == second.digest
    assert first.final_digest == second.final_digest
    assert _HEX64.fullmatch(first.digest)
    assert _HEX64.fullmatch(first.final_digest)

    # The replay must exercise real transitions, not 48 identical no-ops.
    events = [event for outcome in first.outcomes for event in outcome.order_events]
    types = [event["event_type"] for event in events]
    assert types.count("placed") == 4
    assert types.count("filled") == 2
    assert types.count("canceled") == 2
    assert sum(event.get("reason_code") == "ioc_unfilled" for event in events) == 1
    assert [fill.side for outcome in first.outcomes for fill in outcome.fills] == ["buy", "sell"]
    open_counts = {label: len(outcome.bar_result.open_orders)
                   for label, outcome in zip(BAR_LABELS, first.outcomes)}
    assert open_counts["09:40"] == 1  # rests
    assert open_counts["09:45"] == 0  # canceled

    # Neither fresh input state was mutated: the second run did not inherit the first.
    assert first_state == _state() and second_state == _state()
    assert first_state.cash == _INITIAL_CASH
    assert first_state.book.rows() == () and first_state.watermark_bar == ""


# ── property 2: per-bar conservation ──────────────────────────────────────


def _assert_bar_conservation(
    label: str, outcome: BarOutcome, *, previous_cash: Decimal
) -> None:
    """Assert every conservation invariant for ONE bar (never only an aggregate)."""
    nav_rows = outcome.bar_result.result.nav_series
    assert len(nav_rows) == 1, label
    point = nav_rows[0]
    cash = Decimal(str(point.cash))
    value = Decimal(str(point.position_value))
    nav = Decimal(str(point.nav))
    for amount in (cash, value, nav):
        assert amount.as_tuple().exponent >= -2, f"{label}: float artifact {amount}"
    assert abs(nav - (cash + value)) <= _CENT, f"{label}: nav != cash + position_value"
    assert nav.quantize(_CENT) == (cash + value).quantize(_CENT), label
    assert cash == outcome.state.cash, f"{label}: document cash disagrees with the ledger"

    assert outcome.state.cash >= 0, label
    for position in outcome.state.positions:
        assert position.qty > 0, f"{label}: non-positive position {position}"

    for row in outcome.state.book.rows():
        qty = Decimal(str(row.qty))
        filled = Decimal(str(row.filled_qty))
        remaining = Decimal(str(row.remaining_qty))
        assert filled + remaining == qty, f"{label}: {row.order_id} is not all-or-none"
        assert bool(filled) == (row.status == "filled"), f"{label}: {row.order_id} {row.status}"
        if row.status == "filled":
            assert remaining == 0, label
        if row.status == "new":
            assert remaining > 0, label

    expected_delta = Decimal("0")
    for fill in outcome.fills:
        notional = (Decimal(str(fill.qty)) * Decimal(str(fill.price))).quantize(_CENT)
        fee = Decimal(str(fill.fee))
        expected_delta += -(notional + fee) if fill.side == "buy" else notional - fee
    delta = outcome.state.cash - previous_cash
    assert abs(delta - expected_delta) <= _CENT, f"{label}: cash-flow conservation"
    assert delta.quantize(_CENT) == expected_delta.quantize(_CENT), label


def test_every_bar_conserves_cash_positions_and_orders() -> None:
    replay = _replay(_fixture_timeline())

    previous_cash = _INITIAL_CASH
    for label, outcome in zip(BAR_LABELS, replay.outcomes):
        _assert_bar_conservation(label, outcome, previous_cash=previous_cash)
        previous_cash = outcome.state.cash

    assert previous_cash == replay.outcomes[-1].state.cash
    assert sum(len(outcome.fills) for outcome in replay.outcomes) == 2
    final_statuses = {row.status for row in replay.outcomes[-1].state.book.rows()}
    assert final_statuses == {"filled", "canceled"}  # no day order left open at 15:00


def test_a_bar_without_a_usable_close_values_at_cost_and_warns() -> None:
    """The valuation fallback: avg_cost mark, deterministic warning, same NAV identity."""
    present = BarRow(
        trade_date=_DAY,
        bar_label="09:35",
        bar_time=f"{_DAY}T09:35:00+08:00",
        symbol=_SYM,
        phase="continuous",
        status="present",
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        volume=1_000.0,
        reference_median=10.0,
        reference_close=10.0,
    )
    missing = BarRow(
        trade_date=_DAY,
        bar_label="09:40",
        bar_time=f"{_DAY}T09:40:00+08:00",
        symbol=_SYM,
        phase="continuous",
        status="missing",
    )
    timeline = BarTimeline(
        trade_date=_DAY,
        bar_labels=("09:35", "09:40"),
        rows_by_bar=MappingProxyType({"09:35": (present,), "09:40": (missing,)}),
        symbols=(_SYM,),
    )
    fill = apply_bar(
        _state(cash=Decimal("100000.00"), positions=()),
        _request("09:35", cash=100_000.0, positions=()),
        timeline,
    )
    assert len(fill.fills) == 1
    opened = fill.state
    assert opened.positions[0].avg_cost == 10.01

    assert opened.watermark_bar == "09:35"
    closed = apply_bar(
        _state(cash=opened.cash, positions=opened.positions),
        _request("09:40", actions=(), cash=float(opened.cash), positions=opened.positions),
        timeline,
    )

    assert closed.fills == ()
    assert closed.bar_result.result.warnings == (
        "valuation: 600519.SH has no usable 09:40 close; position valued at avg_cost",
    )
    nav = closed.bar_result.result.nav_series[0]
    assert nav.position_value == 1_001.0
    assert Decimal(str(nav.position_value)) == 100 * Decimal(str(opened.positions[0].avg_cost))
    assert abs(Decimal(str(nav.nav)) - (Decimal(str(nav.cash)) + Decimal("1001.00"))) <= _CENT
    _assert_bar_conservation("09:40", closed, previous_cash=opened.cash)


# ── property 3: no wall clock, no randomness ──────────────────────────────


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _scan_source(name: str, source: str) -> tuple[str, ...]:
    """Offenders for one module: clock calls plus executable random/uuid refs.

    String literals and comments are stripped before the reference scan: a
    docstring saying "no uuid, no random" is documentation, not a dependency.
    The AST pass is what makes the callable check exact.
    """
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if isinstance(node, ast.Call) and _call_name(node.func) in _CLOCK_CALLS:
            offenders.append(f"{name}:{node.lineno}: call to {_call_name(node.func)}()")
    executable = " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.STRING, tokenize.COMMENT)
    )
    if _FORBIDDEN_REFERENCE.search(executable):
        offenders.append(f"{name}: executable random/uuid reference")
    return tuple(offenders)


def _scan_package() -> tuple[tuple[str, ...], int]:
    root = Path(paper_trade_package.__file__).resolve().parent
    modules = sorted(root.glob("*.py"))
    offenders = tuple(
        offender
        for path in modules
        for offender in _scan_source(path.name, path.read_text(encoding="utf-8"))
    )
    return offenders, len(modules)


def test_no_paper_trade_module_reads_a_clock_or_randomness() -> None:
    offenders, scanned = _scan_package()

    assert offenders == ()
    assert scanned == len(list(Path(paper_trade_package.__file__).resolve().parent.glob("*.py")))
    assert scanned >= 30  # the package really is the 38-module surface, not one file


def test_the_clock_scan_detects_a_violation() -> None:
    """The gate must be able to fail: a known-bad module is flagged."""
    bad = (
        "import datetime\n"
        "import random\n"
        "import uuid\n"
        "def f():\n"
        "    return datetime.datetime.now(), random.random(), uuid.uuid4()\n"
    )
    offenders = _scan_source("bad.py", bad)
    assert any("now()" in offender for offender in offenders)
    assert any("random/uuid" in offender for offender in offenders)
    assert _scan_source("good.py", '"""no uuid, no random. here"""\n') == ()


# ── property 4: symbol-order independence ─────────────────────────────────


def test_the_replay_is_independent_of_the_requested_symbol_order() -> None:
    forward = _two_symbol_timeline((_SYM, _OTHER))
    reversed_order = _two_symbol_timeline((_OTHER, _SYM))
    assert forward.symbols == reversed_order.symbols == (_OTHER, _SYM)

    first = _replay(forward)
    second = _replay(reversed_order)
    for label, left, right in zip(BAR_LABELS, first.outcomes, second.outcomes):
        assert left.state == right.state, label
        assert left.bar_result == right.bar_result, label
    assert first.documents == second.documents
    assert first.digest == second.digest
