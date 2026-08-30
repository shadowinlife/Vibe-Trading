"""Benchmark adapters for harness_bench.

Each adapter implements ``HarnessAdapter`` (see ``adapter.py``) for one
benchmark. Adding a benchmark = one module here + one registry entry in
``run.py``.
"""

from __future__ import annotations

from src.evals.harness_bench.adapters.tau2_adapter import Tau2Adapter

__all__ = ["Tau2Adapter"]
