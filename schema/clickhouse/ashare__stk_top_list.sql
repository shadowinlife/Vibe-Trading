-- ClickHouse DDL snapshot: ashare.stk_top_list (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_top_list
(
    `trade_date` Date,
    `ts_code` String,
    `name` String,
    `close` Float64,
    `pct_change` Float64,
    `turnover_rate` Float64,
    `amount` Float64,
    `l_sell` Float64,
    `l_buy` Float64,
    `l_amount` Float64,
    `net_amount` Float64,
    `net_rate` Float64,
    `amount_rate` Float64,
    `float_values` Float64,
    `reason` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
