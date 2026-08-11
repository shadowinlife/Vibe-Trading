"""Integration tests for the ClickHouse-backed fundamental data provider."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from backtest.loaders.tushare_fundamentals import (
    ClickHouseFundamentalProvider,
    TableSchema,
    TushareFundamentalProvider,
    UnknownTableError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_clickhouse_reachable() -> bool:
    """Return True if a ClickHouse server is reachable at the configured address."""
    try:
        connector = ClickHouseFundamentalProvider._build_connector()
        return connector.health_check()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Test 1: list_tables
# ---------------------------------------------------------------------------


def test_list_tables() -> None:
    """``list_tables()`` returns the four supported fundamental tables in stable order."""
    provider = ClickHouseFundamentalProvider()
    assert provider.list_tables() == [
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "income",
    ]


# ---------------------------------------------------------------------------
# Test 2: describe_table
# ---------------------------------------------------------------------------


def test_describe_table() -> None:
    """``describe_table("fina_indicator")`` returns a ``TableSchema`` with correct columns."""
    provider = ClickHouseFundamentalProvider()
    schema = provider.describe_table("fina_indicator")

    assert isinstance(schema, TableSchema)
    assert schema.name == "fina_indicator"
    assert schema.api_name == "fina_indicator"
    assert schema.point_in_time_column == "ann_date"

    column_names = {column.name for column in schema.columns}
    assert {"ts_code", "ann_date", "end_date"} <= column_names
    assert "eps" in column_names


def test_describe_table_rejects_unknown_table() -> None:
    """``describe_table`` raises ``UnknownTableError`` for unsupported tables."""
    provider = ClickHouseFundamentalProvider()
    with pytest.raises(UnknownTableError):
        provider.describe_table("nonexistent_table")


# ---------------------------------------------------------------------------
# Test 3: query_fundamentals PIT-safe
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _is_clickhouse_reachable(), reason="ClickHouse is not reachable"
)
def test_query_fundamentals_pit_safe() -> None:
    """Query ``fina_indicator`` for 000001.SZ — all rows must have ``ann_date`` ≤ ``as_of``."""
    provider = ClickHouseFundamentalProvider()
    as_of = "2024-12-31"

    result = provider.query_fundamentals(
        "fina_indicator",
        ["000001.SZ"],
        as_of=as_of,
    )

    assert not result.empty, "Expected non-empty DataFrame for 000001.SZ"

    as_of_date = pd.to_datetime(as_of)
    ann_dates = pd.to_datetime(result["ann_date"], format="%Y%m%d")
    assert (ann_dates <= as_of_date).all(), (
        f"All ann_date values must be ≤ {as_of}; "
        f"violations: {ann_dates[ann_dates > as_of_date].tolist()}"
    )


# ---------------------------------------------------------------------------
# Test 4: fallback when CH is unreachable
# ---------------------------------------------------------------------------


def test_query_fundamentals_fallback() -> None:
    """When ClickHouse is unreachable the provider falls back to
    ``TushareFundamentalProvider``."""
    provider = ClickHouseFundamentalProvider()

    # Simulate ClickHouse being unreachable
    provider._ch_connector.query = MagicMock(
        side_effect=ConnectionError("CH unavailable"),
    )

    # Pre-seed the fallback with a mock that returns a known DataFrame
    mock_fallback = MagicMock(spec=TushareFundamentalProvider)
    mock_fallback._query_pit_cut = MagicMock(
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20231231"],
                "ann_date": ["20240401"],
                "eps": [2.5],
            }
        ),
    )
    provider._fallback = mock_fallback

    result = provider.query_fundamentals(
        "fina_indicator",
        ["000001.SZ"],
        as_of="2024-12-31",
        fields=["eps"],
    )

    assert not result.empty
    assert list(result["ts_code"]) == ["000001.SZ"]
    assert list(result["eps"]) == [2.5]

    # Verify the fallback was actually invoked
    mock_fallback._query_pit_cut.assert_called_once()
