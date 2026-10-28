"""Execution semantics for scheduled ``strategy`` jobs.

Mechanical proofs for the async-start / persisted-handle / poll / restart
contract, with injected clocks and a fake runner (no real backtests, no
sleeps):

(a) a never-finishing runner is started once across ticks and stays RUNNING,
    and each firing that overlaps it is recorded as ``skipped``;
(b) a completed poll writes COMPLETED, one history row and clears ``in_flight``;
(c) a persisted RUNNING job is recovered by ``start()`` without a dispatch;
(d) a job deleted during its poll stays deleted and gets no history file;
(e) the run timeout is enforced by millisecond comparison;
(f) the executor stays inside its 799-line budget;
(g) a completed job does not restart on later ticks and never re-polls.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.strategy_library import history
from src.strategy_library.history import RunOutcome

_AGENT_DIR = Path(__file__).resolve().parents[1]
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_IN_FLIGHT_KEYS = {"run_id", "kind", "session_id", "run_dir", "started_at"}


@pytest.fixture()
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point strategy run history at a per-test runtime root."""
    root = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
    return root


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _job(**overrides: object) -> ScheduledResearchJob:
    fields: dict[str, object] = {
        "id": "job-1",
        "prompt": "",
        "schedule": "60000",
        "source_type": "strategy",
        "executor_kind": "strategy",
        "strategy_id": "momentum-1",
        "strategy_version": "v1-abcdef12",
        "next_run_at": 1000,
        "created_at": 0,
    }
    fields.update(overrides)
    return ScheduledResearchJob(**fields)  # type: ignore[arg-type]


def _in_flight(run_id: str = "run-crash", started_at: int = 1000) -> dict[str, object]:
    return {
        "run_id": run_id,
        "kind": "code",
        "session_id": None,
        "run_dir": None,
        "started_at": started_at,
    }


class _FakeHandle:
    def __init__(self, *, run_id: str, started_at: int) -> None:
        self.run_id = run_id
        self.kind = "code"
        self.session_id = None
        self.run_dir = None
        self.started_at = started_at

    def to_in_flight(self) -> dict[str, object]:
        # The extra ``pid`` is deliberate: it must never reach the store.
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_dir": self.run_dir,
            "started_at": self.started_at,
            "pid": 4242,
        }


class _FakeRunner:
    def __init__(
        self,
        clock: list[int],
        *,
        outcomes: tuple[RunOutcome, ...] = (),
        start_error: Exception | None = None,
        poll_error: Exception | None = None,
    ) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.start_error = start_error
        self.poll_error = poll_error
        self.starts = 0
        self.polls = 0

    async def start(self, job: ScheduledResearchJob) -> _FakeHandle:
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error
        return _FakeHandle(
            run_id=f"run-{self.starts:04d}", started_at=self.clock[0]
        )

    def poll(self, handle: _FakeHandle) -> RunOutcome | None:
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        return self.outcomes.pop(0) if self.outcomes else None


async def _prompt_dispatch(job: ScheduledResearchJob) -> None:
    raise AssertionError(f"prompt dispatch ran for {job.id}")


def _executor(
    store: ScheduledResearchJobStore,
    runner: object,
    clock: list[int],
    **overrides: object,
) -> ScheduledResearchExecutor:
    return ScheduledResearchExecutor(
        store,
        _prompt_dispatch,
        strategy_runner=runner,  # type: ignore[arg-type]
        now_fn=lambda: clock[0],
        **overrides,  # type: ignore[arg-type]
    )


# (a) -----------------------------------------------------------------------


