"""Integration tests for the ClickHouse OHLCV loader.

Tests that depend on a reachable ClickHouse instance are gated behind a
module-level ``skip_if_no_ch`` marker.  Set the ``CH_HOST`` / ``CH_PORT`` /
``CH_USER`` / ``CH_PASSWORD`` / ``CH_DATABASE`` environment variables (or
use the defaults from ``src.config.env_schema.DataConfig``) to point at a
running ClickHouse server.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from src.clickhouse_connector import ClickHouseConnector

# ---------------------------------------------------------------------------
# Module-level skip flag
# ---------------------------------------------------------------------------


def _ch_is_available() -> bool:
    """Check whether ClickHouse is reachable, returning False on any error."""
    try:
        return ClickHouseConnector().health_check()
    except Exception:  # noqa: BLE001 — check is best-effort
        return False


_ch_available = _ch_is_available()

skip_if_no_ch = pytest.mark.skipif(
    not _ch_available,
    reason=(
        "ClickHouse is not reachable — set CH_HOST / CH_PORT / CH_USER / "
        "CH_PASSWORD / CH_DATABASE env vars to a running instance"
    ),
)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _six_months_ago() -> str:
    return (dt.date.today() - dt.timedelta(days=180)).isoformat()


def _yesterday() -> str:
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Test 1 — Health check (requires CH)
# ---------------------------------------------------------------------------


@skip_if_no_ch
def test_health_check_available() -> None:
    """CH reachable → ``is_available()`` returns True."""
    from backtest.loaders.clickhouse import DataLoader

    loader = DataLoader()
    assert loader.is_available() is True


# ---------------------------------------------------------------------------
# Test 2 — Historical daily fetch (requires CH)
# ---------------------------------------------------------------------------


@skip_if_no_ch
def test_fetch_historical_daily() -> None:
    """Fetch 600519.SH for a 6-month historical range → DataFrame with
    at least 50 columns and more than 100 rows."""
    from backtest.loaders.clickhouse import DataLoader

    loader = DataLoader()
    result = loader.fetch(
        ["600519.SH"],
        _six_months_ago(),
        _yesterday(),
        interval="1D",
    )
    assert "600519.SH" in result
    df = result["600519.SH"]
    assert len(df.columns) >= 50, f"Expected at least 50 columns, got {len(df.columns)}"
    assert len(df) > 100, f"Expected more than 100 rows, got {len(df)}"


# ---------------------------------------------------------------------------
# Test 3 — Federation with today's data (requires CH)
# ---------------------------------------------------------------------------


@skip_if_no_ch
def test_fetch_federation_today() -> None:
    """Fetch a range that includes today → federation works,
    CH data + mocked network data are merged."""
    from backtest.loaders.clickhouse import DataLoader

    # Mock network data for today so the federation path is exercised
    # without depending on a live network source.
    today_idx = pd.to_datetime([_today()])
    mock_network = pd.DataFrame(
        {
            "open": [1800.0],
            "high": [1820.0],
            "low": [1790.0],
            "close": [1810.0],
            "volume": [5000000.0],
        },
        index=today_idx,
    )

    with patch.object(
        DataLoader,
        "_fetch_network_bars",
        return_value=mock_network,
    ):
        loader = DataLoader()
        result = loader.fetch(
            ["600519.SH"],
            _yesterday(),
            _today(),
            interval="1D",
        )

    assert "600519.SH" in result
    df = result["600519.SH"]
    # Should have at least 2 rows: yesterday from CH + today from network.
    assert len(df) >= 2, f"Expected at least 2 rows (CH + network), got {len(df)}"


# ---------------------------------------------------------------------------
# Test 4 — Non-A-share symbol (no CH needed)
# ---------------------------------------------------------------------------


def test_fetch_non_ashare() -> None:
    """Fetch AAPL.US → returns empty dict (loader skips non-A-share codes)."""
    from backtest.loaders.clickhouse import DataLoader

    loader = DataLoader()
    result = loader.fetch(
        ["AAPL.US"],
        "2024-01-01",
        "2024-12-31",
        interval="1D",
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Test 5 — Minute interval (no CH needed)
# ---------------------------------------------------------------------------


def test_fetch_minute_interval() -> None:
    """Fetch with interval="5m" → returns empty dict (CH has no minute data)."""
    from backtest.loaders.clickhouse import DataLoader

    loader = DataLoader()
    result = loader.fetch(
        ["600519.SH"],
        "2024-01-01",
        "2024-01-31",
        interval="5m",
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Test 6 — Unreachable CH (no CH needed)
# ---------------------------------------------------------------------------


def test_is_available_unreachable() -> None:
    """Mock CH as unreachable → ``is_available()`` returns False, never raises."""
    from backtest.loaders.clickhouse import DataLoader

    with patch.object(
        ClickHouseConnector,
        "health_check",
        return_value=False,
    ):
        loader = DataLoader()
        assert loader.is_available() is False


# ---------------------------------------------------------------------------
# Test 7 — Loader registered in registry (no CH needed)
# ---------------------------------------------------------------------------


def test_loader_registered() -> None:
    """Check that ``LOADER_REGISTRY["clickhouse"]`` exists and has
    the correct ``name`` and ``markets`` attributes."""
    import backtest.loaders.clickhouse  # noqa: F401 — triggers @register

    from backtest.loaders.registry import LOADER_REGISTRY

    assert (
        "clickhouse" in LOADER_REGISTRY
    ), "clickhouse loader not found in LOADER_REGISTRY"
    cls = LOADER_REGISTRY["clickhouse"]
    assert cls.name == "clickhouse"
    assert cls.markets == {"a_share"}


# ---------------------------------------------------------------------------
# Test 8 — Auto source routing (no CH needed)
# ---------------------------------------------------------------------------


def test_source_auto_routes_to_ch() -> None:
    """With source="auto", A-share codes route to clickhouse via
    ``detect_source()``."""
    from src.market_data import detect_source

    assert detect_source("600519.SH") == "clickhouse"
    assert detect_source("000001.SZ") == "clickhouse"
    assert detect_source("835174.BJ") == "clickhouse"


# ---------------------------------------------------------------------------
# Table routing — per instrument class (no CH needed)
# ---------------------------------------------------------------------------


class _RecordingConnector:
    """Connector double that records every query and replays one frame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.queries: list[tuple[str, dict[str, str]]] = []

    def health_check(self) -> bool:
        return True

    def query(self, sql: str, params: dict[str, str] | None = None) -> pd.DataFrame:
        self.queries.append((sql, dict(params or {})))
        return self.frame.copy()


