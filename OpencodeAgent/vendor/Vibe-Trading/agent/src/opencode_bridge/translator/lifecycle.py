"""Attempt lifecycle: quiescence terminal, novelty filter, abort, failure.

Normative rules (plan D4 + the three Phase-0 mandatory conditions):

* **Terminal only on a QUIESCENT idle.** ``session.idle`` arms a quiescence
  timer; the ``attempt.completed`` terminal fires only when the timer
  expires with no intervening genuine activity. **Default 8.0 s** —
  mandatory condition ①: OmO's stop-hook continuation re-prompts 6.4 s
  after idle (n=2, sigma < 0.02 s, timer-driven), falsifying the initial
  3.0 s value (spike §5c/§7f).
* **Novelty filter.** Within ~12 ms after ``session.idle`` the server
  re-emits bookkeeping (``session.updated``, ``session.diff``, a
  ``message.updated`` for the ORIGINAL user message). A timer that resets
  on any event never expires — this bug was hit live during the spike.
  Activity = a message/part event for a message id never seen before,
  ``session.status busy``, ``session.error``, or a permission event
  (``ActivityScanner`` semantics from the T1 ``analyze_traces.py``,
  re-implemented here because src must not import test fixtures).
* **Idle handling is sessionID-scoped** — mandatory condition ③: subagent
  child sessions emit their own idles on the same ``/event`` stream up to
  14 s before the parent's (spike §5d). Only the idle of the session with
  the announced attempt arms its timer (enforced by the orchestrator's
  per-session dispatch); double idles re-arm harmlessly.
* **Abort is bridge-recorded state (idle != completed).** ``note_abort``
  emits ``attempt.cancelled`` immediately; the engine's aftermath
  (``session.error MessageAbortedError``, double ``session.idle``, late
  part snapshots) arrives post-terminal and is tolerated, never re-terminal.
* **Failure.** ``session.error`` (without abort) parks the error; the
  terminal fires as ``attempt.failed`` on the next sessionID-scoped idle,
  or — kimaki #74 guard, kept despite the spike always observing
  error->idle — when no idle follows within ``quiescence_s`` (synthetic
  idle). Genuine novel activity before the idle clears the parked failure
  (engine recovery, e.g. ``continue_loop_on_deny``).
* **Finalized text** = the last natural completion
  (``message.time.completed && finish != "tool-calls"``) before the
  terminal; continuation rounds re-settle it. Assistant-message persistence
  is T5's job at terminal time — the translator only carries ``summary``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .text import TextState
from .tools import ToolState

logger = logging.getLogger("opencode_bridge")

__all__ = ["AttemptContext", "LifecycleState"]


def format_engine_error(error: Any) -> str:
    """Render a ``session.error`` payload as the terminal ``error`` string.

    Args:
        error: The ``properties.error`` object, e.g.
            ``{"name": "MessageAbortedError", "data": {"message": ...}}``.

    Returns:
        ``"<name>: <message>"`` when both are present, either one alone
        otherwise, or a generic fallback for unrecognised shapes.
    """
    if isinstance(error, str) and error:
        return error
    if isinstance(error, Mapping):
        name = error.get("name")
        data = error.get("data")
        message = data.get("message") if isinstance(data, Mapping) else None
        if isinstance(name, str) and isinstance(message, str):
            return f"{name}: {message}"
        if isinstance(message, str) and message:
            return message
        if isinstance(name, str) and name:
            return name
    return "engine reported a session error"


class LifecycleState:
    """Quiescence / failure / runtime-identity state for one attempt.

    Args:
        quiescence_s: Silence window after ``session.idle`` before the
            completed terminal fires (mandatory condition ①: default 8.0).
    """

    def __init__(self, quiescence_s: float) -> None:
        self.quiescence_s = quiescence_s
        self.quiescence_deadline: float | None = None
        self.failure_error: str | None = None
        self.synthetic_idle_deadline: float | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self._seen_message_ids: set[str] = set()
        self._usage_emitted: set[str] = set()

    # -- novelty filter (ActivityScanner semantics, analyze_traces.py) --------

    def message_is_novel(self, info: Mapping[str, Any]) -> bool:
        """Whether this ``message.updated`` proves resumed WORK.

        Args:
            info: The event's ``properties.info`` message object.

        Returns:
            ``True`` only for a message id never seen before — post-idle
            bookkeeping re-emits the ORIGINAL user message and must not
            reset the quiescence timer.
        """
        mid = info.get("id")
        if not isinstance(mid, str) or not mid:
            return False
        if mid in self._seen_message_ids:
            return False
        self._seen_message_ids.add(mid)
        return True

    def part_message_is_novel(self, message_id: Any) -> bool:
        """Novelty of a part event, keyed by its owning message id."""
        if not isinstance(message_id, str) or not message_id:
            return False
        if message_id in self._seen_message_ids:
            return False
        self._seen_message_ids.add(message_id)
        return True

    def on_activity(self) -> None:
        """Genuine activity: cancel the quiescence timer and parked failure."""
        self.quiescence_deadline = None
        self.failure_error = None
        self.synthetic_idle_deadline = None

    # -- timer arming -----------------------------------------------------------

    def arm_quiescence(self, now: float) -> None:
        """Arm the completed-terminal timer on a sessionID-scoped idle."""
        self.quiescence_deadline = now + self.quiescence_s

    def note_error(self, error: Any, now: float) -> None:
        """Park a ``session.error`` and arm the kimaki-#74 synthetic idle."""
        self.failure_error = format_engine_error(error)
        self.quiescence_deadline = None
        self.synthetic_idle_deadline = now + self.quiescence_s

    def clear_deadlines(self) -> None:
        """Drop every pending timer (terminal reached)."""
        self.quiescence_deadline = None
        self.synthetic_idle_deadline = None

    def next_deadline(self) -> float | None:
        """Earliest lifecycle deadline, or ``None`` when nothing is armed."""
        deadlines = [
            deadline
            for deadline in (self.quiescence_deadline, self.synthetic_idle_deadline)
            if deadline is not None
        ]
        return min(deadlines) if deadlines else None

    # -- runtime identity + usage ------------------------------------------------

    def note_runtime_identity(self, info: Mapping[str, Any]) -> None:
        """Track provider/model from assistant ``message.updated`` infos."""
        provider = info.get("providerID")
        if isinstance(provider, str) and provider:
            self.provider = provider
        model = info.get("modelID")
        if isinstance(model, str) and model:
            self.model = model

    def usage_payload(
        self, info: Mapping[str, Any], iter_value: int
    ) -> dict[str, Any] | None:
        """Best-effort ``llm_usage`` from message tokens, once per message.

        opencode reports cumulative per-message tokens
        (``{total, input, output, reasoning, cache}``); the vt payload is
        the native ``{input_tokens, output_tokens, total_tokens, iter}``
        shape (``loop.py:1360-1366``). Duplicate ``message.updated``
        emissions are normal — dedupe by message id.
        """
        mid = info.get("id")
        if not isinstance(mid, str) or mid in self._usage_emitted:
            return None
        tokens = info.get("tokens")
        if not isinstance(tokens, Mapping):
            return None
        input_tokens = _as_int(tokens.get("input"))
        output_tokens = _as_int(tokens.get("output"))
        total_tokens = _as_int(tokens.get("total"))
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        if not (input_tokens or output_tokens or total_tokens):
            return None
        self._usage_emitted.add(mid)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "iter": iter_value,
        }


