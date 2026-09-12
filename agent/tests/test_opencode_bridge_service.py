"""Contract tests for OpencodeSessionService (work-plan T5).

Runs the real bridge service against a stub translator (the binding
``EventTranslatorLike`` Protocol — T4's concrete translator lands in parallel
and must structurally satisfy it) and a fake EngineDriver. No live opencode
serve (live E2E is T7), no network. Ports the restart-recovery suite shape
(``test_session_restart_recovery.py``) and the stub-service shape
(``test_api_live_runtime.py:355``); the IM-relay and OpenBB consumption
shapes live in ``test_opencode_bridge_service_consumers.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.config.paths import get_uploads_dir
from src.opencode_bridge.errors import (
    EnginePresumedDeadError,
    OpencodeConnectionError,
)
from src.opencode_bridge.events import OpencodeEvent
from src.opencode_bridge.service import OpencodeSessionService
from src.session.events import EventBus, SSEEvent
from src.session.models import Attempt, AttemptStatus, Message, Session
from src.session.service import SessionBusyError
from src.session.store import SessionStore


@dataclass
class StubVtEvent:
    """VtEventLike-shaped canned event."""

    type: str
    data: Dict[str, Any]


class StubTranslator:
    """Canned-event translator satisfying EventTranslatorLike structurally."""

    def __init__(self) -> None:
        self.fed: List[OpencodeEvent] = []
        self.noted_attempts: List[Tuple[str, str]] = []
        self.noted_aborts: List[str] = []
        self.closed = False
        self.auto_terminal: Optional[Tuple[str, Dict[str, Any]]] = None
        self._out: Optional[asyncio.Queue] = None

    async def feed(self, event: OpencodeEvent) -> None:
        self.fed.append(event)

    def events(self):
        return self._stream()

    async def _stream(self):
        while True:
            item = await self._queue().get()
            if item is None:
                return
            yield item

    def _queue(self) -> asyncio.Queue:
        if self._out is None:
            self._out = asyncio.Queue()
        return self._out

    def emit(self, event_type: str, **data: Any) -> None:
        self._queue().put_nowait(StubVtEvent(event_type, dict(data)))

    def note_attempt(self, session_id: str, attempt_id: str) -> None:
        self.noted_attempts.append((session_id, attempt_id))
        if self.auto_terminal is not None:
            event_type, extra = self.auto_terminal
            self.emit(event_type, attempt_id=attempt_id, **extra)

    def note_abort(self, session_id: str) -> None:
        self.noted_aborts.append(session_id)

    async def aclose(self) -> None:
        self.closed = True
        self._queue().put_nowait(None)


class FakeDriver:
    """EngineDriver stand-in: records primitives, serves a canned event stream."""

    def __init__(self) -> None:
        self.created_titles: List[str] = []
        self.prompts: List[Tuple[str, str]] = []
        self.aborts: List[str] = []
        self.closed = False
        self.prompt_error: Optional[Exception] = None
        self.create_gate: Optional[asyncio.Event] = None
        self._native: Optional[asyncio.Queue] = None
        self._counter = 0

    async def create_session(self, title: str = "") -> str:
        if self.create_gate is not None:
            await self.create_gate.wait()
        self._counter += 1
        self.created_titles.append(title)
        return f"ses_engine{self._counter}"

    async def prompt_async(self, session_id: str, text: str) -> None:
        if self.prompt_error is not None:
            raise self.prompt_error
        self.prompts.append((session_id, text))

    async def abort(self, session_id: str) -> None:
        self.aborts.append(session_id)

    async def aclose(self) -> None:
        self.closed = True

    def emit_native(self, event: OpencodeEvent) -> None:
        if self._native is None:
            self._native = asyncio.Queue()
        self._native.put_nowait(event)

    async def events(self):
        if self._native is None:
            self._native = asyncio.Queue()
        while True:
            item = await self._native.get()
            if item is None:
                return
            yield item


class DeadStreamDriver(FakeDriver):
    """events() dies immediately (auth failure / double consumer shape)."""

    async def events(self):
        raise RuntimeError("boom")
        yield  # pragma: no cover - makes this an async generator


#: Sentinel: emitting it makes PresumedDeadDriver.events() declare death.
_DEAD = object()


class PresumedDeadDriver(FakeDriver):
    """events() raises EnginePresumedDeadError on the _DEAD sentinel (T8-1).

    Re-iterable afterwards — mirrors the real driver, whose single-consumer
    slot frees when the generator raises, so ``_ensure_pumps`` can restart
    the stream when the engine returns.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stream_generations = 0

    async def events(self):
        self.stream_generations += 1
        if self._native is None:
            self._native = asyncio.Queue()
        while True:
            item = await self._native.get()
            if item is None:
                return
            if item is _DEAD:
                raise EnginePresumedDeadError("serve presumed dead (test)")
            yield item


