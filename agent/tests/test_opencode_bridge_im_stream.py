"""Contract tests for the T9 IM streaming producer (``im_stream``).

Covers the plan-T9 acceptance surface with an injectable clock (virtual
time — no real sleeping):

* delta → ``OutboundMessage`` mapping with the exact consumer metadata
  (``_stream_delta``/``_stream_id``) and ``session.config`` addressing;
* the grinev throttle ladder (1s→2s→5s→10s cap by attempt age) layered
  UNDER the manager's coalescing — publication-rate assertions only;
* ``_stream_end`` on ALL THREE terminals (completed/failed/cancelled);
* per-channel ``streaming`` switch OFF ⇒ ZERO publications and the
  terminal single-message fallback unchanged;
* interleaved / out-of-order attempts isolated by ``_stream_id``
  (QA failure scenario: 乱序 delta 不串话);
* flush ordering — pending text flushes BEFORE the tool boundary and
  before segment closes / question-class terminal content;
* the one-shot ``_streamed`` tagger that keeps the runtime's polled final
  message from duplicating the finalized stream (and passes through any
  content mismatch — proposal suffix, failure text);
* service-level tap: the Web SSE surface (EventBus vt vocabulary) is
  UNAFFECTED and a raising observer never kills the dispatch loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.channels.bus.events import OutboundMessage
from src.opencode_bridge.im_stream import (
    THROTTLE_CAP_S,
    ImStreamProducer,
    interval_for_age,
    normalize_text,
)

from tests.test_opencode_bridge_service import make_ctx, wait_for_reply, wait_until

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """Injectable monotonic clock (virtual time — tests never sleep)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingBus:
    """MessageBus stand-in recording every outbound publication."""

    def __init__(self) -> None:
        self.published: List[OutboundMessage] = []

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        self.published.append(msg)

    def deltas(self, stream_id: Optional[str] = None) -> List[OutboundMessage]:
        return [
            m
            for m in self.published
            if m.metadata.get("_stream_delta")
            and (stream_id is None or m.metadata.get("_stream_id") == stream_id)
        ]

    def ends(self, stream_id: Optional[str] = None) -> List[OutboundMessage]:
        return [
            m
            for m in self.published
            if m.metadata.get("_stream_end")
            and (stream_id is None or m.metadata.get("_stream_id") == stream_id)
        ]


class FakeChannel:
    """Adapter stand-in: only the ``supports_streaming`` gate is consulted."""

    def __init__(self, streaming: bool) -> None:
        self._streaming = streaming

    @property
    def supports_streaming(self) -> bool:
        return self._streaming


class FakeManager:
    def __init__(self, channels: Dict[str, FakeChannel]) -> None:
        self.channels = channels


class FakeStore:
    """SessionStore stand-in serving canned ``session.config`` dicts."""

    def __init__(self, configs: Dict[str, Optional[Dict[str, Any]]]) -> None:
        self.configs = configs
        self.reads = 0

    def get_session(self, session_id: str) -> Any:
        self.reads += 1
        config = self.configs.get(session_id)
        if config is None:
            return None
        return SimpleNamespace(config=config)


@dataclass
class Evt:
    """VtEventLike-shaped canned event."""

    type: str
    data: Dict[str, Any] = field(default_factory=dict)


IM_CONFIG = {"channel": "mockim", "channel_chat_id": "chat-9"}


def make_producer(
    *,
    streaming: bool = True,
    clock: Optional[FakeClock] = None,
    bus: Optional[RecordingBus] = None,
    configs: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
    plumbing: Any = "default",
) -> Tuple[ImStreamProducer, RecordingBus, FakeClock, FakeStore]:
    clock = clock or FakeClock()
    bus = bus or RecordingBus()
    store = FakeStore(configs if configs is not None else {"s-im": dict(IM_CONFIG)})
    manager = FakeManager({"mockim": FakeChannel(streaming)})
    resolver = (lambda: (bus, manager)) if plumbing == "default" else plumbing
    producer = ImStreamProducer(store=store, clock=clock, plumbing_resolver=resolver)
    return producer, bus, clock, store


async def feed(producer: ImStreamProducer, events: List[Evt], session_id: str) -> None:
    for event in events:
        await producer.handle_vt_event(event, session_id)


def delta(aid: str, text: str, it: int = 1) -> Evt:
    return Evt("text_delta", {"attempt_id": aid, "delta": text, "iter": it})


