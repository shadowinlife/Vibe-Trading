"""Integration tests for the ClickHouse OHLCV loader.

Tests that depend on a reachable ClickHouse instance are gated behind a
module-level ``skip_if_no_ch`` marker.  Set the ``CH_HOST`` / ``CH_PORT`` /
``CH_USER`` / ``CH_PASSWORD`` / ``CH_DATABASE`` environment variables (or
use the defaults from ``src.config.env_schema.DataConfig``) to point at a
running ClickHouse server.
"""

from __future__ import annotations

import datetime as dt
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
