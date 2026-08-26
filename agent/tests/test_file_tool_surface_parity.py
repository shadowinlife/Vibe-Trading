"""read_file/write_file must declare their backtest-workspace scope on every routing surface.

The host harness ships its own read/write tools with different path
conventions, so a router that sees both toolsets cannot arbitrate "which
filesystem" from the tool names alone. The arbitration rule is: relative
paths inside the backtest workspace (run_dir) belong to these tools, host
files and source code to the host's own tools — and the rule only works if
every surface a router might read actually states it.

There are four such surfaces, and they are separate texts with no shared
constant: the two MCP wrapper docstrings in ``mcp_server.py`` (what MCP
clients see) and the two agent tool-class ``description`` attributes (what
the local agent loop sees). Editing one without its mirror on the other
surface silently re-opens the ambiguity, so each must carry the three scope
markers — ``backtest workspace``, ``run_dir`` and ``relative path`` — the
minimum a router needs to apply the rule. Matching is case-insensitive
because a docstring may open a sentence with "Relative paths".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import mcp_server  # noqa: E402 — needs AGENT_DIR above
from src.tools.read_file_tool import ReadFileTool  # noqa: E402 — needs AGENT_DIR above
from src.tools.write_file_tool import WriteFileTool  # noqa: E402 — needs AGENT_DIR above

# The minimum scope declaration a router needs to arbitrate "which filesystem":
# which workspace the tool owns, the directory relative paths resolve against,
# and that relative paths are the convention at all.
SCOPE_MARKERS = ("backtest workspace", "run_dir", "relative path")

SURFACES = (
    "mcp write_file",
    "mcp read_file",
    "WriteFileTool.description",
    "ReadFileTool.description",
)


def _mcp_tool_description(name: str) -> str:
    """Return the description an MCP client sees for a wrapper tool.

    The wrappers are ``@mcp.tool``-decorated, so the docstring is read from
    the module attribute when the decorator leaves the function visible; when
    a decorator version hides ``__doc__``, the client-facing text is what
    ``list_tools()`` reports, matched by tool name.

    Args:
        name: MCP tool name (``read_file`` or ``write_file``).

    Returns:
        The tool's description text.
    """
    doc = getattr(getattr(mcp_server, name), "__doc__", None)
    if doc:
        return doc
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        if tool.name == name:
            assert tool.description, f"MCP tool {name!r} exposes no description"
            return tool.description
    raise AssertionError(f"MCP tool {name!r} is not registered")


def _surface_text(surface: str) -> str:
    """Return the description text one routing surface carries.

    Args:
        surface: One of the ``SURFACES`` labels.

    Returns:
        The description text that surface exposes to routers.
    """
    if surface == "mcp write_file":
        return _mcp_tool_description("write_file")
    if surface == "mcp read_file":
        return _mcp_tool_description("read_file")
    if surface == "WriteFileTool.description":
        return WriteFileTool.description
    if surface == "ReadFileTool.description":
        return ReadFileTool.description
    raise AssertionError(f"unknown surface {surface!r}")


@pytest.mark.parametrize("surface", SURFACES)
def test_every_file_tool_surface_declares_the_backtest_workspace_scope(surface: str) -> None:
    """Each of the four surfaces must carry all three scope markers."""
    text = _surface_text(surface).lower()
    missing = [marker for marker in SCOPE_MARKERS if marker not in text]
    assert not missing, f"{surface} is missing scope markers {missing}"
