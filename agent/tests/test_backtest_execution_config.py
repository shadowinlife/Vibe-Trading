"""Neutral frozen backtest inputs adapt in memory without native fee defaults."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from pydantic import ValidationError

from backtest.engines.china_a import ChinaAEngine
from backtest.runner import (
    _StageProgress,
    _execute_stages,
    _unrecognized_config_warning,
)
from src.core.runner import RunResult
from src.strategy_runs import backtest_config as bc
from src.strategy_runs.backtest_config import (
    NEUTRAL_CONFIG_FIELDS,
    adapt_backtest_config,
)
from src.tools.backtest_tool import run_backtest


def _neutral_config() -> dict[str, str | list[str]]:
    return {
        "strategy_id": "cn-cost",
        "strategy_version": "v1",
        "market": "CN_A",
        "symbols": ["000001.SZ"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-03",
        "bar_interval": "1d",
        "data_source": "local",
        "data_as_of": "2026-01-03",
        "snapshot_basis": "local-csv-v1",
        "initial_cash": "100000.00",
        "currency": "CNY",
        "commission_bps": "2.50",
        "tax_bps": "5.00",
        "slippage_bps": "10.00",
    }


def test_neutral_config_maps_exact_aliases_and_decimal_costs() -> None:
    # Given a complete neutral config with end_date exactly at the snapshot.
    config = _neutral_config()

    # When it is adapted for the native engine.
    native = adapt_backtest_config(config)

    # Then only native keys and private provenance/cost keys are introduced.
    assert native == {
        "codes": ["000001.SZ"],
        "source": "local",
        "interval": "1D",
        "initial_cash": 100000.0,
        "start_date": "2026-01-01",
        "end_date": "2026-01-03",
        "neutral_execution_costs": {
            "commission_bps": "2.50",
            "tax_bps": "5.00",
            "slippage_bps": "10.00",
        },
        "_neutral_market": "CN_A",
        "_neutral_currency": "CNY",
        "_neutral_strategy_id": "cn-cost",
        "_neutral_strategy_version": "v1",
        "_neutral_snapshot_basis": "local-csv-v1",
    }
    assert config["bar_interval"] == "1d"


def test_every_neutral_field_is_required_and_extras_are_forbidden() -> None:
    assert set(bc.NeutralBacktestConfig.model_fields) == set(NEUTRAL_CONFIG_FIELDS)
    assert all(
        field.is_required() for field in bc.NeutralBacktestConfig.model_fields.values()
    )
    for missing in NEUTRAL_CONFIG_FIELDS:
        config = _neutral_config()
        config.pop(missing)
        with pytest.raises(ValidationError):
            adapt_backtest_config(config)
    config = _neutral_config()
    config["position_adjustment"] = "hold"
    with pytest.raises(ValidationError):
        adapt_backtest_config(config)


def test_legacy_native_config_is_the_same_object_even_with_shared_fields() -> None:
    legacy = {
        "codes": ["BTC-USDT"],
        "source": "okx",
        "interval": "1D",
        "start_date": "2026-01-01",
        "end_date": "2026-01-03",
        "initial_cash": 10000,
        "commission": 0.001,
    }
    assert adapt_backtest_config(legacy) is legacy


def test_native_aliases_and_hidden_cost_defaults_fail_closed() -> None:
    aliases = {
        "codes": ["000001.SZ"],
        "source": "local",
        "interval": "1D",
        "commission_rate": 0.00025,
        "commission_min": 5,
        "stamp_tax": 0.0005,
        "transfer_fee": 0.00001,
        "slippage": 0.001,
        "commission": 0.001,
        "maker_rate": 0.0002,
        "taker_rate": 0.0005,
        "funding_rate": 0.0001,
        "execution_cost_multiplier": 2,
        "extra_slippage_bps": 1,
    }
    for key, value in aliases.items():
        config = _neutral_config()
        config[key] = value
        with pytest.raises(ValidationError):
            adapt_backtest_config(config)
    config = _neutral_config()
    config.pop("commission_bps")
    config["commission"] = 0.001
    with pytest.raises(ValidationError):
        adapt_backtest_config(config)


def test_symbols_are_non_empty_and_unique() -> None:
    for symbols in ([], ["000001.SZ", "000001.SZ"], ["000001.SZ", " "]):
        config = _neutral_config()
        config["symbols"] = symbols
        with pytest.raises(ValidationError):
            adapt_backtest_config(config)


def test_decimal_costs_are_finite_non_negative_and_cash_is_positive() -> None:
    for field in ("commission_bps", "tax_bps", "slippage_bps"):
        for value in ("-0.01", "NaN", "Infinity"):
            config = _neutral_config()
            config[field] = value
            with pytest.raises(ValidationError):
                adapt_backtest_config(config)
    for value in ("0", "-1", "NaN", "Infinity"):
        config = _neutral_config()
        config["initial_cash"] = value
        with pytest.raises(ValidationError):
            adapt_backtest_config(config)


def test_iso_dates_enforce_start_before_end_no_later_than_snapshot() -> None:
    for dates in (
        ("2026-01-03", "2026-01-03", "2026-01-03"),
        ("2026-01-04", "2026-01-03", "2026-01-04"),
        ("2026-01-01", "2026-01-04", "2026-01-03"),
    ):
        config = _neutral_config()
        config.update(zip(("start_date", "end_date", "data_as_of"), dates, strict=True))
        with pytest.raises(ValidationError):
            bc.validate_backtest_chronology(config)
    config = _neutral_config()
    config["start_date"] = "01/01/2026"
    with pytest.raises(ValidationError):
        adapt_backtest_config(config)


def test_duplicate_provenance_must_match_exactly() -> None:
    provenance = {
        "data_source": "local",
        "data_as_of": "2026-01-03",
        "snapshot_basis": "local-csv-v1",
    }
    parsed = bc.validate_duplicate_provenance(_neutral_config(), **provenance)
    assert parsed.data_as_of.isoformat() == provenance["data_as_of"]
    for field, value in (
        ("data_source", "LOCAL"),
        ("data_as_of", "2026-01-04"),
        ("snapshot_basis", "other"),
    ):
        changed = {**provenance, field: value}
        with pytest.raises(bc.BacktestConfigValidationError):
            bc.validate_duplicate_provenance(_neutral_config(), **changed)


def test_neutral_cn_fills_use_only_declared_costs_and_directional_slippage() -> None:
    # Given 100 shares, 2.5bps commission, 5bps close tax, and 20bps slippage.
    config = _neutral_config()
    config["slippage_bps"] = "20.00"
    engine = ChinaAEngine(adapt_backtest_config(config))
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    bars = pd.DataFrame(
        {name: [100.0] * 3 for name in ("open", "high", "low", "close", "pre_close")},
        index=dates,
    )

    # When a real hold-mode execution opens then closes after the T+1 boundary.
    engine._execute_bars(
        dates,
        {"000001.SZ": bars},
        bars[["close"]].rename(columns={"close": "000001.SZ"}),
        pd.DataFrame({"000001.SZ": [0.2, 0.2, 0.0]}, index=dates),
        ["000001.SZ"],
    )

    # Then there is no minimum, transfer fee, native default, or cost multiplier.
    assert len(engine.fill_records) == 2
    assert [fill.execution_price for fill in engine.fill_records] == pytest.approx(
        [100.2, 99.8]
    )
    assert [fill.fee for fill in engine.fill_records] == pytest.approx([2.505, 7.485])
    assert (
        max(fill.timestamp for fill in engine.fill_records).date().isoformat()
        == "2026-01-03"
    )


def _write_tool_run(run_dir: Path, config: dict) -> None:
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (run_dir / "code" / "signal_engine.py").write_text("", encoding="utf-8")


def test_tool_accepts_valid_neutral_config_without_rewriting_frozen_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = tmp_path / "run"
    _write_tool_run(run_dir, _neutral_config())
    frozen = (run_dir / "config.json").read_bytes()
    result = RunResult(success=False, exit_code=7, stdout="", stderr="", artifacts={})

    with (
        patch("src.tools.backtest_tool.emit_progress"),
        patch("src.tools.backtest_tool.Runner") as runner_cls,
    ):
        runner_cls.return_value.execute.return_value = result
        envelope = json.loads(run_backtest(str(run_dir)))

    assert envelope["exit_code"] == 7
    assert "source must be one of" not in envelope["stderr"]
    runner_cls.return_value.execute.assert_called_once()
    assert (run_dir / "config.json").read_bytes() == frozen


def test_tool_rejects_bad_chronology_before_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = tmp_path / "run"
    config = _neutral_config()
    config["data_as_of"] = "2026-01-02"
    _write_tool_run(run_dir, config)

    with patch("src.tools.backtest_tool.Runner") as runner_cls:
        envelope = json.loads(run_backtest(str(run_dir)))

    assert envelope["status"] == "error"
    assert envelope["error_kind"] == "invalid_config"
    runner_cls.assert_not_called()
    assert not (run_dir / "state.json").exists()


def test_runner_rejects_bad_chronology_before_data_fetch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "run"
    config = _neutral_config()
    config["data_as_of"] = "2026-01-02"
    _write_tool_run(run_dir, config)

    with patch("backtest.runner.fetch_data_map") as fetch, pytest.raises(SystemExit):
        _execute_stages(run_dir, _StageProgress(run_dir))

    assert "start_date < end_date <= data_as_of" in capsys.readouterr().out
    fetch.assert_not_called()


def test_neutral_cost_block_is_a_recognized_config_key() -> None:
    native = adapt_backtest_config(_neutral_config())
    assert _unrecognized_config_warning(native) is None
