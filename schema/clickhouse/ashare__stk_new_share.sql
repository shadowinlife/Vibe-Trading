-- ClickHouse DDL snapshot: ashare.stk_new_share (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_new_share
(
    `ts_code` String,
    `sub_code` Int64,
    `name` String,
    `ipo_date` Date,
    `issue_date` Date,
    `amount` Float64,
    `market_amount` Float64,
    `price` Float64,
    `pe` Float64,
    `limit_amount` Float64,
    `funds` Float64,
    `ballot` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, sub_code)
SETTINGS index_granularity = 8192
