"""Loader resolution and frozen-enrichment contracts at public entry points."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest import runner
from backtest.engines import base as base_engine
from backtest.engines.china_a import ChinaAEngine
from backtest.loaders.base import NoAvailableSourceError


_REJECTED_ENRICHMENT = [
    (
        {"fundamental_fields": {"income": ["total_revenue"]}},
        "fundamental_fields",
    ),
    (
        {
            "event_feeds": [
                {
                    "name": "news",
                    "route_template": "/news/{code}",
                    "event_type": "news",
                }
            ]
        },
        "event_feeds",
    ),
]

_EMPTY_ENRICHMENT = [
    (None, None),
    ({}, {}),
    ([], []),
    ({"income": []}, []),
    ({"income": None, "cashflow": []}, {}),
]


def _config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "codes": ["000001.SZ"],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
        "source": "local",
    }
    config.update(overrides)
    return config


def _frozen_config(enrichment: dict[str, object]) -> dict[str, object]:
    return _config(frozen_data_manifest="manifest.json", **enrichment)


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.5, 11.0, 11.5],
            "low": [9.5, 10.0, 10.5],
            "close": [10.2, 10.7, 11.2],
            "volume": [100.0, 110.0, 120.0],
        },
        index=pd.bdate_range("2024-01-02", periods=3),
    )


def _forbidden(calls: list[str], label: str):
    def access(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append(label)
        raise AssertionError(f"{label} must not be accessed")

    return access


def _block_providers_and_loaders(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    monkeypatch.setattr(
        runner,
        "get_loader_cls_with_fallback",
        _forbidden(calls, "loader resolution"),
    )
    monkeypatch.setattr(runner, "_get_loader", _forbidden(calls, "loader"))
    monkeypatch.setattr(
        base_engine,
        "TushareFundamentalProvider",
        _forbidden(calls, "Tushare provider"),
    )
    monkeypatch.setattr(
        base_engine,
        "RSSHubEventProvider",
        _forbidden(calls, "RSSHub provider"),
    )


def _block_snapshot(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    monkeypatch.setattr(
        "backtest.frozen.load_frozen_frames",
        _forbidden(calls, "frozen snapshot"),
    )


class _ForbiddenLoader:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def fetch(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.calls.append("loader.fetch")
        raise AssertionError("loader.fetch must not be accessed")


class _ForbiddenSignal:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def generate(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.calls.append("signal generation")
        raise AssertionError("signal generation must not be accessed")


def test_resolution_failure_never_constructs_ultimate_tushare_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructions: list[str] = []
    resolutions: list[tuple[str, list[dict[str, str]] | None]] = []

    class ForbiddenTushare:
        def __init__(self) -> None:
            constructions.append("Tushare")
            raise AssertionError("Tushare fallback must not be constructed")

    def fail_resolution(
        source: str, *, diagnostics: list[dict[str, str]] | None = None
    ) -> None:
        resolutions.append((source, diagnostics))
        raise NoAvailableSourceError("requested source is unavailable")

    monkeypatch.setitem(runner.LOADER_REGISTRY, "tushare", ForbiddenTushare)
    monkeypatch.setattr(runner, "get_loader_cls_with_fallback", fail_resolution)

    with pytest.raises(NoAvailableSourceError, match="requested source is unavailable"):
        runner.fetch_data_map(_config(source="unavailable"))

    assert constructions == []
    assert resolutions == [("unavailable", [])]


@pytest.mark.parametrize(("enrichment", "field"), _REJECTED_ENRICHMENT)
def test_schema_rejects_frozen_enrichment(
    enrichment: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=rf"{field}.*frozen_data_manifest"):
        runner.BacktestConfigSchema(**_frozen_config(enrichment))


@pytest.mark.parametrize(("enrichment", "field"), _REJECTED_ENRICHMENT)
def test_fetch_rejects_frozen_enrichment_before_any_data_access(
    monkeypatch: pytest.MonkeyPatch,
    enrichment: dict[str, object],
    field: str,
) -> None:
    calls: list[str] = []
    _block_snapshot(monkeypatch, calls)
    _block_providers_and_loaders(monkeypatch, calls)

    with pytest.raises(ValueError, match=rf"{field}.*frozen_data_manifest"):
        runner.fetch_data_map(_frozen_config(enrichment))

    assert calls == []


@pytest.mark.parametrize(("enrichment", "field"), _REJECTED_ENRICHMENT)
def test_direct_engine_rejects_frozen_enrichment_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    enrichment: dict[str, object],
    field: str,
) -> None:
    calls: list[str] = []
    _block_snapshot(monkeypatch, calls)
    _block_providers_and_loaders(monkeypatch, calls)
    config = _frozen_config(enrichment)
    engine = ChinaAEngine(config)

    with pytest.raises(ValueError, match=rf"{field}.*frozen_data_manifest"):
        engine.run_backtest(
            config,
            _ForbiddenLoader(calls),
            _ForbiddenSignal(calls),
            tmp_path,
        )

    assert calls == []


def test_fundamental_helper_retains_defensive_frozen_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _block_providers_and_loaders(monkeypatch, calls)
    enrichment, field = _REJECTED_ENRICHMENT[0]

    with pytest.raises(ValueError, match=rf"{field}.*frozen_data_manifest"):
        base_engine._maybe_enrich_fundamentals(
            {"000001.SZ": _bars()}, _frozen_config(enrichment)
        )

    assert calls == []


def test_event_helper_retains_defensive_frozen_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _block_providers_and_loaders(monkeypatch, calls)
    enrichment, field = _REJECTED_ENRICHMENT[1]

    with pytest.raises(ValueError, match=rf"{field}.*frozen_data_manifest"):
        base_engine._maybe_enrich_events(
            {"000001.SZ": _bars()}, _frozen_config(enrichment)
        )

    assert calls == []


@pytest.mark.parametrize(("fundamental_fields", "event_feeds"), _EMPTY_ENRICHMENT)
def test_fetch_accepts_effectively_empty_frozen_enrichment(
    monkeypatch: pytest.MonkeyPatch,
    fundamental_fields: object,
    event_feeds: object,
) -> None:
    snapshot_calls: list[str] = []
    unexpected_calls: list[str] = []
    _block_providers_and_loaders(monkeypatch, unexpected_calls)

    def load_snapshot(path: str, config: dict[str, object]):
        assert config["frozen_data_manifest"] == path
        snapshot_calls.append(path)
        return {"000001.SZ": _bars()}, {"sources": ["local"]}

    monkeypatch.setattr("backtest.frozen.load_frozen_frames", load_snapshot)

    result = runner.fetch_data_map(
        _frozen_config(
            {
                "fundamental_fields": fundamental_fields,
                "event_feeds": event_feeds,
            }
        )
    )

    assert list(result.data_map) == ["000001.SZ"]
    assert result.effective_sources == ["local"]
    assert snapshot_calls == ["manifest.json"]
    assert unexpected_calls == []


def test_non_frozen_fetch_keeps_live_enrichment_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_calls: list[tuple[list[str], str]] = []

    class LiveLoader:
        name = "local"

        def fetch(
            self,
            codes: list[str],
            start_date: str,
            end_date: str,
            *,
            fields: object = None,
            interval: str = "1D",
        ) -> dict[str, pd.DataFrame]:
            del start_date, end_date, fields
            fetch_calls.append((list(codes), interval))
            return {code: _bars() for code in codes}

    monkeypatch.setattr(runner, "_get_loader", lambda source, **kwargs: LiveLoader)
    enrichment, _ = _REJECTED_ENRICHMENT[0]
    event_enrichment, _ = _REJECTED_ENRICHMENT[1]

    result = runner.fetch_data_map(_config(**enrichment, **event_enrichment))

    assert list(result.data_map) == ["000001.SZ"]
    assert fetch_calls == [(["000001.SZ"], "1D")]
