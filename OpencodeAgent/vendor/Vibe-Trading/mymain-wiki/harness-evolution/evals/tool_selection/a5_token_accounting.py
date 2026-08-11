"""A5 token-tax accounting for the skill double-exposure decision.

The 90 bundled skills reach the agent through TWO paths at once:

* the HOST path — ``.opencode/skills/`` is injected into the agent's
  ``available_skills`` system-prompt block (``SkillsLoader.get_descriptions``);
* the MCP path — the ``list_skills`` / ``load_skill`` tools expose the same
  catalogue on demand.

While both paths are active the catalogue is disclosed twice, so every routing
decision pays a duplicate disclosure tax. Closing one side (the A5 decision)
saves one copy. This helper measures that per-copy cost deterministically so
the A5 "real improvement" claim reduces to a number, not an intuition.

Two disclosure formats are measured:

* ``host``  — the grouped ``get_descriptions`` block actually injected into the
  system prompt (the always-on cost);
* ``mcp``   — the ``list_skills`` JSON array (the on-demand cost, only paid when
  the tool is called).

Token counts use tiktoken ``cl100k_base`` as the primary estimate with a
chars/3 heuristic cross-check (the same heuristic the judge runner uses for
budget pre-checks). Both are estimates — the judges are DashScope models whose
native tokenizers differ — but the pair bounds the real figure.

Usage:
    cd agent
    python -m src.evals.tool_selection.a5_token_accounting
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[2]


def _load_bundled_skills() -> list:
    """Load the bundled skills, excluding user-created skills.

    Returns:
        The list of bundled ``Skill`` objects in loader order.
    """
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    from src.agent.skills import SkillsLoader

    loader = SkillsLoader(user_skills_dir=HERE / "_no_user_skills")
    return loader.skills


def host_disclosure_text(skills: list) -> str:
    """Render the always-on host disclosure block (system-prompt format).

    Args:
        skills: Bundled skill objects.

    Returns:
        The grouped ``get_descriptions`` text injected into the system prompt.
    """
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    from src.agent.skills import SkillsLoader

    loader = SkillsLoader(user_skills_dir=HERE / "_no_user_skills")
    return loader.get_descriptions()


def mcp_disclosure_text(skills: list) -> str:
    """Render the on-demand MCP ``list_skills`` JSON array.

    Args:
        skills: Bundled skill objects.

    Returns:
        The JSON array string ``list_skills`` returns.
    """
    payload = [
        {"name": skill.name, "description": skill.description or ""}
        for skill in skills
    ]
    return json.dumps(payload, ensure_ascii=False)


def count_tokens(text: str) -> dict:
    """Estimate a disclosure's token cost two ways.

    Args:
        text: The disclosure text.

    Returns:
        Dict with ``chars``, ``tiktoken_cl100k`` and ``chars_over_3`` counts.
    """
    chars = len(text)
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        tiktoken_count = len(enc.encode(text))
    except Exception:
        tiktoken_count = None
    return {
        "chars": chars,
        "tiktoken_cl100k": tiktoken_count,
        "chars_over_3": (chars + 2) // 3,
    }


def build_accounting() -> dict:
    """Assemble the full token-tax accounting.

    Returns:
        Dict with the skill count and per-format disclosure costs.
    """
    skills = _load_bundled_skills()
    return {
        "skill_count": len(skills),
        "host_disclosure": count_tokens(host_disclosure_text(skills)),
        "mcp_disclosure": count_tokens(mcp_disclosure_text(skills)),
    }


def render_report(accounting: dict) -> str:
    """Render the accounting as a markdown report.

    Args:
        accounting: Output of ``build_accounting``.

    Returns:
        Markdown report text.
    """
    host = accounting["host_disclosure"]
    mcp = accounting["mcp_disclosure"]
    lines = [
        "# A5 skill double-exposure token-tax accounting",
        "",
        f"Bundled skills measured: **{accounting['skill_count']}**",
        "",
        "While both exposure paths are active the catalogue below is paid twice.",
        "Closing one side saves one copy — that saving IS the A5 token tax.",
        "",
        "| Disclosure | Chars | Tokens (tiktoken cl100k) | Tokens (chars/3 heuristic) |",
        "|---|---|---|---|",
    ]

    def row(label: str, counts: dict) -> str:
        tiktoken_cell = (
            str(counts["tiktoken_cl100k"])
            if counts["tiktoken_cl100k"] is not None else "n/a"
        )
        return (
            f"| {label} | {counts['chars']} | {tiktoken_cell} "
            f"| {counts['chars_over_3']} |"
        )

    lines.append(row("host `get_descriptions` (always-on)", host))
    lines.append(row("mcp `list_skills` JSON (on-demand)", mcp))
    lines += [
        "",
        "> Token counts are estimates: the judges are DashScope models whose",
        "> native tokenizers differ from cl100k_base. The two columns bound the",
        "> real figure; treat the host row as the per-decision always-on tax.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of the markdown report",
    )
    args = parser.parse_args(argv)

    accounting = build_accounting()
    if args.json:
        print(json.dumps(accounting, ensure_ascii=False, indent=2))
    else:
        print(render_report(accounting))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
