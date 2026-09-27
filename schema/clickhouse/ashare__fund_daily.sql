-- ClickHouse DDL snapshot: ashare.fund_daily (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fund_daily
(
    `ts_code` String,
    `trade_date` String,
    `pre_close` Float64,
    `open` Float64,
    `high` Float64,
    `low` Float64,
    `close` Float64,
    `change` Float64,
    `pct_chg` Float64,
    `vol` Float64,
    `amount` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
