"""Startup crash-recovery tests for the opencode bridge (work-plan T6).

Covers the three reconciliation branches (re-attach / backfill / interrupted),
the cascade lifecycle (delete -> engine DELETE, IM /new -> fresh engine
session), the acceptance assertion (replay=active never re-streams dead
turns, sessions_routes.py:806) and both plan QA scenarios:

* happy: gateway restart with an in-flight turn -> re-attach keeps streaming
  (proven twice: stub translator mechanics + a REAL EventTranslator replaying
  the T1 golden trace ``scenario_a_multi_tool.jsonl`` end to end);
* failure: opencode also died -> attempt lands interrupted and an IM-style
  polling loop (runtime.py ``_wait_for_reply`` shape) gets the terminal
  message before its timeout budget.

No live opencode serve (live E2E is T7): the engine is a stub driver in the
T5-test FakeDriver lineage (``messages`` + the ``_http`` client seam the
cascade DELETE uses); recorded T1 traces are read-only fixtures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.opencode_bridge.engine_state import TurnState, probe_turn
from src.opencode_bridge.errors import OpencodeConnectionError, OpencodeHttpError
from src.opencode_bridge.events import decode_event
from src.opencode_bridge.recovery import (
    ENGINE_SESSION_CONFIG_KEY,
    RecoverableOpencodeSessionService,
    delete_engine_session,
)
from src.opencode_bridge.tool_names import ToolNameMap
from src.opencode_bridge.translator import EventTranslator
from src.session.events import EventBus
from src.session.models import Attempt, AttemptStatus, Message, Session
from src.session.service import SessionBusyError
from src.session.store import SessionStore

from tests.test_opencode_bridge_service import (
    COMPLETED_EXTRA,
    BusRecorder,
    Ctx,
    FakeDriver,
    RecordingIndex,
    StubTranslator,
    wait_for_reply,
    wait_until,
)
from tests.test_opencode_bridge_translator_harness import load_trace, scenario_sid

# ---------------------------------------------------------------------------
# Stubs + builders
# ---------------------------------------------------------------------------

HEARTBEAT_FRAME = '{"type":"server.heartbeat","properties":{}}'

INJECTION = "[gateway context]\nvt_session_id=vt-x\n\nUploaded files: none\n\n"

TMAP = ToolNameMap(
    prefixed_to_bare={"vibe-trading_list_skills": "list_skills"},
    server_prefixes=("vibe-trading_",),
)


class StubHttpClient:
    """Stand-in for the driver's ``_http`` client seam (cascade DELETE)."""

    def __init__(self) -> None:
        self.requests: List[Tuple[str, str]] = []
        self.error: Optional[Exception] = None

    async def request_json(self, method, path, *, json_body=None):
        self.requests.append((method, path))
        if self.error is not None:
            raise self.error
        return True


class RecoveryDriver(FakeDriver):
    """T5 FakeDriver + the two concrete surfaces recovery reads/writes."""

    def __init__(self) -> None:
        super().__init__()
        self.messages_payload: Dict[str, Any] = {}
        self.messages_calls: List[str] = []
        self._http = StubHttpClient()

    async def messages(self, session_id: str):
        self.messages_calls.append(session_id)
        payload = self.messages_payload.get(session_id)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            raise OpencodeHttpError(
                "GET", f"/session/{session_id}/message", 404, "not found"
            )
        return payload

    def bare_tool_name(self, name: str) -> str:
        return name.removeprefix("vibe-trading_")


def user_entry(text: str, *, mid: str = "msg_user", created: int = 1_000) -> dict:
    return {
        "info": {"id": mid, "role": "user", "time": {"created": created}},
        "parts": [{"id": mid + "_p", "type": "text", "text": text, "messageID": mid}],
    }


def omo_entry(*, mid: str = "msg_omo", created: int = 1_500) -> dict:
    return user_entry(
        "[SYSTEM DIRECTIVE: OH-MY-OPENCODE] continue the task",
        mid=mid,
        created=created,
    )


def tool_part(
    *,
    tool: str,
    call_id: str,
    status: str = "completed",
    arguments: Optional[dict] = None,
    output: str = "",
    start: int = 1_000,
    end: int = 2_000,
) -> dict:
    state: Dict[str, Any] = {"status": status, "input": arguments or {}}
    if status == "running":
        state["time"] = {"start": start}
    else:
        state["time"] = {"start": start, "end": end}
        state["output"] = output
    return {
        "id": f"prt_{call_id}",
        "type": "tool",
        "tool": tool,
        "callID": call_id,
        "state": state,
    }


def assistant_entry(
    *,
    mid: str,
    created: int,
    completed: Optional[int] = None,
    finish: Optional[str] = None,
    text: str = "",
    provider: str = "alibaba-cn",
    model: str = "qwen3.8-max",
    extra_parts: Tuple[dict, ...] = (),
    summary: bool = False,
) -> dict:
    info: Dict[str, Any] = {
        "id": mid,
        "role": "assistant",
        "time": {"created": created},
        "providerID": provider,
        "modelID": model,
    }
    if completed is not None:
        info["time"]["completed"] = completed
    if finish is not None:
        info["finish"] = finish
    if summary:
        info["summary"] = True
    parts = list(extra_parts)
    if text:
        parts.append({"id": mid + "_t", "type": "text", "text": text, "messageID": mid})
    return {"info": info, "parts": parts}


