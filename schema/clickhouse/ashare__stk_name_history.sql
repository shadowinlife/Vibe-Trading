-- ClickHouse DDL snapshot: ashare.stk_name_history (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_name_history
(
    `ts_code` String,
    `name` String,
    `start_date` String,
    `end_date` String,
    `ann_date` String,
    `change_reason` String
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date)
SETTINGS index_granularity = 8192
