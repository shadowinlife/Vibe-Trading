"""Canonical source routing and provenance for external A-share benchmarks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import backtest.runner as runner
from backtest.benchmark import resolve_benchmark
from backtest.engines.china_a import ChinaAEngine
from backtest.loaders.registry import FALLBACK_CHAINS

_DATES = pd.bdate_range("2026-01-05", periods=3)
_STRATEGY = "600519.SH"
_BENCHMARK = "000300.SH"


@pytest.fixture(autouse=True)
def _disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


def _bars(closes: list[float], *, opens: list[float] | None = None) -> pd.DataFrame:
    opens = opens if opens is not None else closes
    return pd.DataFrame(
        {
            "open": opens,
            "high": closes,
            "low": opens,
            "close": closes,
            "volume": [1_000.0] * len(closes),
        },
        index=_DATES,
    )


def _payload(
    frame: pd.DataFrame,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        _BENCHMARK: frame,
        "_provenance": {_BENCHMARK: provenance},
    }


def _patch_canonical(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_fetch_market_data(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr("src.market_data.fetch_market_data", fake_fetch_market_data)
    return calls


def _forbid_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("YfinanceLoader must not be constructed")

    monkeypatch.setattr("backtest.benchmark.YfinanceLoader", forbidden)


def _forbid_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(**kwargs: Any) -> None:
        raise AssertionError(
            "local/frozen benchmarks must not use canonical market data"
        )

    monkeypatch.setattr("src.market_data.fetch_market_data", forbidden)


def test_auto_a_share_benchmark_uses_canonical_ch_without_yfinance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a canonical CH hit and a forbidden direct Yahoo constructor.
    federation = {
        "mode": "clickhouse_only",
        "history": {"source": "clickhouse", "adjustment": "raw"},
    }
    calls = _patch_canonical(
        monkeypatch,
        _payload(
            _bars([100.0, 110.0, 121.0]),
            {"source": "clickhouse", "fallback_used": False, "federation": federation},
        ),
    )
    _forbid_yfinance(monkeypatch)
    evidence: dict[str, Any] = {}

    # When the default A-share benchmark is resolved through source=auto.
    result = resolve_benchmark(
        [_STRATEGY],
        "auto",
        "2026-01-05",
        "2026-01-07",
        evidence=evidence,
    )

    # Then canonical selection owns the full market chain and reports what served.
    assert result is not None
    assert result.ticker == _BENCHMARK
    assert result.total_ret == pytest.approx(0.21)
    assert result.source == "clickhouse"
    assert result.fallback_reason is None
    assert result.federation == federation
    assert calls == [
        {
            "codes": [_BENCHMARK],
            "start_date": "2026-01-05",
            "end_date": "2026-01-07",
            "source": "auto",
            "interval": "1D",
            "max_fallback_attempts": calls[0]["max_fallback_attempts"],
            "include_provenance": True,
            "as_frames": True,
        }
    ]
    assert calls[0]["max_fallback_attempts"] >= len(FALLBACK_CHAINS["a_share"])
    assert evidence == {
        "benchmark_source": "clickhouse",
        "benchmark_federation": federation,
    }


@pytest.mark.parametrize("source", ["auto", "clickhouse"])
def test_canonical_benchmark_records_actual_tencent_fallback(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    # Given CH has no bars and the canonical chain serves realtime Tencent bars.
    federation = {
        "mode": "realtime_only",
        "realtime": {"source": "tencent", "adjustment": "raw"},
    }
    provenance = {
        "source": "tencent",
        "fallback_used": True,
        "fallback_reason": "clickhouse:no_data",
        "federation": federation,
    }
    calls = _patch_canonical(
        monkeypatch, _payload(_bars([100.0, 105.0, 110.0]), provenance)
    )
    _forbid_yfinance(monkeypatch)

    # When auto and explicit CH both resolve the A-share benchmark.
    result = resolve_benchmark([_STRATEGY], source, "2026-01-05", "2026-01-07")

    # Then neither mode bypasses to Yahoo and both retain actual provenance.
    assert result is not None
    assert result.total_ret == pytest.approx(0.1)
    assert result.source == "tencent"
    assert result.fallback_reason == "clickhouse:no_data"
    assert result.federation == federation
    assert len(calls) == 1
    assert calls[0]["source"] == source
    assert calls[0]["max_fallback_attempts"] >= len(FALLBACK_CHAINS["a_share"])


def test_unresolved_canonical_benchmark_preserves_stable_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every canonical attempt failed and diagnostics contain no exception text.
    payload = {
        "_unresolved": [_BENCHMARK],
        "_diagnostics": {
            _BENCHMARK: {
                "attempts": [
                    {"source": "clickhouse", "reason": "no_data"},
                    {
                        "source": "tencent",
                        "reason": "fetch_failed",
                        "raw_exception": "must not escape",
                    },
                ]
            }
        },
    }
    _patch_canonical(monkeypatch, payload)
    _forbid_yfinance(monkeypatch)
    evidence: dict[str, Any] = {}

    # When the benchmark remains unresolved and the caller degrades its reference.
    result = resolve_benchmark(
        [_STRATEGY], "auto", "2026-01-05", "2026-01-07", evidence=evidence
    )

    # Then equal-weight fallback can still expose stable source:reason evidence.
    assert result is None
    assert evidence == {
        "benchmark_fallback_reason": "clickhouse:no_data; tencent:fetch_failed"
    }


def test_local_benchmark_keeps_loader_path_without_canonical_or_yfinance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a local loader that can serve the declared benchmark.
    class LocalLoader:
        name = "local"

        def fetch(
            self, codes: list[str], *args: Any, **kwargs: Any
        ) -> dict[str, pd.DataFrame]:
            return {codes[0]: _bars([100.0, 110.0, 121.0])}

    _forbid_canonical(monkeypatch)
    _forbid_yfinance(monkeypatch)

    # When a local benchmark is resolved.
    result = resolve_benchmark(
        [_STRATEGY],
        "local",
        "2026-01-05",
        "2026-01-07",
        explicit=_BENCHMARK,
        loader=LocalLoader(),
    )

    # Then the legacy offline path and return math remain unchanged.
    assert result is not None
    assert result.total_ret == pytest.approx(0.21)
    assert result.source is None


class _FlatSignal:
    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        return {
            code: pd.Series(0.0, index=frame.index) for code, frame in data_map.items()
        }


class _StrategyLoader:
    name = "strategy_loader"

    def fetch(
        self, codes: list[str], *args: Any, **kwargs: Any
    ) -> dict[str, pd.DataFrame]:
        return {code: _bars([10.0, 10.0, 10.0]) for code in codes}


def _run_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _patch_canonical(monkeypatch, payload)
    _forbid_yfinance(monkeypatch)
    strategy_provenance = {
        _STRATEGY: {"source": "strategy_source", "adjustment": "raw", "attrs": {}}
    }
    config = {
        "codes": [_STRATEGY],
        "start_date": "2026-01-05",
        "end_date": "2026-01-07",
        "source": "auto",
        "benchmark": "auto",
        "initial_cash": 100_000.0,
        "_run_card_effective_sources": ["strategy_source"],
        "_run_card_data_provenance": strategy_provenance,
    }

    metrics = ChinaAEngine({"initial_cash": 100_000.0}).run_backtest(
        config, _StrategyLoader(), _FlatSignal(), tmp_path
    )
    card = json.loads((tmp_path / "run_card.json").read_text(encoding="utf-8"))
    return metrics, card


def test_engine_exposes_external_benchmark_evidence_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a Tencent benchmark fallback and independent strategy provenance.
    federation = {
        "mode": "realtime_only",
        "realtime": {"source": "tencent", "adjustment": "raw"},
    }
    payload = _payload(
        _bars([100.0, 105.0, 110.0], opens=[99.0, 104.0, 109.0]),
        {
            "source": "tencent",
            "fallback_used": True,
            "fallback_reason": "clickhouse:no_data",
            "federation": federation,
        },
    )

    # When the complete engine resolves and grades that external benchmark.
    metrics, card = _run_engine(tmp_path, monkeypatch, payload)

    # Then benchmark evidence is optional metrics, never strategy source evidence.
    assert metrics["benchmark_kind"] == "external"
    assert metrics["benchmark_source"] == "tencent"
    assert metrics["benchmark_fallback_reason"] == "clickhouse:no_data"
    assert metrics["benchmark_federation"] == federation
    assert card["data_sources"] == ["strategy_source"]
    assert card["data_provenance"] == {
        _STRATEGY: {"source": "strategy_source", "adjustment": "raw", "attrs": {}}
    }


def test_engine_equal_weight_fallback_retains_benchmark_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given unresolved canonical attempts and no external benchmark result.
    payload = {
        "_unresolved": [_BENCHMARK],
        "_diagnostics": {
            _BENCHMARK: {
                "attempts": [
                    {"source": "clickhouse", "reason": "no_data"},
                    {"source": "tencent", "reason": "fetch_failed"},
                ]
            }
        },
    }

    # When the engine honestly degrades to its equal-weight basket.
    metrics, card = _run_engine(tmp_path, monkeypatch, payload)

    # Then the benchmark warning and its source evidence both survive.
    assert metrics["benchmark_kind"] == "equal_weight"
    assert metrics["benchmark_fallback_reason"] == (
        "clickhouse:no_data; tencent:fetch_failed"
    )
    assert "benchmark_source" not in metrics
    assert card["data_sources"] == ["strategy_source"]
    assert any("could not be resolved" in warning for warning in card["warnings"])


def test_frozen_benchmark_never_touches_canonical_or_yfinance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a sealed snapshot containing both strategy and benchmark bars.
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "code" / "signal_engine.py").write_text(
        "class SignalEngine:\n    pass\n", encoding="utf-8"
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest_frames: dict[str, dict[str, str]] = {}
    for symbol, frame in {
        _STRATEGY: _bars([10.0, 10.0, 10.0]),
        _BENCHMARK: _bars([100.0, 110.0, 121.0]),
    }.items():
        raw = frame.rename_axis("date").to_csv().encode("utf-8")
        (snapshot / f"{symbol}.csv").write_bytes(raw)
        manifest_frames[symbol] = {
            "path": f"{symbol}.csv",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    manifest = snapshot / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "interval": "1D",
                "sources": ["local"],
                "frames": manifest_frames,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "codes": [_STRATEGY, _BENCHMARK],
                "start_date": "2026-01-05",
                "end_date": "2026-01-07",
                "source": "local",
                "benchmark": _BENCHMARK,
                "initial_cash": 100_000.0,
                "frozen_data_manifest": str(manifest),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.setattr(
        runner,
        "_load_module_from_file",
        lambda path, name: SimpleNamespace(SignalEngine=_FlatSignal),
    )
    monkeypatch.setattr(runner, "_validate_signal_engine_class", lambda cls: None)

    def forbidden_registry(name: str, **kwargs: Any) -> None:
        raise AssertionError("frozen run consulted the loader registry")

    monkeypatch.setattr(runner, "_get_loader", forbidden_registry)
    _forbid_canonical(monkeypatch)
    _forbid_yfinance(monkeypatch)

    # When the frozen run resolves its benchmark solely from the snapshot.
    runner.main(run_dir)
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))

    # Then existing sealed behavior and benchmark calculations are unchanged.
    assert card["metrics"]["benchmark_kind"] == "external"
    assert card["metrics"]["benchmark_return"] == pytest.approx(0.21, abs=1e-6)
    assert "benchmark_source" not in card["metrics"]
