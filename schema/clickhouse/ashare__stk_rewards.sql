-- ClickHouse DDL snapshot: ashare.stk_rewards (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_rewards
(
    `ts_code` String,
    `ann_date` String,
    `end_date` String,
    `name` String,
    `title` String,
    `reward` Float64,
    `hold_vol` Float64
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
