#!/usr/bin/env python3
"""Apply structured column COMMENTs to ClickHouse (Phase 0 P0.2 semantic layer).

Reads ``schema/clickhouse/comments.yaml`` — the machine-readable semantic
layer for the Tier-1 ``ashare`` tables — and turns it into one
``ALTER TABLE <db>.<table> COMMENT COLUMN <col> '<comment>'`` statement per
column.

Modes
-----
(default, ``--dry-run``)
    Print the ALTER statements.  No ClickHouse connection is needed.
``--apply``
    Execute the statements via ``clickhouse_connect``, printing each
    statement's result.  Failures do not abort the run; a summary is
    printed and the exit code is non-zero when anything failed.
``--verify``
    Query ``system.columns`` for the covered tables and report per-table
    empty-comment counts (goal: all 0).

Credentials mirror ``agent/src/clickhouse_connector.py``:

    CLICKHOUSE_HOST      default 172.24.165.51
    CLICKHOUSE_PORT      default 8123
    CLICKHOUSE_USER      default "" (ClickHouse ``default`` user)
    CLICKHOUSE_PASSWORD  default ""
    CLICKHOUSE_DATABASE  default ashare

Idempotent: ``COMMENT COLUMN`` overwrites the previous comment, so the tool
can be re-run safely after edits to ``comments.yaml``.

Usage::

    python tools/clickhouse_apply_comments.py                 # dry-run
    python tools/clickhouse_apply_comments.py --tables stk_margin,stk_info
    python tools/clickhouse_apply_comments.py --apply         # execute
    python tools/clickhouse_apply_comments.py --verify        # audit
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Defaults — mirror agent/src/clickhouse_connector.py
# ---------------------------------------------------------------------------

DEFAULT_HOST = "172.24.165.51"
DEFAULT_PORT = 8123
DEFAULT_USER = ""
DEFAULT_PASSWORD = ""
DEFAULT_DATABASE = "ashare"
DEFAULT_CHUNK_SIZE = 50

CONVENTION = "unit=; adjust=; caliber=; source=; desc=; ambiguous_with="

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Statement:
    """One ``COMMENT COLUMN`` ALTER statement for a single column."""

    table: str
    column: str
    sql: str


@dataclass(frozen=True)
class ConnConfig:
    """ClickHouse connection parameters resolved from the environment."""

    host: str
    port: int
    user: str
    password: str
    database: str


def default_yaml_path() -> Path:
    """Locate ``schema/clickhouse/comments.yaml`` relative to this file."""
    return (
        Path(__file__).resolve().parent.parent
        / "schema"
        / "clickhouse"
        / "comments.yaml"
    )


# ---------------------------------------------------------------------------
# YAML loading / validation
# ---------------------------------------------------------------------------


def load_comments(path: Path) -> dict[str, dict[str, str]]:
    """Load and validate ``comments.yaml``.

    Returns ``{table: {column: comment}}`` preserving the YAML order.
    Raises ``ValueError`` on structural problems or empty comments.
    """
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("tables"), dict):
        raise ValueError(f"{path}: missing top-level 'tables' mapping")
    if doc.get("version") != 1:
        raise ValueError(f"{path}: unsupported version {doc.get('version')!r}")

    tables: dict[str, dict[str, str]] = {}
    for table, spec in doc["tables"].items():
        if not isinstance(spec, dict) or not isinstance(spec.get("columns"), dict):
            raise ValueError(f"{path}: table {table!r} has no 'columns' mapping")
        columns: dict[str, str] = {}
        for column, comment in spec["columns"].items():
            if not isinstance(comment, str) or not comment.strip():
                raise ValueError(f"{path}: {table}.{column} has an empty comment")
            columns[column] = comment
        if not columns:
            raise ValueError(f"{path}: table {table!r} has no columns")
        tables[table] = columns
    if not tables:
        raise ValueError(f"{path}: no tables defined")
    return tables


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------


def escape_sql_string(text: str) -> str:
    """Escape *text* for use inside a single-quoted ClickHouse literal.

    Backslash is an escape character in ClickHouse string literals, so it is
    doubled first; single quotes are doubled (SQL standard).
    """
    return text.replace("\\", "\\\\").replace("'", "''")


def _validate_identifier(kind: str, name: str) -> None:
    """Reject anything that is not a plain identifier (injection guard)."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe {kind} identifier: {name!r}")


def build_statements(
    comments: dict[str, dict[str, str]],
    database: str,
    only: list[str] | None = None,
) -> list[Statement]:
    """Build one ALTER statement per column.

    ``only`` restricts the run to the given table names; unknown names raise
    ``ValueError`` so a typo cannot silently turn into a no-op.
    """
    _validate_identifier("database", database)
    if only is not None:
        unknown = [t for t in only if t not in comments]
        if unknown:
            raise ValueError(f"unknown table(s) requested: {', '.join(unknown)}")
        table_names = list(only)
    else:
        table_names = list(comments)

    statements: list[Statement] = []
    for table in table_names:
        _validate_identifier("table", table)
        for column, comment in comments[table].items():
            _validate_identifier("column", column)
            sql = (
                f"ALTER TABLE {database}.{table} "
                f"COMMENT COLUMN {column} '{escape_sql_string(comment)}'"
            )
            statements.append(Statement(table=table, column=column, sql=sql))
    return statements


# ---------------------------------------------------------------------------
# Environment / connection
# ---------------------------------------------------------------------------


