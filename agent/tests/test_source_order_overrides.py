"""Tests for env-configurable per-market source-order overrides.

Covers the registry machinery behind ``MARKET_DATA_ORDER_<MARKET>`` env
vars: snapshot-gated refresh, in-place chain reassignment (visible through
both by-name imports and attribute access), validation (permutation-only),
reset-to-default, and the guarantee that override-free environments never
touch :data:`FALLBACK_CHAINS` (existing ``patch.dict``/``setitem`` tests
depend on that).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from unittest.mock import patch

import backtest.loaders.registry as registry
from backtest.loaders.registry import (
    FALLBACK_CHAINS,
    parse_source_order,
    refresh_source_order_overrides,
    source_order_env_var,
)

_MISSING = object()
_ALL_ORDER_ENV_VARS = [
    source_order_env_var(market) for market in registry._DEFAULT_CHAINS
]


@pytest.fixture(autouse=True)
def _clean_order_env():
    """Scrub order env vars and restore default chains around each test.

    Teardown re-scrubs *before* refreshing (not relying on monkeypatch undo
    order), so chains always return to defaults even if the test patched
    env via monkeypatch, and the saved shell values are put back afterwards
    for fixtures further out.
    """
    saved = {var: os.environ.get(var, _MISSING) for var in _ALL_ORDER_ENV_VARS}
    for var in _ALL_ORDER_ENV_VARS:
        os.environ.pop(var, None)
    refresh_source_order_overrides()
    try:
        yield
    finally:
        for var in _ALL_ORDER_ENV_VARS:
            os.environ.pop(var, None)
        refresh_source_order_overrides()
        for var, value in saved.items():
            if value is not _MISSING:
                os.environ[var] = value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_source_order_env_var() -> None:
    assert source_order_env_var("a_share") == "MARKET_DATA_ORDER_A_SHARE"
    assert (
        source_order_env_var("vietnam_equity")
        == "MARKET_DATA_ORDER_VIETNAM_EQUITY"
    )


def test_parse_source_order_strips_lowercases_and_drops_empty() -> None:
    assert parse_source_order(" TUSHARE, tencent ,, ") == ["tushare", "tencent"]
    assert parse_source_order("") == []


def test_is_valid_source_order_requires_exact_permutation() -> None:
    assert registry.is_valid_source_order(
        "crypto", ["local", "okx", "binance", "ccxt", "yfinance"]
    )
    assert not registry.is_valid_source_order("crypto", ["okx"])  # subset
    assert not registry.is_valid_source_order("crypto", ["okx"] * 5)  # dupes
    assert not registry.is_valid_source_order(
        "crypto", ["okx", "binance", "ccxt", "yfinance", "stooq"]
    )  # foreign member
    assert not registry.is_valid_source_order("no_such_market", ["okx"])


def test_get_default_source_order_returns_copy() -> None:
    chain = registry.get_default_source_order("us_equity")
    chain.append("mutated")
    assert "mutated" not in registry._DEFAULT_CHAINS["us_equity"]
    assert (
        registry.get_default_source_order("us_equity")
        == registry._DEFAULT_CHAINS["us_equity"]
    )
    assert registry.get_default_source_order("no_such_market") == []


# ---------------------------------------------------------------------------
# Refresh behavior
# ---------------------------------------------------------------------------


def test_import_time_refresh_left_defaults_intact() -> None:
    for market, chain in FALLBACK_CHAINS.items():
        assert chain == registry._DEFAULT_CHAINS[market]
    assert registry.get_source_order_override("a_share") is None


def test_no_env_overrides_refresh_is_noop_for_patched_chains() -> None:
    """Regression guard: override-free env must never touch FALLBACK_CHAINS.

    test_ui_services monkeypatches individual chain entries; a refresh that
    blindly reassigned from defaults would wipe those patches.
    """
    with patch.dict(
        registry.FALLBACK_CHAINS, {"ca_equity": ["yfinance", "yahoo", "local"]}
    ):
        refresh_source_order_overrides()
        assert registry.FALLBACK_CHAINS["ca_equity"] == [
            "yfinance", "yahoo", "local",
        ]


def test_refresh_gated_on_env_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MARKET_DATA_ORDER_CRYPTO", "binance,okx,ccxt,yfinance,local"
    )
    refresh_source_order_overrides()
    first = registry.FALLBACK_CHAINS["crypto"]
    assert first == ["binance", "okx", "ccxt", "yfinance", "local"]

    # Unchanged env -> no-op, entry object untouched.
    refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["crypto"] is first

    # Changed env -> reassignment (new list object).
    monkeypatch.setenv(
        "MARKET_DATA_ORDER_CRYPTO", "ccxt,okx,binance,yfinance,local"
    )
    refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["crypto"] is not first
    assert registry.FALLBACK_CHAINS["crypto"][0] == "ccxt"


def test_override_reorders_in_place_for_byname_importers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runner.py/market_data.py do ``from ...registry import FALLBACK_CHAINS``.

    The reassignment must be a setitem on the same dict object so those
    by-name references observe the new order.
    """
    monkeypatch.setenv("MARKET_DATA_ORDER_FUTURES", "local,akshare")
    refresh_source_order_overrides()
    assert FALLBACK_CHAINS["futures"] == ["local", "akshare"]

    override = registry.get_source_order_override("futures")
    assert override == ["local", "akshare"]
    # Returned copies — callers cannot corrupt internal state.
    override.append("zzz")
    assert registry.get_source_order_override("futures") == ["local", "akshare"]


