-- ClickHouse DDL snapshot: ashare.stk_surv (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_surv
(
    `ts_code` String,
    `name` String,
    `surv_date` Date,
    `fund_visitors` String,
    `rece_place` String,
    `rece_mode` String,
    `rece_org` String,
    `org_type` String,
    `comp_rece` String
)
ENGINE = MergeTree
ORDER BY (ts_code, surv_date)
SETTINGS index_granularity = 8192
