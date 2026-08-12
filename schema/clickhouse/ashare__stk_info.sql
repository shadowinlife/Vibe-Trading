-- ClickHouse DDL snapshot: ashare.stk_info (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_info
(
    `ts_code` String,
    `symbol` String,
    `name` String,
    `area` String,
    `industry` String,
    `cnspell` String,
    `market` String,
    `list_date` String,
    `act_name` String,
    `act_ent_type` String
)
ENGINE = MergeTree
ORDER BY ts_code
SETTINGS index_granularity = 8192