def _sample_bar_frame(table: str) -> pd.DataFrame:
    """One bar shaped like *table*'s served rows (String vs Date trade_date)."""
    trade_date: str | dt.date = (
        "20240102" if table == "fund_daily" else dt.date(2024, 1, 2)
    )
    return pd.DataFrame(
        [
            {
                "ts_code": "000000.XX",
                "trade_date": trade_date,
                "pre_close": 3.00,
                "open": 3.00,
                "high": 3.10,
                "low": 2.90,
                "close": 3.05,
                "change": 0.05,
                "vol": 1000.0,
                "amount": 3050.0,
            }
        ]
    )


def _loader_with_frame(
    monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame
) -> tuple[Any, _RecordingConnector]:
    """Build a loader whose connector is the recording double.

    The loader cache is bypassed by routing ``cached_loader_fetch`` straight
    to its ``fetch`` callback, so each call reaches the connector.
    """
    from backtest.loaders import clickhouse as ch

    connector = _RecordingConnector(frame)
    monkeypatch.setattr(
        ch.DataLoader, "_build_connector", staticmethod(lambda: connector)
    )
    monkeypatch.setattr(ch, "cached_loader_fetch", lambda **kwargs: kwargs["fetch"]())
    return ch.DataLoader(), connector


def test_resolve_table_routes_by_instrument_class() -> None:
    from backtest.loaders.clickhouse import resolve_table

    assert resolve_table("600519.SH") == "stk_factor_pro"
    assert resolve_table("000001.SZ") == "stk_factor_pro"
    assert resolve_table("430139.BJ") == "stk_factor_pro"
    assert resolve_table("510300.SH") == "fund_daily"
    assert resolve_table("588000.SH") == "fund_daily"
    assert resolve_table("159915.SZ") == "fund_daily"
    assert resolve_table("000300.SH") == "idx_factor_pro"
    assert resolve_table("000688.SH") == "idx_factor_pro"
    assert resolve_table("399001.SZ") == "idx_factor_pro"
    assert resolve_table("899050.BJ") == "idx_factor_pro"
    assert resolve_table("AAPL.US") is None
    assert resolve_table("BTC-USDT") is None


