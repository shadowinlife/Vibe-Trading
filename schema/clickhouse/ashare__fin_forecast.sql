-- ClickHouse DDL snapshot: ashare.fin_forecast (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_forecast
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `type` String,
    `p_change_min` Float64,
    `p_change_max` Float64,
    `net_profit_min` Float64,
    `net_profit_max` Float64,
    `last_parent_net` Float64,
    `first_ann_date` Date,
    `summary` String,
    `change_reason` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date, ann_date)
SETTINGS index_granularity = 8192
