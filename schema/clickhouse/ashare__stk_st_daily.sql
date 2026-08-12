-- ClickHouse DDL snapshot: ashare.stk_st_daily (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_st_daily
(
    `ts_code` String,
    `name` String,
    `trade_date` Date,
    `type` String,
    `type_name` String
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