def test_000001_suffix_disambiguates_index_from_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``000001.SH`` is the SSE Composite; ``000001.SZ`` is Ping An Bank."""
    from backtest.loaders.clickhouse import resolve_table

    assert resolve_table("000001.SH") == "idx_factor_pro"
    assert resolve_table("000001.SZ") == "stk_factor_pro"

    loader, connector = _loader_with_frame(
        monkeypatch, _sample_bar_frame("stk_factor_pro")
    )
    loader.fetch(["000001.SH", "000001.SZ"], "2024-01-01", "2024-01-31", interval="1D")

    assert len(connector.queries) == 2
    assert "FROM idx_factor_pro" in connector.queries[0][0]
    assert "FROM stk_factor_pro" in connector.queries[1][0]


def test_etf_fetch_queries_fund_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    from backtest.loaders.clickhouse_columns import FUND_DAILY_COLUMNS

    loader, connector = _loader_with_frame(monkeypatch, _sample_bar_frame("fund_daily"))
    result = loader.fetch(["510300.SH"], "2024-01-01", "2024-01-31", interval="1D")

    assert "510300.SH" in result
    sql = connector.queries[-1][0]
    assert "FROM fund_daily" in sql
    assert "FROM stk_factor_pro" not in sql
    for column in FUND_DAILY_COLUMNS:
        assert f"`{column}`" in sql
    assert "pct_chg" not in sql  # present in fund_daily, not needed for OHLCV
    assert "close_hfq" not in sql  # stk_factor_pro-only column

    df = result["510300.SH"]
    assert list(df.index) == [pd.Timestamp("2024-01-02")]
    assert "volume" in df.columns
    assert df.attrs["_provenance"]["table"] == "fund_daily"


def test_index_fetch_queries_idx_factor_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    from backtest.loaders.clickhouse_columns import IDX_FACTOR_PRO_COLUMNS

    loader, connector = _loader_with_frame(
        monkeypatch, _sample_bar_frame("idx_factor_pro")
    )
    result = loader.fetch(["000300.SH"], "2024-01-01", "2024-01-31", interval="1D")

    assert "000300.SH" in result
    sql = connector.queries[-1][0]
    assert "FROM idx_factor_pro" in sql
    assert "FROM stk_factor_pro" not in sql
    for column in IDX_FACTOR_PRO_COLUMNS:
        assert f"`{column}`" in sql
    assert "pct_change" not in sql  # idx_factor_pro's spelling; must not leak in
    assert "_hfq" not in sql  # stk_factor_pro-only factor columns

    assert result["000300.SH"].attrs["_provenance"]["table"] == "idx_factor_pro"


def test_stock_fetch_queries_stk_factor_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    from backtest.loaders.clickhouse_columns import (
        STK_FACTOR_PRO_COLUMNS,
        stk_factor_pro_column_sql,
    )

    loader, connector = _loader_with_frame(
        monkeypatch, _sample_bar_frame("stk_factor_pro")
    )
    result = loader.fetch(["600519.SH"], "2024-01-01", "2024-01-31", interval="1D")

    assert "600519.SH" in result
    sql = connector.queries[-1][0]
    assert "FROM stk_factor_pro" in sql
    assert stk_factor_pro_column_sql() in sql
    assert len(STK_FACTOR_PRO_COLUMNS) == 199
    assert result["600519.SH"].attrs["_provenance"]["table"] == "stk_factor_pro"


def test_fund_daily_string_date_predicate_casts_the_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fund_daily.trade_date`` is a ``YYYYMMDD`` String, never a Date."""
    loader, connector = _loader_with_frame(monkeypatch, _sample_bar_frame("fund_daily"))
    loader.fetch(["510300.SH"], "2024-01-01", "2024-01-31", interval="1D")

    sql, params = connector.queries[-1]
    assert "toDate(trade_date) >= toDate({start_date:String})" in sql
    assert "toDate(trade_date) <= toDate({end_date:String})" in sql
    assert "trade_date >= {start_date:String}" not in sql
    assert params == {
        "param_ts_code": "510300.SH",
        "param_start_date": "2024-01-01",
        "param_end_date": "2024-01-31",
    }


@pytest.mark.parametrize(
    "raw_trade_date",
    ["2025-04-30", "20250430", dt.date(2025, 4, 30)],
)
def test_normalize_ch_frame_accepts_date_and_legacy_string(raw_trade_date: Any) -> None:
    """A live ``Date`` column and a legacy ``YYYYMMDD`` String both parse."""
    from backtest.loaders.clickhouse import DataLoader

    frame = _sample_bar_frame("fund_daily")
    frame["trade_date"] = raw_trade_date

    normalized = DataLoader._normalize_ch_frame(frame)

    assert list(normalized.index) == [pd.Timestamp("2025-04-30")]
    assert "volume" in normalized.columns


def test_normalize_ch_frame_accepts_a_partially_migrated_column() -> None:
    """Rows written before and after the type change may coexist."""
    from backtest.loaders.clickhouse import DataLoader

    frame = _sample_bar_frame("fund_daily")
    frame = pd.concat([frame, frame.copy()], ignore_index=True)
    frame["trade_date"] = ["20250430", "2025-05-06"]

    normalized = DataLoader._normalize_ch_frame(frame)

    assert list(normalized.index) == [
        pd.Timestamp("2025-04-30"),
        pd.Timestamp("2025-05-06"),
    ]


