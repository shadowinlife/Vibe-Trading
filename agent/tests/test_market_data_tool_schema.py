"""FE-1 R3: the get_market_data envelope is self-describing, additive-only.

``MarketDataTool.execute`` annotates the fetch layer's JSON with a
top-level ``schema_version`` and a per-symbol ``_provenance.date_field``
naming the bar-date key that symbol actually uses — without renaming
anything. The fetch layer itself is mocked here: these tests pin the
tool-layer contract only, never the network.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import src.tools.market_data_tool as mod

_VALID_CALL = {
    "codes": ["600519.SH", "AAPL.US"],
    "start_date": "2026-01-01",
    "end_date": "2026-03-31",
}


def _bar(date_key: str, value: str = "2026-01-02") -> dict[str, Any]:
    return {
        date_key: value,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 100.0,
    }


def _provenance_entry(source: str = "tencent") -> dict[str, Any]:
    return {
        "source": source,
        "requested_source": source,
        "detected_source": source,
        "fallback_used": False,
        "currency_conversion": "none",
        "volume_unit": "lots",
        "adjustment": "split_dividend_additive",
    }


def _run_with_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the tool with the fetch layer answering ``payload`` verbatim."""
    envelope = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    with mock.patch.object(mod, "fetch_market_data_json", return_value=envelope):
        out = mod.MarketDataTool().execute(**_VALID_CALL)
    return json.loads(out)


def test_success_envelope_carries_schema_version():
    payload = {
        "600519.SH": [_bar("trade_date")],
        "_provenance": {"600519.SH": _provenance_entry()},
    }
    out = _run_with_payload(payload)
    assert out["schema_version"] == "1.0"
    assert mod.SCHEMA_VERSION == "1.0"


def test_date_field_matches_each_symbols_actual_bar_key():
    payload = {
        "600519.SH": [_bar("trade_date")],
        "AAPL.US": [_bar("date")],
        "_provenance": {
            "600519.SH": _provenance_entry(),
            "AAPL.US": _provenance_entry(source="yfinance"),
        },
    }
    out = _run_with_payload(payload)
    assert out["_provenance"]["600519.SH"]["date_field"] == "trade_date"
    assert out["_provenance"]["AAPL.US"]["date_field"] == "date"


def test_bar_keys_are_never_renamed():
    bar = _bar("trade_date")
    out = _run_with_payload(
        {"600519.SH": [bar], "_provenance": {"600519.SH": _provenance_entry()}}
    )
    assert out["600519.SH"][0] == bar
    assert set(out["600519.SH"][0]) == {
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }


def test_existing_provenance_fields_are_preserved():
    entry = _provenance_entry()
    out = _run_with_payload(
        {"600519.SH": [_bar("trade_date")], "_provenance": {"600519.SH": entry}}
    )
    annotated = out["_provenance"]["600519.SH"]
    for key, value in entry.items():
        assert annotated[key] == value
    assert annotated["volume_unit"] == "lots"
    assert annotated["adjustment"] == "split_dividend_additive"


def test_empty_or_missing_bars_yield_null_date_field_without_crash():
    payload = {
        "EMPTY.SH": [],
        "AAPL.US": [_bar("timestamp")],
        "_provenance": {
            "EMPTY.SH": _provenance_entry(),
            "GHOST.SZ": _provenance_entry(),
            "AAPL.US": _provenance_entry(source="yfinance"),
        },
        "_unresolved": ["GHOST.SZ"],
    }
    out = _run_with_payload(payload)
    assert out["_provenance"]["EMPTY.SH"]["date_field"] is None
    assert out["_provenance"]["GHOST.SZ"]["date_field"] is None
    assert out["_provenance"]["AAPL.US"]["date_field"] == "timestamp"
    assert out["schema_version"] == "1.0"
    assert out["_unresolved"] == ["GHOST.SZ"]


def test_meta_entries_never_gain_date_field():
    payload = {
        "AAPL.US": [_bar("time")],
        "_provenance": {"AAPL.US": _provenance_entry(source="yfinance")},
    }
    out = _run_with_payload(payload)
    assert "date_field" not in out["_provenance"]
    assert out["_provenance"]["AAPL.US"]["date_field"] == "time"


def test_no_data_error_envelope_passes_through_unchanged():
    error_envelope = json.dumps(
        {
            "status": "error",
            "error_code": "no_market_data",
            "error": "No data returned for any requested symbol: BAD.US",
            "_unresolved": ["BAD.US"],
        },
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    with mock.patch.object(mod, "fetch_market_data_json", return_value=error_envelope):
        out = mod.MarketDataTool().execute(**_VALID_CALL)
    assert out == error_envelope


def test_validation_error_envelope_shape_unchanged():
    out = json.loads(
        mod.MarketDataTool().execute(
            codes=[], start_date="2026-01-01", end_date="2026-03-31"
        )
    )
    assert out == {"ok": False, "error": "codes must be a non-empty list of strings"}
    assert "schema_version" not in out


def test_non_json_fetch_result_passes_through_unchanged():
    with mock.patch.object(mod, "fetch_market_data_json", return_value="not-json"):
        out = mod.MarketDataTool().execute(**_VALID_CALL)
    assert out == "not-json"