def conn_config_from_env(database_override: str | None = None) -> ConnConfig:
    """Resolve connection parameters from ``CLICKHOUSE_*`` env vars."""
    return ConnConfig(
        host=os.environ.get("CLICKHOUSE_HOST", DEFAULT_HOST),
        port=int(os.environ.get("CLICKHOUSE_PORT", str(DEFAULT_PORT))),
        user=os.environ.get("CLICKHOUSE_USER", DEFAULT_USER),
        password=os.environ.get("CLICKHOUSE_PASSWORD", DEFAULT_PASSWORD),
        database=database_override
        or os.environ.get("CLICKHOUSE_DATABASE", DEFAULT_DATABASE),
    )


def _connect(cfg: ConnConfig) -> Any:
    """Create a ``clickhouse_connect`` client (lazy import)."""
    try:
        import clickhouse_connect
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "clickhouse_connect is required for --apply/--verify "
            "(pip install clickhouse-connect)"
        ) from exc
    return clickhouse_connect.get_client(
        host=cfg.host,
        port=cfg.port,
        username=cfg.user or "default",
        password=cfg.password,
        database=cfg.database,
    )


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_apply(statements: list[Statement], cfg: ConnConfig, chunk_size: int) -> int:
    """Execute every statement; continue past failures; return failure count."""
    client = _connect(cfg)
    total = len(statements)
    failures: list[tuple[Statement, str]] = []
    try:
        for idx, stmt in enumerate(statements, 1):
            try:
                client.command(stmt.sql)
                print(f"[{idx}/{total}] OK   {stmt.table}.{stmt.column}")
            except Exception as exc:  # noqa: BLE001 - keep going, summarize later
                failures.append((stmt, str(exc)))
                print(f"[{idx}/{total}] FAIL {stmt.table}.{stmt.column}: {exc}")
            if idx % chunk_size == 0:
                print(f"progress: {idx}/{total} statements done")
    finally:
        client.close()

    print(f"\nsummary: {total - len(failures)}/{total} applied, {len(failures)} failed")
    for stmt, err in failures:
        print(f"  FAILED {cfg.database}.{stmt.table}.{stmt.column}: {err}")
    return len(failures)


def run_verify(cfg: ConnConfig, tables: list[str]) -> int:
    """Report per-table empty-comment counts from ``system.columns``.

    Returns the number of tables that still have empty comments (or that are
    missing from the database entirely).
    """
    for table in tables:
        _validate_identifier("table", table)
    quoted = ", ".join(f"'{table}'" for table in tables)
    sql = (
        "SELECT table, count() AS total, countIf(comment = '') AS empty "
        "FROM system.columns "
        f"WHERE database = '{escape_sql_string(cfg.database)}' AND table IN ({quoted}) "
        "GROUP BY table ORDER BY table"
    )
    client = _connect(cfg)
    try:
        result = client.query(sql)
    finally:
        client.close()

    seen: dict[str, tuple[int, int]] = {
        row[0]: (int(row[1]), int(row[2])) for row in result.result_rows
    }
    dirty = 0
    for table in tables:
        if table not in seen:
            print(f"{table}: NOT FOUND in database '{cfg.database}'")
            dirty += 1
            continue
        total, empty = seen[table]
        marker = "OK" if empty == 0 else "NEEDS COMMENTS"
        print(f"{table}: columns={total} empty_comments={empty} [{marker}]")
        if empty:
            dirty += 1
    goal = "all columns commented" if dirty == 0 else f"{dirty} table(s) incomplete"
    print(f"verify result: {goal}")
    return dirty


def run_dry_run(statements: list[Statement], database: str) -> None:
    """Print every ALTER statement (copy-paste ready for clickhouse-client)."""
    print(f"-- dry-run: {len(statements)} ALTER statements for database '{database}'")
    for stmt in statements:
        print(stmt.sql + ";")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply structured column COMMENTs to the ClickHouse ashare database."
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        default=None,
        help="comments.yaml path (default: schema/clickhouse/comments.yaml)",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="target database (default: $CLICKHOUSE_DATABASE or 'ashare')",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help="comma-separated table filter (default: all tables in the YAML)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute the ALTER statements via clickhouse_connect",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="query system.columns and report per-table empty-comment counts",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"progress interval for --apply (default {DEFAULT_CHUNK_SIZE})",
    )
    args = parser.parse_args(argv)

    if args.apply and args.verify:
        parser.error("--apply and --verify are mutually exclusive")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")

    yaml_path = args.yaml or default_yaml_path()
    if not yaml_path.is_file():
        print(f"error: comments file not found: {yaml_path}", file=sys.stderr)
        return 1

    try:
        comments = load_comments(yaml_path)
        only = (
            [t.strip() for t in args.tables.split(",") if t.strip()]
            if args.tables
            else None
        )
        cfg = conn_config_from_env(args.database)

        if args.verify:
            tables = list(only) if only else list(comments)
            unknown = [t for t in tables if t not in comments]
            if unknown:
                raise ValueError(f"unknown table(s) requested: {', '.join(unknown)}")
            dirty = run_verify(cfg, tables)
            return 0 if dirty == 0 else 1

        statements = build_statements(comments, cfg.database, only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.apply:
        failures = run_apply(statements, cfg, args.chunk_size)
        return 0 if failures == 0 else 1

    run_dry_run(statements, cfg.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
