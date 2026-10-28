"""Tests for tools/ci_clickhouse_comments_gate.py.

All fixtures are self-contained mini DDL snapshots + mini yaml files under
pytest's tmp_path — the real schema/clickhouse/comments.yaml (owned by a
separate workstream) is never read or written.

Run with::

    pytest tools/test_ci_clickhouse_comments_gate.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_clickhouse_comments_gate import (  # type: ignore[import-untyped]
    GateError,
    load_comments_yaml,
    main,
    parse_ddl_columns,
)

DEMO_DDL = """\
-- ClickHouse DDL snapshot: ashare.demo (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.demo
(
    `a` String,
    `b` Float64
)
ENGINE = MergeTree
ORDER BY a
SETTINGS index_granularity = 8192
"""

FULL_YAML = """\
tables:
  demo:
    columns:
      a: "stock code"
      b: "close price, unit=yuan"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup(
    tmp_path: Path, yaml_text: str | None, ddl_text: str | None = DEMO_DDL
) -> tuple[Path, Path]:
    """Write a mini schema dir + comments yaml; return (yaml, schema_dir)."""
    schema_dir = tmp_path / "schema" / "clickhouse"
    schema_dir.mkdir(parents=True)
    if ddl_text is not None:
        (schema_dir / "ashare__demo.sql").write_text(ddl_text, encoding="utf-8")
    comments = schema_dir / "comments.yaml"
    if yaml_text is not None:
        comments.write_text(yaml_text, encoding="utf-8")
    return comments, schema_dir


def _run_gate(comments: Path, schema_dir: Path) -> int:
    return main(["--comments", str(comments), "--schema-dir", str(schema_dir)])


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------


class TestFullCoverage:
    """Every DDL column documented with a non-empty comment -> exit 0."""

    def test_full_coverage_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        comments, schema_dir = _setup(tmp_path, FULL_YAML)
        assert _run_gate(comments, schema_dir) == 0
        assert "OK" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tests — missing comments (the CI-red requirement)
# ---------------------------------------------------------------------------


class TestMissingComment:
    """Removing one comment entry -> exit 1 naming the column."""

    def test_removed_comment_fails_naming_column(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        yaml_text = """\
tables:
  demo:
    columns:
      a: "stock code"
"""
        comments, schema_dir = _setup(tmp_path, yaml_text)
        assert _run_gate(comments, schema_dir) == 1
        out = capsys.readouterr().out
        assert "'b'" in out
        assert "demo" in out

    def test_empty_comment_string_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        yaml_text = 'tables:\n  demo:\n    columns:\n      a: "x"\n      b: ""\n'
        comments, schema_dir = _setup(tmp_path, yaml_text)
        assert _run_gate(comments, schema_dir) == 1
        assert "'b'" in capsys.readouterr().out

    def test_non_string_comment_fails(self, tmp_path: Path) -> None:
        yaml_text = 'tables:\n  demo:\n    columns:\n      a: "x"\n      b: 123\n'
        comments, schema_dir = _setup(tmp_path, yaml_text)
        assert _run_gate(comments, schema_dir) == 1


# ---------------------------------------------------------------------------
# Tests — yaml columns absent from the DDL (typo detection)
# ---------------------------------------------------------------------------


class TestExtraYamlColumn:
    """A yaml column that is not in the DDL -> exit 1."""

    def test_unknown_column_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        yaml_text = FULL_YAML + '      c: "typo column"\n'
        comments, schema_dir = _setup(tmp_path, yaml_text)
        assert _run_gate(comments, schema_dir) == 1
        out = capsys.readouterr().out
        assert "'c'" in out
        assert "typo" in out


# ---------------------------------------------------------------------------
# Tests — structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    """Missing files or malformed yaml fail closed with exit 1."""

    def test_comments_yaml_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        comments, schema_dir = _setup(tmp_path, yaml_text=None)
        assert _run_gate(comments, schema_dir) == 1
        assert "not found" in capsys.readouterr().out

    def test_ddl_snapshot_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        yaml_text = 'tables:\n  ghost:\n    columns:\n      x: "y"\n'
        comments, schema_dir = _setup(tmp_path, yaml_text)
        assert _run_gate(comments, schema_dir) == 1
        assert "ghost" in capsys.readouterr().out

    def test_empty_tables_mapping_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        comments, schema_dir = _setup(tmp_path, "tables: {}\n")
        assert _run_gate(comments, schema_dir) == 1
        assert "no tables" in capsys.readouterr().out

    def test_missing_tables_key_fails(self, tmp_path: Path) -> None:
        comments, schema_dir = _setup(tmp_path, "something_else: 1\n")
        assert _run_gate(comments, schema_dir) == 1

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        comments, schema_dir = _setup(tmp_path, "tables: [unclosed\n")
        assert _run_gate(comments, schema_dir) == 1

    def test_columns_key_missing_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        comments, schema_dir = _setup(tmp_path, "tables:\n  demo:\n    note: hi\n")
        assert _run_gate(comments, schema_dir) == 1
        assert "columns" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tests — pure parsers
# ---------------------------------------------------------------------------


class TestParsers:
    """Direct unit tests of the parsing helpers."""

    def test_parse_ddl_columns(self) -> None:
        assert parse_ddl_columns(DEMO_DDL) == ["a", "b"]

    def test_parse_ddl_columns_rejects_garbage(self) -> None:
        bad = "CREATE TABLE ashare.demo\n(\n    ???\n)\nENGINE = MergeTree\n"
        with pytest.raises(ValueError):
            parse_ddl_columns(bad)

    def test_load_comments_yaml_shape(self, tmp_path: Path) -> None:
        path = tmp_path / "comments.yaml"
        path.write_text(FULL_YAML, encoding="utf-8")
        tables = load_comments_yaml(path)
        assert set(tables) == {"demo"}

    def test_load_comments_yaml_rejects_non_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "comments.yaml"
        path.write_text("- just\n- a list\n", encoding="utf-8")
        with pytest.raises(GateError):
            load_comments_yaml(path)
