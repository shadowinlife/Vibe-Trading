"""Frozen prompt protocol for the E2 LLM-judge evaluation.

The template, candidate format, response parsing and scoring definitions
used by ``run_llm_judge``. Everything here is deterministic and offline.
The sha256 of the frozen template is pinned in three places that must
agree: the trace header, ``artifacts/llm_judge_design.md`` and
``tests/test_llm_judge.py`` — any edit to the template is a protocol
change and must update all three.
"""

from __future__ import annotations

import hashlib
import json

# --------------------------------------------------------------------------- #
# Frozen prompt template. ANY edit changes prompt_template_sha256() and is a
# protocol change: record it in artifacts/llm_judge_design.md.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a strict tool router for a finance research agent. Given a user "
    "request and the available capabilities (tools and skills, each with id "
    "and description), select the capabilities that best serve the request. "
    "Answer with ONLY a JSON object: {\"first\": \"<id>\", \"second\": "
    "\"<id>\", \"third\": \"<id>\"} where <id> is a candidate id exactly as "
    "listed, in order of suitability. No explanation, no markdown fences."
)
USER_TEMPLATE = "## Candidates\n{candidates}\n\n## User request\n{query}"
CANDIDATE_LINE = "{kind}:{name} — {description}"


def prompt_template_sha256() -> str:
    """Hash the frozen prompt template.

    The payload is ``SYSTEM_PROMPT + "\\n" + USER_TEMPLATE + "\\n" +
    CANDIDATE_LINE`` UTF-8 encoded; the placeholders stay in the payload so
    the hash pins the template, not one rendered prompt.

    Returns:
        Hex sha256 of the template payload.
    """
    payload = f"{SYSTEM_PROMPT}\n{USER_TEMPLATE}\n{CANDIDATE_LINE}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_candidates_block(corpus: dict) -> str:
    """Render the candidate block, one line per capability in corpus order.

    Tools come first in registration order, then skills in loader order;
    description whitespace is collapsed so each candidate is exactly one
    line (see ``candidate_format`` in ``judge_config.yaml``).

    Args:
        corpus: Parsed corpus snapshot.

    Returns:
        The candidate block text.
    """
    lines = []
    for kind in ("tool", "skill"):
        for row in corpus[f"{kind}s"]:
            description = " ".join(row["description"].split())
            lines.append(
                CANDIDATE_LINE.format(kind=kind, name=row["name"], description=description)
            )
    return "\n".join(lines)


