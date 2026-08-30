"""Constrained ad-hoc SQL exploration of the ClickHouse ashare warehouse.

``ch_query`` is the Execute tier of the flexibility channel
(mymain-wiki/clickhouse/CLICKHOUSE_ITERATION_PLAN.md Phase 2). Safety model, all fail-closed:

- connects ONLY with the dedicated read-only ``llm_role`` credentials
  (``CLICKHOUSE_LLM_USER`` / ``CLICKHOUSE_LLM_PASSWORD`` via the centralized
  env config). When they are unset the tool fails with an actionable error —
  it NEVER falls back to the default user;
- the SQL is parsed by sqlglot and must be a single plain SELECT over
  whitelisted ashare tables (see ``clickhouse_query_guard``);
- a forced ``LIMIT 500`` is injected/clamped on the outermost SELECT;
- server-side 30s timeout via ``max_execution_time``;
- results are explicitly serialized to Python-native types (official
  ClickHouse MCP #111 UInt64 defense) and capped at ~50KB with an explicit
  truncation declaration;
- every call appends one JSON line to ``~/.vibe-trading/logs/ch_query_audit.jsonl``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import clickhouse_connect

from src.agent.tools import BaseTool
from src.tools.clickhouse_query_guard import (
    MAX_LIMIT,
    QueryGuardError,
    append_audit_record,
    guard_select,
    serialize_rows,
    snapshot_table_names,
    truncate_to_budget,
)

logger = logging.getLogger(__name__)

_QUERY_TIMEOUT_S = 30
# Client socket budget sits above the server-side max_execution_time so the
# 30s ClickHouse cap — not a socket timeout — is what terminates slow queries.
_RECEIVE_TIMEOUT_S = 40


def _error_envelope(message: str, guard: str | None = None) -> dict[str, Any]:
    envelope: dict[str, Any] = {"ok": False, "error": message}
    if guard is not None:
        envelope["guard"] = guard
    return envelope


def _llm_role_credentials() -> tuple[str, str, str, int, str]:
    """Return (user, password, host, port, database) from the env config.

    Read strictly through the config accessor layer — never raw os.environ.
    """
    from src.config.accessor import get_env_config

    data = get_env_config().data
    return (
        data.clickhouse_llm_user,
        data.clickhouse_llm_password,
        data.clickhouse_host,
        data.clickhouse_port,
        data.clickhouse_database,
    )


def _load_table_whitelist(client: Any, database: str) -> set[str]:
    """Fetch the live ashare table list; degrade to the DDL snapshot names."""
    try:
        result = client.query(
            "SELECT name FROM system.tables WHERE database = {db:String}",
            parameters={"db": database},
        )
        names = {str(row[0]) for row in result.result_rows}
        if names:
            return names
    except Exception as exc:  # noqa: BLE001 - degrade to snapshot whitelist
        logger.debug("live ashare table list unavailable, using snapshots: %s", exc)
    return snapshot_table_names()


class ChQueryTool(BaseTool):
    """Run a guarded read-only SELECT against the ashare ClickHouse warehouse."""

    name = "ch_query"
    description = (
        "EXECUTE a constrained read-only SELECT against the ClickHouse A-share "
        "warehouse (database 'ashare', 56 tables). Safety model: connects only "
        "with the dedicated read-only llm_role credentials "
        "(CLICKHOUSE_LLM_USER / CLICKHOUSE_LLM_PASSWORD — never the default "
        "user); the SQL must parse as a SINGLE plain SELECT — any DDL/DML "
        "(INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE), SYSTEM/SET/USE/"
        "KILL-style statement, GLOBAL IN/JOIN, SETTINGS clause, INTO target, "
        "or table outside the ashare whitelist is rejected; LIMIT 500 is "
        "injected when missing and clamped when larger; results are capped at "
        "~50KB with an explicit truncation note; 30-second server timeout; "
        "every call is audit-logged. Discover schema first with "
        'ch_list_tables / ch_describe_table. Example: ch_query(sql="SELECT '
        "ts_code, close FROM stk_factor_pro WHERE trade_date = '2024-01-02'\")."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single read-only SELECT statement over ashare tables. "
                    "LIMIT is forced to at most 500 rows."
                ),
            },
        },
        "required": ["sql"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Guard, execute, serialize and audit one constrained SELECT.

        Args:
            **kwargs: ``sql`` (str, required).

        Returns:
            JSON envelope ``{"ok": true, "columns", "rows", "row_count",
            "truncated", "limit_applied", "elapsed_ms"}`` on success;
            ``{"ok": false, "error", "guard"?}`` on guard rejection or
            failure.
        """
        started = time.monotonic()
        sql = kwargs.get("sql")
        sql_text = sql if isinstance(sql, str) else ""

        user, password, host, port, database = _llm_role_credentials()
        if not user or not password:
            return self._finish(
                _error_envelope(
                    "ch_query requires the dedicated read-only llm_role "
                    "credentials: set CLICKHOUSE_LLM_USER and "
                    "CLICKHOUSE_LLM_PASSWORD (e.g. in agent/.env). This tool "
                    "never falls back to the default ClickHouse user.",
                    guard="missing_llm_role_credentials",
                ),
                sql=sql_text,
                rows_returned=0,
                truncated=False,
                started=started,
                error="missing_llm_role_credentials",
            )

        try:
            client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=user,
                password=password,
                database=database,
                settings={"max_execution_time": _QUERY_TIMEOUT_S},
                send_receive_timeout=_RECEIVE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - surface as error envelope
            return self._finish(
                _error_envelope(f"ClickHouse connection failed: {exc}"),
                sql=sql_text,
                rows_returned=0,
                truncated=False,
                started=started,
                error=f"connection_failed: {exc}",
            )

        try:
            return self._run_guarded_query(client, sql_text, database, started)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    def _run_guarded_query(
        self, client: Any, sql_text: str, database: str, started: float
    ) -> str:
        allowed_tables = _load_table_whitelist(client, database)
        try:
            guarded = guard_select(sql_text, allowed_tables, database=database)
        except QueryGuardError as exc:
            return self._finish(
                _error_envelope(str(exc), guard=exc.category),
                sql=sql_text,
                rows_returned=0,
                truncated=False,
                started=started,
                error=f"guard:{exc.category}",
            )

        try:
            result = client.query(guarded.sql)
        except Exception as exc:  # noqa: BLE001 - surface as error envelope
            return self._finish(
                _error_envelope(f"ClickHouse query failed: {exc}"),
                sql=guarded.sql,
                rows_returned=0,
                truncated=False,
                started=started,
                error=f"query_failed: {exc}",
            )

        columns, rows = serialize_rows(result.column_names, result.result_rows)
        kept_rows, truncated = truncate_to_budget(rows)
        envelope: dict[str, Any] = {
            "ok": True,
            "columns": columns,
            "rows": kept_rows,
            "row_count": len(kept_rows),
            "truncated": truncated,
            "limit_applied": guarded.limit_applied,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        if truncated:
            envelope["truncation_note"] = (
                f"result exceeded the ~{MAX_LIMIT}-row/50KB budget: "
                f"{len(kept_rows)} of {len(rows)} fetched rows returned; "
                "narrow the query (filters, fewer columns, smaller LIMIT) "
                "to see the rest"
            )
        return self._finish(
            envelope,
            sql=guarded.sql,
            rows_returned=len(kept_rows),
            truncated=truncated,
            started=started,
            error=None,
        )

    def _finish(
        self,
        envelope: dict[str, Any],
        *,
        sql: str,
        rows_returned: int,
        truncated: bool,
        started: float,
        error: str | None,
    ) -> str:
        """Audit the call (never breaking it) and serialize the envelope."""
        append_audit_record(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sql": sql,
                "rows_returned": rows_returned,
                "truncated": truncated,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "error": error,
            }
        )
        return json.dumps(envelope, ensure_ascii=False)
