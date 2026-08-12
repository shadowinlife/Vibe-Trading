#!/usr/bin/env python3
"""Export ClickHouse DDL snapshots into ``schema/clickhouse/``.

ClickHouse semantic layer P0.1 (see CLICKHOUSE_ITERATION_PLAN.md): the repo
files are the semantic contract snapshot; the physical tables are created by
``/opt/qdata/sync/schema.py`` on the production host. Default mode (re)writes
one deterministic, timestamp-free ``<database>__<table>.sql`` per table;
``--check`` diffs source vs repo and exits 1 on drift; ``--from-dump FILE``
reads an offline JSONEachRow dump (``{"name", "create_table_query"}``/line).

Connection uses the same env vars/defaults as
``agent/src/clickhouse_connector.py`` (CLICKHOUSE_HOST / _PORT / _USER /
_PASSWORD / _DATABASE). The password is env-only and never printed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

# Must stay aligned with agent/src/config/env_schema.py (DataConfig).
DEFAULT_HOST = "172.24.165.51"
DEFAULT_PORT = 8123
DEFAULT_USER = "default"
DEFAULT_PASSWORD = ""
DEFAULT_DATABASE = "ashare"

HEADER_TEMPLATE = (
    "-- ClickHouse DDL snapshot: {database}.{table} "
    "(exported by tools/clickhouse_export_ddl.py)"
)

_CLAUSE_KEYWORDS: tuple[str, ...] = (
    "PARTITION BY",
    "PRIMARY KEY",
    "ORDER BY",
    "SAMPLE BY",
    "ENGINE",
    "SETTINGS",
    "TTL",
)

_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SNAPSHOT_COLUMN_RE = re.compile(r"\s+`([^`]+)`\s")
_QUOTE_CHARS = ("'", '"', "`")


def _iter_outside_strings(text: str):
    """Yield (index, char) outside string literals, escape-aware.

    Parentheses/commas yielded here sit outside ``'...'`` / ``"..."`` /
    backtick identifiers, so callers can do depth counting on them.
    """
    in_str: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in _QUOTE_CHARS:
            in_str = ch
            i += 1
            continue
        yield i, ch
        i += 1


def _match_close_paren(text: str, open_idx: int) -> int:
    """Index of the ``)`` matching ``text[open_idx]``."""
    depth = 0
    for offset, ch in _iter_outside_strings(text[open_idx:]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return open_idx + offset
    raise ValueError(f"unbalanced parentheses in CREATE TABLE: {text[:80]}...")


def extract_column_list(create_table_query: str) -> tuple[str, str]:
    """Split a single-line CREATE TABLE into (column_list, tail-after-ENGINE)."""
    query = create_table_query.strip()
    open_idx = query.find("(")
    if open_idx == -1:
        raise ValueError("CREATE TABLE statement has no column list")
    close_idx = _match_close_paren(query, open_idx)
    column_list = query[open_idx + 1 : close_idx].strip()
    tail = query[close_idx + 1 :].strip()
    if not tail.upper().startswith("ENGINE"):
        raise ValueError("expected ENGINE clause after the column list")
    if not column_list:
        raise ValueError("empty column list in CREATE TABLE statement")
    return column_list, tail


def split_top_level_commas(text: str) -> list[str]:
    """Split on commas at paren depth 0 outside string literals."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in _iter_outside_strings(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            part = text[start:i].strip()
            if part:
                parts.append(part)
            start = i + 1
    tail_part = text[start:].strip()
    if tail_part:
        parts.append(tail_part)
    return parts


def _is_ident_boundary(text: str, idx: int, keyword: str) -> bool:
    """True when *keyword* at *idx* is not glued to a longer identifier."""
    before = text[idx - 1] if idx > 0 else " "
    after_pos = idx + len(keyword)
    after = text[after_pos] if after_pos < len(text) else " "
    return not (before.isalnum() or before == "_") and not (
        after.isalnum() or after == "_"
    )