def terminal(aid: str, kind: str = "completed", **extra: Any) -> Evt:
    return Evt(f"attempt.{kind}", {"attempt_id": aid, **extra})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_interval_for_age_ladder() -> None:
    assert interval_for_age(0.0) == 1.0
    assert interval_for_age(14.9) == 1.0
    assert interval_for_age(15.0) == 2.0
    assert interval_for_age(59.9) == 2.0
    assert interval_for_age(60.0) == 5.0
    assert interval_for_age(179.9) == 5.0
    assert interval_for_age(180.0) == THROTTLE_CAP_S
    assert interval_for_age(10_000.0) == THROTTLE_CAP_S


def test_normalize_text_matches_manager_fingerprint_semantics() -> None:
    assert normalize_text("  a\t b\n\nc ") == "a b c"
    assert normalize_text("") == ""


# ---------------------------------------------------------------------------
# Mapping / addressing
# ---------------------------------------------------------------------------


def test_first_delta_maps_to_addressed_stream_delta() -> None:
    producer, bus, _, store = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "Hello ")], "s-im")

    asyncio.run(scenario())

    assert len(bus.published) == 1
    msg = bus.published[0]
    assert (msg.channel, msg.chat_id) == ("mockim", "chat-9")
    assert msg.content == "Hello "
    assert msg.metadata == {"_stream_delta": True, "_stream_id": "a1"}
    assert store.reads == 1  # session.config resolved once, then cached


def test_web_session_without_channel_config_is_inert() -> None:
    producer, bus, _, store = make_producer(
        configs={"s-web": {"model": "x"}, "s-none": None}
    )

    async def scenario() -> None:
        await feed(producer, [delta("a1", "hi"), terminal("a1")], "s-web")
        await feed(producer, [delta("a2", "hi"), terminal("a2")], "s-none")
        await feed(producer, [delta("a3", "hi")], "s-web")

    asyncio.run(scenario())

    assert bus.published == []
    assert store.reads == 2  # one cached read per session, never per event


def test_missing_plumbing_keeps_producer_inert() -> None:
    producer, bus, _, _ = make_producer(plumbing=lambda: (None, None))

    async def scenario() -> None:
        await feed(producer, [delta("a1", "hi"), terminal("a1")], "s-im")

    asyncio.run(scenario())

    assert bus.published == []


def test_default_resolver_reads_state_singletons(monkeypatch) -> None:
    import sys

    import src.api.state as state_mod
    from src.opencode_bridge.im_stream import resolve_channel_plumbing

    # Hermetic: no api_server host module shadowing the state globals.
    monkeypatch.delitem(sys.modules, "api_server", raising=False)
    sentinel_bus, sentinel_manager = object(), object()
    monkeypatch.setattr(state_mod, "_channel_bus", sentinel_bus, raising=False)
    monkeypatch.setattr(state_mod, "_channel_manager", sentinel_manager, raising=False)
    assert resolve_channel_plumbing() == (sentinel_bus, sentinel_manager)

    monkeypatch.setattr(state_mod, "_channel_bus", None, raising=False)
    monkeypatch.setattr(state_mod, "_channel_manager", None, raising=False)
    assert resolve_channel_plumbing() == (None, None)


# ---------------------------------------------------------------------------
# Throttle ladder (virtual clock)
# ---------------------------------------------------------------------------


def test_first_flush_immediate_then_ladder_interval() -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A")], "s-im")  # t0: immediate
        clock.advance(0.5)
        await feed(producer, [delta("a1", "B")], "s-im")  # held (0.5 < 1.0)
        clock.advance(0.5)
        await feed(producer, [delta("a1", "C")], "s-im")  # t0+1.0: flush "BC"

    asyncio.run(scenario())

    assert [m.content for m in bus.deltas()] == ["A", "BC"]


@pytest.mark.parametrize(
    ("age_at_flush", "hold_gap", "flush_gap", "band"),
    [
        (16.0, 1.5, 2.0, "2s"),
        (61.0, 4.5, 5.0, "5s"),
        (181.0, 9.5, 10.0, "10s cap"),
    ],
)
def test_ladder_widens_with_attempt_age(
    age_at_flush: float, hold_gap: float, flush_gap: float, band: str
) -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A")], "s-im")
        clock.advance(age_at_flush)
        await feed(producer, [delta("a1", "B")], "s-im")  # age band → flush
        assert len(bus.deltas()) == 2, band
        clock.advance(hold_gap)
        await feed(producer, [delta("a1", "C")], "s-im")  # held by the band
        assert len(bus.deltas()) == 2, band
        clock.advance(flush_gap - hold_gap)
        await feed(producer, [delta("a1", "D")], "s-im")  # interval elapsed
        assert [m.content for m in bus.deltas()] == ["A", "B", "CD"], band

    asyncio.run(scenario())


