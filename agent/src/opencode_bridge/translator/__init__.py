"""EventTranslator: opencode-native events -> vt SSE vocabulary (plan T4).

The semantic heart of the engine bridge. Consumes driver-native
:class:`~src.opencode_bridge.events.OpencodeEvent` via :meth:`feed`,
emits :class:`VtEvent`\\ s — including TIMER-DRIVEN emissions (quiescence
terminals, 3 s tool heartbeats) — from :meth:`events`. Normative rules
live in plan D4/D5 and the submodules (:mod:`.text`, :mod:`.tools`,
:mod:`.lifecycle`, :mod:`.routing`); the Phase-0 spike (opencode 1.18.30
+ OmO 4.19.4) fixed its three mandatory conditions: quiescence default
8.0 s, the partID->part.type join before text routing, and
sessionID-scoped idle handling — all implemented here.

Binding public API (orchestrator ruling for parallel work — T5 codes
against this exact surface):

.. code-block:: python

    EventTranslator(*, quiescence_s=8.0, child_events="drop", clock=None)
    await translator.feed(event)            # non-blocking ingest
    async for vt_event in translator.events(): ...   # one consumer
    translator.note_attempt(session_id, attempt_id)  # service announces
    translator.note_abort(session_id)       # next terminal = cancelled
    await translator.aclose()

Documented addition to the binding surface (structurally compatible —
every documented call shape stays valid): an optional ``tool_map``
keyword argument plus a settable :attr:`EventTranslator.tool_map`
property. Justification: D5 requires bare vt tool names in
``tool_call``/``tool_result`` (relay exact-match, ``sessions_routes.py``),
and T3 owns the prefixed->bare table (``tool_names.ToolNameMap``); the
translator must consume it, and injection keeps it free of env/driver
coupling. T5 passes ``driver.tool_map`` (or sets it after
``load_tool_mapping()``).

Virtual-clock contract (golden tests drive virtual time — no real
sleeping): ``clock`` returns monotonic seconds; timers fire when a
generator wake-up observes ``clock()`` past their deadline. With an
injected clock the caller advances time and feeds any event (ignored
types like ``server.heartbeat`` work) to wake the generator; catch-up
emissions are deterministic (heartbeat ``elapsed_s`` uses the beat's
SCHEDULED time). In production (real clock) the generator also wakes on
real-time deadline expiry, so heartbeats stay punctual while the wire is
silent (B3: measured 120.75 s intra-tool silence would trip the frontend
90 s watchdog).

Posture (spike §8): the wire vocabulary is ALLOWLISTED — see
:mod:`.routing`. Degradation list: #11 ``tool_progress`` — opencode
1.18.30 exposes no MCP progress notifications on ``/event``, so none are
emitted; #14 ``stream_reset`` — best-effort from ``session.status
{type:"retry"}`` (never observed in the spike corpus).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, AsyncIterator, Callable

from ..events import OpencodeEvent
from ..tool_names import EMPTY_TOOL_NAME_MAP, ToolNameMap
from .events import VT_EVENT_TYPES, VtEvent
from .lifecycle import AttemptContext
from .routing import WireRouter, stamped

logger = logging.getLogger("opencode_bridge")

__all__ = ["EventTranslator", "VT_EVENT_TYPES", "VtEvent"]


class _Sentinel:
    """Private inbox marker (wake-up / close), never a wire event."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self._name}>"


_WAKE = _Sentinel("WAKE")
_CLOSE = _Sentinel("CLOSE")


