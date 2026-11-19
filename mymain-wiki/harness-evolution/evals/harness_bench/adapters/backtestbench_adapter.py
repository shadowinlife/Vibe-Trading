"""BacktestBench (KDD 2026) HarnessAdapter mapped to this repo's backtest chain.

BacktestBench (https://github.com/jensenw1/BacktestBench, arXiv:2605.17937)
is ~18k QA pairs over A-share daily bars (2020-2025) enforcing no-lookahead /
T-1 signal discipline. Task machinery (QA materialization onto the repo
run-dir format, the no-lookahead check, seeded subset sampling, grading)
lives in ``backtestbench_tasks.py``.

Harness modes (same dispatch shape as ``tau2_adapter``):

* ``mock``     -- deterministic synthetic tasks, no data dependency; every
                  task still passes through the REAL no-lookahead check.
* ``opencode`` -- Stage-1 baseline bridge wiring: the QA pair is materialized
                  as ``config.json`` + ``code/signal_engine.py`` (repo run-dir
                  format), the question is posed to the bridge, and the reply
                  is graded against the benchmark's expected answer.
* ``pydantic`` -- NotImplemented placeholder (todo 14); ``run.py`` converts
                  the raise into skip marker ``poc-not-wired``.

No-lookahead verification: every graded task records a ``no_lookahead_check``
in ``TaskResult.details`` and the report carries a ``no_lookahead_pass_rate``
row derived from those per-task check results.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.evals.harness_bench.adapter import TaskResult
from src.evals.harness_bench.adapters.backtestbench_tasks import (
    check_no_lookahead,
    grade_answer,
    materialize_run_dir,
    render_task_artifacts,
    sample_subset,
    subset_rule_text,
    synthetic_qa,
    unit_hash,
)
from src.evals.harness_bench.report import build_report

BENCHMARK_ID = "backtestbench"
POC_NOT_WIRED = (
    "poc-not-wired: the pydantic-ai harness wiring for backtestbench lands in todo 14"
)

_PKG_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = _PKG_DIR / "artifacts" / "backtestbench_data_manifest.json"
_PINNED_COMMIT_SHORT = "4bacdfbe"


class BacktestBenchDataUnavailable(RuntimeError):
    """QA dataset missing locally (run scripts/fetch_backtestbench.py first)."""


def _default_data_dir() -> Path:
    override = os.environ.get("VT_BTB_DATA_DIR", "")
    if override:
        return Path(override)
    return _PKG_DIR.parents[3] / ".venv-eval" / "data" / "backtestbench"


class BacktestBenchAdapter:
    """HarnessAdapter for BacktestBench; see module docstring for the modes."""

    def __init__(self, harness: str, bridge: Any = None, data_dir: Path | None = None):
        if harness not in ("mock", "opencode", "pydantic"):
            raise ValueError(f"unknown harness {harness!r}")
        self.harness = harness
        self.harness_id = {
            "mock": "mock",
            "opencode": "opencode_omo_baseline",
            "pydantic": "pydantic_ai_poc",
        }[harness]
        self._bridge = bridge
        self._data_dir = data_dir
        self._config: dict[str, Any] = {}
        self._results: list[TaskResult] = []
        self._skip_markers: list[dict[str, Any]] = []
        self._setup_done = False

    @property
    def bridge(self) -> Any:
        """The attached OpenCodeBridge or None (run.py's gate checks this)."""
        return self._bridge

    def setup(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self._results = []
        self._skip_markers = []
        self._setup_done = True
        if self.harness == "opencode" and self._bridge is None:
            raise RuntimeError("opencode harness requires a bridge at construction")

    def run_task(self, task: dict[str, Any]) -> TaskResult:
        if not self._setup_done:
            raise RuntimeError("BacktestBenchAdapter.run_task called before setup()")
        if self.harness == "pydantic":
            raise NotImplementedError(POC_NOT_WIRED)
        result = (
            self._run_mock(task) if self.harness == "mock" else self._run_opencode(task)
        )
        self._results.append(result)
        return result

    def teardown(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            try:
                bridge.teardown()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def report(self) -> dict[str, Any]:
        if not self._setup_done:
            raise RuntimeError("BacktestBenchAdapter.report called before setup()")
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        sampling_seed = self._sampling_seed()
        n_subset = len({r.task_id for r in self._results})
        return build_report(
            harness_id=self.harness_id,
            metrics=self._metric_rows(),
            total_cost_usd=round(sum(r.cost_usd for r in self._results), 6),
            skip_markers=list(self._skip_markers),
            seeds={BENCHMARK_ID: seeds} if seeds else {},
            git_commit=self._config.get("git_commit"),
            cost_note=(
                f"{subset_rule_text(sampling_seed, n_subset)}; data_source="
                f"jensenw1/BacktestBench@{_PINNED_COMMIT_SHORT} (KDD 2026), "
                "manifest=artifacts/backtestbench_data_manifest.json"
            ),
        )

    def prepare_tasks(self, n: int) -> list[dict[str, Any]]:
        """Task specs (seed-free); run.py expands them across the seed list."""
        if self.harness == "opencode":
            try:
                pool = self._load_qa_pool()
            except BacktestBenchDataUnavailable as exc:
                self.add_skip_marker(
                    BENCHMARK_ID,
                    f"backtestbench-data-unavailable: {exc}",
                    "excluded_from_adjudication",
                )
                return []
            picked = sample_subset([q["uuid"] for q in pool], n, self._sampling_seed())
            by_uuid = {q["uuid"]: q for q in pool}
            return [
                {
                    "task_id": f"btb-{uuid}",
                    "benchmark": BENCHMARK_ID,
                    "_qa": by_uuid[uuid],
                }
                for uuid in picked
            ]
        # pydantic gets synthetic specs too: its first run_task raises
        # poc-not-wired, which run.py converts into a skip marker.
        return [
            {
                "task_id": f"btb-synthetic-{i}",
                "benchmark": BENCHMARK_ID,
                "_qa": synthetic_qa(f"t{i}"),
            }
            for i in range(n)
        ]

    def add_skip_marker(self, benchmark: str, reason: str, decision: str) -> None:
        self._skip_markers.append(
            {
                "benchmark": benchmark,
                "reason": reason,
                "degraded": True,
                "decision": decision,
            }
        )
        self._results = [r for r in self._results if r.benchmark != benchmark]

    def _sampling_seed(self) -> int:
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            return int(manifest["subset_rule"]["sampling_seed"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return 20260823

    def _load_qa_pool(self) -> list[dict[str, Any]]:
        test_path = Path(self._data_dir or _default_data_dir()) / "raw" / "test.json"
        if not test_path.exists():
            raise BacktestBenchDataUnavailable(
                f"{test_path} missing; run scripts/fetch_backtestbench.py first"
            )
        try:
            records = json.loads(test_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BacktestBenchDataUnavailable(f"test.json unreadable: {exc}") from exc
        pool = [r for r in records if isinstance(r, dict) and r.get("uuid")]
        if not pool:
            raise BacktestBenchDataUnavailable("test.json holds no QA records")
        return pool

    def _run_mock(self, task: dict[str, Any]) -> TaskResult:
        seed = task.get("seed")
        qa = task.get("_qa") or synthetic_qa(str(task["task_id"]))
        config, engine_source = render_task_artifacts(qa)
        ok, detail = check_no_lookahead(config, engine_source)
        unit = unit_hash(task["task_id"], seed)
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=BENCHMARK_ID,
            status="passed" if (unit >= 0.3 and ok) else "failed",
            score=round(unit, 6),
            cost_usd=round(0.001 + unit * 0.01, 6),
            duration_seconds=round(0.1 + unit * 0.5, 6),
            seed=int(seed) if seed is not None else None,
            details={
                "mode": "mock",
                "strategy_type": qa.get("strategy_type"),
                "no_lookahead_check": {"passed": ok, "detail": detail},
                "mock_unit": unit,
            },
        )

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        seed = int(task.get("seed", 0))
        timeout_s = int(self._config.get("task_timeout_s", 900))
        qa = task.get("_qa") or synthetic_qa(str(task["task_id"]))
        with tempfile.TemporaryDirectory(prefix="btb-") as tmp:
            config = materialize_run_dir(qa, Path(tmp))
            engine_source = (Path(tmp) / "code" / "signal_engine.py").read_text(
                encoding="utf-8"
            )
        ok, detail = check_no_lookahead(config, engine_source)
        prompt = (
            "You are a quantitative backtesting agent. Execute the following "
            "backtest using the repo backtest tool (config.json + "
            "signal_engine.py), honouring T-1 / no-lookahead discipline, and "
            f"report the requested KPI as a single number.\n"
            f"Strategy spec:\n{str(qa.get('strategy', ''))[:4000]}\n"
            f"KPI to report: {qa.get('KPI', '')}"
        )
        trajectory = self._bridge.run_task(prompt, timeout_s=timeout_s, seed=seed)
        if trajectory.error is not None:
            return TaskResult(
                task_id=str(task["task_id"]),
                benchmark=BENCHMARK_ID,
                status="error",
                score=None,
                cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
                duration_seconds=trajectory.duration_s,
                seed=seed,
                details={
                    "bridge_error": trajectory.error.as_dict(),
                    "no_lookahead_check": {"passed": ok, "detail": detail},
                },
            )
        score, grade_mode = grade_answer(
            str(qa.get("answer", "")), str(trajectory.final_result or "")
        )
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=BENCHMARK_ID,
            status="passed" if score >= 0.99 else "failed",
            score=round(score, 6),
            cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
            duration_seconds=trajectory.duration_s,
            seed=seed,
            details={
                "strategy_type": qa.get("strategy_type"),
                "grade_mode": grade_mode,
                "no_lookahead_check": {"passed": ok, "detail": detail},
            },
        )

    def _metric_rows(self) -> list[dict[str, Any]]:
        if any(m["benchmark"] == BENCHMARK_ID for m in self._skip_markers):
            return []
        graded = [r for r in self._results if r.status in ("passed", "failed")]
        if not graded:
            return []
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        passed = sum(1 for r in graded if r.status == "passed")
        checks = [
            bool((r.details or {}).get("no_lookahead_check", {}).get("passed"))
            for r in graded
        ]
        return [
            {
                "benchmark": BENCHMARK_ID,
                "metric": "task_success_rate",
                "value": round(passed / len(graded), 6),
                "seeds": seeds,
            },
            {
                "benchmark": BENCHMARK_ID,
                "metric": "no_lookahead_pass_rate",
                "value": round(sum(checks) / len(checks), 6),
                "seeds": seeds,
            },
        ]
