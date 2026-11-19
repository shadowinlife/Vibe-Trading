-- ClickHouse DDL snapshot: ashare.stk_share_float (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_share_float
(
    `ts_code` String,
    `ann_date` String,
    `float_date` String,
    `float_share` Float64,
    `float_ratio` Float64,
    `holder_name` String,
    `share_type` String
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date, float_date, float_share)
SETTINGS index_granularity = 8192
