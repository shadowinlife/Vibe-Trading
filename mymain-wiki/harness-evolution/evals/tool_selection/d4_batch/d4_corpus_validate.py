"""D4 routing-corpus validator: deterministic gates for the D4 admission eval.

Gates (HARNESS_EVOLUTION_D2_PLAN §5, D19 discipline): schema, route labels
within the admitted candidate set (+ direct/quant-agent/web-docs-agent),
expected.name inside the owning candidate's verified whitelist, near-duplicate
screen against the D-batch routing corpus and internally, and boundary-entry
rules (boundary_with present and naming a real sibling).

Exit 0 iff all gates pass.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

D4_DIR = Path(__file__).resolve().parent
D_BATCH = D4_DIR.parent / "d_batch"

CONTROL_ROUTES = {"direct", "quant-agent", "web-docs-agent"}


def norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--d4-dir", type=Path, default=D4_DIR)
    args = parser.parse_args()
    d4 = args.d4_dir

    cands = yaml.safe_load((d4 / "candidates_d4.yaml").read_text(encoding="utf-8"))
    admitted = {
        c["name"]: set(c.get("tools", [])) | set(c.get("skills", []))
        for c in cands["candidates"]
        if c["name"] not in ("orchestrator", "trading-connector-agent")
    }
    valid_routes = CONTROL_ROUTES | set(admitted)

    existing = yaml.safe_load(
        (D_BATCH / "queries_d_routing.yaml").read_text(encoding="utf-8")
    )["entries"]
    seen = {norm(e["query"]): e["id"] for e in existing}

    problems: list[str] = []
    total_new = 0
    for fname in ("queries_d4_routing_a.yaml", "queries_d4_routing_b.yaml"):
        entries = yaml.safe_load((d4 / fname).read_text(encoding="utf-8"))["entries"]
        total_new += len(entries)
        block_ids: set[str] = set()
        per_route: dict[str, int] = {}
        for e in entries:
            qid = e["id"]
            if qid in block_ids:
                problems.append(f"{qid}: duplicate id within {fname}")
            block_ids.add(qid)
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
                bw = e["boundary_with"]
                if bw not in valid_routes and bw != "main":
                    problems.append(f"{qid}: boundary_with {bw!r} not a real sibling")
            nq = norm(e["query"])
            if nq in seen:
                problems.append(f"{qid}: near-duplicate of {seen[nq]}")
            seen[nq] = qid
        print(f"[{fname}] entries={len(entries)}")
        for r, n in sorted(per_route.items()):
            print(f"  {r}: {n}")

    n_boundary = sum(
        1
        for fname in ("queries_d4_routing_a.yaml", "queries_d4_routing_b.yaml")
        for e in yaml.safe_load((d4 / fname).read_text(encoding="utf-8"))["entries"]
        if "boundary_with" in e
    )
    print(f"total new: {total_new}, boundary: {n_boundary}")
    if n_boundary != 20:
        problems.append(f"boundary entries = {n_boundary}, expected 20")
    if problems:
        print(f"\n{len(problems)} violation(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nall gates pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
