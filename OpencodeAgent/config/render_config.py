#!/usr/bin/env python3
"""Render opencode.json from the Jinja2 template + tool governance manifest.

Single source of truth for the container's opencode config assembly
(previously inlined in ``entrypoint.sh``). Responsibilities:

1. Render ``opencode.json.tmpl`` with the ClickHouse credentials from env.
2. Compile ``vibe-trading-tools.json`` (the tool governance manifest) into
   opencode ``permission`` deny entries, so disabled VT tools are removed
   from the model's visible tool surface instead of sitting in a file that
   opencode never reads.
3. Compile ``subagents.json`` (domain subagent manifests) into ``agent.<name>``
   sections: each subagent gets a deny-all permission gate across EVERY MCP
   namespace present in the rendered template (not just vibe-trading — a
   deny scoped to one namespace leaks sibling servers), then an explicit
   whitelist of allowed VT tools. ``main()`` additionally copies the
   referenced prompt files next to the rendered config, because opencode's
   ``{file:}`` loader silently drops references escaping the config
   directory's subtree.
4. Validate the final JSON before writing. Fail loud — ``entrypoint.sh``
   owns the fallback path when rendering fails.

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
DEFAULT_SUBAGENTS = CONFIG_DIR / "subagents.json"

#: opencode namespaces MCP tools as ``<server>_<tool>``; the vibe-trading
#: server keeps its hyphen (see the rendered tool names in any session).
VT_SERVER = "vibe-trading"

#: VT infrastructure tools every domain subagent needs so it can load its
#: whitelisted skills at runtime (the skill whitelist itself is enforced at
#: the prompt layer, matching the D-batch L2-validated configuration).
SKILL_LOADER_TOOLS = ("list_skills", "load_skill")

#: MCP namespaces injected by the oh-my-openagent plugin at runtime (see
#: createBuiltinMcps in the plugin bundle). They never appear in the
#: template's ``mcp`` section, so namespace derivation from the template
#: alone would leave them reachable from a subagent — the exact
#: cross-namespace leak the D-batch L2 runs exposed with
#: ``websearch_web_search_exa``. Keep in sync when the plugin is upgraded.
OMO_BUILTIN_NAMESPACES = ("websearch", "context7", "grep_app", "lsp")


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


def load_subagents(path: Path) -> list[dict]:
    """Load and validate the domain subagent manifest.

    Args:
        path: Path to ``subagents.json``.

    Returns:
        Validated list of subagent entries, each with ``name``,
        ``description``, ``prompt`` and ``tools``.

    Raises:
        ValueError: If the structure or any entry field is invalid.
    """
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    subagents = manifest.get("subagents") if isinstance(manifest, dict) else None
    if not isinstance(subagents, list) or not subagents:
        raise ValueError("subagents manifest must carry a non-empty 'subagents' list")
    for entry in subagents:
        if not isinstance(entry, dict):
            raise ValueError("each subagent entry must be a JSON object")
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError("subagent 'name' must be a non-empty string")
        if not isinstance(entry.get("description"), str) or not entry["description"]:
            raise ValueError(f"subagent {entry.get('name')!r}: 'description' must be a non-empty string")
        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.startswith("{file:./"):
            raise ValueError(f"subagent {entry['name']!r}: 'prompt' must be a {{file:./...}} reference")
        tools = entry.get("tools")
        if not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools):
            raise ValueError(f"subagent {entry['name']!r}: 'tools' must be a list of non-empty strings")
    return subagents


def mcp_namespaces(config: dict) -> list[str]:
    """Derive opencode tool namespaces from the rendered MCP server map.

    opencode namespaces MCP tools as ``<server>_<tool>`` with spaces in the
    server name folded to underscores (``search mcp`` → ``search_mcp``);
    hyphens are kept (``vibe-trading`` → ``vibe-trading``).

    Args:
        config: Rendered template config containing an ``mcp`` object.

    Returns:
        One namespace per configured MCP server.
    """
    mcp = config.get("mcp", {})
    return [server.replace(" ", "_") for server in mcp]


def build_agent_entries(
    subagents: list[dict],
    namespaces: list[str],
    prompt_base: Path | None = None,
    target_dir: Path | None = None,
) -> dict:
    """Compile subagent manifests into opencode ``agent.<name>`` sections.

    Each subagent is permission-gated deny-first: a wildcard deny for EVERY
    MCP namespace in the deployment — the template-derived ones plus the
    oh-my-openagent plugin builtins — followed by the whitelist allows.
    opencode permission evaluation is last-match-wins, so the allow entries
    must come after the wildcard denies.

    opencode resolves ``{file:...}`` prompt references relative to the
    directory holding the final config file, and silently refuses any
    reference escaping that directory's subtree (probed on 1.18.23:
    ``./prompts/x.md`` loads, ``../`` and outside-absolute paths are
    dropped). ``main()`` therefore materializes the referenced prompt files
    next to the rendered config, keeping every ``{file:./prompts/...}``
    reference inside the subtree in both layouts (container renders into
    ``~/.opencode/``; host-direct renders in place).

    Args:
        subagents: Validated entries from ``load_subagents``.
        namespaces: MCP namespaces from ``mcp_namespaces``.

    Returns:
        Mapping of subagent name to its opencode agent section.
    """
    agents = {}
    denied = list(namespaces) + [ns for ns in OMO_BUILTIN_NAMESPACES if ns not in namespaces]
    for entry in subagents:
        permission = {f"{ns}_*": "deny" for ns in denied}
        for tool in list(entry["tools"]) + list(SKILL_LOADER_TOOLS):
            permission[f"{VT_SERVER}_{tool}"] = "allow"
        agents[entry["name"]] = {
            "description": entry["description"],
            "mode": "subagent",
            "prompt": entry["prompt"],
            "permission": permission,
        }
    return agents


def materialize_prompts(subagents: list[dict], prompt_base: Path, target_path: Path) -> list[Path]:
    """Copy referenced prompt files next to the rendered config.

    Keeps every subagent ``{file:./prompts/...}`` reference inside the
    rendered config's directory subtree, which is the only location
    opencode's ``{file:}`` loader accepts.

    Args:
        subagents: Validated entries from ``load_subagents``.
        prompt_base: Directory the manifest references are relative to
            (the subagents.json directory).
        target_path: Where the rendered opencode.json is written.

    Returns:
        The written prompt file paths.

    Raises:
        ValueError: If a reference escapes ``prompt_base``.
    """
    written = []
    for entry in subagents:
        ref = entry["prompt"].removeprefix("{file:").removesuffix("}")
        src = (prompt_base / ref).resolve()
        if prompt_base.resolve() not in src.parents:
            raise ValueError(f"subagent {entry['name']!r}: prompt reference escapes config dir: {ref}")
        dest = target_path.parent / "prompts" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(dest)
    return written


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
    }


def render(
    template_path: Path,
    manifest_path: Path,
    subagents_path: Path | None = DEFAULT_SUBAGENTS,
) -> str:
    """Render the final opencode.json content.

    Args:
        template_path: Path to ``opencode.json.tmpl``.
        manifest_path: Path to ``vibe-trading-tools.json``.
        subagents_path: Path to ``subagents.json``; defaults to the file
            next to ``render_config.py``. Pass ``None`` to skip subagent
            rendering.

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
    if subagents_path is not None:
        agent = config.setdefault("agent", {})
        if not isinstance(agent, dict):
            raise ValueError("opencode.json.tmpl 'agent' must be a JSON object")
        agent.update(build_agent_entries(load_subagents(subagents_path), mcp_namespaces(config)))
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
    parser.add_argument("--subagents", default=str(DEFAULT_SUBAGENTS))
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    try:
        rendered = render(Path(args.template), Path(args.manifest), Path(args.subagents))
        subagents = load_subagents(Path(args.subagents))
        json.loads(rendered)  # final validation gate
    except Exception as exc:  # fail loud; entrypoint.sh owns the fallback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(target)
    materialize_prompts(subagents, Path(args.subagents).parent, target)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
