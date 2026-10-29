"""Tests for tools/clickhouse_export_ddl.py.

Covers ONLY the pure formatting / parsing functions and the local
dump-to-file path — no ClickHouse server is contacted anywhere.

Run with::

    pytest tools/test_clickhouse_export_ddl.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clickhouse_export_ddl import (  # type: ignore[import-untyped]
    check_snapshots,
    export_snapshots,
    extract_column_list,
    format_create_table,
    load_dump,
    parse_snapshot_columns,
    render_snapshot,
    snapshot_filename,
    split_tail_clauses,
    split_top_level_commas,
)

# Single-line statement exactly as SHOW CREATE TABLE emits it.
SAMPLE_QUERY = (
    "CREATE TABLE ashare.demo (`ts_code` String, `trade_date` Date, "
    "`close` Float64 DEFAULT 0 COMMENT 'unit=yuan') "
    "ENGINE = MergeTree PARTITION BY toYYYYMM(trade_date) "
    "ORDER BY (ts_code, trade_date) SETTINGS index_granularity = 8192"
)

SAMPLE_FORMATTED = (
    "CREATE TABLE ashare.demo\n"
    "(\n"
    "    `ts_code` String,\n"
    "    `trade_date` Date,\n"
    "    `close` Float64 DEFAULT 0 COMMENT 'unit=yuan'\n"
    ")\n"
    "ENGINE = MergeTree\n"
    "PARTITION BY toYYYYMM(trade_date)\n"
    "ORDER BY (ts_code, trade_date)\n"
    "SETTINGS index_granularity = 8192\n"
)


# ---------------------------------------------------------------------------
# Format stability
# ---------------------------------------------------------------------------


class TestFormatStability:
    """Same input must always produce identical bytes (no timestamps)."""

    def test_format_matches_golden(self) -> None:
        assert format_create_table("demo", SAMPLE_QUERY) == SAMPLE_FORMATTED

    def test_repeated_format_identical(self) -> None:
        first = render_snapshot("demo", SAMPLE_QUERY)
        second = render_snapshot("demo", SAMPLE_QUERY)
        assert first == second

    def test_header_line(self) -> None:
        text = render_snapshot("demo", SAMPLE_QUERY)
        assert text.splitlines()[0] == (
            "-- ClickHouse DDL snapshot: ashare.demo "
            "(exported by tools/clickhouse_export_ddl.py)"
        )

    def test_database_override(self) -> None:
        text = format_create_table("demo", SAMPLE_QUERY, database="other")
        assert text.splitlines()[0] == "CREATE TABLE other.demo"


class TestColumnClausePreservation:
    """Column definitions are preserved verbatim, one per line."""

    def test_default_comment_preserved(self) -> None:
        text = format_create_table("demo", SAMPLE_QUERY)
        assert "    `close` Float64 DEFAULT 0 COMMENT 'unit=yuan'" in text

    def test_commas_only_between_columns(self) -> None:
        lines = format_create_table("demo", SAMPLE_QUERY).splitlines()
        # line 0: CREATE TABLE, 1: "(", 2-4: columns, 5: ")"
        assert lines[2] == "    `ts_code` String,"
        assert lines[4] == "    `close` Float64 DEFAULT 0 COMMENT 'unit=yuan'"

    def test_nested_parens_and_quoted_comma(self) -> None:
        parts = split_top_level_commas("`a` Decimal(18, 4), `b` String DEFAULT 'x,y'")
        assert parts == ["`a` Decimal(18, 4)", "`b` String DEFAULT 'x,y'"]


class TestTailClauses:
    """Each ENGINE/PARTITION/ORDER/PRIMARY KEY/SETTINGS clause gets a line."""

    def test_clauses_on_own_lines(self) -> None:
        text = format_create_table("demo", SAMPLE_QUERY)
        assert "ENGINE = MergeTree\n" in text
        assert "PARTITION BY toYYYYMM(trade_date)\n" in text
        assert "ORDER BY (ts_code, trade_date)\n" in text
        assert "SETTINGS index_granularity = 8192\n" in text

    def test_primary_key_and_ttl(self) -> None:
        query = (
            "CREATE TABLE ashare.t (`a` String) ENGINE = ReplacingMergeTree "
            "ORDER BY a PRIMARY KEY a TTL a + INTERVAL 1 DAY "
            "SETTINGS index_granularity = 8192"
        )
        clauses = split_tail_clauses(query[query.find(")") + 1 :])
        assert clauses == [
            "ENGINE = ReplacingMergeTree",
            "ORDER BY a",
            "PRIMARY KEY a",
            "TTL a + INTERVAL 1 DAY",
            "SETTINGS index_granularity = 8192",
        ]

    def test_keyword_inside_function_not_split(self) -> None:
        # 'ORDER' inside an expression must not start a new clause.
        clauses = split_tail_clauses(
            "ENGINE = MergeTree ORDER BY (a, cityHash64(b)) SETTINGS x = 1"
        )
        assert clauses == [
            "ENGINE = MergeTree",
            "ORDER BY (a, cityHash64(b))",
            "SETTINGS x = 1",
        ]


class TestParseErrors:
    """Malformed statements fail loudly instead of producing bad snapshots."""

    def test_missing_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="ENGINE"):
            extract_column_list("CREATE TABLE ashare.t (`a` String)")

    def test_unbalanced_parens_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_column_list("CREATE TABLE ashare.t (`a` String ENGINE = MergeTree")

    def test_unsafe_table_name_raises(self) -> None:
        with pytest.raises(ValueError, match="unsafe table name"):
            format_create_table("bad;name", SAMPLE_QUERY)


# ---------------------------------------------------------------------------
# Round-trip: formatted file -> column names
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Column-list parsing from a formatted file round-trips."""

    def test_round_trip_names(self) -> None:
        text = render_snapshot("demo", SAMPLE_QUERY)
        assert parse_snapshot_columns(text) == ["ts_code", "trade_date", "close"]

    def test_parse_rejects_garbage_line(self) -> None:
        text = "CREATE TABLE ashare.t\n(\n    not-a-column\n)\nENGINE = MergeTree\n"
        with pytest.raises(ValueError):
            parse_snapshot_columns(text)


