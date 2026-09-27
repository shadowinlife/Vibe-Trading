"""Shared plumbing for the todo-7 SWE-bench / terminal-bench adapters.

Holds the constants, the data-unavailable exception ``run.py`` degrades on,
the small helpers, and ``SweTerminalBase`` — the common HarnessAdapter
implementation (mock dispatch, report assembly with the anti-exploit
spot-check post-run hook, skip markers) shared by ``SWEbenchVerifiedAdapter``
and ``TerminalBenchAdapter`` in ``swe_terminal_adapter.py``.
"""

from __future__ import annotations

import re
import subprocess
import zlib
from pathlib import Path
from typing import Any

from src.evals.harness_bench.adapter import TaskResult
from src.evals.harness_bench.adapters.spot_check import spot_check_sample
from src.evals.harness_bench.adapters.tau2_adapter import Tau2DataUnavailable
from src.evals.harness_bench.report import build_report

PKG_DIR = Path(__file__).resolve().parents[1]
SUBSET_PATH = PKG_DIR / "artifacts" / "swebench_subset.json"
REPO_ROOT = PKG_DIR.parents[2]
SWE_DATASET = "princeton-nlp/SWE-bench_Verified"
SWE_DATA_FILE = "data/test-00000-of-00001.parquet"
DOCKER_PROBE_TIMEOUT_S = 10
POC_NOT_WIRED = (
    "poc-not-wired: the pydantic-ai harness wiring for {benchmark} lands in todo 14"
)


class BenchmarkDataUnavailable(Tau2DataUnavailable):
    """SWE/terminal-bench data or environment unavailable.

    Subclasses ``Tau2DataUnavailable`` because ``run.py``'s task loop
    degrades to a skip marker on exactly that type (run.py predates this
    adapter and catches it by name); the message carries the real cause.
    """


def _unit_hash(*parts: Any) -> float:
    digest = zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))
    return (digest % 1000) / 1000.0


def extract_patch(text: str) -> str | None:
    """Pull the unified diff out of an agent's final answer (None if absent).

    Prefers a ```diff fence; otherwise takes everything from the first
    ``--- ``/``+++ `` file-header pair to the end of the text.
    """
    if not text:
        return None
    fenced = re.search(r"```diff\s*\n(.*?)```", text, re.DOTALL)
    if fenced and fenced.group(1).strip():
        return fenced.group(1).strip()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            return "\n".join(lines[index:]).strip() or None
    return None


def _docker_available() -> bool:
    """Bounded docker liveness probe (never raises, never hangs)."""
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=DOCKER_PROBE_TIMEOUT_S
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _collect_key(doc: Any, key: str) -> list[Any]:
    """Recursively collect every value stored under ``key`` in nested JSON."""
    found: list[Any] = []
    if isinstance(doc, dict):
        for item_key, value in doc.items():
            if item_key == key:
                found.append(value)
            found.extend(_collect_key(value, key))
    elif isinstance(doc, list):
        for value in doc:
            found.extend(_collect_key(value, key))
    return found


class SweTerminalBase:
    """Shared HarnessAdapter plumbing for both todo-7 benchmarks."""

    benchmark_id = ""
    metric_name = ""

    def __init__(self, harness: str, bridge: Any = None):
        if harness not in ("mock", "opencode", "pydantic"):
            raise ValueError(f"unknown harness {harness!r}")
        self.harness = harness
        self.harness_id = {
            "mock": "mock",
            "opencode": "opencode_omo_baseline",
            "pydantic": "pydantic_ai_poc",
        }[harness]
        self.bridge = bridge  # run.py's opencode gate checks this attribute
        self._config: dict[str, Any] = {}
        self._results: list[TaskResult] = []
        self._skip_markers: list[dict[str, Any]] = []
        self._trajectories: list[dict[str, Any]] = []
        self._setup_done = False
        self._spot_summary: dict[str, Any] | None = None

    # -- HarnessAdapter protocol ------------------------------------------- #

    def setup(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self._results = []
        self._skip_markers = []
        self._trajectories = []
        self._setup_done = True
        if self.harness == "opencode" and self.bridge is None:
            raise RuntimeError("opencode harness requires a bridge at construction")

    def run_task(self, task: dict[str, Any]) -> TaskResult:
        if not self._setup_done:
            raise RuntimeError(f"{type(self).__name__}.run_task called before setup()")
        if not task.get("task_id"):
            raise ValueError("task requires a non-empty 'task_id'")
        if self.harness == "pydantic":
            raise NotImplementedError(POC_NOT_WIRED.format(benchmark=self.benchmark_id))
        runner = self._run_mock if self.harness == "mock" else self._run_opencode
        result = runner(task)
        self._results.append(result)
        return result

    def teardown(self) -> None:
        if self.bridge is not None:
            try:
                self.bridge.teardown()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def report(self) -> dict[str, Any]:
        """Assemble the report; runs the anti-exploit spot-check hook first."""
        if not self._setup_done:
            raise RuntimeError(f"{type(self).__name__}.report called before setup()")
        self._spot_summary = spot_check_sample(self._trajectories)
        sampled = set(self._spot_summary["sampled_task_ids"])
        for result in self._results:
            if result.task_id in self._spot_summary["findings"]:
                result.details["spot_check"] = "flagged"
            elif result.task_id in sampled:
                result.details["spot_check"] = "clean"
            else:
                result.details["spot_check"] = "not_sampled"
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        return build_report(
            harness_id=self.harness_id,
            metrics=self._metric_rows(),
            total_cost_usd=round(sum(r.cost_usd for r in self._results), 6),
            skip_markers=list(self._skip_markers),
            seeds={self.benchmark_id: seeds} if seeds else {},
            git_commit=self._config.get("git_commit"),
            cost_note=self._disclosure_note(),
        )

    # -- run.py integration ------------------------------------------------ #

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

    # -- internals --------------------------------------------------------- #

    def _disclosure_note(self) -> str:
        summary = self._spot_summary or {}
        note = (
            f"spot_check: checked={summary.get('checked', 0)} "
            f"passed={summary.get('passed', 0)} flagged={summary.get('flagged', 0)}"
        )
        if summary.get("findings"):
            note += "; flagged_tasks=" + ",".join(sorted(summary["findings"]))
        return note

    def _metric_rows(self) -> list[dict[str, Any]]:
        if any(m["benchmark"] == self.benchmark_id for m in self._skip_markers):
            return []
        attempted = [r for r in self._results if r.status in ("passed", "failed")]
        if not attempted:
            return []
        passed = [r for r in attempted if r.status == "passed"]
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        return [
            {
                "benchmark": self.benchmark_id,
                "metric": self.metric_name,
                "value": round(len(passed) / len(attempted), 6),
                "seeds": seeds,
            }
        ]

    def _record_trajectory(self, task_id: str, texts: list[str]) -> None:
        self._trajectories.append(
            {
                "task_id": str(task_id),
                "texts": texts,
                "files": {},
                "reference_texts": None,
            }
        )

    def _run_mock(self, task: dict[str, Any]) -> TaskResult:
        seed = task.get("seed")
        unit = _unit_hash(self.benchmark_id, task["task_id"], seed)
        self._record_trajectory(
            task["task_id"], [f"mock trajectory {task['task_id']} seed={seed}"]
        )
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=self.benchmark_id,
            status="passed" if unit >= 0.3 else "failed",
            score=round(unit, 6),
            cost_usd=round(0.001 + unit * 0.01, 6),
            duration_seconds=round(0.1 + unit * 0.5, 6),
            seed=int(seed) if seed is not None else None,
            details={"mock_unit": unit},
        )

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        raise NotImplementedError
