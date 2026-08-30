"""SQL guard + result-safety utilities for the ClickHouse flexibility channel.

The ``ch_query`` tool (mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md Phase 2) lets the agent run
constrained ad-hoc SQL against the ``ashare`` warehouse. This module is the
safety core of that channel and fails CLOSED on any ambiguity:

- exactly ONE statement, parsed by sqlglot with the ClickHouse dialect;
  anything sqlglot cannot fully classify is rejected, never executed;
- the AST root must be a plain ``SELECT`` (no UNION/INTERSECT/EXCEPT root);
- any DDL/DML/system node anywhere in the tree is rejected (CREATE, DROP,
  ALTER, TRUNCATE, INSERT, UPDATE, DELETE, MERGE, USE, SET, KILL, ATTACH,
  DETACH, GRANT, SYSTEM-style ``Command`` nodes, ``INTO`` targets, ...);
- GLOBAL IN / GLOBAL JOIN and per-query SETTINGS clauses are rejected;
- every referenced table must be a plain identifier inside the ``ashare``
  database that is present in the supplied whitelist — table-functions such
  as ``file()`` / ``url()`` / ``remote()`` / ``merge()`` are rejected;
- forced LIMIT: an outermost SELECT without LIMIT gets ``LIMIT 500``; a LIMIT
  above 500 is clamped to 500; a non-integer or ``LIMIT ... BY`` form is
  rejected rather than guessed at.

It also carries the result-side defenses shared with the exploration tools:
explicit Python-native cell serialization (defense-in-depth against the
official ClickHouse MCP #111 UInt64 crash), an explicit ~50KB result cap with
a truncation declaration, and the append-only ``ch_query`` audit log.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

# sqlglot logs a WARNING for every construct it degrades to a Command node
# (SYSTEM / ATTACH / RENAME / ...). The guard rejects those nodes anyway, so
# the warnings are pure noise on the tool's stderr.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_LIMIT = 500
MAX_RESULT_BYTES = 50 * 1024
_ENVELOPE_RESERVE_BYTES = 1024
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SNAPSHOT_PREFIX = "ashare__"

# Statement / clause types that can never appear in a read-only SELECT. Built
# dynamically so a sqlglot upgrade that renames a class degrades to "missing"
# instead of crashing the import of every tool in this package.
_FORBIDDEN_TYPE_NAMES = (
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "AlterColumn",
    "TruncateTable",
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Command",
    "Use",
    "Set",
    "SetItem",
    "Kill",
    "Attach",
    "Detach",
    "Grant",
    "Revoke",
    "Into",
    "Union",
    "Intersect",
    "Except",
    "Transaction",
    "Commit",
    "Rollback",
    "Describe",
    "Values",
    "Optimize",
    "Move",
    "Refresh",
    "Declare",
)
_FORBIDDEN_TYPES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_TYPE_NAMES if hasattr(exp, name)
)


class QueryGuardError(Exception):
    """Raised when the SQL guard rejects a query; carries the guard reason."""

    def __init__(self, reason: str, category: str) -> None:
        super().__init__(reason)
        self.category = category


@dataclass(frozen=True)
class GuardedQuery:
    """Outcome of a successful guard pass."""

    sql: str
    limit_applied: int


def is_valid_table_name(name: Any) -> bool:
    """Return True when ``name`` is safe to interpolate into SQL.

    Only ``[A-Za-z0-9_]+`` identifiers pass; everything else (quotes, spaces,
    semicolons, empty strings, non-strings) is rejected.
    """
    return isinstance(name, str) and bool(_TABLE_NAME_RE.fullmatch(name))


def snapshot_table_names() -> set[str]:
    """Return ashare table names derived from the DDL snapshot filenames.

    The snapshot files are named ``ashare__<table>.sql`` (see
    ``schema/clickhouse/README.md``). Used as the offline whitelist source when
    the live server is unreachable.
    """
    schema_dir = Path(__file__).resolve().parents[3] / "schema" / "clickhouse"
    names: set[str] = set()
    try:
        for path in schema_dir.glob(f"{_SNAPSHOT_PREFIX}*.sql"):
            names.add(path.name[len(_SNAPSHOT_PREFIX) : -len(".sql")])
    except OSError:
        logger.warning(
            "ClickHouse schema snapshot directory unreadable: %s", schema_dir
        )
    return names


def guard_select(
    sql: Any, allowed_tables: set[str], database: str = "ashare"
) -> GuardedQuery:
    """Validate and normalize an LLM-supplied SELECT for the ashare database.

    Args:
        sql: Candidate SQL text (must be a single read-only SELECT).
        allowed_tables: Whitelist of table names in ``database``.
        database: The only database a table reference may resolve to.

    Returns:
        A :class:`GuardedQuery` carrying the regenerated SQL with the forced
        LIMIT applied.

    Raises:
        QueryGuardError: On ANY ambiguity or policy violation (fail closed).
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QueryGuardError("query must be a non-empty SQL string", "empty_query")

    try:
        statements = sqlglot.parse(sql, read="clickhouse")
    except sqlglot.errors.ParseError as exc:
        raise QueryGuardError(f"SQL parse failed: {exc}", "parse_error") from exc

    statements = [stmt for stmt in statements if stmt is not None]
    if len(statements) == 0:
        raise QueryGuardError("no SQL statement found", "empty_query")
    if len(statements) > 1:
        raise QueryGuardError(
            f"exactly one statement is allowed, found {len(statements)}",
            "multiple_statements",
        )

    select = statements[0]
    if not isinstance(select, exp.Select):
        raise QueryGuardError(
            f"only a plain SELECT is allowed, got {type(select).__name__}",
            "non_select_root",
        )

    _reject_forbidden_nodes(select)
    _reject_global_and_settings(select)
    _check_table_whitelist(select, allowed_tables, database)
    limit_applied = _enforce_limit(select)

    return GuardedQuery(
        sql=select.sql(dialect="clickhouse"), limit_applied=limit_applied
    )


