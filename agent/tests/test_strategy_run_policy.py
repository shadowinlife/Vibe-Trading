"""Overlap, missed-slot, retention and run-now policy for strategy jobs.

Every scenario drives the real :class:`ScheduledResearchExecutor` with an
injected clock and a fake runner, so the tick composition (recovery →
dispatch → sweep) is what is proven rather than the helpers in isolation.

(a) the four run statuses — completed, failed, skipped, missed — each append
    exactly one history row;
(b) a skipped firing leaves the live run, the job status and the failure
    budget untouched;
(c) an overslept clock records one ``missed`` row and never backfills;
(d) ``retention=5`` keeps exactly five rows, for completed and skipped runs;
(e) ``run_now`` persists ``next_run_at`` before it wakes, refuses a RUNNING
    job with ``StrategyRunConflict`` and writes no row;
(f) a tick and a ``run_now`` never dispatch the same job twice, in either
    order;
(g) a restart of a RUNNING, overdue job yields one ``recovered_after_restart``
    row and no ``missed`` row;
(h) a completed job advances past ``now`` and three ticks start it only once.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduled_research.strategy_execution import StrategyRunConflict, run_now
from src.strategy_library import history
from src.strategy_library.history import RunOutcome

_TICK_MS = 60_000
_FIRST_SLOT = 1000


@pytest.fixture()
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point strategy run history at a per-test runtime root."""
    root = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
    return root


def _store(tmp_path: Path, name: str = "jobs.json") -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / name)


def _job(**overrides: object) -> ScheduledResearchJob:
    fields: dict[str, object] = {
        "id": "job-1",
        "prompt": "",
        "schedule": str(_TICK_MS),
        "source_type": "strategy",
        "executor_kind": "strategy",
        "strategy_id": "momentum-1",
        "strategy_version": "v1-abcdef12",
        "next_run_at": _FIRST_SLOT,
        "created_at": 0,
    }
    fields.update(overrides)
    return ScheduledResearchJob(**fields)  # type: ignore[arg-type]


def _in_flight(run_id: str = "run-0001", started_at: int = _FIRST_SLOT) -> dict[str, object]:
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
        return _in_flight(self.run_id, self.started_at)


class _FakeRunner:
    """Starts immediately; ``poll`` replays the scripted outcomes once each."""

    def __init__(
        self, clock: list[int], *, outcomes: tuple[RunOutcome, ...] = ()
    ) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.starts = 0
        self.polls = 0

    async def start(self, job: ScheduledResearchJob) -> _FakeHandle:
        self.starts += 1
        return _FakeHandle(run_id=f"run-{self.starts:04d}", started_at=self.clock[0])

    def poll(self, handle: _FakeHandle) -> RunOutcome | None:
        self.polls += 1
        return self.outcomes.pop(0) if self.outcomes else None


class _GatedRunner(_FakeRunner):
    """Blocks inside ``start`` until the scenario releases it."""

    def __init__(self, clock: list[int]) -> None:
        super().__init__(clock)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, job: ScheduledResearchJob) -> _FakeHandle:
        self.starts += 1
        self.entered.set()
        await self.release.wait()
        return _FakeHandle(run_id=f"run-{self.starts:04d}", started_at=self.clock[0])


async def _prompt_dispatch(job: ScheduledResearchJob) -> None:
    raise AssertionError(f"prompt dispatch ran for {job.id}")


def _executor(
    store: ScheduledResearchJobStore,
    runner: object,
    clock: list[int],
    **overrides: object,
) -> ScheduledResearchExecutor:
    options: dict[str, object] = {"max_consecutive_failures": 3}
    options.update(overrides)
    return ScheduledResearchExecutor(
        store,
        _prompt_dispatch,
        strategy_runner=runner,  # type: ignore[arg-type]
        now_fn=lambda: clock[0],
        **options,  # type: ignore[arg-type]
    )


async def _tick(
    executor: ScheduledResearchExecutor, clock: list[int], now_ms: int
) -> None:
    clock[0] = now_ms
    await executor.tick(now_ms)


def _statuses(job_id: str) -> list[str]:
    return [record.status for record in history.read_runs(job_id)]


# (a) -----------------------------------------------------------------------


