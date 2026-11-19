-- ClickHouse DDL snapshot: ashare.stk_margin (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_margin
(
    `ts_code` String,
    `trade_date` Date,
    `name` String,
    `rzye` Float64,
    `rqye` Float64,
    `rzmre` Float64,
    `rqyl` Float64,
    `rzche` Float64,
    `rqchl` Float64,
    `rqmcl` Float64,
    `rzrqye` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
