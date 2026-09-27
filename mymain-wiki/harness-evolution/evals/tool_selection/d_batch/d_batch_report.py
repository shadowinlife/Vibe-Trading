#!/usr/bin/env python3
"""D-batch statistics: paired Level-W comparison + Level-R routing metrics.

Reads golden traces from ``../artifacts/`` and prints the numbers the
pre-registered criteria in HARNESS_EVOLUTION_D_PLAN.md §5 are judged on:

- W group: per-judge and pooled strict top-1 for subagent surface vs full
  surface on the same query set, paired exact McNemar p, and an exact 95%
  CI for the paired accuracy difference (score method on discordants).
- R group: route-hit rates overall and per expected route, target-domain
  recall, over-delegation rate, invalid-response count.

Usage:
    cd agent
    python -m src.evals.tool_selection.d_batch.d_batch_report
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
JUDGES = ["qwen3.8-max", "kimi-k3"]


def load_selection_trace(model: str, tag: str) -> dict[str, dict]:
    path = ARTIFACTS / f"llm_judge_trace_{model}_post_{tag}.jsonl"
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("header"):
            continue
        out[rec["query_id"]] = rec
    return out


def load_routing_trace(model: str, tag: str | None = "v2") -> list[dict]:
    suffix = f"_{tag}" if tag else ""
    path = ARTIFACTS / f"d_routing_trace_{model}{suffix}.jsonl"
    if not path.exists():
        path = ARTIFACTS / f"d_routing_trace_{model}.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("header"):
            out.append(rec)
    return out


def exact_ci_difference(n_a: int, total_a: int, n_b: int, total_b: int) -> tuple[float, float, float]:
    """Wilson-style CI for a difference of two proportions via Newcombe.

    Returns (point_estimate, lower, upper) of p_a - p_b.
    """
    def wilson(k: int, n: int) -> tuple[float, float]:
        if n == 0:
            return 0.0, 0.0
        z = 1.959964
        p = k / n
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return center - half, center + half

    pa = n_a / total_a
    pb = n_b / total_b
    lo_a, hi_a = wilson(n_a, total_a)
    lo_b, hi_b = wilson(n_b, total_b)
    return pa - pb, (pa - pb) - math.sqrt((pa - lo_a) ** 2 + (hi_b - pb) ** 2), \
        (pa - pb) + math.sqrt((hi_a - pa) ** 2 + (pb - lo_b) ** 2)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def within_group(domain_tag: str) -> None:
    print(f"\n=== Level-W {domain_tag} (strict top-1, paired by query) ===")
    pooled_discordant = [0, 0]  # b = within-only correct, c = full-only
    pooled_hits = {"within": 0, "full": 0, "n": 0}
    for model in JUDGES:
        within = load_selection_trace(model, f"d-{domain_tag}-within")
        full = load_selection_trace(model, f"d-{domain_tag}-full")
        ids = sorted(set(within) & set(full))
        b = c = 0
        for qid in ids:
            w = bool(within[qid].get("top1_hit"))
            f = bool(full[qid].get("top1_hit"))
            if w and not f:
                b += 1
            elif f and not w:
                c += 1
        hw = sum(1 for q in ids if within[q].get("top1_hit"))
        hf = sum(1 for q in ids if full[q].get("top1_hit"))
        pooled_discordant[0] += b
        pooled_discordant[1] += c
        pooled_hits["within"] += hw
        pooled_hits["full"] += hf
        pooled_hits["n"] += len(ids)
        diff, lo, hi = exact_ci_difference(hw, len(ids), hf, len(ids))
        print(f"  {model}: within {hw}/{len(ids)} vs full {hf}/{len(ids)} "
              f"Δ={diff:+.4f} CI95 [{lo:+.4f}, {hi:+.4f}] "
              f"McNemar b/c={b}/{c} p={mcnemar_exact(b, c):.4f}")
    diff, lo, hi = exact_ci_difference(
        pooled_hits["within"], pooled_hits["n"],
        pooled_hits["full"], pooled_hits["n"])
    b, c = pooled_discordant
    print(f"  POOLED: within {pooled_hits['within']}/{pooled_hits['n']} vs "
          f"full {pooled_hits['full']}/{pooled_hits['n']} Δ={diff:+.4f} "
          f"CI95 [{lo:+.4f}, {hi:+.4f}] McNemar b/c={b}/{c} "
          f"p={mcnemar_exact(b, c):.4f}")


def routing_group() -> None:
    print("\n=== Level-R routing ===")
    pooled = {"total": 0, "hits": 0, "invalid": 0}
    per_route: dict[str, list[int]] = {}
    over_del = [0, 0]  # delegated when direct expected, direct total
    boundary = [0, 0]
    for model in JUDGES:
        recs = load_routing_trace(model)
        hits = sum(1 for r in recs if r.get("route_hit"))
        invalid = sum(1 for r in recs if r.get("route") is None)
        pooled["total"] += len(recs)
        pooled["hits"] += hits
        pooled["invalid"] += invalid
        for r in recs:
            er = r["expected_route"]
            per_route.setdefault(er, [0, 0])
            per_route[er][1] += 1
            per_route[er][0] += int(bool(r.get("route_hit")))
            if er == "direct":
                over_del[1] += 1
                if r.get("route") in ("quant-agent", "web-docs-agent"):
                    over_del[0] += 1
            if r["query_id"].startswith("BND-"):
                boundary[1] += 1
                boundary[0] += int(bool(r.get("route_hit")))
        print(f"  {model}: {hits}/{len(recs)} hits, {invalid} invalid")
    t = pooled["total"]
    h = pooled["hits"]
    lo, hi = wilson_pair(h, t)
    print(f"  POOLED route-hit: {h}/{t} = {h/t:.4f} CI95 [{lo:.4f}, {hi:.4f}]"
          f" (invalid counted as miss: {pooled['invalid']})")
    for route, (hit, n) in sorted(per_route.items()):
        print(f"  expected={route}: recall {hit}/{n} = {hit/n:.4f}")
    od, odn = over_del
    print(f"  over-delegation (direct→subagent): {od}/{odn} = {od/odn:.4f}")
    print(f"  boundary set: {boundary[0]}/{boundary[1]}")


def wilson_pair(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    z = 1.959964
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def main() -> None:
    within_group("quant")
    within_group("webdocs")
    routing_group()


if __name__ == "__main__":
    main()