def seed_crashed(
    tmp_path: Path,
    *,
    prompt: str = "analyze AAPL",
    partial: Optional[str] = None,
    engine_sid: Optional[str] = "ses_engine1",
    reply: Optional[Message] = None,
    status: str = "running",
    title: str = "crashed",
) -> Tuple[SessionStore, str, str]:
    """Seed the store exactly as a gateway crash mid-turn leaves it."""
    store = SessionStore(tmp_path / "sessions")
    session = Session(title=title)
    if engine_sid is not None:
        session.config[ENGINE_SESSION_CONFIG_KEY] = engine_sid
    store.create_session(session)
    attempt = Attempt(session_id=session.session_id, prompt=prompt)
    if status == "running":
        attempt.mark_running()
    store.create_attempt(attempt)
    session.last_attempt_id = attempt.attempt_id
    store.update_session(session)
    if partial is not None:
        store.save_partial_response(session.session_id, attempt.attempt_id, partial)
    if reply is not None:
        store.append_message(reply)
    return store, session.session_id, attempt.attempt_id


def make_recovery_ctx(
    tmp_path: Path,
    monkeypatch,
    driver=None,
    translator=None,
    store: Optional[SessionStore] = None,
) -> Ctx:
    driver = driver if driver is not None else RecoveryDriver()
    translator = translator if translator is not None else StubTranslator()
    index = RecordingIndex()
    monkeypatch.setattr("src.opencode_bridge.service.get_shared_index", lambda: index)
    store = store if store is not None else SessionStore(tmp_path / "sessions")
    svc = RecoverableOpencodeSessionService(
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


def route_replay_all(svc, session_id: str) -> bool:
    """Replica of the replay=active decision (sessions_routes.py:806)."""
    session = svc.get_session(session_id)
    if not session or not session.last_attempt_id:
        return False
    attempt = svc.store.get_attempt(session_id, session.last_attempt_id)
    status = getattr(attempt.status, "value", attempt.status) if attempt else None
    return status == "running"


async def im_poll_reply(svc, session_id: str, attempt_id: str, *, timeout_s: float):
    """Replica of runtime.py ``_wait_for_reply`` polling (protected file)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for message in reversed(svc.get_messages(session_id, limit=200)):
            if message.role == "assistant" and message.linked_attempt_id == attempt_id:
                return message
        await asyncio.sleep(0.01)
    raise TimeoutError("timed out waiting for assistant reply")


def replies_for(store: SessionStore, sid: str, aid: str) -> List[Message]:
    return [m for m in store.get_messages(sid) if m.linked_attempt_id == aid]


# ---------------------------------------------------------------------------
# Deferred baseline: construction alone must not clobber recoverable attempts
# ---------------------------------------------------------------------------


def test_construction_defers_interrupted_finalization(tmp_path, monkeypatch) -> None:
    store, sid, aid = seed_crashed(tmp_path, partial="Partial answer")
    ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)

    # The T5 baseline would have landed this at construction; the T6 service
    # defers to reconcile() so branch 1/2 attempts are not clobbered.
    attempt = ctx.store.get_attempt(sid, aid)
    assert attempt.status == AttemptStatus.RUNNING
    assert replies_for(ctx.store, sid, aid) == []
    assert ctx.store.get_partial_response(sid, aid) == "Partial answer"


# ---------------------------------------------------------------------------
# Branch 1: re-attach (QA happy — gateway restart with an in-flight turn)
# ---------------------------------------------------------------------------


def test_branch1_reattach_keeps_running_and_streams_to_terminal(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path, partial="Pre-crash text")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        engine_sid = "ses_engine1"
        ctx.driver.messages_payload[engine_sid] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 20_000),
            assistant_entry(
                mid="msg_a1",
                created=now - 19_000,
                completed=now - 15_000,
                finish="tool-calls",
                extra_parts=(
                    tool_part(
                        tool="vibe-trading_get_market_data",
                        call_id="call_done",
                        arguments={"symbol": "AAPL"},
                        output="AAPL 195.0",
                        start=now - 18_000,
                        end=now - 16_000,
                    ),
                    tool_part(
                        tool="bash",
                        call_id="call_live",
                        status="running",
                        arguments={"command": "sleep 1"},
                        start=now - 14_000,
                    ),
                ),
            ),
        ]

        report = await ctx.svc.reconcile()

        assert report.reattached == (aid,)
        assert report.backfilled == () and report.interrupted == ()
        # attempt stays RUNNING (D3: engine wins liveness), no re-prompt ever
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.RUNNING
        assert ctx.driver.prompts == []
        assert ctx.driver.messages_calls == [engine_sid]
        # re-announced to the translator so in-flight events stamp this attempt
        assert ctx.translator.noted_attempts == [(engine_sid, aid)]
        # pre-crash tool trail rebuilt from engine state; the running part is
        # a "running" entry so the live tool_result consolidates onto it
        run = ctx.svc._runs[aid]
        assert [entry["status"] for entry in run.tool_trail] == ["ok", "running"]
        assert run.tool_trail[0]["tool"] == "get_market_data"
        # busy gate re-claimed: a concurrent send is a 409 like a live turn
        with pytest.raises(SessionBusyError):
            await ctx.svc.send_message(sid, "second")

        # post-re-attach streaming continues to the normal terminal
        ctx.translator.emit("text_delta", attempt_id=aid, delta="Full ", iter=1)
        ctx.translator.emit("text_delta", attempt_id=aid, delta="answer", iter=1)
        ctx.translator.emit(
            "tool_result",
            attempt_id=aid,
            tool="bash",
            status="ok",
            elapsed_ms=7,
            preview="done",
            call_id="call_live",
        )
        ctx.translator.emit(
            "attempt.completed",
            attempt_id=aid,
            **{**COMPLETED_EXTRA, "summary": "Full answer"},
        )
        reply = await wait_for_reply(ctx.svc, sid, aid)

        assert reply.content == "Full answer"
        assert reply.metadata["status"] == "completed"
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.COMPLETED
        assert ctx.store.get_partial_response(sid, aid) is None
        # the rebuilt pre-crash entry consolidated with the live tool_result
        assert [(e["tool"], e["status"]) for e in reply.tool_trail] == [
            ("get_market_data", "ok"),
            ("bash", "ok"),
        ]
        assert ctx.bus.by_type("attempt.completed")[0].data["summary"] == "Full answer"
        # claim released at terminal: the session accepts a new turn, reusing
        # the restored engine session (no fresh create_session)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        result2 = await ctx.svc.send_message(sid, "next turn")
        await wait_for_reply(ctx.svc, sid, result2["attempt_id"])
        assert ctx.driver.created_titles == []
        assert ctx.driver.prompts[0][0] == engine_sid
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch1_reattached_turn_stays_cancellable(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path)
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        ctx.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(mid="msg_a1", created=now - 4_000),
        ]
        await ctx.svc.reconcile()

        assert ctx.svc.cancel_current(sid) is True
        assert ctx.translator.noted_aborts == ["ses_engine1"]
        await wait_until(lambda: ctx.driver.aborts == ["ses_engine1"])

        ctx.translator.emit("attempt.cancelled", attempt_id=aid, status="cancelled")
        reply = await wait_for_reply(ctx.svc, sid, aid)
        assert reply.metadata["status"] == "cancelled"
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.CANCELLED
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch1_real_translator_replays_t1_trace_to_terminal(
    tmp_path, monkeypatch
) -> None:
    """QA happy on the REAL translator: T1 golden trace replayed post-re-attach.

    The engine session is scenario a's recorded session; the store says the
    attempt is still running with only its user message on the engine (the
    gateway died right after prompt acceptance). Re-attach announces the
    attempt, the recorded wire frames stream through pump -> translator ->
    dispatch under virtual time, and the turn terminates exactly like the
    golden run: deltas on the bus, bare tool names, summary "DONE_A".
    """

    async def scenario() -> None:
        frames = load_trace("scenario_a_multi_tool.jsonl")
        engine_sid = scenario_sid(frames)
        wire = [f for f in frames if not f["type"].startswith("_recorder")]

        user_mid = None
        for frame in wire:
            if frame["type"] == "message.updated":
                info = json.loads(frame["data"])["properties"]["info"]
                if info.get("role") == "user":
                    user_mid = info["id"]
                    break
        user_text = ""
        for frame in wire:
            if frame["type"] == "message.part.updated":
                part = json.loads(frame["data"])["properties"]["part"]
                if part.get("messageID") == user_mid and part.get("type") == "text":
                    text = part.get("text") or ""
                    user_text = max(user_text, text, key=len)
        assert user_text

        store, sid, aid = seed_crashed(
            tmp_path, prompt=user_text, engine_sid=engine_sid
        )
        driver = RecoveryDriver()
        driver.messages_payload[engine_sid] = [user_entry(user_text)]
        clock = _VirtualClock(frames[0]["mono"])
        translator = EventTranslator(quiescence_s=8.0, clock=clock, tool_map=TMAP)
        ctx = make_recovery_ctx(
            tmp_path, monkeypatch, driver=driver, translator=translator, store=store
        )

        report = await ctx.svc.reconcile()
        assert report.reattached == (aid,)

        async def settle(passes: int = 60) -> None:
            quiet = 0
            for _ in range(passes):
                before = len(ctx.bus.events)
                await asyncio.sleep(0)
                drained = translator._inbox.empty() and (
                    driver._native is None or driver._native.empty()
                )
                if len(ctx.bus.events) == before and drained:
                    quiet += 1
                    if quiet >= 4:
                        return
                else:
                    quiet = 0

        async def advance(seconds: float) -> None:
            target = clock() + seconds
            while clock() < target - 1e-9:
                clock.set(min(clock() + 1.0, target))
                driver.emit_native(decode_event(HEARTBEAT_FRAME))
                await settle()

        for frame in wire:
            gap = frame["mono"] - clock()
            if gap > 1.0:
                await advance(gap - 1.0)
            clock.set(frame["mono"])
            event = decode_event(frame["data"])
            assert event is not None
            driver.emit_native(event)
            await settle()
        await advance(8.05)  # quiescence terminal past the recorded idle

        reply = await wait_for_reply(ctx.svc, sid, aid, timeout=5)
        assert reply.content == "DONE_A"
        assert reply.metadata["status"] == "completed"
        assert reply.metadata["elapsed_ms"] > 0
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.COMPLETED
        assert [(e["tool"], e["status"]) for e in reply.tool_trail] == [
            ("list_skills", "ok"),
            ("bash", "ok"),
        ]
        # re-attach kept streaming: deltas reached the bus stamped correctly
        deltas = ctx.bus.by_type("text_delta")
        assert "".join(e.data["delta"] for e in deltas) == "DONE_A"
        assert all(e.data["attempt_id"] == aid for e in deltas)
        assert {e.data["tool"] for e in ctx.bus.by_type("tool_call")} == {
            "list_skills",
            "bash",
        }
        # reconciliation never re-sent the prompt nor created an engine session
        assert driver.prompts == []
        assert driver.created_titles == []
        await ctx.svc.aclose()

    asyncio.run(scenario())


class _VirtualClock:
    def __init__(self, t0: float) -> None:
        self._t = float(t0)

    def __call__(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        self._t = float(t)


# ---------------------------------------------------------------------------
# Branch 2: backfill (turn finished while the gateway was down)
# ---------------------------------------------------------------------------


def _branch2_entries(tmp_path: Path, prompt: str) -> Tuple[List[dict], str, int]:
    now = int(time.time() * 1000)
    run_dir = tmp_path / "runs" / "run_bt1"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "metrics.csv").write_text(
        "sharpe,max_drawdown\n1.5,-0.2\n", encoding="utf-8"
    )
    entries = [
        user_entry(INJECTION + prompt, created=now - 90_000),
        assistant_entry(
            mid="msg_a1",
            created=now - 89_000,
            completed=now - 80_000,
            finish="tool-calls",
            extra_parts=(
                tool_part(
                    tool="vibe-trading_run_backtest",
                    call_id="call_bt",
                    arguments={"codes": "AAPL"},
                    output=json.dumps({"run_dir": str(run_dir), "ok": True}),
                    start=now - 88_000,
                    end=now - 81_000,
                ),
            ),
        ),
        omo_entry(created=now - 79_000),
        assistant_entry(
            mid="msg_a2",
            created=now - 70_000,
            completed=now - 60_000,
            finish="stop",
            text="Final answer",
        ),
        assistant_entry(
            mid="msg_sum",
            created=now - 59_000,
            completed=now - 58_000,
            finish="stop",
            text="compaction summary (must never surface)",
            summary=True,
        ),
    ]
    return entries, str(run_dir), now


def test_branch2_backfills_terminal_reply_from_engine_state(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        store, sid, aid = seed_crashed(tmp_path, partial="stale partial")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        entries, run_dir, now = _branch2_entries(tmp_path, "analyze AAPL")
        ctx.driver.messages_payload["ses_engine1"] = entries

        report = await ctx.svc.reconcile()

        assert report.backfilled == (aid,)
        assert ctx.driver.prompts == []
        attempt = ctx.store.get_attempt(sid, aid)
        assert attempt.status == AttemptStatus.COMPLETED
        assert attempt.summary == "Final answer"
        assert attempt.run_dir == run_dir

        reply = replies_for(ctx.store, sid, aid)[-1]
        assert reply.content == "Final answer"
        assert reply.linked_attempt_id == aid
        assert set(reply.metadata) == {
            "run_id",
            "status",
            "metrics",
            "elapsed_ms",
            "provider",
            "model",
        }
        assert reply.metadata["status"] == "completed"
        assert reply.metadata["run_id"] == "run_bt1"
        assert reply.metadata["metrics"] == {"sharpe": 1.5, "max_drawdown": -0.2}
        assert reply.metadata["elapsed_ms"] == 30_000  # engine timestamps
        assert reply.metadata["provider"] == "alibaba-cn"
        assert reply.metadata["model"] == "qwen3.8-max"
        # tool trail rebuilt from the engine's tool parts (native shape)
        assert len(reply.tool_trail) == 1
        trail = reply.tool_trail[0]
        assert trail["tool"] == "run_backtest"
        assert trail["status"] == "ok"
        assert trail["arguments"] == {"codes": "AAPL"}
        assert trail["call_id"] == "call_bt"
        assert trail["elapsed_ms"] == 7_000
        assert trail["preview"].startswith('{"run_dir"')
        # FTS indexed at the recovery anchor; partial cleaned up
        assert (sid, "assistant", "Final answer") in ctx.index.message_calls
        assert ctx.store.get_partial_response(sid, aid) is None
        # terminal bus event with the D5/native payload enumeration
        completed = ctx.bus.by_type("attempt.completed")
        assert len(completed) == 1
        assert completed[0].data == {
            "attempt_id": aid,
            "status": "completed",
            "summary": "Final answer",
            "error": None,
            "run_dir": run_dir,
            "elapsed_ms": 30_000,
            "provider": "alibaba-cn",
            "model": "qwen3.8-max",
        }
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch2_finalized_text_is_last_natural_completion(tmp_path) -> None:
    """D4: continuation rounds re-settle the finalized text."""
    now = int(time.time() * 1000)
    entries = [
        user_entry(INJECTION + "write a plan", created=now - 300_000),
        assistant_entry(
            mid="msg_a1",
            created=now - 290_000,
            completed=now - 200_000,
            finish="stop",
            text="First draft",
        ),
        omo_entry(created=now - 199_000),
        assistant_entry(
            mid="msg_a2",
            created=now - 190_000,
            completed=now - 100_000,
            finish="stop",
            text="Revised final",
        ),
    ]
    probe = probe_turn(
        entries, "write a plan", continuation_grace_ms=15_000, bare_name=str
    )
    assert probe.state is TurnState.FINISHED
    assert probe.finalized_text == "Revised final"
    assert probe.elapsed_ms == 200_000


def test_probe_classification_matrix() -> None:
    """The classifier's branch decisions, one assertion per rule."""
    now = int(time.time() * 1000)
    grace = 15_000
    prompt = "do it"
    user = user_entry(INJECTION + prompt, created=now - 60_000)

    def classify(entries) -> TurnState:
        return probe_turn(
            entries, prompt, now_ms=now, continuation_grace_ms=grace, bare_name=str
        ).state

    # in-flight assistant message -> running
    assert classify([user, assistant_entry(mid="a", created=now - 50_000)]) is (
        TurnState.RUNNING
    )
    # finish=tool-calls: the ReAct loop continues -> running
    assert (
        classify(
            [
                user,
                assistant_entry(
                    mid="a",
                    created=now - 50_000,
                    completed=now - 40_000,
                    finish="tool-calls",
                ),
            ]
        )
        is TurnState.RUNNING
    )
    # natural completion inside the continuation window -> ambiguous running
    assert (
        classify(
            [
                user,
                assistant_entry(
                    mid="a",
                    created=now - 10_000,
                    completed=now - 5_000,
                    finish="stop",
                    text="maybe done",
                ),
            ]
        )
        is TurnState.RUNNING
    )
    # natural completion past the window -> finished
    assert (
        classify(
            [
                user,
                assistant_entry(
                    mid="a",
                    created=now - 60_000,
                    completed=now - 40_000,
                    finish="stop",
                    text="done",
                ),
            ]
        )
        is TurnState.FINISHED
    )
    # prompt accepted, no assistant output yet -> running
    assert classify([user]) is TurnState.RUNNING
    # no user message at all -> branch 3
    assert classify([]) is TurnState.NO_PROMPT
    assert classify([omo_entry()]) is TurnState.NO_PROMPT
    # last real user message is a different prompt -> branch 3
    assert classify([user_entry("unrelated question")]) is TurnState.PROMPT_MISMATCH


# ---------------------------------------------------------------------------
# Branch 3: interrupted (native oracle alignment) + QA failure scenario
# ---------------------------------------------------------------------------


def test_branch3_engine_404_lands_native_interrupted_shape(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        store, sid, aid = seed_crashed(tmp_path, partial="Partial answer")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        # default payload: 404 (engine has no such session)

        report = await ctx.svc.reconcile()

        assert report.interrupted == (aid,)
        assert ctx.driver.prompts == []
        attempt = ctx.store.get_attempt(sid, aid)
        assert attempt.status == AttemptStatus.INTERRUPTED
        assert attempt.error == "service restarted before attempt completed"
        assert attempt.completed_at is not None
        replies = replies_for(ctx.store, sid, aid)
        assert len(replies) == 1
        # native oracle shape (service.py:101-154), partial surfaced
        assert "Partial answer" in replies[0].content
        assert "Vibe Trading restarted" in replies[0].content
        assert replies[0].metadata == {
            "status": "interrupted",
            "partial": True,
            "recovery_reason": "service_restart",
        }
        assert ctx.store.get_partial_response(sid, aid) is None
        assert (sid, "assistant", replies[0].content) in ctx.index.message_calls
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch3_existing_reply_finishes_from_its_metadata(
    tmp_path, monkeypatch
) -> None:
    """Crash between reply-append and attempt.json: finish, don't mislabel."""

    async def scenario() -> None:
        store = SessionStore(tmp_path / "sessions")
        session = Session(title="half-written")
        session.config[ENGINE_SESSION_CONFIG_KEY] = "ses_engine1"
        store.create_session(session)
        attempt = Attempt(session_id=session.session_id, prompt="q")
        attempt.mark_running()
        store.create_attempt(attempt)
        store.append_message(
            Message(
                session_id=session.session_id,
                role="assistant",
                content="the complete answer",
                linked_attempt_id=attempt.attempt_id,
                metadata={"status": "completed", "elapsed_ms": 10},
            )
        )
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)

        report = await ctx.svc.reconcile()

        assert report.interrupted == (attempt.attempt_id,)
        recovered = ctx.store.get_attempt(session.session_id, attempt.attempt_id)
        assert recovered.status == AttemptStatus.COMPLETED
        assert recovered.summary == "the complete answer"
        assert len(replies_for(ctx.store, session.session_id, attempt.attempt_id)) == 1
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch3_engine_unreachable_im_polling_gets_terminal_in_budget(
    tmp_path, monkeypatch
) -> None:
    """QA failure: opencode also died -> IM polling sees the terminal reply."""

    async def scenario() -> None:
        store, sid, aid = seed_crashed(tmp_path, partial="half")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        ctx.driver.messages_payload["ses_engine1"] = OpencodeConnectionError(
            "serve unreachable"
        )
        # IM relay shape: _wait_for_reply polls the transcript with a budget
        poller = asyncio.create_task(im_poll_reply(ctx.svc, sid, aid, timeout_s=5.0))
        await asyncio.sleep(0.05)  # let the poller spin at least once

        started = time.monotonic()
        report = await ctx.svc.reconcile()
        reply = await asyncio.wait_for(poller, timeout=2.0)
        elapsed = time.monotonic() - started

        assert report.interrupted == (aid,)
        assert reply.metadata["status"] == "interrupted"
        assert reply.linked_attempt_id == aid
        assert "half" in reply.content
        assert elapsed < 5.0  # terminal landed well inside the IM budget
        assert ctx.driver.prompts == []
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_branch3_no_mapping_no_prompt_and_mismatch_never_resend(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        # (a) no persisted engine mapping: nothing to query
        store_a, sid_a, aid_a = seed_crashed(tmp_path / "a", engine_sid=None)
        ctx_a = make_recovery_ctx(tmp_path / "a", monkeypatch, store=store_a)
        report_a = await ctx_a.svc.reconcile()
        assert report_a.interrupted == (aid_a,)
        assert ctx_a.driver.messages_calls == []
        await ctx_a.svc.aclose()

        # (b) engine session exists but never received the prompt
        store_b, sid_b, aid_b = seed_crashed(tmp_path / "b")
        ctx_b = make_recovery_ctx(tmp_path / "b", monkeypatch, store=store_b)
        ctx_b.driver.messages_payload["ses_engine1"] = []
        report_b = await ctx_b.svc.reconcile()
        assert report_b.interrupted == (aid_b,)
        assert ctx_b.driver.prompts == []  # 永不重复执行
        await ctx_b.svc.aclose()

        # (c) engine/store disagreement: last user message is another prompt
        store_c, sid_c, aid_c = seed_crashed(tmp_path / "c")
        ctx_c = make_recovery_ctx(tmp_path / "c", monkeypatch, store=store_c)
        ctx_c.driver.messages_payload["ses_engine1"] = [
            user_entry("a totally different question")
        ]
        report_c = await ctx_c.svc.reconcile()
        assert report_c.interrupted == (aid_c,)
        assert ctx_c.driver.prompts == []
        await ctx_c.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Acceptance: replay=active does not re-stream dead turns
# ---------------------------------------------------------------------------


def test_replay_active_never_restreams_dead_turns(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        now = int(time.time() * 1000)
        # dead turn 1: finished while down -> backfilled completed
        store1, sid1, aid1 = seed_crashed(tmp_path / "s1", title="backfilled")
        ctx1 = make_recovery_ctx(tmp_path / "s1", monkeypatch, store=store1)
        ctx1.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 90_000),
            assistant_entry(
                mid="a",
                created=now - 80_000,
                completed=now - 60_000,
                finish="stop",
                text="done",
            ),
        ]
        await ctx1.svc.reconcile()
        # dead turn 2: engine gone -> interrupted
        store2, sid2, aid2 = seed_crashed(tmp_path / "s2", title="interrupted")
        ctx2 = make_recovery_ctx(tmp_path / "s2", monkeypatch, store=store2)
        await ctx2.svc.reconcile()
        # live turn: re-attached -> still running
        store3, sid3, aid3 = seed_crashed(tmp_path / "s3", title="reattached")
        ctx3 = make_recovery_ctx(tmp_path / "s3", monkeypatch, store=store3)
        ctx3.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(mid="a", created=now - 4_000),
        ]
        await ctx3.svc.reconcile()

        # sessions_routes.py:806 replica: replay_all only for a RUNNING
        # last attempt — dead turns never re-stream
        assert route_replay_all(ctx1.svc, sid1) is False
        assert route_replay_all(ctx2.svc, sid2) is False
        assert route_replay_all(ctx3.svc, sid3) is True
        # the backfill DID emit a terminal event into the buffer; the
        # replay=active gate (replay_all=False) is what keeps it unre-streamed
        assert ctx1.bus.by_type("attempt.completed")
        assert ctx1.svc.event_bus.replay(sid1, replay_all=False) == []
        assert ctx1.svc.event_bus.replay(sid1, replay_all=True) != []

        await ctx1.svc.aclose()
        await ctx2.svc.aclose()
        await ctx3.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Idempotency + re-attach watch timer (no-hang guarantee)
