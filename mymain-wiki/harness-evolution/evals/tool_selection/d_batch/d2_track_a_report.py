"""D2 Track A verdict: paired within-vs-full analysis over the powered re-run.

Frozen gate (HARNESS_EVOLUTION_D2_PLAN §3.1): per domain, the Newcombe
(unpaired, D-batch method) CI lower bound of (within - full) top-1 hit rate
must exceed -10pp. Pooling convention per D batch: query x judge-model pairs
(quant 120x2=240, webdocs 160x2=320). The paired Newcombe #10 lower bound is
reported alongside as the pre-registered sensitivity view (A0).

Usage: python -m src.evals.tool_selection.d_batch.d2_track_a_report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from d2_power_analysis import paired_ci_lower  # noqa: E402
from d_batch_report import exact_ci_difference, mcnemar_exact  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
MODELS = ("qwen3.8-max", "kimi-k3")
GATE_MARGIN = -0.10


def load_trace(model: str, tag: str) -> dict[str, dict]:
    path = ARTIFACTS / f"llm_judge_trace_{model}_post_{tag}.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("header"):
            out[rec["query_id"]] = rec
    return out


def domain_verdict(domain: str) -> dict:
    b = c = 0
    w_hits = f_hits = n = 0
    misses: list[str] = []
    for model in MODELS:
        within = load_trace(model, f"d2-{domain}-within")
        full = load_trace(model, f"d2-{domain}-full")
        ids = sorted(set(within) & set(full))
        for qid in ids:
            w = bool(within[qid].get("top1_hit"))
            f = bool(full[qid].get("top1_hit"))
            n += 1
            w_hits += w
            f_hits += f
            if w and not f:
                b += 1
            elif f and not w:
                c += 1
                misses.append(f"{model}:{qid}:{within[qid].get('parsed', {})}")
    delta, lo, hi = exact_ci_difference(w_hits, n, f_hits, n)
    lo_paired = paired_ci_lower(
        w_hits - b, b, c, n
    )  # both = within hits minus within-only
    return {
        "n": n,
        "within": w_hits,
        "full": f_hits,
        "delta": delta,
        "ci": (lo, hi),
        "ci_lower_paired": lo_paired,
        "mcnemar_p": mcnemar_exact(b, c),
        "b": b,
        "c": c,
        "gate_pass": lo > GATE_MARGIN,
        "misses": misses,
    }


def main() -> int:
    print("D2 Track A 裁决（冻结门禁：unpaired Newcombe CI 下界 > -10pp）\n")
    all_pass = True
    for domain in ("quant", "webdocs"):
        r = domain_verdict(domain)
        verdict = "PASS ✅" if r["gate_pass"] else "FAIL ❌"
        all_pass &= r["gate_pass"]
        print(
            f"[{domain}] N={r['n']}  within={r['within']}/{r['n']} "
            f"({r['within']/r['n']:.1%})  full={r['full']}/{r['n']} "
            f"({r['full']/r['n']:.1%})"
        )
        print(
            f"  Δ={r['delta']:+.2%}  CI95(unpaired)=[{r['ci'][0]:+.2%}, "
            f"{r['ci'][1]:+.2%}]  CI下界(paired)={r['ci_lower_paired']:+.2%}"
        )
        print(f"  McNemar b/c={r['b']}/{r['c']} p={r['mcnemar_p']:.3f}")
        print(f"  门禁: {verdict}")
        if r["misses"]:
            print(f"  within-only miss 分解（{len(r['misses'])}）:")
            for m in r["misses"][:10]:
                print(f"    {m}")
        print()
    print(
        f"总体: {'D2-1 通过，解锁 D2-2/D2-3' if all_pass else '未过——执行计划§3.1 划掉分支'}"
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