def test_never_finishing_runner_starts_once_and_stays_running(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _FakeRunner(clock)  # poll always returns None
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        for now in (1000, 61_000, 121_000):
            clock[0] = now
            await executor.tick(now)

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert runner.starts == 1, "the RUNNING job was re-dispatched"
    assert saved.status is JobStatus.RUNNING
    assert saved.in_flight is not None
    assert set(saved.in_flight) == _IN_FLIGHT_KEYS, "a leaked pid reached the store"
    assert saved.in_flight["run_id"] == "run-0001"
    assert saved.in_flight["started_at"] == 1000
    assert [record.status for record in history.read_runs("job-1")] == [
        "skipped",
        "skipped",
    ], "an overlapped firing is recorded, never dispatched"


class _ObservingRunner(_FakeRunner):
    """Records what the store held at the moment the runner was awaited."""

    def __init__(self, clock: list[int], store: ScheduledResearchJobStore) -> None:
        super().__init__(clock)
        self._store = store
        self.at_start: ScheduledResearchJob | None = None

    async def start(self, job: ScheduledResearchJob) -> _FakeHandle:
        self.at_start = self._store.get(job.id)
        return await super().start(job)


def test_running_is_persisted_before_the_runner_is_awaited(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _ObservingRunner(clock, store)
    executor = _executor(store, runner, clock)

    asyncio.run(executor.tick(1000))

    assert runner.at_start is not None
    assert runner.at_start.status is JobStatus.RUNNING
    assert runner.at_start.in_flight is None


# (b) -----------------------------------------------------------------------


def test_poll_completed_writes_terminal_state_and_one_history_row(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _FakeRunner(
        clock,
        outcomes=(
            None,
            RunOutcome(status="completed", summary="done", run_dir="/runs/x"),
        ),
    )
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        clock[0] = 1000
        await executor.tick(1000)  # start + poll 1 -> None
        clock[0] = 2000
        await executor.tick(2000)  # poll 2 -> completed

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert runner.starts == 1
    assert runner.polls == 2
    assert saved.status is JobStatus.COMPLETED
    assert saved.in_flight is None
    assert saved.consecutive_failures == 0
    assert saved.failure_kind is None
    assert saved.next_run_at == 62_000
    assert saved.next_run_at > saved.last_run_at == 2000

    records = history.read_runs("job-1")
    assert len(records) == 1
    record = records[0]
    assert record.status == "completed"
    assert record.run_id == "run-0001"
    assert record.started_at == 1000
    assert record.finished_at == 2000
    assert record.strategy_version == "v1-abcdef12"


# (c) -----------------------------------------------------------------------


def test_restart_recovers_running_job_without_dispatch(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(
        _job(
            status=JobStatus.RUNNING,
            in_flight=_in_flight(run_id="run-crash", started_at=1000),
        )
    )
    clock = [50_000]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        executor.start()  # recover_stale_running runs synchronously here
        await executor.stop()

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert runner.starts == 0, "restart re-dispatched the job"
    assert runner.polls == 0
    assert saved.status is JobStatus.PENDING
    assert saved.in_flight is None
    assert saved.consecutive_failures == 1
    assert saved.next_run_at == 110_000
    assert saved.next_run_at > 50_000

    records = history.read_runs("job-1")
    assert len(records) == 1
    assert records[0].run_id == "run-crash"
    assert records[0].status == "failed"
    assert records[0].error_kind == "recovered_after_restart"
    assert records[0].started_at == 1000
    assert records[0].finished_at == 50_000


def test_restart_recovery_without_handle_writes_one_generated_record(
    tmp_path: Path, runtime_root: Path
) -> None:
    """The crash window between the RUNNING write and the handle write."""
    store = _store(tmp_path)
    store.upsert(_job(status=JobStatus.RUNNING))
    clock = [50_000]
    executor = _executor(store, _FakeRunner(clock), clock)

    assert executor.recover_stale_running() == 1

    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.PENDING
    records = history.read_runs("job-1")
    assert len(records) == 1
    assert _RUN_ID_RE.fullmatch(records[0].run_id)
    assert records[0].error_kind == "recovered_after_restart"
    assert records[0].started_at == 50_000
    assert records[0].finished_at == 50_000


# (d) -----------------------------------------------------------------------


class _DeletingRunner(_FakeRunner):
    def __init__(self, clock: list[int], store: ScheduledResearchJobStore) -> None:
        super().__init__(clock)
        self._store = store

    def poll(self, handle: _FakeHandle) -> RunOutcome | None:
        self.polls += 1
        assert self._store.delete("job-1") is True
        return RunOutcome(status="completed", summary="must be dropped")


def test_job_deleted_during_poll_stays_deleted_without_history(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _DeletingRunner(clock, store)
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        clock[0] = 1000
        await executor.tick(1000)
        clock[0] = 2000
        await executor.tick(2000)

    asyncio.run(scenario())

    assert runner.polls == 1
    assert store.get("job-1") is None, "the sweep resurrected a deleted job"
    assert not history.history_path("job-1").exists(), "the sweep recreated .jsonl"
    assert history.read_runs("job-1") == []


# (e) -----------------------------------------------------------------------


def test_timeout_fails_the_run_by_milliseconds(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(schedule="3600000", run_policy={"run_timeout_s": 60}))
    clock = [1000]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock, max_consecutive_failures=1)

    async def scenario() -> None:
        clock[0] = 1000
        await executor.tick(1000)  # start; poll 1
        clock[0] = 60_999
        await executor.tick(60_999)  # 59_999 ms elapsed -> still live
        clock[0] = 61_000
        await executor.tick(61_000)  # 60_000 ms elapsed -> timeout, no poll

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert runner.polls == 2, "a timed-out run was polled again"
    assert saved.status is JobStatus.FAILED
    assert saved.failure_kind == "strategy"
    assert saved.in_flight is None
    assert saved.next_run_at > 61_000

    records = history.read_runs("job-1")
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].error_kind == "timeout"
    assert records[0].started_at == 1000
    assert records[0].finished_at == 61_000


# (f) -----------------------------------------------------------------------


def test_executor_and_strategy_module_stay_within_their_line_budgets() -> None:
    executor_lines = len(
        (_AGENT_DIR / "src/scheduled_research/executor.py").read_text(
            encoding="utf-8"
        ).splitlines()
    )
    strategy_lines = len(
        (_AGENT_DIR / "src/scheduled_research/strategy_execution.py").read_text(
            encoding="utf-8"
        ).splitlines()
    )
    assert executor_lines <= 799
    assert strategy_lines <= 400


# (g) -----------------------------------------------------------------------


def test_completed_job_does_not_restart_on_later_ticks(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _FakeRunner(
        clock, outcomes=(RunOutcome(status="completed", summary="done"),)
    )
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        for now in (1000, 2000, 3000):
            clock[0] = now
            await executor.tick(now)

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert runner.starts == 1, "a COMPLETED job was started again"
    assert runner.polls == 1, "a terminal job was polled twice"
    assert saved.status is JobStatus.COMPLETED
    assert saved.next_run_at == 61_000
    assert saved.next_run_at > saved.last_run_at == 1000
    assert len(history.read_runs("job-1")) == 1


# Failure paths --------------------------------------------------------------


def test_start_failure_counts_and_caps_at_the_budget(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _FakeRunner(clock, start_error=RuntimeError("boom"))
    executor = _executor(store, runner, clock, max_consecutive_failures=2)

    async def scenario() -> None:
        clock[0] = 1000
        await executor.tick(1000)
        first = store.get("job-1")
        assert first is not None
        assert first.status is JobStatus.PENDING
        assert first.consecutive_failures == 1
        assert first.failure_kind == "strategy"
        assert "boom" in (first.last_error or "")
        assert first.next_run_at == 61_000
        clock[0] = first.next_run_at
        await executor.tick(first.next_run_at)

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert saved.consecutive_failures == 2
    assert saved.in_flight is None
    records = history.read_runs("job-1")
    assert [record.status for record in records] == ["failed", "failed"]
    assert {record.error_kind for record in records} == {"runner_error"}


def test_missing_strategy_runner_fails_closed(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    executor = _executor(store, None, clock, max_consecutive_failures=1)

    asyncio.run(executor.tick(1000))

    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert saved.failure_kind == "strategy"
    assert saved.consecutive_failures == 1
    assert saved.in_flight is None
    records = history.read_runs("job-1")
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].error_kind == "runner_error"


def test_poll_error_records_a_runner_failure(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _FakeRunner(clock, poll_error=RuntimeError("poll boom"))
    executor = _executor(store, runner, clock, max_consecutive_failures=1)

    asyncio.run(executor.tick(1000))

    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert "poll boom" in (saved.last_error or "")
    records = history.read_runs("job-1")
    assert len(records) == 1
    assert records[0].error_kind == "runner_error"


class _ReplacingRunner(_FakeRunner):
    def __init__(
        self, clock: list[int], store: ScheduledResearchJobStore
    ) -> None:
        super().__init__(clock)
        self._store = store

    def poll(self, handle: _FakeHandle) -> RunOutcome | None:
        self.polls += 1
        self._store.upsert(
            _job(
                strategy_version="v2-deadbeef",
                created_at=999,
                status=JobStatus.PENDING,
                next_run_at=10**15,
            )
        )
        return RunOutcome(status="completed", summary="must be dropped")


def test_replaced_attempt_drops_the_poll_write(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [1000]
    runner = _ReplacingRunner(clock, store)
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        clock[0] = 1000
        await executor.tick(1000)
        clock[0] = 2000
        await executor.tick(2000)

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert saved.strategy_version == "v2-deadbeef"
    assert saved.status is JobStatus.PENDING
    assert saved.last_run_at is None
    assert history.read_runs("job-1") == []
