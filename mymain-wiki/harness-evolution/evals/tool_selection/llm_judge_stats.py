"""Paired panel statistics for the E2 LLM-judge evaluation.

Reads the golden traces, probe records and config produced by
``run_llm_judge`` and renders ``artifacts/llm_judge_stats_report.md``.

The design is paired — every query is its own control across the baseline
and post surfaces — so the headline test is exact McNemar (a two-sided
binomial test on the discordant pairs, ``scipy.stats.binomtest``), with
Wilson 95% CIs on each surface's proportion. A lexical-vs-semantic
cross-check scores the same queries through ``run_eval`` on both corpora:
the disagreements are exactly the proxy blind spots the LLM judge exists
to catch.

Deterministic given the trace files: no wall-clock content, no network.

Usage:
    cd agent
    python -m src.evals.tool_selection.llm_judge_stats
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from scipy.stats import binomtest

from src.evals.tool_selection import run_eval
from src.evals.tool_selection.llm_judge_protocol import prompt_template_sha256
from src.evals.tool_selection.llm_judge_report import estimate_cost
from src.evals.tool_selection.run_llm_judge import (
    ARTIFACTS_DIR,
    CONFIG_PATH,
    load_config,
    load_corpus,
    load_queries,
    load_trace,
    trace_path_for,
)

Z_95 = 1.959963984540054
SURFACES = ("baseline", "post")
PROBE_CONFIDENCE_FLOOR = 0.95


def probe_path_for(artifacts_dir: Path, model_id: str, surface: str) -> Path:
    """Return the probe-record path for one (model, surface) probe run.

    Args:
        artifacts_dir: Artifact directory.
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.

    Returns:
        The probe JSONL path.
    """
    return artifacts_dir / f"llm_judge_probe_{model_id}_{surface}.jsonl"


def wilson_ci(hits: int, n: int) -> tuple[float, float] | None:
    """Wilson score 95% confidence interval for a proportion.

    Args:
        hits: Number of successes.
        n: Number of trials.

    Returns:
        (lower, upper) bounds, or None when n == 0.
    """
    if n == 0:
        return None
    p = hits / n
    denom = 1.0 + Z_95 * Z_95 / n
    center = (p + Z_95 * Z_95 / (2.0 * n)) / denom
    half = Z_95 * math.sqrt(p * (1.0 - p) / n + Z_95 * Z_95 / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def mcnemar_p(n_improved: int, n_regressed: int) -> float:
    """Exact two-sided McNemar p-value on the discordant pairs.

    Args:
        n_improved: baseline miss -> post hit pairs.
        n_regressed: baseline hit -> post miss pairs.

    Returns:
        ``binomtest`` two-sided p-value with null p=0.5; 1.0 when there are
        no discordant pairs.
    """
    discordant = n_improved + n_regressed
    if discordant == 0:
        return 1.0
    result = binomtest(n_improved, discordant, 0.5, alternative="two-sided")
    return float(result.pvalue)


@dataclass(frozen=True)
class PairRow:
    """One query's paired outcomes across the two surfaces."""

    query_id: str
    baseline_top1: bool
    post_top1: bool
    baseline_top3: bool
    post_top3: bool
    baseline_first: str | None
    post_first: str | None


def load_query_map(artifacts_dir: Path, model_id: str, surface: str) -> dict[str, dict]:
    """Load one (model, surface) trace keyed by query_id.

    Args:
        artifacts_dir: Artifact directory.
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.

    Returns:
        query_id -> trace record (empty when the trace is missing).
    """
    _header, lines = load_trace(trace_path_for(artifacts_dir, model_id, surface))
    return {r["query_id"]: r for r in lines if r.get("query_id")}


def build_pairs(baseline_map: dict, post_map: dict) -> list[PairRow]:
    """Align two surface traces by query_id (the paired design).

    Args:
        baseline_map: Baseline-surface records keyed by query_id.
        post_map: Post-surface records keyed by query_id.

    Returns:
        PairRows for the queries present in BOTH surfaces, sorted by id.
    """
    rows = []
    for query_id in sorted(set(baseline_map) & set(post_map)):
        base, post = baseline_map[query_id], post_map[query_id]
        base_parsed = base.get("parsed") or {}
        post_parsed = post.get("parsed") or {}
        rows.append(PairRow(
            query_id=query_id,
            baseline_top1=bool(base.get("top1_hit")),
            post_top1=bool(post.get("top1_hit")),
            baseline_top3=bool(base.get("top3_hit")),
            post_top3=bool(post.get("top3_hit")),
            baseline_first=base_parsed.get("first"),
            post_first=post_parsed.get("first"),
        ))
    return rows


