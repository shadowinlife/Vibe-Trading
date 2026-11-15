"""Fake-Popen tests for NativeSupervisor subprocess environment hygiene."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

from src.strategy_runs import native_supervisor as native
from src.strategy_runs.control_contract import CONTRACT_VERSION
from src.strategy_runs.control_store import ControlStore
from src.strategy_runs.execution_worker import WorkerSpec

_AGENT_ROOT = Path(__file__).resolve().parents[1]
_CLICKHOUSE_CREDENTIALS = (
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_LLM_USER",
    "CLICKHOUSE_LLM_PASSWORD",
)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class _FakeThread:
    def __init__(self, **kwargs) -> None:
        self.daemon = kwargs["daemon"]

    def start(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass


def _start_capture(
    tmp_path: Path,
    parent: dict[str, str],
    overrides: dict[str, str],
) -> dict[str, str]:
    run_dir = (tmp_path / "runs" / "run-1").resolve()
    run_dir.mkdir(parents=True)
    captured = {}

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProcess(os.getpid())

    with patch.dict(os.environ, parent, clear=True):
        spec = WorkerSpec(
            kind="backtest",
            argv=(sys.executable, "worker.py"),
            cwd=_AGENT_ROOT,
            timeout_s=10,
            run_dir=run_dir,
            result_path=run_dir / "worker_result.json",
            log_path=run_dir / "worker.log",
            env=overrides,
        )
        store = ControlStore(tmp_path / "strategy_runs.sqlite3")
        store.claim_dispatch(
            "dispatch-a",
            "backtest",
            {"run_dir": str(run_dir)},
            "remote-a",
            CONTRACT_VERSION,
        )
        supervisor = native.NativeSupervisor(
            store,
            popen=fake_popen,
            thread_factory=_FakeThread,
        )
        supervisor.start("dispatch-a", spec)
    captured["stdout"].close()
    return captured["env"]


def test_native_start_uses_allowlist_and_preserves_nonsecret_overrides(
    tmp_path: Path,
) -> None:
    parent = {
        "PATH": "parent-path",
        "PYTHONPATH": "parent-pythonpath",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS": str(tmp_path),
        "TUSHARE_TOKEN": "tushare-sentinel",
        "CLICKHOUSE_HOST": "clickhouse.sentinel.invalid",
        "OPENAI_API_KEY": "parent-llm-sentinel",
        "VT_SUPERVISOR_TEST": "parent-value-must-not-leak",
        **{key: f"parent-{key.lower()}-sentinel" for key in _CLICKHOUSE_CREDENTIALS},
    }
    overrides = {
        "VT_SUPERVISOR_TEST": "server-sentinel",
        **{key: f"override-{key.lower()}-sentinel" for key in _CLICKHOUSE_CREDENTIALS},
    }

    env = _start_capture(tmp_path, parent, overrides)

    assert env["TUSHARE_TOKEN"] == "tushare-sentinel"
    assert env["CLICKHOUSE_HOST"] == "clickhouse.sentinel.invalid"
    assert env["VT_SUPERVISOR_TEST"] == "server-sentinel"
    assert env["PYTHONPATH"] == f"{_AGENT_ROOT}{os.pathsep}parent-pythonpath"
    assert "OPENAI_API_KEY" not in env
    assert all(key not in env for key in _CLICKHOUSE_CREDENTIALS)


def test_native_start_enforces_runtime_guards_after_overrides(tmp_path: Path) -> None:
    run_dir = (tmp_path / "runs" / "run-1").resolve()
    roots = f"{tmp_path},{tmp_path},{run_dir},{tmp_path}"
    parent = {
        "PATH": "parent-path",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS": str(tmp_path),
    }
    overrides = {
        "PATH": "",
        "PYTHONUNBUFFERED": "0",
        "PYTHONIOENCODING": "ascii",
        "PYTHONUTF8": "0",
        "VIBE_TRADING_BACKTEST_SANDBOX": "0",
        "VIBE_TRADING_ALLOWED_RUN_ROOTS": roots,
    }

    env = _start_capture(tmp_path, parent, overrides)

    assert env["PATH"] == os.defpath
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["VIBE_TRADING_BACKTEST_SANDBOX"] == "1"
    assert env["VIBE_TRADING_ALLOWED_RUN_ROOTS"].split(",") == [
        str(tmp_path),
        str(run_dir),
    ]
