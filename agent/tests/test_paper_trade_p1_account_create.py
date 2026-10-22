"""Todo 30 gate — the pt-v1.3 account-create MCP surface.

Pins the contract of ``run_paper_trade_account_create`` after it moved to
``tool_v13.py`` and became store-backed:

* a valid payload writes ``account.json`` + ``steps/`` under the INJECTED
  store, returns ONE in-band pt-v1.3 document whose account block always
  carries ``open_orders == []`` / ``order_seq == 0``, and round-trips through
  :meth:`AccountStore.load`;
* ``create_account`` remains the single validation authority: a past start is
  ``start_date_in_past``, an arbitrary future date is
  ``start_date_not_admissible``, ``5m`` outside cn_a and duplicate initial
  symbols are ``invalid_account`` / ``invalid_initial_positions`` — every one
  an ``INVALID_REQUEST`` envelope that names the stable code;
* a duplicate id is ``account_already_exists`` and the original bytes are
  untouched (the store's lock-guarded create is the authority; the
  ``existing_account_ids`` pre-check only preserves the early error and writes
  nothing);
* determinism: the package never reads a clock, ``created_at`` derives from
  the injected ``today``, and two independent stores receive the same bytes.

No network, no clock, no real runtime root: every test injects a ``tmp_path``
store (or sets ``VIBE_TRADING_HOME`` for the MCP-wrapper test).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from src.tools.paper_trade.store import ACCOUNT_FILE, AccountStore, StoreError
from src.tools.paper_trade.tool import (
    run_paper_trade_account_create,
    run_paper_trade_account_status,
)
from src.tools.paper_trade.tool_v13 import (
    run_paper_trade_account_create as create_from_v13,
)

TODAY = "2026-10-20"
NEXT_DAY = "2026-10-21"
PAST_DAY = "2026-10-19"
ARBITRARY_FUTURE = "2026-10-22"
ACCOUNT_ID = "pa_t30"

_TOOL_SOURCE = Path(__file__).resolve().parents[1] / "src" / "tools" / "paper_trade" / "tool_v13.py"

#: Every key a pt-v1.3 account summary must carry (``to_dict(include_v13=True)``
#: on a fresh account).  A missing key is a silent "unknown", not a default.
_REQUIRED_SUMMARY_KEYS = {
    "schema_version",
    "account_id",
    "market",
    "start_date",
    "initial_cash",
    "initial_positions",
    "cash",
    "positions",
    "watermark",
    "engine_fingerprint",
    "created_at",
    "interval",
    "path_model",
    "fill_model",
    "non_trading_days",
    "open_orders",
    "order_seq",
    "watermark_bar",
    "slippage_model",
    "session_phase",
}

#: Clock call sites that must never appear in the package clock-free surface.
_CLOCK_CALL = re.compile(
    r"\b(?:datetime\.(?:now|utcnow|today)|date\.today|time\.(?:time|monotonic|perf_counter))\b"
)


def _payload(**overrides: Any) -> dict[str, Any]:
    """The minimal valid MCP payload (only the six required arguments)."""
    payload: dict[str, Any] = {
        "account_id": ACCOUNT_ID,
        "market": "cn_a",
        "start_date": TODAY,
        "initial_cash": 100_000.0,
        "today": TODAY,
        "next_trading_day": NEXT_DAY,
    }
    payload.update(overrides)
    return payload


def _store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "paper_trade")


def _document(text: str) -> dict[str, Any]:
    return json.loads(text)


def _message(text: str) -> str:
    """The deterministic message of an in-band INVALID_REQUEST refusal."""
    document = _document(text)
    assert document["status"] == "error"
    assert document["code"] == "INVALID_REQUEST"
    return document["message"]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _account_tree(store: AccountStore) -> dict[str, str]:
    """Every file under the account directory keyed by relative path."""
    root = store.account_dir(ACCOUNT_ID)
    return {
        str(path.relative_to(root)): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ── success path ──


def test_a_valid_create_writes_the_account_and_steps_directory(tmp_path: Path) -> None:
    """Given a valid payload, When created, Then account.json + steps/ exist."""
    store = _store(tmp_path)
    text = run_paper_trade_account_create(_payload(), store=store)

    document = _document(text)
    assert document["schema_version"] == "pt-v1.3"
    assert document["tool"] == "paper_trade_account_create"
    assert document["status"] == "created"

    summary = document["account"]
    assert summary["account_id"] == ACCOUNT_ID
    assert summary["market"] == "cn_a"
    assert summary["start_date"] == TODAY
    assert summary["open_orders"] == []
    assert summary["order_seq"] == 0
    assert summary["watermark"] is None

    assert store.account_path(ACCOUNT_ID).is_file()
    assert store.steps_dir(ACCOUNT_ID).is_dir()
    assert {path.name for path in store.account_dir(ACCOUNT_ID).iterdir()} >= {
        ACCOUNT_FILE,
        "steps",
    }
    # The response is the account summary only: no absolute store path leaks.
    assert str(tmp_path) not in text


def test_the_success_document_is_a_complete_v13_account_summary(tmp_path: Path) -> None:
    """Given a created account, When returned, Then every v1.3 key is present."""
    store = _store(tmp_path)
    summary = _document(run_paper_trade_account_create(_payload(), store=store))["account"]

    assert set(summary) == _REQUIRED_SUMMARY_KEYS
    assert summary["schema_version"] == "pt-account-v1"
    assert summary["start_date"] == TODAY
    assert summary["created_at"] == f"{TODAY}T00:00:00+08:00"
    assert summary["open_orders"] == []


def test_the_created_account_round_trips_through_the_store(tmp_path: Path) -> None:
    """Given a created account, When reloaded, Then it equals the summary."""
    store = _store(tmp_path)
    document = _document(run_paper_trade_account_create(_payload(), store=store))

    loaded = store.load(ACCOUNT_ID)

    assert loaded.to_dict(include_v13=True) == document["account"]
    assert loaded.account_id == ACCOUNT_ID
    assert loaded.open_orders == ()
    assert loaded.order_seq == 0
    assert loaded.watermark is None
    assert loaded.created_at == f"{TODAY}T00:00:00+08:00"
    assert loaded.interval == "5m"
    assert loaded.path_model == "bar_median"
    assert loaded.fill_model == "all_or_none"
    assert loaded.slippage_model == ""


def test_a_start_on_the_next_trading_day_is_admissible(tmp_path: Path) -> None:
    """Given start_date == next_trading_day, When created, Then it succeeds."""
    store = _store(tmp_path)
    document = _document(
        run_paper_trade_account_create(
            _payload(start_date=NEXT_DAY, next_trading_day=NEXT_DAY), store=store
        )
    )
    assert document["status"] == "created"
    assert document["account"]["start_date"] == NEXT_DAY
    assert store.load(ACCOUNT_ID).start_date == NEXT_DAY


def test_initial_positions_and_models_are_persisted(tmp_path: Path) -> None:
    """Given positions + non-default models, When created, Then all persist."""
    store = _store(tmp_path)
    positions = [{"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0}]
    document = _document(
        run_paper_trade_account_create(
            _payload(
                initial_positions=positions,
                path_model="bar_close",
                fill_model="all_or_none",
                slippage_model="fixed_bps",
            ),
            store=store,
        )
    )

    summary = document["account"]
    assert summary["initial_positions"] == positions
    assert summary["positions"] == positions
    assert summary["path_model"] == "bar_close"
    assert summary["slippage_model"] == "fixed_bps"
    assert store.load(ACCOUNT_ID).positions[0].symbol == "600519.SH"


# ── determinism ──


def test_two_independent_stores_receive_identical_account_bytes(tmp_path: Path) -> None:
    """Given the same payload, When created twice, Then the bytes match."""
    first_store = _store(tmp_path / "first")
    second_store = _store(tmp_path / "second")

    first_text = run_paper_trade_account_create(_payload(), store=first_store)
    second_text = run_paper_trade_account_create(_payload(), store=second_store)

    assert first_text == second_text
    assert (
        first_store.account_path(ACCOUNT_ID).read_bytes()
        == second_store.account_path(ACCOUNT_ID).read_bytes()
    )


def test_the_same_create_twice_is_created_then_refused(tmp_path: Path) -> None:
    """Given the same payload twice, When repeated, Then the second refuses."""
    store = _store(tmp_path)
    first = _document(run_paper_trade_account_create(_payload(), store=store))
    second_text = run_paper_trade_account_create(_payload(), store=store)

    assert first["status"] == "created"
    assert _message(second_text).startswith("account_already_exists")
    assert _document(second_text).get("status") == "error"


# ── refusals ──


def test_a_past_start_date_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """Given start_date < today, When created, Then start_date_in_past."""
    store = _store(tmp_path)
    message = _message(
        run_paper_trade_account_create(_payload(start_date=PAST_DAY), store=store)
    )
    assert message.startswith("start_date_in_past")
    assert not store.account_dir(ACCOUNT_ID).exists()


def test_a_future_date_that_is_not_the_next_trading_day_is_refused(tmp_path: Path) -> None:
    """Given an arbitrary future start, When created, Then not admissible."""
    store = _store(tmp_path)
    message = _message(
        run_paper_trade_account_create(_payload(start_date=ARBITRARY_FUTURE), store=store)
    )
    assert message.startswith("start_date_not_admissible")
    assert not store.account_dir(ACCOUNT_ID).exists()


def test_five_minute_interval_outside_cn_a_is_refused(tmp_path: Path) -> None:
    """Given interval=5m (the default) on hk, When created, Then invalid."""
    store = _store(tmp_path)
    message = _message(
        run_paper_trade_account_create(_payload(market="hk", start_date=TODAY), store=store)
    )
    assert message.startswith("invalid_account")
    assert "5m is cn_a-only" in message
    assert not store.account_dir(ACCOUNT_ID).exists()


def test_duplicate_initial_positions_keep_their_code(tmp_path: Path) -> None:
    """Given one symbol listed twice, When created, Then its stable code."""
    store = _store(tmp_path)
    duplicated = [
        {"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0},
        {"symbol": "600519.SH", "qty": 200, "avg_cost": 1490.0},
    ]
    message = _message(
        run_paper_trade_account_create(_payload(initial_positions=duplicated), store=store)
    )
    assert message.startswith("invalid_initial_positions")
    assert not store.account_dir(ACCOUNT_ID).exists()


def test_a_duplicate_id_is_refused_and_the_original_bytes_survive(tmp_path: Path) -> None:
    """Given an existing account, When re-created, Then bytes are untouched."""
    store = _store(tmp_path)
    run_paper_trade_account_create(_payload(), store=store)
    before = _account_tree(store)
    original = store.account_path(ACCOUNT_ID).read_bytes()

    message = _message(run_paper_trade_account_create(_payload(), store=store))

    assert message.startswith("account_already_exists")
    assert store.account_path(ACCOUNT_ID).read_bytes() == original
    assert _account_tree(store) == before
    assert list(store.steps_dir(ACCOUNT_ID).iterdir()) == []


def test_the_existing_ids_precheck_preserves_the_early_conflict(tmp_path: Path) -> None:
    """Given a known id, When created, Then the conflict needs no store write."""
    store = _store(tmp_path)
    message = _message(
        run_paper_trade_account_create(
            _payload(), store=store, existing_account_ids=[ACCOUNT_ID]
        )
    )
    assert message.startswith("account_already_exists")
    assert not store.account_dir(ACCOUNT_ID).exists()


def test_an_unknown_store_refusal_keeps_its_code(tmp_path: Path) -> None:
    """Given a store refusal that is not a duplicate, Then its code survives."""

    class _RefusingStore:
        """A store whose create raises a non-duplicate StoreError."""

        def create(self, created: Any) -> Path:
            raise StoreError("account_corrupt", "account.json for pa_t30 is unreadable")

    message = _message(
        run_paper_trade_account_create(_payload(), store=_RefusingStore())
    )
    assert message == "account_corrupt: account.json for pa_t30 is unreadable"


# ── clock-free + re-export surface ──


def test_the_create_module_never_reads_a_clock() -> None:
    """Given the tool_v13 source, Then it contains no clock call or import."""
    source = _TOOL_SOURCE.read_text(encoding="utf-8")
    assert _CLOCK_CALL.search(source) is None
    assert "import time" not in source
    assert "import datetime" not in source
    assert "from datetime" not in source


def test_tool_re_exports_the_v13_entry_points() -> None:
    """Given the split, Then ``tool`` re-exports the same create function."""
    assert run_paper_trade_account_create is create_from_v13
    assert callable(run_paper_trade_account_status)


# ── MCP wrapper wiring ──


def test_the_mcp_wrapper_creates_under_the_runtime_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Given the MCP wrapper, When called, Then it uses the store-backed path."""
    import mcp_server

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime_root))
    wrapper = getattr(mcp_server.paper_trade_account_create, "fn", None) or getattr(
        mcp_server.paper_trade_account_create,
        "__wrapped__",
        mcp_server.paper_trade_account_create,
    )

    text = wrapper(
        account_id=ACCOUNT_ID,
        market="cn_a",
        start_date=TODAY,
        initial_cash=100_000.0,
        today=TODAY,
        next_trading_day=NEXT_DAY,
    )

    document = _document(text)
    assert document["status"] == "created"
    assert document["account"]["interval"] == "5m"
    assert document["account"]["open_orders"] == []
    account_path = runtime_root / "paper_trade" / "accounts" / ACCOUNT_ID / ACCOUNT_FILE
    assert account_path.is_file()