class RecordingIndex:
    """FTS stand-in recording every index call (native _DummyIndex shape)."""

    def __init__(self) -> None:
        self.session_calls: List[Tuple[str, str]] = []
        self.message_calls: List[Tuple[str, str, str]] = []

    def index_session(self, session_id: str, title: str = "", ts=None) -> None:
        self.session_calls.append((session_id, title))

    def index_message(
        self, session_id: str, role: str, content: str, tool_name=None
    ) -> None:
        self.message_calls.append((session_id, role, content))


class BusRecorder:
    """Collects every published SSEEvent via the listener hook."""

    def __init__(self, bus: EventBus) -> None:
        self.events: List[SSEEvent] = []
        bus.add_listener(self.events.append)

    def types(self) -> List[str]:
        return [event.event_type for event in self.events]

    def by_type(self, event_type: str) -> List[SSEEvent]:
        return [e for e in self.events if e.event_type == event_type]


@dataclass
class Ctx:
    svc: OpencodeSessionService
    driver: FakeDriver
    translator: StubTranslator
    index: RecordingIndex
    bus: BusRecorder
    store: SessionStore


def make_ctx(tmp_path: Path, monkeypatch, **overrides: Any) -> Ctx:
    driver = overrides.get("driver") or FakeDriver()
    translator = overrides.get("translator") or StubTranslator()
    index = RecordingIndex()
    monkeypatch.setattr("src.opencode_bridge.service.get_shared_index", lambda: index)
    store = SessionStore(tmp_path / "sessions")
    svc = OpencodeSessionService(
        store=store,
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
        driver=driver,
        translator=translator,
    )
    return Ctx(
        svc=svc,
        driver=driver,
        translator=translator,
        index=index,
        bus=BusRecorder(svc.event_bus),
        store=store,
    )


async def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"condition not met within {timeout}s")


async def wait_for_reply(
    svc: OpencodeSessionService, session_id: str, attempt_id: str, timeout: float = 2.0
) -> Message:
    async def _find() -> Optional[Message]:
        for message in reversed(svc.get_messages(session_id, limit=50)):
            if message.role == "assistant" and message.linked_attempt_id == attempt_id:
                return message
        return None

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        reply = await _find()
        if reply is not None:
            return reply
        await asyncio.sleep(0.01)
    raise AssertionError(f"no assistant reply for attempt {attempt_id}")


COMPLETED_EXTRA: Dict[str, Any] = {
    "status": "completed",
    "summary": "engine answer",
    "elapsed_ms": 1234,
    "provider": "opencode",
    "model": "claude-test",
}


# ---------------------------------------------------------------------------
# Session CRUD (seam methods 1-4, 6)
# ---------------------------------------------------------------------------


def test_create_session_persists_indexes_and_emits(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path, monkeypatch)
    session = ctx.svc.create_session(title="chat", config={"channel": "websocket"})

    assert ctx.store.get_session(session.session_id) is not None
    assert ctx.index.session_calls == [(session.session_id, "chat")]
    created = ctx.bus.by_type("session.created")
    assert len(created) == 1
    assert created[0].data == {"session_id": session.session_id, "title": "chat"}
    assert ctx.svc.get_session(session.session_id).title == "chat"
    assert [s.session_id for s in ctx.svc.list_sessions()] == [session.session_id]


def test_delete_session_clears_bus_and_engine_mapping(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        session = ctx.svc.create_session(title="doomed")
        result = await ctx.svc.send_message(session.session_id, "hi")
        await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])
        engine_sid = ctx.translator.noted_attempts[0][0]

        assert ctx.svc.delete_session(session.session_id) is True
        assert ctx.svc.get_session(session.session_id) is None
        # buffered events were cleared (OpenBB adapter relies on this release)
        assert ctx.svc.event_bus.replay(session.session_id, replay_all=True) == []
        assert ctx.svc._engine_sessions == {}
        assert ctx.svc._vt_by_engine.get(engine_sid) is None
        assert ctx.svc.delete_session(session.session_id) is False

    asyncio.run(scenario())