def test_tool_events_force_flush_bypassing_throttle() -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A")], "s-im")
        clock.advance(0.1)
        await feed(producer, [delta("a1", "B")], "s-im")  # held
        await feed(
            producer,
            [Evt("tool_call", {"attempt_id": "a1", "tool": "bash", "call_id": "c1"})],
            "s-im",
        )

    asyncio.run(scenario())

    assert [m.content for m in bus.deltas()] == ["A", "B"]


def test_tool_event_without_pending_text_publishes_nothing() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(
            producer,
            [Evt("tool_heartbeat", {"attempt_id": "a1", "tool": "bash"})],
            "s-im",
        )

    asyncio.run(scenario())

    assert bus.published == []


# ---------------------------------------------------------------------------
# Terminals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["completed", "failed", "cancelled"])
def test_stream_end_on_every_terminal(kind: str) -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A")], "s-im")
        clock.advance(0.1)
        await feed(producer, [delta("a1", "B")], "s-im")  # held → terminal flush
        extra = {"summary": "AB"} if kind == "completed" else {}
        await feed(producer, [terminal("a1", kind, **extra)], "s-im")

    asyncio.run(scenario())

    assert [m.content for m in bus.deltas()] == ["A", "B"]
    ends = bus.ends()
    assert len(ends) == 1
    assert ends[0].content == ""
    assert ends[0].metadata == {"_stream_end": True, "_stream_id": "a1"}
    assert "_stream_delta" not in ends[0].metadata  # coalescer-absorption guard
    assert (ends[0].channel, ends[0].chat_id) == ("mockim", "chat-9")


def test_terminal_without_any_delta_still_closes_stream() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A")], "s-im")
        await feed(producer, [terminal("a1", "failed", error="boom")], "s-im")
        # A second (duplicate) terminal must not re-open or re-close anything.
        await feed(producer, [terminal("a1", "failed", error="boom")], "s-im")

    asyncio.run(scenario())

    assert len(bus.ends()) == 1


# ---------------------------------------------------------------------------
# Gating (per-channel streaming switch)
# ---------------------------------------------------------------------------


def test_switch_off_zero_publications_and_untouched_final() -> None:
    producer, bus, _, _ = make_producer(streaming=False)

    async def scenario() -> None:
        await feed(
            producer,
            [delta("a1", "A"), delta("a1", "B"), terminal("a1", summary="AB")],
            "s-im",
        )
        # The runtime's polled final passes through the (unwrapped) bus as-is.
        final = OutboundMessage(
            channel="mockim",
            chat_id="chat-9",
            content="AB",
            metadata={"_channel_runtime": True, "attempt_id": "a1", "message_id": "m1"},
        )
        await bus.publish_outbound(final)
        assert "_streamed" not in final.metadata

    asyncio.run(scenario())

    assert bus.deltas() == []
    assert bus.ends() == []
    assert len(bus.published) == 1  # only the runtime final
    assert not hasattr(bus, "_vt_im_stream_tagger")  # tagger never installed


def test_unknown_channel_in_manager_is_inert() -> None:
    producer, bus, _, _ = make_producer(
        configs={"s-im": {"channel": "nope", "channel_chat_id": "c"}}
    )

    async def scenario() -> None:
        await feed(producer, [delta("a1", "A"), terminal("a1")], "s-im")

    asyncio.run(scenario())

    assert bus.published == []


# ---------------------------------------------------------------------------
# Interleaved / out-of-order attempts (QA: _stream_id 隔离不串话)
# ---------------------------------------------------------------------------


def test_interleaved_attempts_isolated_by_stream_id() -> None:
    producer, bus, clock, _ = make_producer(
        configs={"s1": dict(IM_CONFIG), "s2": dict(IM_CONFIG)}
    )

    async def scenario() -> None:
        # Same chat, two attempts; deltas arrive interleaved/out-of-order.
        await feed(producer, [delta("A", "a1")], "s1")
        await feed(producer, [delta("B", "b1")], "s2")
        clock.advance(0.1)
        await feed(producer, [delta("A", "a2")], "s1")  # held
        await feed(producer, [delta("B", "b2")], "s2")  # held
        await feed(producer, [terminal("A", summary="a1a2")], "s1")
        await feed(producer, [terminal("B", summary="b1b2")], "s2")

    asyncio.run(scenario())

    assert [m.content for m in bus.deltas("A")] == ["a1", "a2"]
    assert [m.content for m in bus.deltas("B")] == ["b1", "b2"]
    assert len(bus.ends("A")) == 1 and len(bus.ends("B")) == 1
    # Global order: A's close precedes B's flush — no cross-stream merging.
    kinds = [
        (m.metadata.get("_stream_id"), "end" if m.metadata.get("_stream_end") else "d")
        for m in bus.published
    ]
    assert kinds == [
        ("A", "d"),
        ("B", "d"),
        ("A", "d"),
        ("A", "end"),
        ("B", "d"),
        ("B", "end"),
    ]


