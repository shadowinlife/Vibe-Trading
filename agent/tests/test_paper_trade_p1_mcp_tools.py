"""Todo 31 gate — the pt-v1.3 MCP bar-step and account-status tools.

Pins the runtime installed on ``tool_v13``'s seams
(:mod:`src.tools.paper_trade.mcp_runtime`):

* a live bar step advances the STORED account through the session runner and
  the status projection then reports the post-bar ledger, the watermark bar,
  the applied-bar count and the next label;
* a repeat of the same call is ``bar_already_applied`` on the in-band
  ``INVALID_REQUEST`` channel — no second step, no byte changed, no
  success-shaped document;
* a carried snapshot that disagrees with the store is ``account_state_stale``
  and names the first mismatched field, so a bar is never applied to a ledger
  the caller did not observe;
* a replay applies the golden 09:35 bar from the carried ledger plus the
  injected bars, byte-identical to the committed golden document, and writes
  nothing — the pure path never constructs a store;
* an unknown action, a replay without bars and a non-cn_a 5m surface are all
  in-band ``INVALID_REQUEST`` refusals;
* the runtime module contains no clock call, no randomness and no network
  client import.

Offline, deterministic: a tmp runtime root and the committed 5m/golden
fixtures only.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from pathlib import Path
from typing import Any

import pytest

from src.tools.paper_trade import bar_clock, mcp_runtime
from src.tools.paper_trade.account import create_account
from src.tools.paper_trade.feeds import ReplayFeed
from src.tools.paper_trade.schema import Position
from src.tools.paper_trade.store import AccountStore
from src.tools.paper_trade.tool import (
    run_paper_trade_account_create,
    run_paper_trade_account_status,
    run_paper_trade_bar_step,
)
from tests.paper_trade_5m_fixtures import continuous_bars, load_5m

_ACCOUNT = "pa_t31"
_HK_ACCOUNT = "pa_t31_hk"
_BAND_ACCOUNT = "pa_t31_band"
_NO_SLIP_ACCOUNT = "pa_t31_no_slip"
_DAY = "2026-10-20"
_SYM = "600519.SH"
_OHLCV = ("open", "high", "low", "close", "volume")
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


def _ledger() -> dict[str, Any]:
    """The golden account's 09:35 pre-bar ledger."""
    return {
        "cash": 500_000.0,
        "positions": [{"symbol": _SYM, "qty": 100.0, "avg_cost": 1420.0}],
    }


def _bars(symbol: str = _SYM) -> list[dict[str, Any]]:
    """The committed 5m fixture as the injected wire shape."""
    frame = continuous_bars(load_5m(symbol, _DAY))
    return [
        {
            "symbol": symbol,
            "bars": [
                {
                    "bar_time": timestamp.isoformat(),
                    **{column: float(row[column]) for column in _OHLCV},
                }
                for timestamp, row in frame.iterrows()
            ],
        }
    ]


def _place() -> dict[str, Any]:
    return {"action": "place", "symbol": _SYM, "side": "buy", "qty": 100.0}


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "from_date": _DAY,
        "to_date": _DAY,
        "bar_time": f"{_DAY}T09:35:00+08:00",
        "account_state": _ledger(),
        "interval": "5m",
        "session": "cn_a",
    }
    payload.update(overrides)
    return payload


def _account(**overrides: Any):
    kwargs: dict[str, Any] = {
        "account_id": _ACCOUNT,
        "market": "cn_a",
        "start_date": _DAY,
        "initial_cash": 500_000.0,
        "initial_positions": (Position(symbol=_SYM, qty=100.0, avg_cost=1420.0),),
        "today": _DAY,
        "next_trading_day": "2026-10-21",
        "now": f"{_DAY}T08:00:00+08:00",
    }
    kwargs.update(overrides)
    return create_account(**kwargs)


def _store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "paper_trade")


def _document(text: str) -> dict[str, Any]:
    return json.loads(text)


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _step_labels(store: AccountStore, account_id: str = _ACCOUNT) -> list[str]:
    bars_root = store.steps_dir(account_id) / _DAY / "bars"
    if not bars_root.is_dir():
        return []
    return sorted(path.name for path in bars_root.iterdir() if path.is_dir())


def _mcp_bar_step(**kwargs: Any) -> str:
    import mcp_server

    wrapper = getattr(mcp_server.paper_trade_bar_step, "fn", None) or getattr(
        mcp_server.paper_trade_bar_step, "__wrapped__", mcp_server.paper_trade_bar_step
    )
    return wrapper(**kwargs)


