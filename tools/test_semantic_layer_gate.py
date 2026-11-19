"""Tests for the semantic-layer consistency gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.ci_semantic_layer_gate import REQUIRED_RULE_SECTIONS, check


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rules() -> str:
    return "\n".join(f"## {s}. section\n\nbody\n" for s in REQUIRED_RULE_SECTIONS)


def _build_repo(root: Path, *, columns: list[str] | None = None) -> Path:
    cols = columns or ["ts_code", "trade_date", "close"]
    metadata = {
        "database": "ashare",
        "exported_at": "2026-10-29",
        "table_count": 1,
        "tables": [
            {
                "name": "stk_demo",
                "engine": "MergeTree",
                "partition_key": "",
                "sorting_key": "",
                "comment": "",
                "total_rows": 10,
                "columns": [
                    {
                        "position": i + 1,
                        "name": c,
                        "type": "String",
                        "comment": "",
                        "default_kind": "",
                    }
                    for i, c in enumerate(cols)
                ],
                "date_column": "trade_date",
                "date_type": "Date",
                "min_date": "2026-01-01",
                "max_date": "2026-10-28",
            }
        ],
    }
    catalog = {
        "version": 1,
        "domains": {"basic": "基础"},
        "tables": {
            "stk_demo": {
                "domain": "basic",
                "grain": "标的-交易日",
                "primary_key": ["ts_code", "trade_date"],
                "use": "demo",
            }
        },
    }
    _write(root / "schema/clickhouse/metadata.json", json.dumps(metadata))
    _write(root / "schema/clickhouse/catalog/catalog.yaml", yaml.safe_dump(catalog))
    out = root / "OpencodeAgent/skills/semantic-layer"
    _write(out / "SKILL.md", "# skill\n")
    _write(out / "RULES.md", _rules())
    _write(out / "INDEX.md", "[stk_demo](tables/stk_demo.md)\n")
    rows = "\n".join(f"| {i + 1} | `{c}` | `String` | — |" for i, c in enumerate(cols))
    _write(
        out / "tables/stk_demo.md",
        f"# stk_demo\n\n| # | 列 | 类型 | 注释 |\n|---:|---|---|---|\n{rows}\n",
    )
    return root


def test_real_repo_passes() -> None:
    repo = Path(__file__).resolve().parents[1]
    assert check(repo) == []


def test_missing_table_doc_fails(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    (root / "OpencodeAgent/skills/semantic-layer/tables/stk_demo.md").unlink()
    errors = check(root)
    assert any("missing tables/stk_demo.md" in e for e in errors)


def test_column_drift_fails(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    _write(
        root / "schema/clickhouse/metadata.json",
        json.dumps(
            {
                **json.loads((root / "schema/clickhouse/metadata.json").read_text()),
                "tables": [
                    {
                        **json.loads(
                            (root / "schema/clickhouse/metadata.json").read_text()
                        )["tables"][0],
                        "columns": [
                            {
                                "position": 1,
                                "name": "ts_code",
                                "type": "String",
                                "comment": "",
                                "default_kind": "",
                            },
                            {
                                "position": 2,
                                "name": "pct_change",
                                "type": "Float64",
                                "comment": "",
                                "default_kind": "",
                            },
                        ],
                    }
                ],
            }
        ),
    )
    errors = check(root)
    assert any("column list mismatch" in e for e in errors)


def test_dead_link_and_unknown_table_fail(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    _write(
        root / "OpencodeAgent/skills/semantic-layer/INDEX.md",
        "[stk_demo](tables/stk_demo.md)\n[gone](tables/stk_gone.md)\n参见 `stk_missing`\n",
    )
    errors = check(root)
    assert any("dead link" in e for e in errors)
    assert any("unknown table stk_missing" in e for e in errors)


def test_missing_catalog_entry_fails(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    catalog = yaml.safe_load(
        (root / "schema/clickhouse/catalog/catalog.yaml").read_text()
    )
    catalog["tables"] = {}
    _write(root / "schema/clickhouse/catalog/catalog.yaml", yaml.safe_dump(catalog))
    errors = check(root)
    assert any("no catalog entry" in e for e in errors)


def test_missing_rule_section_fails(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    _write(root / "OpencodeAgent/skills/semantic-layer/RULES.md", "## R1. only\n")
    errors = check(root)
    assert any("missing section R5" in e for e in errors)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
