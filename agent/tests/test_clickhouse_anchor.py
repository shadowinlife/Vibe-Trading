"""Real-data anchor for the ClickHouse semantic layer (plan §3 acceptance).

Asserts the verified 600519.SH 2026-07-27 anchor values through the loader's
explicit-column path, so environments WITH ClickHouse access regression-test
the contract end to end. Skipped when ClickHouse is unreachable (e.g. local
development machines outside the VPC).

Anchor values (verified 2026-08-12 on the production instance):
    close=1289.5 (yuan, raw), vol=31990.44 (lots), amount=4129228.56
    (thousand CNY), total_mv=161198022.32 (ten-thousand CNY).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.clickhouse_connector import ClickHouseConnector

_ANCHOR_CODE = "600519.SH"
_ANCHOR_DATE = pd.Timestamp("2026-07-27")


def _ch_is_reachable() -> bool:
    """Return True when a live ClickHouse instance responds to a health check."""
    try:
        return ClickHouseConnector().health_check()
    except Exception:  # noqa: BLE001 — reachability probe is best-effort
        return False


pytestmark = pytest.mark.skipif(
    not _ch_is_reachable(),
    reason="ClickHouse is unreachable — anchor runs only with CH access",
)


def test_anchor_600519_2026_07_27() -> None:
    """Loader bars for the anchor day carry the verified values and units."""
    from backtest.loaders.clickhouse import DataLoader

    loader = DataLoader()
    result = loader.fetch([_ANCHOR_CODE], "2026-07-20", "2026-07-31", interval="1D")
    assert _ANCHOR_CODE in result, "anchor symbol missing from loader result"

    df = result[_ANCHOR_CODE]
    assert _ANCHOR_DATE in df.index, f"anchor date missing; got {list(df.index)}"
    row = df.loc[_ANCHOR_DATE]

    # close: yuan (raw), vol renamed to volume: lots, amount: thousand CNY,
    # total_mv: ten-thousand CNY — see schema/clickhouse/comments.yaml.
    assert row["close"] == pytest.approx(1289.5, rel=1e-6)
    assert row["volume"] == pytest.approx(31990.44, rel=1e-6)
    assert row["amount"] == pytest.approx(4129228.56, rel=1e-6)
    assert row["total_mv"] == pytest.approx(161198022.32, rel=1e-6)

    # P1.1 contract: the explicit column list still serves the full 199-column
    # table (minus the trade_date index), not a silently-changed projection.
    assert len(df.columns) >= 190, f"expected ~199 columns, got {len(df.columns)}"

    # P1.2 contract: served frames carry additive provenance metadata.
    provenance = df.attrs.get("_provenance")
    assert isinstance(provenance, dict), "missing _provenance attrs on CH frame"
    assert provenance["volume_unit"] == "lot"
    assert provenance["amount_unit"] == "thousand CNY"
    assert provenance["price_adjust"] == "raw"
