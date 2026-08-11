-- ClickHouse DDL snapshot: ashare.fin_mainbz (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_mainbz
(
    `ts_code` String,
    `end_date` String,
    `bz_item` String,
    `bz_code` String,
    `bz_sales` Float64,
    `bz_profit` Float64,
    `bz_cost` Float64,
    `curr_type` String,
    `_param_type` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date)
SETTINGS index_granularity = 8192
