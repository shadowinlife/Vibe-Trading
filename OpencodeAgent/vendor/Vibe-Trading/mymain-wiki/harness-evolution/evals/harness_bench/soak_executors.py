"""Workload executors for the soak rig.

An executor runs ONE iteration of the standard workload loop (see
``soak.DEFAULT_WORKLOAD``) against a concrete harness. ``run_soak`` calls
``setup()`` before the measurement clock starts and ``teardown()`` after the
final sample; ``measured_pid`` names the process the process-boundary sampler
observes.
"""

from __future__ import annotations

import os
import time
from typing import Any

from src.evals.harness_bench.soak_samplers import WorkloadExecutor


class MockWorkloadExecutor:
    """Cheap deterministic executor for tests and seconds-scale smoke runs."""

    def __init__(self, per_iteration_sleep: float = 0.001) -> None:
        self._sleep = per_iteration_sleep
        self._ballast: list[bytes] = []

    def setup(self) -> None:
        self._ballast = []

    def run_iteration(self, index: int) -> None:
        # Deterministic, bounded memory churn so RSS has something to do.
        self._ballast.append(bytes(1024))
        if len(self._ballast) > 256:
            self._ballast.pop(0)
        if self._sleep > 0:
            time.sleep(self._sleep)

    def teardown(self) -> None:
        self._ballast = []

    @property
    def measured_pid(self) -> int | None:
        return os.getpid()


class McpWorkloadExecutor:
    """Real workload against a live MCP subprocess (process boundary).

    Each iteration runs the standard loop: a ``tools/list`` round-trip, one
    lightweight network-free ``tools/call`` (``analyze_options``), and one
    report generation + validation. The measured process is the MCP server
    subprocess itself.
    """

    def __init__(self, env_overrides: dict[str, str | None] | None = None) -> None:
        self._env_overrides = env_overrides or {}
        self._client: Any = None

    def setup(self) -> None:
        from src.evals.harness_bench import mcp_spawn

        self._client = mcp_spawn.McpStdioClient(env_overrides=self._env_overrides)
        self._client.__enter__()
        self._client.initialize()

    def run_iteration(self, index: int) -> None:
        from src.evals.harness_bench import report

        self._client.list_tools()
        self._client.call_tool(
            "analyze_options", {"spot": 100.0, "strike": 105.0, "expiry_days": 30}
        )
        small_report = report.build_report(
            harness_id="soak_workload",
            metrics=[
                {"benchmark": "soak", "metric": "iteration", "value": float(index)}
            ],
            total_cost_usd=0.0,
            git_commit=report.current_git_commit(),
        )
        report.validate_report(small_report)

    def teardown(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def measured_pid(self) -> int | None:
        proc = getattr(self._client, "_proc", None) if self._client else None
        return proc.pid if proc is not None else None


def build_executor(kind: str) -> WorkloadExecutor:
    """CLI factory: ``mock`` for cheap runs, ``mcp`` for the real workload."""
    if kind == "mock":
        return MockWorkloadExecutor()
    return McpWorkloadExecutor(env_overrides={"VT_MEMORY_MCP_TOOLS": "1"})
