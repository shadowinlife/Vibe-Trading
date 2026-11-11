"""Integration tests for the frozen alpha-bench execution path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.market_data import get_loader
from src.tools.alpha_bench_frozen import run_frozen_bench
from src.tools.alpha_bench_tool import run_alpha_bench

SYMBOLS = [f"SYM{i}.US" for i in range(6)]
DATES = pd.date_range("2023-01-02", periods=270, freq="B")
SPLIT_POSITION = 260


def _frame(symbol: str) -> pd.DataFrame:
    offset = SYMBOLS.index(symbol)
    steps = np.arange(len(DATES), dtype=float)
    returns = 0.0005 * (offset + 1) + 0.01 * np.sin(
        steps * (0.07 + 0.01 * offset) + offset
    )
    close = (50.0 + 10.0 * offset) * np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000.0 + offset + steps,
        },
        index=DATES,
    )


def _payload(root: Path, phase: str = "in_sample") -> dict[str, Any]:
    return {
        "candidate_id": "academic_bab",
        "phase": phase,
        "symbols": list(SYMBOLS),
        "start_date": DATES[0].date().isoformat(),
        "end_date": DATES[-1].date().isoformat(),
        "horizon": 1,
        "source": "alpha_zoo",
        "split_date": DATES[SPLIT_POSITION].date().isoformat(),
        "as_of": DATES[-1].date().isoformat(),
        "output_dir": str(root / phase),
    }


@pytest.fixture
def run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runs"
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


@pytest.fixture
def market_data(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_fetch_market_data(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {symbol: _frame(symbol) for symbol in SYMBOLS}

    monkeypatch.setattr("src.market_data.fetch_market_data", fake_fetch_market_data)
    return calls


@pytest.mark.parametrize(
    ("phase", "return_positions", "paired_positions"),
    [
        ("in_sample", list(range(0, 259)), list(range(252, 259))),
        ("out_of_sample", list(range(260, 269)), list(range(260, 269))),
    ],
)
def test_frozen_bench_writes_masked_csvs_with_one_load(
    run_root: Path,
    market_data: list[dict[str, Any]],
    phase: str,
    return_positions: list[int],
    paired_positions: list[int],
) -> None:
    result = run_frozen_bench(_payload(run_root, phase))

    output_dir = (run_root / phase).resolve()
    expected_obs = len(paired_positions) * len(SYMBOLS)
    assert result["status"] == "ok"
    assert result["candidate_id"] == "academic_bab"
    assert result["phase"] == phase
    assert result["horizon"] == 1
    assert result["n_obs"] == expected_obs
    assert result["n_dates"] == len(paired_positions)
    assert result["reasons"] == []
    assert result["factor_csv"] == str(output_dir / "factor.csv")
    assert result["returns_csv"] == str(output_dir / "returns.csv")
    assert result["output_dir"] == str(output_dir)

    factor = pd.read_csv(
        output_dir / "factor.csv", index_col="date", parse_dates=["date"]
    )
    returns = pd.read_csv(
        output_dir / "returns.csv", index_col="date", parse_dates=["date"]
    )
    for frame in (factor, returns):
        assert frame.shape == (len(DATES), len(SYMBOLS))
        assert frame.index.name == "date"
        pd.testing.assert_index_equal(frame.index, DATES.rename("date"))
        assert frame.columns.tolist() == SYMBOLS
    assert factor.iloc[:252].isna().all().all()
    assert factor.iloc[252:].notna().all().all()
    expected_index = pd.DatetimeIndex(DATES.to_numpy(), name="date")
    expected_mask = pd.DataFrame(False, index=expected_index, columns=SYMBOLS)
    expected_mask.iloc[return_positions, :] = True
    pd.testing.assert_frame_equal(returns.notna(), expected_mask)

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary == {
        "status": "ok",
        "candidate_id": "academic_bab",
        "phase": phase,
        "horizon": 1,
        "windows": {
            "start_date": DATES[0].date().isoformat(),
            "split_date": DATES[SPLIT_POSITION].date().isoformat(),
            "end_date": DATES[-1].date().isoformat(),
            "as_of": DATES[-1].date().isoformat(),
        },
        "n_obs": expected_obs,
        "n_dates": len(paired_positions),
        "reasons": [],
    }
    assert len(market_data) == 1
    call = market_data[0]
    assert call["codes"] == SYMBOLS
    assert call["start_date"] == DATES[0].date().isoformat()
    assert call["end_date"] == DATES[-1].date().isoformat()
    assert call["source"] == "auto"
    assert call["include_provenance"] is True
    assert call["as_frames"] is True
    assert call["loader_resolver"] is get_loader


def test_unknown_candidate_errors_without_csvs(
    run_root: Path, market_data: list[dict[str, Any]]
) -> None:
    payload = _payload(run_root)
    payload["candidate_id"] = "academic_missing"

    result = run_frozen_bench(payload)

    assert result["status"] == "error"
    assert "not in registry" in result["error"]
    assert not list(run_root.rglob("*.csv"))


def test_fundamental_candidate_is_insufficient_without_fabrication(
    run_root: Path, market_data: list[dict[str, Any]]
) -> None:
    payload = _payload(run_root)
    payload["candidate_id"] = "fund_roe"

    result = run_frozen_bench(payload)

    output_dir = (run_root / "in_sample").resolve()
    assert result["status"] == "insufficient_data"
    assert result["n_obs"] == 0
    assert result["n_dates"] == 0
    assert result["reasons"]
    assert (output_dir / "summary.json").is_file()
    assert not (output_dir / "factor.csv").exists()
    assert not (output_dir / "returns.csv").exists()


def test_no_market_data_writes_only_summary(
    run_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.market_data.fetch_market_data",
        lambda **kwargs: {"_unresolved": list(SYMBOLS)},
    )

    result = run_frozen_bench(_payload(run_root))

    output_dir = (run_root / "in_sample").resolve()
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "no_market_data"
    assert result["reasons"] == ["no_market_data"]
    assert result["factor_csv"] is None
    assert result["returns_csv"] is None
    assert (output_dir / "summary.json").is_file()
    assert not (output_dir / "factor.csv").exists()
    assert not (output_dir / "returns.csv").exists()


def test_insufficient_rerun_removes_stale_csvs(
    run_root: Path,
    market_data: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(run_root)
    assert run_frozen_bench(payload)["status"] == "ok"
    monkeypatch.setattr(
        "src.market_data.fetch_market_data",
        lambda **kwargs: {"_unresolved": list(SYMBOLS)},
    )

    result = run_frozen_bench(payload)

    output_dir = (run_root / "in_sample").resolve()
    assert result["status"] == "insufficient_data"
    assert not (output_dir / "factor.csv").exists()
    assert not (output_dir / "returns.csv").exists()
    assert (output_dir / "summary.json").is_file()


def test_bad_spec_is_rejected_before_load(
    run_root: Path, market_data: list[dict[str, Any]]
) -> None:
    payload = _payload(run_root)
    payload["horizon"] = 0

    result = run_frozen_bench(payload)

    assert result["status"] == "error"
    assert result["field"] == "horizon"
    assert market_data == []


def test_path_escape_is_rejected_before_load(
    run_root: Path, tmp_path: Path, market_data: list[dict[str, Any]]
) -> None:
    payload = _payload(run_root)
    payload["output_dir"] = str(tmp_path / "outside")

    result = run_frozen_bench(payload)

    assert result["status"] == "error"
    assert result["field"] == "output_dir"
    assert market_data == []


@pytest.mark.parametrize("source", ["alpha_zoo", "complete_fields"])
def test_run_alpha_bench_dispatches_frozen_payload_before_legacy_validation(
    run_root: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(payload: dict[str, Any]) -> dict[str, Any]:
        seen.update(payload)
        return {"status": "dispatched"}

    monkeypatch.setattr("src.tools.alpha_bench_frozen.run_frozen_bench", fake_run)
    payload = _payload(run_root)
    payload["source"] = source

    assert run_alpha_bench(**payload) == {"status": "dispatched"}
    assert seen == payload


def test_legacy_validation_is_unchanged_without_frozen_fields() -> None:
    assert run_alpha_bench() == {
        "status": "error",
        "error": "universe is required (string)",
    }
    assert run_alpha_bench(universe="sp500") == {
        "status": "error",
        "error": "period is required (string)",
    }