def test_normalize_ch_frame_rejects_an_unparseable_trade_date() -> None:
    """A future format change must fail loudly, not become NaT rows."""
    from backtest.loaders.clickhouse import DataLoader

    frame = _sample_bar_frame("fund_daily")
    frame["trade_date"] = "not-a-date"

    with pytest.raises(ValueError):
        DataLoader._normalize_ch_frame(frame)


def test_fund_daily_date_typed_trade_date_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production ``Date`` shape (ISO 8601) must not fail the fetch."""
    frame = _sample_bar_frame("fund_daily")
    frame["trade_date"] = "2025-04-30"
    loader, _ = _loader_with_frame(monkeypatch, frame)

    result = loader.fetch(["510300.SH"], "2025-04-01", "2025-04-30", interval="1D")

    assert list(result["510300.SH"].index) == [pd.Timestamp("2025-04-30")]
    assert result["510300.SH"].attrs["_provenance"]["table"] == "fund_daily"


def test_empty_clickhouse_result_warns_with_symbol_table_and_range(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loader, _ = _loader_with_frame(monkeypatch, pd.DataFrame())
    with caplog.at_level(logging.WARNING, logger="backtest.loaders.clickhouse"):
        result = loader.fetch(["510300.SH"], "2024-01-01", "2024-01-31", interval="1D")

    assert result == {}
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "510300.SH" in message
    assert "fund_daily" in message
    assert "2024-01-01" in message
    assert "2024-01-31" in message


def test_market_data_provenance_uses_served_table() -> None:
    from src.market_data import _clickhouse_provenance

    frame = pd.DataFrame({"close": [3.05]})
    frame.attrs["_provenance"] = {"table": "fund_daily"}
    provenance = _clickhouse_provenance(frame)

    assert provenance["table"] == "fund_daily"
    assert "fund_daily" in provenance["caliber"]


def test_clickhouse_price_caliber_is_raw() -> None:
    from backtest.loaders.registry import price_caliber

    assert price_caliber("clickhouse", "a_share", "600519.SH") == "raw"


# ---------------------------------------------------------------------------
# Federation must resolve a real network source, never itself
# ---------------------------------------------------------------------------


class _FakeNetworkLoader:
    """Stand-in network loader that records each ``fetch`` call."""

    name = "fake_network"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.fetch_calls: list[tuple[list[str], str, str, str]] = []

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        self.fetch_calls.append((list(codes), start_date, end_date, interval))
        return {code: self.frame.copy() for code in codes}


def _spy_on_resolve_loader(
    monkeypatch: pytest.MonkeyPatch, fake_loader: _FakeNetworkLoader
) -> list[tuple[str, set[str]]]:
    """Patch ``registry.resolve_loader`` and record (market, exclude) calls."""
    from backtest.loaders import registry as registry_module

    calls: list[tuple[str, set[str]]] = []

    def _resolve(
        market: str, *, exclude: frozenset[str] | set[str] = frozenset()
    ) -> _FakeNetworkLoader:
        calls.append((market, set(exclude)))
        return fake_loader

    monkeypatch.setattr(registry_module, "resolve_loader", _resolve)
    return calls


def test_network_fetch_excludes_clickhouse_from_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_network = _FakeNetworkLoader(_sample_bar_frame("stk_factor_pro"))
    calls = _spy_on_resolve_loader(monkeypatch, fake_network)
    loader, _ = _loader_with_frame(monkeypatch, pd.DataFrame())

    loader.fetch(["000001.SZ"], _six_months_ago(), _today(), interval="1D")

    assert calls == [("a_share", {"clickhouse"})]
    assert fake_network.fetch_calls == [(["000001.SZ"], _today(), _today(), "1D")]


def test_federation_does_not_recurse(monkeypatch: pytest.MonkeyPatch) -> None:
    today = _today()
    network_frame = pd.DataFrame(
        {
            "open": [10.0],
            "high": [10.2],
            "low": [9.9],
            "close": [10.1],
            "volume": [500.0],
        },
        index=pd.to_datetime([today]),
    )
    fake_network = _FakeNetworkLoader(network_frame)
    calls = _spy_on_resolve_loader(monkeypatch, fake_network)
    loader, _ = _loader_with_frame(monkeypatch, pd.DataFrame())

    result = loader.fetch(["000001.SZ"], today, today, interval="1D")

    assert list(result["000001.SZ"].index) == [pd.Timestamp(today)]
    assert "000001.SZ" in result
    assert len(calls) == 1, "federation must resolve the network source once"
    assert len(fake_network.fetch_calls) == 1