def _runtime_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountStore:
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "runtime"))
    return AccountStore()


# ── 1. live: fill + status ───────────────────────────────────────────────


def test_a_live_bar_step_fills_and_the_status_projects_the_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a created account, When 09:35 fills, Then status shows the result."""
    store = _runtime_store(tmp_path, monkeypatch)
    created = _document(
        run_paper_trade_account_create(
            {
                "account_id": _ACCOUNT,
                "market": "cn_a",
                "start_date": _DAY,
                "initial_cash": 500_000.0,
                "initial_positions": _ledger()["positions"],
                "today": _DAY,
                "next_trading_day": "2026-10-21",
            },
            store=store,
        )
    )
    assert created["status"] == "created"

    text = _mcp_bar_step(
        account_id=_ACCOUNT,
        market="cn_a",
        account_state=_ledger(),
        from_date=_DAY,
        to_date=_DAY,
        bar_time=f"{_DAY}T09:35:00+08:00",
        order_actions=[_place()],
        interval="5m",
        session="cn_a",
        bars=_bars(),
    )

    document = _document(text)
    assert document["schema_version"] == "pt-v1.3"
    assert document["tool"] == "paper_trade_step"
    assert document["bar_time"] == f"{_DAY}T09:35:00+08:00"
    assert [(fill["symbol"], fill["side"], fill["qty"], fill["price"]) for fill in document["fills"]] == [
        (_SYM, "buy", 100.0, 1422.42)
    ]
    assert document["end_state"] == {
        "cash": 357_721.02,
        "positions": [{"symbol": _SYM, "qty": 200.0, "avg_cost": 1421.21}],
    }
    assert document["order_events"][-1]["event_type"] == "filled"
    assert document["order_seq"] == 1
    assert _step_labels(store) == ["0935"]

    status = _document(
        run_paper_trade_account_status({"account_id": _ACCOUNT, "market": "cn_a"})
    )
    assert status["schema_version"] == "pt-v1.3"
    assert status["tool"] == "paper_trade_account_status"
    assert status["status"] == "ok"
    assert status["account"]["positions"] == [
        {"symbol": _SYM, "qty": 200.0, "avg_cost": 1421.21}
    ]
    assert status["watermark"] is None
    assert status["watermark_bar"] == "09:35"
    assert status["applied_bars"] == 1
    assert status["next_bar"] == "09:40"
    assert status["session_status"] == "open"
    assert status["open_orders"] == []


def test_the_status_shows_a_resting_open_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a non-marketable limit, When status reads, Then the book is visible."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())
    action = {"action": "place", "symbol": _SYM, "side": "buy", "qty": 100.0, "limit": 1300.0}

    text = run_paper_trade_bar_step(_payload(order_actions=[action], bars=_bars()))

    document = _document(text)
    assert document["fills"] == []
    assert len(document["open_orders"]) == 1
    status = _document(
        run_paper_trade_account_status({"account_id": _ACCOUNT, "market": "cn_a"})
    )
    assert status["status"] == "ok"
    assert status["open_orders"] == document["open_orders"]
    assert status["open_orders"][0]["status"] == "new"
    assert status["open_orders"][0]["remaining_qty"] == 100.0
    assert status["account"]["open_orders"] == document["open_orders"]


# ── 1b. cn_a price bands (the prev_closes input) ─────────────────────────


def test_a_supplied_prev_close_band_rejects_the_buy_with_limit_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given prev_closes, When the bar trades far above the band, Then limit_up."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())
    store.create(_account(account_id=_BAND_ACCOUNT))

    unbounded = _document(
        run_paper_trade_bar_step(_payload(order_actions=[_place()], bars=_bars()))
    )
    assert [(fill["side"], fill["qty"], fill["price"]) for fill in unbounded["fills"]] == [
        ("buy", 100.0, 1422.42)  # 1421.00 + 10 bps
    ]
    assert unbounded["rejected_orders"] == []

    bounded = _document(
        run_paper_trade_bar_step(
            _payload(
                account_id=_BAND_ACCOUNT,
                order_actions=[_place()],
                bars=_bars(),
                prev_closes={_SYM: 100000},  # 1000.00 -> band [900.00, 1100.00]
            )
        )
    )

    assert bounded["fills"] == []
    assert [row["reason_code"] for row in bounded["rejected_orders"]] == ["limit_up"]
    assert [event["event_type"] for event in bounded["order_events"]] == ["placed", "rejected"]
    assert bounded["open_orders"] == []
    assert store.load(_BAND_ACCOUNT).watermark_bar == "09:35"


def test_the_live_path_derives_the_band_from_a_feed_previous_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given no prev_closes but a feed that exposes one, Then the band applies."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())

    class _PreviousCloseFeed(ReplayFeed):
        def previous_close(self, *, symbol: str, trade_date: str) -> float:
            assert (symbol, trade_date) == (_SYM, _DAY)
            return 1000.0

    def _feed(request: Any) -> ReplayFeed:
        frames = mcp_runtime._frames_from_bars(request)
        return _PreviousCloseFeed(
            frames={symbol: {request.trade_date: frame} for symbol, frame in frames.items()}
        )

    monkeypatch.setattr(mcp_runtime, "_feed_for", _feed)

    document = _document(
        run_paper_trade_bar_step(_payload(order_actions=[_place()], bars=_bars()))
    )

    assert document["fills"] == []
    assert [row["reason_code"] for row in document["rejected_orders"]] == ["limit_up"]


# ── 1c. the account's slippage model governs the fill ────────────────────


def test_an_account_slippage_model_none_fills_unslipped_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given slippage_model=none, When the bar fills, Then zero slippage applies."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account(slippage_model="none"))

    document = _document(
        run_paper_trade_bar_step(_payload(order_actions=[_place()], bars=_bars()))
    )

    # The 09:35 reference is 1421.00 (flat OHLCV); `none` pays exactly that.
    assert [(fill["side"], fill["qty"], fill["price"]) for fill in document["fills"]] == [
        ("buy", 100.0, 1421.0)
    ]
    assert document["slippage"] == {"model": "none", "bps": "0", "amount": "0"}
    assert document["order_events"][-1]["event_type"] == "filled"
    assert document["order_events"][-1]["price"] == 1421.0
    assert store.load(_ACCOUNT).slippage_model == "none"

    status = _document(
        run_paper_trade_account_status({"account_id": _ACCOUNT, "market": "cn_a"})
    )
    assert status["account"]["slippage_model"] == "none"


# ── 2. idempotency ───────────────────────────────────────────────────────


def test_a_repeat_of_an_applied_bar_is_bar_already_applied_with_no_new_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an applied bar, When the same call repeats, Then nothing changes."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())
    payload = _payload(order_actions=[_place()], bars=_bars())
    first = _document(run_paper_trade_bar_step(payload))
    assert len(first["fills"]) == 1
    account_path = store.account_path(_ACCOUNT)
    before = hashlib.sha256(account_path.read_bytes()).hexdigest()

    second = _document(run_paper_trade_bar_step(payload))

    assert second["status"] == "error"
    assert second["code"] == "INVALID_REQUEST"
    assert second["message"].startswith("bar_already_applied")
    assert "fills" not in second and "end_state" not in second
    assert _step_labels(store) == ["0935"]
    assert hashlib.sha256(account_path.read_bytes()).hexdigest() == before
    assert store.load(_ACCOUNT).cash == first["end_state"]["cash"]


# ── 3. refusals that must not write ─────────────────────────────────────


def test_an_unknown_order_action_is_invalid_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an unknown action, When the bar runs, Then no step is written."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())

    document = _document(
        run_paper_trade_bar_step(
            _payload(order_actions=[{"action": "modify", "symbol": _SYM, "side": "buy", "qty": 1.0}])
        )
    )

    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    assert "action must be one of" in document["message"]
    assert _step_labels(store) == []


def test_a_stale_carried_state_is_account_state_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a carried cash that disagrees with the store, When sent, Then refused."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())
    stale = _ledger()
    stale["cash"] = 499_999.0

    document = _document(
        run_paper_trade_bar_step(
            _payload(account_state=stale, order_actions=[_place()], bars=_bars())
        )
    )

    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    assert document["message"].startswith("account_state_stale")
    assert "account_state.cash" in document["message"]
    assert _step_labels(store) == []
    assert store.load(_ACCOUNT).cash == 500_000.0


def test_a_non_cn_a_account_refuses_the_5m_bar_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an hk account, When a 5m cn_a bar arrives, Then it is refused."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(
        _account(
            account_id=_HK_ACCOUNT,
            market="hk",
            initial_positions=(),
            interval="1d",
        )
    )

    document = _document(
        run_paper_trade_bar_step(
            _payload(
                account_id=_HK_ACCOUNT,
                market="hk",
                account_state={"cash": 500_000.0, "positions": []},
                order_actions=[],
                bars=_bars(),
            )
        )
    )

    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    assert "cn_a 5m only" in document["message"]
    assert _step_labels(store, _HK_ACCOUNT) == []


def test_a_replay_without_bars_is_invalid_request() -> None:
    """Given mode=replay and no bars, When sent, Then the request is refused."""
    document = _document(run_paper_trade_bar_step(_payload(mode="replay")))

    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    assert "requires bars" in document["message"]


def test_an_unknown_account_status_is_account_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given no account, When status reads, Then account_not_found is in-band."""
    _runtime_store(tmp_path, monkeypatch)

    document = _document(
        run_paper_trade_account_status({"account_id": "pa_missing", "market": "cn_a"})
    )

    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    assert document["message"].startswith("account_not_found")