def _reject_forbidden_nodes(select: exp.Select) -> None:
    """Reject any write/DDL/system construct anywhere in the AST."""
    for node in select.walk():
        if isinstance(node, _FORBIDDEN_TYPES):
            raise QueryGuardError(
                f"forbidden construct in query: {type(node).__name__}",
                "forbidden_construct",
            )
        if isinstance(node, exp.Placeholder):
            raise QueryGuardError(
                "parameter placeholders ({name:Type}) are not supported",
                "placeholder_disallowed",
            )


def _reject_global_and_settings(select: exp.Select) -> None:
    """Reject GLOBAL IN/JOIN fan-out and per-query SETTINGS overrides."""
    for node in select.walk():
        if isinstance(node, exp.In) and node.args.get("is_global"):
            raise QueryGuardError(
                "GLOBAL IN subqueries are not allowed", "global_disallowed"
            )
        if isinstance(node, exp.Join) and node.args.get("global_"):
            raise QueryGuardError("GLOBAL JOIN is not allowed", "global_disallowed")
    if select.args.get("settings"):
        raise QueryGuardError(
            "per-query SETTINGS clauses are not allowed", "settings_disallowed"
        )


def _cte_names(select: exp.Select) -> set[str]:
    """Return the alias names introduced by WITH clauses.

    A CTE alias is referenced like a table in the FROM clause but is not a
    physical table, so it must be exempt from the ashare whitelist.
    """
    names: set[str] = set()
    with_clause = select.find(exp.With)
    if with_clause is None:
        return names
    for cte in with_clause.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias is not None and alias.name:
            names.add(alias.name)
    return names


