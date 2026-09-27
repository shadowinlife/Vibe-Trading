-- ClickHouse DDL snapshot: ashare.idx_sw_member_all (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.idx_sw_member_all
(
    `l1_code` String,
    `l1_name` String,
    `l2_code` String,
    `l2_name` String,
    `l3_code` String,
    `l3_name` String,
    `ts_code` String,
    `name` String,
    `in_date` String,
    `out_date` Int32,
    `is_new` String
)
ENGINE = MergeTree
ORDER BY (l1_code, l2_code, l3_code, ts_code, in_date, out_date, is_new)
SETTINGS index_granularity = 8192
