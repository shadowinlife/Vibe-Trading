-- ClickHouse DDL snapshot: ashare.stk_managers (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_managers
(
    `ts_code` String,
    `ann_date` String,
    `name` String,
    `gender` String,
    `lev` String,
    `title` String,
    `edu` String,
    `national` String,
    `birthday` String,
    `begin_date` String,
    `end_date` String
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
