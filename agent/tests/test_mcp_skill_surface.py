"""PLAN-B4/B5 tests: list_skills exposes category; MCP skill tools carry the host-first downgrade.

B4: the routing surface gets the category the data model already carries —
every bundled skill must appear with its frontmatter category. B5 (DEC-1,
option A): the MCP-side skill tools stay registered for pure MCP clients but
their descriptions direct host-backed sessions to the host's native path.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
BUNDLED_SKILLS_DIR = AGENT_DIR / "src" / "skills"

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _bundled_frontmatter() -> dict[str, str]:
    """Return {skill name: category} read straight from the bundled frontmatter."""
    out: dict[str, str] = {}
    for skill_md in sorted(BUNDLED_SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(.+)$", text, re.M)
        category = re.search(r"^category:\s*(.+)$", text, re.M)
        assert name, f"{skill_md}: no frontmatter name"
        out[name.group(1).strip()] = category.group(1).strip() if category else "other"
    return out


def test_list_skills_output_carries_category_for_every_bundled_skill() -> None:
    import mcp_server

    rows = json.loads(mcp_server.list_skills())
    by_name = {row["name"]: row for row in rows}

    assert rows, "list_skills returned no skills"
    for row in rows:
        assert set(row) == {"name", "description", "category"}, f"unexpected shape: {sorted(row)}"

    bundled = _bundled_frontmatter()
    missing = sorted(set(bundled) - set(by_name))
    assert not missing, f"bundled skills missing from list_skills: {missing}"

    wrong = {
        name: (by_name[name]["category"], category)
        for name, category in bundled.items()
        if by_name[name]["category"] != category
    }
    assert not wrong, f"category mismatches (got, want): {wrong}"


def test_list_skills_categories_match_the_frontmatter_taxonomy() -> None:
    import mcp_server

    rows = json.loads(mcp_server.list_skills())
    taxonomy = set(_bundled_frontmatter().values())
    assert taxonomy == {row["category"] for row in rows if row["name"] in _bundled_frontmatter()}
    assert len(taxonomy) == 9


def test_mcp_skill_tools_stay_registered_but_point_hosts_to_their_native_path() -> None:
    """DEC-1 downgrade: keep the pure-MCP-client path, prefer the host surface."""
    import asyncio

    import mcp_server

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert "list_skills" in tools, "pure MCP clients must keep the skill surface"
    assert "load_skill" in tools, "pure MCP clients must keep the skill surface"

    for name in ("list_skills", "load_skill"):
        description = tools[name].description
        assert (
            "host" in description and ".opencode/skills" in description
        ), f"{name} lost the host-first downgrade guidance"
