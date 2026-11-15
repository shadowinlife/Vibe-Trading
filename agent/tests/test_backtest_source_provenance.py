"""Live per-symbol data provenance through fetch, runner, and run card."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest import runner


def _frame(adjustment: str = "split_dividend", **attrs: object) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.5, 11.0, 11.5],
            "low": [9.5, 10.0, 10.5],
            "close": [10.2, 10.7, 11.2],
            "volume": [100.0, 110.0, 120.0],
        },
        index=pd.bdate_range("2024-01-02", periods=3),
    )
    frame.attrs = {"adjustment": adjustment, **attrs}
    return frame


def _config(source: str = "auto") -> dict[str, object]:
    return {
        "source": source,
        "codes": ["AAPL.US", "MSFT.US"],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
    }


def test_auto_partial_fallback_records_rejections_per_final_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PrimaryLoader:
        name = "primary"

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {
                "AAPL.US": _frame(
                    quote_currency="USD",
                    currency_conversion="none",
                    private_token="must-not-serialize",
                    empty_attr="",
                    non_string_attr=123,
                )
            }

    class OfflineLoader:
        name = "offline"

        def is_available(self):
            return False

    class EmptyLoader:
        name = "empty"

        def is_available(self):
            return True

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {}

    class BackupLoader:
        name = "backup"

        def is_available(self):
            return True

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {code: _frame("raw", quote_currency="USD") for code in codes}

    def resolve(market, *, diagnostics=None, **kwargs):
        assert market == "us_equity"
        diagnostics.append({"source": "retired", "reason": "source_unavailable"})
        return PrimaryLoader()

    monkeypatch.setattr(runner, "resolve_loader", resolve)
    monkeypatch.setitem(
        runner.FALLBACK_CHAINS,
        "us_equity",
        ["primary", "offline", "empty", "backup"],
    )
    monkeypatch.setattr(
        runner,
        "LOADER_REGISTRY",
        {"offline": OfflineLoader, "empty": EmptyLoader, "backup": BackupLoader},
    )
    config = _config()
    original = json.loads(json.dumps(config))

    result = runner.fetch_data_map(config)

    assert config == original
    assert result.effective_sources == ["backup", "primary"]
    assert result.data_provenance == {
        "AAPL.US": {
            "source": "primary",
            "adjustment": "split_dividend",
            "fallback_attempts": [
                {"source": "retired", "reason": "source_unavailable"}
            ],
            "attrs": {
                "quote_currency": "USD",
                "currency_conversion": "none",
                "adjustment": "split_dividend",
            },
        },
        "MSFT.US": {
            "source": "backup",
            "adjustment": "raw",
            "fallback_attempts": [
                {"source": "retired", "reason": "source_unavailable"},
                {"source": "primary", "reason": "no_data"},
                {"source": "offline", "reason": "source_unavailable"},
                {"source": "empty", "reason": "no_data"},
            ],
            "attrs": {"quote_currency": "USD", "adjustment": "raw"},
        },
    }
    serialized = json.dumps(result.data_provenance)
    assert "must-not-serialize" not in serialized
    assert "private_token" not in serialized
    assert "non_string_attr" not in serialized


def test_explicit_resolution_and_partial_fallback_use_actual_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ActualLoader:
        name = "yahoo"

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {"AAPL.US": _frame()}

    class BackupLoader:
        name = "backup"

        def is_available(self):
            return True

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {code: _frame("raw") for code in codes}

    def get_loader(source, *, diagnostics=None):
        assert source == "requested"
        diagnostics.append({"source": source, "reason": "source_unavailable"})
        return ActualLoader

    monkeypatch.setattr(runner, "_get_loader", get_loader)
    monkeypatch.setitem(runner.FALLBACK_CHAINS, "us_equity", ["requested", "backup"])
    monkeypatch.setattr(runner, "LOADER_REGISTRY", {"backup": BackupLoader})

    result = runner.fetch_data_map(_config("requested"))

    assert result.effective_sources == ["yahoo", "backup"]
    assert result.data_provenance["AAPL.US"] == {
        "source": "yahoo",
        "adjustment": "split_dividend",
        "fallback_attempts": [{"source": "requested", "reason": "source_unavailable"}],
        "attrs": {"adjustment": "split_dividend"},
    }
    assert result.data_provenance["MSFT.US"]["fallback_attempts"] == [
        {"source": "requested", "reason": "source_unavailable"},
        {"source": "yahoo", "reason": "no_data"},
    ]


def test_diagnostics_are_isolated_between_fetch_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Loader:
        name = "yahoo"

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {code: _frame() for code in codes}

    def get_loader(source, *, diagnostics=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            diagnostics.append({"source": "retired", "reason": "source_unavailable"})
        return Loader

    monkeypatch.setattr(runner, "_get_loader", get_loader)
    config = {**_config("yahoo"), "codes": ["AAPL.US"]}

    first = runner.fetch_data_map(config)
    second = runner.fetch_data_map(config)

    assert first.data_provenance["AAPL.US"]["fallback_attempts"] == [
        {"source": "retired", "reason": "source_unavailable"}
    ]
    assert second.data_provenance["AAPL.US"]["fallback_attempts"] == []
    assert first.data_provenance["AAPL.US"]["fallback_attempts"] == [
        {"source": "retired", "reason": "source_unavailable"}
    ]


def test_runner_main_writes_final_sources_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "code" / "signal_engine.py").write_text(
        "class SignalEngine:\n    pass\n", encoding="utf-8"
    )
    (run_dir / "config.json").write_text(
        json.dumps({**_config(), "initial_cash": 100_000}), encoding="utf-8"
    )
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))

    class PrimaryLoader:
        name = "primary"

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {"AAPL.US": _frame()}

    class BackupLoader:
        name = "backup"

        def is_available(self):
            return True

        def fetch(self, codes, start_date, end_date, **kwargs):
            return {code: _frame() for code in codes}

    def resolve(market, *, diagnostics=None, **kwargs):
        diagnostics.append({"source": "retired", "reason": "source_unavailable"})
        return PrimaryLoader()

    class SignalEngine:
        def generate(self, data_map):
            return {
                code: pd.Series(0.0, index=frame.index)
                for code, frame in data_map.items()
            }

    monkeypatch.setattr(runner, "resolve_loader", resolve)
    monkeypatch.setitem(runner.FALLBACK_CHAINS, "us_equity", ["primary", "backup"])
    monkeypatch.setattr(runner, "LOADER_REGISTRY", {"backup": BackupLoader})
    monkeypatch.setattr(
        runner,
        "_load_module_from_file",
        lambda path, name: SimpleNamespace(SignalEngine=SignalEngine),
    )
    monkeypatch.setattr(runner, "_validate_signal_engine_class", lambda cls: None)

    runner.main(run_dir)

    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert card["schema_version"] == "1.0"
    assert card["data_sources"] == ["backup", "primary"]
    assert card["data_provenance"]["AAPL.US"]["source"] == "primary"
    assert card["data_provenance"]["MSFT.US"]["source"] == "backup"
    assert card["data_provenance"]["MSFT.US"]["fallback_attempts"] == [
        {"source": "retired", "reason": "source_unavailable"},
        {"source": "primary", "reason": "no_data"},
    ]


def test_runner_main_skips_dotenv_inside_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "allowed"))
    monkeypatch.setenv("VIBE_TRADING_BACKTEST_SANDBOX", "1")
    calls = []
    monkeypatch.setattr(
        "dotenv.load_dotenv", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    with pytest.raises(SystemExit):
        runner.main(tmp_path / "outside")

    assert calls == []


def test_runner_main_loads_dotenv_for_direct_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "allowed"))
    monkeypatch.delenv("VIBE_TRADING_BACKTEST_SANDBOX", raising=False)
    calls = []
    monkeypatch.setattr(
        "dotenv.load_dotenv", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    with pytest.raises(SystemExit):
        runner.main(tmp_path / "outside")

    assert len(calls) == 1
