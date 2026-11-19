"""HarnessAdapter protocol shared by every harness under comparison.

The benchmark suite (todos 4-8, 14) measures harnesses, not models: both the
opencode+OMO baseline and the PydanticAI PoC run the SAME tasks under the
SAME parity spec and emit reports through this one contract, so their
results are comparable row for row.

Contract:

* ``setup(config)``   -- accept the run configuration (parity-checked).
* ``run_task(task)``  -- run one benchmark task, return a ``TaskResult``.
* ``teardown()``      -- release resources; must be safe to call twice.
* ``report()``        -- return the benchmark report dict; it must validate
  against ``report_schema.json`` (see ``report.validate_report``).

``MockAdapter`` is the deterministic reference implementation: no network,
no randomness (it derives every value from CRC32 of the task identity), and
its report always passes schema validation. Later todos' mock runs and the
scaffold tests use it to pin the contract before real adapters exist.

Research-only: nothing here places orders or touches product runtime code.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.evals.harness_bench import report

#: Task statuses a harness may report. ``skipped`` is reserved for the
#: degraded-mode convention (see README.md): the task was never attempted.
TASK_STATUSES = ("passed", "failed", "skipped", "error")


@dataclass(frozen=True)
class TaskResult:
    """Outcome of one benchmark task.

    Attributes:
        task_id: Benchmark-local unique task identifier.
        benchmark: Benchmark id (e.g. ``tau2``, ``financebench``).
        status: One of ``TASK_STATUSES``.
        score: Task score in [0, 1] when the benchmark defines one, else
            ``None`` (e.g. for ``skipped``/``error`` results).
        cost_usd: Attributed model/API cost of this task in USD (>= 0).
        duration_seconds: Wall-clock time spent on this task.
        seed: Seed this task ran under, per the parity spec.
        details: Harness/benchmark-specific extras (free-form JSON-able).
    """

    task_id: str
    benchmark: str
    status: str
    score: float | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    seed: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class HarnessAdapter(Protocol):
    """The contract every harness under comparison must satisfy."""

    harness_id: str

    def setup(self, config: dict[str, Any]) -> None:
        """Accept the run configuration (parity spec already validated)."""
        ...

    def run_task(self, task: dict[str, Any]) -> TaskResult:
        """Run one benchmark task and return its result."""
        ...

    def teardown(self) -> None:
        """Release resources; must be idempotent and never raise."""
        ...

    def report(self) -> dict[str, Any]:
        """Return the benchmark report (validates against report_schema.json)."""
        ...


def _unit_hash(*parts: Any) -> float:
    """Deterministic unit value in [0, 1) from the given parts.

    CRC32 (not the builtin ``hash``) so the value is stable across processes
    regardless of PYTHONHASHSEED — mock runs must be reproducible.
    """
    digest = zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))
    return (digest % 1000) / 1000.0


class MockAdapter:
    """Deterministic reference implementation of ``HarnessAdapter``.

    Config keys (all optional):
        harness_id: Reported harness id (default ``mock``).
        skip_benchmarks: ``{benchmark: reason}`` — benchmarks to mark as
            degraded skips in the report (exercises the skip-marker section).
        git_commit: Provenance commit string (default: real HEAD or a
            fixed placeholder when git is unavailable).

    Task keys: ``task_id`` and ``benchmark`` are required; ``seed`` optional.
    """

    def __init__(self) -> None:
        self.harness_id = "mock"
        self._config: dict[str, Any] = {}
        self._results: list[TaskResult] = []
        self._setup_done = False
        self._torn_down = False

    def setup(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self.harness_id = str(self._config.get("harness_id") or "mock")
        self._results = []
        self._setup_done = True
        self._torn_down = False

    def run_task(self, task: dict[str, Any]) -> TaskResult:
        if not self._setup_done:
            raise RuntimeError("MockAdapter.run_task called before setup()")
        task_id = str(task.get("task_id", ""))
        benchmark = str(task.get("benchmark", ""))
        if not task_id or not benchmark:
            raise ValueError("task requires non-empty 'task_id' and 'benchmark'")
        seed = task.get("seed")
        unit = _unit_hash(benchmark, task_id, seed)
        status = "passed" if unit >= 0.3 else "failed"
        result = TaskResult(
            task_id=task_id,
            benchmark=benchmark,
            status=status,
            score=round(unit, 6),
            cost_usd=round(0.001 + unit * 0.01, 6),
            duration_seconds=round(0.1 + unit * 0.5, 6),
            seed=int(seed) if seed is not None else None,
            details={"mock_unit": unit},
        )
        self._results.append(result)
        return result

    def teardown(self) -> None:
        self._torn_down = True

    def report(self) -> dict[str, Any]:
        if not self._setup_done:
            raise RuntimeError("MockAdapter.report called before setup()")
        metrics: list[dict[str, Any]] = []
        seeds: dict[str, list[int]] = {}
        for benchmark in sorted({r.benchmark for r in self._results}):
            rows = [r for r in self._results if r.benchmark == benchmark]
            attempted = [r for r in rows if r.status in ("passed", "failed")]
            passed = [r for r in rows if r.status == "passed"]
            if attempted:
                metrics.append(
                    {
                        "benchmark": benchmark,
                        "metric": "pass_rate",
                        "value": round(len(passed) / len(attempted), 6),
                        "seeds": sorted({r.seed for r in rows if r.seed is not None}),
                    }
                )
            bench_seeds = sorted({r.seed for r in rows if r.seed is not None})
            if bench_seeds:
                seeds[benchmark] = bench_seeds
        skip_markers = [
            {
                "benchmark": str(name),
                "reason": str(reason),
                "degraded": True,
                "decision": "excluded_from_adjudication",
            }
            for name, reason in sorted(
                (self._config.get("skip_benchmarks") or {}).items()
            )
        ]
        total_cost = round(sum(r.cost_usd for r in self._results), 6)
        return report.build_report(
            harness_id=self.harness_id,
            metrics=metrics,
            total_cost_usd=total_cost,
            skip_markers=skip_markers,
            seeds=seeds,
            git_commit=str(
                self._config.get("git_commit") or report.current_git_commit()
            ),
        )