class EventTranslator:
    """State machine translating one opencode ``/event`` stream to vt SSE.

    One instance serves the whole stream (all sessions); state is
    sessionID-scoped and only sessions with an announced attempt
    (:meth:`note_attempt`) translate. Child (subagent) sessions are
    tracked and dropped per D4 (see :mod:`.routing`). Exactly one
    consumer may iterate :meth:`events` (a second concurrent iteration
    raises ``RuntimeError``), mirroring the driver contract; fan-out is
    the service's job (T5).
    """

    def __init__(
        self,
        *,
        quiescence_s: float = 8.0,
        child_events: str = "drop",
        clock: Callable[[], float] | None = None,
        tool_map: ToolNameMap = EMPTY_TOOL_NAME_MAP,
    ) -> None:
        """Bind a translator.

        Args:
            quiescence_s: Silence window after ``session.idle`` before the
                completed terminal fires. Default 8.0 — mandatory
                condition ①: OmO re-prompts 6.4 s after idle; 3.0 was
                falsified (spike §5c/§7f).
            child_events: Child-session policy; only ``"drop"`` (D4).
            clock: Injectable monotonic-seconds source (golden tests
                drive virtual time); defaults to :func:`time.monotonic`.
            tool_map: T3's prefixed->bare tool-name table (module
                docstring); replaceable at runtime via the property.

        Raises:
            ValueError: ``quiescence_s`` is not positive.
        """
        if quiescence_s <= 0:
            raise ValueError(f"quiescence_s must be positive, got {quiescence_s}")
        if child_events != "drop":
            logger.warning(
                "child_events=%r is not supported by the T4 translator; "
                "falling back to 'drop' (D4)",
                child_events,
            )
        self._quiescence_s = quiescence_s
        self._clock: Callable[[], float] = (
            clock if clock is not None else time.monotonic
        )
        self._tool_map = tool_map
        self._sessions: dict[str, AttemptContext] = {}
        self._router = WireRouter(sessions=self._sessions, terminal=self._terminal)
        self._inbox: asyncio.Queue[Any] = asyncio.Queue()
        self._sync_pending: deque[VtEvent] = deque()
        self._stream_active = False
        self._closed = False

    # -- binding public API ----------------------------------------------------

    @property
    def tool_map(self) -> ToolNameMap:
        """The active prefixed->bare tool-name table."""
        return self._tool_map

    @tool_map.setter
    def tool_map(self, value: ToolNameMap) -> None:
        self._tool_map = value

    async def feed(self, event: OpencodeEvent) -> None:
        """Ingest one driver-native event (non-blocking)."""
        if self._closed:
            logger.debug("feed() after aclose() dropped: %s", event.type)
            return
        self._inbox.put_nowait(event)

    def note_attempt(self, session_id: str, attempt_id: str) -> None:
        """Announce a new attempt; emitted events stamp this *attempt_id*.

        The service assigns attempt ids (T5) — the translator never
        invents them. Announcing replaces any prior attempt state for the
        session (a still-active one is a service-contract violation and
        is logged).
        """
        existing = self._sessions.get(session_id)
        if existing is not None and not existing.terminal_emitted:
            logger.warning(
                "note_attempt replaces active attempt %s on session %s",
                existing.attempt_id,
                session_id,
            )
        self._sessions[session_id] = AttemptContext(
            session_id=session_id,
            attempt_id=attempt_id,
            started_at=self._clock(),
            quiescence_s=self._quiescence_s,
            bare_name=self._bare,
        )

    def note_abort(self, session_id: str) -> None:
        """Announce a user abort: emit ``attempt.cancelled`` now (D4).

        Abort is bridge-recorded state — idle != completed. The engine's
        aftermath (``session.error MessageAbortedError``, double
        ``session.idle``, late part snapshots — spike §5b) arrives
        post-terminal and is tolerated, never re-terminal.
        """
        ctx = self._sessions.get(session_id)
        if ctx is None or ctx.terminal_emitted:
            logger.debug(
                "note_abort for session %s without an active attempt; ignored",
                session_id,
            )
            return
        ctx.abort_requested = True
        self._sync_pending.append(self._terminal(ctx, "cancelled", self._clock()))
        self._inbox.put_nowait(_WAKE)

    async def aclose(self) -> None:
        """Stop the translator; the :meth:`events` iteration ends."""
        if self._closed:
            return
        self._closed = True
        self._inbox.put_nowait(_CLOSE)

    async def events(self) -> AsyncIterator[VtEvent]:
        """Yield translated vt events, including timer-driven emissions.

        The loop wakes on inbox items AND on real-time deadline expiry
        (production clock), so quiescence terminals and tool heartbeats
        fire even while the wire is silent (B3). With an injected virtual
        clock, wakes come from feeds — see the module docstring.

        Raises:
            RuntimeError: Another consumer is already iterating.
        """
        if self._stream_active:
            raise RuntimeError(
                "events() already has an active consumer on this translator "
                "instance; fan out from the single consumer instead"
            )
        self._stream_active = True
        get_task: asyncio.Task[Any] | None = None
        timer: asyncio.Task[None] | None = None
        try:
            while True:
                if get_task is None:
                    get_task = asyncio.ensure_future(self._inbox.get())
                timeout = self._next_deadline_offset()
                item: Any = None
                if timeout is None:
                    item = await get_task
                    get_task = None
                else:
                    timer = asyncio.ensure_future(asyncio.sleep(max(0.0, timeout)))
                    await asyncio.wait(
                        {get_task, timer}, return_when=asyncio.FIRST_COMPLETED
                    )
                    timer.cancel()
                    timer = None
                    if get_task.done():
                        item = get_task.result()
                        get_task = None
                if item is _CLOSE:
                    break
                now = self._clock()
                drafts = self._fire_due(now)
                if item is not None and item is not _WAKE:
                    drafts.extend(self._router.route(item, now))
                for draft in drafts:
                    yield draft
        finally:
            self._stream_active = False
            if timer is not None:
                timer.cancel()
            if get_task is not None and not get_task.done():
                get_task.cancel()

    # -- timer wheel ---------------------------------------------------------------

    def _next_deadline_offset(self) -> float | None:
        """Seconds until the earliest deadline, or ``None`` when unarmed."""
        best: float | None = None
        for ctx in self._sessions.values():
            if ctx.terminal_emitted:
                continue
            deadline = ctx.next_deadline()
            if deadline is not None and (best is None or deadline < best):
                best = deadline
        return None if best is None else best - self._clock()

    def _fire_due(self, now: float) -> list[VtEvent]:
        """Timer-driven emissions: sync pendings, heartbeats, terminals."""
        drafts: list[VtEvent] = []
        while self._sync_pending:
            drafts.append(self._sync_pending.popleft())
        for ctx in list(self._sessions.values()):
            if ctx.terminal_emitted:
                continue
            drafts.extend(stamped(ctx, ctx.tools.fire_due(now)))
            life = ctx.life
            synthetic = life.synthetic_idle_deadline
            quiescence = life.quiescence_deadline
            if synthetic is not None and now >= synthetic:
                # kimaki #74 guard: session.error without a following idle
                # would wedge the queue forever — inject the synthetic idle.
                logger.debug(
                    "session %s: injecting synthetic idle (session.error "
                    "got no idle within %.1fs)",
                    ctx.session_id,
                    self._quiescence_s,
                )
                drafts.append(self._terminal(ctx, "failed", now))
            elif quiescence is not None and now >= quiescence:
                drafts.append(self._terminal(ctx, "completed", now))
        return drafts

    def _terminal(self, ctx: AttemptContext, kind: str, now: float) -> VtEvent:
        """Build one attempt terminal and freeze the context."""
        ctx.life.clear_deadlines()
        ctx.tools.cancel_all()
        ctx.terminal_emitted = True
        if kind == "completed":
            payload = ctx.completed_payload(now)
        elif kind == "failed":
            payload = ctx.failed_payload()
        else:
            payload = ctx.cancelled_payload()
        return VtEvent(f"attempt.{kind}", payload)

    def _bare(self, name: str) -> str:
        """Indirection so runtime tool_map updates reach live contexts."""
        return self._tool_map.bare(name)
