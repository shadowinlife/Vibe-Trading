"""Pre-registered B-batch verdict statistics for the LLM-judge evaluation.

Closes the four methodology gaps of ``artifacts/llm_judge_design.md``
("Known methodology gaps", 2026-08-27 review) exactly as pre-registered in
``HARNESS_EVOLUTION_B_TEST_PLAN.md`` §5 (criteria frozen 2026-08-27, before
any B-batch experiment):

* **gap ① power-aligned thresholds** — the primary efficacy surface is the
  full query set minus the absent set (§1 K9), pooled across the two panel
  models; no post-hoc target-set criteria exist here.
* **gap ② margin-based non-inferiority** — C1 passes iff the EXACT 95% CI
  lower bound of the pooled paired difference Δ is > −δ (``--margin``,
  default 0.05). "No significant regression" is never treated as evidence
  of non-inferiority.
* **gap ③ named primary caliber** — strict top-1 is the single primary
  caliber. The enforcement is structural: ``overall_verdict`` and
  ``non_inferior_pass`` receive strict rows only (``StrictPair``); lenient
  scores are computed separately and reported in a clearly-labeled
  sensitivity section (C4) that can never flip the strict verdict.
* **gap ④ judge test-retest noise floor** — ``--noise-band X``: when the
  pooled |Δtop-1| ≤ X the verdict line reads "within noise band —
  uninterpretable, recorded as no effect" (a third state distinct from
  PASS/FAIL). The band comes from ``retest_noise.py`` (two independent
  probe administrations, B test plan §5.2).

Statistical construction (frozen — the exact CI method is pinned here):

    Δ = (b − c) / n with b = improved pairs (baseline miss -> post hit),
    c = regressed pairs, n = ALL paired observations. Conditional on the
    observed discordant count d = b + c, the improved count b is
    Binomial(d, q) where q is the probability that a discordant pair is an
    improvement, and Δ = d(2q − 1)/n is linear in q. The verdict therefore
    uses the EXACT CLOPPER-PEARSON binomial interval (Clopper & Pearson,
    1934; ``scipy.stats.binomtest(b, d).proportion_ci(method="exact")``)
    for q and transforms it to the Δ scale:
        Δ ∈ [ d(2·q_lo − 1)/n , d(2·q_hi − 1)/n ].
    With d = 0 the observed Δ is exactly 0 and the interval degenerates to
    [0, 0]. The exact McNemar p (two-sided binomial on the discordant
    pairs) is REPORTED alongside but never enters the verdict.

Expected-absent handling (B test plan §5.3): ``--absent-ids`` names queries
whose expected capability is absent from the post surface. Those rows are
EXCLUDED from the primary efficacy set and reported separately as a
descriptive "absent-behavior probe" (what the model picked instead,
distribution only — no accuracy claim).

Usage:
    cd agent
    python -m src.evals.tool_selection.b_batch_stats \\
        --tag b_batch --margin 0.05 --noise-band 0.0834 \\
        --absent-ids D01-007,D11-001,D11-002,D16-001,...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scipy.stats import binomtest

from src.evals.tool_selection.a7a8_stats import load_tagged_map
from src.evals.tool_selection.b_batch_pairs import (
    StrictPair,
    absent_behavior_probe,
    build_pairs,
    lenient_rows,
    strict_rows,
)
from src.evals.tool_selection.b_batch_report import render_verdict_report
from src.evals.tool_selection.llm_judge_stats import mcnemar_p
from src.evals.tool_selection.run_llm_judge import ARTIFACTS_DIR

DEFAULT_MODELS = ("qwen3.8-max", "kimi-k3")
DEFAULT_MARGIN = 0.05

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOISE = "within noise band — uninterpretable, recorded as no effect"


def exact_delta_ci(n_improved: int, n_regressed: int, n_pairs: int) -> tuple[float, float] | None:
    """Exact 95% CI for the paired difference Δ (see module docstring).

    Clopper-Pearson exact binomial interval on the discordant-direction
    proportion, transformed to the Δ scale.

    Args:
        n_improved: b — baseline miss -> post hit pairs.
        n_regressed: c — baseline hit -> post miss pairs.
        n_pairs: n — all paired observations.

    Returns:
        (lower, upper) on the Δ scale; (0.0, 0.0) with no discordant pairs;
        None when n_pairs == 0.
    """
    if n_pairs == 0:
        return None
    discordant = n_improved + n_regressed
    if discordant == 0:
        return (0.0, 0.0)
    ci = binomtest(n_improved, discordant).proportion_ci(method="exact")
    return (
        discordant * (2.0 * ci.low - 1.0) / n_pairs,
        discordant * (2.0 * ci.high - 1.0) / n_pairs,
    )


def paired_block(rows: list[StrictPair]) -> dict:
    """Paired statistics for one caliber's rows.

    Args:
        rows: StrictPair rows of a single caliber.

    Returns:
        n_pairs, rates, delta, discordant counts, exact Δ CI, and the exact
        McNemar p (report-only — never verdict-driving).
    """
    n = len(rows)
    base_hits = sum(1 for r in rows if r.base_hit)
    post_hits = sum(1 for r in rows if r.post_hit)
    improved = sum(1 for r in rows if r.post_hit and not r.base_hit)
    regressed = sum(1 for r in rows if r.base_hit and not r.post_hit)
    return {
        "n_pairs": n,
        "baseline_rate": base_hits / n if n else None,
        "post_rate": post_hits / n if n else None,
        "delta": (post_hits - base_hits) / n if n else None,
        "n_improved": improved,
        "n_regressed": regressed,
        "delta_ci": exact_delta_ci(improved, regressed, n),
        "mcnemar_p": mcnemar_p(improved, regressed),
    }


def non_inferior_pass(block: dict, margin: float) -> bool | None:
    """C1 decision for one pooled block: exact CI lower bound > −margin.

    Args:
        block: ``paired_block`` output of the PRIMARY (strict) caliber.
        margin: Non-inferiority margin δ (positive, e.g. 0.05).

    Returns:
        True/False, or None when the block has no pairs (non-inferiority
        cannot be established — treated as not passed by the caller).
    """
    ci = block["delta_ci"]
    if ci is None:
        return None
    return ci[0] > -margin


def overall_verdict(c1_pass: bool | None, pooled_delta: float | None, noise_band: float | None) -> str:
    """Final verdict line from the strict primary caliber alone.

    Args:
        c1_pass: C1 outcome (strict pooled non-inferiority).
        pooled_delta: Pooled strict Δ (C2 input).
        noise_band: Noise band X; None disables the C2 interpretation rule.

    Returns:
        VERDICT_NOISE when |Δ| ≤ X (recorded as no effect — distinct from
        PASS/FAIL); otherwise PASS/FAIL from C1, failing closed on no data.
    """
    if noise_band is not None and pooled_delta is not None and abs(pooled_delta) <= noise_band:
        return VERDICT_NOISE
    return VERDICT_PASS if c1_pass else VERDICT_FAIL


def build_result(
    models: tuple[str, ...],
    tag: str | None,
    artifacts_dir: Path,
    absent_ids: frozenset[str],
    margin: float,
    noise_band: float | None,
) -> dict:
    """Assemble the full pre-registered verdict result (C1-C5).

    Args:
        models: Judge model ids (panel order).
        tag: Trace tag (None = untagged full-surface traces).
        artifacts_dir: Artifact directory holding the golden traces.
        absent_ids: Query ids excluded from the primary efficacy set.
        margin: Non-inferiority margin δ.
        noise_band: Noise band X (None disables the C2 rule).

    Returns:
        JSON-serializable result dict consumed by ``b_batch_report``.
    """
    per_model: dict[str, dict] = {}
    pooled_efficacy: list[dict] = []
    pooled_absent: list[dict] = []
    all_paired_ids: set[str] = set()
    for model_id in models:
        baseline_map = load_tagged_map(model_id, "baseline", tag, artifacts_dir)
        post_map = load_tagged_map(model_id, "post", tag, artifacts_dir)
        pairs = build_pairs(baseline_map, post_map)
        all_paired_ids.update(p["query_id"] for p in pairs)
        efficacy = [p for p in pairs if p["query_id"] not in absent_ids]
        absent = [p for p in pairs if p["query_id"] in absent_ids]
        per_model[model_id] = {
            "efficacy_block": paired_block(strict_rows(efficacy)),
            "lenient_block": paired_block(lenient_rows(efficacy)),
            "lenient_available": any(p["lenient_available"] for p in efficacy),
            "absent_probe": absent_behavior_probe(absent),
        }
        pooled_efficacy.extend({**p, "query_id": f"{model_id}:{p['query_id']}"} for p in efficacy)
        pooled_absent.extend({**p, "query_id": f"{model_id}:{p['query_id']}"} for p in absent)

    pooled_block = paired_block(strict_rows(pooled_efficacy))
    c1_pass = non_inferior_pass(pooled_block, margin)
    lenient_available = any(e["lenient_available"] for e in per_model.values())
    lenient_block = paired_block(lenient_rows(pooled_efficacy)) if lenient_available else None
    absent_probe = absent_behavior_probe(pooled_absent)
    return {
        "schema": "b_batch_verdict/1",
        "verdict": overall_verdict(c1_pass, pooled_block["delta"], noise_band),
        "config": {
            "tag": tag,
            "models": list(models),
            "artifacts_dir": str(artifacts_dir),
            "margin": margin,
            "noise_band": noise_band,
            "absent_ids": sorted(absent_ids),
            "absent_ids_not_found": sorted(absent_ids - all_paired_ids),
            "primary_caliber": "strict top-1 (lenient is sensitivity-only, C4)",
            "ci_construction": "exact Clopper-Pearson binomial CI on the "
            "discordant-direction proportion, transformed to the delta scale",
        },
        "criteria": {
            "C1": {
                "criterion": "pooled strict non-inferiority, primary efficacy set",
                "threshold": f"exact 95% CI lower bound > -{margin:g}",
                "role": "primary gate",
                "pass": bool(c1_pass) if c1_pass is not None else False,
                "has_data": c1_pass is not None,
                "stats": pooled_block,
            },
            "C2": {
                "criterion": "noise-band interpretation rule",
                "role": "primary interpretation rule",
                "noise_band": noise_band,
                "pooled_abs_delta": (abs(pooled_block["delta"]) if pooled_block["delta"] is not None else None),
                "within_band": (
                    noise_band is not None
                    and pooled_block["delta"] is not None
                    and abs(pooled_block["delta"]) <= noise_band
                ),
            },
            "C3": {
                "criterion": "per-model strict non-inferiority",
                "role": "report only — no gate authority",
                "per_model": {
                    model_id: {
                        "pass": (non_inferior_pass(entry["efficacy_block"], margin) is True),
                        "stats": entry["efficacy_block"],
                    }
                    for model_id, entry in per_model.items()
                },
            },
            "C4": {
                "criterion": "lenient sensitivity",
                "role": "report only — can never flip C1 (structural)",
                "available": lenient_available,
                "pooled": lenient_block,
                "flips_c1": False,
            },
            "C5": {
                "criterion": "absent-behavior probe (descriptive, no accuracy claim)",
                "role": "auxiliary",
                "pass": absent_probe["removed_capability_pick_events"] == 0,
                "probe": absent_probe,
            },
        },
        "primary_efficacy": {
            "n_pairs_pooled": pooled_block["n_pairs"],
            "per_model_n_pairs": {
                model_id: entry["efficacy_block"]["n_pairs"] for model_id, entry in per_model.items()
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: write the verdict JSON + markdown report.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok, 2 configuration error). The verdict itself
        is data in the artifacts, never an exit-code failure.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tag",
        default=None,
        help="trace tag of the B-batch run (default: untagged traces)",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated judge model ids",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN,
        help="non-inferiority margin delta (default 0.05)",
    )
    parser.add_argument(
        "--noise-band",
        type=float,
        default=None,
        help="judge noise band X from retest_noise.py; pooled " "|delta| <= X reads as no effect (C2)",
    )
    parser.add_argument(
        "--absent-ids",
        default="",
        help="comma-separated query ids whose expected " "capability is absent from the post surface",
    )
    parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="artifact directory (default: the suite's own artifacts/)",
    )
    parser.add_argument(
        "--output-prefix",
        default="b_batch_verdict",
        help="output file prefix (.json + .md)",
    )
    args = parser.parse_args(argv)

    if args.margin <= 0:
        print("error: --margin must be positive", file=sys.stderr)
        return 2
    if args.noise_band is not None and not 0.0 <= args.noise_band <= 1.0:
        print("error: --noise-band must be within [0, 1]", file=sys.stderr)
        return 2
    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    absent_ids = frozenset(i.strip() for i in args.absent_ids.split(",") if i.strip())
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else ARTIFACTS_DIR
    result = build_result(models, args.tag, artifacts_dir, absent_ids, args.margin, args.noise_band)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifacts_dir / f"{args.output_prefix}.json"
    md_path = artifacts_dir / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_verdict_report(result), encoding="utf-8")
    c1 = result["criteria"]["C1"]
    if c1["has_data"]:
        block = c1["stats"]
        ci = block["delta_ci"]
        print(
            f"C1 pooled strict non-inferiority: delta {block['delta']:+.4f} "
            f"(n={block['n_pairs']}), exact 95% CI [{ci[0]:+.4f}, {ci[1]:+.4f}] "
            f"-> {'PASS' if c1['pass'] else 'FAIL'} at margin {args.margin:g}"
        )
    else:
        print("C1: no paired data — non-inferiority cannot be established")
    print(f"VERDICT: {result['verdict']}")
    print(f"verdict JSON: {json_path}\nverdict report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