def test_get_messages_delegates_with_limit(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path, monkeypatch)
    session = ctx.svc.create_session()
    for i in range(3):
        ctx.store.append_message(
            Message(session_id=session.session_id, role="user", content=f"m{i}")
        )
    assert [m.content for m in ctx.svc.get_messages(session.session_id, limit=2)] == [
        "m1",
        "m2",
    ]


# ---------------------------------------------------------------------------
# send_message happy path: full D6 contract
# ---------------------------------------------------------------------------


def test_send_message_roundtrip_full_contract(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session(title="roundtrip")
        sid = session.session_id

        result = await ctx.svc.send_message(sid, "analyze AAPL")
        assert set(result) == {"message_id", "attempt_id"}
        attempt_id = result["attempt_id"]

        await wait_until(lambda: bool(ctx.driver.prompts))
        engine_sid = ctx.translator.noted_attempts[0][0]
        assert ctx.translator.noted_attempts == [(engine_sid, attempt_id)]
        assert ctx.driver.prompts[0][0] == engine_sid

        ctx.translator.emit("text_delta", attempt_id=attempt_id, delta="Hello ", iter=1)
        ctx.translator.emit("text_delta", attempt_id=attempt_id, delta="world", iter=1)
        ctx.translator.emit(
            "reasoning_delta", attempt_id=attempt_id, tail="thinking", iter=1, chars=8
        )
        ctx.translator.emit(
            "tool_call",
            attempt_id=attempt_id,
            tool="get_market_data",
            arguments={"symbol": "AAPL"},
            call_id="call-1",
        )
        ctx.translator.emit(
            "tool_result",
            attempt_id=attempt_id,
            tool="get_market_data",
            status="ok",
            elapsed_ms=125,
            preview="AAPL 195.00",
            call_id="call-1",
        )
        # partial_response is checkpointed while streaming (native parity)
        await wait_until(
            lambda: ctx.store.get_partial_response(sid, attempt_id) is not None
        )
        partial = ctx.store.get_partial_response(sid, attempt_id)
        assert partial.startswith("Hello")

        ctx.translator.emit(
            "attempt.completed", attempt_id=attempt_id, **COMPLETED_EXTRA
        )
        reply = await wait_for_reply(ctx.svc, sid, attempt_id)

        # Reply Message contract (D6)
        assert reply.content == "engine answer"
        assert reply.linked_attempt_id == attempt_id
        assert reply.metadata["status"] == "completed"
        assert reply.metadata["elapsed_ms"] == 1234
        assert reply.metadata["provider"] == "opencode"
        assert reply.metadata["model"] == "claude-test"
        assert reply.tool_trail == [
            {
                "tool": "get_market_data",
                "status": "ok",
                "arguments": {"symbol": "AAPL"},
                "elapsed_ms": 125,
                "preview": "AAPL 195.00",
                "call_id": "call-1",
                "timestamp": reply.tool_trail[0]["timestamp"],
            }
        ]
        assert isinstance(reply.tool_trail[0]["timestamp"], int)

        # session.last_attempt_id maintenance (sessions_routes.py:806 reads it)
        reloaded = ctx.store.get_session(sid)
        assert reloaded.last_attempt_id == attempt_id
        assert reloaded.config["include_shell_tools"] is False

        # attempt record
        attempt = ctx.store.get_attempt(sid, attempt_id)
        assert attempt.status == AttemptStatus.COMPLETED
        assert attempt.summary == "engine answer"
        assert attempt.parent_attempt_id is None

        # partial cleaned up at terminal
        assert ctx.store.get_partial_response(sid, attempt_id) is None

        # event vocabulary + ordering (native parity)
        assert ctx.bus.types() == [
            "session.created",
            "message.received",
            "attempt.created",
            "attempt.started",
            "text_delta",
            "text_delta",
            "reasoning_delta",
            "tool_call",
            "tool_result",
            "attempt.completed",
        ]
        delta = ctx.bus.by_type("text_delta")[0]
        assert delta.session_id == sid
        assert delta.data["delta"] == "Hello "
        assert delta.data["attempt_id"] == attempt_id
        received = ctx.bus.by_type("message.received")[0].data
        assert received == {
            "message_id": result["message_id"],
            "role": "user",
            "content": "analyze AAPL",
        }
        assert ctx.bus.by_type("attempt.created")[0].data == {
            "attempt_id": attempt_id,
            "prompt": "analyze AAPL",
        }
        terminal = ctx.bus.by_type("attempt.completed")[0].data
        assert terminal["attempt_id"] == attempt_id
        assert terminal["status"] == "completed"
        assert terminal["summary"] == "engine answer"
        assert terminal["error"] is None
        assert terminal["run_dir"] is None
        assert terminal["elapsed_ms"] == 1234
        assert terminal["provider"] == "opencode"
        assert terminal["model"] == "claude-test"

        # FTS anchors: user message + assistant reply indexed
        assert (sid, "user", "analyze AAPL") in ctx.index.message_calls
        assert (sid, "assistant", "engine answer") in ctx.index.message_calls

        # engine session reuse + parent chain + busy release on a second turn
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        result2 = await ctx.svc.send_message(sid, "second turn")
        reply2 = await wait_for_reply(ctx.svc, sid, result2["attempt_id"])
        assert reply2.metadata["status"] == "completed"
        assert len(ctx.driver.created_titles) == 1  # engine session reused
        attempt2 = ctx.store.get_attempt(sid, result2["attempt_id"])
        assert attempt2.parent_attempt_id == attempt_id
        assert ctx.store.get_session(sid).last_attempt_id == result2["attempt_id"]

        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_terminal_run_dir_yields_run_id_and_metrics(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        run_dir = tmp_path / "runs" / "run_abc"
        (run_dir / "artifacts").mkdir(parents=True)
        (run_dir / "artifacts" / "metrics.csv").write_text(
            "sharpe,max_drawdown\n1.5,-0.2\n", encoding="utf-8"
        )
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "backtest it")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(ctx.driver.prompts))
        ctx.translator.emit(
            "attempt.completed",
            attempt_id=attempt_id,
            status="completed",
            summary="done",
            run_dir=str(run_dir),
            elapsed_ms=99,
        )
        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["run_id"] == "run_abc"
        assert reply.metadata["metrics"] == {"sharpe": 1.5, "max_drawdown": -0.2}
        terminal = ctx.bus.by_type("attempt.completed")[0].data
        assert terminal["run_dir"] == str(run_dir)
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_non_user_role_persists_without_attempt(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "noted", "assistant")
        assert set(result) == {"message_id"}
        messages = ctx.svc.get_messages(session.session_id)
        assert [(m.role, m.content) for m in messages] == [("assistant", "noted")]
        assert not ctx.bus.by_type("attempt.created")
        assert ctx.driver.prompts == []
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_send_message_unknown_session_raises_value_error(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            await ctx.svc.send_message("missing", "hello")
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Busy gate (409 semantics)
# ---------------------------------------------------------------------------


def test_concurrent_second_send_raises_native_session_busy_error(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session()
        first = await ctx.svc.send_message(session.session_id, "first")
        await wait_until(lambda: bool(ctx.driver.prompts))

        with pytest.raises(SessionBusyError):
            await ctx.svc.send_message(session.session_id, "second")

        # the rejected send persisted nothing (claim precedes the append)
        contents = [m.content for m in ctx.svc.get_messages(session.session_id)]
        assert contents == ["first"]

        ctx.translator.emit(
            "attempt.completed",
            attempt_id=first["attempt_id"],
            **COMPLETED_EXTRA,
        )
        await wait_for_reply(ctx.svc, session.session_id, first["attempt_id"])

        # claim released: the next send is admitted
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        second = await ctx.svc.send_message(session.session_id, "third")
        await wait_for_reply(ctx.svc, session.session_id, second["attempt_id"])
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Failure paths (D6/B-fix + T5 QA: no hang)
# ---------------------------------------------------------------------------


def test_prompt_acceptance_failure_fails_attempt_without_hang(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.driver.prompt_error = OpencodeConnectionError("serve unreachable")
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "do research")
        attempt_id = result["attempt_id"]

        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["status"] == "failed"
        assert reply.content.startswith("Execution failed:")
        assert "serve unreachable" in reply.content
        assert reply.linked_attempt_id == attempt_id
        assert reply.tool_trail == []

        attempt = ctx.store.get_attempt(session.session_id, attempt_id)
        assert attempt.status == AttemptStatus.FAILED
        assert "serve unreachable" in attempt.error

        failed = ctx.bus.by_type("attempt.failed")
        assert len(failed) == 1
        assert failed[0].data["attempt_id"] == attempt_id
        assert failed[0].data["status"] == "failed"
        assert "serve unreachable" in failed[0].data["error"]

        # claim released despite the failure
        ctx.driver.prompt_error = None
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        nxt = await ctx.svc.send_message(session.session_id, "retry")
        await wait_for_reply(ctx.svc, session.session_id, nxt["attempt_id"])
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_translator_failed_terminal_writes_status_metadata(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = (
            "attempt.failed",
            {"error": "engine exploded"},
        )
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "boom")
        reply = await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])
        assert reply.metadata["status"] == "failed"
        assert reply.content == "Execution failed: engine exploded"
        failed = ctx.bus.by_type("attempt.failed")[0].data
        assert failed["status"] == "failed"
        assert failed["error"] == "engine exploded"
        assert isinstance(failed["elapsed_ms"], int)
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_dead_event_stream_fails_pending_attempt(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch, driver=DeadStreamDriver())
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "orphan")
        reply = await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])
        assert reply.metadata["status"] == "failed"
        assert "engine event stream lost" in reply.content
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_engine_presumed_dead_fails_once_then_resumes_next_send(
    tmp_path, monkeypatch
) -> None:
    """T8-1 contract: death -> failed exactly once -> resume on next send.

    Given a running attempt and a T9-style vt_event_observer tap, when the
    driver's stream declares the engine dead mid-turn, then the attempt
    lands failed through the EXISTING _fail_all_pending path (one reply
    message, one attempt.failed bus event — no double terminal). When the
    engine returns, the NEXT send_message restarts the pump (existing
    _ensure_pumps path — no service rebuild, no gateway restart), the
    observer tap survives the restart, and the new turn completes.
    """

    async def scenario() -> None:
        driver = PresumedDeadDriver()
        ctx = make_ctx(tmp_path, monkeypatch, driver=driver)
        observed: List[str] = []

        async def observer(event: Any, vt_session_id: str) -> None:
            observed.append(event.type)

        ctx.svc.vt_event_observer = observer
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "long turn")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(driver.prompts))
        pump_before = ctx.svc._pump_task

        driver.emit_native(_DEAD)

        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["status"] == "failed"
        assert "engine event stream lost" in reply.content
        assert "presumed dead" in reply.content
        attempt = ctx.store.get_attempt(session.session_id, attempt_id)
        assert attempt.status == AttemptStatus.FAILED
        await wait_until(lambda: ctx.svc._pump_task.done())
        await asyncio.sleep(0.05)
        replies = [
            m
            for m in ctx.svc.get_messages(session.session_id)
            if m.linked_attempt_id == attempt_id
        ]
        assert len(replies) == 1  # exactly-once terminal
        assert len(ctx.bus.by_type("attempt.failed")) == 1

        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        result2 = await ctx.svc.send_message(session.session_id, "after return")
        attempt2 = result2["attempt_id"]
        reply2 = await wait_for_reply(ctx.svc, session.session_id, attempt2)
        assert reply2.metadata["status"] == "completed"
        assert ctx.svc._pump_task is not pump_before
        assert driver.stream_generations == 2
        assert "attempt.completed" in observed
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_after_engine_attach_aborts_and_cancels(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "long run")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(ctx.driver.prompts))
        engine_sid = ctx.translator.noted_attempts[0][0]

        assert ctx.svc.cancel_current(session.session_id) is True
        assert ctx.translator.noted_aborts == [engine_sid]
        await wait_until(lambda: ctx.driver.aborts == [engine_sid])

        ctx.translator.emit(
            "attempt.cancelled", attempt_id=attempt_id, error="cancelled by user"
        )
        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["status"] == "cancelled"
        assert reply.content == "Run cancelled."
        assert reply.tool_trail == []
        attempt = ctx.store.get_attempt(session.session_id, attempt_id)
        assert attempt.status == AttemptStatus.CANCELLED
        cancelled = ctx.bus.by_type("attempt.cancelled")[0].data
        assert cancelled["status"] == "cancelled"
        assert cancelled["attempt_id"] == attempt_id
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_cancel_before_engine_attach_cancels_task_and_persists(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.driver.create_gate = asyncio.Event()
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "gated")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: "attempt.started" in ctx.bus.types())
        task = ctx.svc._active_tasks[session.session_id]

        assert ctx.svc.cancel_current(session.session_id) is True
        ctx.driver.create_gate.set()
        await asyncio.gather(task, return_exceptions=True)

        attempt = ctx.store.get_attempt(session.session_id, attempt_id)
        assert attempt.status == AttemptStatus.CANCELLED
        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["status"] == "cancelled"
        assert ctx.bus.by_type("attempt.cancelled")
        # claim released
        assert session.session_id not in ctx.svc._inflight
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_cancel_without_active_attempt_returns_false(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path, monkeypatch)
    session = ctx.svc.create_session()
    assert ctx.svc.cancel_current(session.session_id) is False


