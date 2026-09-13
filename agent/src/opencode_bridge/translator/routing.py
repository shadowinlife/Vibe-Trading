"""Allowlist dispatch of opencode wire events into per-session state.

The router is the drift-defense gate (spike §8): known-ignored wire types
pass silently, unknown types are ignored with a one-time warning, and only
the translated vocabulary reaches a session's :class:`AttemptContext`.
It also owns the child-session registry (D4 ``child_events="drop"``) and
the post-terminal drop guard (late deltas after the done archive would
spawn duplicate answer bubbles, ``Agent.tsx:855-889``).

Routing semantics per wire type are documented on the handlers below and
in plan D4/D5; the novelty filter that decides quiescence resets lives in
:mod:`.lifecycle` (``LifecycleState``).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from ..events import OpencodeEvent
from .events import VtEvent
from .lifecycle import AttemptContext
from .text import _Held

logger = logging.getLogger("opencode_bridge")

__all__ = ["WireRouter", "sid_of", "stamped"]

_HELD_CAP = 500

# Spike report §3 vocabulary: translated by the handlers below.
_HANDLED_TYPES = frozenset(
    {
        "message.updated",
        "message.part.updated",
        "message.part.delta",
        "session.status",
        "session.idle",
        "session.error",
        "session.created",
        "session.updated",
        "session.deleted",
        "permission.asked",
        "permission.replied",
    }
)

# Spike report §3 vocabulary: known, irrelevant to translation — silent.
_IGNORED_TYPES = frozenset(
    {
        "server.connected",
        "server.heartbeat",
        "session.diff",
        "todo.updated",
        "file.edited",
        "file.watcher.updated",
        "tui.toast.show",
        "catalog.updated",
        "integration.updated",
        "plugin.added",
        "reference.updated",
    }
)

#: Builds one attempt terminal and freezes the context (EventTranslator).
TerminalFn = Callable[[AttemptContext, str, float], VtEvent]


def sid_of(props: Mapping[str, Any]) -> str | None:
    """The session an event belongs to (analyze_traces.sid_of semantics)."""
    sid = props.get("sessionID")
    if isinstance(sid, str) and sid:
        return sid
    info = props.get("info")
    if isinstance(info, Mapping):
        nested = info.get("sessionID")
        if isinstance(nested, str) and nested:
            return nested
    return None


def stamped(ctx: AttemptContext, drafts: list[VtEvent]) -> list[VtEvent]:
    """Stamp ``attempt_id`` into every emitted payload (service.py:502)."""
    return [
        (
            draft
            if "attempt_id" in draft.data
            else VtEvent(draft.type, {**draft.data, "attempt_id": ctx.attempt_id})
        )
        for draft in drafts
    ]


class WireRouter:
    """Route one ``/event`` stream across sessionID-scoped attempt contexts.

    Args:
        sessions: Live ``sessionID -> AttemptContext`` map (owned by the
            translator; contexts exist only for announced attempts).
        terminal: Callback building an attempt terminal event.
    """

    def __init__(
        self,
        *,
        sessions: dict[str, AttemptContext],
        terminal: TerminalFn,
    ) -> None:
        self._sessions = sessions
        self._terminal = terminal
        self._child_sids: set[str] = set()
        self._child_parent: dict[str, str] = {}
        self._warned_types: set[str] = set()

    def route(self, event: OpencodeEvent, now: float) -> list[VtEvent]:
        """Translate one wire event into (stamped) vt draft events."""
        etype = event.type
        if etype in _IGNORED_TYPES:
            return []
        if etype not in _HANDLED_TYPES:
            if etype not in self._warned_types:
                self._warned_types.add(etype)
                logger.warning(
                    "ignoring unknown opencode event type %r "
                    "(allowlist / version-drift defense)",
                    etype,
                )
            return []
        props = event.properties
        if etype in ("session.created", "session.updated"):
            self._track_child(props)
            return []
        sid = sid_of(props)
        if sid is None:
            return []
        if etype == "session.deleted":
            self._forget(sid)
            return []
        if sid in self._child_sids:
            logger.debug("dropping child-session event %s (child_events=drop)", etype)
            return []
        ctx = self._sessions.get(sid)
        if ctx is None:
            # No attempt announced for this session: nothing to translate
            # against (T5 announces before prompt_async — subscribe-first).
            return []
        if ctx.terminal_emitted:
            self._drop_post_terminal(ctx, etype)
            return []
        match etype:
            case "message.updated":
                drafts = self._on_message_updated(ctx, event, now)
            case "message.part.updated":
                drafts = self._on_message_part_updated(ctx, event, now)
            case "message.part.delta":
                drafts = self._on_message_part_delta(ctx, event, now)
            case "session.status":
                drafts = self._on_session_status(ctx, props)
            case "session.idle":
                drafts = self._on_session_idle(ctx, now)
            case "session.error":
                drafts = self._on_session_error(ctx, props, now)
            case _:  # permission.asked / permission.replied
                ctx.life.on_activity()
                drafts = []
        return stamped(ctx, drafts)

    # -- wire handlers -----------------------------------------------------------

    def _on_message_updated(
        self, ctx: AttemptContext, event: OpencodeEvent, now: float
    ) -> list[VtEvent]:
        info = event.properties.get("info")
        if not isinstance(info, Mapping):
            return []
        if ctx.life.message_is_novel(info):
            ctx.life.on_activity()
        ctx.text.note_message(info)
        drafts: list[VtEvent] = []
        mid = info.get("id")
        mid = mid if isinstance(mid, str) else None
        if info.get("role") == "assistant":
            ctx.life.note_runtime_identity(info)
            usage = ctx.life.usage_payload(info, ctx.text.iter_of(mid))
            if usage is not None:
                drafts.append(VtEvent("llm_usage", usage))
            message_time = info.get("time")
            completed = (
                isinstance(message_time, Mapping)
                and message_time.get("completed") is not None
            )
            finish = info.get("finish")
            if completed and finish not in (None, "tool-calls"):
                # Natural completion (D4): the finalized text of THIS
                # message settles the terminal summary; a continuation
                # round re-settles it later.
                ctx.summary_source_id = mid
        drafts.extend(self._flush_held(ctx, now))
        return drafts

    def _on_message_part_updated(
        self, ctx: AttemptContext, event: OpencodeEvent, now: float
    ) -> list[VtEvent]:
        part = event.properties.get("part")
        if not isinstance(part, Mapping):
            return []
        mid = part.get("messageID") or event.properties.get("messageID")
        if ctx.life.part_message_is_novel(mid):
            ctx.life.on_activity()
        if part.get("type") == "tool":
            iter_value = ctx.text.iter_of(mid if isinstance(mid, str) else None)
            drafts = ctx.tools.handle_part(part, now, iter_value)
            self._absorb_child_sids(ctx)
            return drafts
        result = ctx.text.handle_part(part, now)
        if isinstance(result, _Held):
            self._hold(ctx, event)
            return []
        drafts = list(result)
        drafts.extend(self._flush_held(ctx, now))
        return drafts

    def _on_message_part_delta(
        self, ctx: AttemptContext, event: OpencodeEvent, now: float
    ) -> list[VtEvent]:
        props = event.properties
        if ctx.life.part_message_is_novel(props.get("messageID")):
            ctx.life.on_activity()
        result = ctx.text.handle_delta(props, now)
        if isinstance(result, _Held):
            self._hold(ctx, event)
            return []
        return list(result)

    def _on_session_status(
        self, ctx: AttemptContext, props: Mapping[str, Any]
    ) -> list[VtEvent]:
        status = props.get("status")
        status = status if isinstance(status, Mapping) else {}
        kind = status.get("type")
        if kind == "busy":
            ctx.life.on_activity()
            return []
        if kind == "retry":
            # Degradation #14: best-effort stream_reset (never observed in
            # the spike corpus — payload read defensively).
            ctx.life.on_activity()
            payload: dict[str, Any] = {
                "iter": ctx.text.current_iter,
                "reason": "engine_retry",
            }
            attempt_no = status.get("attempt")
            if isinstance(attempt_no, int) and not isinstance(attempt_no, bool):
                payload["retry_attempt"] = attempt_no
            message = status.get("message")
            if isinstance(message, str) and message:
                payload["message"] = message[:200]
            return [VtEvent("stream_reset", payload)]
        return []

    def _on_session_idle(self, ctx: AttemptContext, now: float) -> list[VtEvent]:
        # sessionID-scoped by construction (mandatory condition ③): only
        # the idle of THIS session reaches its context — child idles are
        # dropped by the child filter above, never terminating the parent.
        if ctx.life.failure_error is not None:
            return [self._terminal(ctx, "failed", now)]
        ctx.life.arm_quiescence(now)
        return []

    def _on_session_error(
        self, ctx: AttemptContext, props: Mapping[str, Any], now: float
    ) -> list[VtEvent]:
        if ctx.abort_requested:
            # Expected abort aftermath — cancelled was already emitted.
            logger.debug(
                "session %s: ignoring session.error after abort", ctx.session_id
            )
            return []
        ctx.life.note_error(props.get("error"), now)
        return []

    # -- post-terminal guard -------------------------------------------------------

    def _drop_post_terminal(self, ctx: AttemptContext, etype: str) -> None:
        """Drop same-attempt events after the terminal (duplicate-bubble guard).

        The frontend only guards stopped/timeout states
        (``Agent.tsx:658-684``); a late delta after the done archive spawns
        a duplicate answer bubble (``Agent.tsx:855-889``) — so the bridge
        drops and warns. Abort aftermath and session bookkeeping are
        expected, not hazards (debug level).
        """
        if etype.startswith("session.") or ctx.abort_requested:
            logger.debug(
                "dropping post-terminal %s for attempt %s", etype, ctx.attempt_id
            )
        else:
            logger.warning(
                "dropping late %s event after attempt %s reached its terminal "
                "(duplicate-bubble guard)",
                etype,
                ctx.attempt_id,
            )

    # -- held-event join buffer ------------------------------------------------------

    def _hold(self, ctx: AttemptContext, event: OpencodeEvent) -> None:
        ctx.held.append(event)
        if len(ctx.held) > _HELD_CAP:
            overflow = len(ctx.held) - _HELD_CAP
            del ctx.held[:overflow]
            logger.warning(
                "session %s: held-event buffer overflow, dropped %d "
                "unresolved events",
                ctx.session_id,
                overflow,
            )

    def _flush_held(self, ctx: AttemptContext, now: float) -> list[VtEvent]:
        """Retry held events once the partID/role join may have resolved."""
        if not ctx.held:
            return []
        drafts: list[VtEvent] = []
        remaining: list[OpencodeEvent] = []
        for event in ctx.held:
            result = self._route_held(ctx, event, now)
            if isinstance(result, _Held):
                remaining.append(event)
            else:
                drafts.extend(result)
        ctx.held = remaining
        return drafts

    @staticmethod
    def _route_held(
        ctx: AttemptContext, event: OpencodeEvent, now: float
    ) -> list[VtEvent] | _Held:
        if event.type == "message.part.delta":
            return ctx.text.handle_delta(event.properties, now)
        part = event.properties.get("part")
        if isinstance(part, Mapping):
            return ctx.text.handle_part(part, now)
        return []

    # -- child-session registry --------------------------------------------------------

    def _absorb_child_sids(self, ctx: AttemptContext) -> None:
        for child_sid in ctx.tools.child_session_ids:
            if child_sid not in self._child_sids:
                logger.debug(
                    "registered child session %s from task part (parent %s)",
                    child_sid,
                    ctx.session_id,
                )
            self._child_sids.add(child_sid)
            self._child_parent.setdefault(child_sid, ctx.session_id)
        ctx.tools.child_session_ids.clear()

    def _track_child(self, props: Mapping[str, Any]) -> None:
        """Live child tracking: ``session.created/updated`` ``info.parentID``."""
        info = props.get("info")
        if not isinstance(info, Mapping):
            return
        parent = info.get("parentID")
        sid = info.get("id")
        if (
            isinstance(parent, str)
            and parent
            and isinstance(sid, str)
            and sid
            and sid != parent
        ):
            if sid not in self._child_sids:
                logger.debug("registered child session %s (parent %s)", sid, parent)
            self._child_sids.add(sid)
            self._child_parent[sid] = parent

    def _forget(self, sid: str) -> None:
        """Drop state for a deleted session and its cascaded children (F7)."""
        self._sessions.pop(sid, None)
        self._child_sids.discard(sid)
        self._child_parent.pop(sid, None)
        for child, parent in list(self._child_parent.items()):
            if parent == sid:
                self._child_sids.discard(child)
                self._child_parent.pop(child, None)
