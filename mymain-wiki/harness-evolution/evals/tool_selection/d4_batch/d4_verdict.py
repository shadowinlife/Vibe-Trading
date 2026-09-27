"""D2-3c D4 admission verdict: per-candidate routing metrics from judge traces.

Frozen gates (HARNESS_EVOLUTION_D2_PLAN §5, same bars as the D-batch pilots):
- R1 per-candidate recall >= 0.85 (target queries routed to the owner);
- R2 over-delegation <= 5% on the 140 direct-control queries;
- R3 boundary arbitration >= 17/20 on the boundary set;
- Pilot regression: quant-agent / web-docs-agent control recall must not
  degrade below their D-batch values (0.85 floor) under the enlarged
  11-card competition.

Judges: qwen3.8-max + kimi-k3, pooled per D-batch convention (query x model).

Usage: python -m src.evals.tool_selection.d4_batch.d4_verdict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parent / "artifacts"
MODELS = ("qwen3.8-max", "kimi-k3")
R1_FLOOR, R2_CEIL, R3_FLOOR = 0.85, 0.05, 17 / 20


def load_trace(model: str, tag: str) -> dict[str, dict]:
    path = ARTIFACTS / f"d_routing_trace_{model}_{tag}.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("header"):
            out[rec["query_id"]] = rec
    return out


def main() -> int:
    corpus = yaml.safe_load((HERE / "queries_d4_routing_all.yaml").read_text(encoding="utf-8"))[
        "entries"
    ]
    by_id = {e["id"]: e for e in corpus}

    traces = {m: load_trace(m, "d4") for m in MODELS}
    n_records = sum(len(t) for t in traces.values())
    print(f"records: {n_records} (expect {len(corpus) * len(MODELS)})\n")

    # Per-route recall on each route's target queries.
    per_route: dict[str, dict[str, int]] = {}
    confusion: dict[tuple[str, str], int] = {}
    for e in corpus:
        route = e.get("route")
        if route in (None, "direct"):
            continue
        per_route.setdefault(route, {"hit": 0, "n": 0})
        for m in MODELS:
            rec = traces[m].get(e["id"])
            if rec is None:
                continue
            per_route[route]["n"] += 1
            if rec.get("route_hit"):
                per_route[route]["hit"] += 1
            elif rec.get("route"):
                key = (route, rec["route"])
                confusion[key] = confusion.get(key, 0) + 1

    print("== R1 per-candidate recall (gate >= 0.85) ==")
    worst = []
    for route, st in sorted(per_route.items()):
        if not st["n"]:
            continue
        r = st["hit"] / st["n"]
        flag = "PASS" if r >= R1_FLOOR else "FAIL"
        if r < R1_FLOOR:
            worst.append((route, r))
        print(f"  {route}: {st['hit']}/{st['n']} = {r:.1%}  [{flag}]")

    direct = [e for e in corpus if e.get("route") == "direct"]
    d_hit = d_n = 0
    over: list[str] = []
    for e in direct:
        for m in MODELS:
            rec = traces[m].get(e["id"])
            if rec is None:
                continue
            d_n += 1
            if rec.get("route") == "direct":
                d_hit += 1
            else:
                over.append(f"{e['id']}->{rec.get('route')}")
    over_rate = 1 - d_hit / d_n if d_n else 0.0
    print(f"\n== R2 over-delegation on direct controls (gate <= {R2_CEIL:.0%}) ==")
    print(f"  direct held: {d_hit}/{d_n}; over-delegation {over_rate:.2%} "
          f"[{'PASS' if over_rate <= R2_CEIL else 'FAIL'}]")
    if over:
        print(f"  over-delegated: {over[:15]}")

    boundary = [e for e in corpus if "boundary_with" in e]
    b_hit = b_n = 0
    b_miss: list[str] = []
    for e in boundary:
        for m in MODELS:
            rec = traces[m].get(e["id"])
            if rec is None:
                continue
            b_n += 1
            if rec.get("route_hit"):
                b_hit += 1
            else:
                b_miss.append(f"{e['id']} exp={e['route']} got={rec.get('route')}")
    b_rate = b_hit / b_n if b_n else 0.0
    print(f"\n== R3 boundary arbitration (gate >= {R3_FLOOR:.0%}) ==")
    print(f"  {b_hit}/{b_n} = {b_rate:.1%} [{'PASS' if b_rate >= R3_FLOOR else 'FAIL'}]")
    for m_ in b_miss:
        print(f"    {m_}")

    print("\n== top confusions ==")
    for (want, got), n in sorted(confusion.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {want} -> {got}: {n}")

    ok = (
        not worst
        and over_rate <= R2_CEIL
        and b_rate >= R3_FLOOR
    )
    print(f"\noverall: {'ALL GATES PASS' if ok else 'GATES FAILED: ' + str(worst)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
