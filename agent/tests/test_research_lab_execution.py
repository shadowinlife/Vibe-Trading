"""Exercise saved inputs -> sandboxed engine -> cost and frozen OOS reports."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.research_lab.models import CostRequest, WalkForwardRequest
from src.research_lab.studies import cost_sensitivity, walk_forward


@pytest.fixture
def saved_run(tmp_path, monkeypatch, request):
    import dotenv
    from backtest import runner
    import backtest.loaders.local_loader as local

    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    dates = pd.bdate_range("2026-01-01", periods=35)
    symbol = getattr(request, "param", "AAPL.US")
    prices = (
        np.linspace(10, 10.02, 35)
        if symbol == "000001.SZ"
        else np.r_[np.linspace(10, 12, 15), np.linspace(11.8, 9, 20)]
    )
    frame = pd.DataFrame(
        {"date": dates, "open": prices, "high": prices * 1.01, "low": prices * 0.99, "close": prices, "volume": 1e8}
    )
    source = tmp_path / "prices.csv"
    frame.to_csv(source, index=False)
    bridge = tmp_path / "bridge.json"
    bridge.write_text(json.dumps({"sources": [{"symbol": symbol, "type": "csv", "path": str(source)}]}))
    monkeypatch.setattr(local, "_CONFIG_PATH", bridge)
    root = tmp_path / "baseline"
    (root / "code").mkdir(parents=True)
    (root / "code/signal_engine.py").write_text(
        "import pandas as pd\nfrom pathlib import Path\nclass SignalEngine:\n"
        "    def __init__(self, direction=1):\n        self.direction=direction\n"
        "    def generate(self, data_map):\n"
        "        snapshot = Path(__file__).resolve().parent.parent / 'snapshot/frame-0.csv'\n"
        "        if snapshot.is_file():\n"
        "            stored = pd.read_csv(snapshot, index_col=0, parse_dates=True)\n"
        "            if stored.index.max() > max(df.index.max() for df in data_map.values()):\n"
        "                raise ValueError('trial snapshot leaked future prices')\n"
        "        return {code:pd.Series(self.direction,index=df.index) for code,df in data_map.items()}\n"
    )
    (root / "config.json").write_text(
        json.dumps(
            {
                "codes": ["local:" + symbol],
                "source": "local",
                "start_date": str(dates[0].date()),
                "end_date": str(dates[-1].date()),
                "initial_cash": 10000,
                "slippage": 0,
                "slippage_us": 0,
            }
        )
    )
    runner.main(root)
    return root


def _write_source_provenance(run_dir: Path, entry: dict | None) -> None:
    card_path = run_dir / "run_card.json"
    card = json.loads(card_path.read_text())
    if entry is None:
        card.pop("data_provenance", None)
    else:
        card["data_provenance"] = {"AAPL.US": entry}
        card["data_sources"] = [entry["source"]]
    card_path.write_text(json.dumps(card))


def test_freeze_preserves_actual_provenance_attrs_and_additive_warning(
    saved_run, tmp_path
):
    from backtest.loaders.registry import additive_caliber_warning
    from src.research_lab.execution import freeze_run

    _write_source_provenance(
        saved_run,
        {
            "source": "tencent",
            "adjustment": "split_dividend_additive",
            "fallback_attempts": [{"source": "eastmoney", "reason": "no_data"}],
            "attrs": {
                "quote_currency": "CNY",
                "currency_conversion": "none",
                "api_key": "secret-token",
                "arbitrary": "must-not-survive",
            },
            "password": "secret-password",
        },
    )

    config, manifest_path, _, _ = freeze_run(saved_run, tmp_path / "study")

    assert config["codes"] == ["local:AAPL.US"]
    manifest = json.loads(manifest_path.read_text())
    expected = {
        "source": "tencent",
        "adjustment": "split_dividend_additive",
        "fallback_attempts": [{"source": "eastmoney", "reason": "no_data"}],
        "attrs": {"quote_currency": "CNY", "currency_conversion": "none"},
    }
    assert manifest["schema_version"] == 1
    assert manifest["data_provenance"] == {"AAPL.US": expected}
    assert manifest["frames"]["AAPL.US"]["attrs"] == {
        "quote_currency": "CNY",
        "currency_conversion": "none",
        "adjustment": "split_dividend_additive",
    }
    assert manifest["caliber_warning"] == additive_caliber_warning(
        {"AAPL.US": ("tencent", "split_dividend_additive")}
    )
    serialized = json.dumps(manifest)
    assert "secret-token" not in serialized
    assert "secret-password" not in serialized
    assert "must-not-survive" not in serialized


def test_freeze_separates_caliber_warnings_like_live_run(
    saved_run, tmp_path, monkeypatch
):
    from src.research_lab import execution

    _write_source_provenance(
        saved_run,
        {
            "source": "tencent",
            "adjustment": "split_dividend_additive",
            "fallback_attempts": [],
            "attrs": {},
        },
    )
    monkeypatch.setattr(execution, "mixed_caliber_warning", lambda stamps: "mixed")
    monkeypatch.setattr(execution, "additive_caliber_warning", lambda stamps: "additive")

    _, manifest_path, _, _ = execution.freeze_run(saved_run, tmp_path / "study")

    manifest = json.loads(manifest_path.read_text())
    assert manifest["caliber_warning"] == "mixed\n\nadditive"


def test_freeze_rejects_source_set_mismatch(saved_run, tmp_path):
    from src.research_lab.execution import freeze_run

    _write_source_provenance(
        saved_run,
        {
            "source": "original-vendor",
            "adjustment": "raw",
            "fallback_attempts": [],
            "attrs": {"adjustment": "raw"},
        },
    )
    card_path = saved_run / "run_card.json"
    card = json.loads(card_path.read_text())
    card["data_sources"] = ["local"]
    card_path.write_text(json.dumps(card))

    with pytest.raises(ValueError, match="sources") as caught:
        freeze_run(saved_run, tmp_path / "study")

    assert "rerun its backtest" in str(caught.value)


def test_freeze_rejects_provenance_adjustment_mismatch(saved_run, tmp_path):
    from src.research_lab.execution import freeze_run

    _write_source_provenance(
        saved_run,
        {
            "source": "tencent",
            "adjustment": "raw",
            "fallback_attempts": [],
            "attrs": {"adjustment": "split_dividend"},
        },
    )

    with pytest.raises(ValueError, match="adjustment") as caught:
        freeze_run(saved_run, tmp_path / "study")

    assert "AAPL.US" in str(caught.value)
    assert "rerun its backtest" in str(caught.value)


@pytest.mark.parametrize(
    "mutation", ["absent", "missing_symbol", "partial", "malformed"]
)
def test_freeze_requires_complete_source_card_provenance_before_snapshot(
    saved_run, tmp_path, mutation
):
    from src.research_lab.execution import freeze_run

    card_path = saved_run / "run_card.json"
    card = json.loads(card_path.read_text())
    if mutation == "absent":
        card.pop("data_provenance")
    elif mutation == "missing_symbol":
        card["data_provenance"] = {}
    elif mutation == "partial":
        del card["data_provenance"]["AAPL.US"]["fallback_attempts"]
    else:
        card["data_provenance"]["AAPL.US"]["source"] = None
    card_path.write_text(json.dumps(card))
    destination = tmp_path / "study"

    with pytest.raises(ValueError) as caught:
        freeze_run(saved_run, destination)

    assert "AAPL.US" in str(caught.value)
    assert "rerun its backtest" in str(caught.value)
    assert not (destination / "snapshot").exists()


def test_trial_run_card_preserves_original_actual_source(saved_run, tmp_path):
    from src.research_lab.execution import freeze_run, trial

    original = {
        "source": "original-vendor",
        "adjustment": "raw",
        "fallback_attempts": [{"source": "requested-vendor", "reason": "no_data"}],
        "attrs": {"quote_currency": "USD", "adjustment": "raw"},
    }
    _write_source_provenance(saved_run, original)
    config, manifest, _, _ = freeze_run(saved_run, tmp_path / "study")

    measured = trial(tmp_path / "study", "roundtrip", config, manifest)

    card = json.loads((Path(measured["run_dir"]) / "run_card.json").read_text())
    assert card["data_sources"] == ["original-vendor"]
    assert card["data_provenance"] == {"AAPL.US": original}
    assert card["data_provenance"]["AAPL.US"]["source"] != "local"


def test_cost_study_reexecutes_same_frozen_data(saved_run, tmp_path):
    result = cost_sensitivity(
        CostRequest(
            run_dir=str(saved_run),
            scenarios=[
                {"name": "base"},
                {"name": "adverse", "extra_slippage_bps": 100},
            ],
        ),
        tmp_path / "cost-study",
        lambda value: None,
    )
    assert len(result["rows"]) == 2
    assert result["rows"][1]["total_return"] < result["rows"][0]["total_return"]
    assert result["rows"][1]["final_value"] < result["rows"][0]["final_value"]
    assert result["input_manifest_sha256"]


def test_training_selection_precedes_frozen_oos_and_holdout(saved_run, tmp_path):
    request = WalkForwardRequest(
        run_dir=str(saved_run),
        train_bars=15,
        test_bars=5,
        holdout_bars=5,
        max_folds=1,
        objective="total_return",
        candidates=[
            {"name": "long", "parameters": {"direction": 1}},
            {"name": "short", "parameters": {"direction": -1}},
        ],
    )
    result = walk_forward(request, tmp_path / "wf-study", lambda value: None)
    assert result["folds"][0]["name"] == "long"
    assert result["folds"][0]["test"]["metrics"]["total_return"] < 0
    assert result["oos_bars"] == 5
    assert result["holdout"]["test_start"] > result["folds"][0]["test_end"]
    assert result["mode"] == "locked"
    assert (tmp_path / "wf-study/sealed_plan.json").is_file()
    assert (tmp_path / "wf-study/selection-1.json").is_file()


def test_modified_source_prices_are_not_relabelled_as_frozen(saved_run, tmp_path):
    prices = saved_run / "artifacts/ohlcv_AAPL.US.csv"
    prices.write_text(prices.read_text().replace("10.0", "10.1"))
    with pytest.raises(ValueError, match="snapshot changed"):
        cost_sensitivity(
            CostRequest(run_dir=str(saved_run), scenarios=[{"name": "base"}]),
            tmp_path / "changed-study",
            lambda value: None,
        )


@pytest.mark.parametrize("saved_run", ["000001.SZ"], indirect=True)
def test_fee_multiplier_reexecutes_minimum_fees_tax_and_measured_break_even(saved_run, tmp_path):
    result = cost_sensitivity(
        CostRequest(
            run_dir=str(saved_run),
            scenarios=[
                {"name": "zero", "fee_multiplier": 0},
                {"name": "high", "fee_multiplier": 10},
            ],
        ),
        tmp_path / "fees-study",
        lambda value: None,
    )
    assert result["rows"][0]["total_return"] > 0
    assert result["rows"][1]["total_return"] < 0
    assert result["break_even_brackets"] == [{"extra_slippage_bps": 0.0, "fee_multiplier_interval": [0.0, 10.0]}]
    import json

    commissions = []
    for row in result["rows"]:
        fills = [json.loads(line) for line in (Path(row["run_dir"]) / "artifacts/fills.jsonl").read_text().splitlines()]
        commissions.append(sum(float(fill["fee"]) for fill in fills))
    assert commissions[0] == 0
    assert commissions[1] > 100


def test_modified_source_configuration_is_refused(saved_run, tmp_path):
    path = saved_run / "config.json"
    config = json.loads(path.read_text())
    config["initial_cash"] = 5000
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="configuration changed"):
        cost_sensitivity(
            CostRequest(run_dir=str(saved_run), scenarios=[{"name": "base"}]),
            tmp_path / "changed-config",
            lambda value: None,
        )


def test_source_strategy_symlink_cannot_escape_run(saved_run, tmp_path):
    strategy = saved_run / "code/signal_engine.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(strategy.read_bytes())
    strategy.unlink()
    strategy.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes its run"):
        cost_sensitivity(
            CostRequest(run_dir=str(saved_run), scenarios=[{"name": "base"}]),
            tmp_path / "escaped-code",
            lambda value: None,
        )


def test_frozen_auto_source_uses_only_snapshot_benchmark(tmp_path, monkeypatch):
    from backtest.engines.global_equity import GlobalEquityEngine
    from backtest.runner import _AutoLoader

    dates = pd.bdate_range("2026-01-01", periods=10)
    prices = np.linspace(10, 11, len(dates))
    frame = pd.DataFrame(
        {"open": prices, "high": prices * 1.01, "low": prices * 0.99, "close": prices, "volume": 1e8}, index=dates
    )

    class FlatSignal:
        def generate(self, data_map):
            return {code: pd.Series(0.0, index=value.index) for code, value in data_map.items()}

    def no_network(*args, **kwargs):
        pytest.fail("frozen benchmark tried a network provider")

    monkeypatch.setattr("backtest.benchmark.YfinanceLoader", no_network)
    config = {
        "codes": ["AAPL.US"],
        "source": "auto",
        "benchmark": "AAPL.US",
        "start_date": str(dates[0].date()),
        "end_date": str(dates[-1].date()),
        "initial_cash": 10000,
        "frozen_data_manifest": "sealed-manifest",
    }
    result = GlobalEquityEngine(config).run_backtest(
        config, _AutoLoader({"AAPL.US": frame}, name="local"), FlatSignal(), tmp_path
    )
    assert result["benchmark_kind"] == "external"
    assert result["benchmark_return"] == pytest.approx(0.1)
