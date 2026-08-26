"""Authoritative inventory of the agent's internal (non-MCP) tool surface.

The agent tool registry (``src/tools/build_registry``) and the MCP surface
(``mcp_server.mcp``) grew side by side and drifted: some capabilities exist
under two names (internal ``pattern`` vs MCP ``pattern_recognition``), some
internal tools never reach MCP at all, and swarm presets whitelist internal
names directly. This script rebuilds the full picture from runtime truth:

1. Measure the registry in a clean child interpreter with the credential
   gates removed — the same protocol as
   ``tests/test_readme_counts.py::_keyless_agent_tool_count``: pop
   FRED_API_KEY / VIBE_TRADING_IWENCAI_KEY / QVERIS_API_KEY /
   VIBE_TW_STOCK_DB from the child env, keep shell tools off. The child also
   reports discovered-but-unregistered tools so gated tools stay visible.
2. Capture the MCP surface (``asyncio.run(mcp_server.mcp.list_tools())``) in
   a second child interpreter.
3. Diff the two, then annotate every non-MCP internal tool with its MCP
   counterpart (the HARNESS_EVOLUTION_CAPABILITY_AUDIT.md §8.1 mapping),
   swarm-preset references (``src/swarm/presets/*.yaml`` ``tools:``
   whitelists), skill-doc references (``src/skills/*/SKILL.md``), and its
   registration gate read statically off the tool module.
4. Reconcile against the partial internal-tool list in audit §2: audit-listed
   tools present at runtime are ``confirmed``, runtime tools absent from the
   audit list are ``new``, audit-listed tools missing from the keyless
   registry are ``audit-only`` with an explanation.

Artifacts (all lists sorted by name; ``captured_at`` is the only
non-deterministic field — pass ``--no-timestamp`` for byte-identical reruns):

    scripts/artifacts/internal_tool_inventory.json
    scripts/artifacts/internal_tool_inventory.md

Usage:
    cd agent && python scripts/inventory_internal_tools.py [--no-timestamp]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

AGENT_DIR = Path(__file__).resolve().parents[1]
PRESETS_DIR = AGENT_DIR / "src" / "swarm" / "presets"
SKILLS_DIR = AGENT_DIR / "src" / "skills"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
JSON_PATH = ARTIFACTS_DIR / "internal_tool_inventory.json"
MD_PATH = ARTIFACTS_DIR / "internal_tool_inventory.md"

# Credential gates closed while measuring, exactly like the keyless count in
# tests/test_readme_counts.py. Only the names are published in artifacts —
# never values.
CREDENTIAL_GATES = (
    "FRED_API_KEY",
    "QVERIS_API_KEY",
    "VIBE_TRADING_IWENCAI_KEY",
    "VIBE_TW_STOCK_DB",
)

SHELL_TOOL_NAMES = frozenset({"bash", "background_run", "cancel_background"})
SHELL_GATE = "entrypoint flag --enable-shell-tools / env VIBE_TRADING_ENABLE_SHELL_TOOLS=1"

# MARKERS searched statically inside each tool module to name its gate.
# Pairs of (gate label, substrings that identify it). The lowercase forms are
# the pydantic EnvConfig field names from src/config/env_schema.py, which the
# tool modules reference as ``get_env_config().data.<field>``.
GATE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("env FRED_API_KEY", ("FRED_API_KEY", "fred_api_key")),
    ("env VIBE_TRADING_IWENCAI_KEY", ("VIBE_TRADING_IWENCAI_KEY", "vibe_trading_iwencai_key")),
    ("env QVERIS_API_KEY + paid mode", ("QVERIS_API_KEY", "qveris_api_key", "is_qveris_configured")),
    ("env VIBE_TW_STOCK_DB (schema-valid SQLite snapshot)", ("VIBE_TW_STOCK_DB", "vibe_tw_stock_db")),
)

# Internal-name -> MCP-name mapping, HARNESS_EVOLUTION_CAPABILITY_AUDIT.md
# §8.1 (port mapping table). ``None`` = the audit states there is no MCP
# equivalent. Nothing outside this table is ever mapped.
MCP_COUNTERPARTS: dict[str, str | None] = {
    "options_pricing": "analyze_options",
    "options_payoff": "analyze_options_payoff",
    "pattern": "pattern_recognition",
    "edit_file": "write_file",
    "financial_rigor": None,
    "report_audit": None,
    "sdm_register": None,
    "sdm_status": None,
    "sdm_decay_scan": None,
}

# Partial internal-tool list from HARNESS_EVOLUTION_CAPABILITY_AUDIT.md §2
# ("VT 内部工具面" table, lines ~418-433): audit row label -> runtime tool
# names it expands to. Group labels expand to every tool of the family:
# ``skill_writer`` is the skill-CRUD family in skill_writer_tool.py,
# ``hypothesis`` is the hypothesis-registry family in hypothesis_tool.py, and
# the audit's ``taiwan_stock_data`` registers as ``get_taiwan_stock_data``.
AUDIT_INTERNAL_TOOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("options_pricing", ("options_pricing",)),
    ("options_payoff", ("options_payoff",)),
    ("pattern", ("pattern",)),
    ("financial_rigor", ("financial_rigor",)),
    ("report_audit", ("report_audit",)),
    ("edit_file", ("edit_file",)),
    ("bash / background_run / cancel_background", ("bash", "background_run", "cancel_background")),
    ("remember", ("remember",)),
    ("skill_writer", ("delete_skill", "patch_skill", "save_skill", "skill_file")),
    ("sdm_register / sdm_status / sdm_decay_scan", ("sdm_decay_scan", "sdm_register", "sdm_status")),
    ("scheduled_research", ("scheduled_research",)),
    (
        "hypothesis / run_research_autopilot / scaffold_signal_engine / link_autopilot_backtest",
        (
            "create_hypothesis",
            "link_autopilot_backtest",
            "link_backtest",
            "run_research_autopilot",
            "scaffold_signal_engine",
            "search_hypotheses",
            "update_hypothesis",
        ),
    ),
    ("portfolio_summary / portfolio_risk_xray", ("portfolio_risk_xray", "portfolio_summary")),
    ("taiwan_stock_data", ("get_taiwan_stock_data",)),
)

AUDIT_ONLY_EXPLANATIONS: dict[str, str] = {
    "bash": (
        "Not registered in the keyless environment: shell tools register only when the "
        "entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); "
        "never exposed on MCP by design."
    ),
    "background_run": (
        "Not registered in the keyless environment: shell tools register only when the "
        "entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); "
        "never exposed on MCP by design."
    ),
    "cancel_background": (
        "Not registered in the keyless environment: shell tools register only when the "
        "entry point enables them (--enable-shell-tools / VIBE_TRADING_ENABLE_SHELL_TOOLS=1); "
        "never exposed on MCP by design."
    ),
    "get_taiwan_stock_data": (
        "Not registered in the keyless environment: check_available requires VIBE_TW_STOCK_DB "
        "to point at a schema-valid SQLite snapshot; agent-side only, never exposed on MCP."
    ),
}

# Child snippet 1: full registry measurement. Emits one JSON object on the
# last stdout line. _discover_subclasses sees gated tools too (check_available
# is evaluated per class), so unregistered tools stay visible to the diff.
_REGISTRY_CHILD = r"""
import json
import re

