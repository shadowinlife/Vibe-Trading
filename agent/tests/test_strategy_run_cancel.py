"""VT-STRAT-0002 cancellation and additive legacy-schema migration."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.strategy_run_routes import register_strategy_run_routes
from src.scheduled_research.models import ScheduledResearchJob
from src.strategy_library import execute_code
from src.strategy_library.paths import version_code_path, version_config_path
from src.strategy_runs import service as service_module
from src.strategy_runs.service import StrategyRunService
from src.strategy_runs.store import StrategyRunStore

NOW = 1_700_000_000_000
TOKEN = "dummy-service-token-cancel-0002"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

_LEGACY_DDL = """
CREATE TABLE strategy_runs (
    vt_run_id TEXT PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    error TEXT,
    error_kind TEXT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    sandbox_id TEXT,
    upstream TEXT,
    timeout_s INTEGER NOT NULL,
    handle_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_strategy_runs_status ON strategy_runs(status);
CREATE INDEX idx_strategy_runs_created_at ON strategy_runs(created_at);
"""
_LEGACY_ROWS = [
    (
        "vt-running",
        "  key:雪  ",
        "running",
        None,
        None,
        "momentum-1",
        "v1-aaaaaaaa",
        "sandbox-1",
        "https://rm.example",
        900,
        '{"run_id":"opaque"}',
        10,
        11,
    ),
    (
        "vt-failed",
        "key-2",
        "failed",
        "boom",
        "timeout",
        "momentum-1",
        "v1-aaaaaaaa",
        None,
        None,
        600,
        None,
        20,
        21,
    ),
]
_INSERT_LEGACY = """
INSERT INTO strategy_runs (
    vt_run_id, run_key, status, error, error_kind, strategy_id,
    strategy_version, sandbox_id, upstream, timeout_s, handle_json,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@pytest.fixture(autouse=True)
def _token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "service-token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(path))


def _client(service: StrategyRunService) -> TestClient:
    app = FastAPI()
    register_strategy_run_routes(app, lambda: service)
    return TestClient(app)


def _service(store: StrategyRunStore) -> StrategyRunService:
    return StrategyRunService(
        store=store,
        runner=SimpleNamespace(),
        strategy_lookup=lambda _strategy_id: None,
        version_checker=lambda *_args: None,
        now_ms=lambda: NOW + 100,
    )


def _start_body(run_key: str = "user:schedule:slot:0") -> dict[str, object]:
    return {
        "strategy_id": "momentum-1",
        "strategy_version": "v1-aaaaaaaa",
        "run_key": run_key,
        "sandbox_id": "default--box",
        "timeout_s": 900,
    }


def _claim_with_handle(
    store: StrategyRunStore, run_key: str, kind: str, run_id: str
) -> str:
    claim = store.claim(
        run_key, "momentum-1", "v1-aaaaaaaa", None, None, 900, now_ms=NOW
    )
    handle = {
        "run_id": run_id,
        "kind": kind,
        "session_id": "session-1" if kind == "workflow" else None,
        "run_dir": None if kind == "workflow" else "/runs/code",
        "started_at": NOW,
    }
    assert store.set_handle(claim.row.vt_run_id, json.dumps(handle), now_ms=NOW + 1)
    return claim.row.vt_run_id


def _write_legacy_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(_LEGACY_DDL)
        conn.executemany(_INSERT_LEGACY, _LEGACY_ROWS)
        conn.execute(
            "CREATE INDEX idx_legacy_custom ON strategy_runs(strategy_id, created_at)"
        )


def _raw_rows_and_indexes(path: Path) -> tuple[list[tuple], set[str]]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT * FROM strategy_runs ORDER BY vt_run_id").fetchall()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='strategy_runs'"
            )
        }
    return rows, indexes


def _job() -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id="cancel-job",
        prompt="",
        schedule="60000",
        source_type="strategy",
        executor_kind="strategy",
        strategy_id="momentum-1",
        strategy_version="v1-aaaaaaaa",
    )


