"""A-share ClickHouse/realtime federation provenance contract tests."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pandas as pd
import pytest

from backtest.source_contract import (
    federation_adjustment,
    federation_from_attrs,
    federation_source,
)
from src.market_data import fetch_market_data

_SYMBOL = "600519.SH"


def _today() -> str:
    return dt.date.today().isoformat()


def _yesterday() -> str:
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _ch_frame(day: str | None = None) -> pd.DataFrame:
    stamp = pd.Timestamp(day or _yesterday())
    return pd.DataFrame(
        {
            "ts_code": [_SYMBOL],
            "trade_date": [stamp.date()],
            "open": [10.0],
            "high": [10.4],
            "low": [9.9],
            "close": [10.2],
            "vol": [100.0],
            "amount": [1020.0],
        }
    )


def _network_frame(
    day: str | None = None, *, adjustment: str | None = None
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [11.0],
            "high": [11.4],
            "low": [10.9],
            "close": [11.2],
            "volume": [200.0],
            "amount": [2240.0],
        },
        index=pd.DatetimeIndex([day or _today()], name="trade_date"),
    )
    if adjustment is not None:
        frame.attrs["adjustment"] = adjustment
    return frame


class _NetworkLoader:
    """Resolved network-loader double; no provider or network is touched."""

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        name: str = "sina",
        volume_unit: str = "lots",
        amount_unit: str = "thousand CNY",
    ) -> None:
        self.name = name
        self.frame = frame
        self.volume_units = {"a_share": volume_unit}
        self.amount_units = {"a_share": amount_unit}
        self.fetch_calls: list[tuple[list[str], str, str, str]] = []

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
    ) -> dict[str, pd.DataFrame]:
        self.fetch_calls.append((list(codes), start_date, end_date, interval))
        return {code: self.frame.copy() for code in codes}


def _loader(
    monkeypatch: pytest.MonkeyPatch,
    ch_frame: pd.DataFrame,
    network: _NetworkLoader | None,
) -> tuple[Any, list[tuple[str, str, str, str]], list[tuple[str, set[str]]]]:
    from backtest.loaders import clickhouse as ch
    from backtest.loaders import registry

    monkeypatch.setattr(
        ch.DataLoader, "_build_connector", staticmethod(lambda: object())
    )
    ch_calls: list[tuple[str, str, str, str]] = []

    def _fetch_ch(
        self: Any, code: str, table: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        ch_calls.append((code, table, start_date, end_date))
        return ch_frame.copy()

    monkeypatch.setattr(ch.DataLoader, "_fetch_ch_bars_cached", _fetch_ch)
    network_calls: list[tuple[str, set[str]]] = []

    def _resolve(
        market: str, *, exclude: set[str] | frozenset[str] = frozenset()
    ) -> Any:
        network_calls.append((market, set(exclude)))
        if network is None:
            raise AssertionError("a historical-only request resolved a network loader")
        return network

    monkeypatch.setattr(registry, "resolve_loader", _resolve)
    return ch.DataLoader(), ch_calls, network_calls


def _federation(frame: pd.DataFrame) -> dict[str, Any]:
    federation = federation_from_attrs(frame.attrs)
    assert federation is not None
    return federation


def _emit(frame: pd.DataFrame) -> dict[str, Any]:
    class ClickHouseProvider:
        name = "clickhouse"
        volume_units = {"a_share": "lot"}

        def fetch(
            self, codes: list[str], start_date: str, end_date: str, interval: str = "1D"
        ) -> dict[str, pd.DataFrame]:
            return {code: frame for code in codes}

    return fetch_market_data(
        codes=[_SYMBOL],
        start_date=_yesterday(),
        end_date=_today(),
        source="clickhouse",
        include_provenance=True,
        as_frames=True,
        loader_resolver=lambda source: ClickHouseProvider,
        fallback_chain_provider=lambda source: ["clickhouse"],
    )


def test_pure_history_is_clickhouse_only_without_network_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    end = _yesterday()
    loader, _, network_calls = _loader(monkeypatch, _ch_frame(end), None)

    result = loader.fetch([_SYMBOL], start, end)

    federation = _federation(result[_SYMBOL])
    assert federation == {
        "mode": "clickhouse_only",
        "history": {
            "source": "clickhouse",
            "table": "stk_factor_pro",
            "start_date": start,
            "end_date": end,
            "adjustment": "raw",
            "volume_unit": "lot",
            "amount_unit": "thousand CNY",
        },
    }
    assert federation_source(federation, None) == "clickhouse"
    assert network_calls == []


def test_two_leg_federation_uses_actual_network_source_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(_network_frame(adjustment="raw"), name="sina")
    loader, _, network_calls = _loader(monkeypatch, _ch_frame(), network)

    result = loader.fetch([_SYMBOL], _yesterday(), _today())

    federation = _federation(result[_SYMBOL])
    assert federation["mode"] == "federated"
    assert federation["history"] == {
        "source": "clickhouse",
        "table": "stk_factor_pro",
        "start_date": _yesterday(),
        "end_date": _yesterday(),
        "adjustment": "raw",
        "volume_unit": "lot",
        "amount_unit": "thousand CNY",
    }
    assert federation["realtime"] == {
        "source": "sina",
        "start_date": _today(),
        "end_date": _today(),
        "adjustment": "raw",
        "volume_unit": "lots",
        "amount_unit": "thousand CNY",
    }
    assert "caliber_warning" not in federation  # lot and lots are equivalent.
    assert federation_source(federation, None) == "federated"
    assert network_calls == [("a_share", {"clickhouse"})]
    assert network.fetch_calls == [([_SYMBOL], _today(), _today(), "1D")]


def test_missing_realtime_degrades_to_actual_ch_history_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(pd.DataFrame())
    loader, _, _ = _loader(monkeypatch, _ch_frame(), network)

    result = loader.fetch([_SYMBOL], _yesterday(), _today())

    federation = _federation(result[_SYMBOL])
    assert federation["mode"] == "clickhouse_only"
    assert set(federation) == {"mode", "history"}
    assert federation["history"]["start_date"] == _yesterday()
    assert federation["history"]["end_date"] == _yesterday()


def test_realtime_only_after_empty_ch_is_not_relabelled_clickhouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(_network_frame(adjustment="raw"), name="sina")
    loader, _, _ = _loader(monkeypatch, pd.DataFrame(), network)

    result = loader.fetch([_SYMBOL], _yesterday(), _today())

    federation = _federation(result[_SYMBOL])
    assert federation["mode"] == "realtime_only"
    assert set(federation) == {"mode", "realtime"}
    assert federation["realtime"]["source"] == "sina"
    assert federation_source(federation, "clickhouse") == "sina"
    assert "table" not in result[_SYMBOL].attrs["_provenance"]


def test_today_only_skips_ch_and_marks_realtime_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(_network_frame(adjustment="raw"), name="sina")
    loader, ch_calls, _ = _loader(monkeypatch, _ch_frame(), network)

    result = loader.fetch([_SYMBOL], _today(), _today())

    federation = _federation(result[_SYMBOL])
    assert federation["mode"] == "realtime_only"
    assert federation["realtime"]["start_date"] == _today()
    assert federation["realtime"]["end_date"] == _today()
    assert ch_calls == []


def test_mixed_caliber_and_units_are_explicit_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(
        _network_frame(),
        name="tencent",
        volume_unit="shares",
        amount_unit="CNY",
    )
    loader, _, _ = _loader(monkeypatch, _ch_frame(), network)

    result = loader.fetch([_SYMBOL], _yesterday(), _today())

    federation = _federation(result[_SYMBOL])
    assert federation_adjustment(federation, None) == "mixed"
    assert federation["history"]["adjustment"] == "raw"
    assert federation["realtime"]["adjustment"] == "split_dividend_additive"
    assert federation["history"]["volume_unit"] == "lot"
    assert federation["realtime"]["volume_unit"] == "shares"
    assert federation["caliber_warning"] == (
        "history adjustment=raw; realtime adjustment=split_dividend_additive; "
        "history volume_unit=lot; realtime volume_unit=shares; "
        "history amount_unit=thousand CNY; realtime amount_unit=CNY"
    )


def test_canonical_emission_is_json_safe_and_federation_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(
        _network_frame(), name="tencent", volume_unit="shares", amount_unit="CNY"
    )
    loader, _, _ = _loader(monkeypatch, _ch_frame(), network)
    frame = loader.fetch([_SYMBOL], _yesterday(), _today())[_SYMBOL]
    federation = _federation(frame)

    out = _emit(frame)
    provenance = out["_provenance"][_SYMBOL]

    assert out[_SYMBOL] is frame
    assert provenance["source"] == "federated"
    assert provenance["adjustment"] == "mixed"
    assert provenance["volume_unit"] == "mixed"
    assert provenance["amount_unit"] == "mixed"
    assert provenance["federation"] == federation
    assert provenance["caliber_warning"] == federation["caliber_warning"]
    assert provenance["fallback_used"] is False  # Planned federation is not fallback.
    assert "fallback_reason" not in provenance
    json.dumps(out["_provenance"], allow_nan=False)


def test_canonical_realtime_only_has_deterministic_ch_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = _NetworkLoader(
        _network_frame(adjustment="raw"),
        name="sina",
        volume_unit="shares",
        amount_unit="CNY",
    )
    loader, _, _ = _loader(monkeypatch, pd.DataFrame(), network)
    frame = loader.fetch([_SYMBOL], _yesterday(), _today())[_SYMBOL]

    provenance = _emit(frame)["_provenance"][_SYMBOL]

    assert provenance["source"] == "sina"
    assert provenance["detected_source"] == "clickhouse"
    assert provenance["fallback_used"] is True
    assert provenance["fallback_reason"] == "clickhouse:no_data"
    assert provenance["adjustment"] == "raw"
    assert provenance["volume_unit"] == "shares"
    assert provenance["amount_unit"] == "CNY"
    assert "table" not in provenance


def test_ch_query_failure_is_fetch_failed_not_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backtest.loaders import clickhouse as ch
    from backtest.loaders import registry

    monkeypatch.setattr(
        ch.DataLoader, "_build_connector", staticmethod(lambda: object())
    )
    monkeypatch.setattr(
        ch,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )

    def fail_query(*args: object, **kwargs: object) -> None:
        raise RuntimeError("query failed")

    monkeypatch.setattr(ch.DataLoader, "_fetch_ch_bars_raw", fail_query)
    network = _NetworkLoader(_network_frame(adjustment="raw"), name="sina")
    monkeypatch.setattr(registry, "resolve_loader", lambda *args, **kwargs: network)

    loader = ch.DataLoader()
    frame = loader.fetch([_SYMBOL], _yesterday(), _today())[_SYMBOL]

    assert frame.attrs["_provenance"]["fallback_reason"] == "clickhouse:fetch_failed"
    provenance = _emit(frame)["_provenance"][_SYMBOL]
    assert provenance["source"] == "sina"
    assert provenance["fallback_used"] is True
    assert provenance["fallback_reason"] == "clickhouse:fetch_failed"
