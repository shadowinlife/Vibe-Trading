-- ClickHouse DDL snapshot: ashare.stk_pledge_stat (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_pledge_stat
(
    `ts_code` String,
    `end_date` String,
    `pledge_count` Int64,
    `unrest_pledge` Float64,
    `rest_pledge` Float64,
    `total_share` Float64,
    `pledge_ratio` Float64
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
