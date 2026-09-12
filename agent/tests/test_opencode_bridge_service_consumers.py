"""Consumer-shape contract tests for OpencodeSessionService (work-plan T5).

Ports the consumption shapes the plan names, running them against the real
bridge service (stub translator + fake driver, defined in
``test_opencode_bridge_service``):

* the IM relay shape (``test_channels_runtime.py:32`` FakeSessionService
  consumed by ChannelRuntime: create_session -> send_message -> poll
  get_messages -> outbound reply),
* the OpenBB adapter shape (``openbb_bridge/adapter.py``: create_session ->
  store.append_message history replay -> positional send_message ->
  event_bus.subscribe until terminal -> event_bus.clear buffer release).

No live opencode serve (T7), no network, no channel adapter changes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus
from src.session.models import Message

from tests.test_opencode_bridge_service import (
    COMPLETED_EXTRA,
    make_ctx,
    wait_for_reply,
    wait_until,
)

# ---------------------------------------------------------------------------
# IM relay shape (ported test_channels_runtime.py:252)
# ---------------------------------------------------------------------------


def test_channel_runtime_im_roundtrip_on_bridge_service(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        from src.channels.runtime import ChannelRuntime

        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = (
            "attempt.completed",
            {**COMPLETED_EXTRA, "summary": "agent reply: hello from IM"},
        )
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus,
            session_service=ctx.svc,
            manager=None,
            session_map_path=tmp_path / "channel_sessions.json",
            reply_timeout_s=5,
            poll_interval_s=0.01,
        )
        await runtime.start(start_manager=False)
        try:
            await bus.publish_inbound(
                InboundMessage(
                    channel="websocket",
                    sender_id="user-1",
                    chat_id="chat-1",
                    content="hello from IM",
                    metadata={"message_id": "orig-msg-42"},
                )
            )
            outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=5)
        finally:
            await runtime.stop()
            await ctx.svc.aclose()

        assert isinstance(outbound, OutboundMessage)
        assert outbound.channel == "websocket"
        assert outbound.chat_id == "chat-1"
        assert outbound.content == "agent reply: hello from IM"
        assert outbound.metadata["_channel_runtime"] is True
        assert outbound.metadata["message_id"] == "orig-msg-42"

        session_id = outbound.metadata["session_id"]
        attempt_id = outbound.metadata["attempt_id"]
        assert attempt_id
        session = ctx.svc.get_session(session_id)
        assert session is not None
        assert session.title == "websocket:chat-1"
        assert session.config["channel"] == "websocket"
        assert session.config["channel_chat_id"] == "chat-1"
        assert session.last_attempt_id == attempt_id

        # the IM relay called send_message positionally with the kwarg
        engine_sid, prompt = ctx.driver.prompts[0]
        assert prompt.endswith("hello from IM")
        assert ctx.translator.noted_attempts == [(engine_sid, attempt_id)]

        # the polled reply is the persisted terminal Message (D6 shape)
        reply = await wait_for_reply(ctx.svc, session_id, attempt_id)
        assert reply.metadata["status"] == "completed"
        assert reply.linked_attempt_id == attempt_id

        # /new resets the mapping; the next message creates a fresh session
        assert runtime.reset_session("websocket:chat-1") == session_id


def test_channel_runtime_second_message_reuses_session(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        from src.channels.runtime import ChannelRuntime

        ctx = make_ctx(tmp_path, monkeypatch)
        ctx.translator.auto_terminal = ("attempt.completed", dict(COMPLETED_EXTRA))
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus,
            session_service=ctx.svc,
            manager=None,
            session_map_path=tmp_path / "channel_sessions.json",
            reply_timeout_s=5,
            poll_interval_s=0.01,
        )
        await runtime.start(start_manager=False)
        try:
            for text in ("first", "second"):
                await bus.publish_inbound(
                    InboundMessage(
                        channel="websocket",
                        sender_id="user-1",
                        chat_id="chat-2",
                        content=text,
                        metadata={"message_id": f"m-{text}"},
                    )
                )
                await asyncio.wait_for(bus.consume_outbound(), timeout=5)
        finally:
            await runtime.stop()
            await ctx.svc.aclose()

        # one vt session, one engine session, two attempts chained
        assert len(ctx.driver.created_titles) == 1
        assert len(ctx.driver.prompts) == 2
        session_id = ctx.svc.list_sessions()[0].session_id
        attempts = [a for a in ctx.store.list_attempts() if a.session_id == session_id]
        assert len(attempts) == 2
        by_created = sorted(attempts, key=lambda a: a.created_at)
        assert by_created[1].parent_attempt_id == by_created[0].attempt_id


# ---------------------------------------------------------------------------
# OpenBB adapter consumption shape (adapter.py:106,125,144,215,253)
# ---------------------------------------------------------------------------


def test_openbb_consumption_shape_subscribe_clear_append(tmp_path, monkeypatch) -> None:
    """Replays the adapter's exact call sequence without the openbb_ai dep."""

    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        svc = ctx.svc

        # adapter._create_ephemeral_session (adapter.py:215)
        session = svc.create_session(title="OpenBB Workspace: what is up")

        # adapter._replay_history writes straight to the store (adapter.py:253)
        svc.store.append_message(
            Message(session_id=session.session_id, role="user", content="earlier q")
        )
        svc.store.append_message(
            Message(
                session_id=session.session_id, role="assistant", content="earlier a"
            )
        )

        # adapter._consume_events subscribes BEFORE dispatching (adapter.py:144)
        collected: list = []
        terminal_seen = asyncio.Event()

        async def consume() -> None:
            async for event in svc.event_bus.subscribe(session.session_id):
                collected.append(event)
                if event.event_type in {
                    "attempt.completed",
                    "attempt.failed",
                    "attempt.cancelled",
                }:
                    terminal_seen.set()
                    break

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)  # let the subscription register

        # adapter.handle_query dispatches positionally (adapter.py:106)
        ctx.translator.auto_terminal = (
            "attempt.completed",
            {**COMPLETED_EXTRA, "summary": "the answer"},
        )
        result = await svc.send_message(session.session_id, "enriched question")
        attempt_id = result["attempt_id"]

        await asyncio.wait_for(terminal_seen.wait(), timeout=5)
        consumer.cancel()

        types = [event.event_type for event in collected]
        assert "message.received" in types
        assert "attempt.created" in types
        assert "attempt.started" in types
        assert "attempt.completed" in types
        terminal = collected[-1]
        assert terminal.data["attempt_id"] == attempt_id
        assert terminal.data["summary"] == "the answer"

        reply = await wait_for_reply(svc, session.session_id, attempt_id)
        assert reply.content == "the answer"
        history = [m.content for m in svc.get_messages(session.session_id)]
        assert history == ["earlier q", "earlier a", "enriched question", "the answer"]

        # adapter.handle_query finally-block releases the buffer (adapter.py:125)
        svc.event_bus.clear(session.session_id)
        assert svc.event_bus.replay(session.session_id, replay_all=True) == []
        await svc.aclose()

    asyncio.run(scenario())


