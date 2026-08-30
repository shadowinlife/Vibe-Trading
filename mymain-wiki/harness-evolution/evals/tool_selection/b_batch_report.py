"""Markdown rendering of the B-batch pre-registered verdict.

Renders the result dict assembled by ``b_batch_stats.build_result`` into the
verdict report (``artifacts/b_batch_verdict.md`` by default), naming the
pre-registered criteria C1-C5 of ``HARNESS_EVOLUTION_B_TEST_PLAN.md`` §5.5
(frozen 2026-08-27). Pure rendering: no statistics happen here, and the
rendering is deterministic given the result dict.
"""

from __future__ import annotations


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _fmt_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.4f}"


def _fmt_ci(ci: tuple[float, float] | list[float] | None) -> str:
    return "n/a" if ci is None else f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"


def _fmt_p(p: float | None) -> str:
    return "n/a" if p is None else f"{p:.4g}"


def _stats_row(label: str, block: dict, margin: float | None = None) -> str:
    """One table row for a paired block; optionally appends the C1 verdict."""
    ci = block["delta_ci"]
    verdict = ""
    if margin is not None:
        if ci is None:
            verdict = " | no data"
        else:
            verdict = f" | {'PASS' if ci[0] > -margin else 'FAIL'}"
    return (
        f"| {label} | {block['n_pairs']} | {_fmt_rate(block['baseline_rate'])} "
        f"| {_fmt_rate(block['post_rate'])} | {_fmt_delta(block['delta'])} "
        f"| {block['n_improved']} | {block['n_regressed']} | {_fmt_ci(ci)} "
        f"| {_fmt_p(block['mcnemar_p'])}{verdict} |"
    )


def _stats_header(with_verdict: bool, verdict_col: str = "NI verdict") -> list[str]:
    suffix = f" {verdict_col} |" if with_verdict else ""
    return [
        "| scope | n pairs | baseline | post | Δ | improved | regressed "
        f"| exact 95% CI on Δ | McNemar p (report only) |{suffix}",
        "|---|---|---|---|---|---|---|---|---|" + ("---|" if with_verdict else ""),
    ]


def render_verdict_report(result: dict) -> str:
    """Render the full B-batch verdict report.

    Args:
        result: The ``b_batch_stats.build_result`` output.

    Returns:
        Markdown report text naming criteria C1-C5.
    """
    config = result["config"]
    criteria = result["criteria"]
    c1, c2, c3, c4, c5 = (criteria[key] for key in ("C1", "C2", "C3", "C4", "C5"))
    margin = config["margin"]
    noise_band_text = "n/a (not supplied)" if config["noise_band"] is None else f"{config['noise_band']:g}"
    lines = [
        "# B-batch L3 — pre-registered verdict report",
        "",
        "Criteria source: `HARNESS_EVOLUTION_B_TEST_PLAN.md` §5.5 "
        "(pre-registered, frozen 2026-08-27 — thresholds were fixed before "
        "the experiment and are never adjusted post-hoc).",
        "",
        f"- traces tag: `{config['tag']}`" if config["tag"] else "- traces: untagged",
        f"- models: {', '.join(config['models'])}",
        f"- primary caliber: {config['primary_caliber']}",
        f"- non-inferiority margin δ: {margin:g}",
        f"- noise band X: {noise_band_text}",
        f"- CI construction: {config['ci_construction']}",
        "",
        "## VERDICT",
        "",
        f"**{result['verdict']}**",
        "",
    ]

    lines += ["## C1 — pooled strict non-inferiority (PRIMARY GATE)", ""]
    lines += _stats_header(with_verdict=True)
    lines.append(_stats_row("POOLED strict", c1["stats"], margin))
    lines += [
        "",
        f"Threshold: exact 95% CI lower bound > −{margin:g}. "
        f"Result: **{'PASS' if c1['pass'] else 'FAIL'}**"
        + (
            ""
            if c1["has_data"]
            else " (no paired data — non-inferiority " "cannot be established, treated as not passed)"
        ),
        "",
    ]

    lines += ["## C2 — noise-band interpretation rule", ""]
    if c2["noise_band"] is None:
        lines.append(
            "No noise band supplied (`--noise-band`); the rule is not applied. "
            "Feed it the test-retest agreement from `retest_noise.py` "
            "(band ≥ max(1 − ρ) across models, B test plan §5.2)."
        )
    else:
        lines.append(
            f"Pooled |Δ| = {_fmt_delta(c2['pooled_abs_delta'])} vs band "
            f"{c2['noise_band']:g} → "
            + (
                "**within noise band — uninterpretable, recorded as no effect** "
                "(neither improvement nor regression may be claimed)."
                if c2["within_band"]
                else "above the noise band; the delta is " "interpretable."
            )
        )
    lines.append("")

    lines += [
        "## C3 — per-model strict non-inferiority (report only, no gate authority)",
        "",
    ]
    lines += _stats_header(with_verdict=True)
    for model_id, row in c3["per_model"].items():
        lines.append(_stats_row(model_id, row["stats"], margin))
    lines.append("")

    lines += [
        "## C4 — lenient sensitivity (SENSITIVITY ONLY — can never flip C1)",
        "",
    ]
    if c4["available"] and c4["pooled"] is not None:
        lines += _stats_header(with_verdict=False)
        lines.append(_stats_row("POOLED lenient", c4["pooled"]))
        lines += [
            "",
            "Structural guarantee: the verdict functions receive strict rows "
            "only; this section is computed separately and cannot change C1.",
        ]
    else:
        lines.append(
            "Suppressed: the traces predate the format-tolerant " "lenient field (it would be misread as all-miss)."
        )
    lines.append("")

    probe = c5["probe"]
    lines += [
        "## C5 — absent-behavior probe (descriptive — no accuracy claim)",
        "",
        f"Excluded from the primary efficacy set: {probe['n_queries']} "
        f"pooled rows ({len(config['absent_ids'])} absent ids requested).",
        "",
        f"- removed-capability pick events (must be 0): "
        f"**{probe['removed_capability_pick_events']}** → "
        f"{'PASS' if c5['pass'] else 'FAIL'}",
    ]
    if config["absent_ids_not_found"]:
        lines.append("- requested absent ids never paired in the traces: " + ", ".join(config["absent_ids_not_found"]))
    if probe["n_queries"]:
        lines += [
            "",
            "What the model picked instead (distribution only):",
            "",
            "| post first pick | count |",
            "|---|---|",
        ]
        for pick, count in probe["post_first_distribution"].items():
            lines.append(f"| `{pick}` | {count} |")
        lines += [
            "",
            "| query_id | expected (absent) | post first pick |",
            "|---|---|---|",
        ]
        for row in probe["rows"]:
            lines.append(f"| {row['query_id']} | `{row['expected_id']}` | `{row['post_first']}` |")
    lines.append("")

    efficacy = result["primary_efficacy"]
    lines += [
        "## Primary efficacy set provenance",
        "",
        f"- pooled paired observations: {efficacy['n_pairs_pooled']}",
    ]
    for model_id, n_pairs in efficacy["per_model_n_pairs"].items():
        lines.append(f"- {model_id}: {n_pairs} pairs")
    lines += [
        "",
        "McNemar exact p values above are reported for context only; the "
        "verdict is driven solely by the C1 exact-CI lower bound (and the "
        "C2 noise-band interpretation rule).",
        "",
    ]
    return "\n".join(lines)