def _as_int(value: Any) -> int:
    """Coerce a token count defensively (drift-tolerant)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    return 0


@dataclass
class AttemptContext:
    """Per-session translation state for one announced attempt.

    Created by ``note_attempt`` (the service assigns attempt ids — the
    translator only stamps them) and replaced wholesale on the next
    attempt. ``terminal_emitted`` drives the post-terminal drop guard
    (late deltas after a done archive would produce duplicate answer
    bubbles, ``Agent.tsx:855-889``).
    """

    session_id: str
    attempt_id: str
    started_at: float
    quiescence_s: float
    bare_name: Callable[[str], str]
    text: TextState = field(init=False)
    tools: ToolState = field(init=False)
    life: LifecycleState = field(init=False)
    abort_requested: bool = False
    terminal_emitted: bool = False
    #: Message id of the last natural completion (finalized-text source).
    summary_source_id: str | None = None
    #: Events held because the partID/role join had not resolved yet.
    held: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = TextState()
        self.tools = ToolState(self.bare_name)
        self.life = LifecycleState(self.quiescence_s)

    def next_deadline(self) -> float | None:
        """Earliest deadline across tools and lifecycle timers."""
        candidates = [
            deadline
            for deadline in (self.tools.next_deadline(), self.life.next_deadline())
            if deadline is not None
        ]
        return min(candidates) if candidates else None

    # -- terminal payloads (D5 full-field enumeration) ---------------------------

    def completed_payload(self, now: float) -> dict[str, Any]:
        """``attempt.completed`` — every D5 field, service.py:408-414 aligned."""
        payload: dict[str, Any] = {
            "attempt_id": self.attempt_id,
            "status": "completed",
            "summary": self.text.finalized_text(self.summary_source_id),
            "run_dir": self.tools.run_dir,
            "elapsed_ms": self._elapsed_ms(now),
            "provider": self.life.provider,
            "model": self.life.model,
        }
        # reasoning_effort is optional in D5 and opencode does not expose
        # it on the event surface — omitted rather than fabricated.
        return payload

    def failed_payload(self) -> dict[str, Any]:
        """``attempt.failed{attempt_id, error}`` (D5, service.py:441 shape)."""
        return {
            "attempt_id": self.attempt_id,
            "error": self.life.failure_error or "engine reported a session error",
        }

    def cancelled_payload(self) -> dict[str, Any]:
        """``attempt.cancelled{attempt_id, status}`` (service.py:426-430)."""
        return {"attempt_id": self.attempt_id, "status": "cancelled"}

    def _elapsed_ms(self, now: float) -> int:
        return max(int((now - self.started_at) * 1000), 0)
