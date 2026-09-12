"""Shared virtual-clock harness for the EventTranslator test suites.

Golden replay drives virtual time from the T1 trace timestamps
(``mono`` field): the clock is set to each frame's monotonic value before
the frame is fed, big inter-frame gaps are stepped in <=1 s increments
(waking the generator with ignored ``server.heartbeat`` events) so
timer-driven emissions land at their scheduled virtual times, and no test
ever sleeps in real time. This module contains helpers only — pytest
collects no tests from it.

Settling: the translator's ``events()`` generator runs as a collector
task; after every feed the harness yields to the event loop until the
output list and the translator inbox are stable, which makes assertions
deterministic without real waits.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from src.opencode_bridge.events import OpencodeEvent, decode_event
from src.opencode_bridge.tool_names import EMPTY_TOOL_NAME_MAP, ToolNameMap
from src.opencode_bridge.translator import EventTranslator, VtEvent

TRACES_DIR = Path(__file__).parent / "fixtures" / "opencode_bridge" / "traces"

#: Max virtual seconds per wake step when crossing a gap (keeps
#: timer-driven emissions within 1 s of their scheduled virtual time).
STEP_S = 1.0

_HEARTBEAT_WAKE = '{"type":"server.heartbeat","properties":{}}'


class VirtualClock:
    """Injectable monotonic-seconds source driven by the test."""

    def __init__(self, t0: float = 1000.0) -> None:
        self._t = float(t0)

    def __call__(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        self._t = float(t)

    def advance(self, dt: float) -> None:
        self._t += float(dt)


def wire_event(event_type: str, **properties: Any) -> OpencodeEvent:
    """Build a synthetic driver-native event with the given properties."""
    payload = {"type": event_type, "properties": properties}
    return OpencodeEvent(type=event_type, properties=properties, raw=payload)


def load_trace(name: str) -> list[dict]:
    """Load one T1 trace corpus file (JSONL frames)."""
    frames: list[dict] = []
    with (TRACES_DIR / name).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                frames.append(json.loads(line))
    return frames


def scenario_sid(frames: list[dict]) -> str:
    """The recorded scenario's session id (``_recorder.scenario_start``)."""
    for frame in frames:
        if frame["type"] == "_recorder.scenario_start":
            return json.loads(frame["data"])["sessionID"]
    raise AssertionError("trace carries no _recorder.scenario_start frame")


class TranslatorBench:
    """Drive one :class:`EventTranslator` under virtual time.

    Usage::

        async with TranslatorBench() as bench:
            bench.translator.note_attempt("ses_1", "att_1")
            await bench.feed(wire_event("session.idle", sessionID="ses_1"))
            await bench.advance(8.05)
        assert bench.types()[-1] == "attempt.completed"
    """

    def __init__(
        self,
        *,
        quiescence_s: float = 8.0,
        tool_map: ToolNameMap = EMPTY_TOOL_NAME_MAP,
        t0: float = 1000.0,
    ) -> None:
        self.clock = VirtualClock(t0)
        self.translator = EventTranslator(
            quiescence_s=quiescence_s, clock=self.clock, tool_map=tool_map
        )
        self.out: list[VtEvent] = []
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> TranslatorBench:
        self._task = asyncio.ensure_future(self._collect())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.translator.aclose()
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=2.0)

    async def _collect(self) -> None:
        async for event in self.translator.events():
            self.out.append(event)

    async def settle(self, passes: int = 40) -> None:
        """Yield to the loop until output and inbox are stable."""
        quiet = 0
        for _ in range(passes):
            before = len(self.out)
            await asyncio.sleep(0)
            if len(self.out) == before and self.translator._inbox.empty():
                quiet += 1
                if quiet >= 3:
                    return
            else:
                quiet = 0

    async def feed(self, event: OpencodeEvent, *, at: float | None = None) -> None:
        """Optionally set the clock, feed one event, and settle."""
        if at is not None:
            self.clock.set(at)
        await self.translator.feed(event)
        await self.settle()

    async def wake(self, *, at: float) -> None:
        """Advance the clock to *at* and wake the generator (ignored event)."""
        await self.feed(decode_event(_HEARTBEAT_WAKE), at=at)  # type: ignore[arg-type]

    async def advance(self, seconds: float, *, step: float = STEP_S) -> None:
        """Move virtual time forward in <=step increments, waking timers."""
        target = self.clock() + seconds
        while self.clock() < target:
            await self.wake(at=min(self.clock() + step, target))

    def types(self) -> list[str]:
        """The emitted event-type sequence."""
        return [event.type for event in self.out]

    def of_type(self, event_type: str) -> list[VtEvent]:
        """All emitted events of one type."""
        return [event for event in self.out if event.type == event_type]


async def replay_trace(
    name: str,
    *,
    quiescence_s: float = 8.0,
    tool_map: ToolNameMap = EMPTY_TOOL_NAME_MAP,
    attempt_id: str = "att_golden",
    announce: bool = True,
    abort_at_error: bool = False,
    tail_advance_s: float = 8.05,
) -> tuple[TranslatorBench, str, list[dict]]:
    """Replay one T1 trace through a fresh translator at virtual speed.

    Args:
        name: Trace file name inside :data:`TRACES_DIR`.
        quiescence_s: Translator quiescence window.
        tool_map: Tool-name mapper under test.
        attempt_id: Attempt id announced for the scenario session.
        announce: Whether to call ``note_attempt`` (scenario h has none).
        abort_at_error: Call ``note_abort`` right before feeding the
            trace's ``session.error`` frame (scenario b's abort POST).
        tail_advance_s: Virtual seconds to advance past the last wire
            frame so a pending quiescence terminal fires.

    Returns:
        ``(bench, session_id, wire_frames)`` — the bench is already closed;
        ``bench.out`` holds the full translated sequence.
    """
    frames = load_trace(name)
    sid = scenario_sid(frames)
    wire = [f for f in frames if not f["type"].startswith("_recorder")]
    async with TranslatorBench(
        quiescence_s=quiescence_s, tool_map=tool_map, t0=frames[0]["mono"]
    ) as bench:
        if announce:
            bench.translator.note_attempt(sid, attempt_id)
        aborted = False
        for frame in wire:
            gap = frame["mono"] - bench.clock()
            if gap > STEP_S:
                await bench.advance(gap - STEP_S)
            if abort_at_error and not aborted and frame["type"] == "session.error":
                bench.translator.note_abort(sid)
                await bench.settle()
                aborted = True
            event = decode_event(frame["data"])
            assert event is not None, f"undecodable frame in {name}"
            await bench.feed(event, at=frame["mono"])
        await bench.advance(tail_advance_s)
    return bench, sid, wire


def run_trace_scenario(
    name: str, **kwargs: Any
) -> tuple[TranslatorBench, str, list[dict]]:
    """Synchronous :func:`replay_trace` wrapper for test functions."""
    return asyncio.run(replay_trace(name, **kwargs))