# ---------------------------------------------------------------------------
# Flush ordering + segmentation
# ---------------------------------------------------------------------------


def test_pending_text_flushes_before_terminal_content() -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "Q: which ")], "s-im")
        clock.advance(0.1)
        await feed(producer, [delta("a1", "symbol?")], "s-im")  # held
        await feed(producer, [terminal("a1", summary="Q: which symbol?")], "s-im")

    asyncio.run(scenario())

    # The question-class text is fully published BEFORE the end marker.
    assert [m.content for m in bus.deltas()] == ["Q: which ", "symbol?"]
    assert bus.published[-1].metadata.get("_stream_end") is True


def test_iter_change_closes_segment_with_resuming_after_flush() -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "Let me check. ", it=1)], "s-im")
        clock.advance(0.1)
        await feed(producer, [delta("a1", "One moment. ", it=1)], "s-im")  # held
        # iter 2 arrives: pending iter-1 text flushes FIRST, then the
        # intermediate end marker (_resuming), then iter-2 text streams.
        await feed(producer, [delta("a1", "The answer ", it=2)], "s-im")
        clock.advance(0.1)
        await feed(producer, [delta("a1", "is 42.", it=2)], "s-im")  # held
        await feed(producer, [terminal("a1", summary="The answer is 42.")], "s-im")

    asyncio.run(scenario())

    published = bus.published
    assert [m.content for m in published] == [
        "Let me check. ",
        "One moment. ",
        "",
        "The answer is 42.",
        "",
    ]
    intermediate = published[2].metadata
    assert intermediate == {
        "_stream_end": True,
        "_stream_id": "a1",
        "_resuming": True,
    }
    final = published[4].metadata
    assert final == {"_stream_end": True, "_stream_id": "a1"}
    assert "_resuming" not in final


def test_iter_jump_closes_prior_segment_once() -> None:
    producer, bus, clock, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "text", it=1)], "s-im")
        clock.advance(1.0)
        # iter jumps 1→3 (tool-only round carried no text): segment 1 closes
        # exactly once (_resuming), segment 3 streams under the same stream id.
        await feed(producer, [delta("a1", "more", it=3)], "s-im")
        await feed(producer, [terminal("a1", summary="more")], "s-im")

    asyncio.run(scenario())

    assert [m.content for m in bus.deltas()] == ["text", "more"]
    ends = bus.ends()
    assert len(ends) == 2  # intermediate (_resuming) + terminal
    assert ends[0].metadata.get("_resuming") is True
    assert "_resuming" not in ends[1].metadata


# ---------------------------------------------------------------------------
# `_streamed` tagger (no duplicate final message)
# ---------------------------------------------------------------------------


def runtime_final(aid: str, content: str, **meta_extra: Any) -> OutboundMessage:
    return OutboundMessage(
        channel="mockim",
        chat_id="chat-9",
        content=content,
        metadata={
            "_channel_runtime": True,
            "attempt_id": aid,
            "session_id": "s-im",
            "message_id": "m-in-1",
            **meta_extra,
        },
    )


def test_tagger_marks_matching_runtime_final_once() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "The answer ")], "s-im")
        await feed(producer, [delta("a1", "is 42.")], "s-im")
        await feed(
            producer,
            [terminal("a1", summary="The answer  is 42.")],  # whitespace differs
            "s-im",
        )
        final = runtime_final("a1", "The answer is 42.")
        await bus.publish_outbound(final)
        assert final.metadata.get("_streamed") is True
        # One-shot: a second identical final is NOT marked again.
        second = runtime_final("a1", "The answer is 42.")
        await bus.publish_outbound(second)
        assert "_streamed" not in second.metadata

    asyncio.run(scenario())


def test_tagger_passes_through_content_mismatch() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "Done.")], "s-im")
        await feed(producer, [terminal("a1", summary="Done.")], "s-im")
        suffixed = runtime_final(
            "a1", "Done.\n\n[Scheduled research confirmation · create]"
        )
        await bus.publish_outbound(suffixed)
        assert "_streamed" not in suffixed.metadata

    asyncio.run(scenario())


