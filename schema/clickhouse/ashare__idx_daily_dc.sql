-- ClickHouse DDL snapshot: ashare.idx_daily_dc (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_daily_dc
(
    `ts_code` String,
    `trade_date` Date,
    `close` Float64,
    `open` Float64,
    `high` Float64,
    `low` Float64,
    `change` Float64,
    `pct_change` Float64,
    `vol` Float64,
    `amount` Float64,
    `swing` Float64,
    `turnover_rate` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
