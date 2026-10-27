"""Production wiring for scheduled strategy runs (code and workflow paths).

The runtime runner is what makes ``executor_kind="strategy"`` jobs execute at
all, so these tests pin the composition instead of the individual runners:

(a) the route singleton constructs the executor with
    ``strategy_runner=resolve_strategy_runner()``;
(b) the resulting executor holds a non-None runner from
    ``src.strategy_library.runner``;
(c) a code job reaches ``completed`` through that production executor, the real
    runner and a real child backtest in a temp home;
(d) a workflow job dispatches exactly one session whose persisted allowlist is
    the strategy set, whose registry excludes ``trading_*`` and whose prompt is
    the six-section plan;
(e) an executor built without a runner keeps failing a due strategy job closed
    with ``failure_kind="strategy"``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.tools as tools_module
from src.api import scheduled_routes
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.scheduled_research.strategy_execution import InFlightHandle
from src.session.events import EventBus
from src.session.service import SessionService
from src.session.store import SessionStore
from src.strategy_library import history
from src.strategy_library import runner as runner_module
from src.strategy_library.execute_workflow import PROMPT_SECTIONS
from src.strategy_library.paths import (
    version_code_path,
    version_config_path,
    version_spec_path,
)
from src.strategy_library.spec import ALLOWED_STRATEGY_TOOLS
from src.strategy_store import _shared as strategy_store_shared
from src.strategy_store.models import Artifact, ArtifactType
from src.strategy_store.sqlite_store import SqliteStrategyStore

_AGENT_DIR = Path(__file__).resolve().parents[1]
_CODE_ID = "buyhold1"
_WORKFLOW_ID = "flowscan1"
_VERSION = "v1-abc12345"
_JOB_ID = "job-runner-1"
_CODE_POLL_TIMEOUT_S = 120.0
_BARS = 20

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

_WORKFLOW_SPEC = {
    "name": "Flow Scan",
    "kind": "workflow",
    "thesis": "Follow sustained capital inflow.",
    "universe": "cn_a",
    "inputs": [],
    "preconditions": ["the market is open"],
    "steps": [
        {"id": "flow", "action": "load fund flow", "tool": "get_fund_flow", "output": "flows"},
        {
            "id": "rank",
            "action": "rank by five-day inflow",
            "tool": "factor_analysis",
            "output": "ranking",
        },
        {
            "id": "confirm",
            "action": "confirm with price strength",
            "tool": "technical_indicators",
            "output": "signals",
        },
    ],
    "output_contract": {
        "format": "json",
        "required_fields": ["signal"],
        "destination": "session",
    },
    "failure_handling": "Stop at the failed step and report its id.",
}

_WORKFLOW_REPLY = json.dumps({"signal": "buy"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a temp dir the executor and any child process share."""
    home = tmp_path / "home"
    (home / ".vibe-trading").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("VIBE_TRADING_HOME", raising=False)
    return home


@pytest.fixture()
def strategy_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SqliteStrategyStore:
    """Bind the process store singleton to a throwaway database."""
    db = SqliteStrategyStore(db_path=tmp_path / "strategy_store.db")
    monkeypatch.setattr(strategy_store_shared, "_store", db)
    return db


@pytest.fixture()
def production_executor(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> ScheduledResearchExecutor:
    """Build the executor exactly as the FastAPI route singleton does."""
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", None)
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_executor", None)
    executor = scheduled_routes._get_scheduled_research_executor()
    assert isinstance(executor, ScheduledResearchExecutor)
    return executor


def _register(
    db: SqliteStrategyStore, *, strategy_id: str, kind: str, name: str
) -> str:
    """Insert the strategy row whose ``kind`` the runner dispatches on."""
    return db.register_artifact(
        Artifact(
            id=strategy_id,
            type=ArtifactType.STRATEGY,
            name=name,
            universe="LOCAL",
            kind=kind,
            artifact_version=_VERSION,
            readiness="ready",
        )
    )


def _job(strategy_id: str, *, job_id: str = _JOB_ID) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt="",
        schedule="60000",
        source_type="strategy",
        executor_kind="strategy",
        strategy_id=strategy_id,
        strategy_version=_VERSION,
        next_run_at=1000,
        created_at=0,
    )


def _write_local_bars(home: Path) -> None:
    """Write the 20 daily bars and the data-bridge config the loader reads."""
    data_dir = home / ".vibe-trading" / "data-bridge"
    data_dir.mkdir(parents=True, exist_ok=True)
    bars_path = data_dir / "bars.csv"
    lines = ["date,open,high,low,close,volume"]
    price = 100.0
    start = dt.date(2024, 1, 1)
    for offset in range(_BARS):
        day = (start + dt.timedelta(days=offset)).isoformat()
        lines.append(
            f"{day},{price},{price * 1.01:.4f},{price * 0.99:.4f},{price},1000"
        )
        price += 1.0
    bars_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (data_dir / "config.yaml").write_text(
        "sources:\n"
        "  - symbol: LOCAL1\n"
        "    type: csv\n"
        f'    path: "{bars_path}"\n',
        encoding="utf-8",
    )


