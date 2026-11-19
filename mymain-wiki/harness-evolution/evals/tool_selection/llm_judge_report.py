"""Aggregation and markdown reporting for the E2 LLM-judge evaluation.

Turns golden-trace records from ``run_llm_judge`` into the per-run report
metrics and the rendered markdown artifact. Pure and offline: no network,
no corpus access beyond what the caller passes in.
"""

from __future__ import annotations

from src.evals.tool_selection.llm_judge_protocol import prompt_template_sha256


def aggregate_lines(lines: list[dict], entries: list[dict]) -> dict:
    """Aggregate trace records into report metrics.

    Args:
        lines: Per-call trace records for one (model, surface) run.
        entries: Query entries (for the per-domain breakdown).

    Returns:
        Aggregate dict: headline counters, per-domain rows, token totals.
    """
    entry_by_id = {entry["id"]: entry for entry in entries}
    per_domain: dict[str, dict[str, int]] = {}
    for record in lines:
        entry = entry_by_id.get(record.get("query_id", ""))
        domain = entry["domain"] if entry else "unknown"
        row = per_domain.setdefault(domain, {"entries": 0, "top1": 0, "top3": 0})
        row["entries"] += 1
        row["top1"] += int(bool(record.get("top1_hit")))
        row["top3"] += int(bool(record.get("top3_hit")))
    neg_lines = [r for r in lines if r.get("neg_false_recall") is not None]
    return {
        "entries": len(lines),
        "top1_hits": sum(int(bool(r.get("top1_hit"))) for r in lines),
        "top3_hits": sum(int(bool(r.get("top3_hit"))) for r in lines),
        "invalid_responses": sum(1 for r in lines if r.get("parsed") is None),
        "neg_entries": len(neg_lines),
        "neg_false_recalls": sum(int(bool(r.get("neg_false_recall"))) for r in neg_lines),
        "per_domain": dict(sorted(per_domain.items())),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in lines),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in lines),
        "api_calls": sum(int(r.get("api_calls") or 1) for r in lines),
    }


def estimate_cost(
    model_id: str, prompt_tokens: int, completion_tokens: int, prices: dict
) -> float | None:
    """Estimate USD cost from the (placeholder) price table.

    Args:
        model_id: Judge model id.
        prompt_tokens: Total prompt tokens.
        completion_tokens: Total completion tokens.
        prices: The ``prices`` block of ``judge_config.yaml``.

    Returns:
        Estimated USD cost, or None when the model has no price row.
    """
    row = (prices or {}).get("per_million_tokens", {}).get(model_id)
    if not row:
        return None
    return (
        prompt_tokens / 1_000_000 * float(row["input"])
        + completion_tokens / 1_000_000 * float(row["output"])
    )


def render_report(
    *,
    model_cfg: dict,
    surface: str,
    corpus: dict,
    aggregates: dict,
    total_entries: int,
    prices: dict,
) -> str:
    """Render the markdown accuracy + cost report.

    Args:
        model_cfg: The judge model's config entry.
        surface: ``baseline`` or ``post``.
        corpus: The scored corpus snapshot.
        aggregates: Output of ``aggregate_lines``.
        total_entries: Total query entries in ``queries.yaml``.
        prices: The ``prices`` block of ``judge_config.yaml``.

    Returns:
        The full markdown report string.
    """
    scored = aggregates["entries"]
    cost = estimate_cost(
        model_cfg["id"],
        aggregates["prompt_tokens"],
        aggregates["completion_tokens"],
        prices,
    )
    lines = [
        f"# LLM-judge tool-selection report — {model_cfg['id']} / {surface}",
        "",
        f"- judge model: `{model_cfg['id']}` (role: {model_cfg.get('role', 'n/a')}, "
        f"temperature {model_cfg['temperature']}, "
        f"max_response_tokens {model_cfg['max_response_tokens']})",
        f"- surface: `{surface}` — corpus captured_at `{corpus['captured_at']}` "
        f"({corpus['tool_count']} tools + {corpus['skill_count']} skills)",
        f"- prompt template sha256: `{prompt_template_sha256()}`",
        f"- entries scored: {scored} / {total_entries}"
        + (" (PARTIAL run — see trace for coverage)" if scored < total_entries else ""),
        "",
        "## Aggregate scores",
        "",
        "| metric | value |",
        "|---|---|",
    ]

    def rate(hits: int) -> str:
        return f"{hits}/{scored} = {hits / scored:.4f}" if scored else f"{hits}/0 = n/a"

    lines += [
        f"| top-1 accuracy | {rate(aggregates['top1_hits'])} |",
        f"| top-3 hit rate | {rate(aggregates['top3_hits'])} |",
        f"| negative false-recall (conservative) | "
        f"{aggregates['neg_false_recalls']}/{aggregates['neg_entries']}"
        + (
            f" = {aggregates['neg_false_recalls'] / aggregates['neg_entries']:.4f} |"
            if aggregates["neg_entries"]
            else " = n/a |"
        ),
        f"| invalid responses (unparseable) | {aggregates['invalid_responses']} |",
        "",
        "## Per-domain breakdown",
        "",
        "| domain | entries | top-1 | top-3 | top-1 accuracy |",
        "|---|---|---|---|---|",
    ]
    for domain, row in aggregates["per_domain"].items():
        accuracy = row["top1"] / row["entries"] if row["entries"] else 0.0
        lines.append(
            f"| {domain} | {row['entries']} | {row['top1']} | {row['top3']}"
            f" | {accuracy:.4f} |"
        )
    lines += [
        "",
        "## Cost",
        "",
        f"- API calls: {aggregates['api_calls']} (retries counted individually)",
        f"- prompt tokens: {aggregates['prompt_tokens']}",
        f"- completion tokens: {aggregates['completion_tokens']}",
    ]
    if cost is None:
        lines.append("- estimated cost: n/a (no price row for this model)")
    else:
        lines.append(
            f"- estimated cost: ${cost:.4f} USD — **estimate:true**, price table "
            f"in judge_config.yaml must be verified before quoting externally"
        )
    lines += [
        "",
        "## Protocol notes",
        "",
        "- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.",
        f"- Golden trace: `llm_judge_trace_{model_cfg['id']}_{surface}.jsonl`.",
        "",
    ]
    return "\n".join(lines)
