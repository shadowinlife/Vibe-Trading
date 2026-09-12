"""Text and reasoning translation: delta passthrough with part-type join.

Normative rules (plan D5 + spike report mandatory condition ②):

* **Main path — direct passthrough.** ``message.part.delta`` carries
  ``{sessionID, messageID, partID, field:"text", delta}``; the delta is
  forwarded verbatim as ``text_delta{delta, iter}`` with zero diff
  computation (1.18.30: 946 samples, single shape, version-stable back to
  kimaki's 1.2.15 fixtures).
* **The join is mandatory.** ``field:"text"`` does NOT distinguish text
  from reasoning — reasoning-part deltas carry ``field:"text"`` too (16 of
  17 deltas in scenario a were reasoning). Every delta is joined against
  the ``partID -> part.type`` registry built from ``message.part.updated``
  snapshots before routing; routing on ``field`` alone would leak
  chain-of-thought into chat text.
* **Fallback diffing only.** The cumulative ``message.part.updated``
  snapshot is diffed into ``text_delta`` ONLY for parts that never streamed
  deltas (sparse-event insurance, kimaki prompt_async pit); ``part.time.end``
  is the completion marker (no event of its own).
* **Reasoning -> rolling tail.** ``reasoning_delta{tail, iter, chars}`` with
  a 600-char rolling tail — the frontend REPLACES, never appends
  (``Agent.tsx:694-699``). Emissions are throttled to one per second
  (native ``VT_REASONING_DELTA_MIN_INTERVAL_S`` default), first chunk of
  each iteration immediate, mirroring ``loop.py``'s ``_on_reasoning_chunk``.
* **Suppression.** ``message.summary === true`` compaction messages never
  reach chat (D5); OmO continuation directives injected as ordinary user
  messages (no ``synthetic`` flag — spike §5c) are identified by the
  ``[SYSTEM DIRECTIVE: OH-MY-OPENCODE`` text prefix and suppressed from
  transcript-facing output. User-role parts never emit regardless.

Deltas or snapshots that arrive before their part type / message role is
known are HELD (returned as :data:`HELD`) and re-dispatched by the
orchestrator once the join resolves — observed traces always deliver the
snapshot first, so the hold path is version-drift insurance, not the
golden path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from .events import VtEvent

logger = logging.getLogger("opencode_bridge")

__all__ = [
    "HELD",
    "OMO_DIRECTIVE_PREFIX",
    "REASONING_EMIT_MIN_INTERVAL_S",
    "REASONING_TAIL_CHARS",
    "TextState",
    "is_omo_directive",
]

#: Rolling reasoning tail window (frontend replace-semantics, Agent.tsx:694-699).
REASONING_TAIL_CHARS = 600

#: Throttle floor between reasoning_delta emissions — mirrors the native
#: ``VT_REASONING_DELTA_MIN_INTERVAL_S`` default (env_schema.py:409-411).
REASONING_EMIT_MIN_INTERVAL_S = 1.0

#: OmO stop-hook continuation injected-user-message prefix (spike §5c, D4).
OMO_DIRECTIVE_PREFIX = "[SYSTEM DIRECTIVE: OH-MY-OPENCODE"


class _Held:
    """Sentinel: routing is blocked until the partID/role join resolves."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<HELD>"


#: Returned by handlers that could not route yet (see module docstring).
HELD = _Held()


def is_omo_directive(text: str) -> bool:
    """Whether *text* is an OmO continuation-injected user message.

    Args:
        text: User-message part text.

    Returns:
        ``True`` when the text starts with the OmO stop-hook directive
        prefix — such messages are suppressed from transcript-facing
        output (D4) while still counting as quiescence-resetting activity.
    """
    return text.startswith(OMO_DIRECTIVE_PREFIX)


@dataclass
class _PartTrack:
    """Per-part join state: type, cumulative texts, fallback-diff cursor."""

    part_id: str
    message_id: str | None = None
    part_type: str | None = None
    snapshot_text: str = ""
    delta_acc: str = ""
    emitted_len: int = 0
    saw_delta: bool = False


