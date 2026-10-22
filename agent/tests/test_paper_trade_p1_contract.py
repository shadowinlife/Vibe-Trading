"""T6 gate — pt-v1.3 contract, defaults, frozen bytes and hash vectors.

What this module pins (P1 intraday plan todo 6; closes GAP-2/GAP-6):

* the pt-v1.3 contract file: three schemas, the version-gated five-key
  requirement, and the frozen v1.1/v1.2 files it builds on;
* ``validate_payload`` over the REAL contract nodes — a representative v1.3
  document has zero errors while a missing ``bar_time``, an unknown event type
  and an unknown order status are each rejected;
* the parser boundary: documented defaults, the slippage table, and every
  invalid enum / negative / naive-time case named in the plan;
* byte identity: real pt-v1.1 and pt-v1.2 documents produced by the committed
  5m fixtures are pinned by byte count + sha256, and ``bar=None`` never grows a
  v1.3 key;
* frozen hash vectors (computed once; changing one is a contract change, not a
  test edit);
* the three new MCP tool signatures.

Offline and deterministic: committed fixtures only (through
``test_paper_trade_5m_golden``), no clock, no network, no data fetch.

Frozen values and how they were produced (do NOT regenerate):

* contract digests — ``git show 7a34365a:schema/tool_contracts/<file> | sha256sum``;
  those exact bytes must survive any pt-v1.3 addition.
* goldens — ``tests.test_paper_trade_5m_golden._run(_payload())`` (pt-v1.2) and
  ``_run(_payload(interval="1d"))`` (pt-v1.1); sha256 + byte count pin the exact
  serialized text, and the ``dumps_payload`` round-trip proves the raw string is
  precisely that document.
* hash vectors — ``state_hash({"a": 1})`` and ``engine_fingerprint()`` under the
  current rules version.

``validate_payload`` implements a keyword subset (no ``if/then``, no
``uniqueItems``), so the version gate is asserted structurally on the loaded
document and duplicate cancel ids are asserted through the parser, which the
contract itself names as their fail-closed gate.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.tools.paper_trade import state_hash, wire
from src.tools.paper_trade.schema import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_V1_2,
    SCHEMA_VERSION_V1_3,
    PaperTradeSchemaError,
    load_contract_schema,
    parse_request,
    validate_payload,
)
from src.tools.paper_trade.schema_v13 import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TIF,
    ORDER_ACTIONS,
    ORDER_EVENT_TYPES,
    ORDER_STATUSES,
    SESSION_PHASES,
    SLIPPAGE_MODELS,
    TIFS,
    BarStepResult,
    parse_bar_step_request,
    parse_order_action,
    parse_order_state,
    parse_slippage,
)
from tests.test_paper_trade_5m_golden import _payload as golden_payload
from tests.test_paper_trade_5m_golden import _run as golden_run
from tests.test_paper_trade_v12_schema import _v11_result, _v12_result

_CONTRACT_DIR = Path(__file__).resolve().parents[2] / "schema" / "tool_contracts"
_DAY = "2026-10-20"
_BAR_TIME = "2026-10-20T09:35:00+08:00"
_ORDER_ID = "paper:pa_p1_contract:00000001"

#: The five additive result keys, in their documented order.
_V13_KEYS = ["bar_time", "order_events", "open_orders", "order_seq", "slippage"]

#: sha256 of the frozen contract files at baseline 7a34365a (see module docstring).
_V11_CONTRACT_SHA256 = "6c1f4a37c82016cd519904fa242f8bd05b25821d1da4b5cabcf5ea888f9a3024"
_V12_CONTRACT_SHA256 = "09ac240989a102f71b55773537c850987a7d97033f8c86815dd7d88af95319b2"

#: Frozen serialized bytes of a real run (byte count + sha256; see docstring).
_V11_GOLDEN_BYTES = 876
_V11_GOLDEN_SHA256 = "20d6dc4258881306a55f420aac5a120fdef3f02599d0a607f9e2eca0e4f9474d"
_V12_GOLDEN_BYTES = 998
_V12_GOLDEN_SHA256 = "641a49a3d89b754b3b7bf5a1be345151a33e797bd057b7c849b9a12e549b00e9"

#: Frozen hash vectors (see docstring).
_FROZEN_STATE_HASH = "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
_FROZEN_ENGINE_FINGERPRINT = (
    "99f38c032e80f54f5307be5ebe6af746089bcef1767c36abc17a6b25250e9ee7"
)


def _account_state() -> dict[str, Any]:
    return {"cash": 100_000.0, "positions": []}


def _request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": "pa_p1_contract",
        "market": "cn_a",
        "account_state": _account_state(),
        "from_date": _DAY,
        "to_date": _DAY,
        "bar_time": _BAR_TIME,
    }
    payload.update(overrides)
    return payload


def _place(**overrides: Any) -> dict[str, Any]:
    action: dict[str, Any] = {"action": "place", "symbol": "600519.SH", "side": "buy", "qty": 100}
    action.update(overrides)
    return action


def _order_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "order_id": _ORDER_ID,
        "account_id": "pa_p1_contract",
        "trade_date": _DAY,
        "symbol": "600519.SH",
        "side": "buy",
        "qty": 100.0,
        "remaining_qty": 100.0,
        "limit": 1500.0,
        "tif": DEFAULT_TIF,
        "status": "new",
        "bar_label": "09:35",
        "seq": 1,
    }
    row.update(overrides)
    return row


def _event(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_type": "placed",
        "order_id": _ORDER_ID,
        "client_order_id": "",
        "symbol": "600519.SH",
        "side": "buy",
        "qty": 100.0,
        "bar_time": _BAR_TIME,
    }
    row.update(overrides)
    return row


def _v13_document() -> dict[str, Any]:
    """A representative success document built by the real wire serializer."""
    bar = BarStepResult(
        result=_v12_result(),
        bar_time=_BAR_TIME,
        order_events=(_event(), _event(event_type="filled", price=1500.01)),
        open_orders=(parse_order_state(_order_row()),),
        order_seq=2,
    )
    return wire.build_success_payload(_v12_result(), bar=bar)


# ── 1. Contract file validity ──


def test_v13_contract_file_exposes_the_three_schemas() -> None:
    document = json.loads((_CONTRACT_DIR / "paper_trade_step-v1.3.json").read_text("utf-8"))
    assert document["version"] == SCHEMA_VERSION_V1_3
    assert set(document["schemas"]) == {"request", "result", "error_envelope"}
    assert load_contract_schema("result", version="pt-v1.3")["type"] == "object"
    assert load_contract_schema("request", version="pt-v1.3")["required"] == []
    assert load_contract_schema("error_envelope", version="pt-v1.3")["required"]


def test_v13_result_gates_the_five_new_keys_on_the_version() -> None:
    result = load_contract_schema("result", version="pt-v1.3")
    assert set(_V13_KEYS).isdisjoint(result["required"])
    conditional = [
        node
        for node in result["allOf"]
        if node.get("if", {}).get("properties", {}).get("schema_version", {}).get("const")
        == SCHEMA_VERSION_V1_3
    ]
    assert len(conditional) == 1
    assert conditional[0]["if"]["required"] == ["schema_version"]
    assert conditional[0]["then"]["required"] == _V13_KEYS


@pytest.mark.parametrize(
    ("filename", "digest"),
    [
        ("paper_trade_step-v1.1.json", _V11_CONTRACT_SHA256),
        ("paper_trade_step-v1.2.json", _V12_CONTRACT_SHA256),
    ],
)
def test_frozen_contract_files_are_byte_identical_to_the_baseline(
    filename: str, digest: str
) -> None:
    assert hashlib.sha256((_CONTRACT_DIR / filename).read_bytes()).hexdigest() == digest


def test_python_vocabularies_match_the_contract_enums() -> None:
    result = load_contract_schema("result", version="pt-v1.3")
    request = load_contract_schema("request", version="pt-v1.3")
    assert tuple(
        result["properties"]["order_events"]["items"]["properties"]["event_type"]["enum"]
    ) == ORDER_EVENT_TYPES
    assert tuple(
        result["properties"]["open_orders"]["items"]["properties"]["status"]["enum"]
    ) == ORDER_STATUSES
    assert tuple(request["properties"]["slippage"]["properties"]["model"]["enum"]) == SLIPPAGE_MODELS
    assert tuple(request["properties"]["session_phase"]["enum"]) == SESSION_PHASES
    actions = request["properties"]["order_actions"]["items"]["properties"]
    assert tuple(actions["action"]["enum"]) == ORDER_ACTIONS
    assert tuple(actions["tif"]["enum"]) == TIFS


# ── 2. validate_payload against the real v1.3 nodes ──


def test_a_representative_v13_document_validates_with_zero_errors() -> None:
    document = _v13_document()
    assert document["schema_version"] == SCHEMA_VERSION_V1_3
    assert list(document)[-5:] == _V13_KEYS
    assert validate_payload(document, load_contract_schema("result", version="pt-v1.3")) == []


def test_a_v13_document_without_bar_time_is_rejected() -> None:
    document = copy.deepcopy(_v13_document())
    del document["bar_time"]
    errors = validate_payload(document, load_contract_schema("result", version="pt-v1.3"))
    assert errors
    assert all("bar_time" in error for error in errors)
    assert len(errors) == 3  # order_events, open_orders and slippage each depend on it


def test_unknown_v13_event_type_is_rejected() -> None:
    document = copy.deepcopy(_v13_document())
    document["order_events"][0]["event_type"] = "modified"
    errors = validate_payload(document, load_contract_schema("result", version="pt-v1.3"))
    assert errors == [
        "$.order_events[0].event_type: value 'modified' not in enum "
        "['placed', 'canceled', 'replaced', 'filled', 'rejected', 'expired']"
    ]


def test_unknown_order_status_is_rejected() -> None:
    document = copy.deepcopy(_v13_document())
    document["open_orders"][0]["status"] = "partial"
    errors = validate_payload(document, load_contract_schema("result", version="pt-v1.3"))
    assert errors == [
        "$.open_orders[0].status: value 'partial' not in enum "
        "['new', 'filled', 'canceled', 'rejected', 'expired']"
    ]


def test_duplicate_cancel_order_ids_are_rejected_by_the_parser() -> None:
    request = load_contract_schema("request", version="pt-v1.3")
    node = request["properties"]["cancel_order_ids"]
    assert node["uniqueItems"] is True
    assert node["items"]["minLength"] == 1
    with pytest.raises(PaperTradeSchemaError, match=r"duplicates order id 'O1'"):
        parse_bar_step_request(_request(cancel_order_ids=["O1", "O1"]))


# ── 3. Parser defaults and enums ──


def test_minimal_bar_step_request_takes_the_documented_defaults() -> None:
    request = parse_bar_step_request(_request())
    assert request.schema_version == SCHEMA_VERSION_V1_3 == "pt-v1.3"
    assert request.bar_time == _BAR_TIME
    assert request.trade_date == _DAY
    assert DEFAULT_SLIPPAGE_BPS == "10"
    assert (request.slippage.model, request.slippage.bps, request.slippage.amount) == (
        "fixed_bps",
        Decimal("10"),
        Decimal("0"),
    )
    assert request.session_phase == ""
    assert request.open_orders == ()
    assert request.order_seq == 0
    assert (request.order_actions, request.cancel_order_ids, request.replace_orders) == ((), (), ())


def test_prev_closes_parse_to_canonical_cents() -> None:
    request = parse_bar_step_request(
        _request(
            prev_closes={
                "600519.SH": 100000,  # an integer IS cents (1000.00)
                "000001.SZ": 1000.0,  # a float is yuan
                "000002.SZ": Decimal("1250.50"),  # a Decimal is yuan, exact
            }
        )
    )
    assert dict(request.prev_closes) == {
        "600519.SH": 100000,
        "000001.SZ": 100000,
        "000002.SZ": 125050,
    }
    assert request.prev_close_yuan() == {
        "600519.SH": 1000.0,
        "000001.SZ": 1000.0,
        "000002.SZ": 1250.5,
    }
    assert dict(parse_bar_step_request(_request()).prev_closes) == {}


@pytest.mark.parametrize(
    "closes",
    [
        {"600519.SH": 0},
        {"600519.SH": -100},
        {"600519.SH": True},
        {"600519.SH": "1000"},
        {"600519.SH": float("nan")},
        {"": 100000},
    ],
    ids=["zero", "negative", "bool", "string", "nan", "empty-symbol"],
)
def test_invalid_prev_closes_are_refused(closes: dict[str, Any]) -> None:
    with pytest.raises(PaperTradeSchemaError, match="prev_closes"):
        parse_bar_step_request(_request(prev_closes=closes))


def test_prev_closes_is_a_v13_only_key() -> None:
    with pytest.raises(PaperTradeSchemaError, match="prev_closes belongs to the pt-v1.3"):
        parse_request({"market": "cn_a", "prev_closes": {"600519.SH": 100000}})


def test_default_tif_on_an_order_action_is_day() -> None:
    # TIF lives on the action row, not on BarStepRequest.
    action = parse_order_action(_place())
    assert action.tif == DEFAULT_TIF == "day"
    assert parse_bar_step_request(_request(order_actions=[_place()])).order_actions[0].tif == "day"


@pytest.mark.parametrize(
    ("raw", "model", "bps", "amount"),
    [
        ({"model": "none"}, "none", "0", "0"),
        ({"model": "fixed_bps"}, "fixed_bps", "10", "0"),
        ({"model": "fixed_bps", "bps": 25}, "fixed_bps", "25", "0"),
        ({"model": "fixed_amount", "amount": "0.02"}, "fixed_amount", "0", "0.02"),
    ],
    ids=["none", "fixed_bps-default", "fixed_bps-25", "fixed_amount"],
)
def test_parse_slippage_table(raw: dict, model: str, bps: str, amount: str) -> None:
    spec = parse_slippage(raw)
    assert (spec.model, spec.bps, spec.amount) == (model, Decimal(bps), Decimal(amount))
    assert spec.as_wire() == {"model": model, "bps": bps, "amount": amount}


@pytest.mark.parametrize(
    ("case", "overrides", "match"),
    [
        ("unknown-tif", {"order_actions": [_place(tif="gtc")]}, "tif must be one of"),
        ("unknown-status", {"open_orders": [_order_row(status="partial")]}, "status must be one of"),
        ("unknown-model", {"slippage": {"model": "vwap"}}, "slippage.model must be one of"),
        (
            "unknown-action",
            {"order_actions": [{"action": "modify", "symbol": "600519.SH", "side": "buy", "qty": 100}]},
            "action must be one of",
        ),
        ("unknown-phase", {"session_phase": "pre_open"}, "session_phase must be one of"),
        ("negative-bps", {"slippage": {"model": "fixed_bps", "bps": -1}}, "bps must be >= 0"),
        (
            "amount-on-fixed-bps",
            {"slippage": {"model": "fixed_bps", "amount": "0.02"}},
            "amount must be 0",
        ),
        ("naive-bar-time", {"bar_time": "2026-10-20T09:35:00"}, "timezone-aware"),
        ("bar-time-plus-09", {"bar_time": "2026-10-20T09:35:00+09:00"}, "Asia/Shanghai"),
        ("two-session-days", {"to_date": "2026-10-21"}, "one session day"),
        ("duplicate-cancel", {"cancel_order_ids": ["O1", "O1"]}, "duplicates order id"),
        (
            "order-seq-not-above",
            {"open_orders": [_order_row(seq=5)], "order_seq": 5},
            "order_seq must be greater",
        ),
        ("place-carries-order-id", {"order_actions": [_place(order_id="O1")]}, "must be empty for a place"),
        (
            "cancel-carries-qty",
            {"order_actions": [{"action": "cancel", "order_id": "O1", "qty": 100}]},
            "cancel must carry no qty",
        ),
    ],
    ids=[
        "unknown-tif", "unknown-status", "unknown-model", "unknown-action",
        "unknown-phase", "negative-bps", "amount-on-fixed-bps", "naive-bar-time",
        "bar-time-plus-09", "two-session-days", "duplicate-cancel",
        "order-seq-not-above", "place-carries-order-id", "cancel-carries-qty",
    ],
)
def test_invalid_bar_step_payloads_are_rejected(case: str, overrides: dict, match: str) -> None:
    with pytest.raises(PaperTradeSchemaError, match=match):
        parse_bar_step_request(_request(**overrides))


# ── 4. v1.1/v1.2 byte identity ──


def test_v12_golden_bytes_are_unchanged() -> None:
    raw, document = golden_run(golden_payload())
    assert document["schema_version"] == SCHEMA_VERSION_V1_2
    assert set(document).isdisjoint(_V13_KEYS)
    assert wire.dumps_payload(document) == raw
    assert len(raw.encode("utf-8")) == _V12_GOLDEN_BYTES
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == _V12_GOLDEN_SHA256


def test_v11_golden_bytes_are_unchanged() -> None:
    raw, document = golden_run(golden_payload(interval="1d"))
    assert document["schema_version"] == SCHEMA_VERSION
    assert set(document).isdisjoint(_V13_KEYS) and set(document).isdisjoint(
        {"engine_fingerprint", "canceled_orders", "events"}
    )
    assert wire.dumps_payload(document) == raw
    assert len(raw.encode("utf-8")) == _V11_GOLDEN_BYTES
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == _V11_GOLDEN_SHA256


def test_bar_none_keeps_the_v11_v12_documents_frozen() -> None:
    for result, version in (
        (_v11_result(), SCHEMA_VERSION),
        (_v12_result(), SCHEMA_VERSION_V1_2),
    ):
        payload = wire.build_success_payload(result)
        assert payload["schema_version"] == version
        assert set(payload).isdisjoint(_V13_KEYS)


# ── 5. Frozen hash vectors ──


def test_frozen_hash_vectors() -> None:
    assert state_hash.canonicalize({"a": 1}) == '{"a":1}'
    assert state_hash.state_hash({"a": 1}) == _FROZEN_STATE_HASH
    assert state_hash.engine_fingerprint() == _FROZEN_ENGINE_FINGERPRINT


def test_engine_fingerprint_changes_with_the_slippage_model() -> None:
    base = state_hash.engine_fingerprint()
    assert state_hash.engine_fingerprint(slippage_model="fixed_bps") != base
    assert state_hash.engine_fingerprint(slippage_model="fixed_bps") == (
        state_hash.engine_fingerprint(slippage_model="fixed_bps")
    )


def test_bar_state_hash_covers_the_v13_order_state() -> None:
    default = state_hash.compute_bar_state_hash(account_state=_account_state())
    assert default == state_hash.state_hash({"account_state": _account_state()})

    row = parse_order_state(_order_row())
    without_seq = state_hash.compute_bar_state_hash(account_state=_account_state(), open_orders=(row,))
    with_seq = state_hash.compute_bar_state_hash(
        account_state=_account_state(), open_orders=(row,), order_seq=2
    )
    assert without_seq != with_seq
    assert without_seq != default and with_seq != default


# ── 6. MCP registry surface ──


def _mcp_tool_parameters() -> dict[str, dict[str, Any]]:
    """Read the registry through the same public ``tools/list`` surface the
    MCP smoke test exercises, without spawning a subprocess."""
    agent_dir = Path(__file__).resolve().parent.parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    module = importlib.import_module("mcp_server")
    return {tool.name: tool.parameters for tool in asyncio.run(module.mcp.list_tools())}


def test_mcp_registry_exposes_the_three_v13_tools_with_their_required_args() -> None:
    tools = _mcp_tool_parameters()
    assert {
        "paper_trade_bar_step",
        "paper_trade_account_create",
        "paper_trade_account_status",
    } <= set(tools)

    bar_step = tools["paper_trade_bar_step"]
    assert set(bar_step["required"]) == {
        "account_id",
        "market",
        "account_state",
        "from_date",
        "to_date",
        "bar_time",
    }
    assert {
        "order_actions",
        "cancel_order_ids",
        "replace_orders",
        "slippage",
        "session_phase",
        "open_orders",
        "order_seq",
    } <= set(bar_step["properties"])

    account_create = tools["paper_trade_account_create"]
    assert {"start_date", "today", "next_trading_day"} <= set(account_create["required"])
    assert tools["paper_trade_account_status"]["required"] == ["account_id", "market"]
