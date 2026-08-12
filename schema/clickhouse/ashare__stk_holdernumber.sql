-- ClickHouse DDL snapshot: ashare.stk_holdernumber (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_holdernumber
(
    `ts_code` String,
    `ann_date` String,
    `end_date` String,
    `holder_num` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, ann_date)
SETTINGS index_granularity = 8192
