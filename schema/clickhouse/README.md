# ClickHouse Schema Snapshots (`schema/clickhouse/`)

DDL-in-repo artifacts of the ClickHouse semantic layer
(see `CLICKHOUSE_ITERATION_PLAN.md`, Phase 0 P0.1/P0.4).

## Source-of-truth relationship

| Artifact | Role |
|---|---|
| `schema/clickhouse/ashare__<table>.sql` (this directory) | **Semantic contract snapshot** — the reviewed, git-versioned column contract and the basis for the CI comment gate. One file per table, deterministic pretty-printed `CREATE TABLE`. |
| `/opt/qdata/sync/schema.py` on the production host | **Physical table creator** — creates the real tables with `CREATE TABLE IF NOT EXISTS` and feeds the daily sync. It does not read this directory (yet). |
| `schema/clickhouse/comments.yaml` | Column-comment contract (`tables.<table>.columns.<column> = comment`), enforced by `tools/ci_clickhouse_comments_gate.py`. |

The repository files are a **snapshot, not a live schema**: nothing applies
them to the database automatically. They exist so that schema drift and
missing column documentation are caught in code review and CI instead of at
query time.

Deployment facts (no credentials are stored in this repository):

- ClickHouse 24.8.14.39, database `ashare`, 56 MergeTree tables (~1279 columns).
- Public/ECS host `47.98.53.40`; VPC address `172.24.165.51:8123` (the
  default used by `agent/src/clickhouse_connector.py` via `CLICKHOUSE_HOST`
  / `CLICKHOUSE_PORT`).

## Snapshot format

Each file is byte-stable (no timestamps) and laid out as:

```sql
-- ClickHouse DDL snapshot: ashare.<table> (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.<table>
(
    `column` Type,
    ...
)
ENGINE = MergeTree
PARTITION BY ...
ORDER BY ...
SETTINGS index_granularity = 8192
```

`ENGINE` / `PARTITION BY` / `PRIMARY KEY` / `ORDER BY` / `SAMPLE BY` /
`SETTINGS` each get their own line; column definitions are preserved
verbatim from the server's `SHOW CREATE TABLE`.

## Regeneration

```bash
# From the live server (credentials via environment, same names/defaults as
# agent/src/clickhouse_connector.py: CLICKHOUSE_HOST / CLICKHOUSE_PORT /
# CLICKHOUSE_USER / CLICKHOUSE_PASSWORD / CLICKHOUSE_DATABASE):
python tools/clickhouse_export_ddl.py

# From a pre-exported JSONEachRow dump (offline, reproducible):
python tools/clickhouse_export_ddl.py --from-dump tmp/ch_dumps/ddl_dump.jsonl

# Drift check only (unified diff, exit 1 on drift — CI-friendly):
python tools/clickhouse_export_ddl.py --check
python tools/clickhouse_export_ddl.py --help
```

The exporter is idempotent: running it twice produces identical bytes.

## Drift-handling process

1. **Detect** — run `python tools/clickhouse_export_ddl.py --check`
   (or re-export and inspect `git diff`).
2. **Re-export** — run the exporter against the live database to refresh
   the snapshots.
3. **Review** — open the diff as a normal code review: confirm the change
   is intentional (new table / column / engine setting), update
   `comments.yaml` coverage if columns changed, and land it via PR.
4. **Gate** — CI fails when a covered column lacks a non-empty comment
   (`tools/ci_clickhouse_comments_gate.py`, exercised by
   `pytest tools/test_ci_clickhouse_comments_gate.py`).

Until Phase 3 (write-back of `/opt/qdata/sync/schema.py` and putting
`/opt/qdata` under git), the physical schema stays the creation authority
and this directory stays the reviewed contract snapshot — drift is resolved
by re-export + review, never by hand-editing snapshots.

## Flexibility channel (Phase 2: L2/L3 ad-hoc exploration)

The deterministic domain tools (`get_market_data`, fund-flow, margin, …) stay
the primary interface. For ad-hoc intents they cannot express, Phase 2 of the
iteration plan adds a **protected exploration channel** — three agent/MCP
tools implementing the official Catalog → Inspect → Execute discovery pattern:

| Tool | Tier | What it does |
|---|---|---|
| `ch_list_tables` | L2 catalog | Lists the 56 ashare tables with their table-level COMMENT (empty where not documented yet). |
| `ch_describe_table` | L2 inspect | One table's columns/types/COMMENTs, engine, partition/sorting keys and 2–3 sample rows. |
| `ch_query` | L3 execute | Constrained read-only SELECTs, guarded and audited. |