def _write_code_snapshot(home: Path) -> None:
    """Publish the config and signal engine of a code version snapshot."""
    root = home / ".vibe-trading"
    config_path = version_config_path(_CODE_ID, _VERSION, runtime_root=root)
    code_path = version_code_path(_CODE_ID, _VERSION, runtime_root=root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(_LOCAL_CONFIG), encoding="utf-8")
    code_path.write_text(_BUY_AND_HOLD, encoding="utf-8")


def _write_workflow_snapshot(home: Path) -> None:
    path = version_spec_path(_WORKFLOW_ID, _VERSION, runtime_root=home / ".vibe-trading")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_WORKFLOW_SPEC), encoding="utf-8")


# ---------------------------------------------------------------------------
# Session/agent stubs (fake LLM, captured registry and prompt)
# ---------------------------------------------------------------------------


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


class _CapturingLoop:
    """AgentLoop stand-in that records the registry and prompt it received."""

    prompts: list[str] = []
    registries: list = []

    def __init__(
        self,
        *,
        registry,
        llm,
        event_callback,
        max_iterations,
        persistent_memory,
        cancel_event=None,
    ) -> None:
        del llm, event_callback, max_iterations, persistent_memory, cancel_event
        _CapturingLoop.registries.append(registry)

    def run(self, *, user_message: str, history, session_id: str) -> dict[str, str]:
        del history, session_id
        _CapturingLoop.prompts.append(user_message)
        return {"status": "success", "content": _WORKFLOW_REPLY}


