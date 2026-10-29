-- ClickHouse DDL snapshot: ashare.stk_moneyflow_ths (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_moneyflow_ths
(
    `trade_date` Date,
    `ts_code` String,
    `name` String,
    `pct_change` Float64,
    `latest` Float64,
    `net_amount` Float64,
    `net_d5_amount` Float64,
    `buy_lg_amount` Float64,
    `buy_lg_amount_rate` Float64,
    `buy_md_amount` Float64,
    `buy_md_amount_rate` Float64,
    `buy_sm_amount` Float64,
    `buy_sm_amount_rate` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
