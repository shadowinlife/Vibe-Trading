"""Federation persistence from run card through sealed replay."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest import runner
from backtest.run_card import write_run_card
from src.research_lab.execution import freeze_run

_CODE = "000001.SZ"


def _federation() -> dict[str, object]:
    return {
        "mode": "federated",
        "history": {
            "source": "clickhouse",
            "table": "stk_factor_pro",
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
            "adjustment": "raw",
            "volume_unit": "lot",
            "amount_unit": "thousand CNY",
        },
        "realtime": {
            "source": "tencent",
            "start_date": "2024-01-04",
            "end_date": "2024-01-04",
            "adjustment": "split_dividend_additive",
            "volume_unit": "lots",
            "amount_unit": None,
        },
        "password": "must-not-survive",
    }


def _source_run(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    run = tmp_path / "source-run"
    (run / "code").mkdir(parents=True)
    (run / "artifacts").mkdir()
    config = {
        "codes": [_CODE],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
        "source": "auto",
        "engine": "daily",
        "initial_cash": 100_000,
        "model_training_cutoff": "auto",
    }
    (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
    strategy = run / "code" / "signal_engine.py"
    strategy.write_text("class SignalEngine:\n    pass\n", encoding="utf-8")
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
    frame.rename_axis("date").to_csv(run / "artifacts" / f"ohlcv_{_CODE}.csv")
    federation = _federation()
    provenance = {
        _CODE: {
            "source": "federated",
            "adjustment": "mixed",
            "fallback_attempts": [],
            "attrs": {"adjustment": "mixed"},
            "federation": federation,
        }
    }
    write_run_card(
        run,
        config,
        {"total_return": 0.0},
        data_sources=["federated"],
        data_provenance=provenance,
        strategy_path=strategy,
    )
    return run, config


def test_run_card_preserves_federation_without_unapproved_fields(
    tmp_path: Path,
) -> None:
    run, _ = _source_run(tmp_path)

    card = json.loads((run / "run_card.json").read_text(encoding="utf-8"))

    federation = card["data_provenance"][_CODE]["federation"]
    assert federation["mode"] == "federated"
    assert federation["history"]["table"] == "stk_factor_pro"
    assert federation["realtime"]["source"] == "tencent"
    assert "password" not in federation


def test_federation_round_trip_through_freeze_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run, _ = _source_run(tmp_path)

    _, manifest_path, _, _ = freeze_run(run, tmp_path / "study")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["data_provenance"][_CODE]["federation"]
    assert expected["mode"] == "federated"
    assert manifest["caliber_warning"] == (
        "history adjustment=raw; realtime adjustment=split_dividend_additive"
    )

    config = {
        "source": "local",
        "codes": [_CODE],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
        "frozen_data_manifest": str(manifest_path),
    }
    result = runner.fetch_data_map(config)

    entry = result.data_provenance[_CODE]
    assert entry["source"] == "federated"
    assert entry["adjustment"] == "mixed"
    assert entry["federation"] == expected
    assert result.effective_sources == ["federated"]
    assert result.caliber_warning == manifest["caliber_warning"]


def test_replay_rejects_malformed_federation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run, _ = _source_run(tmp_path)
    _, manifest_path, _, _ = freeze_run(run, tmp_path / "study")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_provenance"][_CODE]["federation"]["mode"] = "invalid"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = {
        "source": "local",
        "codes": [_CODE],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
        "frozen_data_manifest": str(manifest_path),
    }

    with pytest.raises(ValueError, match="federation"):
        runner.fetch_data_map(config)
