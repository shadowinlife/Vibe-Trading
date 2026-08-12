-- ClickHouse DDL snapshot: ashare.stk_block_trade (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_block_trade
(
    `ts_code` String,
    `trade_date` Date,
    `price` Float64,
    `vol` Float64,
    `amount` Float64,
    `buyer` String,
    `seller` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