def _write_snapshot(root: Path) -> None:
    config = version_config_path("momentum-1", "v1-aaaaaaaa", runtime_root=root)
    code = version_code_path("momentum-1", "v1-aaaaaaaa", runtime_root=root)
    config.parent.mkdir(parents=True, exist_ok=True)
    code.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("{}", encoding="utf-8")
    code.write_text("# frozen test snapshot\n", encoding="utf-8")


def _spawn_sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.wait(timeout=5)


def _group_file(run_dir: str | None) -> Path:
    path = Path(run_dir or "") / "group.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return path
        time.sleep(0.01)
    pytest.fail(f"code child did not publish its process group: {path}")


def _cancel_child_main() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(sys.argv[1], "group.json").write_text(
        json.dumps(
            {"pid": os.getpid(), "pgid": os.getpgrp(), "grandchild": grandchild.pid}
        ),
        encoding="utf-8",
    )
    signal.pause()


if __name__ == "__main__":
    _cancel_child_main()


def test_legacy_migration_preserves_rows_indexes_and_run_key_unique(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    _write_legacy_db(db_path)
    before, indexes_before = _raw_rows_and_indexes(db_path)

    store = StrategyRunStore(db_path)

    after, indexes_after = _raw_rows_and_indexes(db_path)
    assert after == before
    assert indexes_after == indexes_before
    store.ensure_schema()
    assert _raw_rows_and_indexes(db_path)[0] == before
    with sqlite3.connect(db_path) as conn:
        cancelled = (
            "vt-cancelled",
            "key-3",
            "cancelled",
            None,
            None,
            "momentum-1",
            "v1",
            None,
            None,
            900,
            None,
            30,
            31,
        )
        conn.execute(_INSERT_LEGACY, cancelled)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                _INSERT_LEGACY,
                (
                    "vt-duplicate",
                    "key-3",
                    "cancelled",
                    None,
                    None,
                    "momentum-1",
                    "v1",
                    None,
                    None,
                    900,
                    None,
                    32,
                    33,
                ),
            )


def test_legacy_migration_is_concurrency_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.sqlite3"
    _write_legacy_db(db_path)
    before, indexes_before = _raw_rows_and_indexes(db_path)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(
            pool.map(lambda _index: StrategyRunStore(db_path).ensure_schema(), range(6))
        )

    assert _raw_rows_and_indexes(db_path) == (before, indexes_before)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            _INSERT_LEGACY,
            (
                "vt-cancelled",
                "key-3",
                "cancelled",
                None,
                None,
                "momentum-1",
                "v1",
                None,
                None,
                900,
                None,
                30,
                31,
            ),
        )


