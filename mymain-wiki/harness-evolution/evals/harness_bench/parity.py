"""Parity validator shared by both harnesses under comparison.

The parity contract lives in ``parity_spec.json`` (schema:
``parity_spec.schema.json``). Every harness must, at runtime, assert that the
configuration it is about to run benchmarks with matches the spec on all
parity-critical fields. This module is that assertion hook.

CLI usage (from ``agent/``):

    # validate the spec itself (schema + internal consistency)
    python -m src.evals.harness_bench.parity --validate

    # assert a runtime config file matches the spec (names drifted fields)
    python -m src.evals.harness_bench.parity --validate runtime_config.json

Exit codes: 0 pass, 1 parity drift / invalid spec, 2 usage or load error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

PKG_DIR = Path(__file__).resolve().parent
SPEC_PATH = PKG_DIR / "parity_spec.json"
SCHEMA_PATH = PKG_DIR / "parity_spec.schema.json"

SEED_FLOOR = 3
HIGH_VARIANCE_SEED_FLOOR = 5

#: Parity-critical leaf paths compared between a runtime config and the spec.
#: A runtime config must match the spec exactly on every one of these paths;
#: anything absent from this list is harness-local and free to differ.
PARITY_PATHS: tuple[str, ...] = (
    "model.harness_model_id",
    "model.endpoint.provider",
    "model.endpoint.mode",
    "model.endpoint.base_url_env",
    "model.endpoint.base_url_default",
    "model.endpoint.api_key_env",
    "model.endpoint.model_name",
    "generation.temperature",
    "generation.top_p",
    "generation.max_output_tokens",
    "generation.max_output_tokens_field",
    "generation.thinking.enable_field",
    "generation.thinking.enable_value",
    "generation.thinking.budget_field",
    "generation.thinking.budget_value",
    "generation.thinking.transport",
    "budgets.suite_cost_cap_usd_per_harness",
    "budgets.suite_token_budget_per_harness",
    "budgets.over_budget_action",
    "subsets.swebench_verified.n",
    "subsets.swebench_verified.sampling_seed",
    "subsets.fineval.n",
    "subsets.fineval.sampling_seed",
)

#: Benchmark-level parity paths, expanded per benchmark id present in the spec.
BENCHMARK_PARITY_SUFFIXES: tuple[str, ...] = (
    "seeds",
    "high_variance",
    "cost_cap_usd_per_run",
    "metric",
)


class ParityDriftError(Exception):
    """Raised when a runtime config diverges from the parity spec."""

    def __init__(self, drifts: list[str]):
        self.drifts = list(drifts)
        super().__init__("; ".join(self.drifts))


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_schema() -> dict:
    return _load_json(SCHEMA_PATH)


def load_spec(path: Path | None = None) -> dict:
    """Load the parity spec and validate it against its jsonschema."""
    spec = _load_json(path or SPEC_PATH)
    jsonschema.validate(spec, load_schema())
    return spec


def _get_path(doc: Any, dotted: str) -> tuple[bool, Any]:
    """Resolve ``a.b.c`` inside nested dicts. Returns (found, value)."""
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _parity_paths_for(spec: dict) -> list[str]:
    paths = list(PARITY_PATHS)
    for bench_id in spec.get("benchmarks", {}):
        for suffix in BENCHMARK_PARITY_SUFFIXES:
            paths.append(f"benchmarks.{bench_id}.{suffix}")
    return paths


def check_spec_consistency(spec: dict) -> list[str]:
    """Internal-consistency rules beyond the jsonschema surface.

    Returns a list of human-readable problems (empty when consistent).
    """
    problems: list[str] = []
    benchmarks = spec.get("benchmarks", {})
    total_cap = 0.0
    for bench_id, bench in benchmarks.items():
        seeds = bench.get("seeds", 0)
        if seeds < SEED_FLOOR:
            problems.append(
                f"benchmarks.{bench_id}.seeds={seeds} is below the floor {SEED_FLOOR}"
            )
        if bench.get("high_variance") and seeds < HIGH_VARIANCE_SEED_FLOOR:
            problems.append(
                f"benchmarks.{bench_id}.seeds={seeds} is below the high-variance "
                f"floor {HIGH_VARIANCE_SEED_FLOOR}"
            )
        total_cap += float(bench.get("cost_cap_usd_per_run", 0.0))
    suite_cap = float(
        spec.get("budgets", {}).get("suite_cost_cap_usd_per_harness", 0.0)
    )
    if suite_cap < total_cap * 0.9:
        problems.append(
            f"budgets.suite_cost_cap_usd_per_harness={suite_cap} does not cover the "
            f"sum of per-benchmark caps ({total_cap})"
        )
    swe = spec.get("subsets", {}).get("swebench_verified", {})
    cap = swe.get("cap")
    if cap is not None and swe.get("n", 0) > cap:
        problems.append(f"subsets.swebench_verified.n exceeds its cap ({cap})")
    return problems


def compare_runtime_config(spec: dict, config: Any) -> list[str]:
    """Compare a runtime config against the spec on every parity path.

    Returns drift descriptions, one per drifted field (empty = parity holds).
    """
    drifts: list[str] = []
    for path in _parity_paths_for(spec):
        in_spec, spec_value = _get_path(spec, path)
        in_config, config_value = _get_path(config, path)
        if not in_spec:
            continue  # spec is the source of truth; it validated separately
        if not in_config:
            drifts.append(f"missing: runtime config lacks parity field '{path}'")
        elif config_value != spec_value:
            drifts.append(
                f"drift: '{path}' is {config_value!r} in runtime config "
                f"but {spec_value!r} in parity spec"
            )
    return drifts


def assert_runtime_config(spec: dict, config: Any) -> None:
    """Raise :class:`ParityDriftError` naming every drifted field."""
    drifts = compare_runtime_config(spec, config)
    if drifts:
        raise ParityDriftError(drifts)


def _print_drifts(drifts: list[str]) -> None:
    for drift in drifts:
        print(f"PARITY {drift}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.evals.harness_bench.parity",
        description="Validate the parity spec, or assert a runtime config matches it.",
    )
    parser.add_argument(
        "--validate",
        nargs="?",
        const="",
        default=None,
        metavar="RUNTIME_CONFIG_JSON",
        help=(
            "Without an argument: validate parity_spec.json (schema + internal "
            "consistency). With an argument: assert that runtime config file "
            "matches the spec on every parity-critical field."
        ),
    )
    parser.add_argument(
        "--spec",
        default=None,
        metavar="SPEC_JSON",
        help="Alternative spec path (defaults to the packaged parity_spec.json).",
    )
    args = parser.parse_args(argv)

    if args.validate is None:
        parser.print_help()
        return 2

    try:
        spec = load_spec(Path(args.spec) if args.spec else None)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        print(f"PARITY spec load/validation failed: {exc}", file=sys.stderr)
        return 1

    problems = check_spec_consistency(spec)
    if problems:
        for problem in problems:
            print(f"PARITY inconsistency: {problem}", file=sys.stderr)
        return 1

    if args.validate == "":
        print(f"PARITY OK: {SPEC_PATH.name} is schema-valid and internally consistent")
        return 0

    config_path = Path(args.validate)
    try:
        config = _load_json(config_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"PARITY runtime config load failed: {exc}", file=sys.stderr)
        return 2

    drifts = compare_runtime_config(spec, config)
    if drifts:
        _print_drifts(drifts)
        return 1
    print(f"PARITY OK: {config_path} matches {SPEC_PATH.name} on all parity fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
