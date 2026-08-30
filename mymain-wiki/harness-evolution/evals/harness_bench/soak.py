"""Soak rig: fixed-frequency RSS sampling over a standard workload loop.

The rig measures the long-running memory behaviour of a harness under a
documented, deterministic WORKLOAD LOOP. It is harness-agnostic: the loop is
a data definition (hashed into the artefact) and each side plugs in its own
executor (``soak_executors``) and sampler (``soak_samplers``). See
``SOAK_RIG.md`` for the boundary semantics — the baseline side uses
``docker stats`` against the opencode container (container boundary) and the
PoC side uses ``psutil`` against the Python process (process boundary). That
asymmetry is disclosed because the two are presented side by side as
reference info only, never in the decision-gate formula.

Sampling a NON-EXISTENT process/container returns a structured error object;
the rig never raises or crashes on it (todo-3 failure QA scenario).

CLI (from ``agent/``), seconds-scale smoke run::

    python -m src.evals.harness_bench.soak --label smoke \\
        --duration-hours 0.0005 --sample-interval 0.2 --executor mock
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.evals.harness_bench.soak_samplers import (
    ContainerRssSampler,
    ExecutorProcessSampler,
    ProcessRssSampler,
    RssSampler,
    Sample,
    WorkloadExecutor,
    parse_mem_usage,
)

__all__ = [
    "SOAK_VERSION",
    "DEFAULT_WORKLOAD",
    "workload_sha256",
    "run_soak",
    "validate_soak_artifact",
    "SoakArtifactError",
    "Sample",
    "RssSampler",
    "WorkloadExecutor",
    "ProcessRssSampler",
    "ContainerRssSampler",
    "ExecutorProcessSampler",
    "parse_mem_usage",
    "main",
]

SOAK_VERSION = "1.0"

#: The standard workload loop. Both harnesses run THIS exact definition so
#: their soak artefacts are comparable; the hash pins it. Representative
#: activities per iteration: a tools/list round-trip, one lightweight
#: network-free tool call, and one report generation.
DEFAULT_WORKLOAD: dict[str, Any] = {
    "name": "harness_bench_standard_workload_v1",
    "steps": [
        {"kind": "tool_list_roundtrip"},
        {
            "kind": "tool_call",
            "tool": "analyze_options",
            "arguments": {"spot": 100.0, "strike": 105.0, "expiry_days": 30},
        },
        {"kind": "report_generation"},
    ],
}


def workload_sha256(workload: dict[str, Any] | None = None) -> str:
    """Stable hash of the canonicalized workload definition."""
    definition = workload if workload is not None else DEFAULT_WORKLOAD
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _growth_mb_per_hour(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of rss_mb over t_seconds, scaled to MB/hour."""
    n = len(points)
    if n < 2:
        return None
    sum_t = sum(p[0] for p in points)
    sum_r = sum(p[1] for p in points)
    sum_tt = sum(p[0] * p[0] for p in points)
    sum_tr = sum(p[0] * p[1] for p in points)
    denominator = n * sum_tt - sum_t * sum_t
    if denominator == 0:
        return None
    slope = (n * sum_tr - sum_t * sum_r) / denominator
    return round(slope * 3600.0, 6)