@dataclass
class _ReasoningTail:
    """Rolling 600-char tail state for the active assistant message."""

    message_id: str | None = None
    tail: str = ""
    chars: int = 0
    last_emit: float | None = None


class TextState:
    """Text/reasoning routing state for one attempt (one session context).

    Owns the ``partID -> part.type`` join registry, the message-role map,
    per-iteration numbering (one assistant message == one ReAct iteration),
    the reasoning tail, and the finalized-text computation used for the
    terminal ``summary`` (D4: last natural completion before terminal).
    """

    def __init__(self) -> None:
        self._parts: dict[str, _PartTrack] = {}
        self._roles: dict[str, str] = {}
        self._suppressed: set[str] = set()
        self._iters: dict[str, int] = {}
        self._iter_counter: int = 0
        self._tail = _ReasoningTail()

    # -- message-level bookkeeping -------------------------------------------

    def note_message(self, info: Mapping[str, Any]) -> None:
        """Record role/summary/iteration facts from a ``message.updated``.

        Args:
            info: The event's ``properties.info`` message object.
        """
        mid = info.get("id")
        if not isinstance(mid, str) or not mid:
            return
        role = info.get("role")
        if isinstance(role, str) and role:
            self._roles[mid] = role
        if info.get("summary") is True:
            # Compaction summary messages never reach chat (D5).
            self._suppressed.add(mid)
        if role == "assistant" and mid not in self._iters:
            self._iter_counter += 1
            self._iters[mid] = self._iter_counter
            # Native semantics: reasoning state resets every iteration.
            self._tail = _ReasoningTail(message_id=mid)

    def iter_of(self, message_id: str | None) -> int:
        """The 1-based iteration number of *message_id* (best effort)."""
        if message_id is not None:
            known = self._iters.get(message_id)
            if known is not None:
                return known
        return max(self._iter_counter, 1)

    @property
    def current_iter(self) -> int:
        """Iteration number of the newest assistant message."""
        return max(self._iter_counter, 1)

    def is_suppressed(self, message_id: str | None) -> bool:
        """Whether *message_id* is filtered from transcript-facing output."""
        return message_id is not None and message_id in self._suppressed

    # -- part / delta routing --------------------------------------------------

    def handle_part(self, part: Mapping[str, Any], now: float) -> list[VtEvent] | _Held:
        """Register a ``message.part.updated`` snapshot; fallback-diff text.

        Args:
            part: The event's ``properties.part`` object (non-tool parts;
                tool parts are routed to :mod:`.tools` by the orchestrator).
            now: Injected-clock reading (monotonic seconds).

        Returns:
            Draft events (``text_delta`` from fallback diffing, or
            ``reasoning_delta`` when a reasoning part only ever arrived as
            snapshots), or :data:`HELD` while the role join is unresolved.
        """
        pid = part.get("id")
        if not isinstance(pid, str) or not pid:
            return []
        track = self._parts.setdefault(pid, _PartTrack(part_id=pid))
        ptype = part.get("type")
        if isinstance(ptype, str) and ptype and track.part_type is None:
            track.part_type = ptype
        mid = part.get("messageID")
        if isinstance(mid, str) and mid:
            track.message_id = mid
        message_id = track.message_id
        role = self._roles.get(message_id) if message_id is not None else None
        if role is None:
            return HELD
        if role == "user" and track.part_type == "text":
            text = part.get("text")
            if isinstance(text, str) and is_omo_directive(text):
                # OmO continuation directive: suppress from transcript (D4).
                self._suppressed.add(message_id or "")
        if track.part_type not in ("text", "reasoning"):
            return []
        if role != "assistant" or self.is_suppressed(message_id):
            return []
        text = part.get("text")
        snapshot = text if isinstance(text, str) else ""
        track.snapshot_text = snapshot
        if track.saw_delta:
            # Passthrough already streamed this part; the snapshot is
            # bookkeeping (and the finalized-text source), never a re-emit.
            return []
        growth = snapshot[track.emitted_len :]
        if not growth:
            return []
        track.emitted_len = len(snapshot)
        if track.part_type == "text":
            return [
                VtEvent(
                    "text_delta",
                    {"delta": growth, "iter": self.iter_of(message_id)},
                )
            ]
        return self._feed_tail(message_id, growth, now)

    def handle_delta(
        self, props: Mapping[str, Any], now: float
    ) -> list[VtEvent] | _Held:
        """Route one ``message.part.delta`` through the partID->type join.

        Args:
            props: The event's ``properties`` object.
            now: Injected-clock reading (monotonic seconds).

        Returns:
            A single ``text_delta`` (verbatim passthrough), a throttled
            ``reasoning_delta``, nothing (suppressed / non-streaming part),
            or :data:`HELD` while the join is unresolved.
        """
        pid = props.get("partID")
        delta = props.get("delta")
        if not isinstance(pid, str) or not isinstance(delta, str) or not delta:
            return []
        track = self._parts.get(pid)
        if track is None or track.part_type is None:
            return HELD
        message_id = track.message_id
        if message_id is None:
            mid = props.get("messageID")
            message_id = mid if isinstance(mid, str) else None
        role = self._roles.get(message_id) if message_id is not None else None
        if role is None:
            return HELD
        track.saw_delta = True
        track.delta_acc += delta
        if role != "assistant" or self.is_suppressed(message_id):
            return []
        if track.part_type == "text":
            return [
                VtEvent(
                    "text_delta",
                    {"delta": delta, "iter": self.iter_of(message_id)},
                )
            ]
        if track.part_type == "reasoning":
            return self._feed_tail(message_id, delta, now)
        return []

    def knows_part_type(self, part_id: str) -> bool:
        """Whether the join registry can already route *part_id*."""
        track = self._parts.get(part_id)
        return track is not None and track.part_type is not None

    # -- finalized text (terminal summary source, D4) --------------------------

    def finalized_text(self, message_id: str | None) -> str:
        """The settled chat text of *message_id*.

        Prefers the cumulative snapshot (authoritative), falls back to the
        delta accumulation when snapshots were sparse. Parts are joined in
        first-seen order.

        Args:
            message_id: Assistant message whose text parts settle the
                terminal ``summary``; ``None`` yields ``""``.

        Returns:
            The concatenated effective text of the message's text parts.
        """
        if message_id is None:
            return ""
        chunks: list[str] = []
        for track in self._parts.values():
            if track.message_id != message_id or track.part_type != "text":
                continue
            snapshot, accumulated = track.snapshot_text, track.delta_acc
            chunks.append(
                snapshot if len(snapshot) >= len(accumulated) else accumulated
            )
        return "".join(chunks)

    # -- reasoning tail ---------------------------------------------------------

    def _feed_tail(
        self, message_id: str | None, delta: str, now: float
    ) -> list[VtEvent]:
        """Roll the 600-char tail; emit at most once per throttle window."""
        tail = self._tail
        if tail.message_id != message_id:
            # A reasoning part of an older/untracked message: keep a fresh
            # tail scoped to it so replace-semantics stay per-iteration.
            tail = _ReasoningTail(message_id=message_id)
            self._tail = tail
        tail.tail = (tail.tail + delta)[-REASONING_TAIL_CHARS:]
        tail.chars += len(delta)
        if (
            tail.last_emit is not None
            and now - tail.last_emit < REASONING_EMIT_MIN_INTERVAL_S
        ):
            return []
        tail.last_emit = now
        return [
            VtEvent(
                "reasoning_delta",
                {
                    "tail": tail.tail,
                    "iter": self.iter_of(message_id),
                    "chars": tail.chars,
                },
            )
        ]
