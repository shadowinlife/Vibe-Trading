-- ClickHouse DDL snapshot: ashare.stk_pledge_detail (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_pledge_detail
(
    `ts_code` String,
    `ann_date` String,
    `holder_name` String,
    `pledge_amount` Float64,
    `start_date` String,
    `end_date` String,
    `is_release` String,
    `release_date` String,
    `pledgor` String,
    `holding_amount` Float64,
    `pledged_amount` Float64,
    `p_total_ratio` Float64,
    `h_total_ratio` Float64,
    `is_buyback` String
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