def test_code_start_registers_pid_pgid_and_a_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    _write_snapshot(root)
    calls: list[dict] = []

    def fake_popen(_argv, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(pid=43210)

    monkeypatch.setattr(execute_code.subprocess, "Popen", fake_popen)
    handle = execute_code.start_code_run(_job(), runtime_root=root)
    try:
        assert calls[0]["start_new_session"] is True
        assert execute_code.PROCESS_REGISTRY[handle.run_id] == (43210, 43210)
        assert execute_code.PID_REGISTRY[handle.run_id] == 43210
        assert set(handle.to_in_flight()) == {
            "run_id",
            "kind",
            "session_id",
            "run_dir",
            "started_at",
        }
    finally:
        execute_code.PROCESS_REGISTRY.pop(handle.run_id, None)
        execute_code.PID_REGISTRY.pop(handle.run_id, None)


def test_real_code_cancel_kills_one_group_and_replays_terminal_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    _write_snapshot(root)
    monkeypatch.setattr(execute_code, "ENTRY_MODULE", "tests.test_strategy_run_cancel")
    handle = execute_code.start_code_run(_job(), runtime_root=root)
    group = json.loads(_group_file(handle.run_dir).read_text(encoding="utf-8"))
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    claim = store.claim(
        "real-code", "momentum-1", "v1-aaaaaaaa", None, None, 900, now_ms=NOW
    )
    assert store.set_handle(
        claim.row.vt_run_id, json.dumps(handle.to_in_flight()), now_ms=NOW + 1
    )
    signals: list[int] = []
    real_killpg = os.killpg

    def recording_killpg(pgid: int, sig: int) -> None:
        if sig in (signal.SIGTERM, signal.SIGKILL):
            signals.append(sig)
        real_killpg(pgid, sig)

    monkeypatch.setattr(execute_code.os, "killpg", recording_killpg)
    expected = {
        "vt_run_id": claim.row.vt_run_id,
        "status": "cancelled",
        "stop_state": "confirmed",
    }
    try:
        assert os.getpgid(group["pid"]) == group["pgid"] == group["pid"]
        assert os.getpgid(group["grandchild"]) == group["pgid"]
        with _client(_service(store)) as client:
            first = client.post(
                f"/strategy-runs/{claim.row.vt_run_id}/cancel", headers=AUTH
            )
            assert first.status_code == 202, first.text
            assert first.json() == expected
            terminal = store.get(claim.row.vt_run_id)
            second = client.post(
                f"/strategy-runs/{claim.row.vt_run_id}/cancel", headers=AUTH
            )
            assert second.status_code == 202
            assert second.json() == expected
        assert signals == [signal.SIGTERM, signal.SIGKILL]
        assert terminal is not None and terminal.status == "cancelled"
        assert terminal.error is None and terminal.error_kind is None
        assert store.get(claim.row.vt_run_id) == terminal
        for pid in (group["pid"], group["grandchild"]):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if group["pgid"] == group["pid"]:
            with contextlib.suppress(OSError):
                os.killpg(group["pgid"], signal.SIGKILL)
        else:
            for pid in (group["pid"], group["grandchild"]):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            os.waitpid(group["pid"], 0)


def test_unknown_workflow_and_unregistered_code_are_honest(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    service = _service(store)
    unrelated = _spawn_sleeper()
    workflow_id = _claim_with_handle(store, "workflow", "workflow", "workflow-run")
    restarted_id = _claim_with_handle(store, "restart", "code", str(unrelated.pid))
    try:
        with _client(service) as client:
            assert client.post("/strategy-runs/missing/cancel").status_code == 401
            missing = client.post("/strategy-runs/missing/cancel", headers=AUTH)
            assert missing.status_code == 404
            assert missing.json() == {"detail": "strategy_run_not_found"}
            for run_id in (workflow_id, restarted_id):
                response = client.post(f"/strategy-runs/{run_id}/cancel", headers=AUTH)
                assert response.status_code == 202
                assert response.json() == {
                    "vt_run_id": run_id,
                    "status": "running",
                    "stop_state": "unknown",
                }
                row = store.get(run_id)
                assert row is not None and row.status == "running"
            assert unrelated.poll() is None
    finally:
        _stop_process(unrelated)


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_cancel_is_idempotent_without_a_terminator_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_status: str
) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    run_id = _claim_with_handle(store, "terminal", "code", "already-finished")
    if terminal_status == "failed":
        assert store.mark_terminal(
            run_id, "failed", error="boom", error_kind="runner_error", now_ms=NOW + 2
        )
    else:
        assert store.mark_terminal(run_id, terminal_status, now_ms=NOW + 2)
    before = store.get(run_id)

    def unexpected_terminator(_run_id: str) -> bool:
        raise AssertionError("terminal cancel must not signal")

    monkeypatch.setattr(
        service_module, "cancel_code_run", unexpected_terminator, raising=False
    )
    first = _service(store).cancel(run_id)
    second = _service(store).cancel(run_id)
    assert first == second
    assert (first.vt_run_id, first.status, first.stop_state) == (
        run_id,
        terminal_status,
        "confirmed",
    )
    assert store.get(run_id) == before


def test_duplicate_start_keeps_the_original_409_body(tmp_path: Path) -> None:
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    with _client(_service(store)) as client:
        first = client.post("/strategy-runs", json=_start_body(), headers=AUTH)
        second = client.post("/strategy-runs", json=_start_body(), headers=AUTH)
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == {
        "error": "duplicate_run_key",
        "vt_run_id": first.json()["vt_run_id"],
    }