def test_four_run_statuses_are_each_recorded_once(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(id="job-a"))
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(
        clock,
        outcomes=(
            RunOutcome(status="completed", summary="done"),
            RunOutcome(status="failed", error="boom", error_kind="runner_error"),
        ),
    )
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        await _tick(executor, clock, _FIRST_SLOT)
        await _tick(executor, clock, 2 * _TICK_MS + _FIRST_SLOT)
        await _tick(executor, clock, 3 * _TICK_MS + _FIRST_SLOT)
        await _tick(executor, clock, 4 * _TICK_MS + _FIRST_SLOT)

    asyncio.run(scenario())

    assert _statuses("job-a") == ["skipped", "failed", "completed"]

    missed_store = _store(tmp_path, name="missed.json")
    missed_store.upsert(_job(id="job-b"))
    missed_clock = [_FIRST_SLOT]
    missed_runner = _FakeRunner(missed_clock)
    missed_executor = _executor(missed_store, missed_runner, missed_clock)

    asyncio.run(_tick(missed_executor, missed_clock, _FIRST_SLOT + 4 * _TICK_MS))

    assert _statuses("job-b") == ["missed"]
    assert missed_runner.starts == 0, "a missed slot was dispatched anyway"


# (b) -----------------------------------------------------------------------


def test_skipped_firing_leaves_the_live_run_untouched(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock)  # poll never finishes
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        await _tick(executor, clock, _FIRST_SLOT)
        first = store.get("job-1")
        assert first is not None and first.in_flight is not None
        live_handle = dict(first.in_flight)

        await _tick(executor, clock, _FIRST_SLOT + _TICK_MS)
        skipped = store.get("job-1")
        assert skipped is not None
        assert skipped.in_flight == live_handle, "the in-flight handle moved"
        assert skipped.status is JobStatus.RUNNING
        assert skipped.consecutive_failures == 0
        assert skipped.failure_kind is None
        assert skipped.last_run_at is None
        assert skipped.next_run_at == _FIRST_SLOT + 2 * _TICK_MS

        await _tick(executor, clock, _FIRST_SLOT + _TICK_MS + 1)

    asyncio.run(scenario())

    assert runner.starts == 1, "the RUNNING job was dispatched twice"
    assert _statuses("job-1") == ["skipped"]
    saved = store.get("job-1")
    assert saved is not None
    assert saved.next_run_at == _FIRST_SLOT + 2 * _TICK_MS, "an early tick re-advanced"


# (c) -----------------------------------------------------------------------


def test_overslept_clock_records_one_missed_slot(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock)
    three_slots_late = _FIRST_SLOT + 4 * _TICK_MS

    async def scenario() -> None:
        await _tick(executor, clock, three_slots_late)
        await _tick(executor, clock, three_slots_late)

    asyncio.run(scenario())

    assert _statuses("job-1") == ["missed"], "a clock jump was backfilled"
    assert runner.starts == 0, "an overdue slot ran instead of being missed"
    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.PENDING
    assert saved.consecutive_failures == 0
    assert saved.last_run_at is None
    assert saved.next_run_at == three_slots_late + _TICK_MS


# (d) -----------------------------------------------------------------------


def _run_six_ticks(executor: ScheduledResearchExecutor, clock: list[int]) -> None:
    async def scenario() -> None:
        for step in range(6):
            await _tick(executor, clock, _FIRST_SLOT + step * _TICK_MS)

    asyncio.run(scenario())


def test_retention_five_keeps_five_rows_after_six_completed_runs(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(run_policy={"retention": 5}))
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock, outcomes=tuple(RunOutcome(status="completed") for _ in range(6)))
    executor = _executor(store, runner, clock)

    _run_six_ticks(executor, clock)

    assert runner.starts == 6
    assert len(history.read_runs("job-1")) == 5
    assert _statuses("job-1") == ["completed"] * 5


def test_retention_five_keeps_five_skipped_rows(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(run_policy={"retention": 5}))
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock)  # never finishes, so every later slot overlaps
    executor = _executor(store, runner, clock)

    _run_six_ticks(executor, clock)

    assert runner.starts == 1
    assert len(history.read_runs("job-1")) == 5


# (e) -----------------------------------------------------------------------