# ---------------------------------------------------------------------------
# Local dump -> file path (still no network)
# ---------------------------------------------------------------------------


def _write_dump(path: Path) -> None:
    rows = [
        {
            "name": "t_two",
            "create_table_query": "CREATE TABLE ashare.t_two "
            "(`b` String) ENGINE = MergeTree ORDER BY b SETTINGS index_granularity = 8192",
        },
        {
            "name": "t_one",
            "create_table_query": "CREATE TABLE ashare.t_one "
            "(`a` String) ENGINE = MergeTree ORDER BY a SETTINGS index_granularity = 8192",
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class TestDumpExport:
    """load_dump + export_snapshots are deterministic and alphabetical."""

    def test_load_dump_sorts_and_exports(self, tmp_path: Path) -> None:
        dump = tmp_path / "dump.jsonl"
        _write_dump(dump)
        ddls = load_dump(dump)
        assert set(ddls) == {"t_one", "t_two"}

        out_dir = tmp_path / "schema"
        written = export_snapshots(ddls, "ashare", out_dir)
        assert [p.name for p in written] == ["ashare__t_one.sql", "ashare__t_two.sql"]

        # Idempotent: a second export yields identical bytes.
        first_bytes = {p.name: p.read_bytes() for p in written}
        export_snapshots(ddls, "ashare", out_dir)
        for path in written:
            assert path.read_bytes() == first_bytes[path.name]

    def test_check_passes_then_detects_drift(self, tmp_path: Path) -> None:
        dump = tmp_path / "dump.jsonl"
        _write_dump(dump)
        ddls = load_dump(dump)
        out_dir = tmp_path / "schema"
        export_snapshots(ddls, "ashare", out_dir)

        assert check_snapshots(ddls, "ashare", out_dir) == 0

        # Mutate one snapshot -> drift.
        target = out_dir / "ashare__t_one.sql"
        target.write_text(
            target.read_text(encoding="utf-8") + "-- stray\n", encoding="utf-8"
        )
        assert check_snapshots(ddls, "ashare", out_dir) == 1

        # Delete one snapshot -> drift.
        target.unlink()
        assert check_snapshots(ddls, "ashare", out_dir) == 1

    def test_load_dump_rejects_bad_records(self, tmp_path: Path) -> None:
        dump = tmp_path / "bad.jsonl"
        dump.write_text('{"name": "t"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="bad dump record"):
            load_dump(dump)

    def test_snapshot_filename(self) -> None:
        assert snapshot_filename("fin_audit", "ashare") == "ashare__fin_audit.sql"
