"""A6 portability coverage assertions (internal/external tool-name drift).

Swarm presets whitelist tools by name, and skill docs reference tools by name,
but the agent-internal registry and the MCP surface do not always agree on
those names. Where an internal name differs from its MCP counterpart (a
"drift pair" such as ``pattern`` vs ``pattern_recognition``), a preset
whitelist or a skill-doc reference that uses the internal name does not
resolve on the MCP surface — the portability obstacle A6 removes by publishing
an authoritative internal<->external mapping and unifying the docs.

This helper measures the CURRENT state so the A6 "real improvement" claim
reduces to before/after numbers:

* **preset resolvability** — every tool name referenced by the 30 bundled
  presets is classified as ``mcp`` (resolves on the MCP surface), ``drift``
  (an internal name whose MCP counterpart has a DIFFERENT name — the
  portability risk), ``internal-only`` (no MCP counterpart) or ``unknown``;
* **skill-doc internal references** — skill SKILL.md files are grepped for the
  drift internal names; the target after A6 is zero unannotated references.

The MCP surface names come from ``corpus_snapshot.yaml`` (no heavy imports);
the drift pairs come from the F1 inventory
(``scripts/artifacts/internal_tool_inventory.json``).

Usage:
    cd agent
    python -m src.evals.tool_selection.a6_coverage_assert
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[2]
CORPUS_PATH = HERE / "corpus_snapshot.yaml"
PRESETS_DIR = AGENT_DIR / "src" / "swarm" / "presets"
SKILLS_DIR = AGENT_DIR / "src" / "skills"
INVENTORY_PATH = AGENT_DIR / "scripts" / "artifacts" / "internal_tool_inventory.json"


def mcp_surface_names() -> set[str]:
    """Return the MCP tool names from the corpus snapshot.

    Returns:
        Set of the 74 MCP tool names.
    """
    corpus = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    return {row["name"] for row in corpus["tools"]}


def drift_pairs() -> dict[str, str]:
    """Return internal-name -> MCP-counterpart for names that differ.

    Returns:
        Mapping of drift internal names to their MCP counterpart names,
        read from the F1 inventory.
    """
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    pairs: dict[str, str] = {}
    for tool in inventory["tools"]:
        name = tool.get("name")
        counterpart = tool.get("mcp_counterpart")
        if name and counterpart and name != counterpart:
            pairs[name] = counterpart
    return pairs


def internal_names() -> set[str]:
    """Return the full internal registry tool-name set from the F1 inventory.

    Returns:
        Set of internal tool names.
    """
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    return {tool["name"] for tool in inventory["tools"] if tool.get("name")}


def preset_tool_refs() -> dict[str, list[str]]:
    """Collect every tool name referenced by each bundled preset.

    Returns:
        Preset filename -> deduplicated tool-name list (agents[].tools).
    """
    refs: dict[str, list[str]] = {}
    for path in sorted(PRESETS_DIR.glob("*.yaml")):
        preset = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names: list[str] = []
        for agent in preset.get("agents") or []:
            for tool in agent.get("tools") or []:
                if tool not in names:
                    names.append(tool)
        refs[path.name] = names
    return refs


def classify(name: str, mcp: set[str], drift: dict[str, str],
             internal: set[str]) -> str:
    """Classify one tool reference.

    Args:
        name: The referenced tool name.
        mcp: MCP surface names.
        drift: Drift internal-name -> MCP counterpart mapping.
        internal: Internal registry names.

    Returns:
        ``mcp``, ``drift``, ``internal-only`` or ``unknown``.
    """
    if name in mcp:
        return "mcp"
    if name in drift:
        return "drift"
    if name in internal:
        return "internal-only"
    return "unknown"


def scan_presets() -> dict:
    """Scan all presets and classify every tool reference.

    Returns:
        Dict with per-class counts and the offending references.
    """
    mcp = mcp_surface_names()
    drift = drift_pairs()
    internal = internal_names()
    counts = {"mcp": 0, "drift": 0, "internal-only": 0, "unknown": 0}
    offending: dict[str, list[dict]] = {}
    for preset_name, names in preset_tool_refs().items():
        for name in names:
            cls = classify(name, mcp, drift, internal)
            counts[cls] += 1
            if cls != "mcp":
                offending.setdefault(preset_name, []).append(
                    {"tool": name, "class": cls,
                     "mcp_counterpart": drift.get(name)}
                )
    return {
        "preset_count": len(preset_tool_refs()),
        "counts": counts,
        "offending": offending,
    }


def scan_skill_docs() -> dict:
    """Grep skill SKILL.md files for UNANNOTATED drift-internal-name tool refs.

    A reference is counted only when BOTH hold, so the count reflects real
    portability problems rather than prose:
    * the internal name appears as a backticked token (`` ``pattern`` ``) — a
      tool reference, not common English such as "candlestick pattern";
    * the line does NOT already carry the MCP counterpart name — a ref that
      shows the MCP name alongside the internal name is an annotation, which
      A6 accepts ("改为 MCP 名或标注映射").

    Returns:
        Dict with the total unannotated reference count and per-file detail.
    """
    drift = drift_pairs()
    detail: dict[str, dict[str, int]] = {}
    total = 0
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        hits: dict[str, int] = {}
        for line in skill_md.read_text(encoding="utf-8").splitlines():
            for internal, mcp_name in drift.items():
                if mcp_name in line:
                    continue
                for _ in re.finditer(r"`" + re.escape(internal) + r"`", line):
                    hits[internal] = hits.get(internal, 0) + 1
        if hits:
            detail[skill_md.parent.name] = hits
            total += sum(hits.values())
    return {"total_internal_refs": total, "per_skill": detail}


def build_report_data() -> dict:
    """Assemble the full A6 coverage picture.

    Returns:
        Dict combining preset resolvability and skill-doc references.
    """
    return {"presets": scan_presets(), "skill_docs": scan_skill_docs()}


def render_report(data: dict) -> str:
    """Render the coverage picture as markdown.

    Args:
        data: Output of ``build_report_data``.

    Returns:
        Markdown report text.
    """
    presets = data["presets"]
    docs = data["skill_docs"]
    counts = presets["counts"]
    lines = [
        "# A6 portability coverage baseline",
        "",
        f"Presets scanned: **{presets['preset_count']}**",
        "",
        "## Preset tool-reference resolvability",
        "",
        "| Class | Count | Meaning |",
        "|---|---|---|",
        f"| mcp | {counts['mcp']} | resolves on the MCP surface |",
        f"| drift | {counts['drift']} | internal name, MCP counterpart differs "
        "(portability risk) |",
        f"| internal-only | {counts['internal-only']} | internal name, no MCP "
        "counterpart |",
        f"| unknown | {counts['unknown']} | not found in registry or MCP |",
    ]
    if presets["offending"]:
        lines += ["", "### Offending preset references (non-`mcp`)", ""]
        for preset_name, items in sorted(presets["offending"].items()):
            for item in items:
                counterpart = item["mcp_counterpart"] or "-"
                lines.append(
                    f"- `{preset_name}`: `{item['tool']}` "
                    f"({item['class']}, mcp: `{counterpart}`)"
                )
    lines += [
        "",
        "## Skill-doc internal-name references (drift names)",
        "",
        f"Total unannotated drift-name references: "
        f"**{docs['total_internal_refs']}** (target after A6: 0)",
    ]
    if docs["per_skill"]:
        lines.append("")
        for skill_name, hits in sorted(docs["per_skill"].items()):
            rendered = ", ".join(f"{name}×{n}" for name, n in sorted(hits.items()))
            lines.append(f"- `{skill_name}`: {rendered}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 ok, 1 when a strict gate fails).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of the markdown report",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="exit 1 if any UNKNOWN preset ref or any unannotated skill-doc "
             "internal reference remains (the post-A6 gate). Drift refs are "
             "expected agent-surface names and are acceptable once the "
             "internal<->MCP mapping table documents their counterpart.",
    )
    args = parser.parse_args(argv)

    data = build_report_data()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_report(data))
    if args.strict:
        counts = data["presets"]["counts"]
        bad_refs = counts["unknown"]
        if bad_refs or data["skill_docs"]["total_internal_refs"]:
            print(
                f"STRICT GATE FAILED: {bad_refs} unresolvable preset refs, "
                f"{data['skill_docs']['total_internal_refs']} skill-doc "
                "internal refs", file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
