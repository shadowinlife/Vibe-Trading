"""Actual and component source contracts for CH-first flow tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.tools.clickhouse_fallbacks import fetch_dragon_tiger_ch
from src.tools.dragon_tiger_tool import DragonTigerTool
from src.tools.fund_flow_tool import FundFlowTool


def _appearances() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "name": "贵州茅台",
                "close": 1700.0,
                "pct_change": 5.2,
                "net_amount": 1.0,
                "l_buy": 2.0,
                "l_sell": 1.0,
                "amount": 3.0,
                "reason": "日涨幅偏离值达7%",
            }
        ]
    )


def _seats() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "exalter": "机构专用",
                "side": "BUY",
                "buy": 2.0,
                "sell": 0.0,
                "net_buy": 2.0,
            }
        ]
    )


def test_fund_flow_aggregates_actual_per_symbol_sources() -> None:
    def fake_flow(symbol: str, *, days: int) -> dict[str, object]:
        source = "clickhouse" if symbol == "600519.SH" else "tushare"
        return {
            "symbol": symbol,
            "source": source,
            "rows": [{"timestamp": "2024-01-03", "main": 1.0}],
        }

    with (
        patch(
            "src.tools.clickhouse_fallbacks.fetch_fund_flow_ch", side_effect=fake_flow
        ),
        patch("src.tools.fund_flow_tool.get_json") as eastmoney,
    ):
        payload = json.loads(
            FundFlowTool().execute(codes=["600519.SH", "000001.SZ"], period="daily")
        )

    eastmoney.assert_not_called()
    assert payload["source"] == "mixed"
    assert payload["data"]["600519.SH"]["source"] == "clickhouse"
    assert payload["data"]["000001.SZ"]["source"] == "tushare"


def test_dragon_ch_appearances_and_tushare_seats_are_mixed() -> None:
    connector = SimpleNamespace(get_top_list=lambda *_, **__: _appearances())
    pro = SimpleNamespace(top_inst=lambda **_: _seats())
    with (
        patch(
            "src.tools.clickhouse_fallbacks.ClickHouseConnector",
            return_value=connector,
        ),
        patch(
            "src.tools.clickhouse_fallbacks.tushare_fallbacks._pro_api",
            return_value=pro,
        ),
    ):
        data = fetch_dragon_tiger_ch("2024-01-02", "600519.SH")

    assert data["source"] == "mixed"
    assert data["appearance_source"] == "clickhouse"
    assert data["seat_source"] == "tushare"

    with patch(
        "src.tools.clickhouse_fallbacks.fetch_dragon_tiger_ch", return_value=data
    ):
        payload = json.loads(
            DragonTigerTool().execute(date="2024-01-02", code="600519.SH")
        )
    assert payload["source"] == "mixed"
    assert payload["data"]["appearance_source"] == "clickhouse"
    assert payload["data"]["seat_source"] == "tushare"


def test_dragon_failed_seats_have_no_fabricated_source() -> None:
    connector = SimpleNamespace(get_top_list=lambda *_, **__: _appearances())
    with (
        patch(
            "src.tools.clickhouse_fallbacks.ClickHouseConnector",
            return_value=connector,
        ),
        patch(
            "src.tools.clickhouse_fallbacks.tushare_fallbacks._pro_api",
            side_effect=RuntimeError("seat provider unavailable"),
        ),
    ):
        data = fetch_dragon_tiger_ch("2024-01-02", "600519.SH")

    assert data["source"] == "clickhouse"
    assert data["appearance_source"] == "clickhouse"
    assert data["seat_source"] is None
    assert data["warnings"] == ["seat detail unavailable"]

    with patch(
        "src.tools.clickhouse_fallbacks.fetch_dragon_tiger_ch", return_value=data
    ):
        payload = json.loads(
            DragonTigerTool().execute(date="2024-01-02", code="600519.SH")
        )
    assert payload["warnings"] == ["seat detail unavailable"]


def test_dragon_legacy_tushare_payload_gets_component_provenance() -> None:
    legacy = {
        "date": "2024-01-02",
        "count": 0,
        "appearances": [],
    }
    with (
        patch(
            "src.tools.clickhouse_fallbacks.ClickHouseConnector",
            side_effect=OSError("CH unavailable"),
        ),
        patch(
            "src.tools.clickhouse_fallbacks.tushare_fallbacks.fetch_dragon_tiger",
            return_value=legacy,
        ),
    ):
        data = fetch_dragon_tiger_ch("2024-01-02", "600519.SH")

    assert data["source"] == "tushare"
    assert data["appearance_source"] == "tushare"