def _install_agent_stubs(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Replace the LLM/loop and record every ``build_registry`` call."""
    _CapturingLoop.prompts = []
    _CapturingLoop.registries = []
    real_build_registry = tools_module.build_registry

    def _recording_build_registry(**kwargs):
        captured.append(dict(kwargs))
        return real_build_registry(**kwargs)

    monkeypatch.setattr(tools_module, "build_registry", _recording_build_registry)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.agent.loop.AgentLoop", _CapturingLoop)
    monkeypatch.setattr(
        "src.config.loader.load_runtime_agent_config",
        lambda overrides=None: SimpleNamespace(mcp_servers={}),
    )
    monkeypatch.setattr(
        "src.config.loader.sanitize_session_overrides",
        lambda overrides: dict(overrides),
    )
    monkeypatch.setattr(
        "src.config.accessor.get_env_config",
        lambda: SimpleNamespace(
            agent_tuning=SimpleNamespace(
                vibe_trading_agent_max_iterations=4,
                vibe_trading_enable_scheduler=False,
            )
        ),
    )


def _session_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SessionService:
    """Build a real SessionService whose files live under the temp dir."""
    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    return SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )


# ---------------------------------------------------------------------------
# (a) the production construction point
# ---------------------------------------------------------------------------


def test_scheduled_routes_singleton_passes_the_runtime_runner() -> None:
    source = (_AGENT_DIR / "src/api/scheduled_routes.py").read_text(encoding="utf-8")

    assert "strategy_runner=resolve_strategy_runner()" in source
    assert [
        line for line in source.splitlines() if "strategy_runner=" in line
    ], "the production executor is built without a strategy runner"


# ---------------------------------------------------------------------------
# (b) the executor holds the runtime runner
# ---------------------------------------------------------------------------


def test_production_executor_holds_a_runtime_runner(
    production_executor: ScheduledResearchExecutor,
) -> None:
    runner = production_executor._strategy_runner

    assert runner is not None, "the production executor fails every strategy job shut"
    assert type(runner) is runner_module.StrategyRunner
    assert type(runner).__module__ == "src.strategy_library.runner"
    assert runner_module.resolve_strategy_runner() is runner, "resolution is not idempotent"


# ---------------------------------------------------------------------------
# (c) a code job reaches completed through the production wiring
# ---------------------------------------------------------------------------


def test_code_job_completes_through_the_production_executor(
    fake_home: Path,
    strategy_db: SqliteStrategyStore,
    production_executor: ScheduledResearchExecutor,
) -> None:
    _write_local_bars(fake_home)
    _write_code_snapshot(fake_home)
    _register(strategy_db, strategy_id=_CODE_ID, kind="code", name="buy and hold")
    job = _job(_CODE_ID)
    production_executor._store.upsert(job)

    async def scenario() -> ScheduledResearchJob | None:
        now = 1000
        await production_executor.tick(now)
        started = production_executor._store.get(job.id)
        assert started is not None
        assert started.status is JobStatus.RUNNING
        assert started.in_flight is not None
        assert started.in_flight["kind"] == "code"
        deadline = time.monotonic() + _CODE_POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            saved = production_executor._store.get(job.id)
            if saved is not None and saved.status is not JobStatus.RUNNING:
                return saved
            await asyncio.sleep(0.1)
            now += 1000
            await production_executor.tick(now)
        return production_executor._store.get(job.id)

    saved = asyncio.run(scenario())

    assert saved is not None
    assert saved.status is JobStatus.COMPLETED, saved.last_error
    assert saved.failure_kind is None
    assert saved.in_flight is None
    assert saved.consecutive_failures == 0

    records = history.read_runs(job.id)
    assert [record.status for record in records] == ["completed"]
    assert records[0].run_dir is not None
    assert Path(records[0].run_dir).is_relative_to(fake_home)
    assert (Path(records[0].run_dir) / "artifacts" / "equity.csv").is_file()


# ---------------------------------------------------------------------------
# (d) a workflow job dispatches one allowlisted, six-section session
# ---------------------------------------------------------------------------


def test_workflow_job_dispatches_the_allowlisted_six_section_prompt(
    fake_home: Path,
    strategy_db: SqliteStrategyStore,
    production_executor: ScheduledResearchExecutor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_workflow_snapshot(fake_home)
    _register(strategy_db, strategy_id=_WORKFLOW_ID, kind="workflow", name="flow scan")
    service = _session_service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner_module, "_SESSION_SERVICE_FACTORY", lambda: service
    )
    captured: list[dict] = []
    _install_agent_stubs(monkeypatch, captured)

    job = _job(_WORKFLOW_ID)
    production_executor._store.upsert(job)

    async def scenario() -> ScheduledResearchJob | None:
        await production_executor.tick(1000)
        started = production_executor._store.get(job.id)
        assert started is not None
        assert started.status is JobStatus.RUNNING
        assert started.in_flight is not None
        assert started.in_flight["kind"] == "workflow"
        session_id = started.in_flight["session_id"]
        assert session_id
        task = service._active_tasks.get(session_id)
        assert task is not None, "the workflow dispatch never enqueued an attempt"
        await asyncio.wait_for(asyncio.shield(task), timeout=60)
        await production_executor.tick(2000)
        return production_executor._store.get(job.id)

    saved = asyncio.run(scenario())

    assert saved is not None
    assert saved.status is JobStatus.COMPLETED, saved.last_error
    assert saved.failure_kind is None
    assert saved.in_flight is None

    session_id = history.read_runs(job.id)[0].session_id
    assert session_id is not None
    session = service.store.get_session(session_id)
    assert session is not None
    assert session.title.startswith(f"strategy:{_WORKFLOW_ID}:{_VERSION}:")
    assert session.tool_allowlist == sorted(ALLOWED_STRATEGY_TOOLS)
    assert "tool_allowlist" not in session.config
    assert not [
        value
        for value in session.config.values()
        if isinstance(value, (list, tuple, set, frozenset))
    ]

    assert len(captured) == 1, "the workflow dispatch built more than one registry"
    assert captured[0]["tool_allowlist"] == ALLOWED_STRATEGY_TOOLS
    registry = _CapturingLoop.registries[0]
    assert set(registry.tool_names) == set(ALLOWED_STRATEGY_TOOLS)
    assert not [name for name in registry.tool_names if name.startswith("trading_")]

    assert len(_CapturingLoop.prompts) == 1
    prompt = _CapturingLoop.prompts[0]
    positions = [prompt.index(heading) for heading in PROMPT_SECTIONS]
    assert positions == sorted(positions), "the prompt sections are out of order"
    assert prompt.startswith("## Role")
    assert prompt.rstrip().endswith("Stop at the failed step and report its id.")


# ---------------------------------------------------------------------------
# Poll totality (the executor sweep calls it unguarded)
# ---------------------------------------------------------------------------


def test_poll_turns_an_unknown_kind_into_a_runner_failure(fake_home: Path) -> None:
    handle = InFlightHandle(
        run_id="run-unknown",
        kind="bogus",
        session_id=None,
        run_dir=None,
        started_at=1000,
    )

    outcome = runner_module.StrategyRunner().poll(handle)

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_kind == "runner_error"
    assert "bogus" in outcome.error


# ---------------------------------------------------------------------------
# (e) no runner still fails closed
# ---------------------------------------------------------------------------


def test_executor_without_a_runner_fails_a_strategy_job_closed(
    fake_home: Path, tmp_path: Path
) -> None:
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    store.upsert(_job(_CODE_ID))

    async def _prompt_dispatch(job: ScheduledResearchJob) -> None:
        raise AssertionError(f"prompt dispatch ran for {job.id}")

    executor = ScheduledResearchExecutor(
        store, _prompt_dispatch, max_consecutive_failures=1
    )

    async def scenario() -> None:
        await executor.tick(1000)

    asyncio.run(scenario())

    saved = store.get(_JOB_ID)
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert saved.failure_kind == "strategy"
    assert saved.in_flight is None
    assert "not configured" in (saved.last_error or "")
    records = history.read_runs(_JOB_ID)
    assert [record.status for record in records] == ["failed"]
    assert [record.error_kind for record in records] == ["runner_error"]