# ---------------------------------------------------------------------------


def test_reconcile_is_idempotent(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path)
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        ctx.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(mid="a", created=now - 4_000),
        ]
        first = await ctx.svc.reconcile()
        assert first.reattached == (aid,)
        second = await ctx.svc.reconcile()
        assert second.reattached == () and second.interrupted == ()
        assert ctx.translator.noted_attempts == [("ses_engine1", aid)]

        # after the terminal, a third reconcile finds nothing recoverable
        ctx.translator.emit("attempt.completed", attempt_id=aid, **COMPLETED_EXTRA)
        await wait_for_reply(ctx.svc, sid, aid)
        third = await ctx.svc.reconcile()
        assert third == type(third)()
        assert len(replies_for(ctx.store, sid, aid)) == 1
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_watch_backfills_turn_that_finished_unseen(tmp_path, monkeypatch) -> None:
    """TOCTOU: the engine finishes right after the re-attach probe."""

    async def scenario() -> None:
        monkeypatch.setattr(
            "src.opencode_bridge.recovery_branches.REATTACH_WATCH_S", 0.05
        )
        monkeypatch.setattr("src.opencode_bridge.recovery.CONTINUATION_GRACE_S", 0.2)
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path)
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        ctx.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(mid="a", created=now - 4_000),
        ]
        report = await ctx.svc.reconcile()
        assert report.reattached == (aid,)

        # the turn completes on the engine while nobody is watching the stream
        ctx.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(
                mid="a",
                created=now - 4_000,
                completed=int(time.time() * 1000),
                finish="stop",
                text="Completed unseen",
            ),
        ]
        reply = await wait_for_reply(ctx.svc, sid, aid)
        assert reply.content == "Completed unseen"
        assert reply.metadata["status"] == "completed"
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.COMPLETED
        # the translator never emitted a terminal — the watch loop backfilled
        assert not ctx.bus.by_type("text_delta")
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_watch_interrupts_when_engine_session_vanishes(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            "src.opencode_bridge.recovery_branches.REATTACH_WATCH_S", 0.05
        )
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path, partial="early text")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        ctx.driver.messages_payload["ses_engine1"] = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(mid="a", created=now - 4_000),
        ]
        report = await ctx.svc.reconcile()
        assert report.reattached == (aid,)

        ctx.driver.messages_payload["ses_engine1"] = OpencodeHttpError(
            "GET", "/session/ses_engine1/message", 404, "gone"
        )
        reply = await wait_for_reply(ctx.svc, sid, aid)
        assert reply.metadata == {
            "status": "interrupted",
            "partial": True,
            "recovery_reason": "service_restart",
        }
        assert ctx.store.get_attempt(sid, aid).status == AttemptStatus.INTERRUPTED
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_continuation_window_ambiguity_resolves_to_backfill(
    tmp_path, monkeypatch
) -> None:
    """A completion inside the OmO re-prompt window re-attaches first."""

    async def scenario() -> None:
        monkeypatch.setattr(
            "src.opencode_bridge.recovery_branches.REATTACH_WATCH_S", 0.05
        )
        monkeypatch.setattr("src.opencode_bridge.recovery.CONTINUATION_GRACE_S", 0.1)
        now = int(time.time() * 1000)
        store, sid, aid = seed_crashed(tmp_path)
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        entries = [
            user_entry(INJECTION + "analyze AAPL", created=now - 5_000),
            assistant_entry(
                mid="a",
                created=now - 4_000,
                completed=int(time.time() * 1000),
                finish="stop",
                text="Fresh completion",
            ),
        ]
        ctx.driver.messages_payload["ses_engine1"] = entries
        report = await ctx.svc.reconcile()
        assert report.reattached == (aid,)  # ambiguous -> re-attach, not backfill

        reply = await wait_for_reply(ctx.svc, sid, aid)
        assert reply.content == "Fresh completion"
        assert reply.metadata["status"] == "completed"
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Cascade lifecycle (D3): delete -> engine DELETE; /new -> fresh engine session
# ---------------------------------------------------------------------------


