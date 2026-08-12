-- ClickHouse DDL snapshot: ashare.stk_moneyflow (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_moneyflow
(
    `ts_code` String,
    `trade_date` Date,
    `buy_sm_vol` Int64,
    `buy_sm_amount` Float64,
    `sell_sm_vol` Int64,
    `sell_sm_amount` Float64,
    `buy_md_vol` Int64,
    `buy_md_amount` Float64,
    `sell_md_vol` Int64,
    `sell_md_amount` Float64,
    `buy_lg_vol` Int64,
    `buy_lg_amount` Float64,
    `sell_lg_vol` Int64,
    `sell_lg_amount` Float64,
    `buy_elg_vol` Int64,
    `buy_elg_amount` Float64,
    `sell_elg_vol` Int64,
    `sell_elg_amount` Float64,
    `net_mf_vol` Int64,
    `net_mf_amount` Float64
)
ENGINE = MergeTree
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192
