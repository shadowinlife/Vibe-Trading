"""Tests for the get_valuation tool (semantic-layer P1.3) with mocked backends.

ClickHouse is mocked at the connector level and tushare at the ``_pro_api``
level, so no network access or token is required. Covers the fixed field
template, explicit unit labels, caliber annotations, the CH→tushare fallback
path, and input validation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.clickhouse_connector import ClickHouseConnector
from src.tools import tushare_fallbacks
from src.tools.valuation_tool import GetValuationTool

_FIELDS = [
    "trade_date",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "total_mv",
    "circ_mv",
    "turnover_rate",
]

_CH_ROW = {
    "trade_date": "2026-07-27",
    "pe_ttm": 20.13,
    "pb": 7.42,
    "ps_ttm": 9.88,
    "dv_ttm": 3.05,
    "total_mv": 161198022.32,
    "circ_mv": 161198022.32,
    "turnover_rate": 0.25,
}


def _ch_frame(row: dict | None = None) -> pd.DataFrame:
    if row is None:
        return pd.DataFrame()
    return pd.DataFrame([{key: row[key] for key in _FIELDS}])


def _run(**kwargs) -> dict:
    return json.loads(GetValuationTool().execute(**kwargs))


# ---------------------------------------------------------------------------
# ClickHouse primary path
# ---------------------------------------------------------------------------


def test_valuation_from_clickhouse_full_template_and_units() -> None:
    """CH path: all 7 template fields, explicit units, caliber annotations."""
    with patch.object(ClickHouseConnector, "query", return_value=_ch_frame(_CH_ROW)):
        result = _run(symbol="600519.SH", trade_date="2026-07-27")

    assert result["ok"] is True
    assert result["source"] == "clickhouse"
    assert result["ts_code"] == "600519.SH"
    assert result["symbol"] == "600519"
    assert result["trade_date"] == "2026-07-27"

    template = (
        "pe_ttm",
        "pb",
        "ps_ttm",
        "dv_ttm",
        "total_mv",
        "circ_mv",
        "turnover_rate",
    )
    for key in ("data", "units", "calibers"):
        assert set(result[key]) == set(template), f"{key} template incomplete"

    assert result["data"]["pe_ttm"] == pytest.approx(20.13)
    assert result["data"]["total_mv"] == pytest.approx(161198022.32)

    # Explicit unit contract: caps in 万元, ratios dimensionless, dv/turnover %.
    assert result["units"]["total_mv"] == "万元 (10k CNY)"
    assert result["units"]["circ_mv"] == "万元 (10k CNY)"
    assert result["units"]["pe_ttm"] == "dimensionless (ratio)"
    assert result["units"]["pb"] == "dimensionless (ratio)"
    assert result["units"]["ps_ttm"] == "dimensionless (ratio)"
    assert result["units"]["dv_ttm"] == "percent (%)"
    assert result["units"]["turnover_rate"] == "percent (%)"

    # COMMENT-layer caliber annotation is attached (comments.yaml text).
    assert result["calibers"]["pe_ttm"] and "TTM" in result["calibers"]["pe_ttm"]


def test_valuation_latest_row_when_date_omitted() -> None:
    """Omitting trade_date queries the most recent row (ORDER BY DESC LIMIT 1)."""
    captured: dict = {}

    def fake_query(sql: str, params=None):
        captured["sql"] = sql
        return _ch_frame(_CH_ROW)

    with patch.object(ClickHouseConnector, "query", side_effect=fake_query):
        result = _run(symbol="600519")

    assert result["ok"] is True
    assert result["trade_date"] == "2026-07-27"
    assert "LIMIT 1" in captured["sql"]
    assert "ORDER BY trade_date DESC" in captured["sql"]


# ---------------------------------------------------------------------------
# tushare daily_basic fallback
# ---------------------------------------------------------------------------


def test_valuation_falls_back_to_tushare_when_ch_unreachable() -> None:
    """CH raises → tushare daily_basic serves the same template."""
    pro = MagicMock()
    pro.daily_basic.return_value = pd.DataFrame(
        [{**_CH_ROW, "trade_date": "20260727", "pe_ttm": 20.5}]
    )
    with (
        patch.object(
            ClickHouseConnector, "query", side_effect=ConnectionError("CH down")
        ),
        patch.object(tushare_fallbacks, "_pro_api", return_value=pro),
    ):
        result = _run(symbol="600519.SH", trade_date="2026-07-27")

    assert result["ok"] is True
    assert result["source"] == "tushare"
    assert result["trade_date"] == "2026-07-27"
    assert result["data"]["pe_ttm"] == pytest.approx(20.5)
    assert result["units"]["total_mv"] == "万元 (10k CNY)"
    pro.daily_basic.assert_called_once()


def test_valuation_falls_back_when_ch_row_missing() -> None:
    """CH reachable but empty for the date → tushare fallback still serves."""
    pro = MagicMock()
    pro.daily_basic.return_value = [_CH_ROW | {"trade_date": "20260727"}]
    with (
        patch.object(ClickHouseConnector, "query", return_value=_ch_frame(None)),
        patch.object(tushare_fallbacks, "_pro_api", return_value=pro),
    ):
        result = _run(symbol="600519.SH", trade_date="2026-07-27")

    assert result["ok"] is True
    assert result["source"] == "tushare"


def test_valuation_error_when_both_sources_empty() -> None:
    """CH empty + tushare empty → clean error envelope, never a crash."""
    pro = MagicMock()
    pro.daily_basic.return_value = []
    with (
        patch.object(ClickHouseConnector, "query", return_value=_ch_frame(None)),
        patch.object(tushare_fallbacks, "_pro_api", return_value=pro),
    ):
        result = _run(symbol="600519.SH", trade_date="2026-07-27")

    assert result["ok"] is False
    assert "no valuation data" in result["error"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_valuation_rejects_invalid_symbol() -> None:
    result = _run(symbol="AAPL.US")
    assert result["ok"] is False
    assert "error" in result


def test_valuation_rejects_missing_symbol() -> None:
    result = _run()
    assert result["ok"] is False


def test_valuation_rejects_malformed_date() -> None:
    result = _run(symbol="600519.SH", trade_date="not-a-date")
    assert result["ok"] is False
    assert "invalid trade_date" in result["error"]