`ch_query` safety model (fail closed at every layer):

- **Dedicated read-only user** — connects only with the `llm_role`
  credentials (`CLICKHOUSE_LLM_USER` / `CLICKHOUSE_LLM_PASSWORD`); when they
  are unset the tool fails with an actionable error and never falls back to
  the default user. Server-side, `llm_role` is SELECT-only on `ashare.*`
  with 30s / 2GB / 1M-row / 50MB limits.
- **sqlglot AST guard** — the SQL must parse (ClickHouse dialect) as a single
  plain `SELECT`. Any DDL/DML (`INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/
  `CREATE`/`TRUNCATE`), `SYSTEM`/`SET`/`USE`/`KILL`/`ATTACH`/`DETACH`-style
  statement, `UNION`, `INTO` target, `GLOBAL IN`/`GLOBAL JOIN`, per-query
  `SETTINGS` clause, parameter placeholder, or table-valued function
  (`file()`/`url()`/`remote()`/…) is rejected; parse errors and anything the
  guard cannot fully classify are rejected too.
- **Table whitelist** — every referenced table must be one of the ashare
  tables (validated from the AST against the live table list, with the
  `ashare__*.sql` snapshot filenames in this directory as the offline
  fallback); cross-database references are rejected.
- **Forced LIMIT** — an outermost `SELECT` without `LIMIT` gets `LIMIT 500`;
  a `LIMIT` above 500 is clamped to 500; non-integer / `LIMIT ... BY` /
  `WITH TIES` forms are rejected.
- **Result cap** — results are capped at ~50KB; an oversized result is
  truncated to whole rows with an explicit truncation declaration in the
  response envelope (never silently cut).
- **Timeout** — 30-second server-side query timeout
  (`max_execution_time = 30`).
- **Custom serialization** — every cell is explicitly converted to
  Python-native types (int/float/str/None/date→ISO string), defense-in-depth
  against the official ClickHouse MCP #111 UInt64 serialization crash.
- **Audit log** — one JSON line per `ch_query` call (timestamp, sql,
  rows_returned, truncated, elapsed_ms, error) is appended to
  `~/.vibe-trading/logs/ch_query_audit.jsonl`; an audit-write failure never
  breaks the query itself.

Guard/serializer implementation: `agent/src/tools/clickhouse_query_guard.py`;
tools: `agent/src/tools/clickhouse_explore_tools.py`,
`agent/src/tools/clickhouse_query_tool.py`.

## Upstream (tushare) structure-drift governance

tushare occasionally changes upstream table structure (new/removed columns,
endpoint contract changes). ClickHouse must stay consistent with tushare;
the pipeline enforces this at runtime and the repository closes the loop:

1. **Runtime detection + controlled auto-extension** (sync engine,
   `/opt/qdata/sync/clickhouse/engine.py::_apply_upstream_drift`): on every
   non-empty fetch the engine compares tushare's DataFrame columns with the
   live ClickHouse columns.
   - **New upstream column** → auto `ALTER TABLE ... ADD COLUMN IF NOT
     EXISTS` as a `Nullable` type inferred from the data
     (`Float64`/`Int64`/`String`). Nullable keeps historical rows valid and
     makes the change reversible (`ALTER TABLE ... DROP COLUMN`).
   - **Column disappeared upstream** → logged only; never auto-dropped
     (removals need human review — they may be transient API glitches).
   - **Every event is audited** in the `ashare.schema_drift_log` table
     (`event_time`, `target_table`, `event_type` ∈ `column_added` /
     `column_add_failed` / `column_missing_in_source`, `column_name`,
     `detail`) and echoed to the daily sync log with a `[DRIFT]` prefix.
2. **Repository follow-up** (mandatory after any drift event):
   - `SELECT * FROM ashare.schema_drift_log ORDER BY event_time DESC` —
     review new events;
   - re-export snapshots (`python tools/clickhouse_export_ddl.py`) and
     commit the diff;
   - extend `comments.yaml` for every new column of a covered table — the
     CI gate fails until the new column is documented;
   - for `column_missing_in_source`: confirm against tushare's official
     documentation before any schema action.
3. **API-contract drift** (calling convention, not columns): when a tushare
   endpoint changes its input contract (e.g. `index_weight` rejecting
   `limit`/`offset` bursts transiently, or parameters becoming mandatory —
   see doc_id=96), the engine's page-level retry with backoff
   (`_query_with_retry`) absorbs transient rejections; persistent failures
   stay visible as error dimensions in `table_sync_state` and must be
   diagnosed against the official interface documentation before the
   registry/engine calling convention is changed.
