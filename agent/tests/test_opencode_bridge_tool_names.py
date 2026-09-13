"""Unit tests for the MCP prefixed->bare tool-name mapping (tool_names.py).

Covers the verified 1.18.x response shapes (``GET /mcp`` flat status record,
``GET /experimental/tool/ids`` string array), the real prefixed tool name
taken from the T1 golden traces, longest-prefix resolution for nested server
names, and the degraded modes that keep version drift harmless.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.opencode_bridge.tool_names import (
    EMPTY_TOOL_NAME_MAP,
    ToolNameMap,
    build_tool_name_map,
    sanitize_tool_component,
)

TRACES_DIR = Path(__file__).parent / "fixtures" / "opencode_bridge" / "traces"


def real_prefixed_tool_names(trace_name: str) -> set[str]:
    """Extract the actual tool-part names from a recorded golden trace."""
    names: set[str] = set()
    with (TRACES_DIR / trace_name).open(encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)
            if frame["type"] != "message.part.updated":
                continue
            part = json.loads(frame["data"]).get("properties", {}).get("part", {})
            if part.get("type") == "tool" and isinstance(part.get("tool"), str):
                names.add(part["tool"])
    return names


def test_sanitize_mirrors_opencode_catalog_rule() -> None:
    assert sanitize_tool_component("vibe-trading") == "vibe-trading"
    assert sanitize_tool_component("my server.v2") == "my_server_v2"
    assert sanitize_tool_component("a:b/c") == "a_b_c"


def test_build_from_verified_1_18_x_shapes() -> None:
    mcp_payload = {
        "vibe-trading": {"status": "connected"},
        "websearch": {"status": "connected"},
        "codegraph": {"status": "disabled"},
    }
    tool_ids = ["bash", "read", "vibe-trading_list_skills", "websearch_web_search"]
    mapping = build_tool_name_map(mcp_payload, tool_ids)
    assert mapping.prefixed_to_bare == {
        "vibe-trading_list_skills": "list_skills",
        "websearch_web_search": "web_search",
    }
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"
    assert mapping.bare("bash") == "bash"  # builtin passes through


def test_mapping_resolves_real_trace_tool_names() -> None:
    # Golden pin: scenario a ran one MCP tool + one builtin (spike §5a).
    trace_names = real_prefixed_tool_names("scenario_a_multi_tool.jsonl")
    assert "vibe-trading_list_skills" in trace_names
    assert "bash" in trace_names
    mapping = build_tool_name_map(
        {"vibe-trading": {"status": "connected"}}, sorted(trace_names)
    )
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"
    assert mapping.bare("bash") == "bash"


def test_longest_prefix_wins_for_nested_server_names() -> None:
    mapping = build_tool_name_map(
        {"a": {"status": "connected"}, "a_b": {"status": "connected"}},
        ["a_b_tool"],
    )
    assert mapping.server_prefixes[0] == "a_b_"
    assert mapping.bare("a_b_tool") == "tool"


def test_dynamic_prefix_stripping_when_exact_entry_missing() -> None:
    # A tool registered after startup is not in the exact table; the
    # server-prefix fallback still strips it.
    mapping = build_tool_name_map({"vibe-trading": {"status": "connected"}}, [])
    assert mapping.prefixed_to_bare == {}
    assert mapping.bare("vibe-trading_brand_new_tool") == "brand_new_tool"


def test_degraded_tool_ids_payload_keeps_prefix_stripping() -> None:
    mapping = build_tool_name_map({"vibe-trading": {"status": "connected"}}, None)
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"


def test_degraded_mcp_payload_never_strips_unknown_prefixes() -> None:
    mapping = build_tool_name_map(None, ["bash", "vibe-trading_list_skills"])
    assert mapping.server_prefixes == ()
    assert mapping.bare("vibe-trading_list_skills") == "vibe-trading_list_skills"
    assert mapping.bare("bash") == "bash"


def test_empty_map_returns_names_unchanged() -> None:
    assert EMPTY_TOOL_NAME_MAP.bare("anything_at_all") == "anything_at_all"


def test_prefix_equal_to_name_is_not_stripped_to_empty() -> None:
    mapping = build_tool_name_map({"srv": {"status": "connected"}}, ["srv_"])
    assert mapping.bare("srv_") == "srv_"


def test_envelope_variants_are_unwrapped() -> None:
    # Forward-compat: a future pin wrapping the record/list must not lose
    # the mapping (defensive parsing per spike report §8).
    wrapped_mcp = {"clients": {"vibe-trading": {"status": "connected"}}}
    wrapped_ids = {"tools": [{"id": "vibe-trading_list_skills"}, {"name": "bash"}]}
    mapping = build_tool_name_map(wrapped_mcp, wrapped_ids)
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"


def test_unrecognised_shapes_degrade_without_raising() -> None:
    mapping = build_tool_name_map(["not", "a", "record"], {"unexpected": 1})
    assert mapping == ToolNameMap(prefixed_to_bare={}, server_prefixes=())
    assert mapping.bare("vibe-trading_x") == "vibe-trading_x"


def test_exact_table_wins_over_dynamic_stripping() -> None:
    mapping = ToolNameMap(
        prefixed_to_bare={"srv_tool": "canonical"},
        server_prefixes=("srv_",),
    )
    assert mapping.bare("srv_tool") == "canonical"