def _check_table_whitelist(
    select: exp.Select, allowed_tables: set[str], database: str
) -> None:
    """Ensure every table reference is a whitelisted ashare table."""
    ctes = _cte_names(select)
    for table in select.find_all(exp.Table):
        if not table.db and table.name in ctes:
            continue
        if not isinstance(table.this, exp.Identifier):
            raise QueryGuardError(
                "table-valued functions and non-table sources are not allowed",
                "table_function_disallowed",
            )
        if table.catalog:
            raise QueryGuardError(
                f"cross-catalog reference is not allowed: {table.sql()}",
                "cross_database_reference",
            )
        if table.db and table.db != database:
            raise QueryGuardError(
                f"cross-database reference is not allowed: {table.sql()}",
                "cross_database_reference",
            )
        if table.name not in allowed_tables:
            raise QueryGuardError(
                f"table '{table.name}' is not in the ashare whitelist",
                "unknown_table",
            )


def _enforce_limit(select: exp.Select) -> int:
    """Force a bounded outermost LIMIT; return the limit that will apply."""
    limit = select.args.get("limit")
    if limit is None:
        select.set("limit", exp.Limit(expression=exp.Literal.number(MAX_LIMIT)))
        return MAX_LIMIT

    if limit.args.get("expressions"):
        raise QueryGuardError("LIMIT ... BY is not supported", "invalid_limit")
    if limit.args.get("limit_options"):
        raise QueryGuardError("LIMIT ... WITH TIES is not supported", "invalid_limit")

    literal = limit.expression
    if not isinstance(literal, exp.Literal) or not literal.is_int:
        raise QueryGuardError("LIMIT must be a plain integer literal", "invalid_limit")

    value = int(literal.this)
    if value <= 0:
        raise QueryGuardError("LIMIT must be a positive integer", "invalid_limit")
    if value > MAX_LIMIT:
        limit.set("expression", exp.Literal.number(MAX_LIMIT))
        return MAX_LIMIT
    return value


# ---------------------------------------------------------------------------
# Result serialization (official-MCP #111 defense-in-depth)
# ---------------------------------------------------------------------------


def serialize_cell(value: Any) -> Any:
    """Convert one driver cell to an explicit Python-native JSON-safe value.

    Never relies on driver defaults for exotic types: ints stay ints (UInt64
    included), non-finite floats become None, dates become ISO strings,
    Decimals become floats, containers are converted recursively.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (timedelta, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [serialize_cell(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_cell(item) for key, item in value.items()}
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return serialize_cell(item())
        except Exception:  # noqa: BLE001 - fall through to str()
            pass
    return str(value)


def serialize_rows(columns: Any, rows: Any) -> tuple[list[str], list[list[Any]]]:
    """Serialize a driver result to native column names and row lists."""
    native_columns = [str(column) for column in (columns or [])]
    native_rows = [[serialize_cell(cell) for cell in row] for row in (rows or [])]
    return native_columns, native_rows


def truncate_to_budget(rows: list[list[Any]]) -> tuple[list[list[Any]], bool]:
    """Drop trailing rows so the serialized payload fits ~MAX_RESULT_BYTES.

    The first row is always kept when present (a single oversized row is
    reported rather than silently yielding an empty result). Callers must
    declare the truncation explicitly in their envelope.
    """
    budget = MAX_RESULT_BYTES - _ENVELOPE_RESERVE_BYTES
    kept: list[list[Any]] = []
    used = 2
    for row in rows:
        cost = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + (
            1 if kept else 0
        )
        if kept and used + cost > budget:
            break
        kept.append(row)
        used += cost
    return kept, len(kept) < len(rows)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def audit_log_path() -> Path:
    """Return ``<runtime root>/logs/ch_query_audit.jsonl``."""
    from src.config.paths import get_runtime_root

    return get_runtime_root() / "logs" / "ch_query_audit.jsonl"


def append_audit_record(record: dict[str, Any]) -> None:
    """Append one JSON line to the ch_query audit log; never raises.

    An audit-write failure must never break the query itself.
    """
    try:
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - audit failure must not break the query
        logger.debug("ch_query audit write failed: %s", exc)
