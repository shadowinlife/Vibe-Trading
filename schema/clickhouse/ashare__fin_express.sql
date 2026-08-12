-- ClickHouse DDL snapshot: ashare.fin_express (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_express
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `revenue` Float64,
    `operate_profit` Float64,
    `total_profit` Float64,
    `n_income` Float64,
    `total_assets` Float64,
    `total_hldr_eqy_exc_min_int` Float64,
    `diluted_eps` Float64,
    `diluted_roe` Float64,
    `yoy_net_profit` Float64,
    `bps` Float64,
    `yoy_sales` Float64,
    `yoy_op` Float64,
    `yoy_tp` Float64,
    `yoy_dedu_np` Float64,
    `yoy_eps` Float64,
    `yoy_roe` Float64,
    `growth_assets` Float64,
    `yoy_equity` Float64,
    `growth_bps` Float64,
    `or_last_year` Float64,
    `op_last_year` Float64,
    `tp_last_year` Float64,
    `np_last_year` Float64,
    `eps_last_year` Float64,
    `open_net_assets` Float64,
    `open_bps` Float64,
    `perf_summary` String,
    `is_audit` Int32,
    `remark` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date, ann_date)
SETTINGS index_granularity = 8192
