"""Tool-part state machine: tool_call / tool_result / tool_heartbeat.

Normative rules (plan D5, spike report §5a/§7g):

* opencode tool parts stream as cumulative ``message.part.updated``
  snapshots: ``pending`` (no input yet) -> ``running`` (``state.input``
  populated, ``state.time.start`` set) -> ``completed``/``error``
  (``state.output``, ``state.time.end``). ``tool_call`` is emitted once per
  callID at the first snapshot that carries arguments (``running``; or the
  terminal snapshot itself when events were sparse), with every argument
  value stringified and truncated to 200 chars (native ``loop.py:2172``).
* ``tool_result{tool, status, elapsed_ms, preview, call_id}`` — status maps
  ``completed -> "ok"`` / ``error -> "error"`` (native vocabulary,
  ``loop.py:2585``); **preview = ``state.output`` first 200 chars, NOT
  ``state.title``** (``state.title`` is empty for MCP tools — spike §5a;
  200-char budget aligns ``loop.py:2600`` so the relay's proposal_id /
  run_id regexes keep matching inside the preview). The output is never
  re-parsed beyond the ``run_dir`` regex below.
* **``tool_heartbeat`` synthesis is mandatory, not a degradation item**
  (B3): measured 120.75 s intra-tool content silence (spike §7g) would trip
  the frontend's 90 s inactivity watchdog, which only content events
  refresh (``Agent.tsx:1247-1269``; the stream-level ``server.heartbeat``
  is a keep-alive no-op). One beat per 3 s per running tool (native
  ``VT_HEARTBEAT_INTERVAL_S`` cadence, ``loop.py:2283``), ``elapsed_s``
  computed at the beat's SCHEDULED time so catch-up bursts after a silent
  window are deterministic.
* ``run_dir`` is regex-extracted from backtest-class tool output (the
  ``backtest`` MCP tool returns JSON with a ``run_dir`` key; the prose
  ``Run directory: <path>`` form is the fallback) and stashed until the
  attempt terminal — the run card's only source (``Agent.tsx:876-877``).
* The ``task`` tool part's ``state.metadata.sessionId`` is the child
  session's canonical id — populated only at completion (spike §5d); it is
  surfaced to the orchestrator for the child-drop filter, never parsed out
  of ``state.output`` (D4).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .events import VtEvent

logger = logging.getLogger("opencode_bridge")

__all__ = [
    "ARG_VALUE_CHARS",
    "HEARTBEAT_INTERVAL_S",
    "PREVIEW_CHARS",
    "ToolState",
]

#: Heartbeat synthesis cadence while a tool part is running (loop.py:2283).
HEARTBEAT_INTERVAL_S = 3.0

#: tool_result preview budget (loop.py:2600 — relay regexes must match inside).
PREVIEW_CHARS = 200

#: Per-argument-value truncation budget (loop.py:2172/2227).
ARG_VALUE_CHARS = 200

#: run_dir extraction from backtest-class tool output (JSON form first).
_RUN_DIR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"run_dir"\s*:\s*"([^"]+)"'),
    re.compile(r"Run directory:\s*(\S+)"),
)

_TERMINAL_STATUSES = frozenset({"completed", "error"})


@dataclass
class _RunningTool:
    """Heartbeat schedule for one running tool part."""

    part_id: str
    tool: str
    call_id: str
    since: float
    next_beat: float


class ToolState:
    """Tool-part state machine for one attempt (one session context).

    Args:
        bare_name: Mapper from opencode tool ids to bare vt tool names
            (T3's :class:`~src.opencode_bridge.tool_names.ToolNameMap.bare`;
            the relay matches bare names, ``sessions_routes.py:220,244``).
    """

    def __init__(self, bare_name: Callable[[str], str]) -> None:
        self._bare_name = bare_name
        self._running: dict[str, _RunningTool] = {}
        self._announced: set[str] = set()
        self._finished: set[str] = set()
        #: run_dir stashed from backtest-class output until the terminal.
        self.run_dir: str | None = None
        #: Child session ids discovered from completed ``task`` parts;
        #: drained by the orchestrator into its child registry.
        self.child_session_ids: list[str] = []

    def handle_part(
        self, part: Mapping[str, Any], now: float, iter_value: int
    ) -> list[VtEvent]:
        """Advance one tool-part snapshot; return draft events.

        Args:
            part: The ``properties.part`` object (``type == "tool"``).
            now: Injected-clock reading (monotonic seconds).
            iter_value: Iteration number of the owning assistant message.

        Returns:
            ``tool_call`` / ``tool_result`` drafts (possibly empty).
            Snapshot re-emissions never duplicate events.
        """
        pid = part.get("id")
        if not isinstance(pid, str) or not pid:
            return []
        state = part.get("state")
        state = state if isinstance(state, Mapping) else {}
        status = state.get("status")
        call_id = part.get("callID")
        call_id = call_id if isinstance(call_id, str) and call_id else pid
        tool_raw = part.get("tool")
        tool = self._bare_name(tool_raw) if isinstance(tool_raw, str) else ""

        if status == "running":
            drafts = self._announce(tool, call_id, state, iter_value)
            if pid not in self._running and pid not in self._finished:
                self._running[pid] = _RunningTool(
                    part_id=pid,
                    tool=tool,
                    call_id=call_id,
                    since=now,
                    next_beat=now + HEARTBEAT_INTERVAL_S,
                )
            return drafts

        if isinstance(status, str) and status in _TERMINAL_STATUSES:
            if pid in self._finished:
                return []
            self._finished.add(pid)
            drafts = self._announce(tool, call_id, state, iter_value)
            running = self._running.pop(pid, None)
            drafts.append(
                VtEvent(
                    "tool_result",
                    {
                        "tool": tool,
                        "status": "ok" if status == "completed" else "error",
                        "elapsed_ms": self._elapsed_ms(state, running, now),
                        "preview": self._preview(state),
                        "call_id": call_id,
                    },
                )
            )
            self._harvest(tool, state)
            return drafts

        # "pending" (no arguments yet) and unknown statuses: wait.
        return []

    def fire_due(self, now: float) -> list[VtEvent]:
        """Emit every heartbeat whose scheduled time is <= *now*."""
        drafts: list[VtEvent] = []
        for running in self._running.values():
            while running.next_beat <= now:
                drafts.append(
                    VtEvent(
                        "tool_heartbeat",
                        {
                            "tool": running.tool,
                            "call_id": running.call_id,
                            "elapsed_s": round(running.next_beat - running.since, 2),
                        },
                    )
                )
                running.next_beat += HEARTBEAT_INTERVAL_S
        return drafts

    def next_deadline(self) -> float | None:
        """Earliest pending heartbeat time, or ``None`` when idle."""
        if not self._running:
            return None
        return min(running.next_beat for running in self._running.values())

    def cancel_all(self) -> None:
        """Drop every heartbeat schedule (attempt reached a terminal)."""
        self._running.clear()

    # -- internals -------------------------------------------------------------

    def _announce(
        self,
        tool: str,
        call_id: str,
        state: Mapping[Any, Any],
        iter_value: int,
    ) -> list[VtEvent]:
        """Emit the once-per-callID ``tool_call`` with truncated arguments."""
        if call_id in self._announced:
            return []
        self._announced.add(call_id)
        raw_input = state.get("input")
        arguments: dict[str, str] = {}
        if isinstance(raw_input, Mapping):
            arguments = {
                str(key): str(value)[:ARG_VALUE_CHARS]
                for key, value in raw_input.items()
            }
        return [
            VtEvent(
                "tool_call",
                {
                    "tool": tool,
                    "arguments": arguments,
                    "iter": iter_value,
                    "call_id": call_id,
                },
            )
        ]

    @staticmethod
    def _elapsed_ms(
        state: Mapping[Any, Any], running: _RunningTool | None, now: float
    ) -> int:
        """Engine-reported part duration, translator-clock span as fallback."""
        time = state.get("time")
        if isinstance(time, Mapping):
            start, end = time.get("start"), time.get("end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                return max(int(end - start), 0)
        if running is not None:
            return max(int((now - running.since) * 1000), 0)
        return 0

    @staticmethod
    def _preview(state: Mapping[Any, Any]) -> str:
        """``state.output`` first 200 chars — never ``state.title`` (D5)."""
        output = state.get("output")
        return output[:PREVIEW_CHARS] if isinstance(output, str) else ""

    def _harvest(self, tool: str, state: Mapping[Any, Any]) -> None:
        """Stash run_dir from backtest output; surface task child sessions."""
        output = state.get("output")
        if isinstance(output, str) and "backtest" in tool:
            for pattern in _RUN_DIR_PATTERNS:
                match = pattern.search(output)
                if match:
                    # Last backtest of the attempt wins (the final answer
                    # references the most recent run).
                    self.run_dir = match.group(1)
                    break
        if tool == "task":
            metadata = state.get("metadata")
            if isinstance(metadata, Mapping):
                child_sid = metadata.get("sessionId")
                if isinstance(child_sid, str) and child_sid:
                    self.child_session_ids.append(child_sid)
