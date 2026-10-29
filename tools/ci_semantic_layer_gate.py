#!/usr/bin/env python3
"""Gate: the generated semantic layer must stay consistent with the live schema.

Checks (all hard failures):
  1. every table in metadata.json has catalog/catalog.yaml entry and tables/<name>.md
  2. INDEX.md links every table doc, and every link resolves
  3. each table doc lists exactly the columns that exist in metadata.json
  4. no document references a table name that does not exist
  5. SKILL.md / RULES.md exist and RULES.md keeps its required sections

Usage:
    python tools/ci_semantic_layer_gate.py [--repo-root .]
Exit code 0 = pass, 1 = violations (printed one per line).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

TABLE_RE = re.compile(
    r"\b((?:stk|idx|fin|fund)_[a-z0-9_]+|trade_calendar|table_sync_state|schema_drift_log)\b"
)
COL_ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
LINK_RE = re.compile(r"\]\((tables/[a-z0-9_]+\.md)\)")
REQUIRED_RULE_SECTIONS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")

# Upstream sources named in prose that are intentionally not warehouse tables.
EXTERNAL_ALLOWLIST = {"fund_basic"}


def check(repo_root: Path) -> list[str]:
    errors: list[str] = []
    meta_path = repo_root / "schema/clickhouse/metadata.json"
    catalog_path = repo_root / "schema/clickhouse/catalog/catalog.yaml"
    out = repo_root / "OpencodeAgent/skills/semantic-layer"

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    tables = {t["name"]: t for t in metadata["tables"]}
    catalog_tables = catalog.get("tables") or {}

    for required in ("SKILL.md", "RULES.md", "INDEX.md", "tables"):
        if not (out / required).exists():
            errors.append(f"missing {required} under {out}")

    for name in sorted(tables):
        if name not in catalog_tables:
            errors.append(f"{name}: no catalog entry")
        doc = out / "tables" / f"{name}.md"
        if not doc.exists():
            errors.append(f"{name}: missing tables/{name}.md")
            continue
        text = doc.read_text(encoding="utf-8")
        documented = COL_ROW_RE.findall(text)
        expected = [c["name"] for c in tables[name]["columns"]]
        if documented != expected:
            missing = sorted(set(expected) - set(documented))
            extra = sorted(set(documented) - set(expected))
            errors.append(
                f"{name}: column list mismatch (missing={missing[:5]}, extra={extra[:5]}, "
                f"documented={len(documented)}, expected={len(expected)})"
            )

    for name in sorted(set(catalog_tables) - set(tables)):
        errors.append(f"{name}: catalog entry has no table in metadata.json")

    index = (
        (out / "INDEX.md").read_text(encoding="utf-8")
        if (out / "INDEX.md").exists()
        else ""
    )
    linked = set(LINK_RE.findall(index))
    for name in tables:
        if f"tables/{name}.md" not in linked:
            errors.append(f"{name}: not linked from INDEX.md")
    for link in sorted(linked):
        if not (out / link).exists():
            errors.append(f"INDEX.md: dead link {link}")

    known = set(tables)
    known_columns = {c["name"] for t in tables.values() for c in t["columns"]}
    for path in [
        out / "INDEX.md",
        out / "RULES.md",
        *sorted((out / "tables").glob("*.md")),
    ]:
        if not path.exists():
            continue
        for ref in set(TABLE_RE.findall(path.read_text(encoding="utf-8"))):
            if (
                ref not in known
                and ref not in known_columns
                and ref not in EXTERNAL_ALLOWLIST
            ):
                errors.append(f"{path.name}: references unknown table {ref}")

    rules = (
        (out / "RULES.md").read_text(encoding="utf-8")
        if (out / "RULES.md").exists()
        else ""
    )
    for section in REQUIRED_RULE_SECTIONS:
        if not re.search(rf"^##\s*{section}\.", rules, re.MULTILINE):
            errors.append(f"RULES.md: missing section {section}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    errors = check(args.repo_root)
    for error in errors:
        print(error)
    print(
        f"semantic-layer gate: {'FAIL' if errors else 'OK'} ({len(errors)} violations)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
