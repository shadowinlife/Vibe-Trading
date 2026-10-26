"""Execution lifecycle of the RM strategy-run service (T4).

Every case uses a temporary SQLite ledger and a fake ``start``/``poll`` pair:
no F9 subprocess, no session runtime, no network and no wall-clock sleep. The
clock is injected, so timeout and restart recovery are deterministic.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import strategy_run_server
from src.api.strategy_run_routes import DuplicateRunKeyError, StrategyRunCommand
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.strategy_policy import InFlightHandle
from src.strategy_library.history import RunOutcome
from src.strategy_runs import StrategyRunStore
from src.strategy_runs import service as service_module
from src.strategy_runs.service import StrategyRunService, SubmitResult

NOW = 1_700_000_000_000
TIMEOUT_S = 900
RUN_KEY = "user-1:schedule-1:2026-10-26T00:00:00Z:0"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"


class _Clock:
    """Mutable epoch-millisecond clock for deterministic sweeps."""

    def __init__(self, value: int = NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance_ms(self, milliseconds: int) -> None:
        self.value += milliseconds


class _FakeRunner:
    """Duck-typed F9 runner: records starts/polls and replays outcomes."""

    def __init__(
        self,
        clock: _Clock,
        *,
        kind: str = "code",
        start_error: BaseException | None = None,
    ) -> None:
        self.clock = clock
        self.kind = kind
        self.start_error = start_error
        self.starts: list[object] = []
        self.polls: list[InFlightHandle] = []
        self.outcomes: list[RunOutcome] = []

    async def start(self, job):  # noqa: ANN001, ANN201 — duck-typed seam
        self.starts.append(job)
        if self.start_error is not None:
            raise self.start_error
        return InFlightHandle(
            run_id=f"runner-{job.id}",
            kind=self.kind,
            session_id="session-1" if self.kind == "workflow" else None,
            run_dir=None if self.kind == "workflow" else f"/runs/{job.id}",
            started_at=self.clock(),
        )

    def poll(self, handle: InFlightHandle) -> RunOutcome | None:
        self.polls.append(handle)
        return self.outcomes.pop(0) if self.outcomes else None


def _command(
    run_key: str = RUN_KEY,
    *,
    strategy_id: str = "momentum-1",
    strategy_version: str = "v1-aaaaaaaa",
    timeout_s: int = TIMEOUT_S,
    sandbox_id: str | None = "box-1",
    upstream: str | None = None,
) -> StrategyRunCommand:
    return StrategyRunCommand(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        run_key=run_key,
        sandbox_id=sandbox_id,
        upstream=upstream,
        timeout_s=timeout_s,
    )


def _service(
    db_path: Path,
    runner: _FakeRunner,
    clock: _Clock,
    *,
    lookup: Callable[[str], object | None] | None = None,
    version_error: str | None = None,
    grace_s: float = 120.0,
    interval_s: float = 5.0,
    target_validator: Callable[[StrategyRunCommand], bool] | None = None,
) -> StrategyRunService:
    def default_lookup(strategy_id: str) -> object:
        return SimpleNamespace(kind=runner.kind)

    return StrategyRunService(
        store=StrategyRunStore(db_path),
        runner=runner,
        strategy_lookup=lookup if lookup is not None else default_lookup,
        version_checker=lambda *_args: version_error,
        now_ms=clock,
        target_validator=target_validator,
        sweep_interval_s=interval_s,
        recovery_grace_s=grace_s,
    )


def _auth_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    token = "dummy-service-token-t5-4c9d"
    path = tmp_path / "rm_vt_strategy_run_token"
    path.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return {"Authorization": f"Bearer {token}"}


def _submit(service: StrategyRunService, command: StrategyRunCommand) -> str:
    result = asyncio.run(service.submit(command))
    assert isinstance(result, SubmitResult)
    assert result.vt_run_id
    return result.vt_run_id


def test_duplicate_submit_starts_once_and_reports_the_same_run(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    command = _command()

    first = _submit(service, command)
    with pytest.raises(DuplicateRunKeyError) as excinfo:
        asyncio.run(service.submit(command))

    assert excinfo.value.vt_run_id == first
    assert len(runner.starts) == 1
    row = service.status(first)
    assert row is not None and row.status == "running"


def test_unknown_strategy_is_terminal_and_still_returns_a_run_id(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(
        tmp_path / "runs.sqlite3", runner, clock, lookup=lambda _sid: None
    )

    vt_run_id = _submit(service, _command())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "strategy_not_found"
    assert row.error and row.error.strip()
    assert row.handle_json is None
    assert runner.starts == []

    with pytest.raises(DuplicateRunKeyError) as excinfo:
        asyncio.run(service.submit(_command()))
    assert excinfo.value.vt_run_id == vt_run_id


def test_unknown_version_is_terminal_and_never_starts(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(
        tmp_path / "runs.sqlite3",
        runner,
        clock,
        version_error="version_not_found",
    )

    vt_run_id = _submit(service, _command(strategy_version="v9-missing"))

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "version_not_found"
    assert row.error and row.error.strip()
    assert runner.starts == []


def test_running_converges_to_completed_and_repeated_status_is_stable(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    runner.outcomes.append(RunOutcome(status="completed", summary="done"))

    vt_run_id = _submit(service, _command())
    asyncio.run(service.sweep_once())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "completed"
    assert row.error is None
    assert row.error_kind is None
    first_read = service.status(vt_run_id)
    second_read = service.status(vt_run_id)
    assert first_read == second_read

    polls = len(runner.polls)
    asyncio.run(service.sweep_once())
    assert len(runner.polls) == polls
    assert runner.polls[0].run_id == json.loads(row.handle_json or "{}")["run_id"]


def test_failed_outcome_persists_both_error_fields(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    runner.outcomes.append(
        RunOutcome(
            status="failed", error="engine exploded", error_kind="backtest_error"
        )
    )

    vt_run_id = _submit(service, _command())
    asyncio.run(service.sweep_once())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error == "engine exploded"
    assert row.error_kind == "backtest_error"


@pytest.mark.parametrize("status", ["skipped", "missed", "unexpected"])
def test_non_terminal_runner_states_collapse_to_runner_error(
    tmp_path: Path, status: str
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    if status == "unexpected":
        # RunOutcome rejects unknown statuses; a duck-typed runner need not.
        runner.outcomes.append(SimpleNamespace(status=status))  # type: ignore[arg-type]
    else:
        runner.outcomes.append(RunOutcome(status=status))

    vt_run_id = _submit(service, _command())
    asyncio.run(service.sweep_once())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "runner_error"
    assert row.error and row.error.strip()


def test_timeout_wins_before_polling_and_stops_further_polls(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)

    vt_run_id = _submit(service, _command())
    clock.advance_ms(TIMEOUT_S * 1000)
    asyncio.run(service.sweep_once())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "timeout"
    assert row.error and str(TIMEOUT_S) in row.error
    assert runner.polls == []

    asyncio.run(service.sweep_once())
    assert runner.polls == []


@pytest.mark.parametrize(
    ("start_error", "expected_kind"),
    [
        (LookupError("gone"), "strategy_not_found"),
        (ValueError("unsupported kind"), "runner_error"),
        (RuntimeError("boom"), "runner_error"),
    ],
)
def test_start_failures_are_terminal_and_still_return_a_run_id(
    tmp_path: Path, start_error: BaseException, expected_kind: str
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock, start_error=start_error)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)

    vt_run_id = _submit(service, _command())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == expected_kind
    assert row.error and row.error.strip()
    assert len(runner.starts) == 1


def test_handle_less_run_waits_out_the_grace_then_recovers(tmp_path: Path) -> None:
    clock = _Clock()
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    claim = store.claim(
        RUN_KEY, "momentum-1", "v1-aaaaaaaa", None, None, TIMEOUT_S, now_ms=NOW
    )
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock, grace_s=120.0)

    clock.value = NOW + 10_000
    asyncio.run(service.sweep_once())
    running = service.status(claim.row.vt_run_id)
    assert running is not None and running.status == "running"

    clock.value = NOW + 120_000
    asyncio.run(service.sweep_once())
    recovered = service.status(claim.row.vt_run_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_kind == "recovered_after_restart"
    assert runner.polls == []


def test_handle_less_run_past_timeout_recovers_as_timeout(tmp_path: Path) -> None:
    clock = _Clock()
    store = StrategyRunStore(tmp_path / "runs.sqlite3")
    store.claim(RUN_KEY, "momentum-1", "v1-aaaaaaaa", None, None, 60, now_ms=NOW)
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)

    clock.value = NOW + 60_000
    service.recover()

    row = store.get_by_run_key(RUN_KEY)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "timeout"
    assert runner.polls == []


def test_code_handle_is_rehydrated_by_a_new_service_and_polled_to_terminal(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runs.sqlite3"
    clock = _Clock()
    first_runner = _FakeRunner(clock, kind="code")
    first_service = _service(db_path, first_runner, clock)
    vt_run_id = _submit(first_service, _command())

    stored = first_service.status(vt_run_id)
    assert stored is not None and stored.status == "running"
    handle = json.loads(stored.handle_json or "{}")
    assert set(handle) == {"run_id", "kind", "session_id", "run_dir", "started_at"}

    second_runner = _FakeRunner(clock, kind="code")
    second_runner.outcomes.append(RunOutcome(status="completed", summary="resumed"))
    second_service = _service(db_path, second_runner, clock)

    second_service.recover()
    still_running = second_service.status(vt_run_id)
    assert still_running is not None and still_running.status == "running"

    asyncio.run(second_service.sweep_once())
    terminal = second_service.status(vt_run_id)
    assert terminal is not None
    assert terminal.status == "completed"
    assert second_runner.starts == []
    assert len(second_runner.polls) == 1
    polled = second_runner.polls[0]
    assert polled.run_id == handle["run_id"]
    assert polled.kind == "code"
    assert polled.run_dir == handle["run_dir"]
    assert polled.started_at == handle["started_at"]


def test_workflow_contract_violation_is_a_terminal_failure(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock, kind="workflow")
    service = _service(
        tmp_path / "runs.sqlite3",
        runner,
        clock,
        lookup=lambda _sid: SimpleNamespace(kind="workflow"),
    )
    runner.outcomes.append(
        RunOutcome(
            status="failed",
            error="the output contract is unavailable for polling",
            error_kind="contract_violation",
        )
    )

    vt_run_id = _submit(service, _command())
    asyncio.run(service.sweep_once())

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "contract_violation"
    assert row.error and row.error.strip()


def test_status_is_a_pure_read(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    vt_run_id = _submit(service, _command())

    row = service.status(vt_run_id)
    assert row is not None
    for _ in range(3):
        assert service.status(vt_run_id) == row
    assert runner.polls == []


def test_a_stale_writer_cannot_overwrite_a_terminal_row(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    vt_run_id = _submit(service, _command())

    assert service._store.mark_terminal(
        vt_run_id, "failed", error="first winner", error_kind="timeout"
    )
    service._apply_outcome(vt_run_id, RunOutcome(status="completed"))

    row = service.status(vt_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_kind == "timeout"
    assert row.error == "first winner"
    assert (
        service._store.mark_terminal(
            vt_run_id, "failed", error="late", error_kind="runner_error"
        )
        is False
    )


def test_sweeper_is_single_task_and_leaves_f9_store_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime))
    f9_path = runtime / "scheduled_research" / "scheduled_research_jobs.json"
    f9_path.parent.mkdir(parents=True)
    f9_path.write_text('{"jobs": {}}', encoding="utf-8")
    before = f9_path.read_bytes()

    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    _submit(service, _command())
    asyncio.run(service.sweep_once())

    async def scenario() -> None:
        await service.start()
        first = service.sweeper_task
        assert first is not None
        await service.start()
        assert service.sweeper_task is first
        await service.stop()
        assert first.done()
        assert service.sweeper_task is None
        pending = {task for task in asyncio.all_tasks() if not task.done()}
        assert pending == {asyncio.current_task()}

    asyncio.run(scenario())
    assert f9_path.read_bytes() == before


def test_sweeper_task_is_cancelled_on_stop(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(tmp_path / "runs.sqlite3", runner, clock, interval_s=30.0)

    async def scenario() -> None:
        await service.start()
        task = service.sweeper_task
        assert task is not None and not task.done()
        await service.stop()
        assert task.cancelled()

    asyncio.run(scenario())


def test_lifespan_starts_and_stops_a_lifecycle_provider() -> None:
    class _LifecycleService:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0

        async def submit(self, command):  # noqa: ANN001, ANN201 — duck-typed
            return SubmitResult("vt-lifecycle")

        def status(self, vt_run_id: str):
            return None

        async def start(self) -> None:
            self.started += 1

        async def stop(self) -> None:
            self.stopped += 1

    service = _LifecycleService()
    app = strategy_run_server.create_app(lambda: service)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert service.started == 1
        assert service.stopped == 0
    assert service.stopped == 1


def test_production_provider_failure_fails_app_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(strategy_run_server, "default_service_provider", _boom)
    app = strategy_run_server.create_app()

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        with TestClient(app):
            pass


def test_build_service_is_a_process_wide_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime))
    monkeypatch.setenv("VT_SANDBOX_ID", "default--built-box")
    monkeypatch.setattr(service_module, "_SERVICE", None)

    first = service_module.build_service()
    second = service_module.build_service()

    assert first is second
    assert first._store.path == runtime / "strategy_runs.sqlite3"
    assert first.sweep_interval_s == 5.0
    assert first.recovery_grace_s == 120.0

    validator = first._target_validator
    assert validator is not None
    assert validator(_command(sandbox_id="default--built-box")) is True
    monkeypatch.setenv("VT_SANDBOX_ID", "default--changed-box")
    assert validator(_command(sandbox_id="default--built-box")) is True
    assert validator(_command(sandbox_id="default--changed-box")) is False


def test_build_service_reads_clamped_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("VT_STRATEGY_RUN_SWEEP_INTERVAL_S", "0")
    monkeypatch.setenv("VT_STRATEGY_RUN_RECOVERY_GRACE_S", "45")
    monkeypatch.setattr(service_module, "_SERVICE", None)

    service = service_module.build_service()

    assert service.sweep_interval_s == 1.0
    assert service.recovery_grace_s == 45.0


class _MismatchService:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def submit(self, command):  # noqa: ANN001, ANN201 — duck-typed seam
        self.calls.append(command)
        raise HTTPException(status_code=403, detail="target_mismatch")

    def status(self, vt_run_id: str):
        return None


def test_route_passes_a_service_http_exception_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth_header(tmp_path, monkeypatch)
    service = _MismatchService()
    app = strategy_run_server.create_app(lambda: service)

    with TestClient(app) as client:
        response = client.post(
            "/strategy-runs",
            json={
                "strategy_id": "momentum-1",
                "strategy_version": "v1-aaaaaaaa",
                "run_key": RUN_KEY,
                "sandbox_id": "default--other-box",
                "timeout_s": TIMEOUT_S,
            },
            headers=auth,
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "target_mismatch"}
    assert len(service.calls) == 1


def test_target_mismatch_fails_closed_before_the_claim(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(
        tmp_path / "runs.sqlite3",
        runner,
        clock,
        target_validator=service_module._target_validator("default--box-a"),
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(service.submit(_command(sandbox_id="default--box-b")))

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "target_mismatch"
    assert runner.starts == []
    assert service._store.get_by_run_key(RUN_KEY) is None

    vt_run_id = _submit(service, _command(sandbox_id="default--box-a"))
    row = service.status(vt_run_id)
    assert row is not None and row.status == "running"
    assert len(runner.starts) == 1


def test_route_refuses_another_box_then_accepts_the_same_run_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth_header(tmp_path, monkeypatch)
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(
        tmp_path / "runs.sqlite3",
        runner,
        clock,
        interval_s=30.0,
        target_validator=service_module._target_validator("default--this-box"),
    )
    body = {
        "strategy_id": "momentum-1",
        "strategy_version": "v1-aaaaaaaa",
        "run_key": RUN_KEY,
        "sandbox_id": "default--other-box",
        "timeout_s": TIMEOUT_S,
    }
    app = strategy_run_server.create_app(lambda: service)

    with TestClient(app) as client:
        rejected = client.post("/strategy-runs", json=body, headers=auth)
        assert rejected.status_code == 403
        assert rejected.json() == {"detail": "target_mismatch"}
        assert runner.starts == []
        assert service._store.get_by_run_key(RUN_KEY) is None

        accepted = client.post(
            "/strategy-runs",
            json={**body, "sandbox_id": "default--this-box"},
            headers=auth,
        )

    assert accepted.status_code == 202
    claimed = service._store.get_by_run_key(RUN_KEY)
    assert claimed is not None and claimed.status == "running"
    assert accepted.json() == {"vt_run_id": claimed.vt_run_id}
    assert len(runner.starts) == 1


def test_box_identity_prefers_vt_sandbox_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VT_SANDBOX_ID", "default--explicit")
    monkeypatch.setenv("HOSTNAME", "pod-host")

    assert service_module._rm_sandbox_id() == "default--explicit"


def test_box_identity_is_the_rm_prefix_plus_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VT_SANDBOX_ID", raising=False)
    monkeypatch.delenv("RM_LLM_SANDBOX_ID_PREFIX", raising=False)
    monkeypatch.setenv("HOSTNAME", "pod-host")
    assert service_module._rm_sandbox_id() == "default--pod-host"

    monkeypatch.setenv("RM_LLM_SANDBOX_ID_PREFIX", "team--")
    assert service_module._rm_sandbox_id() == "team--pod-host"

    monkeypatch.delenv("RM_LLM_SANDBOX_ID_PREFIX")
    monkeypatch.delenv("HOSTNAME")
    monkeypatch.setattr(socket, "gethostname", lambda: "socket-host")
    assert service_module._rm_sandbox_id() == "default--socket-host"


def test_bare_hostname_is_never_a_valid_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VT_SANDBOX_ID", raising=False)
    monkeypatch.delenv("RM_LLM_SANDBOX_ID_PREFIX", raising=False)
    monkeypatch.setenv("HOSTNAME", "pod-host")
    validator = service_module._target_validator(service_module._rm_sandbox_id())

    assert validator(_command(sandbox_id="pod-host")) is False
    assert validator(_command(sandbox_id="default--pod-host")) is True
    assert validator(_command(sandbox_id="default--other")) is False
    assert (
        validator(
            _command(sandbox_id=None, upstream="https://gw.example/kruise/x/8000")
        )
        is True
    )


def test_upstream_only_is_accepted_and_never_dialled(tmp_path: Path) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock)
    service = _service(
        tmp_path / "runs.sqlite3",
        runner,
        clock,
        target_validator=service_module._target_validator("default--this-box"),
    )

    vt_run_id = _submit(
        service,
        _command(sandbox_id=None, upstream="https://gw.example/kruise/other/8000"),
    )

    row = service.status(vt_run_id)
    assert row is not None and row.status == "running"
    assert len(runner.starts) == 1
    assert not hasattr(runner.starts[0], "upstream")


@pytest.mark.parametrize("kind", ["code", "workflow"])
def test_both_kinds_reach_the_runner_with_the_in_memory_job(
    tmp_path: Path, kind: str
) -> None:
    clock = _Clock()
    runner = _FakeRunner(clock, kind=kind)
    service = _service(tmp_path / "runs.sqlite3", runner, clock)
    runner.outcomes.append(RunOutcome(status="completed", summary="done"))

    vt_run_id = _submit(service, _command())

    assert len(runner.starts) == 1
    job = runner.starts[0]
    assert isinstance(job, ScheduledResearchJob)
    assert job.id == f"svc-{vt_run_id}"
    assert job.schedule == "60000"
    assert job.prompt == ""
    assert job.source_type == "strategy"
    assert job.executor_kind == "strategy"
    assert job.strategy_id == "momentum-1"
    assert job.strategy_version == "v1-aaaaaaaa"
    assert job.run_policy["run_timeout_s"] == TIMEOUT_S

    asyncio.run(service.sweep_once())

    assert len(runner.polls) == 1
    assert runner.polls[0].kind == kind
    row = service.status(vt_run_id)
    assert row is not None and row.status == "completed"


def test_service_module_does_not_dispatch_runners_itself() -> None:
    source = Path(service_module.__file__).read_text(encoding="utf-8")

    assert "start_code_run" not in source
    assert "start_workflow_run" not in source
