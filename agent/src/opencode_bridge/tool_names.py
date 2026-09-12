"""MCP tool-name mapping: opencode prefixed names -> bare vt tool names.

opencode registers every MCP tool under a prefixed id computed as
``sanitize(serverName) + "_" + sanitize(toolName)`` (verified in the
opencode 1.18.18 and 1.18.30 sources, ``packages/opencode/src/mcp/catalog.ts``
— identical on both pin candidates), where ``sanitize`` replaces every
character outside ``[a-zA-Z0-9_-]`` with ``_``. The vt relay matches tool
names by their *bare* names (``sessions_routes.py``), so the bridge must be
able to strip the prefix — e.g. ``vibe-trading_list_skills`` -> ``list_skills``.

The mapping table is built at driver startup from two serve endpoints:

* ``GET /mcp`` — ``Record<serverName, {status, ...}>`` (legacy surface;
  verified shape on 1.18.18/1.18.30). Yields the set of MCP server names.
* ``GET /experimental/tool/ids`` — ``string[]`` of every registered tool id
  (builtin bare names + MCP prefixed names). Present and identically shaped
  on both 1.18.18 and 1.18.30. This is NOT part of the ``/api/*`` preview
  surface the work plan forbids (D10); should it disappear on a future pin,
  the builder degrades to prefix-only matching (see below) instead of
  failing.

Both payloads are parsed defensively (work plan T3 / spike report §8:
version drift is expected, unknown shapes must degrade, never crash):
unrecognised envelopes are unwrapped when obvious and ignored otherwise.
A :class:`ToolNameMap` always answers :meth:`ToolNameMap.bare` — with the
exact table entry when known, with longest-server-prefix stripping when the
name looks prefixed, and with the input unchanged for builtins (``bash``)
or unknown names.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger("opencode_bridge")

__all__ = ["ToolNameMap", "build_tool_name_map", "sanitize_tool_component"]

_SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_component(value: str) -> str:
    """Mirror opencode's MCP name sanitization (``catalog.ts``).

    Args:
        value: Raw MCP server or tool name.

    Returns:
        The name with every character outside ``[a-zA-Z0-9_-]`` replaced
        by ``_``, exactly as opencode computes prefixed tool ids.
    """
    return _SANITIZE_PATTERN.sub("_", value)


@dataclass(frozen=True, slots=True)
class ToolNameMap:
    """Immutable prefixed-name -> bare-name lookup.

    Attributes:
        prefixed_to_bare: Exact mapping built from the serve's tool-id list.
        server_prefixes: Sanitized ``"<server>_"`` prefixes of all known MCP
            servers, longest first, used for dynamic stripping when a name
            is missing from the exact table (e.g. the tool-id endpoint was
            unavailable, or the tool appeared after startup).
    """

    prefixed_to_bare: Mapping[str, str]
    server_prefixes: tuple[str, ...]

    def bare(self, name: str) -> str:
        """Return the bare vt tool name for an opencode tool id.

        Args:
            name: Tool name as it appears in opencode events (``tool`` field
                of a tool part), e.g. ``vibe-trading_list_skills`` or
                ``bash``.

        Returns:
            The mapped bare name (``list_skills``), the dynamically stripped
            name when *name* starts with a known server prefix, or *name*
            unchanged for builtins and unknown tools.
        """
        exact = self.prefixed_to_bare.get(name)
        if exact is not None:
            return exact
        for prefix in self.server_prefixes:
            if len(name) > len(prefix) and name.startswith(prefix):
                return name[len(prefix) :]
        return name


EMPTY_TOOL_NAME_MAP = ToolNameMap(prefixed_to_bare={}, server_prefixes=())


def _server_names(mcp_payload: Any) -> list[str]:
    """Extract MCP server names from a ``GET /mcp`` response, defensively."""
    if not isinstance(mcp_payload, dict):
        if mcp_payload is not None:
            logger.warning(
                "GET /mcp returned unrecognised shape %s; "
                "tool-name mapping degrades to exact-table only",
                type(mcp_payload).__name__,
            )
        return []
    # Unwrap known envelope variants (forward-compat; 1.18.x is flat).
    for envelope_key in ("clients", "servers", "mcp"):
        inner = mcp_payload.get(envelope_key)
        if len(mcp_payload) == 1 and isinstance(inner, dict):
            mcp_payload = inner
            break
        if len(mcp_payload) == 1 and isinstance(inner, list):
            return [
                entry["name"]
                for entry in inner
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            ]
    return [key for key in mcp_payload if isinstance(key, str)]


def _tool_ids(tool_ids_payload: Any) -> list[str]:
    """Extract tool ids from a ``GET /experimental/tool/ids`` response."""
    if isinstance(tool_ids_payload, dict):
        # Forward-compat: tolerate an envelope like {"tools": [...]}.
        for envelope_key in ("tools", "ids", "toolIDs"):
            inner = tool_ids_payload.get(envelope_key)
            if len(tool_ids_payload) == 1 and isinstance(inner, list):
                tool_ids_payload = inner
                break
    if not isinstance(tool_ids_payload, list):
        if tool_ids_payload is not None:
            logger.warning(
                "tool-id list returned unrecognised shape %s; "
                "tool-name mapping degrades to prefix stripping",
                type(tool_ids_payload).__name__,
            )
        return []
    ids: list[str] = []
    for entry in tool_ids_payload:
        if isinstance(entry, str):
            ids.append(entry)
        elif isinstance(entry, dict):
            # Forward-compat: tolerate [{"id": ...}] / [{"name": ...}].
            for id_key in ("id", "name"):
                value = entry.get(id_key)
                if isinstance(value, str):
                    ids.append(value)
                    break
    return ids


def build_tool_name_map(mcp_payload: Any, tool_ids_payload: Any) -> ToolNameMap:
    """Build the prefixed->bare mapping from the two serve responses.

    Args:
        mcp_payload: Decoded ``GET /mcp`` body (server-name record), or
            ``None`` when the endpoint was unavailable.
        tool_ids_payload: Decoded ``GET /experimental/tool/ids`` body
            (tool-id array), or ``None`` when the endpoint was unavailable.

    Returns:
        A :class:`ToolNameMap`. Never raises: unrecognised shapes degrade
        the map (exact table and/or prefix list may be empty) and log a
        warning — version drift must not take the bridge down.
    """
    servers = _server_names(mcp_payload)
    prefixes = tuple(
        sorted(
            (sanitize_tool_component(name) + "_" for name in servers),
            # Longest first so nested server names ("a" vs "a_b") strip
            # against the most specific prefix.
            key=lambda prefix: (-len(prefix), prefix),
        )
    )
    exact: dict[str, str] = {}
    for tool_id in _tool_ids(tool_ids_payload):
        for prefix in prefixes:
            if len(tool_id) > len(prefix) and tool_id.startswith(prefix):
                exact[tool_id] = tool_id[len(prefix) :]
                break
    return ToolNameMap(prefixed_to_bare=exact, server_prefixes=prefixes)
