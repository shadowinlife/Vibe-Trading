#!/usr/bin/env python3
"""Recompute every published intake baseline rate from ``results/baseline.jsonl``.

The baseline report is not allowed to contain hand-copied numbers. This
script is the single source of truth: it reads the harness output and the
frozen corpus, derives the four layers plus per-class route agreement, and
(with ``--check``) compares the result against the values stored in
``results/baseline_meta.json`` and the machine-readable block embedded in
``baseline_report.md``. Any drift exits nonzero.

Stdlib only, by design: the eval harness must run on a stock Python.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CORPUS_PATH = BASE_DIR / "corpus.jsonl"
RESULTS_PATH = BASE_DIR / "results" / "baseline.jsonl"
META_PATH = BASE_DIR / "results" / "baseline_meta.json"
REPORT_PATH = BASE_DIR / "baseline_report.md"

LAYERS = ("route_class", "intake_loaded", "question_called", "params_compiled")
ROUTE_LABELS = ("execute_with_defaults", "clarify_private", "direct_answer", "refuse")
RUN_ERROR = "run_error"
Z_95 = statistics.NormalDist().inv_cdf(0.975)
REPORT_BLOCK = re.compile(
    r"<!-- recompute:begin -->\s*```json\s*(\{.*?\})\s*```\s*<!-- recompute:end -->",
    re.DOTALL,
)


def wilson(k: int, n: int) -> dict[str, float] | None:
    """Wilson score interval for k successes out of n, 95% two-sided."""
    if n <= 0:
        return None
    p = k / n
    denominator = 1.0 + Z_95 * Z_95 / n
    center = (p + Z_95 * Z_95 / (2 * n)) / denominator
    half = Z_95 * ((p * (1 - p) / n) + (Z_95 * Z_95 / (4 * n * n))) ** 0.5 / denominator
    return {
        "low": round(max(0.0, center - half), 6),
        "high": round(min(1.0, center + half), 6),
    }


def rate(k: int, n: int) -> float | None:
    return round(k / n, 6) if n else None


def load_corpus() -> dict[str, dict[str, Any]]:
    corpus: dict[str, dict[str, Any]] = {}
    for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            corpus[item["id"]] = item
    return corpus


def load_runs() -> dict[str, dict[str, Any]]:
    """Group JSONL records into one run per corpus item.

    Raises ValueError when an item has no run, more than one run, or more
    than one distinct ``run_id``. The baseline is a single pass of the
    frozen corpus; ambiguity here would make every rate unreproducible.
    """
    runs: dict[str, dict[str, Any]] = {}
    for lineno, line in enumerate(RESULTS_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        corpus_id, run_id = record["corpus_id"], record["run_id"]
        run = runs.setdefault(corpus_id, {"run_id": run_id, "records": []})
        if run["run_id"] != run_id:
            raise ValueError(
                f"{RESULTS_PATH}:{lineno}: {corpus_id} carries two run ids "
                f"({run['run_id']}, {run_id}); the baseline must be one pass"
            )
        run["records"].append(record)
    return runs


def _single(records: list[dict[str, Any]], key: str) -> Any:
    values = {json.dumps(record.get(key), sort_keys=True) for record in records}
    if len(values) > 1:
        raise ValueError(f"records disagree on {key}: {sorted(values)}")
    return records[0].get(key)


def compute() -> dict[str, Any]:
    corpus = load_corpus()
    runs = load_runs()
    if set(runs) != set(corpus):
        missing = sorted(set(corpus) - set(runs))
        extra = sorted(set(runs) - set(corpus))
        raise ValueError(f"corpus/result mismatch: missing={missing} extra={extra}")

    per_item: dict[str, dict[str, Any]] = {}
    layer_records: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
    run_errors: list[str] = []

    for corpus_id in corpus:
        run = runs[corpus_id]
        records = run["records"]
        by_event = {record["event"]: record for record in records}
        if len(by_event) != len(records):
            raise ValueError(f"{corpus_id}: duplicate event records")
        if RUN_ERROR in by_event:
            run_errors.append(corpus_id)
            terminal_event = RUN_ERROR
        else:
            missing = [layer for layer in LAYERS if layer not in by_event]
            if missing:
                raise ValueError(f"{corpus_id}: incomplete layer set, missing {missing}")
            terminal_event = records[-1]["event"]
            for layer in LAYERS:
                layer_records[layer].append(by_event[layer])
        wall_s = _single(records, "wall_s")
        tokens = _single(records, "tokens")
        per_item[corpus_id] = {
            "event_terminal": terminal_event,
            "wall_s": wall_s,
            "tokens": tokens,
            "route_observed": by_event.get("route_class", {}).get("observed"),
            "route_expected": corpus[corpus_id]["expect"],
        }

    layers: dict[str, dict[str, Any]] = {}
    route = layer_records["route_class"]
    route_n = len(route)
    route_k = sum(1 for record in route if record["observed"] == record["expected"])
    layers["route_class"] = {
        "kind": "agreement",
        "n": route_n,
        "agreement": route_k,
        "observed_rate": rate(route_k, route_n),
        "expected_rate": 1.0 if route_n else None,
        "wilson95": wilson(route_k, route_n),
    }
    for layer in LAYERS[1:]:
        records = layer_records[layer]
        n = len(records)
        observed_k = sum(1 for record in records if record["observed"] is True)
        expected_k = sum(1 for record in records if record["expected"] is True)
        agreement = sum(1 for record in records if record["observed"] == record["expected"])
        layers[layer] = {
            "kind": "binary_positive",
            "n": n,
            "observed_k": observed_k,
            "observed_rate": rate(observed_k, n),
            "expected_k": expected_k,
            "expected_rate": rate(expected_k, n),
            "agreement": agreement,
            "wilson95": wilson(observed_k, n),
        }

    route_by_class: dict[str, dict[str, Any]] = {}
    for label in ROUTE_LABELS:
        records = [record for record in route if record["expected"] == label]
        n = len(records)
        k = sum(1 for record in records if record["observed"] == record["expected"])
        route_by_class[label] = {
            "n": n,
            "agreement": k,
            "rate": rate(k, n),
            "wilson95": wilson(k, n),
        }

    token_values = [item["tokens"] for item in per_item.values() if item["tokens"] is not None]
    wall_values = [item["wall_s"] for item in per_item.values() if item["wall_s"] is not None]
    return {
        "n_items": len(per_item),
        "n_terminal": len(per_item),
        "n_event_complete": sum(
            1 for item in per_item.values() if item["event_terminal"] != RUN_ERROR
        ),
        "n_run_error": len(run_errors),
        "run_error_ids": run_errors,
        "operational": {
            "n": len(per_item),
            "terminal": len(per_item),
            "rate": rate(len(per_item), len(per_item)),
            "wilson95": wilson(len(per_item), len(per_item)),
        },
        "layers": layers,
        "route_by_class": route_by_class,
        "tokens_total": sum(token_values) if token_values else None,
        "wall_s_total": round(sum(wall_values), 3) if wall_values else None,
        "per_item": per_item,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def print_human(metrics: dict[str, Any]) -> None:
    print(
        f"items={metrics['n_items']} terminal={metrics['n_terminal']}/{metrics['n_items']} "
        f"event_complete={metrics['n_event_complete']} run_error={metrics['n_run_error']}"
    )
    operational = metrics["operational"]
    print(
        f"{'operational':16s} n={operational['n']:2d} terminal={operational['terminal']:2d} "
        f"rate={_pct(operational['rate'])}"
    )
    for layer in LAYERS:
        row = metrics["layers"][layer]
        interval = row["wilson95"]
        ci = f"[{_pct(interval['low'])}, {_pct(interval['high'])}]" if interval else "n/a"
        print(
            f"{layer:16s} n={row['n']:2d} observed={_pct(row['observed_rate']):>6s} "
            f"expected={_pct(row['expected_rate']):>6s} "
            f"agreement={row['agreement']:2d}/{row['n']:<2d} wilson95={ci}"
        )
    print("route agreement by expected class:")
    for label in ROUTE_LABELS:
        row = metrics["route_by_class"][label]
        print(
            f"  {label:24s} {row['agreement']}/{row['n']} = {_pct(row['rate'])}"
        )
    print("per item:")
    for corpus_id, item in metrics["per_item"].items():
        print(
            f"  {corpus_id} {item['event_terminal']:16s} wall_s={item['wall_s']} "
            f"tokens={item['tokens']} route={item['route_observed']}"
        )
    if metrics["tokens_total"] is None:
        print("tokens_total=null (the run stream carried no usage block)")
    else:
        print(f"tokens_total={metrics['tokens_total']}")


def _diff(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float)) and isinstance(actual, (int, float))
    ):
        return [f"{path}: type {type(expected).__name__} != {type(actual).__name__}"]
    if isinstance(expected, dict):
        problems: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                problems.append(f"{path}.{key}: extra in recomputed values")
            elif key not in actual:
                problems.append(f"{path}.{key}: missing from published values")
            else:
                problems.extend(_diff(expected[key], actual[key], f"{path}.{key}"))
        return problems
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(expected)} != {len(actual)}"]
        problems = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            problems.extend(_diff(left, right, f"{path}[{index}]"))
        return problems
    if expected != actual:
        return [f"{path}: published={expected!r} recomputed={actual!r}"]
    return []


def _published_from_meta() -> Any:
    if not META_PATH.is_file():
        raise SystemExit(f"--check: missing {META_PATH}")
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    if "published" not in meta:
        raise SystemExit(f"--check: {META_PATH} has no 'published' block")
    return meta["published"]


def _published_from_report() -> Any:
    if not REPORT_PATH.is_file():
        raise SystemExit(f"--check: missing {REPORT_PATH}")
    match = REPORT_BLOCK.search(REPORT_PATH.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"--check: {REPORT_PATH} has no recompute block")
    return json.loads(match.group(1))


def check(metrics: dict[str, Any]) -> int:
    failures = 0
    for label, published in (
        ("baseline_meta.json", _published_from_meta()),
        ("baseline_report.md", _published_from_report()),
    ):
        problems = _diff(published, metrics)
        if problems:
            failures += len(problems)
            print(f"FAIL {label}: {len(problems)} mismatch(es)", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
        else:
            print(f"PASS {label}: published rates match baseline.jsonl")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the metric object as JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against baseline_meta.json and baseline_report.md (0 tolerance)",
    )
    args = parser.parse_args(argv)
    try:
        metrics = compute()
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"recompute: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    elif not args.check:
        print_human(metrics)
    if args.check:
        return check(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