def test_tagger_ignores_non_runtime_and_failed_attempts() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "partial")], "s-im")
        await feed(producer, [terminal("a1", "failed", error="boom")], "s-im")
        failure_final = runtime_final("a1", "Execution failed: boom")
        await bus.publish_outbound(failure_final)
        assert "_streamed" not in failure_final.metadata

        unrelated = OutboundMessage(
            channel="mockim",
            chat_id="chat-9",
            content="partial",
            metadata={"session_reset": True},
        )
        await bus.publish_outbound(unrelated)
        assert "_streamed" not in unrelated.metadata

    asyncio.run(scenario())


def test_tagger_skips_when_summary_never_streamed() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        # completed with a summary the stream never delivered (e.g. sparse
        # snapshots settled after the last delta) → final passes through.
        await feed(producer, [delta("a1", "half")], "s-im")
        await feed(producer, [terminal("a1", summary="half of the truth")], "s-im")
        final = runtime_final("a1", "half of the truth")
        await bus.publish_outbound(final)
        assert "_streamed" not in final.metadata

    asyncio.run(scenario())


def test_detach_restores_original_publish() -> None:
    producer, bus, _, _ = make_producer()

    async def scenario() -> None:
        await feed(producer, [delta("a1", "x")], "s-im")  # installs the tagger
        assert hasattr(bus, "_vt_im_stream_tagger")
        producer.detach()
        assert not hasattr(bus, "_vt_im_stream_tagger")
        assert "publish_outbound" not in vars(bus)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Service-level tap (real OpencodeSessionService; Web SSE surface unaffected)
# ---------------------------------------------------------------------------


def _im_ctx(tmp_path: Path, monkeypatch, clock: FakeClock):
    ctx = make_ctx(tmp_path, monkeypatch)
    bus = RecordingBus()
    manager = FakeManager({"mockim": FakeChannel(True)})
    producer = ImStreamProducer(
        store=ctx.store, clock=clock, plumbing_resolver=lambda: (bus, manager)
    )
    producer.attach(ctx.svc)
    session = ctx.svc.create_session(title="im", config=dict(IM_CONFIG))
    return ctx, bus, session.session_id


def test_service_tap_streams_to_im_and_keeps_web_sse_intact(
    tmp_path, monkeypatch
) -> None:
    clock = FakeClock()
    ctx, im_bus, sid = _im_ctx(tmp_path, monkeypatch, clock)

    async def scenario() -> str:
        result = await ctx.svc.send_message(sid, "hi")
        aid = result["attempt_id"]
        await wait_until(lambda: aid in ctx.svc._runs)
        ctx.translator.emit("text_delta", attempt_id=aid, delta="Hello ", iter=1)
        ctx.translator.emit("text_delta", attempt_id=aid, delta="world", iter=1)
        ctx.translator.emit(
            "attempt.completed",
            attempt_id=aid,
            summary="Hello world",
            run_dir=None,
            elapsed_ms=5,
        )
        reply = await wait_for_reply(ctx.svc, sid, aid)
        assert reply.content == "Hello world"
        assert reply.metadata.get("status") == "completed"
        return aid

    aid = asyncio.run(scenario())

    # IM surface: two deltas (first immediate, second flushed by the terminal)
    # + the end marker, correctly addressed.
    assert [m.content for m in im_bus.deltas(aid)] == ["Hello ", "world"]
    assert len(im_bus.ends(aid)) == 1
    assert all((m.channel, m.chat_id) == ("mockim", "chat-9") for m in im_bus.published)
    # Web SSE surface: the vt vocabulary is published exactly as before (T5/T7).
    types = ctx.bus.types()
    assert types.count("text_delta") == 2
    assert "attempt.completed" in types
    # The runtime-shaped final would be marked (stream delivered it verbatim).
    final = runtime_final(aid, "Hello world")

    async def tag() -> None:
        await im_bus.publish_outbound(final)

    asyncio.run(tag())
    assert final.metadata.get("_streamed") is True


def test_raising_observer_never_breaks_dispatch(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path, monkeypatch)

    async def boom(event: Any, session_id: str) -> None:
        raise RuntimeError("im side exploded")

    ctx.svc.vt_event_observer = boom
    session = ctx.svc.create_session(title="web")

    async def scenario() -> None:
        result = await ctx.svc.send_message(session.session_id, "hi")
        aid = result["attempt_id"]
        await wait_until(lambda: aid in ctx.svc._runs)
        ctx.translator.emit("text_delta", attempt_id=aid, delta="ok", iter=1)
        ctx.translator.emit(
            "attempt.completed", attempt_id=aid, summary="ok", run_dir=None
        )
        reply = await wait_for_reply(ctx.svc, session.session_id, aid)
        assert reply.content == "ok"

    asyncio.run(scenario())

    assert "text_delta" in ctx.bus.types()
    assert "attempt.completed" in ctx.bus.types()