def test_abort_transport_failure_forces_cancelled_terminal(
    tmp_path, monkeypatch
) -> None:
    class AbortsFail(FakeDriver):
        async def abort(self, session_id: str) -> None:
            raise OpencodeConnectionError("serve gone")

    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch, driver=AbortsFail())
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "cancel me")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(ctx.driver.prompts))

        assert ctx.svc.cancel_current(session.session_id) is True
        reply = await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        assert reply.metadata["status"] == "cancelled"
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# D8 prompt injection
# ---------------------------------------------------------------------------


def test_prompt_injection_prepended_transcript_stays_raw(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        session = ctx.svc.create_session(title="injected")
        result = await ctx.svc.send_message(session.session_id, "what is up")
        await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])

        engine_sid, prompt = ctx.driver.prompts[0]
        assert prompt.startswith("[gateway context]")
        assert f"vt_session_id={session.session_id}" in prompt
        assert f"session_id='{session.session_id}'" in prompt
        assert str(get_uploads_dir()) in prompt
        assert "uploads/<name>" in prompt
        assert prompt.endswith("what is up")

        # transcript keeps the RAW user content (D3 ownership)
        user_messages = [
            m for m in ctx.svc.get_messages(session.session_id) if m.role == "user"
        ]
        assert [m.content for m in user_messages] == ["what is up"]
        attempt = ctx.store.get_attempt(session.session_id, result["attempt_id"])
        assert attempt.prompt == "what is up"
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Restart recovery (ported test_session_restart_recovery.py shape)
# ---------------------------------------------------------------------------


