---
name: data-warehouse
description: |
  ClickHouse 数据仓库查询工具。Use when: 查询数据仓库、执行 SQL、列出表、ClickHouse 查询、数据仓库查询。
  Provides `query_warehouse(sql)` and `list_tables()` tools.
argument-hint: "SQL 查询语句 或 --list-tables"
user-invocable: true
---

# Data Warehouse Skill

Use this skill to query the ClickHouse data warehouse for analytics, market data, and financial indicators.

> **表名与口径先查语义层目录**：`../semantic-layer/INDEX.md`（57 张表逐表说明）与 `../semantic-layer/RULES.md`（空结果、ETF↔指数、估值降级、过期标注）。
> 本 skill 只负责执行 SQL；不知道用哪张表时不要猜。

## Tools

### `query_warehouse(sql)`

Execute an arbitrary SQL query against the ClickHouse data warehouse and return results as JSON.

**Usage:**
```bash
python /workspace/.opencode/skills/data-warehouse/query.py --sql "SELECT ts_code, trade_date, close, pe_ttm FROM stk_factor_pro WHERE ts_code='000001.SZ' ORDER BY trade_date DESC LIMIT 10"
```

**Output:** JSON array of objects with column-name keys.

### `list_tables()`

List all available tables in the ClickHouse database with their schema information.

**Usage:**
```bash
python /workspace/.opencode/skills/data-warehouse/query.py --list-tables
```

**Output:** JSON array of table names and column definitions.

## Environment Variables

Configure ClickHouse connection via environment variables (never hardcode credentials):

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `CLICKHOUSE_HOST` | Yes | — | ClickHouse server hostname |
| `CLICKHOUSE_PORT` | Yes | `8123` | HTTP port |
| `CLICKHOUSE_DATABASE` | Yes | `ashare` | Database name |
| `CLICKHOUSE_USER` | Yes | `default` | Username |
| `CLICKHOUSE_PASSWORD` | No | — | Password (omit for no-auth setups) |

## Degradation Path

If ClickHouse is unreachable (CLICKHOUSE_HOST not set or connection fails):
1. `list_tables()` returns `{"available": false, "reason": "CLICKHOUSE_HOST not configured"}`
2. `query_warehouse()` returns `{"available": false, "reason": "..."}`
3. Agent should fall back to vibe-trading MCP tools (`get_market_data`, `get_financial_statements`, etc.)

## Important Notes

- Always use this skill FIRST for any A-share data query before falling back to vibe-trading MCP tools
- The data warehouse is T-1 history; the widest table (`stk_factor_pro`) has 261 columns. Do not assume one table covers every asset class: stocks live in `stk_factor_pro`, ETFs in `fund_daily`, indices in `idx_factor_pro`
- For real-time/current-day data, use vibe-trading MCP tools directly
- SQL queries should include appropriate LIMIT clauses for large result sets