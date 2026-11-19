-- ClickHouse DDL snapshot: ashare.stk_holdertrade (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_holdertrade
(
    `ts_code` String,
    `ann_date` String,
    `holder_name` String,
    `holder_type` String,
    `in_de` String,
    `change_vol` Float64,
    `change_ratio` Float64,
    `after_share` Float64,
    `after_ratio` Float64,
    `avg_price` Float64,
    `total_share` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date)
SETTINGS index_granularity = 8192
