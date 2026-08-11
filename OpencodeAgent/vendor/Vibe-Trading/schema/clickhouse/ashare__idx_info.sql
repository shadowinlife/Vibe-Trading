-- ClickHouse DDL snapshot: ashare.idx_info (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_info
(
    `ts_code` String,
    `name` String,
    `market` String,
    `publisher` String,
    `category` String,
    `base_date` String,
    `base_point` Float64,
    `list_date` String
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
