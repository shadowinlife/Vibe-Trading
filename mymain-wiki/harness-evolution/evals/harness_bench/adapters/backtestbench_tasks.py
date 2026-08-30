"""BacktestBench task machinery: QA materialization, T-1 checks, sampling.

Pure helpers shared by the BacktestBench adapter (todo 6). BacktestBench
(KDD 2026, arXiv:2605.17937) QA records carry ``uuid``, ``strategy``,
``strategy_type``, ``SQL_statement``, ``KPI``, ``code`` and ``answer``.

No-lookahead verification: ``check_no_lookahead`` is the static assertion on
a generated (config, signal-engine) pair — config date discipline (the
decision window starts after the data window, so decisions at t can only
consume bars <= t-1), a future-reference pattern scan, and presence of the
runtime T-1 guard hook that every materialized engine embeds.

Toolchain mapping (no re-implemented backtest math): ``materialize_run_dir``
writes a repo-format run dir; ``execute_run_dir`` drives it through the same
wiring as ``src/tools/backtest_tool.py`` (``src.core.runner.Runner``
executing ``agent/backtest/runner.py``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import string
import zlib
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parents[1]
AGENT_ROOT = Path(__file__).resolve().parents[4]

#: Calendar warm-up before the first decision, guaranteeing the first
#: decision at t has strictly-earlier bars (t-1, t-2, ...) available.
WARMUP_DAYS = 30
DEFAULT_FALLBACK_WINDOW = ("2024-01-02", "2024-12-31")

T1_GUARD_MARKER = "_vt_assert_t1_discipline"
_LOOKAHEAD_PATTERNS = (
    (re.compile(r"\.shift\s*\(\s*-\s*\d"), "negative_shift: future bar reference"),
    (re.compile(r"\bcenter\s*=\s*True"), "centered_window: uses future bars"),
    (
        re.compile(r"\[\s*(?:i|t|idx)\s*\+\s*1\s*\]"),
        "forward_index: explicit t+1 access",
    ),
)


def unit_hash(*parts: Any) -> float:
    """Deterministic unit value in [0, 1) (CRC32: stable across processes)."""
    digest = zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))
    return (digest % 1000) / 1000.0


def sample_subset(uuids: list[str], n: int, seed: int) -> list[str]:
    """Seeded subset sampler; identical inputs always give identical output.

    Rule (mirrored into manifest + reports): sort the uuid pool
    lexicographically, then ``random.Random(f"backtestbench-v1:{seed}")``
    samples ``min(n, len(pool))``. String-seeded ``random.Random`` is stable
    across processes (independent of PYTHONHASHSEED).
    """
    pool = sorted(set(uuids))
    if n <= 0 or not pool:
        return []
    if n >= len(pool):
        return list(pool)
    return random.Random(f"backtestbench-v1:{seed}").sample(pool, n)


def subset_rule_text(seed: int, n: int) -> str:
    return (
        "subset_rule=backtestbench-v1: uuid pool (test split) sorted "
        f"lexicographically, random.Random('backtestbench-v1:{seed}').sample"
        f"(pool, min({n}, len(pool))); identical subset per harness per seed"
    )


def check_no_lookahead(config: dict[str, Any], engine_source: str) -> tuple[bool, str]:
    """T-1 discipline assertion on a (config, signal-engine) pair.

    (1) config date discipline — valid window; a declared
    ``decision_start_date`` must be strictly after ``start_date`` so the
    first decision can only consume bars <= t-1; (2) static scan for
    future-reference patterns; (3) runtime T-1 guard hook present.
    """
    try:
        start = dt.date.fromisoformat(str(config["start_date"]))
        end = dt.date.fromisoformat(str(config["end_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"config_dates_invalid: {exc}"
    if start >= end:
        return False, f"config_dates_invalid: start_date {start} >= end_date {end}"
    decision_raw = config.get("decision_start_date")
    if decision_raw is not None:
        try:
            decision_start = dt.date.fromisoformat(str(decision_raw))
        except (TypeError, ValueError):
            return False, f"decision_start_invalid: {decision_raw!r}"
        if decision_start <= start:
            return False, (
                "no_warmup: decision_start_date must be after start_date so a "
                "decision at t only consumes bars <= t-1"
            )
        if decision_start > end:
            return False, "decision_start_after_end"
    for pattern, name in _LOOKAHEAD_PATTERNS:
        if pattern.search(engine_source):
            return False, f"lookahead_pattern: {name}"
    if T1_GUARD_MARKER not in engine_source:
        return False, "missing_runtime_hook: engine never calls the T-1 guard"
    return True, (
        "ok: config dates T-1-disciplined, no future-reference pattern, "
        "runtime guard present"
    )


_ENGINE_TEMPLATE = string.Template(
    '''"""Materialized from BacktestBench QA $uuid (strategy_type=$strategy_type).

