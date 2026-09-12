"""Structural types for the opencode bridge session service (T5).

The orchestrator ruling for T5: the concrete ``EventTranslator`` is built in
parallel (T4) and must NOT be imported by the service — the service codes
against the local structural Protocols below, and T4's class satisfies them
structurally (concrete wiring is verified at T7).
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Protocol

from .events import OpencodeEvent

__all__ = ["EventTranslatorLike", "VtEventLike"]


class VtEventLike(Protocol):
    """Structural shape of one translated vt-vocabulary event (T4 output).

    Attributes:
        type: vt SSE event name (``text_delta``, ``tool_call``,
            ``attempt.completed``, ...).
        data: Mutable payload dict. Routing keys, in precedence order:
            ``attempt_id``, ``session_id`` (vt), ``sessionID`` (engine
            passthrough).
    """

    type: str
    data: Dict[str, Any]


class EventTranslatorLike(Protocol):
    """Binding translator interface for the bridge service.

    ``session_id`` arguments are ENGINE (opencode) session ids: the
    translator correlates raw ``/event`` stream events by
    ``properties.sessionID`` and cannot route without them (see the service
    module docstring for the full adjudication).
    """

    async def feed(self, event: OpencodeEvent) -> None:
        """Consume one engine-native event from the driver stream."""
        ...

    def events(self) -> AsyncIterator[VtEventLike]:
        """Yield translated vt-vocabulary events (single consumer)."""
        ...

    def note_attempt(self, session_id: str, attempt_id: str) -> None:
        """Bind engine *session_id* to vt *attempt_id* for one run."""
        ...

    def note_abort(self, session_id: str) -> None:
        """Flag a user abort so the run terminates as cancelled, not completed."""
        ...

    async def aclose(self) -> None:
        """Release translator resources (service shutdown)."""
        ...
