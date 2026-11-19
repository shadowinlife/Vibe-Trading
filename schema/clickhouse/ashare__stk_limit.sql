-- ClickHouse DDL snapshot: ashare.stk_limit (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_limit
(
    `trade_date` Date,
    `ts_code` String,
    `up_limit` Float64,
    `down_limit` Float64
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
