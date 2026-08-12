"""ClickHouse schema-exploration tools (flexibility channel, L2 discovery).

Phase 2 of the ClickHouse semantic layer (CLICKHOUSE_ITERATION_PLAN.md §4)
adds a protected ad-hoc channel next to the deterministic domain tools:
``ch_list_tables`` and ``ch_describe_table`` are the Catalog → Inspect tiers
of the MCP three-tier progressive-discovery pattern; ``ch_query`` (see
``clickhouse_query_tool.py``) is the constrained Execute tier.

Both tools here are strictly read-only metadata/sample viewers. They connect
through :class:`~src.clickhouse_connector.ClickHouseConnector` (centralized
``CLICKHOUSE_*`` env config) and only read ``system.tables`` /
``system.columns`` plus a 3-row sample of the one inspected table. Table
names are validated against ``^[A-Za-z0-9_]+$`` before any query is built.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool
from src.clickhouse_connector import ClickHouseConnector
from src.tools.clickhouse_query_guard import is_valid_table_name, serialize_cell

logger = logging.getLogger(__name__)

_SAMPLE_ROW_LIMIT = 3

_TABLE_META_SQL = """
    SELECT name, comment, engine, partition_key, sorting_key, total_rows
    FROM system.tables
    WHERE database = {database:String} AND name = {table:String}
"""
_COLUMN_SQL = """
    SELECT name, type, comment
    FROM system.columns
    WHERE database = {database:String} AND table = {table:String}
    ORDER BY position
"""
_LIST_TABLES_SQL = """
    SELECT name, comment, total_rows
    FROM system.tables
    WHERE database = {database:String}
    ORDER BY name
"""


def _error_envelope(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _cell(value: Any) -> Any:
    """Normalize one metadata/sample cell to a JSON-safe native value."""
    return serialize_cell(value)


class ChListTablesTool(BaseTool):
    """List every ashare table with its one-line COMMENT description."""

    name = "ch_list_tables"
    description = (
        "LIST the ClickHouse A-share warehouse catalog: every table in the "
        "'ashare' database (56 tables, ~1279 columns) with its table-level "
        "COMMENT (may be empty where no description is documented yet) and an "
        "optional row-count estimate. Read-only metadata; use "
        "ch_describe_table(table) to inspect one table's columns and sample "
        "rows, then ch_query(sql) for constrained SELECTs. Example: "
        "ch_list_tables()."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, **kwargs: Any) -> str:
        """Return the sorted table catalog as a JSON envelope.

        Returns:
            ``{"ok": true, "database": str, "count": int, "tables":
            [{"table", "comment", "row_estimate"}]}`` on success, or
            ``{"ok": false, "error": str}`` when the warehouse is unreachable.
        """
        try:
            connector = ClickHouseConnector()
            frame = connector.query(
                _LIST_TABLES_SQL,
                params={"param_database": connector.database},
            )
        except Exception as exc:  # noqa: BLE001 - surface as error envelope
            logger.warning("ch_list_tables failed: %s", exc)
            return _error_envelope(f"ClickHouse catalog query failed: {exc}")

        tables: list[dict[str, Any]] = []
        for record in frame.to_dict(orient="records"):
            tables.append(
                {
                    "table": str(record.get("name", "")),
                    "comment": str(record.get("comment") or ""),
                    "row_estimate": _cell(record.get("total_rows")),
                }
            )
        envelope = {
            "ok": True,
            "database": connector.database,
            "count": len(tables),
            "tables": tables,
        }
        return json.dumps(envelope, ensure_ascii=False)


class ChDescribeTableTool(BaseTool):
    """Describe one ashare table: columns, types, COMMENTs, keys, samples."""

    name = "ch_describe_table"
    description = (
        "INSPECT one ClickHouse ashare table before querying it: every column "
        "with its type and COMMENT (documented on the Tier-1 tables), the "
        "engine / partition key / sorting key, the table COMMENT, and 2-3 "
        "sample rows. Read-only. The table name must be one of the ashare "
        "tables (see ch_list_tables). Example: "
        "ch_describe_table(table='stk_factor_pro')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "description": (
                    "Ashare table name to inspect, e.g. 'stk_factor_pro'. "
                    "Must match [A-Za-z0-9_]+ and exist in the database."
                ),
            },
        },
        "required": ["table"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Return the table description as a JSON envelope.

        Args:
            **kwargs: ``table`` (str, required).

        Returns:
            ``{"ok": true, "table", "comment", "engine", "partition_key",
            "sorting_key", "columns": [{"name","type","comment"}],
            "sample_rows": {"columns": [...], "rows": [[...]]}}`` on success;
            ``{"ok": false, "error": str}`` on an unknown/invalid table or an
            unreachable warehouse.
        """
        table = kwargs.get("table")
        if not is_valid_table_name(table):
            return _error_envelope(
                "invalid table name: must match [A-Za-z0-9_]+ "
                f"(got {table!r}); use ch_list_tables to list valid names"
            )

        try:
            connector = ClickHouseConnector()
            database = connector.database
            meta_frame = connector.query(
                _TABLE_META_SQL,
                params={"param_database": database, "param_table": table},
            )
        except Exception as exc:  # noqa: BLE001 - surface as error envelope
            logger.warning("ch_describe_table(%s) meta query failed: %s", table, exc)
            return _error_envelope(f"ClickHouse metadata query failed: {exc}")

        if meta_frame.empty:
            return _error_envelope(
                f"unknown table '{table}' in database '{database}'; "
                "use ch_list_tables to list valid table names"
            )

        meta = meta_frame.to_dict(orient="records")[0]
        try:
            column_frame = connector.query(
                _COLUMN_SQL,
                params={"param_database": database, "param_table": table},
            )
            sample_frame = connector.query(
                f"SELECT * FROM {table} LIMIT {_SAMPLE_ROW_LIMIT}"
            )
        except Exception as exc:  # noqa: BLE001 - surface as error envelope
            logger.warning("ch_describe_table(%s) detail query failed: %s", table, exc)
            return _error_envelope(f"ClickHouse detail query failed: {exc}")

        columns = [
            {
                "name": str(record.get("name", "")),
                "type": str(record.get("type", "")),
                "comment": str(record.get("comment") or ""),
            }
            for record in column_frame.to_dict(orient="records")
        ]
        sample_rows = [
            [_cell(cell) for cell in row] for row in sample_frame.values.tolist()
        ]
        envelope = {
            "ok": True,
            "table": table,
            "comment": str(meta.get("comment") or ""),
            "engine": str(meta.get("engine") or ""),
            "partition_key": str(meta.get("partition_key") or ""),
            "sorting_key": str(meta.get("sorting_key") or ""),
            "row_estimate": _cell(meta.get("total_rows")),
            "columns": columns,
            "sample_rows": {
                "columns": [str(column) for column in sample_frame.columns],
                "rows": sample_rows,
            },
        }
        return json.dumps(envelope, ensure_ascii=False)