# ── 4. the pure replay ───────────────────────────────────────────────────


def _golden_payload(**overrides: Any) -> dict[str, Any]:
    payload = _payload(
        account_id=_GOLDEN_ACCOUNT,
        account_state=_ledger(),
        order_actions=[_place()],
        mode="replay",
        bars=_bars(),
    )
    payload.pop("interval")
    payload.pop("session")
    payload.update(overrides)
    return payload


def test_replay_reproduces_the_golden_document_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given the golden ledger and bars, When replayed, Then bytes match, no write."""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime_root))

    class _ExplodingStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("the replay path must not construct a store")

    monkeypatch.setattr(mcp_runtime, "AccountStore", _ExplodingStore)

    document = _document(run_paper_trade_bar_step(_golden_payload()))

    expected = dict(_GOLDEN["bars"][0]["document"])
    document.pop("events", None)
    expected.pop("events", None)
    assert document == expected
    assert not runtime_root.exists()


def test_replay_fills_the_golden_order_with_zero_store_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a 5m replay, When it fills, Then the golden price/ledger appear."""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime_root))

    document = _document(run_paper_trade_bar_step(_golden_payload(interval="5m", session="cn_a")))

    golden = _GOLDEN["bars"][0]["document"]
    assert document["fills"] == golden["fills"]
    assert document["end_state"] == golden["end_state"]
    assert document["nav_series"] == golden["nav_series"]
    assert not runtime_root.exists()
    assert not any(
        path.name == "paper_trade" for path in runtime_root.parent.iterdir()
    )


