-- ClickHouse DDL snapshot: ashare.stk_cyq_perf (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_cyq_perf
(
    `ts_code` String,
    `trade_date` Date,
    `his_low` Float64,
    `his_high` Float64,
    `cost_5pct` Float64,
    `cost_15pct` Float64,
    `cost_50pct` Float64,
    `cost_85pct` Float64,
    `cost_95pct` Float64,
    `weight_avg` Float64,
    `winner_rate` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
