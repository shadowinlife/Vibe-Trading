"""ClickHouse-backed flow data adapters that try ClickHouse first, falling back to tushare.

Each function mirrors the corresponding tushare_fallbacks function's signature and
return dict shape. When ClickHouse is unreachable or returns no data, the call is
transparently forwarded to the tushare fallback so the caller always receives a
compatible envelope.

Unit conversions are metadata-driven (mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md P1.4): the
factors come from ``src.clickhouse_units`` (``schema/clickhouse/comments.yaml``)
with transition assertions, so a COMMENT edit that breaks the verified contract
fails loudly instead of silently rescaling data.
"""

from __future__ import annotations

import logging
from typing import Any

from src import clickhouse_units
from src.clickhouse_connector import ClickHouseConnector
from src.tools import tushare_fallbacks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ts_code(code: str) -> str:
    """Re-export the tushare symbol normaliser for convenience."""
    return tushare_fallbacks._ts_code(code)


def _to_float(value: Any) -> float | None:
    """Coerce a raw cell to ``float``, returning ``None`` on missing/garbage."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dashed_date(value: Any) -> str | None:
    if value is None:
        return None
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value)[:10] if value else None


# ---------------------------------------------------------------------------
# Fund flow — stk_moneyflow
# ---------------------------------------------------------------------------


def fetch_fund_flow_ch(symbol: str, *, days: int) -> dict[str, Any]:
    """Query ClickHouse ``stk_moneyflow``, falling back to tushare on failure.

    Returns the same dict shape as ``tushare_fallbacks.fetch_fund_flow``:
    ``{symbol, ts_code, source, rows: [{timestamp, main, small, medium, large, super_large}]}``.

    Amount columns are stored in 万元 (10k CNY) and emitted in 元; the ×10⁴
    factor is read from the unit registry (comments.yaml) and asserted against
    the verified contract (``clickhouse_units.moneyflow_amount_to_yuan_factor``).
    """
    ts_code = _ts_code(symbol)
    try:
        ch = ClickHouseConnector()
        df = ch.get_moneyflow(ts_code, days=days)
    except Exception as exc:
        logger.debug("CH fund_flow unavailable for %s: %s", symbol, exc)
        return tushare_fallbacks.fetch_fund_flow(symbol, days=days)

    if df.empty:
        logger.debug(
            "CH fund_flow returned empty for %s, falling back to tushare", symbol
        )
        return tushare_fallbacks.fetch_fund_flow(symbol, days=days)

    wan_to_yuan = clickhouse_units.moneyflow_amount_to_yuan_factor()
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "timestamp": _dashed_date(row.get("trade_date")),
                "main": (_to_float(row.get("net_mf_amount")) or 0.0) * wan_to_yuan,
                "small": _net_amount(
                    row, "buy_sm_amount", "sell_sm_amount", wan_to_yuan
                ),
                "medium": _net_amount(
                    row, "buy_md_amount", "sell_md_amount", wan_to_yuan
                ),
                "large": _net_amount(
                    row, "buy_lg_amount", "sell_lg_amount", wan_to_yuan
                ),
                "super_large": _net_amount(
                    row, "buy_elg_amount", "sell_elg_amount", wan_to_yuan
                ),
            }
        )
    rows.sort(key=lambda item: item.get("timestamp") or "")
    rows = rows[-days:]
    return {"symbol": symbol, "ts_code": ts_code, "source": "clickhouse", "rows": rows}


def _net_amount(row: Any, buy_key: str, sell_key: str, factor: float) -> float | None:
    """Compute net amount from buy/sell columns, converting 10k CNY to CNY."""
    buy = _to_float(row.get(buy_key))
    sell = _to_float(row.get(sell_key))
    if buy is None and sell is None:
        return None
    return ((buy or 0.0) - (sell or 0.0)) * factor


# ---------------------------------------------------------------------------
# Margin trading — stk_margin
# ---------------------------------------------------------------------------


def fetch_margin_trading_ch(code: str, *, days: int) -> dict[str, Any]:
    """Query ClickHouse ``stk_margin``, falling back to tushare on failure.

    Returns the same dict shape as ``tushare_fallbacks.fetch_margin_trading``:
    ``{code, ts_code, rows: [{trade_date, financing_balance, financing_buy, ...}]}``.
    """
    ts_code = _ts_code(code)
    try:
        ch = ClickHouseConnector()
        df = ch.get_margin(ts_code, days=days)
    except Exception as exc:
        logger.debug("CH margin_trading unavailable for %s: %s", code, exc)
        return tushare_fallbacks.fetch_margin_trading(code, days=days)

    if df.empty:
        logger.debug(
            "CH margin_trading returned empty for %s, falling back to tushare", code
        )
        return tushare_fallbacks.fetch_margin_trading(code, days=days)

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "trade_date": _dashed_date(row.get("trade_date")),
                "financing_balance": _to_float(row.get("rzye")),
                "financing_buy": _to_float(row.get("rzmre")),
                "financing_repay": _to_float(row.get("rzche")),
                "short_balance": _to_float(row.get("rqye")),
                "short_volume": _to_float(row.get("rqyl")),
                "margin_total_balance": _to_float(row.get("rzrqye")),
            }
        )
    rows.sort(key=lambda item: item.get("trade_date") or "", reverse=True)
    return {"code": ts_code.split(".", 1)[0], "ts_code": ts_code, "rows": rows[:days]}


# ---------------------------------------------------------------------------
# Dragon-tiger board — stk_top_list
# ---------------------------------------------------------------------------


def fetch_dragon_tiger_ch(trade_date: str, code: str | None) -> dict[str, Any]:
    """Query ClickHouse ``stk_top_list``, falling back to tushare on failure.

    Returns the same dict shape as ``tushare_fallbacks.fetch_dragon_tiger``:
    ``{date, count, appearances, code?, seats?}``.
    When ``code`` is supplied and CH returns appearances, seat-level detail is
    fetched from tushare (CH does not store the ``top_inst`` breakdown).
    """
    compact = trade_date.strip().replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        compact = tushare_fallbacks._compact_date(trade_date)
    dashed = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"

    ts_code = _ts_code(code) if code else None

    try:
        ch = ClickHouseConnector()
        df = ch.get_top_list(dashed, ts_code=ts_code)
    except Exception as exc:
        logger.debug("CH dragon_tiger unavailable for %s/%s: %s", dashed, code, exc)
        return tushare_fallbacks.fetch_dragon_tiger(trade_date, code)

    if df.empty:
        logger.debug(
            "CH dragon_tiger returned empty for %s/%s, falling back to tushare",
            dashed,
            code,
        )
        return tushare_fallbacks.fetch_dragon_tiger(trade_date, code)

    appearances_raw = df.to_dict("records")
    appearances = [
        {
            "code": str(row.get("ts_code", "")).split(".", 1)[0] or None,
            "name": row.get("name"),
            "close": row.get("close"),
            "change_pct": row.get("pct_change"),
            "net_buy": row.get("net_amount"),
            "buy_amount": row.get("l_buy"),
            "sell_amount": row.get("l_sell"),
            "turnover": row.get("amount"),
            "reason": row.get("reason"),
        }
        for row in appearances_raw
    ]

    data: dict[str, Any] = {
        "date": dashed,
        "count": len(appearances_raw),
        "appearances": appearances,
    }

    if ts_code:
        data["code"] = ts_code.split(".", 1)[0]
        # Seat detail is not available from ClickHouse (no top_inst table),
        # so fetch it from tushare when needed.
        try:
            seats_raw = tushare_fallbacks._records(
                tushare_fallbacks._pro_api().top_inst(
                    trade_date=compact, ts_code=ts_code
                )
            )
            data["seats"] = [
                {
                    "seat": row.get("exalter"),
                    "side": row.get("side"),
                    "buy": row.get("buy"),
                    "sell": row.get("sell"),
                    "net": row.get("net_buy"),
                    "rank": None,
                }
                for row in seats_raw
            ]
        except Exception as exc:
            logger.debug(
                "CH dragon_tiger seat detail unavailable for %s: %s", ts_code, exc
            )

    return data


# ---------------------------------------------------------------------------
# Northbound flow — stk_moneyflow_hsgt
# ---------------------------------------------------------------------------


def fetch_northbound_flow_ch(*, lookback_days: int) -> dict[str, Any]:
    """Query ClickHouse ``stk_moneyflow_hsgt``, falling back to tushare on failure.

    Returns the same dict shape as ``tushare_fallbacks.fetch_northbound_flow``:
    ``{unit, lookback_days, realtime: {shanghai_connect, shenzhen_connect, total}, history: [{...}]}``.

    Unit correction (P1.4): ``hgt`` / ``sgt`` / ``north_money`` are stored in
    万元 (10k CNY) and the envelope unit is 万元, so values pass through with
    factor 1. The legacy ×100 was a confirmed 100x data bug — verified
    2026-08-12 against live data (CH 20260722 north_money=375048.34 equals the
    tushare live value, ≈37.5亿元, the correct northbound magnitude) — and has
    been removed. The factor stays registry-asserted so a COMMENT edit that
    changes the raw unit fails loudly.
    """
    try:
        ch = ClickHouseConnector()
        df = ch.get_moneyflow_hsgt(lookback_days=lookback_days)
    except Exception as exc:
        logger.debug("CH northbound_flow unavailable: %s", exc)
        return tushare_fallbacks.fetch_northbound_flow(lookback_days=lookback_days)

    if df.empty:
        logger.debug("CH northbound_flow returned empty, falling back to tushare")
        return tushare_fallbacks.fetch_northbound_flow(lookback_days=lookback_days)

    raw_to_wan = clickhouse_units.northbound_raw_to_wan_factor()
    history: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        shanghai = _to_float(row.get("hgt"))
        shenzhen = _to_float(row.get("sgt"))
        total = _to_float(row.get("north_money"))
        history.append(
            {
                "trade_date": _dashed_date(row.get("trade_date")),
                "shanghai_connect": (
                    shanghai * raw_to_wan if shanghai is not None else None
                ),
                "shenzhen_connect": (
                    shenzhen * raw_to_wan if shenzhen is not None else None
                ),
                "total": total * raw_to_wan if total is not None else None,
            }
        )
    history.sort(key=lambda item: item.get("trade_date") or "")
    history = history[-lookback_days:]
    latest = history[-1] if history else {}
    return {
        "unit": "10k CNY",
        "lookback_days": lookback_days,
        "realtime": {
            "shanghai_connect": latest.get("shanghai_connect"),
            "shenzhen_connect": latest.get("shenzhen_connect"),
            "total": latest.get("total"),
        },
        "history": history,
    }