def paired_block(rows: list[PairRow], base_attr: str, post_attr: str) -> dict:
    """Paired comparison of one outcome across surfaces.

    Args:
        rows: PairRows (each query its own control).
        base_attr: PairRow attribute for the baseline outcome.
        post_attr: PairRow attribute for the post outcome.

    Returns:
        n_pairs, per-surface hits/rates/Wilson CIs, n_improved,
        n_regressed, delta (post - baseline) and the exact McNemar p.
    """
    n = len(rows)
    base_hits = sum(int(getattr(r, base_attr)) for r in rows)
    post_hits = sum(int(getattr(r, post_attr)) for r in rows)
    improved = sum(int(getattr(r, post_attr) and not getattr(r, base_attr)) for r in rows)
    regressed = sum(int(getattr(r, base_attr) and not getattr(r, post_attr)) for r in rows)
    return {
        "n_pairs": n,
        "baseline_hits": base_hits,
        "post_hits": post_hits,
        "baseline_rate": base_hits / n if n else None,
        "post_rate": post_hits / n if n else None,
        "baseline_ci": wilson_ci(base_hits, n),
        "post_ci": wilson_ci(post_hits, n),
        "n_improved": improved,
        "n_regressed": regressed,
        "delta": (post_hits - base_hits) / n if n else None,
        "mcnemar_p": mcnemar_p(improved, regressed),
    }


def rate_delta(baseline_map: dict, post_map: dict, kind: str) -> dict:
    """Plain per-surface rate delta for invalid or neg-false-recall.

    Args:
        baseline_map: Baseline-surface records.
        post_map: Post-surface records.
        kind: ``invalid`` (parsed is None) or ``neg_false_recall``
            (restricted to entries where the field is applicable).

    Returns:
        Per-surface rates, denominators and the post-minus-baseline delta.
    """
    def rate(records: dict) -> tuple[float | None, int]:
        if kind == "invalid":
            values = [r.get("parsed") is None for r in records.values()]
        else:
            values = [bool(r.get("neg_false_recall")) for r in records.values()
                      if r.get("neg_false_recall") is not None]
        if not values:
            return None, 0
        return sum(values) / len(values), len(values)

    base_rate, base_n = rate(baseline_map)
    post_rate, post_n = rate(post_map)
    delta = None
    if base_rate is not None and post_rate is not None:
        delta = post_rate - base_rate
    return {"baseline": base_rate, "post": post_rate, "delta": delta,
            "baseline_n": base_n, "post_n": post_n}


def flip_rows(rows: list[PairRow]) -> list[dict]:
    """List every query whose top-1 outcome flipped between surfaces.

    Args:
        rows: PairRows for one model.

    Returns:
        Flip dicts with query_id, direction and each surface's first pick.
    """
    flips = []
    for row in rows:
        if row.baseline_top1 != row.post_top1:
            flips.append({
                "query_id": row.query_id,
                "direction": "improved" if row.post_top1 else "regressed",
                "baseline_first": row.baseline_first or "-",
                "post_first": row.post_first or "-",
            })
    return flips


def lexical_top1() -> dict[str, dict[str, bool]]:
    """Lexical top-1 outcome per query on both corpora (run_eval scoring).

    A query is scored only where it is evaluatable on that corpus — expected
    target and every listed negative must exist in it. The Q2 rename entries
    are exactly the ones the baseline corpus cannot evaluate; they drop out
    of the lexical cross-check there (a documented proxy blind spot, not a
    scoring choice).

    Returns:
        surface -> {query_id -> top1_hit}.
    """
    queries = load_queries()
    outcomes: dict[str, dict[str, bool]] = {}
    for surface in SURFACES:
        corpus = load_corpus(surface)
        names = {
            row["name"]
            for kind in ("tools", "skills")
            for row in corpus[kind]
        }
        scoped = [
            entry for entry in queries
            if entry["expected"]["name"] in names
            and all(n in names for n in entry.get("negatives") or [])
        ]
        results, _aggregates = run_eval.evaluate(corpus, scoped)
        outcomes[surface] = {r.entry_id: r.top1_hit for r in results}
    return outcomes


