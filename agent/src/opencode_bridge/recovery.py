"""Startup crash recovery + opencode liveness reconciliation (plan T6).

Ownership rules (D3, quoted verbatim from the work plan; wiki:
``mymain-wiki/features/f8-engine-bridge.md``):

    双存储所有权规则（成文）：``messages.jsonl`` = 用户可见转录（对引擎只写）；
    opencode store = 引擎上下文真相。会话删除/IM ``/new`` 必须级联到 opencode
    session；opencode compaction 对 vt 转录不可见（接受）。

    Dual-store ownership (codified): ``messages.jsonl`` is the user-visible
    transcript (write-only toward the engine); the opencode store is the
    engine-context truth. Session deletion and the IM ``/new`` command must
    cascade to the opencode session; opencode compaction stays invisible to
    the vt transcript (accepted).

Crash corollary: after a gateway crash the ENGINE state wins for LIVENESS
(opencode is an independent process that survives gateway death — T2 memo
F2 — so re-attach is the common production case); the STORE wins for
HISTORY (the transcript is never rewritten, only appended).

Three-branch reconciliation: for every pending/running attempt in the store,
query the engine session (``GET /session/:id/message`` via the T3 driver's
``messages()``) and take exactly one branch (:mod:`.engine_state`
classifies, :mod:`.recovery_branches` lands):

1. **Still running -> RE-ATTACH.** The driver's subscribe-first persistent
   SSE connection makes re-subscription natural: the pump reconnects, the
   attempt is re-announced via ``note_attempt(engine_session_id,
   attempt_id)`` so in-flight events stamp the right attempt, and the
   attempt stays RUNNING. A watch timer re-probes the engine so a turn that
   finished unseen backfills instead of hanging (no-hang guarantee).
2. **Finished while the gateway was down -> BACKFILL** the terminal reply +
   bus event from the engine's message list: finalized text per D4,
   metadata per the D6 enumeration, FTS indexed at the recovery anchor —
   written through T5's ``_result_from_terminal`` +
   ``_persist_terminal_attempt`` seams (identical to a live terminal).
3. **No such session (404) / engine unreachable / prompt never arrived ->
   INTERRUPTED** with the EXISTING interrupted semantics, aligned with the
   native restart-recovery oracle (``agent/src/session/service.py:101-154``,
   ``recovery_reason: "service_restart"`` and reply-before-attempt.json
   ordering included).

**NEVER re-sends a prompt** (plan T6 Must-NOT: 永不重复执行) — reconciliation
reads engine state only; branch 1 re-attaches, it does not restart.

T7 wiring contract (factory switch at ``api/state.py``)::

    service = RecoverableOpencodeSessionService(
        store, event_bus, runs_dir, driver, translator
    )
    await service.reconcile()  # gateway startup, before serving traffic

Construction alone finalizes nothing: the T5 baseline
``_recover_interrupted_attempts`` is DEFERRED to :meth:`reconcile` (the
baseline would clobber branch-1 attempts that must stay running).
:meth:`reconcile` is idempotent.

Cascade lifecycle (D3): ``delete_session`` also DELETEs the engine session
(spike §7h: 200 + ``session.deleted`` + child cascade; fire-and-forget, 404
= success). IM ``/new``: ``runtime.reset_session`` (protected, zero diff)
drops the channel mapping; the next message creates a fresh vt session and
lazily a FRESH engine session. The old engine session is deliberately kept
(its transcript stays browsable, the engine keeps its context) and the
persisted ``session.config`` mapping restores that context on the next send
even across a restart. Residual edge (T8's live-death path, not T6): if
opencode itself dies mid-turn and restarts, its zombie in-flight message
keeps the re-attached attempt waiting like a long-silent live tool.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from src.session.events import EventBus
from src.session.models import Attempt, AttemptStatus, Session
from src.session.store import SessionStore

from ._types import EventTranslatorLike
from .driver import EngineDriver
from .engine_state import EngineTurnProbe, TurnState, probe_engine
from .errors import OpencodeBridgeError, OpencodeHttpError
from .recovery_branches import BranchLanding
from .service import OpencodeSessionService

logger = logging.getLogger("opencode_bridge")

__all__ = [
    "ENGINE_SESSION_CONFIG_KEY",
    "ReconciliationReport",
    "RecoverableOpencodeSessionService",
    "delete_engine_session",
]

#: ``session.config`` key persisting the vt -> engine session mapping. The
#: T5 service keeps this mapping in memory only; recovery needs it to find
#: the engine session after a restart (branch 1/2), to restore engine-side
#: conversation context on the next send (D3), and to cascade deletes.
ENGINE_SESSION_CONFIG_KEY = "opencode_engine_session_id"

#: A natural completion newer than this is ambiguous: OmO's stop-hook
#: continuation re-prompts 6.4 s after idle (spike §5c, D4 mandatory
#: condition ①). 15 s covers the re-prompt plus scheduling slack.
CONTINUATION_GRACE_S = 15.0

#: Bound for draining in-flight cascade DELETEs during shutdown.
_CASCADE_DRAIN_S = 5.0


async def delete_engine_session(driver: Any, engine_session_id: str) -> None:
    """``DELETE /session/:id`` on the engine (D3 delete cascade, spike §7h).

    Small concrete addition on top of the frozen T3 driver surface: it uses
    the driver's httpx client seam (``_http``) instead of editing
    ``driver.py``. Idempotent — a 404 (session already gone, e.g. cascaded
    by its parent's delete) counts as success.

    Args:
        driver: The bridge driver (``OpencodeDriver`` or a test stub that
            exposes the same ``_http`` client seam).
        engine_session_id: opencode session id to delete.

    Raises:
        TypeError: The driver exposes no httpx client seam.
        OpencodeConnectionError: The serve is unreachable.
        OpencodeHttpError: The serve returned a non-2xx, non-404 status.
    """
    http = getattr(driver, "_http", None)
    if http is None:
        raise TypeError(
            "engine DELETE requires a driver exposing the httpx client "
            f"seam (_http); got {type(driver).__name__}"
        )
    try:
        await http.request_json("DELETE", f"/session/{engine_session_id}")
    except OpencodeHttpError as exc:
        if exc.status_code == 404:
            logger.debug(
                "engine session %s already gone (DELETE -> 404)", engine_session_id
            )
            return
        raise


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Outcome of one :meth:`RecoverableOpencodeSessionService.reconcile`.

    Attributes:
        reattached: Attempt ids re-attached to a still-running engine turn.
        backfilled: Attempt ids whose terminal was backfilled from engine state.
        interrupted: Attempt ids landed with interrupted semantics.
    """

    reattached: tuple[str, ...] = ()
    backfilled: tuple[str, ...] = ()
    interrupted: tuple[str, ...] = ()


class RecoverableOpencodeSessionService(OpencodeSessionService, BranchLanding):
    """The bridge session service with T6 recovery + cascade lifecycle.

    Composition over modification: the T5 service files stay frozen; this
    subclass adds the deferred three-branch startup reconciliation, the
    persisted vt->engine session mapping, and the engine-side delete
    cascade. T7's factory constructs THIS class and awaits :meth:`reconcile`
    at gateway startup. Branch landing lives in
    :class:`~src.opencode_bridge.recovery_branches.BranchLanding`.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
        runs_dir: Path,
        driver: EngineDriver,
        translator: EventTranslatorLike,
    ) -> None:
        """Initialize the service; recovery is deferred to :meth:`reconcile`.

        Args:
            store: Session persistence store.
            event_bus: SSE event bus.
            runs_dir: Root runs directory.
            driver: Engine transport (T3 ``EngineDriver``).
            translator: Event translator satisfying ``EventTranslatorLike``.
        """
        self._cascade_tasks: set[asyncio.Task[None]] = set()
        super().__init__(
            store=store,
            event_bus=event_bus,
            runs_dir=runs_dir,
            driver=driver,
            translator=translator,
        )

    # -- deferred baseline + mapping persistence ------------------------------

    def _recover_interrupted_attempts(self) -> None:
        """Defer the T5 baseline finalization to :meth:`reconcile`.

        The baseline marks every pending/running attempt interrupted at
        construction — right for the native in-process loop, wrong for the
        engine bridge: opencode survives gateway death (T2 memo F2), so
        branch 1 must keep live attempts RUNNING and branch 2 must backfill
        finished ones. Clobbering first would append a spurious interrupted
        reply to a transcript whose turn is still streaming.
        """
        logger.debug(
            "opencode bridge: interrupted-attempt recovery deferred to "
            "reconcile() (engine liveness reconciliation owns it)"
        )

    async def _engine_session_for(self, session: Session) -> str:
        """Map a vt session to its engine session, durably (D3 context truth).

        Restores the persisted mapping after a restart (a fresh engine
        session would silently drop the engine-side conversation context)
        and persists newly created mappings into ``session.config`` so the
        next startup's reconciliation and delete cascade can find them.

        Args:
            session: The vt session being sent to.

        Returns:
            The engine (opencode) session id.
        """
        if self._engine_sessions.get(session.session_id) is None:
            persisted = session.config.get(ENGINE_SESSION_CONFIG_KEY)
            if isinstance(persisted, str) and persisted:
                self._engine_sessions[session.session_id] = persisted
                self._vt_by_engine[persisted] = session.session_id
                return persisted
        engine_session_id = await super()._engine_session_for(session)
        if session.config.get(ENGINE_SESSION_CONFIG_KEY) != engine_session_id:
            session.config[ENGINE_SESSION_CONFIG_KEY] = engine_session_id
            self.store.update_session(session)
        return engine_session_id

    def _engine_session_id_for(self, session_id: str) -> Optional[str]:
        """The engine session id for a vt session (memory, then store)."""
        engine_session_id = self._engine_sessions.get(session_id)
        if engine_session_id is not None:
            return engine_session_id
        stored = self.store.get_session(session_id)
        if stored is not None:
            persisted = stored.config.get(ENGINE_SESSION_CONFIG_KEY)
            if isinstance(persisted, str) and persisted:
                return persisted
        return None

    # -- delete cascade (D3) ---------------------------------------------------

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and cascade ``DELETE /session/:id`` to the engine.

        The vt-side delete (T5: store removal + bus clear + mapping drop)
        never blocks on the engine; the engine DELETE is scheduled
        fire-and-forget and logged when it fails (an unreachable engine
        orphans its session but must not fail the user's delete).

        Args:
            session_id: Session to delete.

        Returns:
            Whether the vt-side delete succeeded.
        """
        engine_session_id = self._engine_session_id_for(session_id)
        deleted = super().delete_session(session_id)
        if deleted and engine_session_id is not None:
            self._schedule_engine_delete(engine_session_id)
        return deleted

    def _schedule_engine_delete(self, engine_session_id: str) -> None:
        """Fire-and-forget the engine DELETE on the best available loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning(
                "no running event loop; engine session %s left orphaned "
                "(delete cascade skipped)",
                engine_session_id,
            )
            return
        task = loop.create_task(
            self._engine_delete(engine_session_id),
            name=f"opencode-bridge-delete-{engine_session_id}",
        )
        self._cascade_tasks.add(task)
        task.add_done_callback(self._cascade_tasks.discard)

    async def _engine_delete(self, engine_session_id: str) -> None:
        """Deliver one cascade DELETE; failures are logged, never raised."""
        try:
            await delete_engine_session(self._driver, engine_session_id)
            logger.info("engine session %s deleted (cascade)", engine_session_id)
        except OpencodeBridgeError as exc:
            logger.warning(
                "engine DELETE failed for session %s (orphaned on the engine): %s",
                engine_session_id,
                exc,
            )

    async def aclose(self) -> None:
        """Drain in-flight cascade deletes, then run the T5 shutdown."""
        cascades = [task for task in self._cascade_tasks if not task.done()]
        if cascades:
            await asyncio.wait(cascades, timeout=_CASCADE_DRAIN_S)
        await super().aclose()

    # -- startup reconciliation (plan T6) --------------------------------------

    async def reconcile(self) -> ReconciliationReport:
        """Reconcile every pending/running attempt against engine liveness.

        Idempotent: attempts already re-attached by an earlier call are
        skipped, and landed attempts are terminal in the store. Reads engine
        state only — never re-sends a prompt. Per-attempt failures land the
        attempt interrupted (fail visible) so one corrupt attempt cannot
        block gateway startup.

        Returns:
            The :class:`ReconciliationReport` (T7 logs it as startup evidence).
        """
        recoverable = {AttemptStatus.PENDING, AttemptStatus.RUNNING}
        attempts = sorted(
            (
                attempt
                for attempt in self.store.list_attempts()
                if attempt.status in recoverable
            ),
            key=lambda attempt: attempt.created_at,
        )
        reattached: List[str] = []
        backfilled: List[str] = []
        interrupted: List[str] = []
        for attempt in attempts:
            if attempt.attempt_id in self._runs:
                continue  # already re-attached by an earlier reconcile
            try:
                engine_session_id = self._engine_session_id_for(attempt.session_id)
                probe = await self._probe_engine(engine_session_id, attempt)
                if probe.state is TurnState.RUNNING:
                    self._ensure_pumps()
                    self._reattach(attempt, engine_session_id, probe)
                    reattached.append(attempt.attempt_id)
                elif probe.state is TurnState.FINISHED:
                    self._backfill(attempt, engine_session_id, probe)
                    backfilled.append(attempt.attempt_id)
                else:
                    logger.warning(
                        "attempt %s: %s -> landing interrupted",
                        attempt.attempt_id,
                        probe.reason,
                    )
                    self._finalize_interrupted(attempt)
                    interrupted.append(attempt.attempt_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "recovery of attempt %s failed (%s); landing interrupted",
                    attempt.attempt_id,
                    exc,
                )
                self._finalize_interrupted(attempt)
                interrupted.append(attempt.attempt_id)
        report = ReconciliationReport(
            reattached=tuple(reattached),
            backfilled=tuple(backfilled),
            interrupted=tuple(interrupted),
        )
        logger.info(
            "opencode bridge recovery: %d reattached, %d backfilled, %d interrupted",
            len(report.reattached),
            len(report.backfilled),
            len(report.interrupted),
        )
        return report

    async def _probe_engine(
        self, engine_session_id: Optional[str], attempt: Attempt
    ) -> EngineTurnProbe:
        """Classify one attempt's turn via :func:`.engine_state.probe_engine`.

        Any inability to confirm a live run (no mapping, 404, unreachable
        serve, transport error) is a branch-3 probe: the attempt lands
        interrupted rather than hanging a polling consumer.
        """
        return await probe_engine(
            self._driver,
            engine_session_id,
            attempt,
            continuation_grace_s=CONTINUATION_GRACE_S,
        )
