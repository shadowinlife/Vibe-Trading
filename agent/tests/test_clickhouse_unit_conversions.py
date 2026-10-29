"""Conversion-behavior tests for the CH flow adapters (semantic-layer P1.4).

Pins the two verified unit contracts at the adapter level with a mocked
ClickHouse connector (no network):

* ``stk_moneyflow`` amount columns are stored in 万元 and emitted in 元 —
  factor ×10⁴ (correct, kept, now metadata-driven).
* ``stk_moneyflow_hsgt`` columns store tushare's native 百万元 (million CNY)
  and are emitted in million CNY — factor ×1, with the 2024-08-19 semantics
  boundary from upstream #1484 (post-cutover rows are turnover, served under
  ``turnover_*`` fields). The legacy ×100 was a confirmed 100x data bug and
  must never come back; the P1.4 "万元" label misread a turnover-era value as
  a net-buy magnitude and was corrected in the 2026-10-10 alignment.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from src.clickhouse_connector import ClickHouseConnector
from src.tools.clickhouse_fallbacks import (
    fetch_fund_flow_ch,
    fetch_northbound_flow_ch,
)

# ---------------------------------------------------------------------------
# Fund flow — 万元 raw → 元 output (×10⁴, metadata-driven)
# ---------------------------------------------------------------------------


def test_fund_flow_applies_metadata_driven_wan_to_yuan() -> None:
    """net_mf_amount and bucket nets are converted 万元 → 元 with ×10⁴."""
    df = pd.DataFrame(
        [
            {
                "trade_date": "2026-07-27",
                "net_mf_amount": 2.5,
                "buy_sm_amount": 3.0,
                "sell_sm_amount": 1.0,
                "buy_md_amount": 5.0,
                "sell_md_amount": 8.0,
                "buy_lg_amount": 20.0,
                "sell_lg_amount": 7.0,
                "buy_elg_amount": 30.0,
                "sell_elg_amount": 10.0,
            }
        ]
    )
    with patch.object(ClickHouseConnector, "get_moneyflow", return_value=df):
        result = fetch_fund_flow_ch("600519.SH", days=5)

    assert result["source"] == "clickhouse"
    row = result["rows"][0]
    assert row["main"] == 25_000.0  # 2.5 万元 × 10⁴
    assert row["small"] == 20_000.0  # (3 - 1) 万元 × 10⁴
    assert row["medium"] == -30_000.0  # (5 - 8) 万元 × 10⁴
    assert row["large"] == 130_000.0  # (20 - 7) 万元 × 10⁴
    assert row["super_large"] == 200_000.0  # (30 - 10) 万元 × 10⁴


# ---------------------------------------------------------------------------
# Northbound — 百万元 raw → million CNY output (×1; #1484 semantics boundary)
# ---------------------------------------------------------------------------


def test_northbound_million_cny_pass_through_with_semantics_boundary() -> None:
    """×1 pass-through in million CNY, split at the 2024-08-19 boundary.

    Anchor magnitude: 375048.34 million CNY ≈ 3750亿元 is a turnover-era
    value (the legacy ×100 would have produced 37504834.0; the P1.4 万元
    reading misinterpreted it as ≈37.5亿 net buy).
    """
    df = pd.DataFrame(
        [
            {
                "trade_date": "2024-08-16",
                "hgt": -2568.22,
                "sgt": 100.0,
                "north_money": -2468.22,
            },
            {
                "trade_date": "2026-07-22",
                "hgt": 200000.0,
                "sgt": 175048.34,
                "north_money": 375048.34,
            },
        ]
    )
    with patch.object(ClickHouseConnector, "get_moneyflow_hsgt", return_value=df):
        result = fetch_northbound_flow_ch(lookback_days=5)

    assert result["unit"] == "CNY million"
    assert "2024-08-19" in result["note"]
    pre, post = result["history"]
    assert pre["trade_date"] == "2024-08-16"
    assert pre["shanghai_connect"] == -2568.22
    assert pre["total"] == -2468.22
    assert pre["turnover_total"] is None
    assert post["trade_date"] == "2026-07-22"
    assert post["shanghai_connect"] is None
    assert post["total"] is None
    assert post["turnover_shanghai"] == 200000.0
    assert post["turnover_shenzhen"] == 175048.34
    assert post["turnover_total"] == 375048.34
    assert result["realtime"]["total"] is None
    assert result["realtime"]["turnover_total"] == 375048.34


def test_northbound_none_cells_stay_none() -> None:
    """Missing cells remain None through the (now identity) conversion."""
    df = pd.DataFrame(
        [{"trade_date": "2026-07-22", "hgt": None, "sgt": 1.5, "north_money": None}]
    )
    with patch.object(ClickHouseConnector, "get_moneyflow_hsgt", return_value=df):
        result = fetch_northbound_flow_ch(lookback_days=5)

    row = result["history"][0]
    assert row["turnover_shanghai"] is None
    assert row["turnover_shenzhen"] == 1.5
    assert row["turnover_total"] is None