def build_messages(corpus: dict, query: str) -> list[dict[str, str]]:
    """Build the frozen chat messages for one query.

    Args:
        corpus: Parsed corpus snapshot (the surface under test).
        query: Natural-language user request.

    Returns:
        OpenAI-style message list: system prompt plus one user message.
    """
    user_text = USER_TEMPLATE.format(
        candidates=build_candidates_block(corpus), query=query
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def parse_response(raw: str) -> dict | None:
    """Parse the judge reply into ``{first, second, third}``.

    Handles clean JSON, markdown-fenced JSON and JSON embedded in prose;
    missing or non-string keys become None. Returns None when no JSON object
    can be extracted (garbage, arrays, empty replies) — callers count that
    as an invalid response, never as a crash.

    Args:
        raw: Raw model reply text.

    Returns:
        Dict with first/second/third (values may be None), or None.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    def clean(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) and value.strip() else None

    return {"first": clean("first"), "second": clean("second"), "third": clean("third")}


def build_name_kinds(corpus: dict) -> dict[str, tuple[str, ...]]:
    """Map every capability name to the kinds it exists as in this corpus.

    Args:
        corpus: Parsed corpus snapshot.

    Returns:
        Name -> tuple of ``tool``/``skill`` kinds (a name may map to both).
    """
    name_kinds: dict[str, list[str]] = {}
    for kind in ("tool", "skill"):
        for row in corpus[f"{kind}s"]:
            name_kinds.setdefault(row["name"], []).append(kind)
    return {name: tuple(kinds) for name, kinds in name_kinds.items()}


def score_response(
    parsed: dict | None, entry: dict, name_kinds: dict[str, tuple[str, ...]]
) -> dict:
    """Score one parsed judge response against one query entry.

    Negative false-recall is conservative by definition: it fires only when
    a listed negative id appears in the model's top-3 WHILE the expected id
    does not — a negative that merely appears alongside a successful top-3
    pick is not counted.

    Args:
        parsed: Parsed response ({first, second, third}, values may be None)
            or None for an unparseable reply.
        entry: Query entry dict from ``queries.yaml``.
        name_kinds: Corpus name -> kinds mapping.

    Returns:
        Dict with expected_id, top1_hit, top3_hit, neg_false_recall (None
        when the entry lists no negatives).
    """
    expected_id = f"{entry['expected']['kind']}:{entry['expected']['name']}"
    if parsed:
        first = parsed.get("first")
        top3_ids = {
            parsed.get(key)
            for key in ("first", "second", "third")
            if isinstance(parsed.get(key), str) and parsed.get(key)
        }
    else:
        first, top3_ids = None, set()
    top1_hit = first == expected_id
    top3_hit = expected_id in top3_ids
    neg_false_recall: bool | None = None
    negatives = entry.get("negatives") or []
    if negatives:
        negative_ids = {
            f"{kind}:{name}"
            for name in negatives
            for kind in name_kinds.get(name, ())
        }
        neg_false_recall = bool(negative_ids & top3_ids) and not top3_hit
    return {
        "expected_id": expected_id,
        "top1_hit": top1_hit,
        "top3_hit": top3_hit,
        "neg_false_recall": neg_false_recall,
    }


def _is_bare_name_pick(pick: str | None, expected_name: str) -> bool:
    """True when ``pick`` is a kind-less bare name equal to ``expected_name``.

    Forgives ONLY the missing ``kind:`` prefix — the documented format
    artifact where a SOTA judge names the right capability but omits the
    ``tool:``/``skill:`` prefix. A pick that carries a prefix is never a
    bare-name match, so a wrong-kind pick (``tool:x`` vs expected
    ``skill:x``) is NOT forgiven here.

    Args:
        pick: One judge pick id (may be None).
        expected_name: The expected capability's bare name.

    Returns:
        True for an unprefixed pick equal to the expected bare name.
    """
    if not pick or ":" in pick:
        return False
    return pick == expected_name


def score_response_lenient(
    parsed: dict | None, entry: dict, name_kinds: dict[str, tuple[str, ...]]
) -> dict:
    """Format-tolerant scoring layered over the strict definitions.

    The strict ``score_response`` requires the exact ``kind:name`` id. SOTA
    judges sometimes emit the bare capability name without the ``kind:``
    prefix — a format artifact, not a routing mistake (observed on kimi-k3 in
    E2, where such flips masqueraded as a nominal McNemar signal). The
    lenient score counts a bare-name pick as a hit when the name matches, so
    format-only flips stop polluting the routing comparison. It never forgives
    a wrong-kind pick, and it leaves the strict contract untouched.

    Args:
        parsed: Parsed response ({first, second, third}, values may be None)
            or None for an unparseable reply.
        entry: Query entry dict from ``queries.yaml``.
        name_kinds: Corpus name -> kinds mapping; unused by the lenient rule,
            kept for signature symmetry with ``score_response``.

    Returns:
        Dict with ``top1_hit_lenient`` and ``top3_hit_lenient``.
    """
    del name_kinds  # signature symmetry only; the lenient rule matches names
    expected_name = entry["expected"]["name"]
    expected_id = f"{entry['expected']['kind']}:{expected_name}"
    if parsed:
        first = parsed.get("first")
        top3_picks = [
            parsed.get(key)
            for key in ("first", "second", "third")
            if isinstance(parsed.get(key), str) and parsed.get(key)
        ]
    else:
        first, top3_picks = None, []
    top1_hit_lenient = (first == expected_id) or _is_bare_name_pick(first, expected_name)
    top3_hit_lenient = (expected_id in top3_picks) or any(
        _is_bare_name_pick(pick, expected_name) for pick in top3_picks
    )
    return {"top1_hit_lenient": top1_hit_lenient, "top3_hit_lenient": top3_hit_lenient}
