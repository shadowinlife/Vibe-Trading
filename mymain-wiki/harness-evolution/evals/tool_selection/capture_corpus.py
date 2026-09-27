"""Capture a full routing-surface corpus snapshot for the LLM-judge suite.

Snapshots the CURRENT tool + skill descriptions into a corpus YAML that
``run_llm_judge`` can score as a baseline or post surface. This is the
freeze/rebuild primitive the A7/A8 before/after comparison relies on:

* capture once BEFORE a description change -> the baseline snapshot;
* apply the change;
* capture again -> the post snapshot;
* score both under identical pins and compare.

Tools come from ``mcp_server.mcp.list_tools()`` in registration order; skills
come from ``src.agent.skills.SkillsLoader`` bundled skills in loader order
(user-created skills are excluded so the surface matches the pinned bundled
count). The output schema mirrors ``corpus_snapshot.yaml`` exactly.

Usage:
    cd agent
    python -m src.evals.tool_selection.capture_corpus --out corpus_a7_baseline.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[2]

SOURCE_TOOLS = "mcp_server.mcp.list_tools() (registration order)"
SOURCE_SKILLS = "src.agent.skills.SkillsLoader bundled skills (loader order)"


def capture_tools() -> list[dict]:
    """Enumerate the MCP surface in registration order.

    Returns:
        One ``{name, description}`` row per registered MCP tool.
    """
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools(run_middleware=False))
    return [
        {"name": tool.name, "description": tool.description or ""}
        for tool in tools
    ]


def capture_skills() -> list[dict]:
    """Enumerate the bundled skills in loader order, excluding user skills.

    Returns:
        One ``{name, description}`` row per bundled skill.
    """
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    from src.agent.skills import SkillsLoader

    absent_user_dir = HERE / "_no_user_skills"
    loader = SkillsLoader(user_skills_dir=absent_user_dir)
    return [
        {"name": skill.name, "description": skill.description or ""}
        for skill in loader.skills
    ]


def build_corpus() -> dict:
    """Assemble the corpus snapshot dict.

    Returns:
        The corpus payload ready for YAML serialization.
    """
    tools = capture_tools()
    skills = capture_skills()
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"tools": SOURCE_TOOLS, "skills": SOURCE_SKILLS},
        "tool_count": len(tools),
        "skill_count": len(skills),
        "tools": tools,
        "skills": skills,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok, 2 usage error).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", required=True,
        help="output corpus YAML path (e.g. corpus_a7_baseline.yaml)",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = HERE / out_path
    corpus = build_corpus()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(
            corpus, sort_keys=False, allow_unicode=True,
            default_flow_style=False, width=100,
        ),
        encoding="utf-8",
    )
    print(
        f"captured {corpus['tool_count']} tools + {corpus['skill_count']} skills "
        f"-> {out_path} (captured_at {corpus['captured_at']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
