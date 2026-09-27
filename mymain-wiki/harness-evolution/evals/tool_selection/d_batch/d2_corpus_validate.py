"""D2-A2 corpus validator: deterministic gates for the twin-arbitration rebuild.

Validates the merged D2 query files (existing D-batch entries + new D2Q/D2W
blocks) against the frozen construction rules from HARNESS_EVOLUTION_D2_PLAN.md
§3.1 A2: schema, whitelist membership, twin-pair side balance, dedup, and the
D19 disambiguation-marker screen (the label-confound repair mechanism).

Exit 0 iff all gates pass. Prints a per-gate report; violations are listed
with query ids for the revision pass.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

D_BATCH = Path(__file__).resolve().parent

WHITELISTS = {
    "quant": {
        "tools": {
            "alpha_zoo",
            "alpha_bench",
            "factor_analysis",
            "list_strategies",
            "query_strategies",
            "get_strategy_evidence",
            "backtest",
            "write_file",
            "read_file",
            "pattern_recognition",
            "quantlib_call",
        },
        "skills": {
            "strategy-generate",
            "factor-research",
            "multi-factor",
            "strategy-discovery",
            "strategy-dev-manager",
            "ml-strategy",
            "backtest-diagnose",
            "execution-model",
            "cross-market-strategy",
            "alpha-zoo",
            "pine-script",
            "vnpy-export",
        },
    },
    "webdocs": {
        "tools": {"web_search", "read_url", "read_document"},
        "skills": {"web-reader", "doc-reader"},
    },
}

HOWTO_MARKERS = (
    "怎么",
    "如何",
    "怎么办",
    "教我",
    "有没有办法",
    "为什么读不了",
    "有没有能",
    "how do i",
    "how to",
    "how can i",
    "what's the way",
    "which tool",
    "什么工具",
    "什么流程",
    "方法论",
    # teach-me family (skill = teaches): 讲讲/explain/walk through/guide through
    "讲讲",
    "给我讲讲",
    "explain",
    "teach me",
    "walk me through",
    "guide me through",
    # methodology family
    "methodology",
    "研究框架",
    "怎么设计",
    "怎么实现",
    "怎么构建",
    "how does",
    "how do i structure",
    "what features should",
    # design-demand family (strategy-generate convention)
    "帮我设计",
    "帮我构建",
    "help me design",
    "help me build",
    # diagnose family (backtest-diagnose convention, cf. D07-004: 诊断→skill)
    "诊断",
    "帮我分析",
    "查查原因",
    "哪里出了问题",
    "什么问题",
    "root cause",
    "diagnose",
)

# Reviewed-and-accepted heuristic flags (2026-08-29, human review):
# D2W-070 — "methodology" is the section to extract, not a how-to demand;
# D2W-120 — substring collision ("explainers" ⊃ "explain") on a search demand.
REVIEWED_OK = frozenset({"D2W-070", "D2W-120"})


def norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def load_entries(path: Path) -> list[dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(doc["entries"])


def check_domain(
    name: str, existing: list[dict], new: list[dict], domain: str
) -> list[str]:
    """Run all gates for one domain; return a list of violation strings."""
    problems: list[str] = []
    wl = WHITELISTS[domain]
    valid = wl["tools"] | wl["skills"]

    seen_ids: set[str] = set()
    seen_queries: dict[str, str] = {}
    for e in existing + new:
        if e["id"] in seen_ids:
            problems.append(f"{e['id']}: duplicate id")
        seen_ids.add(e["id"])
        nq = norm(e["query"])
        if nq in seen_queries:
            problems.append(f"{e['id']}: near-duplicate of {seen_queries[nq]}")
        seen_queries[nq] = e["id"]

    for e in new:
        qid = e["id"]
        exp = e.get("expected", {})
        if exp.get("name") not in valid:
            problems.append(f"{qid}: expected.name {exp.get('name')} not in whitelist")
            continue
        if (exp.get("kind") == "tool") != (exp["name"] in wl["tools"]):
            problems.append(f"{qid}: kind/name mismatch ({exp})")
        if not e.get("twin_pair") or not e.get("twin_side"):
            problems.append(f"{qid}: missing twin_pair/twin_side")
        negs = e.get("negatives") or []
        if not negs:
            problems.append(f"{qid}: negatives empty")

        text = e["query"].lower()
        has_howto = any(m in text for m in HOWTO_MARKERS)
        if exp.get("kind") == "skill" and not has_howto:
            problems.append(f"{qid}: skill-side without how-to marker")
        if exp.get("kind") == "tool" and has_howto and "怎" not in text[:4]:
            # tool-side with a how-to marker mid-sentence is allowed only when
            # the sentence opens with an execution demand (e.g. 先教我X再跑Y is
            # rejected; 把X跑一下 despite 怎么 in a clause passes review).
            if qid not in REVIEWED_OK:
                problems.append(f"{qid}: tool-side with how-to marker (review)")

    pair_side: dict[str, dict[str, int]] = {}
    for e in new:
        # Group by pair FAMILY (the skill side): a designed pair may span
        # several sibling tools (e.g. the strategy catalogue trio), and the
        # balance requirement applies at family level.
        pair = str(e.get("twin_pair", "?")).split("×")[-1]
        side = e.get("twin_side", "?")
        pair_side.setdefault(pair, {"tool": 0, "skill": 0})
        if side in pair_side[pair]:
            pair_side[pair][side] += 1
    for pair, counts in sorted(pair_side.items()):
        t, s = counts["tool"], counts["skill"]
        if t + s == 0:
            continue
        skew = abs(t - s) / (t + s)
        if skew > 0.25:
            problems.append(f"{name}:{pair}: side skew {t}t/{s}s (>{0.25:.0%})")

    print(f"[{name}] entries: existing={len(existing)} new={len(new)}")
    for pair, counts in sorted(pair_side.items()):
        print(f"  {pair}: tool={counts['tool']} skill={counts['skill']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--d-batch-dir", type=Path, default=D_BATCH)
    args = parser.parse_args()
    d = args.d_batch_dir

    all_problems: list[str] = []
    all_problems += check_domain(
        "quant",
        load_entries(d / "queries_d_quant.yaml"),
        load_entries(d / "queries_d2_quant.yaml"),
        "quant",
    )
    all_problems += check_domain(
        "webdocs",
        load_entries(d / "queries_d_webdocs.yaml"),
        load_entries(d / "queries_d2_webdocs.yaml"),
        "webdocs",
    )

    if all_problems:
        print(f"\n{len(all_problems)} violation(s):")
        for p in all_problems:
            print(f"  - {p}")
        return 1
    print("\nall gates pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
