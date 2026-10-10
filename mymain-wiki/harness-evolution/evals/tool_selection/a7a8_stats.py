"""Paired statistics + pre-registered verdict for the A7/A8 targeted runs.

Layered over the tested primitives in ``llm_judge_stats`` (``mcnemar_p``,
``wilson_ci``) and ``run_llm_judge`` (``load_trace``, ``trace_path_for``),
this module adds exactly what the A7/A8 test plan §6 needs and the generic
stats module does not have:

* **tagged traces** — reads the ``--tag``-namespaced baseline/post traces a
  targeted run writes, instead of the untagged full-surface traces;
* **lenient top-1** — the format-tolerant outcome recorded alongside the
  strict one, so format-only flips do not masquerade as routing changes;
* **baseline-failure recovery rate** — the fraction of baseline top-1 misses
  the post surface fixes; the metric most sensitive to a targeted improvement
  under a high ceiling;
* **the §6.1 pre-registered verdict** — real improvement requires the pooled
  target McNemar p < 0.05 AND Δtop-1 >= +3pp AND recovery >= 30% AND a
  non-regressing full set.

Usage:
    cd agent
    python -m src.evals.tool_selection.a7a8_stats --tag a7_target --label "A7 target"
    python -m src.evals.tool_selection.a7a8_stats --tag a7_full --label "A7 full"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.evals.tool_selection.llm_judge_stats import mcnemar_p, wilson_ci
from src.evals.tool_selection.run_llm_judge import (
    ARTIFACTS_DIR,
    load_trace,
    trace_path_for,
)

DEFAULT_MODELS = ("qwen3.8-max", "kimi-k3")

P_MIN = 0.05
DELTA_MIN = 0.03
RECOVERY_MIN = 0.30


def load_tagged_map(model_id: str, surface: str, tag: str,
                    artifacts_dir: Path) -> dict[str, dict]:
    """Load one tagged (model, surface) trace keyed by query_id.

    Args:
        model_id: Judge model id.
        surface: ``baseline`` or ``post``.
        tag: The run tag used when the trace was recorded.
        artifacts_dir: Artifact directory.

    Returns:
        query_id -> trace record (empty when the trace is missing).
    """
    path = trace_path_for(artifacts_dir, model_id, surface, tag)
    _header, lines = load_trace(path)
    return {r["query_id"]: r for r in lines if r.get("query_id")}


def build_pairs(baseline_map: dict, post_map: dict) -> list[dict]:
    """Align two tagged surface traces by query_id.

    Args:
        baseline_map: Baseline records keyed by query_id.
        post_map: Post records keyed by query_id.

    Returns:
        One dict per common query_id with strict and lenient top-1 outcomes.
    """
    pairs = []
    for query_id in sorted(set(baseline_map) & set(post_map)):
        base, post = baseline_map[query_id], post_map[query_id]
        pairs.append({
            "query_id": query_id,
            "base_top1": bool(base.get("top1_hit")),
            "post_top1": bool(post.get("top1_hit")),
            "base_top1_len": bool(base.get("top1_hit_lenient")),
            "post_top1_len": bool(post.get("top1_hit_lenient")),
            "base_first": (base.get("parsed") or {}).get("first"),
            "post_first": (post.get("parsed") or {}).get("first"),
        })
    return pairs


def paired_metrics(pairs: list[dict], base_key: str, post_key: str) -> dict:
    """Paired comparison of one outcome across surfaces.

    Args:
        pairs: Aligned pair dicts.
        base_key: Pair key for the baseline outcome.
        post_key: Pair key for the post outcome.

    Returns:
        n_pairs, rates, Wilson CIs, improved/regressed, delta, McNemar p.
    """
    n = len(pairs)
    base_hits = sum(int(p[base_key]) for p in pairs)
    post_hits = sum(int(p[post_key]) for p in pairs)
    improved = sum(int(p[post_key] and not p[base_key]) for p in pairs)
    regressed = sum(int(p[base_key] and not p[post_key]) for p in pairs)
    return {
        "n_pairs": n,
        "baseline_rate": base_hits / n if n else None,
        "post_rate": post_hits / n if n else None,
        "baseline_ci": wilson_ci(base_hits, n),
        "post_ci": wilson_ci(post_hits, n),
        "n_improved": improved,
        "n_regressed": regressed,
        "delta": (post_hits - base_hits) / n if n else None,
        "mcnemar_p": mcnemar_p(improved, regressed),
    }


def recovery_rate(pairs: list[dict], base_key: str, post_key: str) -> dict:
    """Baseline-failure recovery rate for one outcome.

    Args:
        pairs: Aligned pair dicts.
        base_key: Pair key for the baseline outcome.
        post_key: Pair key for the post outcome.

    Returns:
        baseline_failures, recovered, and the recovery rate (None when there
        is nothing to recover).
    """
    failures = [p for p in pairs if not p[base_key]]
    recovered = sum(int(p[post_key]) for p in failures)
    return {
        "baseline_failures": len(failures),
        "recovered": recovered,
        "rate": recovered / len(failures) if failures else None,
    }


def collect_pairs(models: tuple[str, ...], tag: str,
                  artifacts_dir: Path) -> tuple[dict[str, list[dict]], list[dict], bool]:
    """Collect per-model and pooled pairs for one tagged set.

    Args:
        models: Judge model ids.
        tag: The run tag.
        artifacts_dir: Artifact directory.

    Returns:
        (model_id -> pairs, pooled pairs, lenient_available). The last is
        False when the baseline traces predate the lenient field (e.g. a
        reused full-surface baseline), so the lenient view is suppressed
        rather than misread as all-miss.
    """
    per_model: dict[str, list[dict]] = {}
    pooled: list[dict] = []
    lenient_available = False
    for model_id in models:
        baseline_map = load_tagged_map(model_id, "baseline", tag, artifacts_dir)
        post_map = load_tagged_map(model_id, "post", tag, artifacts_dir)
        if any("top1_hit_lenient" in r for r in baseline_map.values()):
            lenient_available = True
        pairs = build_pairs(baseline_map, post_map)
        per_model[model_id] = pairs
        for p in pairs:
            pooled.append({**p, "query_id": f"{model_id}:{p['query_id']}"})
    return per_model, pooled, lenient_available


def _fmt_rate(rate: float | None) -> str:
    return "-" if rate is None else f"{rate:.4f}"


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    return "-" if ci is None else f"[{ci[0]:.4f}, {ci[1]:.4f}]"


def _fmt_p(p: float) -> str:
    return f"{p:.5f}"


def render_block_table(title: str, blocks: list[tuple[str, dict]]) -> list[str]:
    """Render one paired-metrics table.

    Args:
        title: Section heading.
        blocks: (label, paired-metrics dict) rows.

    Returns:
        Markdown lines.
    """
    lines = [
        f"### {title}",
        "",
        "| scope | n | baseline (95% CI) | post (95% CI) | Δ | improved | regressed | McNemar p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, m in blocks:
        lines.append(
            f"| {label} | {m['n_pairs']} | "
            f"{_fmt_rate(m['baseline_rate'])} {_fmt_ci(m['baseline_ci'])} | "
            f"{_fmt_rate(m['post_rate'])} {_fmt_ci(m['post_ci'])} | "
            f"{m['delta']:+.4f} | {m['n_improved']} | {m['n_regressed']} | "
            f"{_fmt_p(m['mcnemar_p'])} |"
        )
    return lines


def build_report(label: str, models: tuple[str, ...], tag: str,
                 artifacts_dir: Path) -> str:
    """Build the stats report for one tagged set.

    Args:
        label: Human label for the set (e.g. "A7 target").
        models: Judge model ids.
        tag: The run tag.
        artifacts_dir: Artifact directory.

    Returns:
        Markdown report text.
    """
    per_model, pooled, lenient_available = collect_pairs(models, tag, artifacts_dir)
    lines = [
        f"# {label} — paired results (tag `{tag}`)",
        "",
        f"Models: {', '.join(models)}",
        "",
    ]

    strict_rows = [(m, paired_metrics(p, "base_top1", "post_top1"))
                   for m, p in per_model.items() if p]
    strict_rows.append(("POOLED", paired_metrics(pooled, "base_top1", "post_top1")))
    lines += render_block_table("Strict top-1", strict_rows)
    lines.append("")

    rec_strict = recovery_rate(pooled, "base_top1", "post_top1")
    if lenient_available:
        lenient_rows = [(m, paired_metrics(p, "base_top1_len", "post_top1_len"))
                        for m, p in per_model.items() if p]
        lenient_rows.append(("POOLED", paired_metrics(pooled, "base_top1_len", "post_top1_len")))
        lines += render_block_table("Lenient top-1 (format-tolerant)", lenient_rows)
        lines.append("")
        rec_lenient = recovery_rate(pooled, "base_top1_len", "post_top1_len")
        lines += [
            "### Baseline-failure recovery rate (pooled)",
            "",
            "| outcome | baseline failures | recovered | recovery rate |",
            "|---|---|---|---|",
            f"| strict | {rec_strict['baseline_failures']} | {rec_strict['recovered']} "
            f"| {_fmt_rate(rec_strict['rate'])} |",
            f"| lenient | {rec_lenient['baseline_failures']} | {rec_lenient['recovered']} "
            f"| {_fmt_rate(rec_lenient['rate'])} |",
            "",
        ]
    else:
        lines += [
            "> Lenient top-1 suppressed: the baseline traces predate the "
            "format-tolerant field (reused full-surface baseline). Strict "
            "top-1 is the non-inferiority metric for this set.",
            "",
            "### Baseline-failure recovery rate (pooled, strict)",
            "",
            "| outcome | baseline failures | recovered | recovery rate |",
            "|---|---|---|---|",
            f"| strict | {rec_strict['baseline_failures']} | {rec_strict['recovered']} "
            f"| {_fmt_rate(rec_strict['rate'])} |",
            "",
        ]

    flips = [p for p in pooled if p["base_top1"] != p["post_top1"]]
    if flips:
        lines += ["### Strict top-1 flips", ""]
        for p in flips:
            direction = "improved" if p["post_top1"] else "regressed"
            lines.append(
                f"- `{p['query_id']}` {direction}: "
                f"`{p['base_first']}` -> `{p['post_first']}`"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="run tag of the set to analyse")
    parser.add_argument("--label", default=None, help="report label (defaults to tag)")
    parser.add_argument(
        "--models", default=",".join(DEFAULT_MODELS),
        help="comma-separated judge model ids",
    )
    args = parser.parse_args(argv)

    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    report = build_report(args.label or args.tag, models, args.tag, ARTIFACTS_DIR)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
