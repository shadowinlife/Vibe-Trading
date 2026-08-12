-- ClickHouse DDL snapshot: ashare.stk_cyq_chips (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_cyq_chips
(
    `ts_code` String,
    `trade_date` Date,
    `price` Float64,
    `percent` Float64
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
