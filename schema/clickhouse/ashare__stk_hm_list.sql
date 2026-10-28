-- ClickHouse DDL snapshot: ashare.stk_hm_list (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.stk_hm_list
(
    `name` String,
    `desc` String,
    `orgs` String
)
ENGINE = MergeTree
ORDER BY name
SETTINGS index_granularity = 8192
