"""Tests for tools/clickhouse_apply_comments.py (Phase 0 P0.2 semantic layer).

Covers the comments.yaml contract (structure, exact Tier-1 column counts,
non-empty comments), SQL literal escaping (embedded quotes, backslashes,
Chinese text), and dry-run statement generation from a fixture YAML.

No network access and no ClickHouse server required.

Run with::

    pytest tools/test_clickhouse_apply_comments.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from clickhouse_apply_comments import (  # type: ignore[import-untyped]
    CONVENTION,
    build_statements,
    default_yaml_path,
    escape_sql_string,
    load_comments,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DUMP_PATH = REPO_ROOT / "tmp" / "ch_dumps" / "columns_dump.jsonl"

# Exact Tier-1 column counts from the system.columns dump.
TIER1_COUNTS = {
    "stk_factor_pro": 199,
    "fin_indicator": 168,
    "stk_moneyflow": 20,
    "stk_top_list": 15,
    "stk_margin": 11,
    "stk_info": 10,
    "stk_top_inst": 10,
    "stk_moneyflow_hsgt": 7,
    "trade_calendar": 4,
}


@pytest.fixture(scope="module")
def comments_yaml() -> Path:
    path = default_yaml_path()
    assert path.is_file(), f"missing deliverable: {path}"
    return path


@pytest.fixture(scope="module")
def parsed(comments_yaml: Path) -> dict:
    with comments_yaml.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# comments.yaml structure
# ---------------------------------------------------------------------------


def test_yaml_parses_with_safe_load(comments_yaml: Path) -> None:
    with comments_yaml.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict)


def test_yaml_contract_header(parsed: dict) -> None:
    assert parsed["version"] == 1
    assert parsed["convention"] == CONVENTION


def test_all_tier1_tables_present_with_exact_counts(parsed: dict) -> None:
    assert set(parsed["tables"]) == set(TIER1_COUNTS)
    for table, expected in TIER1_COUNTS.items():
        columns = parsed["tables"][table]["columns"]
        assert len(columns) == expected, f"{table}: {len(columns)} != {expected}"


def test_every_table_has_api_and_doc(parsed: dict) -> None:
    for table, spec in parsed["tables"].items():
        assert isinstance(spec.get("api"), str) and spec["api"], table
        assert isinstance(spec.get("doc"), str) and spec["doc"].startswith(
            "http"
        ), table


def test_every_comment_is_nonempty_str(parsed: dict) -> None:
    for table, spec in parsed["tables"].items():
        for column, comment in spec["columns"].items():
            assert isinstance(comment, str), f"{table}.{column}"
            assert comment.strip(), f"{table}.{column} has an empty comment"
            assert len(comment) <= 500, f"{table}.{column} exceeds 500 chars"


def test_load_comments_matches_yaml_order(comments_yaml: Path, parsed: dict) -> None:
    loaded = load_comments(comments_yaml)
    assert set(loaded) == set(parsed["tables"])
    for table in loaded:
        assert list(loaded[table]) == list(parsed["tables"][table]["columns"])


@pytest.mark.skipif(not DUMP_PATH.is_file(), reason="columns dump not available")
def test_column_names_match_system_columns_dump(parsed: dict) -> None:
    dump: dict[str, set[str]] = {}
    with DUMP_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["table"] in TIER1_COUNTS:
                dump.setdefault(rec["table"], set()).add(rec["name"])
    for table in TIER1_COUNTS:
        yaml_cols = set(parsed["tables"][table]["columns"])
        assert yaml_cols == dump[table], f"{table}: {yaml_cols ^ dump[table]}"


# ---------------------------------------------------------------------------
# SQL escaping
# ---------------------------------------------------------------------------


def test_escape_embedded_single_quote() -> None:
    assert escape_sql_string("it's") == "it''s"
    assert escape_sql_string("'") == "''"
    assert escape_sql_string("a'b'c") == "a''b''c"


def test_escape_backslash() -> None:
    assert escape_sql_string("a\\b") == "a\\\\b"
    assert escape_sql_string("\\") == "\\\\"


def test_escape_chinese_text_passthrough() -> None:
    text = "成交额（千元）; 换手率"
    assert escape_sql_string(text) == text


def test_escape_combined_quote_backslash_chinese() -> None:
    text = "含'引号\\反斜杠和中文"
    assert escape_sql_string(text) == "含''引号\\\\反斜杠和中文"


def test_escape_leaves_no_unpaired_quote() -> None:
    for text in ["it's", "''", "a'b\\c'd", "中文'测试\\"]:
        escaped = escape_sql_string(text)
        assert escaped.replace("''", "").find("'") == -1


# ---------------------------------------------------------------------------
# Dry-run statement generation (fixture YAML, no network)
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_yaml(tmp_path: Path) -> Path:
    doc = {
        "version": 1,
        "convention": CONVENTION,
        "tables": {
            "tbl_a": {
                "api": "api_a",
                "doc": "https://example.com/a",
                "columns": {
                    "ts_code": "source=tushare api_a; desc=代码",
                    "amount": "unit=千元; desc=it's 成交额 \\ 中文",
                },
            },
            "tbl_b": {
                "api": "api_b",
                "doc": "https://example.com/b",
                "columns": {
                    "cal_date": "source=tushare api_b; desc=日历日期",
                },
            },
        },
    }
    path = tmp_path / "comments.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
    return path


def test_fixture_dry_run_statements(fixture_yaml: Path) -> None:
    comments = load_comments(fixture_yaml)
    statements = build_statements(comments, database="ashare")
    assert [s.sql for s in statements] == [
        "ALTER TABLE ashare.tbl_a COMMENT COLUMN ts_code 'source=tushare api_a; desc=代码'",
        (
            "ALTER TABLE ashare.tbl_a COMMENT COLUMN amount "
            "'unit=千元; desc=it''s 成交额 \\\\ 中文'"
        ),
        "ALTER TABLE ashare.tbl_b COMMENT COLUMN cal_date 'source=tushare api_b; desc=日历日期'",
    ]
    assert [(s.table, s.column) for s in statements] == [
        ("tbl_a", "ts_code"),
        ("tbl_a", "amount"),
        ("tbl_b", "cal_date"),
    ]


def test_fixture_tables_filter(fixture_yaml: Path) -> None:
    comments = load_comments(fixture_yaml)
    statements = build_statements(comments, database="ashare", only=["tbl_b"])
    assert len(statements) == 1
    assert statements[0].table == "tbl_b"


def test_fixture_unknown_table_raises(fixture_yaml: Path) -> None:
    comments = load_comments(fixture_yaml)
    with pytest.raises(ValueError, match="unknown table"):
        build_statements(comments, database="ashare", only=["nope"])


def test_identifier_validation_rejects_injection(fixture_yaml: Path) -> None:
    comments = load_comments(fixture_yaml)
    comments["bad; DROP TABLE x"] = comments.pop("tbl_b")
    with pytest.raises(ValueError, match="unsafe table identifier"):
        build_statements(comments, database="ashare")


def test_load_comments_rejects_empty_comment(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "convention": CONVENTION,
        "tables": {
            "tbl": {
                "api": "api",
                "doc": "https://example.com",
                "columns": {"col": "   "},
            }
        },
    }
    path = tmp_path / "bad.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh)
    with pytest.raises(ValueError, match="empty comment"):
        load_comments(path)


def test_main_dry_run_exit_code(
    fixture_yaml: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--yaml", str(fixture_yaml), "--database", "ashare"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "dry-run: 3 ALTER statements" in captured.out
    assert "COMMENT COLUMN ts_code" in captured.out


def test_main_dry_run_tables_filter(
    fixture_yaml: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        ["--yaml", str(fixture_yaml), "--database", "ashare", "--tables", "tbl_b"]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "dry-run: 1 ALTER statements" in captured.out
    assert "tbl_a" not in captured.out


def test_main_missing_yaml_returns_error(tmp_path: Path) -> None:
    rc = main(["--yaml", str(tmp_path / "nope.yaml")])
    assert rc == 1
