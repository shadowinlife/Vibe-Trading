-- ClickHouse DDL snapshot: ashare.stk_limit_list_d (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_limit_list_d
(
    `trade_date` Date,
    `ts_code` String,
    `industry` String,
    `name` String,
    `close` Float64,
    `pct_chg` Float64,
    `amount` Float64,
    `limit_amount` Float64,
    `float_mv` Float64,
    `total_mv` Float64,
    `turnover_ratio` Float64,
    `fd_amount` Float64,
    `first_time` String,
    `last_time` String,
    `open_times` Int64,
    `up_stat` String,
    `limit_times` Float64,
    `limit` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
