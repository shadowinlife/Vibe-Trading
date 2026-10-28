"""Paired-trace assembly for the B-batch verdict statistics.

Trace-consumption layer for ``b_batch_stats``: aligns the baseline/post
golden traces of each judge model into per-query pair dicts, projects them
into caliber-specific rows, and builds the descriptive absent-behavior
probe. Pure data shaping — no statistic is computed here.

Lenient fields may be absent on traces recorded before the format-tolerant
scoring existed; such pairs carry ``lenient_available = False`` and the
lenient view is suppressed rather than misread as all-miss (same behavior
as ``a7a8_stats``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class StrictPair:
    """One paired observation of a SINGLE caliber (strict by construction).

    The verdict path only ever sees these rows; lenient data is physically
    not reachable from a StrictPair, which is the structural guarantee that
    the sensitivity caliber cannot flip the primary verdict (gap ③ of
    ``artifacts/llm_judge_design.md``).
    """

    query_id: str
    base_hit: bool
    post_hit: bool


def build_pairs(baseline_map: dict, post_map: dict) -> list[dict]:
    """Align two surface traces by query_id, keeping verdict + probe fields.

    Args:
        baseline_map: Baseline records keyed by query_id.
        post_map: Post records keyed by query_id.

    Returns:
        One dict per common query_id (sorted), with strict/lenient top-1
        outcomes, first picks, the post top-3 and the expected id.
    """
    pairs = []
    for query_id in sorted(set(baseline_map) & set(post_map)):
        base, post = baseline_map[query_id], post_map[query_id]
        base_parsed = base.get("parsed") or {}
        post_parsed = post.get("parsed") or {}
        has_lenient = "top1_hit_lenient" in base and "top1_hit_lenient" in post
        post_top3 = [
            post_parsed.get(key)
            for key in ("first", "second", "third")
            if post_parsed.get(key)
        ]
        pairs.append(
            {
                "query_id": query_id,
                "base_top1": bool(base.get("top1_hit")),
                "post_top1": bool(post.get("top1_hit")),
                "base_top1_len": (
                    bool(base.get("top1_hit_lenient")) if has_lenient else None
                ),
                "post_top1_len": (
                    bool(post.get("top1_hit_lenient")) if has_lenient else None
                ),
                "lenient_available": has_lenient,
                "base_first": base_parsed.get("first"),
                "post_first": post_parsed.get("first"),
                "post_top3": post_top3,
                "expected_id": post.get("expected_id") or base.get("expected_id"),
            }
        )
    return pairs


def strict_rows(pairs: list[dict]) -> list[StrictPair]:
    """Project pair dicts to strict-caliber rows (the only verdict input).

    Args:
        pairs: Pair dicts from ``build_pairs``.

    Returns:
        StrictPair rows (strict top-1 outcomes only).
    """
    return [StrictPair(p["query_id"], p["base_top1"], p["post_top1"]) for p in pairs]


def lenient_rows(pairs: list[dict]) -> list[StrictPair]:
    """Project lenient-available pair dicts to sensitivity rows.

    Args:
        pairs: Pair dicts from ``build_pairs``.

    Returns:
        Rows for the pairs whose traces carry the lenient field; pairs
        without it are suppressed, never read as all-miss.
    """
    return [
        StrictPair(p["query_id"], bool(p["base_top1_len"]), bool(p["post_top1_len"]))
        for p in pairs
        if p["lenient_available"]
    ]


def absent_behavior_probe(pairs: list[dict]) -> dict:
    """Descriptive probe for queries whose expected capability is absent.

    Distribution-only by pre-registration (B test plan §5.3): no accuracy
    claim is computable on an absent expected target. The one structural
    guard is counted: a top-3 pick equal to the (absent) expected id would
    mean the model "called" a removed capability — impossible while the
    candidate list omits it, and reported as C5 evidence.

    Args:
        pairs: Absent-set pair dicts from ``build_pairs``.

    Returns:
        Probe dict: ids, post first-pick distribution, per-row picks and
        the removed-capability pick event count.
    """
    distribution = Counter(
        p["post_first"] if p["post_first"] else "(none/invalid)" for p in pairs
    )
    events = sum(
        1 for p in pairs if p["expected_id"] and p["expected_id"] in p["post_top3"]
    )
    return {
        "n_queries": len(pairs),
        "query_ids": [p["query_id"] for p in pairs],
        "post_first_distribution": dict(distribution.most_common()),
        "removed_capability_pick_events": events,
        "rows": [
            {
                "query_id": p["query_id"],
                "expected_id": p["expected_id"],
                "post_first": p["post_first"],
            }
            for p in pairs
        ],
    }