def _seed_running_attempt(tmp_path: Path) -> Tuple[SessionStore, str, str]:
    store = SessionStore(tmp_path / "sessions")
    session = Session(title="restart")
    store.create_session(session)
    attempt = Attempt(session_id=session.session_id, prompt="analyze")
    attempt.mark_running()
    store.create_attempt(attempt)
    store.save_partial_response(
        session.session_id, attempt.attempt_id, "Partial answer"
    )
    return store, session.session_id, attempt.attempt_id


def test_restart_recovers_partial_reply_once(tmp_path, monkeypatch) -> None:
    store, sid, aid = _seed_running_attempt(tmp_path)
    ctx = make_ctx(tmp_path, monkeypatch)

    recovered = ctx.store.get_attempt(sid, aid)
    assert recovered.status == AttemptStatus.INTERRUPTED
    assert recovered.completed_at is not None
    replies = [m for m in ctx.store.get_messages(sid) if m.linked_attempt_id == aid]
    assert len(replies) == 1
    assert "Partial answer" in replies[0].content
    assert replies[0].metadata == {
        "status": "interrupted",
        "partial": True,
        "recovery_reason": "service_restart",
    }
    assert ctx.store.get_partial_response(sid, aid) is None
    assert (sid, "assistant", replies[0].content) in ctx.index.message_calls
    del store

    # startup reconciliation is safe to run repeatedly
    make_ctx(tmp_path, monkeypatch)
    replies = [
        m
        for m in SessionStore(tmp_path / "sessions").get_messages(sid)
        if m.linked_attempt_id == aid
    ]
    assert len(replies) == 1


