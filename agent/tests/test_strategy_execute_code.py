"""Start/poll contract of the scheduled code-strategy runner.

The happy path starts a real child process against a 20-bar ``local`` CSV
fixture, which is the only way to prove the snapshot copy, the HOME-relative
data bridge and the runner's artifact writes line up. The error paths use
``run_code_entry``'s injection seam so they stay deterministic and fast.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.paths import get_runs_dir
from src.scheduled_research.models import ScheduledResearchJob
from src.strategy_library import execute_code, run_code_entry
from src.strategy_library.paths import version_code_path, version_config_path

_STRATEGY_ID = "buyhold"
_VERSION = "v1-abc12345"
_JOB_ID = "job-code-1"
_BARS = 20
_POLL_TIMEOUT_S = 90.0

_LOCAL_CONFIG = {
    "source": "local",
    "codes": ["LOCAL1"],
    "start_date": "2024-01-01",
    "end_date": "2024-01-31",
    "initial_cash": 100000.0,
}

_BUY_AND_HOLD = """\
import pandas as pd


class SignalEngine:
    def generate(self, data_map):
        return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}
"""

_TOP_LEVEL_EXEC = """\
import os

import pandas as pd

os.system("echo pwned")


class SignalEngine:
    def generate(self, data_map):
        return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}
"""


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at the temp dir the child inherits and never touch the real one."""
    home = tmp_path / "home"
    (home / ".vibe-trading" / "data-bridge").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("VIBE_TRADING_HOME", raising=False)
    return home


def _write_local_bars(home: Path) -> None:
    """Write the 20 daily bars and the ``data-bridge`` config the loader reads."""
    data_dir = home / ".vibe-trading" / "data-bridge"
    bars_path = data_dir / "bars.csv"
    lines = ["date,open,high,low,close,volume"]
    price = 100.0
    start = dt.date(2024, 1, 1)
    for offset in range(_BARS):
        day = (start + dt.timedelta(days=offset)).isoformat()
        lines.append(f"{day},{price},{price * 1.01:.4f},{price * 0.99:.4f},{price},1000")
        price += 1.0
    bars_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (data_dir / "config.yaml").write_text(
        "sources:\n"
        "  - symbol: LOCAL1\n"
        "    type: csv\n"
        f'    path: "{bars_path}"\n',
        encoding="utf-8",
    )


def _runtime_root(home: Path) -> Path:
    return home / ".vibe-trading"


def _write_snapshot(home: Path, signal_source: str, *, version: str = _VERSION) -> None:
    """Publish the two files a version snapshot must carry."""
    root = _runtime_root(home)
    config_path = version_config_path(_STRATEGY_ID, version, runtime_root=root)
    code_path = version_code_path(_STRATEGY_ID, version, runtime_root=root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(_LOCAL_CONFIG), encoding="utf-8")
    code_path.write_text(signal_source, encoding="utf-8")


def _job(version: str = _VERSION, job_id: str = _JOB_ID) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt="",
        schedule="60000",
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=_STRATEGY_ID,
        strategy_version=version,
    )


def _poll_until_terminal(handle: execute_code.CodeHandle):
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        outcome = execute_code.poll_code_run(handle)
        if outcome is not None:
            return outcome
        time.sleep(0.05)
    logs = Path(handle.run_dir or "") / "logs"
    detail = {
        path.name: path.read_text(encoding="utf-8", errors="replace")[-2000:]
        for path in sorted(logs.glob("*.txt"))
    }
    pytest.fail(f"run did not reach a terminal state within {_POLL_TIMEOUT_S}s: {detail}")


def _release(handle: execute_code.CodeHandle) -> None:
    execute_code.PID_REGISTRY.pop(handle.run_id, None)


def test_start_polls_to_completed_with_equity_and_metrics(fake_home):
    _write_local_bars(fake_home)
    _write_snapshot(fake_home, _BUY_AND_HOLD)

    started = time.monotonic()
    handle = execute_code.start_code_run(_job())
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"start blocked for {elapsed:.3f}s"
    run_dir = Path(handle.run_dir)
    assert run_dir.is_relative_to(get_runs_dir() / "scheduled" / _JOB_ID)
    assert json.loads((run_dir / "state.json").read_text(encoding="utf-8")) == {
        "status": "running"
    }

    outcome = _poll_until_terminal(handle)
    _release(handle)

    assert outcome.status == "completed", outcome.error
    assert "total_return=" in outcome.summary
    assert "trade_count=" in outcome.summary
    assert outcome.error == ""
    equity = (run_dir / "artifacts" / "equity.csv").read_text(encoding="utf-8")
    assert len([line for line in equity.splitlines() if line.strip()]) == _BARS + 1
    assert outcome.run_dir == str(run_dir)


