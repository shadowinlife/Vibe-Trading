"""Result-budget contract for the backtest tool envelope.

Pins the MCP path's 48 KiB / 2000-line budget (4 KiB headroom under the host's
50 KiB cap), the model path's 10,000-byte cap, and the trimming order: logs,
the legacy artifact map, optional summary blocks, preview points, codes and
metric entries. ``summary`` is never removed, required string fields are only
clipped as a last resort, and every dropped block is named in
``summary["omitted_fields"]``.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src.config.limits import (
    MCP_TOOL_RESULT_BYTE_BUDGET,
    TOOL_RESULT_LIMIT,
    TOOL_RESULT_LINE_BUDGET,
)
from src.tools.backtest_summary import (
    MAX_PREVIEW_POINTS,
    REQUIRED_SUMMARY_KEYS,
    build_backtest_summary,
)
from src.tools.backtest_tool import run_backtest

CARD_METRICS = {
    "sharpe": 1.25,
    "return_pct": 0.1523,
    "max_drawdown": -0.0831,
    "annual_return": 0.31,
    "trades": 42,
}
EQUITY_COLUMNS = [
    "timestamp",
    "ret",
    "equity",
    "drawdown",
    "benchmark_equity",
    "active_ret",
]


def _run_card(
    codes: list[str] | None = None, warnings: list[str] | None = None
) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-09-30T00:00:00Z",
        "backtest": {
            "codes": codes if codes is not None else ["BTC-USDT"],
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "interval": "1D",
            "engine": "crypto",
            "initial_cash": 1000000,
            "source": "okx",
        },
        "reproducibility": {"config_hash": "0" * 64},
        "data_sources": ["okx"],
        "metrics": dict(CARD_METRICS),
        "warnings": warnings or [],
        "artifacts": [],
    }


def _equity_rows(count: int) -> list[dict]:
    start = date(2024, 1, 1)
    return [
        {
            "timestamp": (start + timedelta(days=i)).isoformat(),
            "ret": 0.001,
            "equity": 1000000.0 + 1000.0 * i,
            "drawdown": -0.0001 * i,
            "benchmark_equity": 1000000.0 + 500.0 * i,
            "active_ret": 0.0005,
        }
        for i in range(count)
    ]


def _write_equity_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EQUITY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


@dataclass
class _FakeRunResult:
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    artifacts: dict = field(default_factory=dict)


def _run_tool(
    run_dir: Path, result: _FakeRunResult, *, byte_limit: int | None = None
) -> dict:
    kwargs = {} if byte_limit is None else {"byte_limit": byte_limit}
    with (
        patch("src.tools.backtest_tool.emit_progress"),
        patch("src.tools.backtest_tool.Runner") as runner_cls,
    ):
        runner_cls.return_value.execute.return_value = result
        return json.loads(run_backtest(str(run_dir), **kwargs))


def _build_budget_run_dir(
    root: Path,
    name: str,
    *,
    codes: list[str],
    warnings: list[str] | None = None,
    validation: dict | None = None,
    equity_rows: int = 5,
) -> Path:
    run_dir = root / name
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "config.json").write_text(
        json.dumps({"source": "okx"}), encoding="utf-8"
    )
    (run_dir / "code" / "signal_engine.py").write_text("", encoding="utf-8")
    card = _run_card(codes=codes, warnings=warnings)
    if validation is not None:
        card["validation"] = validation
    (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")
    _write_equity_csv(run_dir / "artifacts" / "equity.csv", _equity_rows(equity_rows))
    for code in codes:
        (run_dir / "artifacts" / f"ohlcv_{code}.csv").write_text(
            "timestamp,open\n", encoding="utf-8"
        )
    return run_dir


_MCP_CODES = [f"6000{index:02d}.SH" for index in range(50)]


def test_mcp_budget_keeps_summary_and_omits_bulk_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(
        tmp_path,
        "kc50_beta_gate_mult",
        codes=_MCP_CODES,
        warnings=[f"warning {index}: adjusted" for index in range(120)],
        validation={"detail": "v" * 200_000},
        equity_rows=500,
    )
    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0, stdout="o" * 4000, stderr="e" * 4000),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    delivered = json.dumps(result, ensure_ascii=False)
    summary = result["summary"]
    assert isinstance(summary, dict)
    assert summary["schema_version"] == "1.0"
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert 0 < len(summary["equity_preview"]) <= MAX_PREVIEW_POINTS
    assert len(delivered.encode("utf-8")) <= MCP_TOOL_RESULT_BYTE_BUDGET
    assert delivered.count("\n") + 1 <= TOOL_RESULT_LINE_BUDGET
    assert "validation" in summary["omitted_fields"]


def test_pathological_validation_is_reported_as_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(
        tmp_path,
        "run_pathological",
        codes=_MCP_CODES,
        validation={"detail": "v" * 2_000_000},
        equity_rows=500,
    )
    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    delivered = json.dumps(result, ensure_ascii=False)
    summary = result["summary"]
    assert isinstance(summary, dict)
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert 0 < len(summary["equity_preview"]) <= MAX_PREVIEW_POINTS
    assert "validation" in summary["omitted_fields"]
    assert len(delivered.encode("utf-8")) <= MCP_TOOL_RESULT_BYTE_BUDGET
    assert delivered.count("\n") + 1 <= TOOL_RESULT_LINE_BUDGET


def test_default_model_delivery_keeps_summary_within_tool_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(
        tmp_path,
        "run_model_delivery",
        codes=_MCP_CODES,
        warnings=[f"warning {index}: adjusted" for index in range(120)],
        equity_rows=500,
    )
    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0, stdout="o" * 4000, stderr="e" * 4000),
    )

    delivered = json.dumps(result, ensure_ascii=False)
    assert len(delivered.encode("utf-8")) <= TOOL_RESULT_LIMIT
    assert isinstance(result["summary"], dict)
    assert all(key in result["summary"] for key in REQUIRED_SUMMARY_KEYS)
    assert 0 < len(result["summary"]["equity_preview"]) <= MAX_PREVIEW_POINTS


def test_run_id_is_restricted_to_consumer_safe_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    normal_dir = _build_budget_run_dir(tmp_path, "run_20260930.a-b", codes=["BTC-USDT"])
    assert build_backtest_summary(normal_dir)["run_id"] == "run_20260930.a-b"

    first = _build_budget_run_dir(tmp_path, "run 测试 01", codes=["BTC-USDT"])
    second = _build_budget_run_dir(tmp_path, "run 测试 02", codes=["BTC-USDT"])
    first_id = build_backtest_summary(first)["run_id"]
    second_id = build_backtest_summary(second)["run_id"]
    assert re.fullmatch(r"[A-Za-z0-9._-]+", first_id)
    assert first_id != second_id, "distinct directories must not share a run_id"

    envelope = _run_tool(first, _FakeRunResult(success=True, exit_code=0))
    assert envelope["summary"]["run_id"] == first_id


def test_oversized_metric_drops_only_its_largest_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(tmp_path, "run_huge_metric", codes=["BTC-USDT"])
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    card["metrics"]["note"] = "x" * (MCP_TOOL_RESULT_BYTE_BUDGET + 100)
    (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")

    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    summary = result["summary"]
    assert "note" not in summary["metrics"]
    assert summary["metrics"]["sharpe"] == CARD_METRICS["sharpe"]
    assert "metrics" in summary["omitted_fields"]
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        <= MCP_TOOL_RESULT_BYTE_BUDGET
    )


def test_single_oversized_code_is_clipped_into_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(tmp_path, "run_huge_code", codes=["BTC-USDT"])
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    card["backtest"]["codes"] = ["C" * 60_000]
    (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")

    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    summary = result["summary"]
    assert summary["codes"] and len(summary["codes"][0]) <= 64
    assert "text" in summary["omitted_fields"]
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        <= MCP_TOOL_RESULT_BYTE_BUDGET
    )


def test_oversized_initial_cash_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(tmp_path, "run_huge_cash", codes=["BTC-USDT"])
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    card["backtest"]["initial_cash"] = "Z" * 60_000
    (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")

    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    summary = result["summary"]
    assert "initial_cash" not in summary
    assert "initial_cash" in summary["omitted_fields"]
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        <= MCP_TOOL_RESULT_BYTE_BUDGET
    )


def test_many_metric_keys_are_trimmed_into_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(tmp_path, "run_many_metrics", codes=["BTC-USDT"])
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    card["metrics"] = {f"metric_{index}": "v" * 100 for index in range(2000)}
    (run_dir / "run_card.json").write_text(json.dumps(card), encoding="utf-8")

    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    summary = result["summary"]
    assert 0 < len(summary["metrics"]) < 2000
    assert "metrics" in summary["omitted_fields"]
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        <= MCP_TOOL_RESULT_BYTE_BUDGET
    )


def test_oversized_preview_time_is_clipped_into_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    run_dir = _build_budget_run_dir(tmp_path, "run_huge_time", codes=["BTC-USDT"])
    (run_dir / "artifacts" / "equity.csv").write_text(
        "timestamp,ret,equity,drawdown,benchmark_equity,active_ret\n"
        + "T" * 60_000
        + ",0,1000000,0,1000000,0\n",
        encoding="utf-8",
    )

    result = _run_tool(
        run_dir,
        _FakeRunResult(success=True, exit_code=0),
        byte_limit=MCP_TOOL_RESULT_BYTE_BUDGET,
    )

    summary = result["summary"]
    assert 0 < len(summary["equity_preview"]) <= MAX_PREVIEW_POINTS
    assert len(summary["equity_preview"][0]["time"]) <= 64
    assert "text" in summary["omitted_fields"]
    assert all(key in summary for key in REQUIRED_SUMMARY_KEYS)
    assert (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        <= MCP_TOOL_RESULT_BYTE_BUDGET
    )
