-- ClickHouse DDL snapshot: ashare.idx_member_daily (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_member_daily
(
    `trade_date` Date,
    `ts_code` String,
    `con_code` String,
    `name` String
)
ENGINE = MergeTree
ORDER BY (trade_date, ts_code, con_code)
SETTINGS index_granularity = 8192