def lexical_agreement(judge_map: dict, lexical_map: dict[str, bool]) -> tuple[float | None, list[str]]:
    """Agreement between lexical and judge top-1 over common queries.

    Args:
        judge_map: Judge trace records keyed by query_id.
        lexical_map: Lexical top-1 outcomes keyed by query_id.

    Returns:
        (agreement rate, sorted disagreement ids); (None, []) when no query
        is common to both.
    """
    common = sorted(set(judge_map) & set(lexical_map))
    if not common:
        return None, []
    disagree = [
        q for q in common
        if bool(judge_map[q].get("top1_hit")) != lexical_map[q]
    ]
    return 1.0 - len(disagree) / len(common), disagree


def probe_agreement(artifacts_dir: Path, model_id: str, surface: str) -> dict | None:
    """First-pick agreement across repeats from one probe JSONL.

    Args:
        artifacts_dir: Artifact directory.
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.

    Returns:
        Agreement rate, query count and repeat count; None when no probe
        records exist.
    """
    _header, lines = load_trace(probe_path_for(artifacts_dir, model_id, surface))
    if not lines:
        return None
    firsts: dict[str, list] = {}
    for record in lines:
        parsed = record.get("parsed")
        firsts.setdefault(record.get("query_id", ""), []).append(
            parsed.get("first") if isinstance(parsed, dict) else None
        )
    per_query = {
        q: Counter(values).most_common(1)[0][1] / len(values)
        for q, values in firsts.items()
    }
    return {
        "agreement": sum(per_query.values()) / len(per_query),
        "n_queries": len(per_query),
        "repeats": max(len(v) for v in firsts.values()),
    }


def surface_cost(records: dict, model_id: str, prices: dict) -> dict:
    """Token and cost totals for one (model, surface) trace.

    Args:
        records: Trace records keyed by query_id.
        model_id: Judge model id (for the price table).
        prices: The ``prices`` block of ``judge_config.yaml``.

    Returns:
        Token totals, API calls and estimated cost (None without prices).
    """
    prompt = sum(int(r.get("prompt_tokens") or 0) for r in records.values())
    completion = sum(int(r.get("completion_tokens") or 0) for r in records.values())
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "api_calls": sum(int(r.get("api_calls") or 1) for r in records.values()),
        "cost": estimate_cost(model_id, prompt, completion, prices),
    }


# --------------------------------------------------------------------------- #
# Report assembly + rendering.
# --------------------------------------------------------------------------- #
def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    return "n/a" if ci is None else f"[{ci[0]:.4f}, {ci[1]:.4f}]"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.4f}"


def _fmt_p(p: float | None) -> str:
    return "n/a" if p is None else f"{p:.4g}"


