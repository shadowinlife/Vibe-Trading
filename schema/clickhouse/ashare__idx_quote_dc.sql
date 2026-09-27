-- ClickHouse DDL snapshot: ashare.idx_quote_dc (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_quote_dc
(
    `ts_code` String,
    `trade_date` Date,
    `name` String,
    `leading` String,
    `leading_code` String,
    `pct_change` Float64,
    `leading_pct` Float64,
    `total_mv` Float64,
    `turnover_rate` Float64,
    `up_num` Int64,
    `down_num` Int64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
