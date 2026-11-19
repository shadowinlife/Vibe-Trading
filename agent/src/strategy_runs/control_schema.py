"""Control-plane DDL shared by the existing strategy-run database.

This module deliberately has no imports. Storage and service wiring both load
this exact vocabulary, keeping their schema contract identical.
"""

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_dispatches (
    dispatch_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('backtest', 'factor')),
    remote_id TEXT NOT NULL UNIQUE,
    declared_digest TEXT NOT NULL,
    declaration_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'dispatched', 'completed', 'failed', 'cancelled'
    )),
    input_digest TEXT,
    config_sha256 TEXT,
    source_sha256 TEXT,
    run_dir TEXT,
    result_path TEXT,
    error_kind TEXT,
    worker_pid INTEGER,
    worker_pgid INTEGER,
    worker_meta_json TEXT,
    started_at INTEGER,
    terminal_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    contract_version TEXT NOT NULL,
    start_declaration_json TEXT,
    start_digest TEXT,
    CHECK(
        (status = 'dispatched' AND terminal_at IS NULL AND error_kind IS NULL)
        OR (status IN ('completed', 'cancelled')
            AND terminal_at IS NOT NULL AND error_kind IS NULL)
        OR (status = 'failed' AND terminal_at IS NOT NULL
            AND error_kind IS NOT NULL AND length(error_kind) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_execution_dispatches_remote_id
    ON execution_dispatches(remote_id);

CREATE TABLE IF NOT EXISTS execution_stops (
    stop_key TEXT PRIMARY KEY,
    dispatch_key TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('requested', 'unknown', 'confirmed')),
    evidence_kind TEXT,
    evidence_json TEXT,
    requested_at INTEGER NOT NULL,
    resolved_at INTEGER,
    deadline_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    contract_version TEXT NOT NULL,
    FOREIGN KEY(dispatch_key) REFERENCES execution_dispatches(dispatch_key),
    CHECK(
        (state = 'requested' AND resolved_at IS NULL
            AND evidence_kind IS NULL AND evidence_json IS NULL)
        OR (state = 'unknown' AND resolved_at IS NOT NULL
            AND evidence_kind IS NULL AND evidence_json IS NULL)
        OR (state = 'confirmed' AND resolved_at IS NOT NULL
            AND evidence_kind IN (
                'process_exit', 'terminal_record', 'process_absent', 'ttl_expired', 'explicit_absence'
            )
            AND evidence_json IS NOT NULL AND length(evidence_json) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_execution_stops_dispatch_remote
    ON execution_stops(dispatch_key, remote_id);
"""