from src.tools import _discover_subclasses, build_registry


def first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]


registry = build_registry()
registered = set(registry.tool_names)
tools = []
for cls in _discover_subclasses():
    tools.append(
        {
            "name": cls.name,
            "class": cls.__name__,
            "module": cls.__module__,
            "purpose": first_sentence(cls.description),
            "check_available": bool(cls.check_available()),
            "registered": cls.name in registered,
        }
    )
payload = {
    "registered": sorted(registered),
    "tools": sorted(tools, key=lambda item: item["name"]),
    "import_failures": dict(registry.import_failures),
    "registration_failures": dict(registry.registration_failures),
}
print(json.dumps(payload, ensure_ascii=False))
"""

# Child snippet 2: the MCP surface, exactly as tests/test_readme_counts.py
# enumerates it.
_MCP_CHILD = r"""
import asyncio
import json
import re

import mcp_server


def first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]


tools = asyncio.run(mcp_server.mcp.list_tools())
payload = [
    {"name": tool.name, "purpose": first_sentence(tool.description or "")}
    for tool in tools
]
payload.sort(key=lambda item: item["name"])
print(json.dumps(payload, ensure_ascii=False))
"""


class InventoryError(RuntimeError):
    """The inventory could not be built; nothing was written."""


def _keyless_env() -> dict[str, str]:
    """Return the child environment with every credential gate popped.

    Returns:
        Copy of os.environ minus CREDENTIAL_GATES.
    """
    env = dict(os.environ)
    for name in CREDENTIAL_GATES:
        env.pop(name, None)
    return env


def _run_child(code: str, env: dict[str, str], label: str) -> Any:
    """Run a measurement snippet in a clean child interpreter.

    Args:
        code: Python source executed with ``-c`` from cwd=agent/.
        env: Child environment (credential gates already removed).
        label: Human-readable measurement name for error messages.

    Returns:
        The JSON value printed on the child's last stdout line.

    Raises:
        InventoryError: The child failed, timed out, or printed unparseable
            output. Never returns a partial measurement.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=AGENT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise InventoryError(f"{label}: child interpreter timed out after 600s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-2000:]
        raise InventoryError(
            f"{label}: child interpreter exited with code {proc.returncode}\n{tail}"
        )
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise InventoryError(f"{label}: child interpreter produced no output")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise InventoryError(
            f"{label}: child output is not JSON ({exc}); first 500 chars: {proc.stdout[:500]!r}"
        ) from exc


def _capture_registry(env: dict[str, str]) -> dict[str, Any]:
    """Measure the keyless registry, failing loudly on any shrinkage signal.

    Args:
        env: Child environment with credential gates removed.

    Returns:
        The child's registry payload (registered names, per-class rows,
        failure maps).

    Raises:
        InventoryError: The import failed or any tool module/instantiation
            failed — the registry would be silently short otherwise (the
            incident behind README 2026-08-18 / issue #1124).
    """
    payload = _run_child(_REGISTRY_CHILD, env, "registry measurement")
    import_failures = payload.get("import_failures") or {}
    registration_failures = payload.get("registration_failures") or {}
    if import_failures or registration_failures:
        named = "; ".join(
            f"src.tools.{module}: {reason}" for module, reason in sorted(import_failures.items())
        ) + "; ".join(
            f"{name}: {reason}" for name, reason in sorted(registration_failures.items())
        )
        raise InventoryError(
            f"registry measurement is incomplete — failing tool modules are named, "
            f"never silently dropped: {named.strip('; ')}"
        )
    if not payload.get("registered"):
        raise InventoryError("registry measurement returned zero tools")
    return payload


def _swarm_refs() -> dict[str, list[dict[str, str]]]:
    """Parse every bundled swarm preset's per-agent ``tools:`` whitelist.

    Returns:
        Tool name -> sorted unique [{"preset", "agent_id", "role"}] refs.
    """
    refs: dict[str, dict[tuple[str, str, str], None]] = {}
    for path in sorted(PRESETS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        preset = str(data.get("name") or path.stem)
        for agent in data.get("agents") or []:
            agent_id = str(agent.get("id") or "?")
            role = str(agent.get("role") or agent_id)
            tools = agent.get("tools") or []
            if isinstance(tools, str):
                tools = [tools]
            for tool_name in tools:
                refs.setdefault(str(tool_name), {})[(preset, agent_id, role)] = None
    return {
        name: [
            {"preset": preset, "agent_id": agent_id, "role": role}
            for preset, agent_id, role in sorted(keys)
        ]
        for name, keys in sorted(refs.items())
    }


def _skill_refs(tool_names: set[str]) -> dict[str, list[str]]:
    """Grep bundled skill docs for whole-word references to each tool name.

    Args:
        tool_names: Internal tool names to look for.

    Returns:
        Tool name -> sorted list of skill directory names whose SKILL.md
        mentions the name on a word boundary (case-sensitive).
    """
    docs: list[tuple[str, str]] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        docs.append((skill_md.parent.name, skill_md.read_text(encoding="utf-8")))
    result: dict[str, list[str]] = {}
    for name in sorted(tool_names):
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        result[name] = sorted(
            skill for skill, text in docs if pattern.search(text) is not None
        )
    return result


def _module_gate(module: str) -> str:
    """Statically identify a tool module's registration gate.

    Args:
        module: Dotted module path, e.g. ``src.tools.fred_macro_tool``.

    Returns:
        Gate label(s) found in the module source, or "none".
    """
    path = AGENT_DIR / Path(*module.split(".")).with_suffix(".py")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "none"
    found = [label for label, markers in GATE_MARKERS if any(m in text for m in markers)]
    return "; ".join(found) if found else "none"


def _gate_for(name: str, module: str, registered: bool) -> str:
    """Resolve the gate annotation for one tool.

    Args:
        name: Tool name.
        module: Dotted module path of the tool class.
        registered: Whether the tool registered in the keyless environment.

    Returns:
        Human-readable gate label.
    """
    if name in SHELL_TOOL_NAMES:
        return SHELL_GATE
    gate = _module_gate(module)
    if gate == "none" and not registered:
        return "unregistered in keyless env (check_available False; gate not statically identified)"
    return gate


def _audit_runtime_map() -> dict[str, str]:
    """Expand the audit §2 rows into runtime tool names.

    Returns:
        Runtime tool name -> audit row label that lists it.
    """
    audit_map: dict[str, str] = {}
    for label, runtime_names in AUDIT_INTERNAL_TOOLS:
        for runtime_name in runtime_names:
            audit_map[runtime_name] = label
    return audit_map


def _reconcile(
    registry_rows: dict[str, dict[str, Any]],
    internal_names: set[str],
    mcp_names: set[str],
    audit_map: dict[str, str],
    registered_names: set[str],
) -> dict[str, Any]:
    """Reconcile the runtime inventory against the audit §2 partial list.

    Args:
        registry_rows: Discovered-tool rows keyed by name (registered or not).
        internal_names: Registered tools absent from the MCP surface.
        mcp_names: MCP surface tool names.
        audit_map: Runtime tool name -> audit row label.
        registered_names: Tools registered in the keyless environment.

    Returns:
        The reconciliation block for the JSON artifact.
    """
    confirmed = sorted(name for name in audit_map if name in registered_names)
    audit_only_rows = []
    for name in sorted(audit_map):
        if name in registered_names:
            continue
        audit_only_rows.append(
            {
                "audit_name": audit_map[name],
                "runtime_name": name,
                "explanation": AUDIT_ONLY_EXPLANATIONS.get(
                    name,
                    "Listed in the audit but not registered in the keyless environment.",
                ),
            }
        )
    new_since_audit = sorted(
        (
            {"name": name, "purpose": registry_rows[name]["purpose"]}
            for name in internal_names
            if name not in audit_map
        ),
        key=lambda item: item["name"],
    )
    gated_not_registered = []
    for name in sorted(set(registry_rows) - registered_names):
        row = registry_rows[name]
        gated_not_registered.append(
            {
                "name": name,
                "gate": _gate_for(name, row["module"], registered=False),
                "on_mcp_surface": name in mcp_names,
                "note": (
                    "Exposed on the MCP surface; hidden from the keyless agent registry by its gate."
                    if name in mcp_names
                    else "Agent-side only; not exposed on the MCP surface."
                ),
            }
        )
    return {
        "audit_document": "HARNESS_EVOLUTION_CAPABILITY_AUDIT.md §2 (VT internal tool rows, lines ~418-433)",
        "audit_rows": len(AUDIT_INTERNAL_TOOLS),
        "audit_runtime_names": sorted(audit_map),
        "confirmed": confirmed,
        "new_since_audit": new_since_audit,
        "audit_only": audit_only_rows,
        "gated_not_registered": gated_not_registered,
    }


def _build_inventory(include_timestamp: bool) -> dict[str, Any]:
    """Run both measurements and assemble the full inventory payload.

    Args:
        include_timestamp: Emit the real capture time; False writes null so
            reruns are byte-identical.

    Returns:
        The JSON-serializable inventory.

    Raises:
        InventoryError: Any measurement failed.
    """
    env = _keyless_env()
    registry_payload = _capture_registry(env)
    mcp_payload = _run_child(_MCP_CHILD, env, "MCP surface measurement")

    registry_rows: dict[str, dict[str, Any]] = {
        row["name"]: row for row in registry_payload["tools"]
    }
    registered_names: set[str] = set(registry_payload["registered"])
    mcp_names: set[str] = {tool["name"] for tool in mcp_payload}
    internal_names = sorted(registered_names - mcp_names)
    audit_map = _audit_runtime_map()
    audit_only_names = sorted(set(audit_map) - registered_names)

    for counterpart in MCP_COUNTERPARTS.values():
        if counterpart is not None and counterpart not in mcp_names:
            raise InventoryError(
                f"audit §8.1 mapping is stale: MCP counterpart {counterpart!r} "
                f"is not on the MCP surface"
            )

    swarm = _swarm_refs()
    skills = _skill_refs(set(internal_names) | set(audit_only_names))

    tools: list[dict[str, Any]] = []
    for name in sorted(registered_names):
        row = registry_rows[name]
        is_mcp = name in mcp_names
        if name in MCP_COUNTERPARTS:
            counterpart = MCP_COUNTERPARTS[name]
            status = "mapped" if counterpart else "no-equivalent"
        elif is_mcp:
            counterpart, status = name, "is-mcp-tool"
        else:
            counterpart, status = None, "no-equivalent"
        tools.append(
            {
                "name": name,
                "class": row["class"],
                "module": row["module"],
                "purpose": row["purpose"],
                "mcp_counterpart": counterpart,
                "mcp_counterpart_status": status,
                "swarm_refs": swarm.get(name, []),
                "skill_refs": [] if is_mcp else skills.get(name, []),
                "gate": _gate_for(name, row["module"], registered=True),
                "registered_keyless": True,
                "audit_status": "confirmed" if name in audit_map else "new",
            }
        )
    for name in audit_only_names:
        row = registry_rows.get(name)
        tools.append(
            {
                "name": name,
                "class": row["class"] if row else None,
                "module": row["module"] if row else None,
                "purpose": row["purpose"] if row else "",
                "mcp_counterpart": MCP_COUNTERPARTS.get(name),
                "mcp_counterpart_status": (
                    "mapped" if MCP_COUNTERPARTS.get(name) else "no-equivalent"
                ),
                "swarm_refs": swarm.get(name, []),
                "skill_refs": skills.get(name, []),
                "gate": _gate_for(name, row["module"] if row else "", registered=False),
                "registered_keyless": False,
                "audit_status": "audit-only",
            }
        )
    tools.sort(key=lambda item: item["name"])

    reconciliation = _reconcile(
        registry_rows, set(internal_names), mcp_names, audit_map, registered_names
    )
    return {
        "captured_at": (
            datetime.now(timezone.utc).isoformat(timespec="seconds") if include_timestamp else None
        ),
        "env_gates_cleared": sorted(CREDENTIAL_GATES),
        "registry_total": len(registered_names),
        "mcp_total": len(mcp_names),
        "internal_total": len(internal_names),
        "tools": tools,
        "reconciliation": reconciliation,
        "mcp_surface_not_in_keyless_registry": sorted(mcp_names - registered_names),
    }


def _md_cell(text: str) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return str(text).replace("|", "\\|")


def _swarm_cell(refs: list[dict[str, str]]) -> str:
    """Render swarm refs as ``preset ×N`` (role detail lives in the JSON)."""
    if not refs:
        return "—"
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref["preset"]] = counts.get(ref["preset"], 0) + 1
    return ", ".join(
        f"{preset} ×{count}" if count > 1 else preset
        for preset, count in sorted(counts.items())
    )


def _render_markdown(inventory: dict[str, Any]) -> str:
    """Render the human-readable artifact.

    Args:
        inventory: The payload returned by _build_inventory.

    Returns:
        Markdown text for internal_tool_inventory.md.
    """
    rec = inventory["reconciliation"]
    captured = inventory["captured_at"] or "(deterministic mode: --no-timestamp)"
    lines: list[str] = [
        "# Vibe-Trading Internal Tool Inventory",
        "",
        f"Captured at: {captured} · Credential gates cleared in child env: "
        + ", ".join(inventory["env_gates_cleared"]),
        "",
        "Protocol: keyless registry measured in a clean subprocess exactly like "
        "`tests/test_readme_counts.py::_keyless_agent_tool_count` (shell tools off); "
        "MCP surface via `asyncio.run(mcp_server.mcp.list_tools())`. Runtime is authoritative.",
        "",
        "## Totals",
        "",
        f"- Agent registry (keyless): **{inventory['registry_total']}**",
        f"- MCP surface: **{inventory['mcp_total']}**",
        f"- Internal tools (registered, absent from MCP): **{inventory['internal_total']}**",
        f"- Audit-only tools (listed in audit §2, not registered keyless): "
        f"**{len(rec['audit_only'])}**",
        f"- Discovered but gated out of the keyless registry: "
        f"**{len(rec['gated_not_registered'])}**",
        f"- MCP surface tools not in the keyless registry: "
        f"**{len(inventory['mcp_surface_not_in_keyless_registry'])}**",
        "",
        "## Tool table",
        "",
        "`mcp_counterpart_status`: mapped · no-equivalent · is-mcp-tool. "
        "Swarm refs list preset whitelists (agent roles in the JSON). "
        "Entries marked audit-only are not registered in the keyless environment.",
        "",
        "| name | purpose | MCP counterpart | swarm refs | skill refs | gate |",
        "|---|---|---|---|---|---|",
    ]
    for tool in inventory["tools"]:
        counterpart = tool["mcp_counterpart"] or "— (no equivalent)"
        if tool["mcp_counterpart_status"] == "is-mcp-tool":
            counterpart = "(on MCP surface)"
        suffix = "" if tool["registered_keyless"] else " — audit-only"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(tool["name"]) + suffix,
                    _md_cell(tool["purpose"]),
                    _md_cell(counterpart),
                    _md_cell(_swarm_cell(tool["swarm_refs"])),
                    _md_cell(", ".join(tool["skill_refs"]) or "—"),
                    _md_cell(tool["gate"]),
                ]
            )
            + " |"
        )

    lines += ["", "## Reconciliation against audit §2", ""]
    lines.append(
        f"Audit partial list: {rec['audit_rows']} rows expanding to "
        f"{len(rec['audit_runtime_names'])} runtime tool names. "
        f"Confirmed at runtime: {len(rec['confirmed'])}."
    )
    lines += ["", "### Confirmed (audit-listed and registered keyless)", ""]
    lines.append(", ".join(f"`{name}`" for name in rec["confirmed"]))
    lines += ["", "### New since audit (internal, registered, not in the audit list)", ""]
    lines.append("| name | purpose |")
    lines.append("|---|---|")
    for item in rec["new_since_audit"]:
        lines.append(f"| {_md_cell(item['name'])} | {_md_cell(item['purpose'])} |")
    lines += ["", "### Audit-only (listed in audit §2, missing from the keyless registry)", ""]
    lines.append("| audit row | runtime name | explanation |")
    lines.append("|---|---|---|")
    for item in rec["audit_only"]:
        lines.append(
            f"| {_md_cell(item['audit_name'])} | {_md_cell(item['runtime_name'])} "
            f"| {_md_cell(item['explanation'])} |"
        )
    lines += ["", "### Discovered but gated out of the keyless registry", ""]
    lines.append("| name | gate | on MCP surface | note |")
    lines.append("|---|---|---|---|")
    for item in rec["gated_not_registered"]:
        lines.append(
            f"| {_md_cell(item['name'])} | {_md_cell(item['gate'])} "
            f"| {'yes' if item['on_mcp_surface'] else 'no'} | {_md_cell(item['note'])} |"
        )
    lines += [
        "",
        "## Audit §8.1 name mapping (as encoded)",
        "",
        "| internal tool | MCP counterpart |",
        "|---|---|",
    ]
    for name in sorted(MCP_COUNTERPARTS):
        lines.append(f"| {name} | {MCP_COUNTERPARTS[name] or '— (no equivalent)'} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Build the inventory and write both artifacts.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Process exit code: 0 on success, 1 on InventoryError.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="write null for captured_at so reruns are byte-identical",
    )
    args = parser.parse_args(argv)

    try:
        inventory = _build_inventory(include_timestamp=not args.no_timestamp)
    except InventoryError as exc:
        print(f"InventoryError: {exc}", file=sys.stderr)
        return 1

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    MD_PATH.write_text(_render_markdown(inventory), encoding="utf-8")
    print(
        f"registry={inventory['registry_total']} mcp={inventory['mcp_total']} "
        f"internal={inventory['internal_total']} "
        f"audit_only={len(inventory['reconciliation']['audit_only'])} -> {JSON_PATH}, {MD_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
