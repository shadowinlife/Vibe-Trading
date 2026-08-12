-- ClickHouse DDL snapshot: ashare.fin_top10_float_holders (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_top10_float_holders
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `holder_name` String,
    `hold_amount` Float64,
    `hold_ratio` Float64,
    `hold_float_ratio` Float64,
    `hold_change` Float64,
    `holder_type` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date, holder_name)
SETTINGS index_granularity = 8192
