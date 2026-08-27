"""Test-retest noise floor for the LLM-judge evaluation (B-batch gap ④).

Closes methodology gap ④ of ``artifacts/llm_judge_design.md`` ("Known
methodology gaps", 2026-08-27 review) as pre-registered in
``HARNESS_EVOLUTION_B_TEST_PLAN.md`` §5.2: before the B-batch matrix, run a
test-retest probe — the SAME corpus, SAME model, TWO independent probe
administrations — and report the first-pick agreement ``ρ``. Any pooled
|Δtop-1| within ``max(1 − ρ)`` across models is judge run-to-run noise and
must be read as uninterpretable (feed it to ``b_batch_stats --noise-band``).

The two administrations are recorded with ``run_llm_judge --probe-only
--probe-tag <tag>`` so each lands in its own JSONL and resume logic can
never clobber one with the other. This module compares the two files:

* per query, each administration is summarized by its representative first
  pick — the majority (modal) first pick across that administration's
  repeats, ties broken by earliest repeat, so an invalid (unparseable)
  reply participates as ``None`` exactly like any other pick;
* ``ρ`` = fraction of common queries whose two representatives agree,
  reported overall and per query.

Deterministic given the two probe files: no network, no LLM calls.

Usage:
    cd agent
    python -m src.evals.tool_selection.retest_noise \\
        --model qwen3.8-max --surface post --tag-a run1 --tag-b run2
    python -m src.evals.tool_selection.retest_noise \\
        --probe-a path/admin1.jsonl --probe-b path/admin2.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.evals.tool_selection.run_llm_judge import ARTIFACTS_DIR, load_trace


def probe_path_for(artifacts_dir: Path, model_id: str, surface: str, tag: str | None = None) -> Path:
    """Return the probe-record path for one (model, surface, tag) probe run.

    Mirrors ``run_llm_judge``'s probe naming, including the ``--probe-tag``
    suffix used to keep two independent administrations apart.

    Args:
        artifacts_dir: Artifact directory.
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.
        tag: Optional probe tag (``run_llm_judge --probe-tag``).

    Returns:
        The probe JSONL path.
    """
    suffix = f"_{tag}" if tag else ""
    return artifacts_dir / f"llm_judge_probe_{model_id}_{surface}{suffix}.jsonl"


def load_probe_lines(path: Path) -> list[dict]:
    """Read one probe JSONL file, tolerating blank or corrupted lines.

    Args:
        path: Probe file; a missing file yields an empty list.

    Returns:
        Per-call probe records (probe files carry no header line).
    """
    _header, lines = load_trace(path)
    return lines


def representative_firsts(lines: list[dict]) -> dict[str, str | None]:
    """Summarize one administration: representative first pick per query.

    The representative is the modal first pick across the administration's
    repeats; ties break toward the pick seen at the earliest repeat, which
    keeps the summary deterministic. An unparseable reply contributes
    ``None`` as its pick, exactly like the within-run probe agreement.

    Args:
        lines: Probe records of one administration (any repeat count).

    Returns:
        query_id -> representative first pick (None when the modal pick is
        an invalid response).
    """
    by_query: dict[str, list[str | None]] = {}
    for record in sorted(lines, key=lambda r: (r.get("query_id", ""), r.get("repeat", 0))):
        parsed = record.get("parsed")
        pick = parsed.get("first") if isinstance(parsed, dict) else None
        by_query.setdefault(record.get("query_id", ""), []).append(pick)
    representatives: dict[str, str | None] = {}
    for query_id, picks in by_query.items():
        counts: dict[str | None, int] = {}
        for pick in picks:
            counts[pick] = counts.get(pick, 0) + 1
        best = max(counts.values())
        representatives[query_id] = next(p for p in picks if counts[p] == best)
    return representatives


def compare_probes(lines_a: list[dict], lines_b: list[dict]) -> dict:
    """First-pick agreement between two administrations of the same probe.

    Args:
        lines_a: Probe records of administration A.
        lines_b: Probe records of administration B.

    Returns:
        Dict with ``rho`` (overall agreement, None without common queries),
        ``n_queries`` (common query count) and ``per_query`` rows
        {query_id: {first_a, first_b, agree}}.
    """
    firsts_a = representative_firsts(lines_a)
    firsts_b = representative_firsts(lines_b)
    common = sorted(set(firsts_a) & set(firsts_b))
    per_query = {
        query_id: {
            "first_a": firsts_a[query_id],
            "first_b": firsts_b[query_id],
            "agree": firsts_a[query_id] == firsts_b[query_id],
        }
        for query_id in common
    }
    agreed = sum(1 for row in per_query.values() if row["agree"])
    return {
        "rho": agreed / len(common) if common else None,
        "n_queries": len(common),
        "per_query": per_query,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: compare two probe administrations, print ρ.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok, 2 configuration error).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probe-a", default=None, help="explicit path of administration A")
    parser.add_argument("--probe-b", default=None, help="explicit path of administration B")
    parser.add_argument("--model", default=None, help="judge model id (with --surface/--tag-*)")
    parser.add_argument("--surface", default="post", choices=("baseline", "post"))
    parser.add_argument("--tag-a", default=None, help="probe tag of administration A")
    parser.add_argument("--tag-b", default=None, help="probe tag of administration B")
    parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="artifact directory (default: the suite's own artifacts/)",
    )
    parser.add_argument("--json-out", default=None, help="also write the result JSON here")
    args = parser.parse_args(argv)

    if args.probe_a and args.probe_b:
        path_a, path_b = Path(args.probe_a), Path(args.probe_b)
    elif args.model:
        artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else ARTIFACTS_DIR
        path_a = probe_path_for(artifacts_dir, args.model, args.surface, args.tag_a)
        path_b = probe_path_for(artifacts_dir, args.model, args.surface, args.tag_b)
    else:
        print(
            "error: pass --probe-a/--probe-b or --model with --tag-a/--tag-b",
            file=sys.stderr,
        )
        return 2
    for path in (path_a, path_b):
        if not path.exists():
            print(f"error: probe file not found: {path}", file=sys.stderr)
            return 2

    result = compare_probes(load_probe_lines(path_a), load_probe_lines(path_b))
    result["probe_a"] = str(path_a)
    result["probe_b"] = str(path_b)
    rho = result["rho"]
    print(
        f"test-retest agreement over {result['n_queries']} common queries: "
        f"rho = {'n/a' if rho is None else f'{rho:.4f}'}"
        + (f"  -> suggested noise band >= {1.0 - rho:.4f}" if rho is not None else "")
    )
    for query_id, row in result["per_query"].items():
        if not row["agree"]:
            print(f"  disagree {query_id}: {row['first_a']} vs {row['first_b']}")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"result JSON written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