Benchmark question (verbatim excerpt): $strategy_excerpt
Target KPI: $kpi
T-1 discipline: every decision dated t consumes only bars dated < t.
"""

from typing import Dict

import pandas as pd


def _vt_assert_t1_discipline(decision_date, consumed_dates) -> None:
    """Runtime no-lookahead guard: a decision at t may only see bars < t."""
    if len(consumed_dates) and max(consumed_dates) >= decision_date:
        raise RuntimeError(
            f"lookahead violation: decision at {decision_date} "
            f"consumed bar {max(consumed_dates)}"
        )


class SignalEngine:
    """Stage-1 reference engine: long when close exceeds the T-1 close."""

    def __init__(self) -> None:
        self.kpi = $kpi_repr

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        signals: Dict[str, pd.Series] = {}
        for code, frame in data_map.items():
            dates = frame.index
            signal = pd.Series(0.0, index=dates)
            closes = frame["close"]
            for i in range(1, len(dates)):
                decision_date = dates[i]
                consumed = dates[:i]  # strictly bars <= t-1
                _vt_assert_t1_discipline(decision_date, consumed)
                signal.iloc[i] = 1.0 if closes.iloc[i] > closes.iloc[i - 1] else 0.0
            signals[code] = signal
        return signals
'''
)

_SQL_DATE_GE = re.compile(r"trade_date\s*>=?\s*'(\d{4}-\d{2}-\d{2})'")
_SQL_DATE_LE = re.compile(r"trade_date\s*<=?\s*'(\d{4}-\d{2}-\d{2})'")


def parse_period(sql: str) -> tuple[str, str]:
    """Data window from the QA's SQL_statement (fallback: documented default)."""
    ge = _SQL_DATE_GE.search(sql or "")
    le = _SQL_DATE_LE.search(sql or "")
    if ge and le and ge.group(1) < le.group(1):
        return ge.group(1), le.group(1)
    return DEFAULT_FALLBACK_WINDOW


def render_task_artifacts(qa: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Pure (config, engine_source) pair for one QA record — no filesystem."""
    start_s, end_s = parse_period(str(qa.get("SQL_statement", "")))
    start = dt.date.fromisoformat(start_s)
    end = dt.date.fromisoformat(end_s)
    decision_start = min(start + dt.timedelta(days=WARMUP_DAYS), end)
    config: dict[str, Any] = {
        "source": os.environ.get("VT_BTB_SOURCE", "tushare"),
        "codes": [os.environ.get("VT_BTB_CODE", "000001.SZ")],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "decision_start_date": decision_start.isoformat(),
        "interval": "1D",
        "engine": "daily",
        "backtestbench": {
            "uuid": str(qa.get("uuid", "")),
            "strategy_type": str(qa.get("strategy_type", "")),
            "kpi": str(qa.get("KPI", "")),
            "expected_answer": str(qa.get("answer", "")),
            "name_to_code_note": (
                "benchmark names stocks in Chinese; name->code resolution is "
                "Stage-2 wiring, the code above is the documented default"
            ),
        },
    }
    engine_source = _ENGINE_TEMPLATE.substitute(
        uuid=str(qa.get("uuid", "?")),
        strategy_type=str(qa.get("strategy_type", "?")),
        strategy_excerpt=str(qa.get("strategy", ""))[:300].replace('"""', "'''"),
        kpi=str(qa.get("KPI", "")),
        kpi_repr=repr(str(qa.get("KPI", ""))),
    )
    return config, engine_source


def materialize_run_dir(qa: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Write ``config.json`` + ``code/signal_engine.py`` for one QA pair."""
    config, engine_source = render_task_artifacts(qa)
    (run_dir / "code").mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "code" / "signal_engine.py").write_text(engine_source, encoding="utf-8")
    return config


def execute_run_dir(run_dir: Path, timeout_s: int = 300) -> dict[str, Any]:
    """Drive a materialized run dir through the repo's own backtest entrypoint.

    Same wiring as ``src/tools/backtest_tool.py``: ``src.core.runner.Runner``
    executes ``agent/backtest/runner.py`` with the run dir as CLI argument.
    Backtest math is never re-implemented here.
    """
    from src.core.runner import Runner  # lazy: mock mode never imports it

    entry_script = AGENT_ROOT / "backtest" / "runner.py"
    runner = Runner(timeout=timeout_s)
    result = runner.execute(
        entry_script, run_dir, cwd=AGENT_ROOT, cli_args=[str(run_dir)]
    )
    return {
        "success": bool(result.success),
        "exit_code": int(result.exit_code),
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "artifacts": {name: str(path) for name, path in result.artifacts.items()},
    }


def grade_answer(expected: str, produced: str) -> tuple[float, str]:
    """Grade a produced answer against the benchmark's expected value."""
    try:
        exp = float(expected)
        got = float(produced)
    except (TypeError, ValueError):
        ok = str(produced).strip() == str(expected).strip()
        return (1.0 if ok else 0.0), "string_exact"
    if exp == 0.0:
        return (1.0 if got == 0.0 else 0.0), "numeric_exact_zero"
    rel = abs(got - exp) / abs(exp)
    if rel <= 0.01:
        return 1.0, "numeric_within_1pct"
    return max(0.0, 1.0 - rel), "numeric_relative"


def synthetic_qa(task_id: str) -> dict[str, Any]:
    """Deterministic synthetic QA record (mock path; zero data dependency)."""
    unit = unit_hash("btb-qa", task_id)
    return {
        "uuid": f"synthetic-{task_id}",
        "strategy": (
            f"Synthetic deterministic task {task_id}: long when today's close "
            "exceeds yesterday's close; T-1 discipline enforced."
        ),
        "strategy_type": "metrics_calculation",
        "SQL_statement": (
            "SELECT closing_price, trade_date FROM synthetic WHERE "
            f"trade_date >= '{DEFAULT_FALLBACK_WINDOW[0]}' AND "
            f"trade_date <= '{DEFAULT_FALLBACK_WINDOW[1]}';"
        ),
        "KPI": "Win Rate",
        "code": "",
        "answer": f"{unit * 100:.4f}",
        "factors": [],
    }
