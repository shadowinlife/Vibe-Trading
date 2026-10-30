#!/usr/bin/env python3
"""Render opencode.json from the Jinja2 template + tool governance manifest.

Single source of truth for the container's opencode config assembly
(previously inlined in ``entrypoint.sh``). Responsibilities:

1. Render ``opencode.json.tmpl`` with the ClickHouse credentials from env.
2. Compile ``vibe-trading-tools.json`` (the tool governance manifest) into
   opencode ``permission`` deny entries, so disabled VT tools are removed
   from the model's visible tool surface instead of sitting in a file that
   opencode never reads.
3. Validate the final JSON before writing, including the VT MCP block whose
   local-server schema is strict (``environment``, not ``env``). Fail loud —
   ``entrypoint.sh`` owns the fallback path when rendering fails.

Rationale for trimming the visible tool surface: MCP tool definitions are
paid every planning turn, and tool-selection accuracy degrades past a few
dozen visible tools. Denying tools via opencode permissions removes them
from the model's tool list entirely.

Covered by ``OpencodeAgent/tests/test_config_render.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from jinja2 import Template

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = CONFIG_DIR / "opencode.json.tmpl"
DEFAULT_MANIFEST = CONFIG_DIR / "vibe-trading-tools.json"

#: opencode namespaces MCP tools as ``<server>_<tool>``; the vibe-trading
#: server keeps its hyphen (see the rendered tool names in any session).
VT_SERVER = "vibe-trading"

#: opencode's ``mcp.<name>`` local-server schema is ``additionalProperties:
#: false`` and names the environment map ``environment``. A legacy ``env`` key
#: is dropped without a warning, so the server would start without its
#: credentials — the block is validated instead of trusting the template.
VT_MCP_ENV_KEY = "environment"


def load_manifest(path: Path) -> dict:
    """Load and validate the tool governance manifest.

    Args:
        path: Path to ``vibe-trading-tools.json``.

    Returns:
        Parsed manifest dict with a validated ``disabled`` list.

    Raises:
        ValueError: If the manifest is not an object or ``disabled`` is not
            a list of strings.
    """
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    disabled = manifest.get("disabled", [])
    if not isinstance(disabled, list) or not all(
        isinstance(entry, str) and entry for entry in disabled
    ):
        raise ValueError("manifest 'disabled' must be a list of non-empty strings")
    return manifest


def build_permission_denies(manifest: dict) -> dict:
    """Compile manifest ``disabled`` entries into opencode permission denies.

    Entries are VT tool names or globs without the MCP server prefix
    (e.g. ``trading_*``); opencode sees MCP tools namespaced as
    ``vibe-trading_<name>``, and permission keys support glob patterns.

    Args:
        manifest: Parsed tool governance manifest.

    Returns:
        Mapping of namespaced tool glob to ``"deny"``.
    """
    return {f"{VT_SERVER}_{entry}": "deny" for entry in manifest.get("disabled", [])}


def validate_mcp_config(config: dict) -> None:
    """Assert the rendered config carries a usable VT MCP server block.

    Args:
        config: Rendered opencode config.

    Raises:
        ValueError: If the VT MCP block is missing or malformed.
    """
    mcp = config.get("mcp")
    if not isinstance(mcp, dict) or VT_SERVER not in mcp:
        raise ValueError(f"mcp.{VT_SERVER} missing from the rendered config")
    server = mcp[VT_SERVER]
    if not isinstance(server, dict):
        raise ValueError(f"mcp.{VT_SERVER} must be a JSON object")
    if server.get("type") != "local":
        raise ValueError(f"mcp.{VT_SERVER}.type must be 'local'")
    command = server.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ValueError(f"mcp.{VT_SERVER}.command must be a non-empty list of strings")
    if "env" in server:
        raise ValueError(
            f"mcp.{VT_SERVER} uses 'env'; opencode names the local MCP "
            f"environment map '{VT_MCP_ENV_KEY}' and silently drops 'env'"
        )
    environment = server.get(VT_MCP_ENV_KEY)
    if not isinstance(environment, dict) or "CLICKHOUSE_HOST" not in environment:
        raise ValueError(
            f"mcp.{VT_SERVER}.{VT_MCP_ENV_KEY} must carry the ClickHouse "
            f"and memory settings"
        )


def build_context() -> dict:
    """Collect template variables from the environment with defaults.

    Returns:
        Context dict for the Jinja2 template.
    """
    return {
        "CLICKHOUSE_HOST": os.environ.get("CLICKHOUSE_HOST", ""),
        "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", "8123"),
        "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.environ.get("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_DATABASE": os.environ.get("CLICKHOUSE_DATABASE", "ashare"),
        "CLICKHOUSE_LLM_USER": os.environ.get("CLICKHOUSE_LLM_USER", ""),
        "CLICKHOUSE_LLM_PASSWORD": os.environ.get("CLICKHOUSE_LLM_PASSWORD", ""),
        # LLM egress proxy (contract A.7): non-empty LLM_BASE_URL routes the
        # built-in alibaba-cn provider through the RM proxy (ID must stay
        # ``alibaba-cn`` or enable_thinking breaks); empty → block omitted,
        # output byte-identical to v3.3.0 (direct DashScope).
        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", ""),
        "LLM_API_KEY": os.environ.get("LLM_API_KEY", ""),
        # HOSTNAME = K8s pod name, PID1-visible and per-pod unique at
        # warm-pool boot (before claim), so x-sandbox-id attribution works.
        "SANDBOX_ID": os.environ.get("HOSTNAME") or os.uname().nodename,
    }


def render(template_path: Path, manifest_path: Path) -> str:
    """Render the final opencode.json content.

    Args:
        template_path: Path to ``opencode.json.tmpl``.
        manifest_path: Path to ``vibe-trading-tools.json``.

    Returns:
        Serialized JSON document (valid JSON, trailing newline).

    Raises:
        ValueError: If the template does not render to a JSON object or the
            manifest is invalid.
        jinja2.TemplateError: On template syntax/render errors.
        json.JSONDecodeError: On invalid template output.
    """
    with open(template_path, encoding="utf-8") as f:
        tmpl = Template(f.read())
    config = json.loads(tmpl.render(**build_context()))
    if not isinstance(config, dict):
        raise ValueError("opencode.json.tmpl must render to a JSON object")
    permission = config.setdefault("permission", {})
    if not isinstance(permission, dict):
        raise ValueError("opencode.json.tmpl 'permission' must be a JSON object")
    permission.update(build_permission_denies(load_manifest(manifest_path)))
    validate_mcp_config(config)
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    """CLI entry point used by ``entrypoint.sh``.

    Returns:
        Process exit code (0 on success, 1 on any failure).
    """
    parser = argparse.ArgumentParser(
        description="Render opencode.json from template + tool governance manifest."
    )
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    try:
        rendered = render(Path(args.template), Path(args.manifest))
        json.loads(rendered)  # final validation gate
    except Exception as exc:  # fail loud; entrypoint.sh owns the fallback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(target)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
