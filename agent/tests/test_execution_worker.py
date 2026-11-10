"""Server-owned native worker specifications (todo 4, Part A).

Every case asserts the public factory/validation seam only: callers can never
supply a command, a shell string, a cwd, or a path outside the allowed run
roots. No process is spawned by this module.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.strategy_runs import execution_worker as worker

AGENT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def allowed_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "runs").resolve()
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(root))
    return root


def _kwargs(run_dir: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "kind": "backtest",
        "argv": (
            sys.executable,
            str(AGENT_ROOT / "backtest" / "runner.py"),
            str(run_dir),
        ),
        "cwd": AGENT_ROOT,
        "timeout_s": 600,
        "run_dir": run_dir,
        "result_path": run_dir / "worker_result.json",
        "log_path": run_dir / "worker.log",
    }
    values.update(overrides)
    return values


def test_backtest_factory_returns_exact_server_command(allowed_runs: Path) -> None:
    run_dir = allowed_runs / "job-1"

    spec = worker.backtest_worker_spec(run_dir, 600)

    assert spec.kind == "backtest"
    assert spec.argv == (
        sys.executable,
        str(AGENT_ROOT / "backtest" / "runner.py"),
        str(run_dir),
    )
    assert spec.cwd == AGENT_ROOT
    assert spec.timeout_s == 600
    assert spec.run_dir == run_dir
    assert spec.result_path == run_dir / "worker_result.json"
    assert spec.log_path == run_dir / "worker.log"
    assert spec.env is None


def test_factor_factory_returns_module_command_and_env(allowed_runs: Path) -> None:
    run_dir = allowed_runs / "job-2"
    run_dir.mkdir()

    spec = worker.factor_worker_spec(run_dir, 30, env={"VT_MODE": "factor"})

    assert spec.kind == "factor"
    assert spec.argv == (
        sys.executable,
        "-m",
        "src.factors.prototype_worker",
        str(run_dir),
    )
    assert spec.cwd == AGENT_ROOT
    assert spec.result_path == run_dir / "worker_result.json"
    assert spec.log_path == run_dir / "worker.log"
    assert spec.env == {"VT_MODE": "factor"}


def test_worker_spec_is_frozen(allowed_runs: Path) -> None:
    spec = worker.backtest_worker_spec(allowed_runs / "job", 60)

    with pytest.raises(FrozenInstanceError):
        spec.timeout_s = 1  # type: ignore[misc]


def test_run_dir_may_be_created_by_the_supervisor(allowed_runs: Path) -> None:
    run_dir = allowed_runs / "not-yet-created" / "nested"

    spec = worker.backtest_worker_spec(run_dir, 60)

    assert spec.run_dir == run_dir
    assert not run_dir.exists()


@pytest.mark.parametrize(
    ("field", "overrides"),
    [
        ("kind", {"kind": "live"}),
        ("argv", {"argv": "python backtest/runner.py"}),
        ("argv", {"argv": ()}),
        ("argv", {"argv": ("/bin/bash", "-c", "true")}),
        ("argv", {"argv": (sys.executable, "-c", "print(1)")}),
        ("argv", {"argv": (sys.executable, 42)}),
        ("cwd", {"cwd": Path("agent")}),
        ("cwd", {"cwd": "/tmp"}),
        ("timeout_s", {"timeout_s": 0}),
        ("timeout_s", {"timeout_s": -5}),
        ("timeout_s", {"timeout_s": 86_401}),
        ("timeout_s", {"timeout_s": True}),
        ("env", {"env": {"A": 1}}),
        ("env", {"env": {1: "A"}}),
        ("env", {"env": [("A", "1")]}),
    ],
)
def test_direct_construction_rejects_invalid_fields(
    allowed_runs: Path, field: str, overrides: dict[str, object]
) -> None:
    run_dir = allowed_runs / "job"

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.WorkerSpec(**_kwargs(run_dir, **overrides))

    assert caught.value.field == field
    assert caught.value.reason


def test_run_dir_outside_allowed_root_is_rejected(allowed_runs: Path) -> None:
    outside = allowed_runs.parent / "outside"
    outside.mkdir()

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.backtest_worker_spec(outside, 60)

    error = caught.value
    assert (error.field, error.reason) == (
        "run_dir",
        "must be inside allowed run roots",
    )
    assert str(error) == "run_dir: must be inside allowed run roots"


def test_relative_run_dir_is_rejected(allowed_runs: Path) -> None:
    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.factor_worker_spec(Path("runs/job"), 60)

    assert caught.value.field == "run_dir"


def test_run_dir_that_is_a_file_is_rejected(allowed_runs: Path) -> None:
    target = allowed_runs / "not-a-directory"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.backtest_worker_spec(target, 60)

    assert caught.value.field == "run_dir"


def test_result_and_log_paths_must_stay_inside_run_dir(allowed_runs: Path) -> None:
    run_dir = allowed_runs / "job"

    for field in ("result_path", "log_path"):
        overrides = {field: allowed_runs / f"escaped-{field}"}
        with pytest.raises(worker.WorkerSpecError) as caught:
            worker.WorkerSpec(**_kwargs(run_dir, **overrides))
        assert caught.value.field == field

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.WorkerSpec(**_kwargs(run_dir, result_path=Path("worker_result.json")))

    assert caught.value.field == "result_path"


def test_symlinked_run_dir_cannot_escape_allowed_root(
    allowed_runs: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed_runs / "linked"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.backtest_worker_spec(link, 60)

    assert caught.value.field == "run_dir"


def test_symlinked_result_file_cannot_escape_run_dir(
    allowed_runs: Path, tmp_path: Path
) -> None:
    run_dir = allowed_runs / "job"
    run_dir.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text("{}", encoding="utf-8")
    (run_dir / "worker_result.json").symlink_to(secret)

    with pytest.raises(worker.WorkerSpecError) as caught:
        worker.WorkerSpec(**_kwargs(run_dir))

    assert caught.value.field == "result_path"
