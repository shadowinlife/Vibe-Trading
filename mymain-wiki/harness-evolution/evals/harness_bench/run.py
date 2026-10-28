"""Benchmark run CLI for harness_bench (shared by todos 4-8).

Usage (from ``agent/``):

    python -m src.evals.harness_bench.run --benchmark tau2 --tasks 1 \
        --harness mock [--seed N] [--report-out PATH] [--task-timeout S]

Flow: build adapter per harness -> setup with the parity-validated config ->
run_task x N across the parity seed list -> teardown -> assemble report
(metrics rows + total-cost row + skip_markers + provenance) -> validate ->
write JSON under ``artifacts/runs/`` (or ``--report-out``).

Bridge-preflight gate (todo 4): before ANY opencode-harness batch the bridge
runs a FRESH ``OpenCodeBridge.preflight()``. PASS -> the batch runs; FAIL ->
the batch is never started, ``artifacts/bridge_preflight_hil_package.json``
is written, and the report carries one skip marker whose reason includes
``awaiting-hil-decision``. A failed preflight can therefore never produce a
pass-marked opencode smoke report.

Benchmark registry: adding a benchmark = one import + one dict entry.

Exit-code contract: 0 = completed (incl. degraded-with-disclosure and
HIL-blocked runs), 1 = parity drift / report validation failure,
2 = usage error (unknown benchmark, bad --tasks value; argparse also exits 2
on bad flags). Any unavailable benchmark/harness combo degrades to a
skip_marker, never a crash.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from src.evals.harness_bench import parity, report
from src.evals.harness_bench.adapter import HarnessAdapter, MockAdapter, TaskResult
from src.evals.harness_bench.adapters.finance_qa_adapter import (
    FinanceBenchAdapter,
    FinEvalAdapter,
)
from src.evals.harness_bench.adapters.swe_terminal_adapter import (
    SWEbenchVerifiedAdapter,
    TerminalBenchAdapter,
)
from src.evals.harness_bench.adapters.tau2_adapter import (
    Tau2Adapter,
    Tau2DataUnavailable,
)
from src.evals.harness_bench.adapters.backtestbench_adapter import (
    BacktestBenchAdapter,
)

PKG_DIR = Path(__file__).resolve().parent
RUNS_DIR = PKG_DIR / "artifacts" / "runs"
HIL_PACKAGE_PATH = PKG_DIR / "artifacts" / "bridge_preflight_hil_package.json"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2

HARNESS_CHOICES = ("opencode", "mock", "pydantic")
DEFAULT_TASK_TIMEOUT_S = 900


def build_bridge(env: dict) -> Any:
    """Bridge factory (test seam: tests monkeypatch this)."""
    from src.evals.harness_bench import opencode_bridge

    spec = parity.load_spec()
    config = opencode_bridge.BridgeConfig.from_env(
        env, model_id=spec["model"]["harness_model_id"]
    )
    return opencode_bridge.OpenCodeBridge(config)


def _tau2_factory(harness: str) -> HarnessAdapter:
    bridge = build_bridge(dict(os.environ)) if harness == "opencode" else None
    return Tau2Adapter(harness, bridge=bridge)


def _mock_factory(harness: str) -> HarnessAdapter:
    adapter = MockAdapter()
    return adapter


#: name -> adapter factory(harness). Adding a benchmark = one import + one
#: entry here; nothing else in this module changes.
BENCHMARK_REGISTRY: dict[str, Callable[[str], HarnessAdapter]] = {
    "tau2": _tau2_factory,
    "mock": _mock_factory,
    "financebench": lambda harness: FinanceBenchAdapter(
        harness,
        bridge=build_bridge(dict(os.environ)) if harness == "opencode" else None,
    ),
    "fineval": lambda harness: FinEvalAdapter(
        harness,
        bridge=build_bridge(dict(os.environ)) if harness == "opencode" else None,
    ),
    "swebench_verified": lambda harness: SWEbenchVerifiedAdapter(
        harness,
        bridge=build_bridge(dict(os.environ)) if harness == "opencode" else None,
    ),
    "terminal-bench": lambda harness: TerminalBenchAdapter(
        harness,
        bridge=build_bridge(dict(os.environ)) if harness == "opencode" else None,
    ),
    "backtestbench": lambda harness: BacktestBenchAdapter(
        harness,
        bridge=build_bridge(dict(os.environ)) if harness == "opencode" else None,
    ),
}


def _synthetic_tasks(benchmark: str, n: int) -> list[dict[str, Any]]:
    return [{"task_id": f"{benchmark}-t{i}", "benchmark": benchmark} for i in range(n)]


def assemble_hil_package(bridge: Any, probe: Any, recommendation: str) -> dict:
    """HIL evidence package for the operator fix-vs-degrade decision."""
    phases_401 = [
        {"phase": name, "detail": str(info.get("detail", ""))[:500]}
        for name, info in probe.phases.items()
        if "401" in str(info.get("detail", ""))
        or "missing_api_key" in str(info.get("detail", ""))
    ]
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "gate": "bridge_preflight",
        "preflight_outcome": "FAIL",
        "probe_transcript": {
            "ok": probe.ok,
            "phases": probe.phases,
            "session_id": probe.session_id,
            "tool_names": probe.tool_names,
            "elapsed_seconds": probe.elapsed_seconds,
            "error": probe.error.as_dict() if probe.error else None,
        },
        "container_image_facts": bridge.hil_facts(),
        "evidence_401": {
            "observed": bool(phases_401),
            "phases_with_401": phases_401,
            "note": (
                "baseline provider intermittently returns HTTP 401 "
                "missing_api_key mid-session (todo-2 finding; re-checked by "
                "this fresh preflight)"
            ),
        },
        "remediation_options": [
            {
                "id": "fix-bridge",
                "summary": (
                    "rebuild the opencode-serve image from the current worktree "
                    "config (pins qwen3.8-max), fix the provider credential, "
                    "restore VT MCP tool visibility in the session"
                ),
            },
            {
                "id": "degrade",
                "summary": (
                    "baseline side missing: record 'baseline missing, PoC "
                    "single-side measurement' and disclose at the decision gate"
                ),
            },
        ],
        "recommendation": recommendation,
    }


def _recommendation(probe: Any, bridge: Any) -> str:
    facts = bridge.image_facts or {}
    mismatch = facts.get("model_config_matches_parity") is False
    detail = (
        f"probe error={probe.error.as_dict() if probe.error else 'unknown'}; "
        f"image model config matches parity={facts.get('model_config_matches_parity')}"
    )
    return (
        "fix-bridge: the deployment is drivable in principle (todo-2 recorded a "
        "full round-trip pass) but intermittently fails with provider 401 and "
        f"runs an image predating the parity model pin (mismatch={mismatch}; "
        f"{detail}). Rebuild the image from this worktree's config, fix the "
        "DashScope credential wiring, restore VT MCP visibility, then re-run "
        "this gate. Degrade only if the credential cannot be restored."
    )


def _gate_opencode(adapter: Any, benchmark: str) -> bool:
    """Fresh bridge preflight. True -> batch may run; False -> skip-marked."""
    probe = adapter.bridge.preflight()
    if probe.ok:
        return True
    package = assemble_hil_package(
        adapter.bridge, probe, _recommendation(probe, adapter.bridge)
    )
    HIL_PACKAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HIL_PACKAGE_PATH.write_text(json.dumps(package, indent=2), encoding="utf-8")
    error = probe.error.as_dict() if probe.error else {"kind": "unknown", "detail": ""}
    adapter.add_skip_marker(
        benchmark,
        (
            "bridge_preflight_failed: awaiting-hil-decision "
            f"(error={error['kind']}: {error['detail'][:200]}); "
            f"hil_package={HIL_PACKAGE_PATH.name}; batch never started"
        ),
        "excluded_from_adjudication",
    )
    return False


def run(args: argparse.Namespace, env: dict) -> int:
    if args.benchmark not in BENCHMARK_REGISTRY:
        known = ", ".join(sorted(BENCHMARK_REGISTRY))
        print(
            f"error: unknown benchmark {args.benchmark!r} (known: {known})",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.tasks < 1:
        print("error: --tasks must be >= 1", file=sys.stderr)
        return EXIT_USAGE

    spec = parity.load_spec()
    parity.assert_runtime_config(spec, spec)
    bench_spec = spec.get("benchmarks", {}).get(args.benchmark, {})
    seed_count = int(bench_spec.get("seeds", 3))
    seed_list = [args.seed] if args.seed is not None else list(range(1, seed_count + 1))
    cost_cap = float(
        bench_spec.get(
            "cost_cap_usd_per_run",
            spec["budgets"]["suite_cost_cap_usd_per_harness"],
        )
    )

    adapter = BENCHMARK_REGISTRY[args.benchmark](args.harness)
    adapter.setup(
        {
            "harness": args.harness,
            "benchmark": args.benchmark,
            "seed_list": seed_list,
            "cost_cap_usd": cost_cap,
            "task_timeout_s": args.task_timeout,
            "tau2": bench_spec,
        }
    )
    try:
        if args.harness == "opencode" and getattr(adapter, "bridge", None) is not None:
            if not _gate_opencode(adapter, args.benchmark):
                return _finish(adapter, args)
        return _run_task_loop(adapter, args, seed_list, cost_cap)
    finally:
        adapter.teardown()


def _run_task_loop(
    adapter: Any, args: argparse.Namespace, seed_list: list[int], cost_cap: float
) -> int:
    try:
        specs = (
            adapter.prepare_tasks(args.tasks)
            if hasattr(adapter, "prepare_tasks")
            else _synthetic_tasks(args.benchmark, args.tasks)
        )
    except Tau2DataUnavailable as exc:
        adapter.add_skip_marker(
            args.benchmark,
            f"tau2-data-unavailable: {exc}",
            "excluded_from_adjudication",
        )
        return _finish(adapter, args)

    total_cost = 0.0
    aborted = False
    for spec in specs:
        for seed in seed_list:
            if total_cost >= cost_cap:
                if hasattr(adapter, "add_skip_marker"):
                    adapter.add_skip_marker(
                        args.benchmark,
                        (
                            f"cost_cap_exceeded: {total_cost:.2f} USD >= cap "
                            f"{cost_cap:.2f} USD; degraded per parity spec "
                            "over_budget_action=degrade-and-disclose"
                        ),
                        "excluded_from_adjudication",
                    )
                aborted = True
                break
            try:
                result = adapter.run_task({**spec, "seed": seed})
            except NotImplementedError as exc:
                adapter.add_skip_marker(
                    args.benchmark, str(exc), "excluded_from_adjudication"
                )
                aborted = True
                break
            except Exception as exc:  # noqa: BLE001 - per-task errors degrade
                result = TaskResult(
                    task_id=str(spec.get("task_id", "?")),
                    benchmark=args.benchmark,
                    status="error",
                    details={"exception": str(exc)[:300]},
                )
            total_cost += result.cost_usd
        if aborted:
            break
    return _finish(adapter, args)


def _finish(adapter: Any, args: argparse.Namespace) -> int:
    try:
        report_dict = adapter.report()
        report.validate_report(report_dict)
    except Exception as exc:  # noqa: BLE001 - validation failure = exit 1
        print(f"error: report validation failed: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    out_path = args.report_out or (
        RUNS_DIR
        / (
            f"{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%SZ}"
            f"_{args.benchmark}_{args.harness}.json"
        )
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    skips = len(report_dict["skip_markers"])
    print(
        f"run: benchmark={args.benchmark} harness={args.harness} "
        f"metrics_rows={len(report_dict['metrics'])} skip_markers={skips} "
        f"total_cost_usd={report_dict['total_cost']['value']} report={out_path}"
    )
    return EXIT_OK


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.evals.harness_bench.run",
        description="Run one harness_bench benchmark under one harness.",
    )
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--tasks", required=True, type=int)
    parser.add_argument("--harness", required=True, choices=HARNESS_CHOICES)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="debug override: run a single seed instead of the parity seed list",
    )
    parser.add_argument("--report-out", type=Path, default=None)
    parser.add_argument("--task-timeout", type=int, default=DEFAULT_TASK_TIMEOUT_S)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args, dict(os.environ))
    except parity.ParityDriftError as exc:
        print(f"error: parity drift: {exc}", file=sys.stderr)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
