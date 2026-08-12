-- ClickHouse DDL snapshot: ashare.fin_audit (exported by tools/clickhouse_export_ddl.py)
CREATE TABLE ashare.fin_audit
(
    `ts_code` String,
    `ann_date` Date,
    `end_date` Date,
    `audit_result` String,
    `audit_fees` Float64,
    `audit_agency` String,
    `audit_sign` String
)
ENGINE = MergeTree
ORDER BY (ts_code, end_date)
SETTINGS index_granularity = 8192
