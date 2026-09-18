"""Integration tests for ClickHouse-backed flow data adapters.

Each CH flow function (fetch_fund_flow_ch, fetch_margin_trading_ch,
fetch_dragon_tiger_ch, fetch_northbound_flow_ch) is tested against a
live ClickHouse instance when available.  When CH is unreachable the
tests are skipped via ``pytest.mark.skipif``.

The unavailability test (test 5) mocks CH as unreachable and verifies each
function raises ``ClickHouseUnavailableError`` without touching tushare —
the calling tool falls through to its own upstream provider chain, keeping
envelope provenance labels truthful.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.clickhouse_connector import ClickHouseConnector
from src.tools.clickhouse_fallbacks import (
    ClickHouseUnavailableError,
    fetch_dragon_tiger_ch,
    fetch_fund_flow_ch,
    fetch_margin_trading_ch,
    fetch_northbound_flow_ch,
)

# ---------------------------------------------------------------------------
# Lazy, cached CH-reachability flag (evaluated once at module load).
# The first ``skipif`` decorator calls ``_ch_is_reachable()`` and the
# result is cached so subsequent decorators are instant.
# ---------------------------------------------------------------------------

_CH_REACHABLE: bool | None = None


def _ch_is_reachable() -> bool:
    """Return True if a live ClickHouse instance responds to a health check.

    The result is cached at module level so the collection-time probe
    runs at most once.
    """
    global _CH_REACHABLE
    if _CH_REACHABLE is not None:
        return _CH_REACHABLE
    try:
        _CH_REACHABLE = ClickHouseConnector().health_check()
    except Exception:
        _CH_REACHABLE = False
    return _CH_REACHABLE


def _mock_ch_connector_unreachable():
    """Return a context manager that makes ClickHouseConnector() raise."""
    return patch.object(
        ClickHouseConnector,
        "__init__",
        side_effect=OSError("CH unreachable"),
    )


# ---------------------------------------------------------------------------
# Test 1 — Fund flow
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ch_is_reachable(), reason="ClickHouse is unreachable")
class TestFetchFundFlowCh:
    """Verify the ClickHouse fund-flow adapter against a live instance."""

    def test_fetch_fund_flow_ch(self):
        """Returns the expected envelope shape with clickhouse source."""
        result = fetch_fund_flow_ch("600519.SH", days=30)
        assert isinstance(result, dict)
        assert result["symbol"] == "600519.SH"
        assert result["ts_code"] == "600519.SH"
        assert result["source"] == "clickhouse"
        assert isinstance(result["rows"], list)
        for row in result["rows"]:
            assert isinstance(row, dict)
            assert "timestamp" in row
            assert "main" in row
            assert "small" in row
            assert "medium" in row
            assert "large" in row
            assert "super_large" in row
            for key in ("main", "small", "medium", "large", "super_large"):
                assert isinstance(row[key], (float, int, type(None)))


# ---------------------------------------------------------------------------
# Test 2 — Margin trading
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ch_is_reachable(), reason="ClickHouse is unreachable")
class TestFetchMarginTradingCh:
    """Verify the ClickHouse margin-trading adapter against a live instance."""

    def test_fetch_margin_trading_ch(self):
        """Returns the expected envelope shape."""
        result = fetch_margin_trading_ch("000001.SZ", days=30)
        assert isinstance(result, dict)
        assert result["code"] == "000001"
        assert result["ts_code"] == "000001.SZ"
        assert isinstance(result["rows"], list)
        for row in result["rows"]:
            assert isinstance(row, dict)
            assert "trade_date" in row
            assert "financing_balance" in row
            assert "financing_buy" in row
            assert "financing_repay" in row
            assert "short_balance" in row
            assert "short_volume" in row
            assert "margin_total_balance" in row


# ---------------------------------------------------------------------------
# Test 3 — Dragon-tiger board
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ch_is_reachable(), reason="ClickHouse is unreachable")
class TestFetchDragonTigerCh:
    """Verify the ClickHouse dragon-tiger adapter against a live instance."""

    def test_fetch_dragon_tiger_ch(self):
        """Returns the expected envelope shape for a given date + code."""
        result = fetch_dragon_tiger_ch("2024-01-02", "600519.SH")
        assert isinstance(result, dict)
        assert "date" in result
        assert "count" in result
        assert isinstance(result["count"], int)
        assert "appearances" in result
        assert isinstance(result["appearances"], list)
        if result["appearances"]:
            first = result["appearances"][0]
            assert isinstance(first, dict)
            assert "code" in first
            assert "name" in first


# ---------------------------------------------------------------------------
# Test 4 — Northbound flow
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ch_is_reachable(), reason="ClickHouse is unreachable")
class TestFetchNorthboundFlowCh:
    """Verify the ClickHouse northbound-flow adapter against a live instance."""

    def test_fetch_northbound_flow_ch(self):
        """Returns the expected envelope shape."""
        result = fetch_northbound_flow_ch(lookback_days=30)
        assert isinstance(result, dict)
        assert result["unit"] == "10k CNY"
        assert result["lookback_days"] == 30
        assert "realtime" in result
        assert isinstance(result["realtime"], dict)
        assert "shanghai_connect" in result["realtime"]
        assert "shenzhen_connect" in result["realtime"]
        assert "total" in result["realtime"]
        assert "history" in result
        assert isinstance(result["history"], list)
        for row in result["history"]:
            assert isinstance(row, dict)
            assert "trade_date" in row
            assert "shanghai_connect" in row
            assert "shenzhen_connect" in row
            assert "total" in row


# ---------------------------------------------------------------------------
# Test 5 — Raise when CH is unavailable (tool falls through to its own chain)
# ---------------------------------------------------------------------------


class TestRaisesWhenUnavailable:
    """CH unreachable → raise; the internal tushare forward is gone.

    Forwarding internally made the tool hooks label tushare-served data as
    ``source="clickhouse"`` — the upstream fallback tests (5fe512dc) reject
    that, rightly. Raising lets each tool run its own eastmoney → tushare
    chain and label the envelope truthfully.
    """

    def test_fetch_fund_flow_raises(self):
        with (
            _mock_ch_connector_unreachable(),
            patch(
                "src.tools.clickhouse_fallbacks.tushare_fallbacks.fetch_fund_flow",
            ) as mock_fallback,
        ):
            with pytest.raises(ClickHouseUnavailableError):
                fetch_fund_flow_ch("600519.SH", days=30)
        mock_fallback.assert_not_called()

    def test_fetch_margin_trading_raises(self):
        with (
            _mock_ch_connector_unreachable(),
            patch(
                "src.tools.clickhouse_fallbacks.tushare_fallbacks.fetch_margin_trading",
            ) as mock_fallback,
        ):
            with pytest.raises(ClickHouseUnavailableError):
                fetch_margin_trading_ch("000001.SZ", days=30)
        mock_fallback.assert_not_called()

    def test_fetch_dragon_tiger_raises(self):
        with (
            _mock_ch_connector_unreachable(),
            patch(
                "src.tools.clickhouse_fallbacks.tushare_fallbacks.fetch_dragon_tiger",
            ) as mock_fallback,
        ):
            with pytest.raises(ClickHouseUnavailableError):
                fetch_dragon_tiger_ch("2024-01-02", "600519.SH")
        mock_fallback.assert_not_called()

    def test_fetch_northbound_flow_raises(self):
        with (
            _mock_ch_connector_unreachable(),
            patch(
                "src.tools.clickhouse_fallbacks.tushare_fallbacks.fetch_northbound_flow",
            ) as mock_fallback,
        ):
            with pytest.raises(ClickHouseUnavailableError):
                fetch_northbound_flow_ch(lookback_days=30)
        mock_fallback.assert_not_called()
