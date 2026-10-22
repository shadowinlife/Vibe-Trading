"""T5 — incremental account model (``account.py``).

Pure in-memory tests: the start-date rule is decided from INJECTED ``today`` /
``next_trading_day`` values (never a clock), the document round-trips losslessly,
and the two ledger transitions refuse to move the watermark backwards.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from types import MappingProxyType

import pytest

from src.tools.paper_trade.account import (
    ACCOUNT_SCHEMA_VERSION,
    INVALID_ACCOUNT,
    INVALID_INITIAL_POSITIONS,
    START_DATE_IN_PAST,
    START_DATE_NOT_ADMISSIBLE,
    V13_ACCOUNT_KEYS,
    AccountError,
    PaperTradeAccount,
    create_account,
    require_date,
)
from src.tools.paper_trade.schema import AccountState, OrderState, Position
from src.tools.paper_trade.state_hash import (
    compute_bar_state_hash,
    compute_received_state_hash,
    effective_fee_model,
    state_hash,
)
from src.tools.paper_trade.storage_io import dumps

TODAY = "2026-03-02"  # Monday
NEXT_TRADING_DAY = "2026-03-03"
NOW = "2026-03-02T15:05:00+08:00"


def _create(**overrides):
    kwargs = {
        "account_id": "acct_t5",
        "market": "cn_a",
        "start_date": TODAY,
        "initial_cash": 100000.0,
        "initial_positions": (),
        "today": TODAY,
        "next_trading_day": NEXT_TRADING_DAY,
        "now": NOW,
    }
    kwargs.update(overrides)
    return create_account(**kwargs)


def test_start_date_today_is_admissible() -> None:
    account = _create()
    assert (account.account_id, account.market, account.start_date) == ("acct_t5", "cn_a", TODAY)
    assert account.cash == account.initial_cash == 100000.0
    assert account.positions == account.initial_positions == ()
    assert account.watermark is None
    assert account.interval == "5m"
    assert account.schema_version == ACCOUNT_SCHEMA_VERSION


def test_start_date_next_trading_day_is_admissible() -> None:
    account = _create(start_date=NEXT_TRADING_DAY)
    assert account.start_date == NEXT_TRADING_DAY
    assert account.watermark is None


def test_start_date_in_the_past_is_refused_with_a_stable_code() -> None:
    with pytest.raises(AccountError) as excinfo:
        _create(start_date="2026-02-27")
    assert excinfo.value.code == START_DATE_IN_PAST
    assert "backtest" in excinfo.value.message


@pytest.mark.parametrize("start_date", ["2026-03-04", "2026-04-01"])
def test_start_date_beyond_the_next_trading_day_is_not_admissible(start_date: str) -> None:
    with pytest.raises(AccountError) as excinfo:
        _create(start_date=start_date)
    assert excinfo.value.code == START_DATE_NOT_ADMISSIBLE


@pytest.mark.parametrize("account_id", ["", "../escape", "a/b", ".hidden", "x" * 65])
def test_account_id_must_be_a_safe_single_path_segment(account_id: str) -> None:
    with pytest.raises(AccountError) as excinfo:
        _create(account_id=account_id)
    assert excinfo.value.code == INVALID_ACCOUNT


def test_unknown_market_and_negative_cash_are_refused() -> None:
    with pytest.raises(AccountError) as market_error:
        _create(market="crypto")
    assert market_error.value.code == INVALID_ACCOUNT
    with pytest.raises(AccountError) as cash_error:
        _create(initial_cash=-1.0)
    assert cash_error.value.code == INVALID_ACCOUNT


def test_duplicate_and_degenerate_initial_positions_are_refused() -> None:
    duplicate = (
        Position("600519.SH", 100, 1500.0),
        Position("600519.SH", 200, 1510.0),
    )
    with pytest.raises(AccountError) as duplicate_error:
        _create(initial_positions=duplicate)
    assert duplicate_error.value.code == INVALID_INITIAL_POSITIONS

    with pytest.raises(AccountError) as qty_error:
        _create(initial_positions=(Position("600519.SH", 0, 1500.0),))
    assert qty_error.value.code == INVALID_INITIAL_POSITIONS

    with pytest.raises(AccountError) as cost_error:
        _create(initial_positions=(Position("600519.SH", 100, -1.0),))
    assert cost_error.value.code == INVALID_INITIAL_POSITIONS


def test_five_minute_interval_is_cn_a_only() -> None:
    with pytest.raises(AccountError) as excinfo:
        _create(market="us")
    assert excinfo.value.code == INVALID_ACCOUNT
    assert "cn_a-only" in excinfo.value.message
    assert _create(market="us", interval="1d").request_fields() == {}


def test_initial_positions_are_carried_into_the_live_ledger() -> None:
    account = _create(initial_positions=[{"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0}])
    assert account.initial_positions == account.positions
    assert account.state() == AccountState(cash=100000.0, positions=account.positions)
    assert account.state_payload() == {
        "cash": 100000.0,
        "positions": [{"symbol": "600519.SH", "qty": 100.0, "avg_cost": 1500.0}],
    }


def test_document_round_trip_is_lossless_and_ignores_unknown_keys() -> None:
    account = _create(initial_positions=(Position("600519.SH", 100, 1500.0),))
    document = account.to_dict()
    assert document["schema_version"] == ACCOUNT_SCHEMA_VERSION
    assert PaperTradeAccount.from_dict(document) == account

    document["a_field_from_a_newer_writer"] = {"ignored": True}
    assert PaperTradeAccount.from_dict(document) == account


def test_document_with_an_unknown_schema_version_is_refused() -> None:
    document = _create().to_dict()
    document["schema_version"] = "pt-account-v99"
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert excinfo.value.code == INVALID_ACCOUNT


def test_state_digest_is_the_frozen_jcs_state_hash() -> None:
    account = _create()
    assert account.state_digest() == state_hash(account.state_payload())


def test_request_fields_are_emitted_only_when_non_default() -> None:
    assert _create(interval="1d").request_fields() == {}
    assert _create().request_fields() == {
        "interval": "5m",
        "path_model": "bar_median",
        "fill_model": "all_or_none",
        "session": "cn_a",
    }


def test_advance_replaces_the_ledger_and_moves_the_watermark_forward() -> None:
    account = _create(initial_positions=(Position("600519.SH", 100, 1500.0),))
    advanced = account.with_advance(
        end_state=AccountState(cash=90000.0, positions=(Position("600519.SH", 50, 1500.0),)),
        watermark=TODAY,
        engine_fingerprint="fp-day-1",
    )
    assert advanced.cash == 90000.0
    assert advanced.positions == (Position("600519.SH", 50, 1500.0),)
    assert advanced.initial_positions == account.initial_positions
    assert advanced.watermark == TODAY
    assert advanced.engine_fingerprint == "fp-day-1"


def test_advance_refuses_to_move_the_watermark_backwards() -> None:
    account = _create().with_advance(
        end_state=AccountState(cash=100000.0), watermark=NEXT_TRADING_DAY
    )
    with pytest.raises(ValueError, match="watermark must move forward"):
        account.with_advance(end_state=AccountState(cash=1.0), watermark=NEXT_TRADING_DAY)
    with pytest.raises(ValueError, match="watermark must move forward"):
        account.with_non_trading_day(TODAY)


def test_non_trading_day_advances_without_touching_the_ledger() -> None:
    account = _create()
    skipped = account.with_non_trading_day(TODAY)
    assert skipped.watermark == TODAY
    assert skipped.non_trading_days == (TODAY,)
    assert (skipped.cash, skipped.positions) == (account.cash, account.positions)
    # Recording the same closed day twice must not duplicate the evidence.
    assert skipped.with_non_trading_day(NEXT_TRADING_DAY).non_trading_days == (TODAY, NEXT_TRADING_DAY)


@pytest.mark.parametrize("value", ["2026-3-2", "20260302", "2026-13-01", "", None])
def test_require_date_rejects_loose_spellings(value) -> None:
    with pytest.raises(AccountError):
        require_date(value, "value")


def test_account_is_frozen() -> None:
    account = _create()
    with pytest.raises(dataclasses.FrozenInstanceError):
        account.cash = 1.0  # type: ignore[misc]


# ── pt-v1.3 account state (P1 todo 22) ──
#
# The v1.3 fields live in ``account.json`` only for a v1.3 account.  A v1.2
# document must stay byte-identical (captured before this change) and a v1.2
# account must never silently gain the keys.  ``bought_today`` is the T+1
# counter that made the per-bar replay lose its sellable quantity: it must
# round-trip as whole shares and stay out of every v1.2 byte.

#: ``dumps(account.to_dict())`` for the v1.2 account built below, captured
#: BEFORE the v1.3 fields existed — the compatibility oracle.
V12_DOCUMENT = (
    '{"account_id":"acct_v12","cash":90000.0,"created_at":"2026-03-02T15:05:00+08:00",'
    '"engine_fingerprint":"fp-v12","fill_model":"all_or_none","initial_cash":100000.0,'
    '"initial_positions":[{"avg_cost":1500.0,"qty":100.0,"symbol":"600519.SH"}],'
    '"interval":"5m","market":"cn_a","non_trading_days":["2026-03-02"],'
    '"path_model":"bar_median","positions":[{"avg_cost":1500.0,"qty":50,"symbol":"600519.SH"}],'
    '"schema_version":"pt-account-v1","start_date":"2026-03-02","watermark":"2026-03-03"}'
)
V12_DOCUMENT_SHA256 = "d1dddaa941a3f0d7be3c035ae787edc563667d58bf09308e4e6edb0eec8d74a9"
V12_STATE_DIGEST = "0ba0decfc9f28609e72268d8324926100d5b4e08eb038f3d7a81e13afa9dbb9e"


def _v12_account() -> PaperTradeAccount:
    account = _create(
        account_id="acct_v12",
        initial_positions=[{"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0}],
    )
    account = account.with_non_trading_day(TODAY)
    return account.with_advance(
        end_state=AccountState(cash=90000.0, positions=(Position("600519.SH", 50, 1500.0),)),
        watermark=NEXT_TRADING_DAY,
        engine_fingerprint="fp-v12",
    )


def _order(**overrides) -> OrderState:
    fields = {
        "order_id": "acct_t5:2026-03-02:09:35:00000001",
        "account_id": "acct_t5",
        "trade_date": TODAY,
        "symbol": "600519.SH",
        "side": "buy",
        "qty": 100.0,
        "remaining_qty": 100.0,
        "limit": None,
        "tif": "day",
        "status": "new",
        "bar_label": "09:35",
        "client_order_id": "c-1",
        "filled_qty": 0.0,
        "avg_fill_price": 0.0,
        "seq": 1,
    }
    fields.update(overrides)
    return OrderState(**fields)


def _v13_account() -> PaperTradeAccount:
    return dataclasses.replace(
        _create(),
        open_orders=(_order(),),
        order_seq=2,
        watermark_bar="09:35",
        slippage_model="fixed_bps",
        session_phase="continuous",
        bought_today=MappingProxyType({"600519.SH": 100}),
    )


def _v13_document() -> dict:
    return _v13_account().to_dict()


def test_v12_payload_is_byte_identical_and_carries_no_v13_keys() -> None:
    account = _v12_account()
    document = account.to_dict()
    assert dumps(document) == V12_DOCUMENT
    assert hashlib.sha256(dumps(document).encode()).hexdigest() == V12_DOCUMENT_SHA256
    assert account.state_digest() == V12_STATE_DIGEST
    assert not set(V13_ACCOUNT_KEYS) & set(document)
    assert account.has_v13_state is False


def test_v12_document_without_v13_keys_loads_with_defaults() -> None:
    restored = PaperTradeAccount.from_dict(json.loads(V12_DOCUMENT))
    assert restored == _v12_account()
    assert (restored.open_orders, restored.order_seq) == ((), 0)
    assert (restored.watermark_bar, restored.slippage_model, restored.session_phase) == ("", "", "")
    assert restored.has_v13_state is False
    assert restored.to_dict() == _v12_account().to_dict()


def test_v13_account_round_trips_every_carried_bar_field() -> None:
    account = _v13_account()
    document = account.to_dict()
    assert set(V13_ACCOUNT_KEYS) <= set(document)
    assert document["open_orders"] == [
        {
            "order_id": "acct_t5:2026-03-02:09:35:00000001",
            "account_id": "acct_t5",
            "trade_date": TODAY,
            "symbol": "600519.SH",
            "side": "buy",
            "qty": 100.0,
            "remaining_qty": 100.0,
            "limit": None,
            "tif": "day",
            "status": "new",
            "bar_label": "09:35",
            "client_order_id": "c-1",
            "filled_qty": 0.0,
            "avg_fill_price": 0.0,
            "seq": 1,
        }
    ]
    assert (document["order_seq"], document["watermark_bar"]) == (2, "09:35")
    assert (document["slippage_model"], document["session_phase"]) == ("fixed_bps", "continuous")
    assert document["bought_today"] == {"600519.SH": 100}

    restored = PaperTradeAccount.from_dict(document)
    assert restored == account
    assert restored.open_orders == (_order(),)
    assert dict(restored.bought_today) == {"600519.SH": 100}
    assert restored.to_dict() == document  # stable under a second round trip


def test_v13_keys_can_be_opted_into_for_a_default_account() -> None:
    account = _create()
    assert not set(V13_ACCOUNT_KEYS) & set(account.to_dict())
    opted_in = account.to_dict(include_v13=True)
    # The five structural keys are always present; the T+1 counter is written
    # only while it carries a quantity (its absent value IS the empty counter).
    assert (set(V13_ACCOUNT_KEYS) - {"bought_today"}) <= set(opted_in)
    assert "bought_today" not in opted_in
    assert (opted_in["open_orders"], opted_in["order_seq"]) == ([], 0)
    assert (opted_in["watermark_bar"], opted_in["slippage_model"], opted_in["session_phase"]) == (
        "",
        "",
        "",
    )


def test_a_fresh_account_starts_with_an_empty_t_plus_one_counter() -> None:
    account = _create()
    assert dict(account.bought_today) == {}
    assert account.has_v13_state is False
    assert "bought_today" not in account.to_dict()


def test_bought_today_is_written_as_sorted_whole_shares() -> None:
    account = dataclasses.replace(
        _create(), bought_today=MappingProxyType({"600519.SH": 100, "000001.SZ": 200})
    )

    document = account.to_dict()

    assert document["bought_today"] == {"000001.SZ": 200, "600519.SH": 100}
    assert list(document["bought_today"]) == ["000001.SZ", "600519.SH"]
    assert all(type(qty) is int for qty in document["bought_today"].values())
    assert PaperTradeAccount.from_dict(document).bought_today == account.bought_today


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, "bought_today['600519.SH'] must be >= 0"),
        (True, "bought_today['600519.SH'] must be an integer"),
        (1.5, "bought_today['600519.SH'] must be an integer"),
        ("100", "bought_today['600519.SH'] must be an integer"),
        (None, "bought_today['600519.SH'] must be an integer"),
    ],
)
def test_a_malformed_t_plus_one_counter_is_refused(value: object, expected: str) -> None:
    document = _v13_document()
    document["bought_today"] = {"600519.SH": value}
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert excinfo.value.code == INVALID_ACCOUNT
    assert expected in excinfo.value.message


def test_a_non_object_t_plus_one_counter_is_refused() -> None:
    document = _v13_document()
    document["bought_today"] = [["600519.SH", 100]]
    with pytest.raises(AccountError, match="bought_today must be an object"):
        PaperTradeAccount.from_dict(document)


def test_a_v13_document_without_the_counter_loads_empty() -> None:
    document = _v13_document()
    document.pop("bought_today")
    restored = PaperTradeAccount.from_dict(document)
    assert dict(restored.bought_today) == {}
    assert restored.to_dict() == document


def test_v13_state_survives_a_ledger_advance() -> None:
    account = _v13_account()
    advanced = account.with_advance(end_state=AccountState(cash=90000.0), watermark=TODAY)
    assert advanced.open_orders == account.open_orders
    assert (advanced.order_seq, advanced.watermark_bar) == (2, "09:35")
    assert (advanced.slippage_model, advanced.session_phase) == ("fixed_bps", "continuous")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("side", "long", "open_orders[0].side"),
        ("status", "resting", "open_orders[0].status"),
        ("bar_label", "9:35", "open_orders[0].bar_label"),
    ],
)
def test_malformed_open_order_row_is_refused(field: str, value: object, expected: str) -> None:
    document = _v13_document()
    document["open_orders"][0][field] = value
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert excinfo.value.code == INVALID_ACCOUNT
    assert expected in excinfo.value.message


def test_new_order_with_nothing_remaining_is_refused() -> None:
    document = _v13_document()
    document["open_orders"][0].update({"qty": 100.0, "remaining_qty": 0.0, "filled_qty": 100.0})
    with pytest.raises(AccountError, match="remaining_qty must be > 0 for a new order"):
        PaperTradeAccount.from_dict(document)


def test_open_orders_must_be_an_array_of_unique_rows() -> None:
    document = _v13_document()
    document["open_orders"] = {"order_id": "x"}
    with pytest.raises(AccountError, match="open_orders must be an array"):
        PaperTradeAccount.from_dict(document)

    duplicate = _v13_document()
    duplicate["open_orders"].append({**duplicate["open_orders"][0], "client_order_id": "c-2"})
    with pytest.raises(AccountError, match="duplicate order_id"):
        PaperTradeAccount.from_dict(duplicate)

    duplicate_client = _v13_document()
    duplicate_client["open_orders"].append(
        {**duplicate_client["open_orders"][0], "order_id": "acct_t5:2026-03-02:09:35:00000002"}
    )
    with pytest.raises(AccountError, match="duplicate client_order_id"):
        PaperTradeAccount.from_dict(duplicate_client)


def test_open_order_from_another_account_is_refused() -> None:
    document = _v13_document()
    document["open_orders"][0]["account_id"] = "someone_else"
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert "open_orders[0].account_id must be 'acct_t5'" in excinfo.value.message


@pytest.mark.parametrize("value", [-1, True, 1.5, "2", None])
def test_order_seq_must_be_a_non_negative_integer(value: object) -> None:
    document = _v13_document()
    document["order_seq"] = value
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert excinfo.value.code == INVALID_ACCOUNT
    assert "order_seq" in excinfo.value.message


def test_order_seq_must_exceed_every_open_order_seq() -> None:
    document = _v13_document()
    document["order_seq"] = 1  # equal to the single open order's seq
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert "order_seq must be greater than every open order seq (1), got 1" in excinfo.value.message


@pytest.mark.parametrize("value", ["9:35", "0935", "24:00", "09:60", "09:35:00", "09:35 "])
def test_watermark_bar_must_be_hh_mm_or_empty(value: str) -> None:
    document = _v13_document()
    document["watermark_bar"] = value
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert "watermark_bar must be HH:MM or empty" in excinfo.value.message


@pytest.mark.parametrize("field", ["slippage_model", "session_phase"])
def test_unknown_slippage_model_and_session_phase_are_refused(field: str) -> None:
    document = _v13_document()
    document[field] = "lunch_break"
    with pytest.raises(AccountError) as excinfo:
        PaperTradeAccount.from_dict(document)
    assert excinfo.value.code == INVALID_ACCOUNT
    assert field in excinfo.value.message


def test_create_account_accepts_a_v13_slippage_model() -> None:
    assert _create().slippage_model == ""
    account = _create(slippage_model="fixed_bps")
    assert account.slippage_model == "fixed_bps"
    assert account.has_v13_state is True
    assert account.to_dict()["slippage_model"] == "fixed_bps"
    with pytest.raises(AccountError) as excinfo:
        _create(slippage_model="twap")
    assert excinfo.value.code == INVALID_ACCOUNT


def test_bar_state_hash_covers_open_orders_while_the_v12_path_ignores_them() -> None:
    account = _v13_account()
    without_orders = dataclasses.replace(account, open_orders=(), order_seq=0)

    def bar_hash(candidate: PaperTradeAccount) -> str:
        return compute_bar_state_hash(
            account_state=candidate.state(),
            open_orders=candidate.open_orders,
            order_seq=candidate.order_seq,
            bar_time="2026-03-02T09:35:00+08:00",
            slippage_model=candidate.slippage_model,
        )

    def received_hash(candidate: PaperTradeAccount) -> str:
        return compute_received_state_hash(
            account_id=candidate.account_id,
            market=candidate.market,
            account_state=candidate.state_payload(),
            fee_model=effective_fee_model(candidate.market),
            from_date=TODAY,
            to_date=NEXT_TRADING_DAY,
        )

    assert bar_hash(account) != bar_hash(without_orders)
    assert received_hash(account) == received_hash(without_orders)
    assert account.state_digest() == without_orders.state_digest()