def test_restart_recovers_pending_attempt_without_partial(
    tmp_path, monkeypatch
) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = Session(title="pending")
    store.create_session(session)
    attempt = Attempt(session_id=session.session_id, prompt="queued")
    store.create_attempt(attempt)

    ctx = make_ctx(tmp_path, monkeypatch)
    recovered = ctx.store.get_attempt(session.session_id, attempt.attempt_id)
    assert recovered.status == AttemptStatus.INTERRUPTED
    reply = ctx.store.get_messages(session.session_id)[-1]
    assert "no complete assistant response was saved" in reply.content
    assert reply.metadata["partial"] is False


# ---------------------------------------------------------------------------
# Event plumbing: pump, routing, drop policy, shutdown
# ---------------------------------------------------------------------------


def test_pump_feeds_translator_and_unknown_events_drop(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        await ctx.svc.start()
        native = OpencodeEvent(
            type="session.status",
            properties={"sessionID": "ses_engine1"},
            raw={"type": "session.status"},
        )
        ctx.driver.emit_native(native)
        await wait_until(lambda: bool(ctx.translator.fed))
        assert ctx.translator.fed[0] is native

        # an event matching no active attempt is dropped, not published
        ctx.translator.emit("text_delta", sessionID="ses_unknown", delta="ghost")
        await asyncio.sleep(0.05)
        assert not ctx.bus.by_type("text_delta")
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_events_route_by_session_id_fallbacks(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "route me")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(ctx.driver.prompts))
        engine_sid = ctx.translator.noted_attempts[0][0]

        # vt session_id fallback routing; attempt_id gets stamped (native parity)
        ctx.translator.emit(
            "text_delta", session_id=session.session_id, delta="via-vt-id"
        )
        # engine sessionID passthrough fallback routing
        ctx.translator.emit("text_delta", sessionID=engine_sid, delta="via-engine-id")
        await wait_until(lambda: len(ctx.bus.by_type("text_delta")) == 2)
        first, second = ctx.bus.by_type("text_delta")
        assert first.data["attempt_id"] == attempt_id
        assert second.data["attempt_id"] == attempt_id
        assert second.session_id == session.session_id

        ctx.translator.emit(
            "attempt.completed", attempt_id=attempt_id, **COMPLETED_EXTRA
        )
        await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_duplicate_terminal_event_is_dropped(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session()
        result = await ctx.svc.send_message(session.session_id, "once")
        attempt_id = result["attempt_id"]
        await wait_until(lambda: bool(ctx.driver.prompts))
        ctx.translator.emit(
            "attempt.completed", attempt_id=attempt_id, **COMPLETED_EXTRA
        )
        await wait_for_reply(ctx.svc, session.session_id, attempt_id)
        # a straggler terminal for the finished attempt must not re-persist
        ctx.translator.emit(
            "attempt.completed", attempt_id=attempt_id, **COMPLETED_EXTRA
        )
        await asyncio.sleep(0.05)
        assert len(ctx.bus.by_type("attempt.completed")) == 1
        replies = [
            m
            for m in ctx.svc.get_messages(session.session_id)
            if m.linked_attempt_id == attempt_id
        ]
        assert len(replies) == 1
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_aclose_closes_translator_and_driver(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        await ctx.svc.start()
        await ctx.svc.aclose()
        assert ctx.translator.closed
        assert ctx.driver.closed
        assert ctx.svc._pump_task is None
        assert ctx.svc._dispatch_task is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Stub-service consumption shape (test_api_live_runtime.py:355 / scheduled /
# live_routes: positional two-arg send, dict result, event_bus.emit)
# ---------------------------------------------------------------------------


def test_positional_send_shapes_from_scheduler_and_live_runner(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        # scheduled_routes.py:88 shape: two positional args, no kwargs
        session = ctx.svc.create_session(title="scheduled-research:job-1")
        result = await ctx.svc.send_message(session.session_id, "nightly briefing")
        assert isinstance(result, dict) and result.get("attempt_id")
        reply = await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])
        # scheduled_routes.py:109-115 reads metadata["status"] off the reply
        assert (reply.metadata or {}).get("status") == "completed"

        # live_routes.py:615 shape: create_session(title=...) + positional send
        live = ctx.svc.create_session(title="live-runner:robinhood")
        result2 = await ctx.svc.send_message(live.session_id, "check portfolio")
        await wait_for_reply(ctx.svc, live.session_id, result2["attempt_id"])
        # event_bus.emit stays callable for the live-action audit relay
        ctx.svc.event_bus.emit(live.session_id, "live.action", {"audit_id": "la_x"})
        assert ctx.bus.by_type("live.action")[0].data == {"audit_id": "la_x"}
        await ctx.svc.aclose()

    asyncio.run(scenario())
