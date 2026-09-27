-- ClickHouse DDL snapshot: ashare.stk_top_inst (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_top_inst
(
    `trade_date` Date,
    `ts_code` String,
    `exalter` String,
    `buy` Float64,
    `buy_rate` Float64,
    `sell` Float64,
    `sell_rate` Float64,
    `net_buy` Float64,
    `side` String,
    `reason` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
