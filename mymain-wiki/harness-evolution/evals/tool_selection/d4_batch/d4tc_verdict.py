#!/usr/bin/env python3
"""Trading-connector mini-admission verdict (DEC-5), protocol gates applied.

Corpus: queries_d4_routing_all_v3.yaml (395 = v2 353 with 8 D16 relabels + 42 block C).
Traces: d_routing_trace_<model>_d4tc.jsonl. Judges: qwen3.8-max + kimi-k3, pooled.

Gates (same bars as D4, HARNESS_EVOLUTION_D2_PLAN §5):
- R1: trading-connector recall >= 0.85 on its 38 target queries;
- R2: over-delegation onto trading-connector from all other queries <= 5%;
- R3: block C boundary set (20 entries with boundary_with) >= 85% correct;
- Regression: every previously-admitted route's recall must stay >= its
  d4r3 value minus 5pp tolerance (12-card competition must not collapse
  the existing roster).

Usage: python -m src.evals.tool_selection.d4_batch.d4tc_verdict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parent / "artifacts"
MODELS = ("qwen3.8-max", "kimi-k3")
R1_FLOOR, R2_CEIL, R3_FLOOR, REG_TOL = 0.85, 0.05, 0.85, 0.05
TC = "trading-connector-agent"


def load_trace(model: str, tag: str) -> dict[str, dict]:
    out = {}
    for line in (ARTIFACTS / f"d_routing_trace_{model}_{tag}.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        rec = json.loads(line)
        if not rec.get("header"):
            out[rec["query_id"]] = rec
    return out


def main() -> int:
    entries = yaml.safe_load((HERE / "queries_d4_routing_all_v3.yaml").read_text(encoding="utf-8"))[
        "entries"
    ]
    traces = {m: load_trace(m, "d4tc") for m in MODELS}
    n_records = sum(len(t) for t in traces.values())
    print(f"records: {n_records} (expect {len(entries) * len(MODELS)})\n")

    def hit(e, m):
        rec = traces[m].get(e["id"])
        return rec is not None and rec.get("route") == e["route"]

    # R1
    tc_targets = [e for e in entries if e["route"] == TC]
    r1_hits = sum(hit(e, m) for e in tc_targets for m in MODELS)
    r1_n = len(tc_targets) * len(MODELS)
    r1 = r1_hits / r1_n
    print(f"R1 trading-connector recall: {r1_hits}/{r1_n} = {r1:.3f} (floor {R1_FLOOR})")

    # R2: non-target queries that routed to TC anyway
    others = [e for e in entries if e["route"] != TC]
    r2_bad = sum(
        1
        for e in others
        for m in MODELS
        if traces[m].get(e["id"], {}).get("route") == TC
    )
    r2_n = len(others) * len(MODELS)
    r2 = r2_bad / r2_n
    print(f"R2 over-delegation onto trading-connector: {r2_bad}/{r2_n} = {r2:.4f} (ceil {R2_CEIL})")

    # R3: block C boundary entries
    boundary = [e for e in entries if e["id"].startswith("D16-1") and "boundary_with" in e]
    r3_hits = sum(hit(e, m) for e in boundary for m in MODELS)
    r3_n = len(boundary) * len(MODELS)
    r3 = r3_hits / r3_n
    print(f"R3 boundary arbitration: {r3_hits}/{r3_n} = {r3:.3f} (floor {R3_FLOOR})")

    # Regression vs d4r3 baselines
    base_traces = {}
    for m in MODELS:
        p = ARTIFACTS / f"d_routing_trace_{m}_d4r3.jsonl"
        if p.exists():
            base_traces[m] = load_trace(m, "d4r3")
    print("\nRegression (per-route recall, d4tc vs d4r3 baseline):")
    regressions = []
    routes = sorted({e["route"] for e in entries if e["route"] not in ("direct", "main", TC)})
    for route in routes:
        route_entries = [e for e in entries if e["route"] == route]
        cur = sum(hit(e, m) for e in route_entries for m in MODELS) / (len(route_entries) * len(MODELS))
        if base_traces:
            base_hits = base_n = 0
            for e in route_entries:
                for m in MODELS:
                    rec = base_traces[m].get(e["id"])
                    if rec is not None:
                        base_n += 1
                        base_hits += rec.get("route") == route
            base = base_hits / base_n if base_n else None
        else:
            base = None
        flag = ""
        if base is not None and cur < base - REG_TOL:
            flag = " ⚠️ REGRESSION"
            regressions.append(route)
        print(f"  {route}: {cur:.3f}" + (f" (baseline {base:.3f}){flag}" if base is not None else ""))

    print("\n--- gates ---")
    ok = True
    for name, passed in (
        ("R1", r1 >= R1_FLOOR),
        ("R2", r2 <= R2_CEIL),
        ("R3", r3 >= R3_FLOOR),
        ("regression", not regressions),
    ):
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
        ok &= passed
    print(f"\nVERDICT: {'ADMIT' if ok else 'REJECT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