def run_soak(
    label: str,
    sampler: RssSampler,
    executor: WorkloadExecutor,
    duration_hours: float,
    sample_interval_seconds: float,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the soak loop and return the (unvalidated) artefact dict."""
    definition = workload if workload is not None else DEFAULT_WORKLOAD
    duration_seconds = max(0.0, float(duration_hours)) * 3600.0
    interval = max(0.01, float(sample_interval_seconds))

    rss_timeseries: list[dict[str, Any]] = []
    sample_errors: list[dict[str, Any]] = []
    iterations = 0
    workload_errors = 0
    next_sample_at = 0.0

    # Setup (e.g. a cold MCP spawn) happens BEFORE the clock starts so harness
    # start-up cost never eats into the measurement window, and the final
    # sample is taken BEFORE teardown so the measured target still exists.
    executor.setup()
    try:
        started_at = _utc_now_iso()
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration_seconds:
                break
            if elapsed >= next_sample_at:
                sample = sampler.sample(elapsed)
                if sample.ok:
                    rss_timeseries.append(
                        {"t_seconds": round(elapsed, 3), "rss_mb": sample.rss_mb}
                    )
                else:
                    sample_errors.append(
                        {"t_seconds": round(elapsed, 3), "error": sample.error}
                    )
                next_sample_at += interval
            try:
                executor.run_iteration(iterations)
                iterations += 1
            except Exception:
                workload_errors += 1
        final_elapsed = time.monotonic() - start
        final_sample = sampler.sample(final_elapsed)
        if final_sample.ok:
            rss_timeseries.append(
                {"t_seconds": round(final_elapsed, 3), "rss_mb": final_sample.rss_mb}
            )
        else:
            sample_errors.append(
                {"t_seconds": round(final_elapsed, 3), "error": final_sample.error}
            )
    finally:
        executor.teardown()

    points = [(p["t_seconds"], p["rss_mb"]) for p in rss_timeseries]
    return {
        "soak_version": SOAK_VERSION,
        "label": label,
        "boundary": sampler.boundary,
        "sampler_target": sampler.target,
        "workload_name": definition.get("name"),
        "workload_sha256": workload_sha256(definition),
        "duration_hours_requested": float(duration_hours),
        "duration_seconds_actual": round(final_elapsed, 3),
        "sample_interval_seconds": interval,
        "started_at": started_at,
        "finished_at": _utc_now_iso(),
        "iterations_completed": iterations,
        "workload_errors": workload_errors,
        "rss_timeseries": rss_timeseries,
        "sample_errors": sample_errors,
        "growth_mb_per_hour": _growth_mb_per_hour(points),
    }


class SoakArtifactError(ValueError):
    """Raised when a soak artefact violates its schema; message names field."""


def validate_soak_artifact(artifact: Any) -> None:
    """Validate a soak artefact dict; raise ``SoakArtifactError`` naming field."""
    if not isinstance(artifact, dict):
        raise SoakArtifactError("invalid field: <root> (artefact must be an object)")
    required = {
        "soak_version": str,
        "label": str,
        "boundary": str,
        "workload_sha256": str,
        "duration_hours_requested": (int, float),
        "duration_seconds_actual": (int, float),
        "sample_interval_seconds": (int, float),
        "iterations_completed": int,
        "rss_timeseries": list,
        "sample_errors": list,
    }
    for key, kind in required.items():
        if key not in artifact:
            raise SoakArtifactError(f"invalid field: {key} (missing)")
        if not isinstance(artifact[key], kind):
            raise SoakArtifactError(f"invalid field: {key} (wrong type)")
    if artifact["boundary"] not in ("process", "container"):
        raise SoakArtifactError(f"invalid field: boundary ({artifact['boundary']!r})")
    for index, point in enumerate(artifact["rss_timeseries"]):
        if (
            not isinstance(point, dict)
            or "t_seconds" not in point
            or "rss_mb" not in point
        ):
            raise SoakArtifactError(f"invalid field: rss_timeseries[{index}]")
        if not isinstance(point["rss_mb"], (int, float)):
            raise SoakArtifactError(f"invalid field: rss_timeseries[{index}].rss_mb")
    growth = artifact.get("growth_mb_per_hour")
    if growth is not None and not isinstance(growth, (int, float)):
        raise SoakArtifactError("invalid field: growth_mb_per_hour")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="harness_bench soak rig")
    parser.add_argument("--label", default="smoke")
    parser.add_argument("--duration-hours", type=float, default=0.0005)
    parser.add_argument("--sample-interval", type=float, default=0.2)
    parser.add_argument("--executor", choices=["mock", "mcp"], default="mock")
    parser.add_argument(
        "--sampler", choices=["process", "container"], default="process"
    )
    parser.add_argument(
        "--container", default="", help="container for --sampler container"
    )
    parser.add_argument(
        "--output", default="", help="output path (default soak_<label>.json)"
    )
    args = parser.parse_args(argv)

    from src.evals.harness_bench import soak_executors

    executor = soak_executors.build_executor(args.executor)
    sampler: RssSampler = (
        ContainerRssSampler(args.container)
        if args.sampler == "container"
        else ExecutorProcessSampler(executor)
    )
    artifact = run_soak(
        label=args.label,
        sampler=sampler,
        executor=executor,
        duration_hours=args.duration_hours,
        sample_interval_seconds=args.sample_interval,
    )
    validate_soak_artifact(artifact)
    out_path = Path(args.output or f"soak_{args.label}.json")
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"soak artefact written: {out_path}")
    print(
        f"  samples={len(artifact['rss_timeseries'])} "
        f"errors={len(artifact['sample_errors'])} "
        f"iterations={artifact['iterations_completed']} "
        f"growth_mb_per_hour={artifact['growth_mb_per_hour']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
