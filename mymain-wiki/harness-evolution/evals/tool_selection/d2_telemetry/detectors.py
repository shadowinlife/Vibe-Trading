"""D2 telemetry detectors: twin-choice, whitelist-conformance, and failure-loop events.

Parses opencode session traces (sqlite ``part`` table or ``--format json``
archives) into normalized tool-call records, then runs three detector classes
frozen in HARNESS_EVOLUTION_D2_PLAN.md §3.2:

- Whitelist conformance: every governed tool call a subagent makes must fall
  inside its manifest whitelist; foreign MCP namespaces (OMO builtins such as
  websearch/context7/grep_app/lsp) are hard violations (the S5 leak class).
- Channel confusion: a direct tool invoked through the ``skill_mcp`` channel
  (the D2-5 failure class, e.g. ``vibe-trading_sentiment`` via ``skill_mcp``).
- Repeated failing calls: consecutive error-status calls to the same target,
  the dead-loop signature from smoke s5d.

Pure functions over call records; IO lives in ``load_calls_*``. No LLM calls,
no network, no mutation — production-data safe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Host builtin tools: not gated by MCP namespace denies (registered soft
# boundary D2-6). Recorded, never counted as violations.
HOST_BUILTINS = frozenset(
    {
        "read",
        "write",
        "edit",
        "grep",
        "glob",
        "bash",
        "webfetch",
        "task",
        "todowrite",
        "list_mcp_resources",
        "read_mcp_resource",
        "list_mcp_resource_templates",
    }
)

# OMO plugin runtime-injected MCP namespaces. For a whitelisted subagent any
# call into these is a hard violation (S5 leak class).
FOREIGN_MCP_PREFIXES = ("websearch_", "context7_", "grep_app_", "lsp_")

# Skill channel tools: governed by the prompt-level skill contract, not the
# tool whitelist (the production manifest carries no skills field).
SKILL_CHANNEL_TOOLS = frozenset(
    {"vibe-trading_load_skill", "vibe-trading_list_skills", "skill_mcp"}
)

VT_PREFIX = "vibe-trading_"


@dataclass(frozen=True)
class ToolCall:
    """One normalized tool invocation extracted from a trace."""

    session_id: str
    tool: str
    target: str  # canonical call target (skill_mcp -> "<mcp>.<tool>")
    status: str  # "completed" | "error" | other
    time_ms: int
    input_hash: str  # stable hash of canonicalized input args


@dataclass
class DetectionReport:
    """Aggregated detector output for one session (subtree)."""

    session_id: str
    total_calls: int = 0
    governed_calls: int = 0  # MCP calls subject to the whitelist
    whitelist_violations: list[ToolCall] = field(default_factory=list)
    foreign_namespace_calls: list[ToolCall] = field(default_factory=list)
    channel_confusions: list[ToolCall] = field(default_factory=list)
    repeated_failures: list[dict[str, Any]] = field(default_factory=list)
    skill_names_loaded: list[str] = field(default_factory=list)
    host_builtin_calls: int = 0


def _canon_input(raw: Any) -> str:
    """Canonicalize a tool input payload to a stable string.

    Handles the observed ``skill_mcp`` variants where args arrive either as
    top-level keys or wrapped in a JSON string under ``arguments``.
    """
    if (
        isinstance(raw, dict)
        and set(raw) == {"arguments"}
        and isinstance(raw["arguments"], str)
    ):
        try:
            raw = json.loads(raw["arguments"])
        except json.JSONDecodeError:
            pass
    try:
        return json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return str(raw)


def _target_of(tool: str, raw_input: Any) -> str:
    """Resolve the canonical call target for loop/confusion grouping."""
    if tool == "skill_mcp" and isinstance(raw_input, dict):
        mcp = raw_input.get("mcp_name", "?")
        inner = raw_input.get("tool_name", "?")
        return f"{mcp}.{inner}"
    return tool


def _record(session_id: str, data: dict[str, Any]) -> ToolCall | None:
    """Build a ToolCall from one trace part, or None if not a tool call."""
    if data.get("type") != "tool":
        return None
    tool = data.get("tool")
    state = data.get("state") or {}
    raw_input = state.get("input", {})
    canon = _canon_input(raw_input)
    return ToolCall(
        session_id=session_id,
        tool=str(tool),
        target=_target_of(str(tool), raw_input if isinstance(raw_input, dict) else {}),
        status=str(state.get("status", "unknown")),
        time_ms=(
            int(data.get("time", {}).get("start", 0) or 0)
            if isinstance(data.get("time"), dict)
            else int(data.get("time", 0) or 0)
        ),
        input_hash=hashlib.sha1(canon.encode()).hexdigest()[:12],
    )


def load_calls_sqlite(
    db_path: str | Path, session_id: str, include_children: bool = True
) -> list[ToolCall]:
    """Load ordered tool calls for a session (optionally its subagent subtree)."""
    db = sqlite3.connect(str(db_path))
    try:
        ids = [session_id]
        if include_children:
            rows = db.execute(
                "select id from session where parent_id = ?", (session_id,)
            ).fetchall()
            ids.extend(r[0] for r in rows)
        marks = ",".join("?" * len(ids))
        rows = db.execute(
            f"select session_id, data, time_created from part "
            f"where session_id in ({marks}) order by time_created",
            ids,
        ).fetchall()
    finally:
        db.close()
    calls = []
    for sid, blob, _ in rows:
        try:
            rec = _record(sid, json.loads(blob))
        except json.JSONDecodeError:
            continue
        if rec is not None:
            calls.append(rec)
    return calls


def load_calls_jsonl(path: str | Path) -> list[ToolCall]:
    """Load tool calls from an archived ``opencode run --format json`` file."""
    calls = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "tool_use":
            continue
        rec = _record(str(event.get("sessionID", "")), event.get("part") or {})
        if rec is not None:
            calls.append(rec)
    return calls


def load_manifest_whitelists(manifest_path: str | Path) -> dict[str, frozenset[str]]:
    """Read subagents.json into ``{agent_name: allowed bare tool names}``."""
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return {s["name"]: frozenset(s.get("tools", [])) for s in doc["subagents"]}


def detect(
    calls: Iterable[ToolCall],
    whitelist: frozenset[str] | None = None,
    repeat_threshold: int = 5,
) -> DetectionReport:
    """Run all detectors over one session's calls.

    Args:
        calls: ordered tool calls (single session or subtree).
        whitelist: allowed bare tool names (manifest ``tools`` list). None
            disables whitelist checking (e.g. for the main agent).
        repeat_threshold: consecutive identical-target error calls that
            constitute a dead-loop event.

    Returns:
        DetectionReport with violations, confusions, loops, and skill loads.
    """
    calls = sorted(calls, key=lambda c: (c.time_ms, c.input_hash))
    session_id = calls[0].session_id if calls else ""
    rep = DetectionReport(session_id=session_id)
    rep.total_calls = len(calls)

    run_target: str | None = None
    run_count = 0

    def flush_run() -> None:
        if run_target is not None and run_count >= repeat_threshold:
            rep.repeated_failures.append(
                {"target": run_target, "consecutive_errors": run_count}
            )

    for c in calls:
        tool = c.tool
        if tool in HOST_BUILTINS:
            rep.host_builtin_calls += 1
        elif tool in SKILL_CHANNEL_TOOLS:
            if tool == "skill_mcp" and c.target.startswith(VT_PREFIX):
                rep.channel_confusions.append(c)
            elif tool == "vibe-trading_load_skill":
                # Skill names are prompt-contract governed; record only.
                pass
        elif tool.startswith(FOREIGN_MCP_PREFIXES):
            rep.foreign_namespace_calls.append(c)
        elif tool.startswith(VT_PREFIX):
            rep.governed_calls += 1
            bare = tool[len(VT_PREFIX) :]
            if whitelist is not None and bare not in whitelist:
                rep.whitelist_violations.append(c)

        # Dead-loop = consecutive errors to one target; args ignored (s5d flailing mutates them).
        if c.status == "error" and c.target == run_target:
            run_count += 1
        else:
            flush_run()
            if c.status == "error":
                run_target, run_count = c.target, 1
            else:
                run_target = None
                run_count = 0
    flush_run()
    return rep


def load_skill_names(db_path: str | Path, session_id: str) -> list[str]:
    """Skill names a session loaded via vibe-trading_load_skill (reporting aid)."""
    db = sqlite3.connect(str(db_path))
    try:
        rows = db.execute(
            "select data from part where session_id = ? and "
            "json_extract(data, '$.tool') = 'vibe-trading_load_skill' "
            "order by time_created",
            (session_id,),
        ).fetchall()
    finally:
        db.close()
    names = []
    for (blob,) in rows:
        name = (json.loads(blob).get("state") or {}).get("input", {}).get("name")
        if name:
            names.append(str(name))
    return names
