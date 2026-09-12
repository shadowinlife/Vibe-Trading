"""The vt SSE vocabulary event emitted by the EventTranslator.

One frozen envelope per translated event: ``type`` is a vt SSE event name
the frontend already subscribes to (``useSSE.ts`` knownTypes), ``data`` is
the payload shaped exactly like the native agent loop's emissions
(``loop.py``) plus the ``attempt_id`` stamp the session service adds
(``service.py:502``). The frontend is the contract — payloads are never
reshaped to make translation easier (plan T4 Must-NOT).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["VT_EVENT_TYPES", "VtEvent"]


@dataclass(frozen=True, slots=True)
class VtEvent:
    """One translated event in the vt SSE vocabulary.

    Attributes:
        type: vt event name — one of :data:`VT_EVENT_TYPES`.
        data: Payload fields exactly per plan D5 (e.g.
            ``text_delta{delta, iter, attempt_id}``). Every event emitted
            inside an announced attempt carries ``attempt_id``.
    """

    type: str
    data: dict[str, Any]


VT_EVENT_TYPES = frozenset(
    {
        "text_delta",
        "reasoning_delta",
        "tool_call",
        "tool_result",
        "tool_heartbeat",
        "tool_progress",
        "llm_usage",
        "stream_reset",
        "attempt.completed",
        "attempt.failed",
        "attempt.cancelled",
    }
)
