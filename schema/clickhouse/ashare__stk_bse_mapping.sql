-- ClickHouse DDL snapshot: ashare.stk_bse_mapping (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_bse_mapping
(
    `name` String,
    `o_code` String,
    `n_code` String,
    `list_date` String
)
ENGINE = MergeTree
ORDER BY (o_code, n_code)
SETTINGS index_granularity = 8192
