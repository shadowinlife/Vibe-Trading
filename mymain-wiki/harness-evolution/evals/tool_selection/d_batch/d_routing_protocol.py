"""D-batch Level-R routing protocol — the delegation-decision template.

Separate from ``llm_judge_protocol`` (the frozen selection template): that
module answers "which capability serves this request"; this one answers
"should the main agent handle this request directly or delegate it to a
domain subagent" — the C-batch failure mode (an unreliable extra decision)
transplanted to the subagent design, measured directly per DecisionBench's
lesson that routing fidelity must be scored, not inferred from end quality.

The template is sha256-pinned in three places that must agree: the trace
header, ``HARNESS_EVOLUTION_D_PLAN.md`` §5, and the D-batch test — any edit
is a protocol change.

The candidate block deliberately includes the FULL post-B surface
(59 tools + 90 skills) plus the two subagent cards: production routing
happens with the whole surface visible, so the eval must reproduce that
competition rather than test an artificially easy 3-way choice.
"""

from __future__ import annotations

import hashlib
import json

# --------------------------------------------------------------------------- #
# Frozen routing template. ANY edit changes routing_template_sha256() and is
# a protocol change: record it in HARNESS_EVOLUTION_D_PLAN.md.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT_R = (
    "You are the main agent of a finance research system. You can handle the "
    "user request yourself with the available capabilities (tools and skills "
    "listed below), or delegate it to ONE specialist subagent listed under "
    "'Delegate subagents'. Delegate when the request falls squarely within a "
    "subagent's described scope; otherwise handle it directly. Answer with "
    "ONLY a JSON object: {\"route\": \"direct\"} or {\"route\": \"<subagent "
    "name>\"}. No explanation, no markdown fences."
)
USER_TEMPLATE_R = (
    "## Capabilities\n{candidates}\n\n## Delegate subagents\n{subagents}"
    "\n\n## User request\n{query}"
)
SUBAGENT_LINE_R = "subagent:{name} — {description}"

#: Valid route labels. ``direct`` means the main loop handles the request.
VALID_ROUTES = ("direct", "quant-agent", "web-docs-agent")


def routing_template_sha256() -> str:
    """Hash the frozen routing template (template text, not one render)."""
    payload = f"{SYSTEM_PROMPT_R}\n{USER_TEMPLATE_R}\n{SUBAGENT_LINE_R}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_subagent_block(definitions: list[dict]) -> str:
    """Render the subagent cards, one line each, definition-file order.

    Args:
        definitions: Parsed subagent definition dicts (name + description).

    Returns:
        The subagent block text.
    """
    lines = []
    for d in definitions:
        description = " ".join(str(d["description"]).split())
        lines.append(SUBAGENT_LINE_R.format(name=d["name"], description=description))
    return "\n".join(lines)


def build_routing_messages(candidates_block: str, subagents_block: str,
                           query: str) -> list[dict[str, str]]:
    """Build the frozen chat messages for one routing decision.

    Args:
        candidates_block: Full-surface candidate block (from the frozen
            selection protocol's ``build_candidates_block``).
        subagents_block: Subagent cards from ``build_subagent_block``.
        query: Natural-language user request.

    Returns:
        OpenAI-style message list: system prompt plus one user message.
    """
    user_text = USER_TEMPLATE_R.format(
        candidates=candidates_block, subagents=subagents_block, query=query
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT_R},
        {"role": "user", "content": user_text},
    ]


def parse_route(raw: str, valid_routes: tuple | list | None = None) -> str | None:
    """Parse the judge reply into a route label.

    Handles clean JSON, markdown-fenced JSON and JSON embedded in prose.
    Returns None when no JSON object with a valid route can be extracted —
    callers count that as an invalid response (a miss), never a crash.

    Args:
        raw: Raw model reply text.
        valid_routes: Allowed route labels; defaults to the frozen D-batch
            VALID_ROUTES. D4 passes its extended candidate list here (the
            template hash is unaffected — route labels are not hashed).

    Returns:
        One of valid_routes, or None.
    """
    allowed = tuple(valid_routes) if valid_routes else VALID_ROUTES
    text = raw.strip()
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        candidates.extend(
            p[4:].strip() if p.startswith("json") else p.strip()
            for p in parts[1:] if p.strip()
        )
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            route = obj.get("route")
            if isinstance(route, str):
                normalized = route.strip().lower().replace("_", "-")
                if normalized in allowed:
                    return normalized
    return None
