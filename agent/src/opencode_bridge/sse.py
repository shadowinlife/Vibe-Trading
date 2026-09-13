"""Hand-rolled Server-Sent Events frame parser for the opencode bridge.

``httpx-sse`` is deliberately NOT a project dependency (work plan D10), so
the SSE wire framing is parsed here. The parser follows the WHATWG
``text/event-stream`` processing model for the fields opencode can emit:

* ``data:`` lines accumulate into the frame payload (joined with ``\\n``);
* an empty line dispatches the accumulated frame;
* ``:``-prefixed lines are comments and are ignored;
* ``event:`` and ``id:`` are captured for completeness even though opencode
  1.18.x sends ``data:``-only frames (the event type lives inside the JSON
  payload — see the Phase-0 spike report §3);
* ``retry:`` and unknown fields are ignored (the reconnect policy is owned
  by :class:`~src.opencode_bridge.driver.OpencodeDriver`, not by the wire).

The parser is incremental and transport-agnostic: feed it text chunks of any
size (split anywhere, including mid-line) and it yields complete
:class:`SseFrame` objects as they are dispatched. Per the spec, a frame that
is still open when the stream ends is discarded — a truncated final frame is
corrupt by definition and must not be delivered half-parsed.

Line endings ``\\n`` and ``\\r\\n`` are both accepted. A lone ``\\r`` as a
line terminator is not handled: it never appears on opencode's wire and
supporting it would require lookahead that complicates the chunk boundary
logic for zero observed benefit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

__all__ = ["SseFrame", "SseFrameParser"]


@dataclass(frozen=True, slots=True)
class SseFrame:
    """One dispatched SSE frame.

    Attributes:
        data: The accumulated ``data:`` payload (multi-line data joined
            with ``\\n``, exactly as the SSE spec prescribes).
        event: The ``event:`` field value, or ``"message"`` when the frame
            carried none (the spec default). opencode 1.18.x never sets it.
        id: The last ``id:`` field value seen so far on the stream (the SSE
            "last event ID" is sticky across frames per spec), or ``""``.
    """

    data: str
    event: str = "message"
    id: str = ""


class SseFrameParser:
    """Incremental SSE parser: feed text chunks, yield complete frames.

    One parser instance belongs to exactly one stream connection; create a
    fresh parser on every reconnect so partial state never leaks across
    connections.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._data_lines: list[str] = []
        self._event_type = ""
        self._last_event_id = ""

    def feed(self, chunk: str) -> Iterator[SseFrame]:
        """Consume one text chunk and yield every frame it completes.

        Args:
            chunk: Decoded stream text, split at arbitrary boundaries.

        Yields:
            Each :class:`SseFrame` dispatched by an empty line in the
            accumulated stream text, in wire order.
        """
        self._buffer += chunk
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                return
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1 :]
            if line.endswith("\r"):
                line = line[:-1]
            frame = self._process_line(line)
            if frame is not None:
                yield frame

    def _process_line(self, line: str) -> SseFrame | None:
        """Process one complete stream line; return a frame on dispatch."""
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None  # comment / keep-alive
        field, colon, value = line.partition(":")
        if colon:
            # Spec: strip exactly one leading space from the value.
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        self._apply_field(field, value)
        return None

    def _apply_field(self, field: str, value: str) -> None:
        if field == "data":
            self._data_lines.append(value)
        elif field == "event":
            self._event_type = value
        elif field == "id":
            # Spec: ignore ids containing NUL; empty id clears the field.
            if "\x00" not in value:
                self._last_event_id = value
        # "retry" and unknown fields are ignored by design (module docstring).

    def _dispatch(self) -> SseFrame | None:
        """Dispatch the accumulated frame; empty data means no dispatch."""
        if not self._data_lines:
            # Spec: reset the event-type buffer and dispatch nothing.
            self._event_type = ""
            return None
        frame = SseFrame(
            data="\n".join(self._data_lines),
            event=self._event_type or "message",
            id=self._last_event_id,
        )
        self._data_lines = []
        self._event_type = ""
        return frame
