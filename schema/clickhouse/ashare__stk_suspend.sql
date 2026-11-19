-- ClickHouse DDL snapshot: ashare.stk_suspend (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_suspend
(
    `ts_code` String,
    `trade_date` Date,
    `suspend_timing` String,
    `suspend_type` String
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