def test_handle_carries_exactly_five_keys_and_no_pid(fake_home, monkeypatch):
    _write_snapshot(fake_home, _BUY_AND_HOLD)
    calls: list[tuple[list[str], dict]] = []

    def _fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=43210)

    monkeypatch.setattr(execute_code.subprocess, "Popen", _fake_popen)

    handle = execute_code.start_code_run(_job())
    try:
        assert set(handle.to_in_flight()) == {
            "run_id",
            "kind",
            "session_id",
            "run_dir",
            "started_at",
        }
        assert not hasattr(handle, "pid")
        assert handle.kind == "code"
        assert handle.session_id is None
        assert execute_code.PID_REGISTRY[handle.run_id] == 43210

        argv, kwargs = calls[0]
        assert argv == [
            sys.executable,
            "-m",
            "src.strategy_library.run_code_entry",
            handle.run_dir,
        ]
        assert "shell" not in kwargs
        assert kwargs["cwd"] == str(execute_code.AGENT_ROOT)
    finally:
        _release(handle)


def test_top_level_execution_is_rejected_and_reported_from_the_stderr_log(fake_home):
    _write_local_bars(fake_home)
    _write_snapshot(fake_home, _TOP_LEVEL_EXEC)

    handle = execute_code.start_code_run(_job())
    outcome = _poll_until_terminal(handle)
    _release(handle)

    assert outcome.status == "failed"
    assert "Executable top-level statement" in outcome.error
    state_text = (Path(handle.run_dir) / "state.json").read_text(encoding="utf-8")
    assert "Executable top-level statement" not in state_text
    stderr_log = (Path(handle.run_dir) / "logs" / "runner_stderr.txt").read_text(
        encoding="utf-8"
    )
    assert "Executable top-level statement" in stderr_log


def test_missing_snapshot_raises_before_creating_the_run_directory(fake_home):
    runs_root = _runtime_root(fake_home) / "runs"

    with pytest.raises(FileNotFoundError):
        execute_code.start_code_run(_job(version="v9-deadbeef"))

    assert not (runs_root / "scheduled" / _JOB_ID).exists()


def test_torn_state_file_reads_as_still_running(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text('{"status": "run', encoding="utf-8")
    handle = execute_code.CodeHandle(
        run_id="torn-run",
        kind="code",
        session_id=None,
        run_dir=str(run_dir),
        started_at=1,
    )

    assert execute_code.poll_code_run(handle) is None
    (run_dir / "state.json").unlink()
    assert execute_code.poll_code_run(handle) is None


def test_success_state_without_an_equity_curve_is_failed(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "state.json").write_text('{"status": "success"}', encoding="utf-8")
    (run_dir / "artifacts" / "equity.csv").write_text("timestamp,equity\n", encoding="utf-8")
    handle = execute_code.CodeHandle(
        run_id="empty-equity",
        kind="code",
        session_id=None,
        run_dir=str(run_dir),
        started_at=1,
    )

    outcome = execute_code.poll_code_run(handle)

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_kind == "empty_equity"


def test_error_envelope_is_translated_into_a_terminal_failed_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text('{"status": "running"}', encoding="utf-8")
    envelope = json.dumps({"status": "error", "error": "config.json parse error"})

    exit_code = run_code_entry.main(
        [str(run_dir)], run_backtest_fn=lambda _run_dir: envelope
    )

    assert exit_code == 1
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state == {"status": "failed", "reason": "config.json parse error"}
    stderr_log = (run_dir / "logs" / "runner_stderr.txt").read_text(encoding="utf-8")
    assert "config.json parse error" in stderr_log

    outcome = execute_code.poll_code_run(
        execute_code.CodeHandle(
            run_id="early-error",
            kind="code",
            session_id=None,
            run_dir=str(run_dir),
            started_at=1,
        )
    )
    assert outcome is not None
    assert outcome.status == "failed"
    assert "config.json parse error" in outcome.error


def test_entry_keeps_the_runners_reason_when_state_is_already_terminal(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        '{"status": "failed", "reason": "backtest engine exited with code 1"}',
        encoding="utf-8",
    )

    run_code_entry.main(
        [str(run_dir)],
        run_backtest_fn=lambda _run_dir: json.dumps({"status": "error", "error": "late"}),
    )

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["reason"] == "backtest engine exited with code 1"
    stderr_log = (run_dir / "logs" / "runner_stderr.txt").read_text(encoding="utf-8")
    assert "late" in stderr_log


def test_entry_records_a_failure_when_run_backtest_raises(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text('{"status": "running"}', encoding="utf-8")

    def _boom(_run_dir: str) -> str:
        raise RuntimeError("engine exploded")

    exit_code = run_code_entry.main([str(run_dir)], run_backtest_fn=_boom)

    assert exit_code == 1
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state == {"status": "failed", "reason": "RuntimeError: engine exploded"}
    stderr_log = (run_dir / "logs" / "runner_stderr.txt").read_text(encoding="utf-8")
    assert "Traceback" in stderr_log