def _paired_table(per_model: dict, key: str) -> list[str]:
    """Render one paired-outcome table (top-1 or top-3) across models."""
    lines = [
        "| model | n pairs | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        block = stats[key]
        if block["n_pairs"] == 0:
            lines.append(f"| {model_id} | 0 | n/a | n/a | n/a | - | - | n/a |")
            continue
        lines.append(
            f"| {model_id} | {block['n_pairs']} "
            f"| {_fmt_rate(block['baseline_rate'])} {_fmt_ci(block['baseline_ci'])} "
            f"| {_fmt_rate(block['post_rate'])} {_fmt_ci(block['post_ci'])} "
            f"| {_fmt_delta(block['delta'])} | {block['n_improved']} "
            f"| {block['n_regressed']} | {_fmt_p(block['mcnemar_p'])} |"
        )
    return lines


def build_stats_report(artifacts_dir: Path, config: dict | None = None) -> str:
    """Assemble the full panel statistics report from on-disk artifacts.

    Args:
        artifacts_dir: Directory holding traces, probes and the output.
        config: Parsed judge config; loaded from disk when omitted.

    Returns:
        The markdown report string (deterministic given the artifacts).
    """
    config = config or load_config(CONFIG_PATH)
    models = config["models"]
    prices = config.get("prices", {})
    lexical = lexical_top1()

    per_model: dict[str, dict] = {}
    pooled_rows: list[PairRow] = []
    for model in models:
        model_id = model["id"]
        maps = {
            surface: load_query_map(artifacts_dir, model_id, surface)
            for surface in SURFACES
        }
        rows = build_pairs(maps["baseline"], maps["post"])
        pooled_rows.extend(rows)
        per_model[model_id] = {
            "role": model.get("role"),
            "maps": maps,
            "rows": rows,
            "top1": paired_block(rows, "baseline_top1", "post_top1"),
            "top3": paired_block(rows, "baseline_top3", "post_top3"),
            "neg": rate_delta(maps["baseline"], maps["post"], "neg_false_recall"),
            "invalid": rate_delta(maps["baseline"], maps["post"], "invalid"),
            "flips": flip_rows(rows),
            "lexical": {
                surface: lexical_agreement(maps[surface], lexical[surface])
                for surface in SURFACES
            },
            "cost": {
                surface: surface_cost(maps[surface], model_id, prices)
                for surface in SURFACES
            },
            "probes": {
                surface: probe_agreement(artifacts_dir, model_id, surface)
                for surface in SURFACES
            },
        }
    pooled = {
        "top1": paired_block(pooled_rows, "baseline_top1", "post_top1"),
        "top3": paired_block(pooled_rows, "baseline_top3", "post_top3"),
    }
    return _render(config, per_model, pooled)


def _render(config: dict, per_model: dict, pooled: dict) -> str:
    """Render the markdown report body.

    Args:
        config: Parsed judge config (panel + pins).
        per_model: Per-model stats from ``build_stats_report``.
        pooled: Pooled paired blocks.

    Returns:
        The markdown report string.
    """
    budget = config["budget"]
    lines = [
        "# LLM-judge panel statistics report",
        "",
        "Paired design: every query is its own control across the frozen",
        "baseline corpus and the current post corpus. Headline test: exact",
        "McNemar (two-sided binomial on discordant pairs); Wilson 95% CIs per",
        "surface. Deterministic given the golden traces.",
        "",
        f"- prompt template sha256: `{prompt_template_sha256()}`",
        "",
        "## Panel and pins",
        "",
        "| model | role | provider | temperature | max_response_tokens |",
        "|---|---|---|---|---|",
    ]
    for model in config["models"]:
        lines.append(
            f"| {model['id']} | {model.get('role', '-')} | {model['provider']}"
            f" | {model['temperature']} | {model['max_response_tokens']} |"
        )
    lines += [
        "",
        f"- budget caps per (model, surface) run: "
        f"{budget['max_input_tokens_per_model_run']} tokens / "
        f"{budget['max_calls_per_model_run']} calls",
        "- price table: **estimate:true** — cost figures below are unverified",
        "",
        "## Per-model paired results (exact McNemar)",
        "",
        "### Top-1",
        "",
    ]
    lines += _paired_table(per_model, "top1")
    lines += ["", "### Top-3", ""]
    lines += _paired_table(per_model, "top3")
    lines += [
        "",
        "### Negative false-recall and invalid responses (plain rates)",
        "",
        "| model | neg baseline | neg post | Δ | invalid baseline | invalid post | Δ |",
        "|---|---|---|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        neg, invalid = stats["neg"], stats["invalid"]
        lines.append(
            f"| {model_id} | {_fmt_rate(neg['baseline'])} ({neg['baseline_n']})"
            f" | {_fmt_rate(neg['post'])} ({neg['post_n']}) | {_fmt_delta(neg['delta'])}"
            f" | {_fmt_rate(invalid['baseline'])} ({invalid['baseline_n']})"
            f" | {_fmt_rate(invalid['post'])} ({invalid['post_n']})"
            f" | {_fmt_delta(invalid['delta'])} |"
        )

    with_pairs = {m: s for m, s in per_model.items() if s["top1"]["n_pairs"]}
    improved = sum(1 for s in with_pairs.values() if (s["top1"]["delta"] or 0) > 0)
    regressed = sum(1 for s in with_pairs.values() if (s["top1"]["delta"] or 0) < 0)
    flat = len(with_pairs) - improved - regressed
    p1, p3 = pooled["top1"], pooled["top3"]
    lines += [
        "",
        "## Pooled across models",
        "",
        "Model is a stratification variable here: pooling assumes the",
        "description change acts in the same direction across judges. Check",
        "the per-model table for heterogeneity before trusting pooled p-values.",
        "",
        f"- per-model heterogeneity (top-1 Δ): {improved} improved / "
        f"{regressed} regressed / {flat} flat "
        f"(of {len(with_pairs)} models with paired data)",
        f"- pooled pairs: {p1['n_pairs']}",
        f"- pooled top-1: baseline {_fmt_rate(p1['baseline_rate'])} -> post "
        f"{_fmt_rate(p1['post_rate'])}, Δ {_fmt_delta(p1['delta'])}, "
        f"improved {p1['n_improved']} / regressed {p1['n_regressed']}, "
        f"McNemar p {_fmt_p(p1['mcnemar_p'])}",
        f"- pooled top-3: baseline {_fmt_rate(p3['baseline_rate'])} -> post "
        f"{_fmt_rate(p3['post_rate'])}, Δ {_fmt_delta(p3['delta'])}, "
        f"improved {p3['n_improved']} / regressed {p3['n_regressed']}, "
        f"McNemar p {_fmt_p(p3['mcnemar_p'])}",
        "",
        "## Flip lists (top-1 outcome flipped between surfaces)",
        "",
    ]
    for model_id, stats in per_model.items():
        flips = stats["flips"]
        lines.append(f"### {model_id} ({len(flips)} flips)")
        lines.append("")
        if not flips:
            lines += ["(none)", ""]
            continue
        lines += [
            "| query_id | direction | baseline first | post first |",
            "|---|---|---|---|",
        ]
        for flip in flips:
            lines.append(
                f"| {flip['query_id']} | {flip['direction']}"
                f" | {flip['baseline_first']} | {flip['post_first']} |"
            )
        lines.append("")

    lines += [
        "## Lexical-vs-semantic agreement (run_eval proxy blind spots)",
        "",
        "Agreement of the lexical top-1 outcome with the LLM-judge top-1",
        "outcome per query; disagreements are where the lexical proxy cannot",
        "see what the judge sees.",
        "",
        "| model | surface | agreement | disagreements |",
        "|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        for surface in SURFACES:
            agreement, disagree = stats["lexical"][surface]
            lines.append(
                f"| {model_id} | {surface} | {_fmt_rate(agreement)}"
                f" | {len(disagree)} |"
            )
    for model_id, stats in per_model.items():
        for surface in SURFACES:
            _agreement, disagree = stats["lexical"][surface]
            if disagree:
                lines += [
                    "",
                    f"disagreement set — {model_id} / {surface}: "
                    + ", ".join(disagree),
                ]

    lines += [
        "",
        "## Determinism audit (probe first-pick agreement)",
        "",
        "| model | surface | queries | repeats | agreement | confidence |",
        "|---|---|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        for surface in SURFACES:
            probe = stats["probes"][surface]
            if probe is None:
                lines.append(f"| {model_id} | {surface} | - | - | n/a | no probe |")
                continue
            flag = "ok" if probe["agreement"] >= PROBE_CONFIDENCE_FLOOR else (
                f"REDUCED-CONFIDENCE (<{PROBE_CONFIDENCE_FLOOR:.0%})"
            )
            lines.append(
                f"| {model_id} | {surface} | {probe['n_queries']}"
                f" | {probe['repeats']} | {probe['agreement']:.4f} | {flag} |"
            )

    lines += [
        "",
        "## Invalid-response audit",
        "",
        "| model | surface | invalid | scored | rate |",
        "|---|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        for surface in SURFACES:
            records = stats["maps"][surface]
            invalid = sum(1 for r in records.values() if r.get("parsed") is None)
            scored = len(records)
            rate = invalid / scored if scored else None
            lines.append(
                f"| {model_id} | {surface} | {invalid} | {scored}"
                f" | {_fmt_rate(rate)} |"
            )

    lines += [
        "",
        "## Cost summary (estimate:true — verify prices before quoting)",
        "",
        "| model | surface | api calls | prompt tokens | completion tokens | est. cost USD |",
        "|---|---|---|---|---|---|",
    ]
    for model_id, stats in per_model.items():
        for surface in SURFACES:
            cost = stats["cost"][surface]
            cost_text = "n/a" if cost["cost"] is None else f"${cost['cost']:.4f}"
            lines.append(
                f"| {model_id} | {surface} | {cost['api_calls']}"
                f" | {cost['prompt_tokens']} | {cost['completion_tokens']}"
                f" | {cost_text} |"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: write artifacts/llm_judge_stats_report.md.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--artifacts-dir", default=None,
        help="artifact directory (default: the suite's own artifacts/)",
    )
    args = parser.parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else ARTIFACTS_DIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = build_stats_report(artifacts_dir)
    output = artifacts_dir / "llm_judge_stats_report.md"
    output.write_text(report, encoding="utf-8")
    print(f"stats report written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