def test_real_openbb_adapter_against_bridge_service(tmp_path, monkeypatch) -> None:
    """The actual adapter class, where openbb_ai is installed."""
    openbb_ai = pytest.importorskip("openbb_ai")
    del openbb_ai

    from src.openbb_bridge.adapter import OpenBBQueryAdapter

    async def scenario() -> None:
        ctx = make_ctx(tmp_path, monkeypatch)
        adapter = OpenBBQueryAdapter(session_service=ctx.svc)
        request = SimpleNamespace(
            messages=[
                SimpleNamespace(role="human", content="prior turn"),
                SimpleNamespace(role="ai", content="prior answer"),
                SimpleNamespace(role="human", content="analyze NVDA"),
            ],
            widgets=None,
            workspace_state=None,
            context=None,
        )

        stream = adapter.handle_query(request)
        collected = []
        consumer = asyncio.create_task(_drain(stream, collected))

        # the adapter subscribes after send_message returns; wait for the
        # subscription to register before releasing the terminal event
        await wait_until(lambda: bool(ctx.translator.noted_attempts))
        session_id = ctx.svc.list_sessions()[0].session_id
        await wait_until(lambda: bool(ctx.svc.event_bus._subscribers.get(session_id)))
        attempt_id = ctx.translator.noted_attempts[0][1]
        ctx.translator.emit("text_delta", attempt_id=attempt_id, delta="the answer")
        ctx.translator.emit(
            "attempt.completed",
            attempt_id=attempt_id,
            **{**COMPLETED_EXTRA, "summary": "the answer"},
        )
        await asyncio.wait_for(consumer, timeout=5)

        kinds = [type(item).__name__ for item in collected]
        assert kinds, "adapter produced no SSE objects"
        assert ctx.svc.event_bus.replay(session_id, replay_all=True) == []
        await ctx.svc.aclose()

    asyncio.run(scenario())


async def _drain(stream, collected: list) -> None:
    async for item in stream:
        collected.append(item)