def split_tail_clauses(tail: str) -> list[str]:
    """Split the post-column tail into one verbatim string per clause line."""
    tail = tail.strip()
    chars = list(_iter_outside_strings(tail))
    positions: list[int] = []
    depth = 0
    k = 0
    while k < len(chars):
        i, ch = chars[k]
        if ch == "(":
            depth += 1
            k += 1
            continue
        if ch == ")":
            depth -= 1
            k += 1
            continue
        if depth == 0:
            for keyword in _CLAUSE_KEYWORDS:
                if tail.startswith(keyword, i) and _is_ident_boundary(tail, i, keyword):
                    positions.append(i)
                    while k < len(chars) and chars[k][0] < i + len(keyword):
                        k += 1
                    break
            else:
                k += 1
        else:
            k += 1

    if not positions or positions[0] != 0:
        raise ValueError(f"tail does not start with a clause keyword: {tail[:60]}...")
    clauses = []
    for begin, end in zip(positions, positions[1:] + [len(tail)]):
        clause = tail[begin:end].strip()
        if clause:
            clauses.append(clause)
    return clauses


def format_create_table(
    table: str, create_table_query: str, database: str = DEFAULT_DATABASE
) -> str:
    """Pretty-print a single-line CREATE TABLE deterministically.

    Output: ``CREATE TABLE <db>.<table>`` / ``(`` / one verbatim column per
    line (4-space indent) / ``)`` / one line per ENGINE..TTL clause.
    """
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(f"unsafe table name: {table!r}")
    if not _TABLE_NAME_RE.match(database):
        raise ValueError(f"unsafe database name: {database!r}")

    column_list, tail = extract_column_list(create_table_query)
    column_defs = split_top_level_commas(column_list)
    clauses = split_tail_clauses(tail)

    lines = [f"CREATE TABLE {database}.{table}", "("]
    for idx, column_def in enumerate(column_defs):
        comma = "," if idx < len(column_defs) - 1 else ""
        lines.append(f"    {column_def}{comma}")
    lines.append(")")
    lines.extend(clauses)
    return "\n".join(lines) + "\n"


def render_snapshot(
    table: str, create_table_query: str, database: str = DEFAULT_DATABASE
) -> str:
    """Full snapshot file content: header line + formatted DDL."""
    header = HEADER_TEMPLATE.format(database=database, table=table)
    return f"{header}\n{format_create_table(table, create_table_query, database)}"


def parse_snapshot_columns(snapshot_text: str) -> list[str]:
    """Parse column names, in order, out of a formatted snapshot file."""
    names: list[str] = []
    in_block = False
    for lineno, line in enumerate(snapshot_text.splitlines(), start=1):
        stripped = line.strip()
        if not in_block:
            if stripped == "(":
                in_block = True
            continue
        if stripped.startswith(")"):
            break
        match = _SNAPSHOT_COLUMN_RE.match(line)
        if not match:
            raise ValueError(f"snapshot line {lineno} is not a column: {line!r}")
        names.append(match.group(1))
    if not in_block or not names:
        raise ValueError("snapshot has no column block")
    return names


def snapshot_filename(table: str, database: str) -> str:
    """Per-table snapshot file name."""
    return f"{database}__{table}.sql"


def load_dump(path: Path) -> dict[str, str]:
    """Load a JSONEachRow dump: ``{"name": ..., "create_table_query": ...}``."""
    ddls: dict[str, str] = {}
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            name = record["name"]
            query = record["create_table_query"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}:{lineno}: bad dump record ({exc})") from exc
        if not _TABLE_NAME_RE.match(str(name)):
            raise ValueError(f"{path}:{lineno}: unsafe table name {name!r}")
        ddls[str(name)] = str(query)
    if not ddls:
        raise ValueError(f"{path}: dump contains no tables")
    return ddls


def fetch_live_ddl(
    host: str, port: int, user: str, password: str, database: str
) -> dict[str, str]:
    """Fetch ``SHOW CREATE TABLE`` for every table via clickhouse_connect."""
    import clickhouse_connect  # lazy: keep offline paths import-safe

    client = clickhouse_connect.get_client(
        host=host, port=port, username=user, password=password, database=database
    )
    try:
        tables = [str(row[0]) for row in client.query("SHOW TABLES").result_rows]
        ddls: dict[str, str] = {}
        for table in sorted(tables):
            if not _TABLE_NAME_RE.match(table):
                raise ValueError(f"unsafe table name from server: {table!r}")
            rows = client.query(f"SHOW CREATE TABLE `{table}`").result_rows
            ddls[table] = str(rows[0][0])
        return ddls
    finally:
        client.close()


