-- ClickHouse DDL snapshot: ashare.stk_repurchase (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_repurchase
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `proc` String,
    `exp_date` String,
    `vol` Float64,
    `amount` Float64,
    `high_limit` Float64,
    `low_limit` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date)
SETTINGS index_granularity = 8192