# ── 5. purity of the runtime module ─────────────────────────────────────

_CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})
_NETWORK_MODULES = frozenset(
    {"socket", "subprocess", "urllib", "http", "requests", "httpx", "aiohttp", "tushare"}
)
_PURE_FUNCTIONS = frozenset(
    {
        "_replay",
        "_carried_state",
        "_frames_from_bars",
        "_frame_from_rows",
        "_replay_timeline",
        "_symbol_universe",
    }
)


def _module_source() -> str:
    return Path(mcp_runtime.__file__).read_text(encoding="utf-8")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_the_mcp_runtime_reads_no_clock_and_imports_no_network_client() -> None:
    tree = ast.parse(_module_source())
    calls = [
        f"{node.lineno}:{_call_name(node.func)}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) in _CLOCK_CALLS
    ]
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    executable = " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(_module_source()).readline)
        if token.type not in (tokenize.STRING, tokenize.COMMENT)
    )
    assert calls == []
    assert imported.isdisjoint(_NETWORK_MODULES)
    assert "random" not in executable
    assert "uuid" not in executable
    assert "sleep" not in executable


def test_the_replay_path_never_names_a_store() -> None:
    tree = ast.parse(_module_source())
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in _PURE_FUNCTIONS:
            continue
        names = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
        } | {
            child.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute)
        }
        if names & {"AccountStore", "load", "open", "read_text", "write_text"}:
            offenders.append(node.name)
    assert offenders == []


def test_the_status_document_carries_the_required_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a stored account, When status reads, Then every documented key is present."""
    store = _runtime_store(tmp_path, monkeypatch)
    store.create(_account())

    document = _document(
        run_paper_trade_account_status({"account_id": _ACCOUNT, "market": "cn_a"})
    )

    assert set(document) == {
        "schema_version",
        "tool",
        "status",
        "account",
        "open_orders",
        "watermark",
        "watermark_bar",
        "applied_bars",
        "next_bar",
        "session_status",
    }
    assert document["next_bar"] == bar_clock.BAR_LABELS[0]
    assert document["applied_bars"] == 0
    assert document["session_status"] == "open"
