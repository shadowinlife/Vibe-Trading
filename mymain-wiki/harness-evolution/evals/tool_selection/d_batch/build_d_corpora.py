#!/usr/bin/env python3
"""D-batch corpus builder + deterministic coverage audit.

Builds the two pilot subagent surfaces (quant-agent D06+D07, web-docs-agent
D19) by filtering the post-B full surface corpus (``corpus_b_post.yaml``,
59 tools + 90 skills) down to each subagent's whitelist, then audits the
whitelist against the versioned query set: every domain query's expected
capability must be reachable inside the subagent surface (no dead ends).

Also reports description-block token counts (tiktoken, cl100k_base) for the
full surface vs each subagent surface — the deterministic disclosure-tax
proxy; the wire-format per-tool constant (~340 tok, B-batch §8.3 C6
measurement) is applied separately in the verdict doc.

Usage:
    cd agent
    python -m src.evals.tool_selection.d_batch.build_d_corpora

Outputs (next to this file):
    corpus_d_quant.yaml / corpus_d_webdocs.yaml — judge-ready surfaces
    queries_d_quant.yaml / queries_d_webdocs.yaml — Level-W query sets
        (base domain entries + expansion, expected ∈ frozen whitelist)
    queries_d_routing.yaml — Level-R routing set (all base + expansion +
        boundary, each entry carrying a derived ``route`` label)
    coverage_report.json — audit evidence (dead ends, per-domain coverage)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FULL_CORPUS = HERE.parent / "corpus_b_post.yaml"
QUERIES = HERE.parent / "queries.yaml"
EXPANSION = HERE / "queries_d_expansion.yaml"
SUBAGENTS = {
    "quant": (HERE / "subagent_quant_agent.yaml", {"D06", "D07"}),
    "webdocs": (HERE / "subagent_web_docs_agent.yaml", {"D19"}),
}
# Domain → route label for the Level-R corpus. Domains not listed here and
# not present as BND entries route to "direct" (the pilot is additive: the
# main loop keeps the full surface).
DOMAIN_ROUTES = {"D06": "quant-agent", "D07": "quant-agent", "D19": "web-docs-agent"}


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_subagent_corpus(full: dict, definition: dict) -> dict:
    """Filter the full surface down to the subagent whitelist.

    Args:
        full: Parsed full-surface corpus (tools + skills lists).
        definition: Parsed subagent definition (tools/skills whitelists).

    Returns:
        Corpus-shaped dict containing only whitelisted entries, in the
        original registration order.

    Raises:
        SystemExit: a whitelisted name is absent from the full corpus —
            the whitelist references a capability that does not exist.
    """
    out = {
        "schema_version": full["schema_version"],
        "captured_at": full["captured_at"],
        "source": f"d_batch filter of {full['source']['tools']} + skills",
        "subagent": definition["name"],
        "tools": [],
        "skills": [],
    }
    full_tools = {t["name"]: t for t in full["tools"]}
    full_skills = {s["name"]: s for s in full["skills"]}
    missing = [n for n in definition["tools"] if n not in full_tools]
    missing += [n for n in definition["skills"] if n not in full_skills]
    if missing:
        sys.exit(f"FATAL: {definition['name']} whitelist names absent "
                 f"from full corpus: {missing}")
    out["tools"] = [t for t in full["tools"] if t["name"] in definition["tools"]]
    out["skills"] = [s for s in full["skills"] if s["name"] in definition["skills"]]
    out["tool_count"] = len(out["tools"])
    out["skill_count"] = len(out["skills"])
    return out


def token_len(text: str) -> int:
    """Count cl100k_base tokens; falls back to a len/4 estimate."""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)


def block_tokens(corpus: dict) -> int:
    """Token count of the one-line candidate block the judge sees."""
    lines = []
    for kind in ("tool", "skill"):
        for row in corpus[f"{kind}s"]:
            desc = " ".join(row["description"].split())
            lines.append(f"{kind}:{row['name']} — {desc}")
    return token_len("\n".join(lines))


def coverage_audit(queries: dict, definition: dict, domains: set[str]) -> dict:
    """Check every domain query's expected hit against the whitelist."""
    white_tools = set(definition["tools"])
    white_skills = set(definition["skills"])
    hits, dead = [], []
    for e in queries["entries"]:
        if e["domain"] not in domains:
            continue
        expected = e["expected"]
        ok = (expected["name"] in white_tools if expected["kind"] == "tool"
              else expected["name"] in white_skills)
        (hits if ok else dead).append(
            {"id": e["id"], "kind": expected["kind"], "name": expected["name"]}
        )
    return {
        "subagent": definition["name"],
        "domains": sorted(domains),
        "total": len(hits) + len(dead),
        "covered": len(hits),
        "dead_ends": dead,
    }


def _check_whitelist_hit(entry: dict, definition: dict) -> bool:
    expected = entry["expected"]
    if expected["kind"] == "tool":
        return expected["name"] in set(definition["tools"])
    if expected["kind"] == "skill":
        return expected["name"] in set(definition["skills"])
    return False