def export_snapshots(
    ddls: dict[str, str], database: str, schema_dir: Path
) -> list[Path]:
    """Write one snapshot file per table (alphabetical), idempotently."""
    schema_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in sorted(ddls):
        out_path = schema_dir / snapshot_filename(table, database)
        content = render_snapshot(table, ddls[table], database)
        out_path.write_text(content, encoding="utf-8", newline="\n")
        written.append(out_path)
    return written


def check_snapshots(ddls: dict[str, str], database: str, schema_dir: Path) -> int:
    """Compare source DDLs against repo snapshots; return exit code."""
    drift = False
    for table in sorted(ddls):
        rel_name = snapshot_filename(table, database)
        path = schema_dir / rel_name
        if not path.exists():
            print(f"DRIFT: schema/clickhouse/{rel_name}: in source, missing in repo")
            drift = True
            continue
        actual = path.read_text(encoding="utf-8")
        expected = render_snapshot(table, ddls[table], database)
        if actual == expected:
            continue
        drift = True
        print(f"DRIFT: schema/clickhouse/{rel_name} differs from source:")
        sys.stdout.writelines(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"repo:schema/clickhouse/{rel_name}",
                tofile=f"source:{database}.{table}",
            )
        )

    known = {snapshot_filename(t, database) for t in ddls}
    for path in sorted(schema_dir.glob(f"{database}__*.sql")):
        if path.name not in known:
            table = path.name[len(database) + 2 : -len(".sql")]
            print(f"DRIFT: {path.name}: in repo, table '{table}' absent from source")
            drift = True

    if drift:
        print(
            "\nDrift detected. Re-export via `python tools/clickhouse_export_ddl.py`."
        )
        return 1
    print(f"OK: {len(ddls)} table snapshot(s) in schema/clickhouse/ match the source.")
    return 0


def _env_config() -> tuple[str, int, str, str, str]:
    """Resolve (host, port, user, password, database) from the environment."""
    try:
        port = int(os.environ.get("CLICKHOUSE_PORT", "") or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return (
        os.environ.get("CLICKHOUSE_HOST") or DEFAULT_HOST,
        port,
        os.environ.get("CLICKHOUSE_USER") or DEFAULT_USER,
        os.environ.get("CLICKHOUSE_PASSWORD", DEFAULT_PASSWORD),
        os.environ.get("CLICKHOUSE_DATABASE") or DEFAULT_DATABASE,
    )


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Export or check ClickHouse DDL snapshots in schema/clickhouse/."
    )
    parser.add_argument(
        "--check", action="store_true", help="diff vs repo, exit 1 on drift"
    )
    parser.add_argument(
        "--from-dump", metavar="FILE", help="offline JSONEachRow dump source"
    )
    parser.add_argument("--database", help="database prefix (default: ashare)")
    parser.add_argument("--schema-dir", help="snapshot dir (default schema/clickhouse)")
    args = parser.parse_args(argv)
    env_host, env_port, env_user, env_password, env_database = _env_config()
    database = args.database or env_database
    default_schema_dir = repo_root / "schema" / "clickhouse"
    schema_dir = Path(args.schema_dir) if args.schema_dir else default_schema_dir
    source_label = (
        f"dump {args.from_dump}"
        if args.from_dump
        else f"live {env_host}:{env_port}/{database}"
    )
    try:
        if args.from_dump:
            ddls = load_dump(Path(args.from_dump))
        else:
            ddls = fetch_live_ddl(env_host, env_port, env_user, env_password, database)
    except Exception as exc:  # message only — never print credentials
        print(f"ERROR: failed to read DDL from {source_label}: {exc}")
        return 1

    print(f"Source: {source_label} — {len(ddls)} table(s), database '{database}'.")
    if args.check:
        return check_snapshots(ddls, database, schema_dir)

    try:
        written = export_snapshots(ddls, database, schema_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    for path in written:
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
        print(f"wrote {rel}")
    print(f"Exported {len(written)} snapshot(s) to {schema_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
