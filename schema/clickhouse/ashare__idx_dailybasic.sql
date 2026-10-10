-- ClickHouse DDL snapshot: ashare.idx_dailybasic (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_dailybasic
(
    `ts_code` String,
    `trade_date` Date,
    `total_mv` Float64,
    `float_mv` Float64,
    `total_share` Float64,
    `float_share` Float64,
    `free_share` Float64,
    `turnover_rate` Float64,
    `turnover_rate_f` Float64,
    `pe` Float64,
    `pe_ttm` Float64,
    `pb` Float64
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