@pytest.mark.parametrize(
    "raw",
    [
        "tushare,akshare",  # dropped 'local'
        "tushare,akshare,local,ccxt",  # extra member
        "tushare,tushare,akshare,local",  # duplicate
        "tushare,akshare,local,yahoo",  # member of another market
    ],
)
def test_invalid_values_keep_default_and_warn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    raw: str,
) -> None:
    monkeypatch.setenv("MARKET_DATA_ORDER_FUND", raw)
    with caplog.at_level(logging.WARNING):
        refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["fund"] == ["tushare", "akshare", "local"]
    assert registry.get_source_order_override("fund") is None
    assert any("MARKET_DATA_ORDER_FUND" in r.message for r in caplog.records)


def test_empty_string_resets_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MARKET_DATA_ORDER_A_SHARE",
        "tushare,clickhouse,tencent,eastmoney,baostock,akshare,gildata,local",
    )
    refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["a_share"][0] == "tushare"

    monkeypatch.setenv("MARKET_DATA_ORDER_A_SHARE", "")
    refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["a_share"] == registry.get_default_source_order("a_share")
    assert registry.get_source_order_override("a_share") is None


def test_default_snapshot_never_aliased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKET_DATA_ORDER_MACRO", "tushare,akshare,local")
    refresh_source_order_overrides()
    assert registry._DEFAULT_CHAINS["macro"] == ["akshare", "tushare", "local"]

    monkeypatch.delenv("MARKET_DATA_ORDER_MACRO")
    refresh_source_order_overrides()
    assert registry._DEFAULT_CHAINS["macro"] == ["akshare", "tushare", "local"]
    assert registry.FALLBACK_CHAINS["macro"] == ["akshare", "tushare", "local"]


def test_ensure_registered_rechecks_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh hook sits before the ``_registered`` early return."""
    monkeypatch.setenv(
        "MARKET_DATA_ORDER_KR_EQUITY", "yahoo,pykrx,yfinance,local"
    )
    registry._ensure_registered()
    assert registry.FALLBACK_CHAINS["kr_equity"][0] == "yahoo"


# ---------------------------------------------------------------------------
# R5 / T10 gate: the paper-trading us_equity reorder shipped in .env.example
# ---------------------------------------------------------------------------

#: R5 suggested value (REQUIREMENTS R5): sina/eastmoney lead, yahoo/stooq sink.
_R5_US_EQUITY_ORDER = (
    "sina,eastmoney,yahoo,stooq,yfinance,tiingo,fmp,finnhub,"
    "alphavantage,longbridge,akshare,local"
)


def test_us_equity_r5_reorder_is_a_valid_permutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T10 gate: setting MARKET_DATA_ORDER_US_EQUITY reorders the chain.

    The env key suffix is ``us_equity`` (not ``us``, E4), and the knob only
    accepts a permutation of the default chain.
    """
    order = parse_source_order(_R5_US_EQUITY_ORDER)
    assert registry.is_valid_source_order("us_equity", order)

    monkeypatch.setenv("MARKET_DATA_ORDER_US_EQUITY", _R5_US_EQUITY_ORDER)
    refresh_source_order_overrides()
    assert registry.FALLBACK_CHAINS["us_equity"] == order
    assert registry.FALLBACK_CHAINS["us_equity"][:2] == ["sina", "eastmoney"]
    assert registry.get_source_order_override("us_equity") == order


def test_env_example_us_equity_line_stays_a_permutation_of_the_default() -> None:
    """Drift guard: the shipped .env.example value must stay a permutation.

    Pins the documented R5 line against the LIVE registry default chain, so
    a future chain edit that drops/renames a source turns this test red
    instead of silently shipping an invalid override (invalid values warn and
    keep the default — the paper path would then lead with yahoo/stooq).
    """
    env_path = Path(__file__).resolve().parents[1] / ".env.example"
    marker = "MARKET_DATA_ORDER_US_EQUITY="
    lines = [
        line for line in env_path.read_text(encoding="utf-8").splitlines() if marker in line
    ]
    assert lines, "agent/.env.example lost the R5 MARKET_DATA_ORDER_US_EQUITY line"
    value = lines[0].split(marker, 1)[1].strip()
    order = parse_source_order(value)
    assert registry.is_valid_source_order("us_equity", order)
    assert order[:2] == ["sina", "eastmoney"]
