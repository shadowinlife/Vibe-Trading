"""Unit tests for the OpencodeDriver persistent SSE event stream.

Covers the plan-T3 robustness mandate (reconnect with backoff 500 ms -> 30 s,
reset on every received event — fixing opencode-runtime's no-backoff
limitation), the single-consumer guard, auth fail-fast, allowlist tolerance
(unknown/undecodable frames never kill the stream), and golden replay of the
T1 real traces recorded on opencode 1.18.30 (``agent/tests/fixtures/
opencode_bridge/traces/``). No live serve — mock transport + trace replay
only (live E2E is T7).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.opencode_bridge.driver import DriverSettings, OpencodeDriver
from src.opencode_bridge.errors import OpencodeHttpError
from src.opencode_bridge.events import OpencodeEvent

BASE = "http://serve.test"
TRACES_DIR = Path(__file__).parent / "fixtures" / "opencode_bridge" / "traces"

FAST_RECONNECT = DriverSettings(
    reconnect_initial_backoff_s=0.01, reconnect_max_backoff_s=0.02
)


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def sse_response(chunks: list[bytes]) -> httpx.Response:
    return httpx.Response(
        200,
        stream=ChunkedStream(chunks),
        headers={"content-type": "text/event-stream"},
    )


def sse_chunks(*payloads: str) -> list[bytes]:
    return [f"data: {payload}\n\n".encode("utf-8") for payload in payloads]


def make_driver(handler, **kwargs) -> OpencodeDriver:
    return OpencodeDriver(BASE, transport=httpx.MockTransport(handler), **kwargs)


async def collect_events(
    driver: OpencodeDriver, count: int, timeout: float = 5.0
) -> list[OpencodeEvent]:
    collected: list[OpencodeEvent] = []
    stream = driver.events()

    async def run() -> None:
        async for event in stream:
            collected.append(event)
            if len(collected) >= count:
                return

    try:
        await asyncio.wait_for(run(), timeout)
    finally:
        await stream.aclose()
    return collected


# ---------------------------------------------------------------------------
# stream semantics
# ---------------------------------------------------------------------------


def test_events_pass_through_in_wire_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/event"
        assert request.headers["accept"] == "text/event-stream"
        return sse_response(
            sse_chunks(
                '{"id":"evt_1","type":"session.idle","properties":{"sessionID":"s"}}',
                '{"id":"evt_2","type":"server.heartbeat","properties":{}}',
                '{"id":"evt_3","type":"brand.new.event","properties":{"x":1}}',
            )
        )

    events = asyncio.run(collect_events(make_driver(handler), 3))
    assert [event.type for event in events] == [
        "session.idle",
        "server.heartbeat",
        "brand.new.event",  # unknown types pass through — T4 filters
    ]
    assert events[0].properties == {"sessionID": "s"}
    assert events[0].raw["id"] == "evt_1"


def test_undecodable_and_typeless_frames_are_skipped_not_fatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            sse_chunks(
                '{"type":"first"}',
                "this is not json",
                '{"properties":{"no":"type"}}',
                "[1,2,3]",
                '{"type":"second"}',
            )
        )

    events = asyncio.run(collect_events(make_driver(handler), 2))
    assert [event.type for event in events] == ["first", "second"]
    assert events[0].properties == {}  # absent properties -> empty mapping


def test_frames_split_across_chunk_boundaries_survive() -> None:
    wire = b"".join(sse_chunks('{"type":"a"}', '{"type":"b"}'))
    chunks = [wire[i : i + 7] for i in range(0, len(wire), 7)]

    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(chunks)

    events = asyncio.run(collect_events(make_driver(handler), 2))
    assert [event.type for event in events] == ["a", "b"]


def test_clean_eof_triggers_reconnect() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return sse_response(sse_chunks(json.dumps({"type": f"e{calls['n']}"})))

    events = asyncio.run(
        collect_events(make_driver(handler, settings=FAST_RECONNECT), 2)
    )
    assert [event.type for event in events] == ["e1", "e2"]
    assert calls["n"] == 2


def test_backoff_grows_and_resets_on_received_event(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("serve down")
        return sse_response(sse_chunks(json.dumps({"type": f"e{calls['n']}"})))

    events = asyncio.run(collect_events(make_driver(handler), 2))
    assert [event.type for event in events] == ["e3", "e4"]
    # 0.5 -> 1.0 across the two failures; the event received from call 3
    # resets the backoff to the floor before the post-EOF reconnect.
    assert sleeps == [0.5, 1.0, 0.5]


def test_backoff_is_capped_at_the_configured_maximum(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            raise httpx.ConnectError("serve down")
        return sse_response(sse_chunks('{"type":"up"}'))

    settings = DriverSettings(
        reconnect_initial_backoff_s=1.0, reconnect_max_backoff_s=2.0
    )
    events = asyncio.run(collect_events(make_driver(handler, settings=settings), 1))
    assert [event.type for event in events] == ["up"]
    assert sleeps == [1.0, 2.0, 2.0]


def test_stream_auth_failure_raises_instead_of_looping() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(OpencodeHttpError) as excinfo:
        asyncio.run(collect_events(make_driver(handler, settings=FAST_RECONNECT), 1))
    assert excinfo.value.status_code == 401
    assert calls["n"] == 1  # fail fast: retrying cannot fix auth


def test_stream_server_error_reconnects() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return sse_response(sse_chunks('{"type":"recovered"}'))

    events = asyncio.run(
        collect_events(make_driver(handler, settings=FAST_RECONNECT), 1)
    )
    assert [event.type for event in events] == ["recovered"]
    assert calls["n"] == 2


def test_second_concurrent_consumer_is_rejected_then_slot_frees() -> None:
    async def run_test() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return sse_response(sse_chunks('{"type":"e"}'))

        driver = make_driver(handler, settings=FAST_RECONNECT)
        first = driver.events()
        assert (await first.__anext__()).type == "e"
        second = driver.events()
        with pytest.raises(RuntimeError, match="single consumer"):
            await second.__anext__()
        await first.aclose()
        third = driver.events()  # slot freed: resubscription must work
        try:
            assert (await third.__anext__()).type == "e"
        finally:
            await third.aclose()

    asyncio.run(run_test())


def test_aclose_stops_the_reconnect_loop() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("serve down")

    async def run_test() -> None:
        driver = make_driver(handler, settings=FAST_RECONNECT)
        consumed: list[OpencodeEvent] = []

        async def consume() -> None:
            async for event in driver.events():
                consumed.append(event)

        task = asyncio.create_task(consume())
        deadline = asyncio.get_running_loop().time() + 2.0
        while calls["n"] < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        assert calls["n"] >= 2
        await driver.aclose()
        await asyncio.wait_for(task, timeout=2.0)  # loop exited, no hang
        assert consumed == []

    asyncio.run(run_test())


# ---------------------------------------------------------------------------
# golden replay of the T1 real traces (opencode 1.18.30)
# ---------------------------------------------------------------------------


def load_wire_frames(trace_name: str) -> list[dict]:
    frames: list[dict] = []
    with (TRACES_DIR / trace_name).open(encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)
            if not frame["type"].startswith("_recorder"):
                frames.append(frame)
    return frames


def replay_events(trace_name: str, chunk_size: int = 97) -> list[OpencodeEvent]:
    """Replay one golden trace through the driver as raw SSE wire bytes."""
    frames = load_wire_frames(trace_name)
    wire = b"".join(
        b"data: " + frame["data"].encode("utf-8") + b"\n\n" for frame in frames
    )
    chunks = [wire[i : i + chunk_size] for i in range(0, len(wire), chunk_size)]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/event"
        return sse_response(chunks)

    driver = make_driver(handler)
    events = asyncio.run(collect_events(driver, len(frames), timeout=30.0))
    assert calls["n"] == 1  # one persistent connection, no reconnect needed
    assert len(events) == len(frames)
    return events


def sid_of(event: OpencodeEvent) -> str | None:
    props = event.properties
    return props.get("sessionID") or (props.get("info") or {}).get("sessionID")


def test_replay_scenario_a_multi_tool() -> None:
    frames = load_wire_frames("scenario_a_multi_tool.jsonl")
    events = replay_events("scenario_a_multi_tool.jsonl")
    assert [event.type for event in events] == [frame["type"] for frame in frames]

    deltas = [event for event in events if event.type == "message.part.delta"]
    assert deltas, "scenario a must contain token deltas (spike §5a)"
    # Golden pin (spike §7i): single stable delta property set on 1.18.30.
    for delta in deltas:
        assert set(delta.properties) == {
            "sessionID",
            "messageID",
            "partID",
            "field",
            "delta",
        }
        assert delta.properties["field"] == "text"

    tool_names = {
        event.properties.get("part", {}).get("tool")
        for event in events
        if event.type == "message.part.updated"
        and event.properties.get("part", {}).get("type") == "tool"
    }
    assert "vibe-trading_list_skills" in tool_names
    assert "bash" in tool_names


def test_replay_scenario_b_abort_terminal() -> None:
    events = replay_events("scenario_b_abort.jsonl")
    errors = [event for event in events if event.type == "session.error"]
    assert errors, "abort trace must carry session.error (spike §5b)"
    assert errors[0].properties["error"]["name"] == "MessageAbortedError"
    # Double idle after abort (spike §5b) — consumers must tolerate it.
    idles = [event for event in events if event.type == "session.idle"]
    assert len(idles) == 2


def test_replay_scenario_d_child_events_share_the_stream() -> None:
    events = replay_events("scenario_d_subagent.jsonl")
    sids = {sid_of(event) for event in events if sid_of(event)}
    # Parent + subagent child on ONE /event stream (spike §5d): the driver
    # passes both through; sessionID-scoped filtering is T4/T5's job.
    assert len(sids) == 2
    created = [event for event in events if event.type == "session.created"]
    assert len(created) == 2
    child_created = [
        event
        for event in created
        if (event.properties.get("info") or {}).get("parentID")
    ]
    assert len(child_created) == 1  # live parent-chain source (spike §5d)


def test_replay_preserves_raw_wire_payload_losslessly() -> None:
    frames = load_wire_frames("measurement_h_delete_session.jsonl")
    events = replay_events("measurement_h_delete_session.jsonl")
    for frame, event in zip(frames, events):
        assert event.raw == json.loads(frame["data"])
        assert event.type == frame["type"]
