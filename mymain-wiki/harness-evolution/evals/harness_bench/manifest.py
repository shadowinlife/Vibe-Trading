"""Canonical tool manifest: the REAL ``tools/list`` surface, pinned and hashed.

The manifest (``canonical_tool_manifest.json``) is captured by spawning this
repo's MCP server as a stdio subprocess — the exact transport and handshake
of the existing smoke test (``mcp_spawn.McpStdioClient``) — under a PINNED
environment recorded in its ``env_pin`` block:

* ``VT_MEMORY_MCP_TOOLS`` fixed to ``"1"`` — the baseline opencode+OMO
  deployment runs with memory tools enabled
  (``OpencodeAgent/config/opencode.json.tmpl``), so the canonical surface is
  captured with them on.
* ``VIBE_TRADING_ENABLE_SHELL_TOOLS`` forced absent (shell tools are an
  opt-in RCE surface and are not part of the eval surface).
* ClickHouse and API-key credential env vars pinned to the state recorded at
  emit time — presence booleans only, values are never written. The capture
  forces each pinned-absent var absent in the child, so ``--check`` is
  deterministic on any host, credential-free.

Callability classification per tool (plan semantics):

* ``normal`` — returns a well-formed envelope.
* ``credential_gated`` — documented not-available envelope while its gating
  credential is absent; still counts as CALLABLE per the plan.
* ``governance_disabled`` — disabled by the baseline governance manifest
  (``OpencodeAgent/config/vibe-trading-tools.json``, pattern ``trading_*``).

Every entry also carries ``schema_sha256`` — the sha256 of its canonicalized
input schema — so todos 11/15 can assert surface integrity. The manifest's
captured env + tool count is the ONLY authority for tool-count thresholds
(not the README's 77).

CLI (from ``agent/``):

    python -m src.evals.harness_bench.manifest --emit
    python -m src.evals.harness_bench.manifest --check [--manifest PATH]

``--check`` re-captures and diffs; exit 1 on drift, naming added/removed
tools and schema-digest changes. Exit 0 when the surface matches.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.evals.harness_bench import mcp_spawn
from src.evals.harness_bench.report import current_git_commit

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[3]
MANIFEST_PATH = PKG_DIR / "canonical_tool_manifest.json"
GOVERNANCE_MANIFEST_PATH = (
    REPO_ROOT / "OpencodeAgent" / "config" / "vibe-trading-tools.json"
)

MANIFEST_VERSION = "1.0"

#: Pinned value for the memory-tools switch (baseline deployment parity).
MEMORY_TOOLS_PIN = "1"

#: Env var whose presence would widen the surface with shell tools; the
#: canonical capture always forces it absent.
SHELL_TOOLS_ENV = "VIBE_TRADING_ENABLE_SHELL_TOOLS"

#: Credential-gated tools and the env vars that gate them (mcp_server.py's
#: ``_key_gated_tool_classes`` plus the ClickHouse llm_role channel).
CREDENTIAL_GATES: dict[str, tuple[str, ...]] = {
    "get_macro_series": ("FRED_API_KEY",),
    "iwencai_search": ("VIBE_TRADING_IWENCAI_KEY",),
    "qveris_search": ("QVERIS_API_KEY",),
    "qveris_inspect": ("QVERIS_API_KEY",),
    "qveris_execute": ("QVERIS_API_KEY",),
    "ch_list_tables": ("CLICKHOUSE_LLM_USER", "CLICKHOUSE_LLM_PASSWORD"),
    "ch_describe_table": ("CLICKHOUSE_LLM_USER", "CLICKHOUSE_LLM_PASSWORD"),
    "ch_query": ("CLICKHOUSE_LLM_USER", "CLICKHOUSE_LLM_PASSWORD"),
}

#: ClickHouse env vars whose PRESENCE (never value) is recorded in env_pin.
CLICKHOUSE_ENV_VARS: tuple[str, ...] = (
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_LLM_USER",
    "CLICKHOUSE_LLM_PASSWORD",
)

ALL_PINNED_CREDENTIAL_VARS: tuple[str, ...] = tuple(
    sorted(
        {var for gates in CREDENTIAL_GATES.values() for var in gates}
        | set(CLICKHOUSE_ENV_VARS)
    )
)

CALLABILITY_NORMAL = "normal"
CALLABILITY_CREDENTIAL_GATED = "credential_gated"
CALLABILITY_GOVERNANCE_DISABLED = "governance_disabled"
CALLABILITY_VALUES = (
    CALLABILITY_NORMAL,
    CALLABILITY_CREDENTIAL_GATED,
    CALLABILITY_GOVERNANCE_DISABLED,
)


class ManifestError(RuntimeError):
    """Manifest capture or check failed."""


def schema_digest(input_schema: Any) -> str:
    """sha256 of the canonicalized (sorted, compact) input-schema JSON."""
    canonical = json.dumps(input_schema or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_governance_disabled_patterns() -> list[str]:
    """Disabled tool patterns from the baseline governance manifest."""
    try:
        data = json.loads(GOVERNANCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read governance manifest: {exc}") from exc
    patterns = data.get("disabled") or []
    return [str(p) for p in patterns]


def record_env_presence() -> dict[str, bool]:
    """Presence booleans (never values) for every pinned credential var."""
    return {var: bool(os.environ.get(var)) for var in ALL_PINNED_CREDENTIAL_VARS}


def build_capture_env(env_pin: dict[str, Any]) -> dict[str, str | None]:
    """Child-env overrides enforcing the pinned state.

    Pinned-absent credential vars are deleted (``None``); pinned-present ones
    are passed through from the host. ``VT_MEMORY_MCP_TOOLS`` is set to the
    pinned value and the shell-tools switch is forced absent.
    """
    overrides: dict[str, str | None] = {
        "VT_MEMORY_MCP_TOOLS": str(
            env_pin.get("VT_MEMORY_MCP_TOOLS", MEMORY_TOOLS_PIN)
        ),
        SHELL_TOOLS_ENV: None,
    }
    presence = env_pin.get("credential_env_presence") or {}
    for var, present in presence.items():
        if not present:
            overrides[var] = None
    return overrides


def classify_tool(
    name: str,
    governance_patterns: list[str],
    env_presence: dict[str, bool],
) -> tuple[str, list[str]]:
    """Return ``(callability, gated_by)`` for one tool under the pinned env."""
    if any(fnmatch.fnmatch(name, pattern) for pattern in governance_patterns):
        return CALLABILITY_GOVERNANCE_DISABLED, []
    gates = CREDENTIAL_GATES.get(name)
    if gates:
        missing = [var for var in gates if not env_presence.get(var)]
        if missing:
            return CALLABILITY_CREDENTIAL_GATED, list(gates)
    return CALLABILITY_NORMAL, list(gates) if gates else []


def capture_tools(env_pin: dict[str, Any]) -> list[dict]:
    """Spawn the real MCP server under the pinned env and return tools/list."""
    try:
        return mcp_spawn.spawn_and_list_tools(env_overrides=build_capture_env(env_pin))
    except mcp_spawn.McpSpawnError as exc:
        raise ManifestError(f"MCP capture failed: {exc}") from exc


def build_manifest(raw_tools: list[dict], env_pin: dict[str, Any]) -> dict[str, Any]:
    """Assemble the manifest dict from a raw tools/list capture."""
    governance_patterns = load_governance_disabled_patterns()
    presence = env_pin["credential_env_presence"]
    entries = []
    for index, tool in enumerate(raw_tools):
        name = str(tool.get("name", ""))
        if not name:
            raise ManifestError(f"tools/list entry {index} has no name")
        callability, gated_by = classify_tool(name, governance_patterns, presence)
        entries.append(
            {
                "name": name,
                "registration_index": index,
                "callability": callability,
                "gated_by": gated_by,
                "schema_sha256": schema_digest(tool.get("inputSchema")),
            }
        )
    entries.sort(key=lambda entry: entry["name"])
    return {
        "manifest_version": MANIFEST_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_count": len(entries),
        "env_pin": env_pin,
        "governance_disabled_patterns": governance_patterns,
        "tools": entries,
    }


def make_env_pin() -> dict[str, Any]:
    """The pinned capture environment block (current credential state)."""
    return {
        "VT_MEMORY_MCP_TOOLS": MEMORY_TOOLS_PIN,
        "shell_tools_forced_absent": True,
        "credential_env_presence": record_env_presence(),
        "python": platform.python_version(),
        "git_commit": current_git_commit(),
        "server_command": [
            "<python>",
            "-c",
            "from mcp_server import main; main()",
        ],
        "governance_manifest": str(GOVERNANCE_MANIFEST_PATH.relative_to(REPO_ROOT)),
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Serialize deterministically: envelope pretty, one tool entry per line."""
    lines = ["{"]
    lines.append(f'  "manifest_version": {json.dumps(manifest["manifest_version"])},')
    lines.append(f'  "captured_at": {json.dumps(manifest["captured_at"])},')
    lines.append(f'  "tool_count": {manifest["tool_count"]},')
    lines.append(
        '  "env_pin": '
        + json.dumps(manifest["env_pin"], sort_keys=True, indent=4)
        + ","
    )
    lines.append(
        '  "governance_disabled_patterns": '
        + json.dumps(manifest["governance_disabled_patterns"])
        + ","
    )
    lines.append('  "tools": [')
    for entry in manifest["tools"][:-1]:
        lines.append(
            "    " + json.dumps(entry, sort_keys=True, separators=(",", ":")) + ","
        )
    if manifest["tools"]:
        lines.append(
            "    "
            + json.dumps(manifest["tools"][-1], sort_keys=True, separators=(",", ":"))
        )
    lines.append("  ]")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def diff_manifests(committed: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Drift report between two manifests (empty lists == no drift)."""
    committed_tools = {t["name"]: t for t in committed.get("tools", [])}
    fresh_tools = {t["name"]: t for t in fresh.get("tools", [])}
    added = sorted(set(fresh_tools) - set(committed_tools))
    removed = sorted(set(committed_tools) - set(fresh_tools))
    schema_changed = sorted(
        name
        for name in set(committed_tools) & set(fresh_tools)
        if committed_tools[name]["schema_sha256"] != fresh_tools[name]["schema_sha256"]
    )
    callability_changed = sorted(
        name
        for name in set(committed_tools) & set(fresh_tools)
        if committed_tools[name]["callability"] != fresh_tools[name]["callability"]
    )
    return {
        "added": added,
        "removed": removed,
        "schema_changed": schema_changed,
        "callability_changed": callability_changed,
    }


def emit(path: Path) -> dict[str, Any]:
    """Capture under the pinned env and write the manifest artifact."""
    env_pin = make_env_pin()
    raw_tools = capture_tools(env_pin)
    manifest = build_manifest(raw_tools, env_pin)
    if manifest["tool_count"] != len(raw_tools):
        raise ManifestError("internal error: tool_count != len(tools/list)")
    write_manifest(manifest, path)
    histogram: dict[str, int] = {}
    for entry in manifest["tools"]:
        histogram[entry["callability"]] = histogram.get(entry["callability"], 0) + 1
    print(f"manifest written: {path}")
    print(
        f"tool_count: {manifest['tool_count']} (tools/list returned {len(raw_tools)})"
    )
    for key in sorted(histogram):
        print(f"  {key}: {histogram[key]}")
    return manifest


def check(path: Path) -> int:
    """Re-capture under the COMMITTED pin and diff; exit 1 on drift."""
    try:
        committed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read committed manifest {path}: {exc}", file=sys.stderr)
        return 2
    env_pin = committed.get("env_pin") or make_env_pin()
    raw_tools = capture_tools(env_pin)
    fresh = build_manifest(raw_tools, env_pin)
    drift = diff_manifests(committed, fresh)
    if not any(drift.values()):
        print(
            f"manifest check OK: {fresh['tool_count']} tools, no drift vs {path.name}"
        )
        return 0
    print(f"manifest DRIFT detected vs {path}:", file=sys.stderr)
    for key in ("added", "removed", "schema_changed", "callability_changed"):
        if drift[key]:
            print(f"  {key}: {drift[key]}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical MCP tool manifest.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--emit", action="store_true", help="capture and write the manifest"
    )
    group.add_argument(
        "--check", action="store_true", help="re-capture and diff (exit 1 on drift)"
    )
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="manifest path")
    args = parser.parse_args(argv)
    path = Path(args.manifest)
    try:
        if args.emit:
            emit(path)
            return 0
        return check(path)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
