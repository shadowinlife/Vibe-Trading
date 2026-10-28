-- ClickHouse DDL snapshot: ashare.stk_moneyflow_hsgt (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_moneyflow_hsgt
(
    `trade_date` Date,
    `ggt_ss` String,
    `ggt_sz` String,
    `hgt` String,
    `sgt` String,
    `north_money` String,
    `south_money` String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY trade_date
SETTINGS index_granularity = 8192
