#!/usr/bin/env python3
"""D4 v3 corpus validator: trading-connector mini-admission block (DEC-5).

Validates block C (queries_d4_routing_c.yaml) on its own and merged with the
frozen v2 corpus: ids unique across the merge, routes valid against the
12-candidate roster (9 admitted + 2 pilots + trading-connector), expected
names inside the owning candidate's whitelist, boundary_with siblings real,
no near-duplicate queries against v2. The validator is a sieve, not a
labeler — it reports, a human adjudicates.

Usage: python -m src.evals.tool_selection.d4_batch.d4_corpus_validate_v3
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
D_BATCH = HERE.parent / "d_batch"

CONTROL_ROUTES = {"direct", "main"}


def norm(text: str) -> str:
    return re.sub(r"\W+", "", str(text).lower())


def main() -> int:
    cands = {}
    for p in sorted(HERE.glob("subagent_*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        cands[d["name"]] = set(d["tools"])
    admitted = cands  # v3: trading-connector-agent joins; orchestrator stays out
    admitted.pop("orchestrator", None)
    valid_routes = CONTROL_ROUTES | set(admitted)

    v2 = yaml.safe_load((HERE / "queries_d4_routing_all_v2.yaml").read_text(encoding="utf-8"))[
        "entries"
    ]
    block_c = yaml.safe_load((HERE / "queries_d4_routing_c.yaml").read_text(encoding="utf-8"))[
        "entries"
    ]

    problems: list[str] = []
    seen_ids = {e["id"] for e in v2}
    seen_queries = {norm(e["query"]): e["id"] for e in v2}

    per_route: dict[str, int] = {}
    n_boundary = 0
    for e in block_c:
        qid = e["id"]
        if qid in seen_ids:
            problems.append(f"{qid}: id already exists in v2")
        seen_ids.add(qid)
        route = e.get("route")
        if route not in valid_routes:
            problems.append(f"{qid}: invalid route {route!r}")
            continue
        per_route[route] = per_route.get(route, 0) + 1
        if route in admitted:
            exp = e.get("expected", {}).get("name")
            if exp not in admitted[route]:
                problems.append(f"{qid}: expected {exp!r} not in {route} whitelist")
        if "boundary_with" in e:
            n_boundary += 1
            bw = e["boundary_with"]
            if bw not in valid_routes:
                problems.append(f"{qid}: boundary_with {bw!r} not a real sibling")
        nq = norm(e["query"])
        if nq in seen_queries:
            problems.append(f"{qid}: near-duplicate of {seen_queries[nq]}")
        seen_queries[nq] = qid

    in_domain = per_route.get("trading-connector-agent", 0)
    print(f"block C entries={len(block_c)}, boundary={n_boundary}, in-domain={in_domain}")
    for r, n in sorted(per_route.items()):
        print(f"  {r}: {n}")
    if in_domain < 30:
        problems.append(f"in-domain entries = {in_domain}, protocol floor is 30")
    if problems:
        print(f"\n{len(problems)} violation(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