def build_level_w_queries(base: dict, expansion: dict, definition: dict,
                          domains: set[str], out_name: str) -> dict:
    """Merge base + expansion domain entries into a Level-W query file.

    Every merged entry's expected capability must be inside the frozen
    whitelist — a violation here means the expansion corpus disagrees with
    the subagent definition, which is a build error, not a data point.
    """
    merged = {"version": 1,
              "source": "queries.yaml domain filter + queries_d_expansion.yaml",
              "entries": []}
    bad = []
    for src in (base["entries"], expansion["entries"]):
        for e in src:
            if e["domain"] not in domains:
                continue
            if not _check_whitelist_hit(e, definition):
                bad.append(e["id"])
                continue
            merged["entries"].append(e)
    if bad:
        sys.exit(f"FATAL: expansion entries outside frozen whitelist "
                 f"for {definition['name']}: {bad}")
    out_path = HERE / out_name
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    return {"file": out_name, "entries": len(merged["entries"])}


def build_routing_queries(base: dict, expansion: dict,
                          definitions: dict[str, dict]) -> dict:
    """Build the Level-R routing corpus with a derived ``route`` label.

    Label rule: D06/D07 → quant-agent, D19 → web-docs-agent, BND → the
    entry's own expected route, everything else → ``direct`` (additive
    pilot: the main loop keeps the full surface). Every expansion
    (non-BND) entry additionally re-checked against its subagent whitelist.
    """
    merged = {"version": 1,
              "source": "queries.yaml + queries_d_expansion.yaml, route "
                        "labels derived by DOMAIN_ROUTES + BND annotations",
              "entries": []}
    bad = []
    for src in (base["entries"], expansion["entries"]):
        for e in src:
            entry = dict(e)
            if e["domain"] == "BND":
                if e["expected"]["kind"] != "route":
                    sys.exit(f"FATAL: BND entry without route expectation: "
                             f"{e['id']}")
                entry["route"] = e["expected"]["name"]
            else:
                route = DOMAIN_ROUTES.get(e["domain"], "direct")
                if route != "direct":
                    definition = definitions[route]
                    if not _check_whitelist_hit(e, definition):
                        bad.append(e["id"])
                        continue
                entry["route"] = route
            merged["entries"].append(entry)
    if bad:
        sys.exit(f"FATAL: routed entries outside frozen whitelist: {bad}")
    out_path = HERE / "queries_d_routing.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    counts: dict[str, int] = {}
    for e in merged["entries"]:
        counts[e["route"]] = counts.get(e["route"], 0) + 1
    return {"file": out_path.name, "entries": len(merged["entries"]),
            "route_counts": counts}


def main() -> None:
    full = load_yaml(FULL_CORPUS)
    queries = load_yaml(QUERIES)
    expansion = load_yaml(EXPANSION) if EXPANSION.exists() else {"entries": []}
    report = {"full_surface": {
        "tools": full["tool_count"],
        "skills": full["skill_count"],
        "description_block_tokens": block_tokens(full),
    }, "subagents": {}, "query_files": []}

    definitions: dict[str, dict] = {}
    for key, (def_path, domains) in SUBAGENTS.items():
        definition = load_yaml(def_path)
        definitions[definition["name"]] = definition
        corpus = build_subagent_corpus(full, definition)
        out_path = HERE / f"corpus_d_{key}.yaml"
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(corpus, f, allow_unicode=True, sort_keys=False)
        audit = coverage_audit(queries, definition, domains)
        report["subagents"][definition["name"]] = {
            "surface": {
                "tools": corpus["tool_count"],
                "skills": corpus["skill_count"],
                "description_block_tokens": block_tokens(corpus),
            },
            "coverage": audit,
            "corpus_file": out_path.name,
        }
        report["query_files"].append(build_level_w_queries(
            queries, expansion, definition, domains,
            f"queries_d_{key}.yaml"))

    report["query_files"].append(
        build_routing_queries(queries, expansion, definitions))

    report_path = HERE / "coverage_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    full_tok = report["full_surface"]["description_block_tokens"]
    print(f"full surface: {report['full_surface']['tools']}T+"
          f"{report['full_surface']['skills']}S, {full_tok} tok")
    for name, r in report["subagents"].items():
        s = r["surface"]
        c = r["coverage"]
        pct = 100 * (1 - s["description_block_tokens"] / full_tok)
        print(f"{name}: {s['tools']}T+{s['skills']}S, "
              f"{s['description_block_tokens']} tok (-{pct:.0f}%), "
              f"coverage {c['covered']}/{c['total']}"
              + (f" DEAD: {c['dead_ends']}" if c["dead_ends"] else ""))
    for qf in report["query_files"]:
        extra = f" routes={qf['route_counts']}" if "route_counts" in qf else ""
        print(f"query file {qf['file']}: {qf['entries']} entries{extra}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
