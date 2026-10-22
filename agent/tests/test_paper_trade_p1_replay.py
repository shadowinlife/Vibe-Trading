"""Todo 25 gate — the deterministic full-day replay (pt-v1.3).

* a fresh replay applies all 48 bars in label order and closes ``session.json``
  with 48 applied bars, the closing ledger and the chain head;
* a rerun of that session is 48 ``bar_already_applied`` no-ops and changes ZERO
  bytes (the account/step tree is hashed before and after);
* a dry run writes nothing (a before/after tree snapshot) yet computes the
  exact documents and summary a real run writes;
* a mid-day resume applies only the remaining bars and keeps the chain;
* a refused bar (a feed outage / a wholly missing day) reports ``partial`` —
  never ``completed`` — with the bars processed so far and the refusal;
* all 48 documents match the committed golden session byte for byte (except
  the golden's halted witness, see below), including the ``t_plus_1``
  refusals that depend on the T+1 counter surviving the bar boundary;
* the module contains no clock call / random reference;
* a request factory that names another day is refused before any write.

Golden boundary: the golden timeline carries a halted witness symbol
(``000001.SZ``) that the account does not hold, while ``bar_step`` builds its
timeline from the account's own symbols; ``events`` is therefore the one key a
replayed golden document cannot share, and it is excluded.  Every other key —
including ``rejected_orders`` and ``end_state``, which are where a lost
``bought_today`` showed up — is compared across the whole session.

Offline, deterministic: a tmp runtime root and the committed 5m fixtures only.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from pathlib import Path

import pytest

from src.tools.paper_trade import bar_clock, bar_store, state_hash, wire
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.feeds import FEED_UNAVAILABLE, FeedError, ReplayFeed
from src.tools.paper_trade.schema import Position, parse_slippage
from src.tools.paper_trade.schema_v13 import BarStepRequest, OrderAction
from src.tools.paper_trade.session_replay import (
    SESSION_STATUS_COMPLETED,
    SESSION_STATUS_DRY_RUN,
    SESSION_STATUS_PARTIAL,
    ReplayError,
    SessionReplay,
    replay_session,
)
from src.tools.paper_trade.session_runner import (
    STATUS_ADOPTED,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_DRY_RUN,
    bar_step,
)
from src.tools.paper_trade.store import AccountStore
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_ACCOUNT = "pa_p1_replay"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_FIRST = "09:35"
_LAST = "15:00"
_GOLDEN_ACCOUNT = "pa_t21_golden"
_GOLDEN = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "paper_trade"
        / "golden"
        / "p1_intraday_session-600519.SH-2026-10-20.json"
    ).read_text(encoding="utf-8")
)
#: The golden's action script, verbatim (see ``test_paper_trade_p1_golden``).
_GOLDEN_SCRIPT: dict[str, tuple[OrderAction, ...]] = {
    "09:35": (OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0),),
    "09:40": (
        OrderAction(
            action="place",
            symbol=_SYM,
            side="buy",
            qty=100.0,
            limit=1421.00,
            client_order_id="limit-fill-1",
        ),
    ),
    "09:45": (
        OrderAction(
            action="place",
            symbol=_SYM,
            side="buy",
            qty=100.0,
            limit=1300.00,
            client_order_id="rest-expire-1",
        ),
    ),
    "09:50": (
        OrderAction(
            action="place",
            symbol=_SYM,
            side="buy",
            qty=100.0,
            limit=1300.00,
            client_order_id="rest-cancel-1",
        ),
    ),
    "09:55": (OrderAction(action="cancel", symbol="", client_order_id="rest-cancel-1"),),
    "10:00": (
        OrderAction(
            action="place",
            symbol=_SYM,
            side="buy",
            qty=100.0,
            limit=1300.00,
            client_order_id="rest-replace-1",
        ),
    ),
    "10:05": (
        OrderAction(
            action="replace",
            symbol="",
            client_order_id="rest-replace-1",
            replace_limit=1300.50,
        ),
    ),
    "10:10": (OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0, limit=1300.00, tif="ioc"),),
    "10:15": (OrderAction(action="place", symbol=_SYM, side="buy", qty=100.0, limit=1300.00, tif="fok"),),
    "10:20": (OrderAction(action="place", symbol=_SYM, side="sell", qty=200.0),),
    "10:25": (OrderAction(action="place", symbol=_SYM, side="sell", qty=100.0),),
    "10:30": (OrderAction(action="place", symbol=_SYM, side="sell", qty=100.0),),
    "10:35": (OrderAction(action="place", symbol=_SYM, side="buy", qty=50.0),),
    "10:40": (OrderAction(action="place", symbol=_SYM, side="sell", qty=400.0),),
    "10:45": (OrderAction(action="place", symbol=_SYM, side="buy", qty=1000.0),),
}


def _store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "paper_trade")


def _account(**overrides):
    kwargs = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "start_date": _DAY,
        "initial_cash": 200_000.0,
        "initial_positions": (Position(symbol=_SYM, qty=100.0, avg_cost=1400.0),),
        "today": _DAY,
        "next_trading_day": "2026-10-21",
        "now": f"{_DAY}T08:00:00+08:00",
    }
    kwargs.update(overrides)
    return create_account(**kwargs)


def _feed(symbols: tuple[str, ...] = (_SYM,)) -> ReplayFeed:
    return ReplayFeed(
        frames={symbol: {_DAY: continuous_bars(load_5m(symbol, _DAY))} for symbol in symbols}
    )


def _request(
    label: str,
    account,
    *,
    actions: tuple[OrderAction, ...] = (),
    day: str = _DAY,
    account_id: str = _ACCOUNT,
) -> BarStepRequest:
    return BarStepRequest(
        account_id=account_id,
        market="cn_a",
        account_state=account.state(),
        from_date=day,
        to_date=day,
        bar_time=f"{day}T{label}:00+08:00",
        order_actions=actions,
        slippage=parse_slippage(None),
    )


def _factory(
    actions: dict[str, tuple[OrderAction, ...]] | None = None,
    *,
    account_id: str = _ACCOUNT,
    day: str = _DAY,
):
    script = dict(actions or {})

    def build(label: str, account) -> BarStepRequest:
        return _request(
            label,
            account,
            actions=script.get(label, ()),
            day=day,
            account_id=account_id,
        )

    return build


def _place(qty: float = 100.0) -> OrderAction:
    return OrderAction(action="place", symbol=_SYM, side="buy", qty=qty)


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with the sha256 of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _step_path(store: AccountStore, label: str) -> Path:
    return store.steps_dir(_ACCOUNT) / _DAY / "bars" / label.replace(":", "")


def _stored_result_hashes(store: AccountStore) -> list[str]:
    return [
        bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, label).result_hash
        for label in bar_clock.BAR_LABELS
    ]


def _hash_stream(hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


class _OutageFeed(ReplayFeed):
    """A cached feed that fails every request from ``cut_off`` onwards."""

    def __init__(self, *, cut_off: str) -> None:
        super().__init__(frames={_SYM: {_DAY: continuous_bars(load_5m(_SYM, _DAY))}})
        self._cut_off = cut_off

    def closed_bars(self, *, symbol: str, trade_date: str, as_of: str):
        if as_of >= self._cut_off:
            raise FeedError(FEED_UNAVAILABLE, "provider outage")
        return super().closed_bars(symbol=symbol, trade_date=trade_date, as_of=as_of)


# ── 1. the happy path ─────────────────────────────────────────────────────


def test_a_full_day_replay_applies_48_bars_in_label_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    replay = replay_session(store, _factory({_FIRST: (_place(),)}), _ACCOUNT, _DAY, _feed())

    assert replay.status == SESSION_STATUS_COMPLETED
    assert (replay.applied, replay.adopted, replay.noop, replay.computed) == (48, 0, 0, 0)
    assert not replay.dry_run and replay.error is None
    assert len(replay.bars) == 48
    assert [bar.status for bar in replay.bars] == [STATUS_APPLIED] * 48
    assert [json.loads(bar.document)["bar_time"] for bar in replay.bars] == [
        f"{_DAY}T{label}:00+08:00" for label in bar_clock.BAR_LABELS
    ]
    assert [bar.step_path for bar in replay.bars] == [
        _step_path(store, label) for label in bar_clock.BAR_LABELS
    ]

    final = store.load(_ACCOUNT)
    assert final.watermark == _DAY
    assert final.watermark_bar == _LAST
    assert final.open_orders == ()
    assert len(list((store.steps_dir(_ACCOUNT) / _DAY / "bars").iterdir())) == 48

    closing = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _LAST)
    session = replay.session
    assert session["status"] == "closed"
    assert session["applied_bar_count"] == 48
    assert session["counts"]["bars"] == 48
    assert session["result_hash_chain_head"] == closing.result_hash
    assert session["final_state"] == {
        "cash": final.cash,
        "positions": [
            {"symbol": row.symbol, "qty": row.qty, "avg_cost": row.avg_cost}
            for row in final.positions
        ],
        "open_orders": [],
    }

    stored = _stored_result_hashes(store)
    assert replay.document_sha256 == _hash_stream(stored)
    assert replay.document_sha256 == _hash_stream(
        [state_hash.state_hash(json.loads(bar.document)) for bar in replay.bars]
    )


# ── 2. the rerun no-op ───────────────────────────────────────────────────


def test_a_rerun_of_a_completed_session_is_a_48_bar_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    factory = _factory({_FIRST: (_place(),)})
    first = replay_session(store, factory, _ACCOUNT, _DAY, _feed())
    root = store.account_dir(_ACCOUNT)
    before = _tree(root)

    again = replay_session(store, factory, _ACCOUNT, _DAY, _feed())

    assert again.status == SESSION_STATUS_COMPLETED
    assert (again.applied, again.adopted, again.noop, again.computed) == (0, 0, 48, 0)
    assert [bar.status for bar in again.bars] == [STATUS_ALREADY_APPLIED] * 48
    assert [bar.step_path for bar in again.bars] == [
        _step_path(store, label) for label in bar_clock.BAR_LABELS
    ]
    assert {json.loads(bar.document)["code"] for bar in again.bars} == {"INVALID_REQUEST"}
    assert dict(again.session) == dict(first.session)
    assert again.document_sha256 == first.document_sha256
    assert _tree(root) == before


# ── 3. the dry run ────────────────────────────────────────────────────────


def test_a_dry_run_writes_nothing_and_computes_the_committed_documents(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    root = store.account_dir(_ACCOUNT)
    before = _tree(root)
    factory = _factory({_FIRST: (_place(),)})

    dry = replay_session(store, factory, _ACCOUNT, _DAY, _feed(), dry_run=True)

    assert dry.status == SESSION_STATUS_DRY_RUN
    assert dry.dry_run is True and dry.error is None
    assert (dry.applied, dry.adopted, dry.noop, dry.computed) == (0, 0, 0, 48)
    assert [bar.status for bar in dry.bars] == [STATUS_DRY_RUN] * 48
    assert all(bar.step_path is None for bar in dry.bars)
    assert _tree(root) == before
    assert not (store.steps_dir(_ACCOUNT) / _DAY).exists()

    real = replay_session(store, factory, _ACCOUNT, _DAY, _feed())

    assert [bar.document for bar in dry.bars] == [bar.document for bar in real.bars]
    assert dry.document_sha256 == real.document_sha256
    assert dict(dry.session) == dict(real.session)
    assert dry.session["final_state"] == real.session["final_state"]


def test_a_dry_run_of_a_completed_session_still_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    factory = _factory({_FIRST: (_place(),)})
    replay_session(store, factory, _ACCOUNT, _DAY, _feed())
    root = store.account_dir(_ACCOUNT)
    before = _tree(root)

    dry = replay_session(store, factory, _ACCOUNT, _DAY, _feed(), dry_run=True)

    assert dry.status == SESSION_STATUS_DRY_RUN
    assert (dry.applied, dry.noop, dry.computed) == (0, 48, 0)
    assert dry.document_sha256 == _hash_stream(_stored_result_hashes(store))
    assert _tree(root) == before


# ── 4. a resumable partial day ───────────────────────────────────────────


def test_a_feed_outage_stops_the_replay_as_partial(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    factory = _factory({_FIRST: (_place(),)})
    cut_off = "10:00"

    replay = replay_session(store, factory, _ACCOUNT, _DAY, _OutageFeed(cut_off=cut_off))

    processed = [label for label in bar_clock.BAR_LABELS if label < cut_off]
    assert replay.status == SESSION_STATUS_PARTIAL
    assert replay.error is not None
    assert replay.error.code == "invalid_request"
    assert "provider outage" in replay.error.message
    assert (replay.applied, replay.adopted, replay.noop, replay.computed) == (
        len(processed),
        0,
        0,
        0,
    )
    assert [json.loads(bar.document)["bar_time"][11:16] for bar in replay.bars] == processed
    assert not _step_path(store, cut_off).exists()
    assert store.load(_ACCOUNT).watermark_bar == processed[-1]

    session = replay.session
    assert session["status"] == "open"
    assert session["applied_bar_count"] == len(processed)
    assert "final_state" not in session
    assert replay.document_sha256 == _hash_stream(
        [state_hash.state_hash(json.loads(bar.document)) for bar in replay.bars]
    )


def test_a_day_with_no_data_reports_partial_with_zero_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    replay = replay_session(store, _factory(), _ACCOUNT, _DAY, ReplayFeed(frames={}))

    assert replay.status == SESSION_STATUS_PARTIAL
    assert replay.error is not None and replay.error.code == "invalid_request"
    assert replay.bars == ()
    assert (replay.applied, replay.adopted, replay.noop, replay.computed) == (0, 0, 0, 0)
    assert replay.session == {}
    assert not (store.steps_dir(_ACCOUNT) / _DAY).exists()


def test_a_mid_day_resume_applies_only_the_remaining_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    factory = _factory({_FIRST: (_place(),)})
    feed = _feed()
    for label in bar_clock.BAR_LABELS[:10]:
        bar_step(store, factory(label, store.load(_ACCOUNT)), feed)

    replay = replay_session(store, factory, _ACCOUNT, _DAY, feed)

    assert replay.status == SESSION_STATUS_COMPLETED
    assert (replay.applied, replay.adopted, replay.noop, replay.computed) == (38, 0, 10, 0)
    assert [bar.status for bar in replay.bars[:10]] == [STATUS_ALREADY_APPLIED] * 10
    assert [bar.status for bar in replay.bars[10:]] == [STATUS_APPLIED] * 38
    assert replay.document_sha256 == _hash_stream(_stored_result_hashes(store))
    assert replay.session["status"] == "closed"
    assert replay.session["applied_bar_count"] == 48


def test_an_orphaned_step_is_adopted_once_by_the_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    factory = _factory({_FIRST: (_place(),)})
    real_save = store.save_account_atomic
    attempts = {"count": 0}

    def explode(account) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("crash between the step and the account")
        real_save(account)

    monkeypatch.setattr(store, "save_account_atomic", explode)
    with pytest.raises(RuntimeError, match="crash between the step and the account"):
        replay_session(store, factory, _ACCOUNT, _DAY, _feed())
    orphan = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert len(orphan.fills) == 1
    assert store.load(_ACCOUNT).watermark_bar == ""

    replay = replay_session(store, factory, _ACCOUNT, _DAY, _feed())

    assert replay.status == SESSION_STATUS_COMPLETED
    assert (replay.applied, replay.adopted, replay.noop) == (47, 1, 0)
    assert replay.bars[0].status == STATUS_ADOPTED
    assert replay.bars[0].step_path == _step_path(store, _FIRST)
    assert len(list((store.steps_dir(_ACCOUNT) / _DAY / "bars").iterdir())) == 48
    assert replay.document_sha256 == _hash_stream(_stored_result_hashes(store))


# ── 5. the golden oracle (all 48 bars) ──────────────────────────────────


def test_the_replayed_documents_match_the_golden_for_all_48_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(
        _account(
            account_id=_GOLDEN_ACCOUNT,
            initial_cash=500_000.0,
            initial_positions=(Position(symbol=_SYM, qty=100.0, avg_cost=1420.0),),
        )
    )

    replay = replay_session(
        store,
        _factory(_GOLDEN_SCRIPT, account_id=_GOLDEN_ACCOUNT),
        _GOLDEN_ACCOUNT,
        _DAY,
        _feed(),
    )

    assert replay.status == SESSION_STATUS_COMPLETED
    golden_bars = _GOLDEN["bars"]
    assert len(golden_bars) == len(replay.bars) == len(bar_clock.BAR_LABELS)
    for index, label in enumerate(bar_clock.BAR_LABELS):
        replayed = json.loads(replay.bars[index].document)
        expected = dict(golden_bars[index]["document"])
        # The golden timeline carries the halted witness 000001.SZ; a replayed
        # bar's universe is the account's own symbols, so `events` is the only
        # key the two can never share.
        replayed.pop("events", None)
        expected.pop("events", None)
        assert wire.dumps_payload(replayed) == wire.dumps_payload(expected), (
            f"bar {label} differs from the golden"
        )
    assert golden_bars[0]["bar_label"] == _FIRST

    golden_session = _GOLDEN["session"]
    assert replay.session["final_state"] == {
        "cash": golden_session["final_cash"],
        "positions": golden_session["final_positions"],
        "open_orders": golden_session["open_orders"],
    }


# ── 6. refusals that must not write ──────────────────────────────────────


def test_a_factory_naming_another_day_is_refused_before_any_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    with pytest.raises(ReplayError, match="2026-10-19"):
        replay_session(store, _factory(day="2026-10-19"), _ACCOUNT, _DAY, _feed())

    assert not (store.steps_dir(_ACCOUNT) / _DAY).exists()


def test_the_replay_object_rejects_an_inconsistent_result() -> None:
    import hashlib as _hashlib

    good = dict(
        trade_date=_DAY,
        account_id=_ACCOUNT,
        status=SESSION_STATUS_COMPLETED,
        bars=(),
        applied=0,
        adopted=0,
        noop=0,
        session={},
        document_sha256=_hashlib.sha256(b"").hexdigest(),
    )
    with pytest.raises(ValueError, match="48"):
        SessionReplay(**good)
    with pytest.raises(ValueError, match="partial replay"):
        SessionReplay(**{**good, "status": SESSION_STATUS_PARTIAL})


# ── 7. determinism / purity of the module ────────────────────────────────

_CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})


def _module_source() -> str:
    import src.tools.paper_trade.session_replay as module

    return Path(module.__file__).read_text(encoding="utf-8")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_the_replay_module_reads_no_clock_and_no_randomness() -> None:
    source = _module_source()
    calls = [
        f"{node.lineno}:{_call_name(node.func)}()"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _call_name(node.func) in _CLOCK_CALLS
    ]
    executable = " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.STRING, tokenize.COMMENT)
    )
    assert calls == []
    assert "datetime" not in executable
    assert "random" not in executable
    assert "uuid" not in executable
    assert "sleep" not in executable
