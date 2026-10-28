-- ClickHouse DDL snapshot: ashare.stk_ah_comparison (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_ah_comparison
(
    `hk_code` String,
    `ts_code` String,
    `trade_date` Date,
    `hk_name` String,
    `hk_pct_chg` Float64,
    `hk_close` Float64,
    `name` String,
    `close` Float64,
    `pct_chg` Float64,
    `ah_comparison` Float64,
    `ah_premium` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