def test_delete_session_cascades_engine_delete(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_recovery_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        session = ctx.svc.create_session(title="doomed")
        result = await ctx.svc.send_message(session.session_id, "hi")
        await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])

        assert ctx.svc.delete_session(session.session_id) is True
        await wait_until(
            lambda: ("DELETE", "/session/ses_engine1") in ctx.driver._http.requests
        )
        assert ctx.svc.get_session(session.session_id) is None
        assert ctx.svc.delete_session(session.session_id) is False
        assert len(ctx.driver._http.requests) == 1  # no cascade on a failed delete
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_delete_cascade_after_restart_uses_persisted_mapping(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        store, sid, _ = seed_crashed(tmp_path, engine_sid="ses_persisted")
        # land the attempt so the session is idle, then delete post-"restart"
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        await ctx.svc.reconcile()  # 404 default -> interrupted
        assert ctx.svc.delete_session(sid) is True
        await wait_until(
            lambda: ("DELETE", "/session/ses_persisted") in ctx.driver._http.requests
        )
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_delete_cascade_survives_engine_failure(tmp_path, monkeypatch, caplog) -> None:
    async def scenario() -> None:
        store, sid, _ = seed_crashed(tmp_path, engine_sid="ses_engine1")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        await ctx.svc.reconcile()
        ctx.driver._http.error = OpencodeConnectionError("serve unreachable")

        assert ctx.svc.delete_session(sid) is True  # vt-side delete never blocks
        await wait_until(lambda: bool(ctx.driver._http.requests))
        await asyncio.sleep(0.01)
        assert ctx.svc.get_session(sid) is None
        await ctx.svc.aclose()

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        asyncio.run(scenario())
    assert any("orphaned on the engine" in r.getMessage() for r in caplog.records)


def test_delete_cascade_404_is_success(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        store, sid, _ = seed_crashed(tmp_path, engine_sid="ses_engine1")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        await ctx.svc.reconcile()
        ctx.driver._http.error = OpencodeHttpError(
            "DELETE", "/session/ses_engine1", 404, "gone"
        )
        assert ctx.svc.delete_session(sid) is True
        await wait_until(lambda: bool(ctx.driver._http.requests))
        await ctx.svc.aclose()  # drains without raising

    asyncio.run(scenario())


def test_delete_without_engine_mapping_skips_cascade(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = make_recovery_ctx(tmp_path, monkeypatch)
        session = ctx.svc.create_session(title="never sent")
        assert ctx.svc.delete_session(session.session_id) is True
        await asyncio.sleep(0.01)
        assert ctx.driver._http.requests == []
        await ctx.svc.aclose()

    asyncio.run(scenario())


def test_delete_engine_session_requires_http_seam() -> None:
    class Bare:
        pass

    with pytest.raises(TypeError):
        asyncio.run(delete_engine_session(Bare(), "ses_1"))


def test_im_new_flow_gets_fresh_engine_session_and_keeps_old(
    tmp_path, monkeypatch
) -> None:
    """IM /new: reset drops the channel mapping; the next send creates a
    fresh vt session -> lazily a FRESH engine session (D3 cascade), while the
    old vt session keeps its persisted mapping (transcript stays browsable)."""

    async def scenario() -> None:
        ctx = make_recovery_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        session_a = ctx.svc.create_session(title="websocket:chat-1")
        result_a = await ctx.svc.send_message(session_a.session_id, "first")
        await wait_for_reply(ctx.svc, session_a.session_id, result_a["attempt_id"])
        assert ctx.driver.created_titles == ["websocket:chat-1"]

        # runtime.reset_session (protected) only drops the channel mapping;
        # the next inbound message creates a fresh vt session like _session_for
        session_b = ctx.svc.create_session(title="websocket:chat-1")
        result_b = await ctx.svc.send_message(session_b.session_id, "after /new")
        await wait_for_reply(ctx.svc, session_b.session_id, result_b["attempt_id"])

        assert len(ctx.driver.created_titles) == 2
        mapping_a = ctx.store.get_session(session_a.session_id).config[
            ENGINE_SESSION_CONFIG_KEY
        ]
        mapping_b = ctx.store.get_session(session_b.session_id).config[
            ENGINE_SESSION_CONFIG_KEY
        ]
        assert mapping_a == "ses_engine1"
        assert mapping_b == "ses_engine2"
        assert ctx.driver.prompts[0][0] == "ses_engine1"
        assert ctx.driver.prompts[1][0] == "ses_engine2"
        await ctx.svc.aclose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Mapping persistence: engine context continuity across restarts (D3)
# ---------------------------------------------------------------------------


def test_engine_mapping_persisted_and_reused_after_restart(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = make_recovery_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        session = ctx.svc.create_session(title="persist me")
        result = await ctx.svc.send_message(session.session_id, "hi")
        await wait_for_reply(ctx.svc, session.session_id, result["attempt_id"])
        assert (
            ctx.store.get_session(session.session_id).config[ENGINE_SESSION_CONFIG_KEY]
            == "ses_engine1"
        )
        await ctx.svc.aclose()

        # "restart": a fresh service over the same store must reuse the
        # persisted engine session (a fresh one would drop the engine-side
        # conversation context — D3 engine-context truth)
        ctx2 = make_recovery_ctx(tmp_path, monkeypatch, store=ctx.store)
        ctx2.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        result2 = await ctx2.svc.send_message(session.session_id, "continue")
        await wait_for_reply(ctx2.svc, session.session_id, result2["attempt_id"])
        assert ctx2.driver.created_titles == []
        assert ctx2.driver.prompts[0][0] == "ses_engine1"
        await ctx2.svc.aclose()

    asyncio.run(scenario())


def test_pending_attempt_without_engine_session_lands_interrupted(
    tmp_path, monkeypatch
) -> None:
    """Gateway died between create_attempt and engine-session creation."""

    async def scenario() -> None:
        store, sid, aid = seed_crashed(tmp_path, engine_sid=None, status="pending")
        ctx = make_recovery_ctx(tmp_path, monkeypatch, store=store)
        report = await ctx.svc.reconcile()
        assert report.interrupted == (aid,)
        reply = replies_for(ctx.store, sid, aid)[-1]
        assert reply.metadata["partial"] is False
        assert "no complete assistant response was saved" in reply.content
        await ctx.svc.aclose()

    asyncio.run(scenario())
