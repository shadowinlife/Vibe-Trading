"""Unit tests for the ch_query SQL guard and result-safety utilities.

The guard is the safety core of the ClickHouse flexibility channel
(CLICKHOUSE_ITERATION_PLAN.md Phase 2); it must fail CLOSED on any
ambiguity. No live ClickHouse is needed — everything here is pure AST /
serialization logic.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from uuid import UUID

import pytest

from src.tools.clickhouse_query_guard import (
    MAX_LIMIT,
    MAX_RESULT_BYTES,
    QueryGuardError,
    guard_select,
    is_valid_table_name,
    serialize_cell,
    serialize_rows,
    snapshot_table_names,
    truncate_to_budget,
)

WHITELIST = {"stk_factor_pro", "fin_indicator", "trade_calendar"}


def _guard(sql: str) -> str:
    return guard_select(sql, WHITELIST).sql


def _category(sql: str) -> str:
    with pytest.raises(QueryGuardError) as excinfo:
        guard_select(sql, WHITELIST)
    return excinfo.value.category


# ---------------------------------------------------------------------------
# (a) plain SELECT passes
# ---------------------------------------------------------------------------


class TestSelectPasses:
    def test_simple_select(self):
        assert "FROM stk_factor_pro" in _guard("SELECT ts_code FROM stk_factor_pro")

    def test_select_with_where_join(self):
        sql = (
            "SELECT a.ts_code, b.end_date FROM stk_factor_pro AS a "
            "ANY LEFT JOIN fin_indicator AS b USING (ts_code) "
            "WHERE a.trade_date >= '2024-01-01'"
        )
        assert "stk_factor_pro" in _guard(sql)

    def test_select_with_cte(self):
        sql = "WITH x AS (SELECT ts_code FROM trade_calendar) " "SELECT * FROM x"
        assert _guard(sql).startswith("WITH")

    def test_database_prefixed_table_allowed(self):
        assert "ashare.stk_factor_pro" in _guard("SELECT * FROM ashare.stk_factor_pro")

    def test_subquery_tables_whitelisted(self):
        sql = (
            "SELECT * FROM (SELECT ts_code FROM stk_factor_pro) AS sub "
            "WHERE ts_code IN (SELECT ts_code FROM fin_indicator)"
        )
        assert "stk_factor_pro" in _guard(sql)

    def test_clickhouse_specific_syntax_roundtrips(self):
        sql = (
            "SELECT ts_code, quantile(0.5)(turnover_rate) AS q50 "
            "FROM stk_factor_pro FINAL PREWHERE trade_date > '2024-01-01' "
            "GROUP BY ts_code"
        )
        assert "quantile" in _guard(sql)


# ---------------------------------------------------------------------------
# (b) every write / DDL / system statement is rejected
# ---------------------------------------------------------------------------


class TestWritesRejected:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO stk_factor_pro VALUES (1)",
            "UPDATE stk_factor_pro SET ts_code = 'x'",
            "DELETE FROM stk_factor_pro WHERE ts_code = 'x'",
            "DROP TABLE stk_factor_pro",
            "ALTER TABLE stk_factor_pro DROP COLUMN close",
            "CREATE TABLE evil (a Int32)",
            "TRUNCATE TABLE stk_factor_pro",
            "SYSTEM FLUSH LOGS",
            "SET max_threads = 1",
            "USE otherdb",
            "KILL QUERY WHERE query_id = 'abc'",
            "ATTACH TABLE stk_factor_pro",
            "DETACH TABLE stk_factor_pro",
            "GRANT SELECT ON stk_factor_pro TO someone",
            "SHOW TABLES",
            "DESCRIBE TABLE stk_factor_pro",
            "RENAME TABLE stk_factor_pro TO evil",
            "OPTIMIZE TABLE stk_factor_pro FINAL",
        ],
    )
    def test_non_select_statement_rejected(self, sql):
        category = _category(sql)
        assert category in {
            "non_select_root",
            "parse_error",
            "forbidden_construct",
        }

    def test_union_root_rejected(self):
        assert (
            _category(
                "SELECT * FROM stk_factor_pro UNION ALL SELECT * FROM fin_indicator"
            )
            == "non_select_root"
        )

    def test_union_inside_subquery_rejected(self):
        sql = (
            "SELECT * FROM (SELECT ts_code FROM stk_factor_pro "
            "UNION ALL SELECT ts_code FROM fin_indicator) AS u"
        )
        assert _category(sql) == "forbidden_construct"

    def test_select_into_table_rejected(self):
        assert (
            _category("SELECT * INTO TABLE fin_indicator FROM stk_factor_pro")
            == "forbidden_construct"
        )


# ---------------------------------------------------------------------------
# (c) multi-statement rejected
# ---------------------------------------------------------------------------


class TestMultiStatement:
    def test_two_statements_rejected(self):
        assert _category("SELECT 1; SELECT 2") == "multiple_statements"

    def test_select_then_write_rejected(self):
        assert (
            _category("SELECT * FROM stk_factor_pro; DROP TABLE stk_factor_pro")
            == "multiple_statements"
        )

    def test_empty_and_blank_rejected(self):
        assert _category("") == "empty_query"
        assert _category("   ") == "empty_query"
        assert _category(";") == "empty_query"

    def test_non_string_rejected(self):
        assert _category(None) == "empty_query"


# ---------------------------------------------------------------------------
# (d) table whitelist
# ---------------------------------------------------------------------------


class TestTableWhitelist:
    def test_unknown_table_rejected(self):
        assert _category("SELECT * FROM not_a_table") == "unknown_table"

    def test_system_table_rejected(self):
        assert _category("SELECT * FROM system.tables") == "cross_database_reference"

    def test_cross_database_rejected(self):
        assert _category("SELECT * FROM other_db.tbl") == "cross_database_reference"

    def test_cross_catalog_rejected(self):
        assert (
            _category("SELECT * FROM cat.other_db.stk_factor_pro")
            == "cross_database_reference"
        )

    def test_table_function_rejected(self):
        assert (
            _category("SELECT * FROM file('/tmp/x.csv')") == "table_function_disallowed"
        )
        assert (
            _category("SELECT * FROM remote('host', 'db', 'tbl')")
            == "table_function_disallowed"
        )
        assert _category("SELECT * FROM numbers(10)") == "table_function_disallowed"

    def test_global_in_rejected(self):
        sql = "SELECT * FROM stk_factor_pro WHERE ts_code GLOBAL IN (SELECT ts_code FROM fin_indicator)"
        assert _category(sql) == "global_disallowed"

    def test_global_join_rejected(self):
        sql = (
            "SELECT * FROM stk_factor_pro GLOBAL ANY LEFT JOIN fin_indicator "
            "USING (ts_code)"
        )
        assert _category(sql) == "global_disallowed"

    def test_settings_clause_rejected(self):
        sql = "SELECT * FROM stk_factor_pro SETTINGS max_threads = 1"
        assert _category(sql) == "settings_disallowed"

    def test_placeholder_rejected(self):
        sql = "SELECT * FROM stk_factor_pro WHERE ts_code = {code:String}"
        assert _category(sql) == "placeholder_disallowed"

    def test_parse_error_rejected(self):
        assert _category("SELECT FROM WHERE") == "parse_error"


# ---------------------------------------------------------------------------
# (e) forced LIMIT injection / clamping
# ---------------------------------------------------------------------------


class TestLimitInjection:
    def test_no_limit_gets_500(self):
        guarded = guard_select("SELECT * FROM stk_factor_pro", WHITELIST)
        assert guarded.sql.endswith(f"LIMIT {MAX_LIMIT}")
        assert guarded.limit_applied == MAX_LIMIT

    def test_limit_5000_clamped_to_500(self):
        guarded = guard_select("SELECT * FROM stk_factor_pro LIMIT 5000", WHITELIST)
        assert guarded.sql.endswith(f"LIMIT {MAX_LIMIT}")
        assert guarded.limit_applied == MAX_LIMIT

    def test_limit_100_untouched(self):
        guarded = guard_select("SELECT * FROM stk_factor_pro LIMIT 100", WHITELIST)
        assert guarded.sql.endswith("LIMIT 100")
        assert guarded.limit_applied == 100

    def test_offset_preserved_when_clamped(self):
        guarded = guard_select(
            "SELECT * FROM stk_factor_pro LIMIT 5000 OFFSET 7", WHITELIST
        )
        assert f"LIMIT {MAX_LIMIT}" in guarded.sql
        assert "OFFSET 7" in guarded.sql

    def test_limit_by_rejected(self):
        assert (
            _category("SELECT * FROM stk_factor_pro LIMIT 2 BY ts_code")
            == "invalid_limit"
        )

    def test_limit_zero_rejected(self):
        assert _category("SELECT * FROM stk_factor_pro LIMIT 0") == "invalid_limit"

    def test_limit_with_ties_rejected(self):
        assert (
            _category("SELECT * FROM stk_factor_pro LIMIT 5 WITH TIES")
            == "invalid_limit"
        )

    def test_non_literal_limit_rejected(self):
        assert (
            _category("SELECT * FROM stk_factor_pro LIMIT toUInt32(5)")
            == "invalid_limit"
        )


# ---------------------------------------------------------------------------
# (f) serialization — official-MCP #111 defense
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_uint64_big_int(self):
        big = 2**64 - 1
        assert serialize_cell(big) == big
        assert isinstance(serialize_cell(big), int)

    def test_none(self):
        assert serialize_cell(None) is None

    def test_bool_before_int(self):
        assert serialize_cell(True) is True

    def test_dates_to_iso(self):
        assert serialize_cell(dt.date(2024, 1, 2)) == "2024-01-02"
        assert serialize_cell(dt.datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05"

    def test_decimal_to_float(self):
        assert serialize_cell(Decimal("12.34")) == pytest.approx(12.34)

    def test_non_finite_floats_become_none(self):
        assert serialize_cell(float("nan")) is None
        assert serialize_cell(float("inf")) is None
        assert serialize_cell(1.5) == 1.5

    def test_bytes_decoded(self):
        assert serialize_cell("中".encode("utf-8")) == "中"

    def test_uuid_and_timedelta(self):
        value = UUID("12345678-1234-5678-1234-567812345678")
        assert serialize_cell(value) == str(value)
        assert serialize_cell(dt.timedelta(days=1)) == "1 day, 0:00:00"

    def test_nested_containers(self):
        cell = [1, Decimal("2.5"), {"k": dt.date(2024, 1, 2)}]
        assert serialize_cell(cell) == [1, 2.5, {"k": "2024-01-02"}]

    def test_unknown_type_falls_back_to_str(self):
        class Weird:
            def __str__(self):
                return "weird"

        assert serialize_cell(Weird()) == "weird"

    def test_serialize_rows_shape(self):
        columns, rows = serialize_rows(
            ("a", "b"),
            [(1, dt.date(2024, 1, 2)), (None, 2**63)],
        )
        assert columns == ["a", "b"]
        assert rows == [[1, "2024-01-02"], [None, 2**63]]
        json.dumps(rows)  # must be JSON-safe


# ---------------------------------------------------------------------------
# (g) truncation with explicit declaration support
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_small_result_not_truncated(self):
        rows = [[i, "x"] for i in range(10)]
        kept, truncated = truncate_to_budget(rows)
        assert not truncated
        assert kept == rows

    def test_oversized_result_truncated_with_declaration_data(self):
        rows = [[i, "payload" * 40] for i in range(400)]
        kept, truncated = truncate_to_budget(rows)
        assert truncated
        assert 0 < len(kept) < len(rows)
        serialized = json.dumps(kept, ensure_ascii=False).encode("utf-8")
        assert len(serialized) <= MAX_RESULT_BYTES

    def test_single_oversized_row_kept_not_silent(self):
        huge = [[0, "x" * (MAX_RESULT_BYTES + 1024)]]
        kept, truncated = truncate_to_budget(huge)
        assert len(kept) == 1
        assert not truncated


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_is_valid_table_name(self):
        assert is_valid_table_name("stk_factor_pro")
        assert is_valid_table_name("tbl_1")
        for bad in ("", "a b", "a;b", "a'b", "a-b", "ashare.tbl", None, 5):
            assert not is_valid_table_name(bad)

    def test_snapshot_table_names(self):
        names = snapshot_table_names()
        assert len(names) == 56
        assert "stk_factor_pro" in names
        assert "fin_indicator" in names
