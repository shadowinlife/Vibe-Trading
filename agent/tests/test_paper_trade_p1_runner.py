"""Todo 24 gate — the idempotent per-bar session runner (pt-v1.3).

* the first bar applies, writes the immutable step BEFORE the account and
  leaves the account / session summary consistent with the document;
* a replay of an applied bar is ``bar_already_applied`` and changes ZERO bytes;
* a skipped bar and a bar ahead of the session are ``bar_out_of_order``;
* a crash between the step write and the account save is adopted on the next
  call: exactly one step, one set of fills, one account advance;
* the 15:00 bar closes the session (``watermark == trade_date``,
  ``session.json`` status ``closed``, 48 steps);
* a held lock is ``account_locked`` (fail fast, no waiting);
* a dry run writes nothing;
* a calendar-closed day and a conflicting stored step refuse without writes;
* an off-grid bar time is ``invalid_request``;
* a held symbol with no feed frame is valued at cost and still lets the bar
  through (the documented fail-closed missing-row policy);
* the module contains no clock call / random reference;
* the MCP helper parses through the ONE boundary parser and aligns the ledger
  carrier with the stored account.

Offline, deterministic: a tmp runtime root and the committed 5m fixtures only.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from src.tools.paper_trade import bar_clock, bar_store, session_summary, state_hash, wire
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.bar_engine import BarEngineState
from src.tools.paper_trade.feeds import ReplayFeed
from src.tools.paper_trade.schema import (
    SCHEMA_VERSION_V1_3,
    AccountState,
    BarStepRequest,
    OrderAction,
    Position,
    load_contract_schema,
    parse_slippage,
    validate_payload,
)
from src.tools.paper_trade.session_runner import (
    STATUS_ADOPTED,
    STATUS_APPLIED,
    STATUS_DRY_RUN,
    SessionRunnerError,
    bar_request,
    bar_step,
)
from src.tools.paper_trade.store import AccountStore
from tests.paper_trade_5m_fixtures import FixtureCalendar, continuous_bars, load_5m

_ACCOUNT = "pa_p1_runner"
_DAY = "2026-10-20"
_PRIOR_DAY = "2026-10-16"
_SYM = "600519.SH"
_NO_DATA = "000001.SZ"
_LAST = "15:00"
_FIRST = "09:35"
_SKIPPED = "09:45"
_NEXT = "09:40"


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


def _feed(symbols: tuple[str, ...] = (_SYM,), day: str = _DAY) -> ReplayFeed:
    return ReplayFeed(
        frames={
            symbol: {day: continuous_bars(load_5m(symbol, day))} for symbol in symbols
        }
    )


def _request(
    label: str,
    *,
    actions: tuple[OrderAction, ...] = (),
    account: AccountState | None = None,
    day: str = _DAY,
    account_id: str = _ACCOUNT,
) -> BarStepRequest:
    return BarStepRequest(
        account_id=account_id,
        market="cn_a",
        account_state=account if account is not None else AccountState(cash=200_000.0),
        from_date=day,
        to_date=day,
        bar_time=f"{day}T{label}:00+08:00",
        order_actions=actions,
        slippage=parse_slippage(None),
    )


def _place(qty: float = 100.0, symbol: str = _SYM) -> OrderAction:
    return OrderAction(action="place", symbol=symbol, side="buy", qty=qty)


def _sell(qty: float, symbol: str = _SYM) -> OrderAction:
    return OrderAction(action="place", symbol=symbol, side="sell", qty=qty)


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with the sha256 of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _step_files(store: AccountStore, label: str) -> Path:
    return store.steps_dir(_ACCOUNT) / _DAY / "bars" / label.replace(":", "")


# ── 1. the happy path ─────────────────────────────────────────────────────


def test_the_first_bar_applies_and_persists_step_account_and_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    account = store.load(_ACCOUNT)
    request = _request(_FIRST, actions=(_place(),), account=account.state())

    run = bar_step(store, request, _feed())

    assert run.status == STATUS_APPLIED
    assert run.step_path == _step_files(store, _FIRST)
    assert run.step_path.is_dir()
    assert sorted(p.name for p in run.step_path.iterdir()) == [
        "bars",
        "events.jsonl",
        "fills.jsonl",
        "manifest.json",
        "order_events.jsonl",
        "orders.jsonl",
        "positions.jsonl",
        "step.json",
    ]

    reloaded = store.load(_ACCOUNT)
    assert reloaded.watermark is None  # only the closing bar moves the day watermark
    assert reloaded.watermark_bar == _FIRST
    assert reloaded.cash == run.account.cash
    assert reloaded.positions == run.account.positions

    stored = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _FIRST)
    payload = json.loads(run.document)
    assert payload["schema_version"] == SCHEMA_VERSION_V1_3
    assert payload["bar_time"] == f"{_DAY}T{_FIRST}:00+08:00"
    assert len(payload["fills"]) == 1
    assert stored.result_hash == state_hash.state_hash(payload)
    assert stored.request_hash != stored.result_hash
    assert [row["symbol"] for row in stored.fills] == [_SYM]

    summary = session_summary.read_session_summary(store.account_dir(_ACCOUNT), _DAY)
    assert summary["status"] == "open"
    assert summary["applied_bar_count"] == 1
    assert summary["watermark_bar"] == _FIRST
    assert summary["result_hash_chain_head"] == stored.result_hash
    assert summary["counts"]["open_orders"] == 0
    assert summary["counts"]["positions"] == 1
    assert summary["counts"]["cash_cents"] == int(round(run.account.cash * 100))

    assert validate_payload(payload, load_contract_schema("result", version=SCHEMA_VERSION_V1_3)) == []


def test_a_bar_without_actions_still_advances_the_watermark(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    run = bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())

    assert run.status == STATUS_APPLIED
    assert run.outcome.fills == ()
    assert store.load(_ACCOUNT).watermark_bar == _FIRST


# ── 2. the stale-state classes: duplicate, skipped, ahead ─────────────────


def test_a_replayed_bar_is_already_applied_and_changes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    request = _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state())
    bar_step(store, request, _feed())
    before = _tree(root)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, request, _feed())

    assert excinfo.value.code == "bar_already_applied"
    assert _tree(root) == before
    assert json.loads(excinfo.value.document)["code"] == "INVALID_REQUEST"


def test_a_skipped_bar_is_out_of_order_and_names_the_expected_label(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())
    before = _tree(root)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, _request(_SKIPPED, account=store.load(_ACCOUNT).state()), _feed())

    assert excinfo.value.code == "bar_out_of_order"
    assert _NEXT in excinfo.value.message
    assert _tree(root) == before


def test_a_bar_ahead_of_the_session_is_out_of_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, _request(_NEXT, account=store.load(_ACCOUNT).state()), _feed())

    assert excinfo.value.code == "bar_out_of_order"
    assert _FIRST in excinfo.value.message
    assert store.load(_ACCOUNT).watermark_bar == ""


def test_a_bar_for_an_earlier_day_is_already_applied(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    closed = replace(store.load(_ACCOUNT), watermark=_DAY, watermark_bar=_LAST)
    store.save_account_atomic(closed)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(
            store,
            _request(_FIRST, account=closed.state(), day=_PRIOR_DAY),
            _feed(day=_PRIOR_DAY),
        )

    assert excinfo.value.code == "bar_already_applied"


# ── 3. the crash window ───────────────────────────────────────────────────


def test_a_crash_between_step_and_account_is_adopted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.create(_account())
    request = _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state())
    real_save = store.save_account_atomic
    attempts = {"count": 0}

    def explode(account) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("crash between the step and the account")
        real_save(account)

    monkeypatch.setattr(store, "save_account_atomic", explode)

    with pytest.raises(RuntimeError, match="crash between the step and the account"):
        bar_step(store, request, _feed())

    orphan = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert len(orphan.fills) == 1
    assert store.load(_ACCOUNT).watermark_bar == ""
    assert not (store.steps_dir(_ACCOUNT) / _DAY / "session.json").exists()

    run = bar_step(store, request, _feed())

    assert run.status == STATUS_ADOPTED
    assert run.step_path == _step_files(store, _FIRST)
    assert len(list((store.steps_dir(_ACCOUNT) / _DAY / "bars").iterdir())) == 1
    adopted = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert adopted.result_hash == state_hash.state_hash(json.loads(run.document))
    assert len(adopted.fills) == 1
    assert len(run.outcome.fills) == 1
    reloaded = store.load(_ACCOUNT)
    assert reloaded.watermark_bar == _FIRST
    assert reloaded.cash == run.account.cash

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, request, _feed())
    assert excinfo.value.code == "bar_already_applied"


def test_a_tampered_stored_step_is_refused_not_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    bar_store.write_bar_step(
        root,
        bar_store.BarStepContent(
            account_id=_ACCOUNT,
            trade_date=_DAY,
            bar_label=_FIRST,
            bar_time=f"{_DAY}T{_FIRST}:00+08:00",
            request_hash=hashlib.sha256(b"other request").hexdigest(),
            result_hash=hashlib.sha256(b"other result").hexdigest(),
        ),
    )
    before = _tree(root)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())

    assert excinfo.value.code == "bar_step_conflict"
    assert _tree(root) == before
    assert store.load(_ACCOUNT).watermark_bar == ""


# ── 4. session close ──────────────────────────────────────────────────────


def test_a_buy_is_persisted_in_the_t_plus_one_counter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    feed = _feed()

    run = bar_step(
        store, _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state()), feed
    )

    assert run.status == STATUS_APPLIED
    reloaded = store.load(_ACCOUNT)
    assert dict(reloaded.bought_today) == {_SYM: 100}
    assert reloaded.to_dict()["bought_today"] == {_SYM: 100}
    assert run.account == reloaded


def test_a_same_session_sell_of_todays_buy_is_rejected_t_plus_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    feed = _feed()
    bar_step(
        store, _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state()), feed
    )
    after_buy = store.load(_ACCOUNT)
    assert after_buy.positions[0].qty == 200.0

    run = bar_step(
        store, _request(_NEXT, actions=(_sell(200.0),), account=after_buy.state()), feed
    )

    assert run.status == STATUS_APPLIED
    assert run.outcome.fills == ()
    assert [(row.reason_code, row.qty) for row in run.outcome.rejected] == [("t_plus_1", 200.0)]
    assert store.load(_ACCOUNT).positions[0].qty == 200.0


def test_a_new_day_starts_with_an_empty_t_plus_one_counter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prior = _PRIOR_DAY
    store.create(_account(start_date=prior, today=prior, now=f"{prior}T08:00:00+08:00"))
    bar_step(
        store,
        _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state(), day=prior),
        _feed(day=prior),
    )
    abandoned = store.load(_ACCOUNT)
    assert abandoned.watermark is None
    assert dict(abandoned.bought_today) == {_SYM: 100}

    run = bar_step(
        store, _request(_FIRST, actions=(_sell(200.0),), account=abandoned.state()), _feed()
    )

    assert run.status == STATUS_APPLIED
    assert [(fill.side, fill.qty) for fill in run.outcome.fills] == [("sell", 200.0)]
    assert run.outcome.rejected == ()
    assert dict(store.load(_ACCOUNT).bought_today) == {}


def test_the_carried_counter_drops_zero_share_rows() -> None:
    account = _account()
    state = BarEngineState.from_account(account, trade_date=_DAY)
    zeroed = replace(state, bought_today={_SYM: Decimal("0"), "000001.SZ": Decimal("200.0")})

    carried = zeroed.to_account(account)

    assert dict(carried.bought_today) == {"000001.SZ": 200}
    assert type(carried.bought_today["000001.SZ"]) is int


def test_the_closing_bar_closes_the_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    feed = _feed()
    statuses: list[str] = []

    for label in bar_clock.BAR_LABELS:
        account = store.load(_ACCOUNT)
        actions = (_place(),) if label == _FIRST else ()
        run = bar_step(store, _request(label, actions=actions, account=account.state()), feed)
        statuses.append(run.status)

    assert set(statuses) == {STATUS_APPLIED}
    final = store.load(_ACCOUNT)
    assert final.watermark == _DAY
    assert final.watermark_bar == _LAST
    assert final.open_orders == ()
    # The 09:35 buy accrued a T+1 counter; the 15:00 settlement must persist
    # it cleared, or tomorrow's sells would be blocked by yesterday's buys.
    assert dict(final.bought_today) == {}
    assert len(list((store.steps_dir(_ACCOUNT) / _DAY / "bars").iterdir())) == 48

    summary = session_summary.read_session_summary(store.account_dir(_ACCOUNT), _DAY)
    assert summary["status"] == "closed"
    assert summary["applied_bar_count"] == 48
    assert summary["first_bar"] == _FIRST
    assert summary["last_bar"] == _LAST
    assert summary["counts"]["bars"] == 48
    assert summary["counts"]["open_orders"] == 0
    assert summary["result_hash_chain_head"] == bar_store.read_bar_step(
        store.account_dir(_ACCOUNT), _DAY, _LAST
    ).result_hash


def test_a_new_day_rolls_the_session_progress_and_t_plus_one_counter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    store.save_account_atomic(
        replace(store.load(_ACCOUNT), watermark=_PRIOR_DAY, watermark_bar=_LAST)
    )

    run = bar_step(store, _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state()), _feed())

    assert run.status == STATUS_APPLIED
    assert run.outcome.state.watermark_bar == _FIRST
    assert store.load(_ACCOUNT).watermark_bar == _FIRST
    assert store.load(_ACCOUNT).watermark == _PRIOR_DAY


# ── 5. refusals that must not write ──────────────────────────────────────


def test_a_held_lock_is_account_locked_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    before = _tree(root)

    with store.lock(_ACCOUNT):
        with pytest.raises(SessionRunnerError) as excinfo:
            bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())

    assert excinfo.value.code == "account_locked"
    assert json.loads(excinfo.value.document)["code"] == "PAPER_DATA_NOT_READY"
    assert _tree(root) == before


def test_a_closed_day_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    before = _tree(root)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(
            store,
            _request(_FIRST, account=store.load(_ACCOUNT).state()),
            _feed(),
            calendar_client=FixtureCalendar(open_days=()),
        )

    assert excinfo.value.code == "skipped_non_trading_day"
    assert _tree(root) == before


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.account_dir(_ACCOUNT)
    store.create(_account())
    before = _tree(root)

    run = bar_step(
        store, _request(_FIRST, actions=(_place(),), account=store.load(_ACCOUNT).state()), _feed(),
        dry_run=True,
    )

    assert run.status == STATUS_DRY_RUN
    assert run.step_path is None
    assert run.account == store.load(_ACCOUNT)
    assert json.loads(run.document)["schema_version"] == SCHEMA_VERSION_V1_3
    assert _tree(root) == before


def test_an_off_grid_bar_time_is_invalid_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(
            store,
            _request("09:31", account=store.load(_ACCOUNT).state()),
            _feed(),
        )

    assert excinfo.value.code == "invalid_request"
    assert "09:31" in excinfo.value.message


def test_a_request_for_another_market_is_invalid_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    request = replace(_request(_FIRST, account=store.load(_ACCOUNT).state()), market="us")

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, request, _feed())

    assert excinfo.value.code == "invalid_request"
    assert "market" in excinfo.value.message


def test_a_missing_account_is_account_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_step(store, _request(_FIRST, account=_account().state(), account_id="pa_absent"), _feed())

    assert excinfo.value.code == "account_not_found"


# ── 6. the missing-row policy ────────────────────────────────────────────


def test_a_held_symbol_without_a_frame_is_valued_at_cost(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(
        _account(
            initial_positions=(
                Position(symbol=_SYM, qty=100.0, avg_cost=1400.0),
                Position(symbol=_NO_DATA, qty=200.0, avg_cost=10.0),
            )
        )
    )

    run = bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())

    assert run.status == STATUS_APPLIED
    warnings = " ".join(run.outcome.bar_result.result.warnings)
    assert _NO_DATA in warnings
    assert "avg_cost" in warnings


# ── 7. determinism / purity of the module ────────────────────────────────

_CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})


def _module_source() -> str:
    import src.tools.paper_trade.session_runner as module

    return Path(module.__file__).read_text(encoding="utf-8")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_the_runner_module_reads_no_clock_and_no_randomness() -> None:
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


def test_the_document_hash_is_the_jcs_hash_of_its_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    run = bar_step(store, _request(_FIRST, account=store.load(_ACCOUNT).state()), _feed())

    assert wire.dumps_payload(json.loads(run.document)) == run.document
    stored = bar_store.read_bar_step(store.account_dir(_ACCOUNT), _DAY, _FIRST)
    assert stored.result_hash == state_hash.state_hash(json.loads(run.document))


# ── 8. the MCP helper ────────────────────────────────────────────────────


def _payload(**overrides) -> dict:
    payload = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "from_date": _DAY,
        "to_date": _DAY,
        "bar_time": f"{_DAY}T{_FIRST}:00+08:00",
        "account_state": {"cash": 1.0, "positions": []},
        "interval": "5m",
        "session": "cn_a",
        "order_actions": [
            {"action": "place", "symbol": _SYM, "side": "buy", "qty": 100.0}
        ],
    }
    payload.update(overrides)
    return payload


def test_bar_request_aligns_the_ledger_carrier_with_the_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    account = store.load(_ACCOUNT)

    request = bar_request(_payload(), account)

    assert request.account_state == AccountState(cash=account.cash, positions=account.positions)
    assert request.order_seq == account.order_seq
    assert request.bar_time == f"{_DAY}T{_FIRST}:00+08:00"
    assert request.order_actions[0].symbol == _SYM


def test_bar_request_refuses_another_account_and_a_non_5m_surface(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_account())
    account = store.load(_ACCOUNT)

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_request(_payload(account_id="pa_someone_else"), account)
    assert excinfo.value.code == "invalid_request"

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_request(_payload(interval="1d"), account)
    assert excinfo.value.code == "invalid_request"

    with pytest.raises(SessionRunnerError) as excinfo:
        bar_request({"account_id": _ACCOUNT}, account)
    assert excinfo.value.code == "invalid_request"
    assert json.loads(excinfo.value.document)["code"] == "INVALID_REQUEST"
