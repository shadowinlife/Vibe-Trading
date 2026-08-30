#!/usr/bin/env python3
"""CI gate: every covered ClickHouse column must have a COMMENT definition.

Part of the ClickHouse semantic layer (mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md P0.4).
Pure repository-file check — no network, no ClickHouse connection.

Inputs (both inside the repository):

* ``schema/clickhouse/comments.yaml`` — the comment contract::

      tables:
        <table>:
          columns:
            <column>: "<non-empty comment>"

* ``schema/clickhouse/<database>__<table>.sql`` — the DDL snapshot of
  record, produced by ``tools/clickhouse_export_ddl.py``.

For every table listed under ``tables:`` the gate asserts that:

1. the table's DDL snapshot exists and parses;
2. every column in the DDL has a non-empty string comment in the yaml;
3. every yaml column actually exists in the DDL (typo detection).

Usage::

    python tools/ci_clickhouse_comments_gate.py            # exit 0/1
    python tools/ci_clickhouse_comments_gate.py --help

Exit codes: 0 = full coverage; 1 = missing/unknown/empty comments, missing
DDL snapshot, missing or malformed comments.yaml.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

DEFAULT_DATABASE = "ashare"
DEFAULT_COMMENTS_REL = "schema/clickhouse/comments.yaml"
DEFAULT_SCHEMA_DIR_REL = "schema/clickhouse"

_SNAPSHOT_COLUMN_RE = re.compile(r"\s+`([^`]+)`\s")


class GateError(Exception):
    """Structural problem that aborts the whole gate (exit 1)."""


# ---------------------------------------------------------------------------
# Parsing helpers (pure)
# ---------------------------------------------------------------------------


def parse_ddl_columns(ddl_text: str) -> list[str]:
    """Extract column names, in order, from a formatted DDL snapshot.

    Expects the ``tools/clickhouse_export_ddl.py`` layout: a ``(`` line,
    one ``    `name` Type...`` line per column, then a ``)`` line.
    Raises :class:`ValueError` on any line inside the block that does not
    look like a column definition (fail closed).
    """
    names: list[str] = []
    in_block = False
    for lineno, line in enumerate(ddl_text.splitlines(), start=1):
        stripped = line.strip()
        if not in_block:
            if stripped == "(":
                in_block = True
            continue
        if stripped.startswith(")"):
            break
        match = _SNAPSHOT_COLUMN_RE.match(line)
        if not match:
            raise ValueError(f"line {lineno} is not a column definition: {line!r}")
        names.append(match.group(1))
    if not in_block or not names:
        raise ValueError("no column block found in DDL snapshot")
    return names


def load_comments_yaml(path: Path) -> dict[str, object]:
    """Load and structurally validate comments.yaml; return the tables map."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GateError(f"cannot parse {path}: {exc}") from exc
    except OSError as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise GateError(f"{path}: top level must be a mapping with a 'tables:' key")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise GateError(f"{path}: missing or malformed top-level 'tables:' mapping")
    if not tables:
        raise GateError(f"{path}: 'tables:' lists no tables — nothing would be checked")
    return tables


def _comment_problem(value: object) -> str | None:
    """Return a problem description when *value* is not a usable comment."""
    if value is None:
        return "has no comment entry"
    if not isinstance(value, str):
        return f"comment is not a string (got {type(value).__name__})"
    if not value.strip():
        return "comment is empty"
    return None


# ---------------------------------------------------------------------------
# Per-table check
# ---------------------------------------------------------------------------


def check_table(
    table: str,
    entry: object,
    schema_dir: Path,
    database: str,
) -> list[str]:
    """Cross-check one table's yaml entry against its DDL snapshot."""
    rel_name = f"{database}__{table}.sql"
    ddl_path = schema_dir / rel_name
    if not ddl_path.is_file():
        return [f"{table}: DDL snapshot schema/clickhouse/{rel_name} not found"]

    try:
        ddl_columns = parse_ddl_columns(ddl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{table}: cannot parse schema/clickhouse/{rel_name}: {exc}"]

    if not isinstance(entry, dict):
        return [f"{table}: entry under 'tables:' must be a mapping with 'columns:'"]
    columns = entry.get("columns")
    if not isinstance(columns, dict):
        return [f"{table}: missing or malformed 'columns:' mapping in comments.yaml"]

    errors: list[str] = []
    ddl_set = set(ddl_columns)

    for column in ddl_columns:
        problem = _comment_problem(columns.get(column))
        if problem is not None:
            errors.append(
                f"{table}: column '{column}' {problem} in comments.yaml "
                f"(present in {rel_name})"
            )

    for column in sorted(set(columns) - ddl_set):
        errors.append(
            f"{table}: comments.yaml documents unknown column '{column}' "
            f"(not in {rel_name}) — possible typo"
        )

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_gate(comments_path: Path, schema_dir: Path, database: str) -> int:
    """Execute the gate; return the process exit code."""
    if not comments_path.is_file():
        print(f"FAIL: {comments_path} not found.")
        print(
            "The comment contract must live in the repository "
            "(see mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md P0.2/P0.4)."
        )
        return 1

    try:
        tables = load_comments_yaml(comments_path)
    except GateError as exc:
        print(f"FAIL: {exc}")
        return 1

    all_errors: list[str] = []
    for table in sorted(tables):
        all_errors.extend(check_table(table, tables[table], schema_dir, database))

    if all_errors:
        print("FAIL: ClickHouse column-comment coverage is incomplete:")
        for error in all_errors:
            print(f"  {error}")
        print()
        print(
            "Fix schema/clickhouse/comments.yaml so every column of every "
            "covered table has a non-empty comment, and every documented "
            "column exists in the DDL snapshot."
        )
        return 1

    print(
        f"OK: {len(tables)} table(s) in comments.yaml have full, "
        "typo-free column-comment coverage."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description=(
            "Assert that every column of every covered ClickHouse table has a "
            "non-empty comment in schema/clickhouse/comments.yaml "
            "(pure repo-file check, no network)."
        )
    )
    parser.add_argument(
        "--comments",
        help=f"comments yaml path (default: <repo>/{DEFAULT_COMMENTS_REL})",
    )
    parser.add_argument(
        "--schema-dir",
        help=f"DDL snapshot directory (default: <repo>/{DEFAULT_SCHEMA_DIR_REL})",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help="database prefix of the snapshot filenames (default: ashare)",
    )
    args = parser.parse_args(argv)

    comments_path = (
        Path(args.comments) if args.comments else repo_root / DEFAULT_COMMENTS_REL
    )
    schema_dir = (
        Path(args.schema_dir) if args.schema_dir else repo_root / DEFAULT_SCHEMA_DIR_REL
    )

    return run_gate(comments_path, schema_dir, args.database)


if __name__ == "__main__":
    sys.exit(main())