def test_run_now_writes_the_mark_before_it_wakes(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(next_run_at=10**12))
    observed: list[int] = []

    def wake() -> None:
        current = store.get("job-1")
        assert current is not None
        observed.append(current.next_run_at)

    marked = run_now("job-1", store=store, now_fn=lambda: 5_000, wake=wake)

    assert observed == [5_000], "the scheduler was woken before the mark landed"
    assert marked.next_run_at == 5_000
    saved = store.get("job-1")
    assert saved is not None
    assert saved.next_run_at == 5_000
    assert saved.status is JobStatus.PENDING


def test_run_now_refuses_a_running_job_and_writes_no_row(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock)
    woken: list[bool] = []

    asyncio.run(_tick(executor, clock, _FIRST_SLOT))
    before = store.get("job-1")
    assert before is not None and before.status is JobStatus.RUNNING

    with pytest.raises(StrategyRunConflict):
        run_now(
            "job-1",
            store=store,
            now_fn=lambda: _FIRST_SLOT + _TICK_MS,
            wake=lambda: woken.append(True),
        )

    after = store.get("job-1")
    assert after is not None
    assert after.next_run_at == before.next_run_at, "a refused run-now moved the schedule"
    assert after.in_flight == before.in_flight
    assert woken == [], "a refused run-now woke the scheduler"
    assert history.read_runs("job-1") == []


def test_run_now_rejects_an_unknown_job(tmp_path: Path, runtime_root: Path) -> None:
    with pytest.raises(LookupError):
        run_now("no-such-job", store=_store(tmp_path), now_fn=lambda: 1)


# (f) -----------------------------------------------------------------------


def test_run_now_then_tick_starts_the_job_once(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(next_run_at=10**12))
    clock = [5_000]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock)

    run_now("job-1", store=store, now_fn=lambda: 5_000, wake=lambda: None)

    async def scenario() -> None:
        await _tick(executor, clock, 5_000)
        await _tick(executor, clock, 5_001)

    asyncio.run(scenario())

    assert runner.starts == 1
    assert history.read_runs("job-1") == []


def test_tick_in_flight_and_run_now_never_double_start(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [_FIRST_SLOT]
    runner = _GatedRunner(clock)
    executor = _executor(store, runner, clock)
    woken: list[bool] = []

    async def scenario() -> None:
        task = asyncio.create_task(_tick(executor, clock, _FIRST_SLOT))
        await runner.entered.wait()  # RUNNING is persisted before this point

        with pytest.raises(StrategyRunConflict):
            run_now(
                "job-1",
                store=store,
                now_fn=lambda: _FIRST_SLOT,
                wake=lambda: woken.append(True),
            )

        runner.release.set()
        await task

    asyncio.run(scenario())

    assert runner.starts == 1, "the tick and run-now both dispatched the job"
    assert woken == []
    assert history.read_runs("job-1") == []


# (g) -----------------------------------------------------------------------


def test_restart_of_running_overdue_job_records_one_recovered_row(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job(status=JobStatus.RUNNING, in_flight=_in_flight()))
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock)
    executor = _executor(store, runner, clock)
    overdue = _FIRST_SLOT + 4 * _TICK_MS

    asyncio.run(_tick(executor, clock, overdue))

    records = history.read_runs("job-1")
    assert [record.status for record in records] == ["failed"]
    assert records[0].error_kind == "recovered_after_restart"
    assert runner.starts == 0, "the recovered job was dispatched"
    saved = store.get("job-1")
    assert saved is not None
    assert saved.in_flight is None
    assert saved.next_run_at > overdue


# (h) -----------------------------------------------------------------------


def test_completed_job_advances_past_now_and_three_ticks_start_once(
    tmp_path: Path, runtime_root: Path
) -> None:
    store = _store(tmp_path)
    store.upsert(_job())
    clock = [_FIRST_SLOT]
    runner = _FakeRunner(clock, outcomes=(RunOutcome(status="completed", summary="done"),))
    executor = _executor(store, runner, clock)

    async def scenario() -> None:
        for now in (_FIRST_SLOT, 2_000, 3_000):
            await _tick(executor, clock, now)

    asyncio.run(scenario())

    saved = store.get("job-1")
    assert saved is not None
    assert saved.status is JobStatus.COMPLETED
    assert saved.next_run_at == _FIRST_SLOT + _TICK_MS
    assert saved.next_run_at > 3_000
    assert runner.starts == 1
    assert _statuses("job-1") == ["completed"]
