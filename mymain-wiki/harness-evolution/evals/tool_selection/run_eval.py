"""Deterministic lexical tool/skill selection eval for the Vibe-Trading agent.

Scores how well the *wording* of the frozen MCP tool and skill descriptions
routes natural-language finance queries to the right capability. It is the
regression sentinel for description edits: the corpus is frozen in
``corpus_snapshot.yaml`` (rebuild only via ``--rebuild-corpus``), the queries
are versioned in ``queries.yaml``, and scoring is pure lexical overlap — no
LLM, no network, and no import of ``mcp_server`` at eval time. Two runs over
the same snapshot print byte-identical output.

Scoring model (deliberately simple and position-sensitive):

* Queries and descriptions are tokenized CJK-aware — character bigrams
  inside CJK runs, lowercased word tokens (>= 2 chars) inside Latin/digit
  runs.
* A query token matching a candidate's NAME token exactly earns
  ``NAME_WEIGHT`` — name-token matches weigh most.
* Otherwise a query token found in the candidate's DESCRIPTION earns the
  position weight ``(len - first_pos) / len`` — a trigger word front-loaded
  in the description outweighs the same word buried at the end. That is the
  point: the baseline measures whether descriptions front-load their
  triggers.
* Candidates rank by total score; ties break on ``(kind, name)`` so the
  ordering is deterministic.

Metrics: top-1 accuracy, top-3 hit rate, negative false-recall (a documented
plausible-but-wrong candidate outscores the expected target), a per-domain
breakdown, and a miss-taxonomy bucket per top-1 miss.

Usage:
    python -m src.evals.tool_selection.run_eval                 # score + print
    python -m src.evals.tool_selection.run_eval --rebuild-corpus
    python -m src.evals.tool_selection.run_eval --write-report artifacts/baseline_report.md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[2]
QUERIES_PATH = HERE / "queries.yaml"
CORPUS_PATH = HERE / "corpus_snapshot.yaml"

NAME_WEIGHT = 3.0
TOP_N = 3

# Generic verb tokens shared by many tool names (get_market_data,
# list_skills, run_swarm, ...). They are excluded from the name-collision
# taxonomy check so the bucket keeps only *meaningful* shared name parts.
NAME_STOPWORDS = frozenset(
    {"get", "list", "run", "read", "write", "load", "add", "update", "start",
     "retry", "refresh", "reap", "scan", "render", "extract", "analyze",
     "search", "check"}
)

TAXONOMY_ORDER = (
    "name-collision", "boundary-missing", "keyword-buried",
    "name-reality-drift", "dual-exposure", "other",
)

_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenize text CJK-aware.

    CJK runs become character bigrams (a lone CJK character emits itself);
    Latin/digit runs become lowercased word tokens of at least two
    characters. Everything else is a separator.

    Args:
        text: Arbitrary input — query, capability name, or description.

    Returns:
        Token list in order of appearance.
    """
    tokens: list[str] = []
    for match in _RUN.finditer(text):
        run = match.group(0)
        if run[0].isascii():
            if len(run) >= 2:
                tokens.append(run.lower())
        elif len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def normalize_name(name: str) -> str:
    """Reduce a capability name to lowercase alphanumerics only.

    Args:
        name: Tool or skill name, e.g. ``alpha_zoo`` or ``alpha-zoo``.

    Returns:
        Normalized form (``alphazoo`` for both examples).
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class Candidate:
    """One routable capability (MCP tool or bundled skill) with token indexes."""

    kind: str
    name: str
    name_tokens: frozenset[str]
    desc_len: int
    desc_first_pos: dict[str, int]


@dataclass(frozen=True)
class EntryResult:
    """Scored outcome of one query entry."""

    entry_id: str
    domain: str
    expected_kind: str
    expected_name: str
    winner_kind: str
    winner_name: str
    top1_hit: bool
    top3_hit: bool
    top3: tuple[str, ...]
    neg_false_recall: bool | None
    taxonomy: str | None


def build_candidates(corpus: dict) -> list[Candidate]:
    """Index every corpus capability for scoring, in corpus order.

    Args:
        corpus: Parsed ``corpus_snapshot.yaml``.

    Returns:
        Candidates for all tools then all skills.
    """
    candidates: list[Candidate] = []
    for kind in ("tool", "skill"):
        for row in corpus[f"{kind}s"]:
            desc_tokens = tokenize(row["description"])
            first_pos: dict[str, int] = {}
            for index, token in enumerate(desc_tokens):
                first_pos.setdefault(token, index)
            candidates.append(
                Candidate(
                    kind=kind,
                    name=row["name"],
                    name_tokens=frozenset(tokenize(row["name"])),
                    desc_len=len(desc_tokens),
                    desc_first_pos=first_pos,
                )
            )
    return candidates


def score_candidate(query_tokens: list[str], candidate: Candidate) -> float:
    """Score one candidate against one tokenized query.

    Args:
        query_tokens: Tokens of the query, in order.
        candidate: Indexed capability.

    Returns:
        Sum of per-token contributions: ``NAME_WEIGHT`` for an exact
        name-token match, else the description position weight
        ``(desc_len - first_pos) / desc_len``, else zero.
    """
    score = 0.0
    for token in query_tokens:
        if token in candidate.name_tokens:
            score += NAME_WEIGHT
            continue
        pos = candidate.desc_first_pos.get(token)
        if pos is not None:
            score += (candidate.desc_len - pos) / candidate.desc_len
    return score


def classify_miss(
    entry: dict,
    expected: Candidate,
    winner: Candidate,
    query_tokens: list[str],
) -> str:
    """Assign a deterministic taxonomy bucket to a top-1 miss.

    Classification keys off the candidate that won instead of the expected
    target, checked in a fixed order (first match wins):

    1. ``name-collision`` — winner and expected share a meaningful name
       token (``sentiment`` vs ``sentiment-analysis``).
    2. ``boundary-missing`` — the winner is one of the entry's documented
       negatives, i.e. a known competitor the descriptions fail to separate.
    3. ``keyword-buried`` — the expected description does contain query
       terms, but its earliest hit sits in the second half of the text.
    4. ``name-reality-drift`` — the expected description (and name) shares
       no query term at all; the wording does not describe what was asked.
    5. ``dual-exposure`` — same capability exposed under both kinds with an
       identical normalized name (``alpha_zoo`` tool vs ``alpha-zoo`` skill).
    6. ``other``.

    Args:
        entry: Query entry dict from ``queries.yaml``.
        expected: The entry's expected candidate.
        winner: The candidate that ranked first.
        query_tokens: Tokens of the query.

    Returns:
        One of ``TAXONOMY_ORDER``.
    """
    winner_name_tokens = winner.name_tokens - NAME_STOPWORDS
    expected_name_tokens = expected.name_tokens - NAME_STOPWORDS
    if winner_name_tokens & expected_name_tokens:
        return "name-collision"
    negatives = entry.get("negatives") or []
    if winner.name in negatives:
        return "boundary-missing"
    hits = [expected.desc_first_pos[t] for t in query_tokens if t in expected.desc_first_pos]
    name_hit = any(t in expected.name_tokens for t in query_tokens)
    if not hits and not name_hit:
        return "name-reality-drift"
    if hits and min(hits) >= expected.desc_len // 2:
        return "keyword-buried"
    if normalize_name(winner.name) == normalize_name(expected.name) and winner.kind != expected.kind:
        return "dual-exposure"
    return "other"


def evaluate(corpus: dict, queries: list[dict]) -> tuple[list[EntryResult], dict]:
    """Score every query entry against the corpus.

    Args:
        corpus: Parsed ``corpus_snapshot.yaml``.
        queries: Entry dicts from ``queries.yaml``.

    Returns:
        Per-entry results in query order, plus an aggregate dict with
        headline metrics, per-domain breakdown, and taxonomy counts.
    """
    candidates = build_candidates(corpus)
    by_name: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.name, []).append(candidate)

    results: list[EntryResult] = []
    for entry in queries:
        query_tokens = tokenize(entry["query"])
        scored = sorted(
            ((score_candidate(query_tokens, c), c) for c in candidates),
            key=lambda pair: (-pair[0], pair[1].kind, pair[1].name),
        )
        expected_candidates = by_name[entry["expected"]["name"]]
        expected = next(
            c for c in expected_candidates if c.kind == entry["expected"]["kind"]
        )
        expected_score = score_candidate(query_tokens, expected)

        top = scored[:TOP_N]
        winner = scored[0][1]
        top1_hit = winner.kind == expected.kind and winner.name == expected.name
        top3_hit = any(
            c.kind == expected.kind and c.name == expected.name for _, c in top
        )

        negatives = entry.get("negatives") or []
        neg_false_recall: bool | None = None
        if negatives:
            neg_scores = [
                score_candidate(query_tokens, c)
                for name in negatives
                for c in by_name[name]
            ]
            neg_false_recall = any(s > expected_score for s in neg_scores)

        taxonomy = None
        if not top1_hit:
            taxonomy = classify_miss(entry, expected, winner, query_tokens)

        results.append(
            EntryResult(
                entry_id=entry["id"],
                domain=entry["domain"],
                expected_kind=expected.kind,
                expected_name=expected.name,
                winner_kind=winner.kind,
                winner_name=winner.name,
                top1_hit=top1_hit,
                top3_hit=top3_hit,
                top3=tuple(f"{c.kind}:{c.name}" for _, c in top),
                neg_false_recall=neg_false_recall,
                taxonomy=taxonomy,
            )
        )

    total = len(results)
    top1_hits = sum(r.top1_hit for r in results)
    top3_hits = sum(r.top3_hit for r in results)
    with_negs = [r for r in results if r.neg_false_recall is not None]
    neg_recalls = sum(r.neg_false_recall for r in with_negs)

    domains = sorted({r.domain for r in results})
    per_domain = {}
    for domain in domains:
        rows = [r for r in results if r.domain == domain]
        per_domain[domain] = {
            "entries": len(rows),
            "top1": sum(r.top1_hit for r in rows),
            "top3": sum(r.top3_hit for r in rows),
        }

    taxonomy_counts = {bucket: 0 for bucket in TAXONOMY_ORDER}
    for result in results:
        if result.taxonomy is not None:
            taxonomy_counts[result.taxonomy] += 1

    aggregates = {
        "entries": total,
        "top1_hits": top1_hits,
        "top3_hits": top3_hits,
        "top1_accuracy": top1_hits / total,
        "top3_hit_rate": top3_hits / total,
        "neg_entries": len(with_negs),
        "neg_false_recalls": neg_recalls,
        "neg_false_recall_rate": neg_recalls / len(with_negs) if with_negs else 0.0,
        "per_domain": per_domain,
        "taxonomy_counts": taxonomy_counts,
    }
    return results, aggregates


def render_report(results: list[EntryResult], aggregates: dict, corpus: dict) -> str:
    """Render the deterministic plain-text score report.

    Args:
        results: Per-entry results from ``evaluate``.
        aggregates: Aggregate metrics from ``evaluate``.
        corpus: Parsed corpus snapshot (for capture metadata).

    Returns:
        The full report string; byte-identical across runs on the same
        snapshot and queries.
    """
    lines = [
        "=== Vibe-Trading tool-selection eval (lexical baseline) ===",
        f"corpus snapshot: captured_at={corpus['captured_at']} "
        f"tools={corpus['tool_count']} skills={corpus['skill_count']}",
        f"queries: {aggregates['entries']} entries, "
        f"{len(aggregates['per_domain'])} domains",
        "",
        f"top-1 accuracy        : {aggregates['top1_hits']}/{aggregates['entries']}"
        f" = {aggregates['top1_accuracy']:.4f}",
        f"top-3 hit rate        : {aggregates['top3_hits']}/{aggregates['entries']}"
        f" = {aggregates['top3_hit_rate']:.4f}",
        f"negative false-recall : {aggregates['neg_false_recalls']}"
        f"/{aggregates['neg_entries']} = {aggregates['neg_false_recall_rate']:.4f}",
        "",
        "per-domain breakdown:",
        "domain  entries  top1  top3  top1_acc",
    ]
    for domain, row in aggregates["per_domain"].items():
        acc = row["top1"] / row["entries"]
        lines.append(
            f"{domain:<7} {row['entries']:>7}  {row['top1']:>4}  {row['top3']:>4}"
            f"  {acc:.4f}"
        )
    lines += ["", "miss taxonomy:", ""]
    for bucket in TAXONOMY_ORDER:
        lines.append(f"  {bucket:<19} {aggregates['taxonomy_counts'][bucket]}")
    lines += ["", "top-1 misses (id, domain, expected -> winner, taxonomy):", ""]
    for result in results:
        if result.top1_hit:
            continue
        lines.append(
            f"{result.entry_id:<9} {result.domain}  "
            f"{result.expected_kind}:{result.expected_name} -> "
            f"{result.winner_kind}:{result.winner_name}  [{result.taxonomy}]"
        )
    lines.append("")
    return "\n".join(lines)


def load_assets() -> tuple[dict, list[dict]]:
    """Load the frozen corpus snapshot and versioned queries.

    Returns:
        The parsed corpus snapshot and the entry list.
    """
    corpus = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    queries = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))["entries"]
    return corpus, queries


def rebuild_corpus() -> int:
    """Re-capture the corpus snapshot from the live server and skill loader.

    This is the only code path that imports ``mcp_server``; it is explicit
    (``--rebuild-corpus``) and never runs during scoring.

    Returns:
        Process exit code (0 on success).
    """
    import asyncio
    from datetime import datetime, timezone

    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    import mcp_server
    from src.agent.skills import SkillsLoader

    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_rows = [
        {"name": tool.name, "description": (tool.description or "").strip()}
        for tool in tools
    ]
    loader = SkillsLoader(user_skills_dir=AGENT_DIR / "__no_user_skills__")
    skill_rows = [
        {"name": skill.name, "description": (skill.description or "").strip()}
        for skill in loader.skills
    ]
    snapshot = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "tools": "mcp_server.mcp.list_tools() (registration order)",
            "skills": "src.agent.skills.SkillsLoader bundled skills (loader order)",
        },
        "tool_count": len(tool_rows),
        "skill_count": len(skill_rows),
        "tools": tool_rows,
        "skills": skill_rows,
    }
    with CORPUS_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            snapshot, handle, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=100,
        )
    print(
        f"corpus snapshot written: {CORPUS_PATH.name} "
        f"(tools={len(tool_rows)}, skills={len(skill_rows)})"
    )
    return 0


def write_markdown_report(
    results: list[EntryResult], aggregates: dict, corpus: dict, path: Path
) -> None:
    """Write the markdown baseline report artifact.

    Args:
        results: Per-entry results from ``evaluate``.
        aggregates: Aggregate metrics from ``evaluate``.
        corpus: Parsed corpus snapshot.
        path: Destination file path.
    """
    query_by_id = {e["id"]: e for e in yaml.safe_load(
        QUERIES_PATH.read_text(encoding="utf-8"))["entries"]}
    lines = [
        "# Tool-selection lexical baseline report",
        "",
        "Deterministic lexical scoring of the Vibe-Trading agent's tool/skill",
        "descriptions against the versioned query set. Regenerate with:",
        "",
        "```",
        "cd agent && python -m src.evals.tool_selection.run_eval"
        " --write-report src/evals/tool_selection/artifacts/baseline_report.md",
        "```",
        "",
        "## Corpus capture",
        "",
        f"- captured_at: `{corpus['captured_at']}`",
        f"- MCP tools: {corpus['tool_count']}",
        f"- bundled skills: {corpus['skill_count']}",
        f"- queries: {aggregates['entries']} entries across"
        f" {len(aggregates['per_domain'])} domains",
        "",
        "## Aggregate scores",
        "",
        "| metric | value |",
        "|---|---|",
        f"| top-1 accuracy | {aggregates['top1_hits']}/{aggregates['entries']}"
        f" = {aggregates['top1_accuracy']:.4f} |",
        f"| top-3 hit rate | {aggregates['top3_hits']}/{aggregates['entries']}"
        f" = {aggregates['top3_hit_rate']:.4f} |",
        f"| negative false-recall | {aggregates['neg_false_recalls']}"
        f"/{aggregates['neg_entries']} = {aggregates['neg_false_recall_rate']:.4f} |",
        "",
        "## Per-domain breakdown",
        "",
        "| domain | entries | top-1 | top-3 | top-1 accuracy |",
        "|---|---|---|---|---|",
    ]
    for domain, row in aggregates["per_domain"].items():
        lines.append(
            f"| {domain} | {row['entries']} | {row['top1']} | {row['top3']}"
            f" | {row['top1'] / row['entries']:.4f} |"
        )
    lines += [
        "",
        "## Miss taxonomy",
        "",
        "| bucket | misses |",
        "|---|---|",
    ]
    for bucket in TAXONOMY_ORDER:
        lines.append(f"| {bucket} | {aggregates['taxonomy_counts'][bucket]} |")
    lines += [
        "",
        "## Complete miss list",
        "",
        "| id | domain | query | expected | winner | taxonomy |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        if result.top1_hit:
            continue
        query = query_by_id[result.entry_id]["query"]
        lines.append(
            f"| {result.entry_id} | {result.domain} | {query}"
            f" | {result.expected_kind}:{result.expected_name}"
            f" | {result.winner_kind}:{result.winner_name}"
            f" | {result.taxonomy} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
        "- This is a **lexical baseline**, a regression sentinel for description",
        "  wording (trigger front-loading, name distinguishability, boundary",
        "  phrasing). It is not a measurement of LLM routing quality; real",
        "  selection also depends on the model's reasoning over full schemas.",
        "- A miss here means the *descriptions as written* do not lexically",
        "  separate the expected target from the winner. Some misses are honest",
        "  evidence of AUDIT findings (K/G/Q items); others are artifacts of",
        "  the bag-of-tokens model. The taxonomy bucket distinguishes the cases.",
        "- The corpus snapshot is frozen on purpose: description edits land",
        "  *after* this baseline and are measured against it. Rebuilding the",
        "  snapshot resets the baseline and is a deliberate act.",
        "- LLM-judge mode (semantic scoring of the same query set) is future",
        "  work E2; this suite is its deterministic anchor.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rebuild-corpus", action="store_true",
        help="re-capture corpus_snapshot.yaml from mcp_server + SkillsLoader",
    )
    parser.add_argument(
        "--write-report", metavar="PATH",
        help="also write the markdown baseline report to PATH",
    )
    args = parser.parse_args(argv)

    if args.rebuild_corpus:
        return rebuild_corpus()

    corpus, queries = load_assets()
    results, aggregates = evaluate(corpus, queries)
    report = render_report(results, aggregates, corpus)
    print(report)
    if args.write_report:
        write_markdown_report(results, aggregates, corpus, Path(args.write_report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
