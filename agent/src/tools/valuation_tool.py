"""Explicit valuation tool for a single A-share symbol and date.

Returns a FIXED template of valuation fields — ``pe_ttm``, ``pb``, ``ps_ttm``,
``dv_ttm``, ``total_mv``, ``circ_mv``, ``turnover_rate`` — read from the
ClickHouse ``stk_factor_pro`` table through the existing HTTP connector.  Each
field is annotated with its COMMENT-layer caliber (pulled from
``schema/clickhouse/comments.yaml`` via the unit registry) and an explicit unit
label, so the LLM never has to guess what a number means.

When ClickHouse is unreachable or the requested row is missing, the tool falls
back to tushare ``daily_basic`` (same field template), keeping the research
workflow alive.  This is a read-only research tool: it places no orders and
reaches no live trading endpoint.

Part of the ClickHouse semantic-layer main-channel hardening
(mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md P1.3).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool
from src.tools import tushare_fallbacks

logger = logging.getLogger(__name__)

# Fixed valuation field template (P1.3). Order is stable and defines the
# envelope's ``data`` / ``units`` / ``calibers`` key order.
_VALUATION_FIELDS: tuple[str, ...] = (
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "total_mv",
    "circ_mv",
    "turnover_rate",
)

# Explicit output unit labels. These match the COMMENT-layer ``unit=`` metadata
# in schema/clickhouse/comments.yaml and make the envelope self-describing:
# market-cap fields are 万元 (10k CNY), price ratios are dimensionless, and the
# dividend yield / turnover are percentages.
_UNIT_LABELS: dict[str, str] = {
    "pe_ttm": "dimensionless (ratio)",
    "pb": "dimensionless (ratio)",
    "ps_ttm": "dimensionless (ratio)",
    "dv_ttm": "percent (%)",
    "total_mv": "万元 (10k CNY)",
    "circ_mv": "万元 (10k CNY)",
    "turnover_rate": "percent (%)",
}

# ClickHouse table backing the primary path.
_CH_TABLE = "stk_factor_pro"


def _to_float(value: Any) -> float | None:
    """Coerce a raw cell to ``float``, returning ``None`` on missing/garbage."""
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # ClickHouse represents missing Float64 values as nan/inf through some
    # formats; treat non-finite as absent so the envelope stays strict-JSON safe.
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _normalize_date(value: Any) -> str | None:
    """Normalize a YYYY-MM-DD or YYYYMMDD input to dashed form, or ``None``."""
    if value is None:
        return None
    digits = str(value).strip().replace("-", "")
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return None


def _field_calibers() -> dict[str, str | None]:
    """Pull the COMMENT-layer caliber text for each valuation field.

    Fail-soft: when the unit registry is unavailable every caliber is ``None``
    rather than raising — calibers are annotations, not the data itself.
    """
    try:
        from src.clickhouse_units import valuation_field_meta

        return {
            field: valuation_field_meta(_CH_TABLE, field).get("caliber")
            for field in _VALUATION_FIELDS
        }
    except Exception as exc:  # noqa: BLE001 — annotation lookup is best-effort
        logger.debug("valuation caliber metadata unavailable: %s", exc)
        return {field: None for field in _VALUATION_FIELDS}


def _build_envelope(
    ts_code: str,
    trade_date: str | None,
    values: dict[str, float | None],
    source: str,
) -> dict[str, Any]:
    """Assemble the success envelope from a resolved value mapping."""
    calibers = _field_calibers()
    return {
        "ok": True,
        "symbol": ts_code.split(".", 1)[0],
        "ts_code": ts_code,
        "trade_date": trade_date,
        "source": source,
        "data": {field: values.get(field) for field in _VALUATION_FIELDS},
        "units": {field: _UNIT_LABELS[field] for field in _VALUATION_FIELDS},
        "calibers": {field: calibers.get(field) for field in _VALUATION_FIELDS},
    }


def _fetch_from_clickhouse(
    ts_code: str, dashed_date: str | None
) -> dict[str, Any] | None:
    """Query ``stk_factor_pro`` for the valuation template.

    Returns the envelope on a resolved row, or ``None`` when ClickHouse is
    unreachable or the row is missing (caller falls back to tushare).
    """
    from src.clickhouse_connector import ClickHouseConnector

    columns = ", ".join(["trade_date", *_VALUATION_FIELDS])
    try:
        connector = ClickHouseConnector()
        if dashed_date is not None:
            sql = f"""
                SELECT {columns}
                FROM {_CH_TABLE}
                WHERE ts_code = {{ts_code:String}}
                  AND trade_date = {{trade_date:String}}
            """
            params = {"param_ts_code": ts_code, "param_trade_date": dashed_date}
        else:
            sql = f"""
                SELECT {columns}
                FROM {_CH_TABLE}
                WHERE ts_code = {{ts_code:String}}
                ORDER BY trade_date DESC
                LIMIT 1
            """
            params = {"param_ts_code": ts_code}
        df = connector.query(sql, params=params)
    except Exception as exc:
        logger.debug("CH valuation unavailable for %s: %s", ts_code, exc)
        return None

    if df is None or df.empty:
        logger.debug("CH valuation returned no row for %s", ts_code)
        return None

    row = df.iloc[0]
    values = {field: _to_float(row.get(field)) for field in _VALUATION_FIELDS}
    resolved_date = tushare_fallbacks._dashed_date(row.get("trade_date")) or dashed_date
    return _build_envelope(ts_code, resolved_date, values, source="clickhouse")


def _fetch_from_tushare(ts_code: str, dashed_date: str | None) -> dict[str, Any] | None:
    """Fallback: query tushare ``daily_basic`` for the same field template."""
    try:
        pro = tushare_fallbacks._pro_api()
        if dashed_date is not None:
            compact = dashed_date.replace("-", "")
            rows = tushare_fallbacks._records(
                pro.daily_basic(ts_code=ts_code, trade_date=compact)
            )
        else:
            start_date, end_date = tushare_fallbacks._date_window(15)
            rows = tushare_fallbacks._records(
                pro.daily_basic(
                    ts_code=ts_code, start_date=start_date, end_date=end_date
                )
            )
            # Keep only the most recent trading day.
            rows = sorted(rows, key=lambda r: str(r.get("trade_date") or ""))
            rows = rows[-1:]
    except Exception as exc:
        logger.debug("tushare daily_basic fallback failed for %s: %s", ts_code, exc)
        return None

    if not rows:
        return None

    row = rows[-1]
    values = {field: _to_float(row.get(field)) for field in _VALUATION_FIELDS}
    resolved_date = tushare_fallbacks._dashed_date(row.get("trade_date")) or dashed_date
    return _build_envelope(ts_code, resolved_date, values, source="tushare")


class GetValuationTool(BaseTool):
    """Fetch a fixed valuation template for one A-share symbol and date."""

    name = "get_valuation"
    description = (
        "Valuation snapshot for ONE mainland China A-share symbol on ONE trade "
        "date: pe_ttm, pb, ps_ttm, dv_ttm (percent), total_mv and circ_mv "
        "(万元 / 10k CNY), and turnover_rate (percent), each annotated with its "
        "data caliber. Reads ClickHouse stk_factor_pro first, falling back to "
        "tushare daily_basic. Omit trade_date for the most recent available "
        "day. Read-only. Example: get_valuation(symbol='600519.SH', "
        "trade_date='2026-07-27')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": (
                    "A-share symbol, e.g. '600519.SH', '000001.SZ', or a bare "
                    "6-digit code like '600519'."
                ),
            },
            "trade_date": {
                "type": "string",
                "description": (
                    "Trade date as YYYY-MM-DD or YYYYMMDD. Omit to use the most "
                    "recent available trading day."
                ),
            },
        },
        "required": ["symbol"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Fetch the valuation template and return a JSON envelope string."""
        symbol = str(kwargs.get("symbol") or "").strip()
        if not symbol:
            return json.dumps(
                {"ok": False, "error": "symbol is required"}, ensure_ascii=False
            )

        try:
            ts_code = tushare_fallbacks._ts_code(symbol)
        except Exception as exc:  # noqa: BLE001 — surface as error envelope
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

        dashed_date = _normalize_date(kwargs.get("trade_date"))
        raw_date = kwargs.get("trade_date")
        if raw_date not in (None, "") and dashed_date is None:
            return json.dumps(
                {"ok": False, "error": f"invalid trade_date: {raw_date!r}"},
                ensure_ascii=False,
            )

        envelope = _fetch_from_clickhouse(ts_code, dashed_date)
        if envelope is None:
            envelope = _fetch_from_tushare(ts_code, dashed_date)
        if envelope is None:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"no valuation data for {ts_code}"
                        + (f" on {dashed_date}" if dashed_date else "")
                        + " (ClickHouse and tushare both unavailable/empty)"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(envelope, ensure_ascii=False)
