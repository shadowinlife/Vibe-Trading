-- ClickHouse DDL snapshot: ashare.stk_hk_hold (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_hk_hold
(
    `code` String,
    `trade_date` Date,
    `ts_code` String,
    `name` String,
    `vol` Int64,
    `ratio` Float64,
    `exchange` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (code, trade_date)
SETTINGS index_granularity = 8192
