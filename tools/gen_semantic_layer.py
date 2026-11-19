#!/usr/bin/env python3
"""Render the agent-facing ClickHouse semantic layer.

Inputs (source of truth):
  - schema/clickhouse/metadata.json      live table/column snapshot (types + comments)
  - schema/clickhouse/catalog/catalog.yaml  table-level semantics
  - schema/clickhouse/catalog/rules.md   behaviour contract (copied verbatim)
  - schema/clickhouse/catalog/skill.md   skill entry (copied verbatim)

Outputs (generated, do not edit by hand):
  - OpencodeAgent/skills/semantic-layer/SKILL.md
  - OpencodeAgent/skills/semantic-layer/RULES.md
  - OpencodeAgent/skills/semantic-layer/INDEX.md
  - OpencodeAgent/skills/semantic-layer/tables/<table>.md
  - OpencodeAgent/skills/semantic-layer/.manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA = REPO_ROOT / "schema/clickhouse/metadata.json"
DEFAULT_CATALOG = REPO_ROOT / "schema/clickhouse/catalog/catalog.yaml"
DEFAULT_RULES = REPO_ROOT / "schema/clickhouse/catalog/rules.md"
DEFAULT_SKILL = REPO_ROOT / "schema/clickhouse/catalog/skill.md"
DEFAULT_OUT = REPO_ROOT / "OpencodeAgent/skills/semantic-layer"

STATUS_LABEL = {
    "ok": "ok",
    "stale": "⚠ 停更",
    "empty": "⚠ 空表",
    "excluded": "⛔ 已排除",
    "snapshot": "快照",
    "unknown": "水位未知",
}


def load_inputs(
    metadata_path: Path, catalog_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or "tables" not in catalog:
        raise SystemExit(f"catalog is not a mapping with 'tables': {catalog_path}")
    return metadata, catalog


def table_status(table: dict[str, Any], entry: dict[str, Any], asof: date) -> str:
    if entry.get("status") == "excluded":
        return "excluded"
    if not table.get("total_rows"):
        return "empty"
    if not table.get("max_date"):
        return (
            "unknown"
            if entry.get("date_column") or table.get("date_column")
            else "snapshot"
        )
    try:
        latest = date.fromisoformat(str(table["max_date"]))
    except ValueError:
        return "stale"
    sla = int(entry.get("freshness_sla_days", 1))
    return "ok" if (asof - latest).days <= sla else "stale"


def lag_days(table: dict[str, Any], asof: date) -> int | None:
    if not table.get("max_date"):
        return None
    try:
        return (asof - date.fromisoformat(str(table["max_date"]))).days
    except ValueError:
        return None


def render_meta_table(
    name: str, table: dict[str, Any], entry: dict[str, Any], asof: date
) -> str:
    status = table_status(table, entry, asof)
    lag = lag_days(table, asof)
    date_col = entry.get("date_column", table.get("date_column"))
    date_type = None
    if date_col:
        date_type = (
            table.get("date_type") if table.get("date_column") == date_col else None
        )
        if not date_type:
            date_type = next(
                (
                    c.get("type")
                    for c in table.get("columns", [])
                    if c.get("name") == date_col
                ),
                None,
            )
    date_cell = f"`{date_col}`" if date_col else "—（快照/无日期列）"
    if date_col and date_type:
        date_cell += f"（{date_type}）"
        if date_type.lower().startswith("string"):
            date_cell += " ⚠ String，过滤需 `toDate()`"
    rows = [
        ("领域", f"`{entry.get('domain', '—')}`"),
        ("资产类别", f"`{entry.get('asset_class', 'stock')}`"),
        ("粒度", str(entry.get("grain", "—"))),
        ("主键", ", ".join(f"`{c}`" for c in entry.get("primary_key", [])) or "—"),
        ("日期列", date_cell),
        (
            "数据水位",
            f"{table.get('max_date') or '—'}"
            + (f"（落后 {lag} 自然日）" if lag is not None else ""),
        ),
        ("新鲜度 SLA", f"{entry.get('freshness_sla_days', 1)} 天"),
        ("权威行情表", "是" if entry.get("authoritative") else "否"),
        (
            "可估值",
            {True: "是", False: "否", "derived": "派生（需披露口径）"}.get(
                entry.get("valuation", False), "否"
            ),
        ),
        ("状态", STATUS_LABEL.get(status, status)),
        ("行数", f"{table.get('total_rows', 0):,}"),
        ("列数", str(len(table.get("columns", [])))),
    ]
    lines = ["| 项 | 值 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)


def render_columns(table: dict[str, Any]) -> str:
    columns = table.get("columns", [])
    commented = sum(1 for c in columns if c.get("comment"))
    lines = [
        f"## 列清单（{len(columns)} 列，有注释 {commented} 列）",
        "",
        "| # | 列 | 类型 | 注释 |",
        "|---:|---|---|---|",
    ]
    for col in columns:
        comment = (col.get("comment") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {col.get('position')} | `{col.get('name')}` | `{col.get('type')}` | {comment or '—'} |"
        )
    if commented == 0:
        lines += [
            "",
            "> 本表列注释未登记。单位/口径以 `RULES.md` R7 为准，仍不确定时在结论中标注「单位未登记」。",
        ]
    return "\n".join(lines)


def render_table_doc(
    name: str, table: dict[str, Any], entry: dict[str, Any], asof: date
) -> str:
    parts = [f"# {name}", "", str(entry.get("use", "")) or "（未登记用途）", ""]
    parts.append(render_meta_table(name, table, entry, asof))
    units = entry.get("units") or {}
    if units:
        parts += ["", "## 单位", "", "| 列 | 单位 |", "|---|---|"]
        parts += [f"| `{c}` | {u} |" for c, u in units.items()]
    caveats = entry.get("caveats") or []
    if caveats:
        parts += ["", "## 注意事项", ""]
        parts += [f"- {c}" for c in caveats]
    examples = entry.get("examples") or []
    if examples:
        parts += ["", "## 示例", ""]
        for sql in examples:
            parts += ["```sql", str(sql).strip(), "```", ""]
    related = entry.get("related") or []
    if related:
        links = ", ".join(f"[`{r}`]({r}.md)" for r in related if r != name)
        parts += ["", f"## 相关表\n\n{links}"]
    parts += ["", render_columns(table)]
    return "\n".join(parts).rstrip() + "\n"
    return "\n".join(parts)


def render_index(metadata: dict[str, Any], catalog: dict[str, Any], asof: date) -> str:
    tables = {t["name"]: t for t in metadata["tables"]}
    domains: dict[str, list[str]] = OrderedDict(
        (key, []) for key in (catalog.get("domains") or {})
    )
    for name in sorted(tables):
        domain = str((catalog["tables"].get(name) or {}).get("domain", "ops"))
        domains.setdefault(domain, []).append(name)

    total_cols = sum(len(t.get("columns", [])) for t in tables.values())
    commented = sum(
        1 for t in tables.values() for c in t.get("columns", []) if c.get("comment")
    )
    stale = [
        n
        for n, t in tables.items()
        if table_status(t, catalog["tables"].get(n) or {}, asof) == "stale"
    ]

    out = [
        "# ClickHouse A 股数据目录（ashare）",
        "",
        "> 自动生成，请勿手改。来源：`schema/clickhouse/metadata.json` + `catalog/catalog.yaml`。",
        f"> 快照日期 {metadata.get('exported_at', asof.isoformat())} · "
        f"{len(tables)} 张表 · {total_cols} 列（有注释 {commented}）",
        f"> 其中 {len(stale)} 张表落后于各自的 SLA，已在表中标「⚠ 停更」。",
        "",
        "## 选表速查",
        "",
        "| 你要查什么 | 用哪张表 |",
        "|---|---|",
        "| 个股日行情 / 估值 / 复权价 / 技术指标 | `stk_factor_pro` |",
        "| ETF / LOF 日行情 | `fund_daily` |",
        "| 指数日行情 / 技术指标 | `idx_factor_pro` |",
        "| 指数估值（PE/PB） | `idx_dailybasic`（仅 12 个代码且已停更，先读 RULES R3） |",
        "| 指数成分与权重 | `idx_weight`（月度快照，非全成分） |",
        "| 板块行情（BK 代码） | `idx_daily_dc` / `idx_quote_dc` |",
        "| 个股基础信息 / 代码解析 | `stk_info`；指数用 `idx_info` |",
        "| 交易日历 / 应有最新交易日 | `trade_calendar` |",
        "| 同步失败 / 是否脱管 | `table_sync_state` |",
        "",
        "> 取数前若涉及 ETF↔指数、估值降级、空结果、范围过大，**必须先读 [RULES.md](RULES.md)**。",
        "",
    ]
    for domain, names in domains.items():
        if not names:
            continue
        label = (catalog.get("domains") or {}).get(domain, domain)
        out += [
            f"## {domain} — {label}",
            "",
            "| 表 | 粒度 | 水位 | 状态 | 用途 | 行数 |",
            "|---|---|---|---|---|---:|",
        ]
        for name in names:
            t = tables[name]
            e = catalog["tables"].get(name) or {}
            status = table_status(t, e, asof)
            use = str(e.get("use", "—")).replace("|", "\\|")
            out.append(
                f"| [`{name}`](tables/{name}.md) | {e.get('grain', '—')} | "
                f"{t.get('max_date') or '—'} | {STATUS_LABEL.get(status, status)} | {use} | "
                f"{t.get('total_rows', 0):,} |"
            )
        out.append("")
    return "\n".join(out)


def stable_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--asof",
        type=date.fromisoformat,
        default=date.today(),
        help="水位判定的基准日（默认今天），用于可重复生成",
    )
    args = parser.parse_args()

    metadata, catalog = load_inputs(args.metadata, args.catalog)
    asof = args.asof
    out: Path = args.out
    tables_dir = out / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(args.skill, out / "SKILL.md")
    shutil.copyfile(args.rules, out / "RULES.md")

    by_name = {t["name"]: t for t in metadata["tables"]}
    for name, table in by_name.items():
        entry = catalog["tables"].get(name) or {}
        (tables_dir / f"{name}.md").write_text(
            render_table_doc(name, table, entry, asof), encoding="utf-8"
        )
    (out / "INDEX.md").write_text(
        render_index(metadata, catalog, asof), encoding="utf-8"
    )

    manifest = {
        "generated_for": metadata.get("exported_at", asof.isoformat()),
        "asof": asof.isoformat(),
        "tables": len(by_name),
        "columns": sum(len(t.get("columns", [])) for t in by_name.values()),
        "catalog_sha": stable_hash(args.catalog),
        "metadata_sha": stable_hash(args.metadata),
        "rules_sha": stable_hash(args.rules),
        "skill_sha": stable_hash(args.skill),
    }
    (out / ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"rendered {len(by_name)} tables -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
