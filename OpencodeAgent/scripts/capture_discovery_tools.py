"""Capture exact tools/list schemas without invoking a finance tool."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import importlib.util
import json
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]


async def capture(server_path: Path, manifest_path: Path, catalog_path: Path) -> dict:
    """Read MCP schemas and configured denies for the discovery recipes.

    Args:
        server_path: Operator-selected local VT MCP entry point.
        manifest_path: Existing harness deny manifest.
        catalog_path: Recipe catalogue used to limit the captured fixture.

    Returns:
        Exact MCP schemas, deny expansion and total exposed tool count.
    """
    sys.path.insert(0, str(server_path.parent))
    spec = importlib.util.spec_from_file_location("discovery_mcp_server", server_path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the local VT MCP server")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with Client(module.mcp) as client:
        tools = await client.list_tools()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recipes = json.loads(catalog_path.read_text(encoding="utf-8"))
    selected = {name for recipe in recipes.values() for name in recipe["tools"]}
    selected.update(
        (
            "start_research_goal",
            "get_research_goal",
            "add_goal_evidence",
            "update_research_goal_status",
        )
    )
    missing = selected - {tool.name for tool in tools}
    if missing:
        raise ValueError(f"Recipe tools absent from tools/list: {sorted(missing)}")
    return {
        "tool_count": len(tools),
        "tools": [
            {"name": tool.name, "inputSchema": tool.inputSchema}
            for tool in tools
            if tool.name in selected
        ],
        "disabled_tools": [
            tool.name
            for tool in tools
            if any(
                fnmatch.fnmatchcase(tool.name, pattern)
                for pattern in manifest["disabled"]
            )
        ],
    }


def main() -> int:
    """Write a sanitized schema fixture; no credentials or results are captured."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--server", type=Path, default=ROOT.parent / "agent" / "mcp_server.py"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "config" / "vibe-trading-tools.json"
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "skills" / "research-discovery" / "recipes.json",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(capture(args.server, args.manifest, args.catalog))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Captured {len(result['tools'])} schemas from {result['tool_count']} exposed tools"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
