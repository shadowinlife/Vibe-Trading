"""Branch-landing machinery for startup recovery (plan T6, helper split).

Implementation half of :mod:`src.opencode_bridge.recovery` — the mixin that
lands one reconciled attempt in its branch, mirroring the T5
``service.py``/``service_persistence.py`` split:

* **branch 1 (re-attach)**: :meth:`BranchLanding._reattach` registers the
  bridge run state, restores the session mappings, keeps the attempt
  RUNNING and re-announces it to the translator; the spawned
  :meth:`BranchLanding._await_reattached` awaiter persists the terminal
  through T5's seams and re-probes the engine on a watch timer so a turn
  that finished unseen backfills instead of hanging (no-hang guarantee).
* **branch 2 (backfill)**: :meth:`BranchLanding._backfill` builds the
  D5-shaped terminal payload from the engine probe and persists via T5's
  ``_result_from_terminal`` + ``_persist_terminal_attempt`` — identical
  reply metadata enumeration, write ordering, FTS indexing and bus event as
  a live terminal (D6).
* **branch 3 (interrupted)**: :meth:`BranchLanding._finalize_interrupted`
  is the per-attempt mirror of the native restart-recovery oracle
  (``agent/src/session/service.py:101-154``).

None of these paths ever calls ``prompt_async`` (plan T6 Must-NOT:
永不重复执行) and none fabricates content beyond engine/store state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.session.checkpoint import ResponseCheckpoint
from src.session.models import Attempt, AttemptStatus, Message
from src.session.service import SessionService

from .engine_state import EngineTurnProbe, TurnState
from .service_persistence import AttemptRun

if TYPE_CHECKING:
    from src.session.search import SessionSearchIndex
    from src.session.store import SessionStore

logger = logging.getLogger("opencode_bridge")

__all__ = ["REATTACH_WATCH_S", "BranchLanding"]

#: Re-attach watch period: re-probe the engine this often while a re-attached
#: attempt has not reached its terminal. Sized to clear the OmO continuation
#: window (15 s grace, ``recovery.CONTINUATION_GRACE_S``) plus the quiescence
#: timer (8 s) with margin, so a turn that finished unseen backfills promptly
#: instead of hanging; long-silent live tools simply keep re-classifying as
#: RUNNING (cheap local REST poll).
REATTACH_WATCH_S = 25.0


class BranchLanding:
    """Per-branch finalization mixin for RecoverableOpencodeSessionService.

    The host class supplies the store/search index, the run registries and
    busy gate, the driver/translator, the T5 persistence seams and
    :meth:`_probe_engine`. This is an implementation detail of the bridge
    package — consumers import
    :class:`~src.opencode_bridge.recovery.RecoverableOpencodeSessionService`.
    """

    store: SessionStore
    _search_index: SessionSearchIndex
    _driver: Any
    _translator: Any
    _runs: Dict[str, AttemptRun]
    _active_by_session: Dict[str, str]
    _active_tasks: Dict[str, asyncio.Task]
    _engine_sessions: Dict[str, str]
    _vt_by_engine: Dict[str, str]
    _user_cancel_requests: set

    async def _probe_engine(
        self, engine_session_id: Optional[str], attempt: Attempt
    ) -> EngineTurnProbe:
        """Host-provided engine query + classification (recovery.py)."""
        raise NotImplementedError

    def _reserve_session(self, session_id: str) -> None:
        """Host-provided busy-gate claim (T5)."""
        raise NotImplementedError

    def _release_session(self, session_id: str) -> None:
        """Host-provided busy-gate release (T5)."""
        raise NotImplementedError

    # -- branch 1: re-attach ---------------------------------------------------

    def _reattach(
        self,
        attempt: Attempt,
        engine_session_id: Optional[str],
        probe: EngineTurnProbe,
    ) -> None:
        """Re-attach one still-running attempt to the live engine turn.

        Registers the bridge run state (so the dispatch loop routes the
        turn's events), restores the session mappings, keeps the attempt
        RUNNING (D3: engine wins liveness), re-announces the attempt to the
        translator so in-flight events stamp the right attempt id, and
        spawns the terminal awaiter. Claims the busy gate first: a
        concurrent send during a re-attached turn is a 409, exactly like a
        live turn.

        Args:
            attempt: The pending/running attempt record.
            engine_session_id: The engine session running the turn.
            probe: The classification that selected this branch (its rebuilt
                trail seeds the run's tool history).
        """
        session_id = attempt.session_id
        self._reserve_session(session_id)
        run = AttemptRun(
            attempt=attempt,
            session_id=session_id,
            started_at=time.perf_counter(),
            checkpoint=ResponseCheckpoint(self.store, attempt),
            done=asyncio.get_running_loop().create_future(),
            engine_session_id=engine_session_id,
            tool_trail=list(probe.trail),
        )
        self._runs[attempt.attempt_id] = run
        self._active_by_session[session_id] = attempt.attempt_id
        if engine_session_id is not None:
            self._engine_sessions.setdefault(session_id, engine_session_id)
            self._vt_by_engine.setdefault(engine_session_id, session_id)
            self._translator.note_attempt(engine_session_id, attempt.attempt_id)
        if attempt.status is not AttemptStatus.RUNNING:
            attempt.mark_running()
            self.store.update_attempt(attempt)
        task = asyncio.get_running_loop().create_task(
            self._await_reattached(run),
            name=f"opencode-bridge-reattach-{attempt.attempt_id}",
        )
        self._active_tasks[session_id] = task
        logger.info(
            "reattached attempt %s to engine session %s (%s)",
            attempt.attempt_id,
            engine_session_id,
            probe.reason,
        )

    async def _await_reattached(self, run: AttemptRun) -> None:
        """Await a re-attached turn's terminal, watching for a silent finish.

        Mirrors ``_run_attempt``'s persistence contract (terminal via T5's
        ``_result_from_terminal`` + ``_persist_terminal_attempt``; user
        cancel vs shutdown distinction; failure path) and adds the watch
        loop: every :data:`REATTACH_WATCH_S` without a terminal, re-probe
        the engine — a turn that finished unseen (TOCTOU, continuation
        window) backfills, a vanished session lands interrupted, and a
        genuinely running turn keeps waiting (long-silent tools are normal,
        B3's >90 s gaps).

        Args:
            run: The re-attached attempt's bridge state.
        """
        attempt = run.attempt
        session_id = run.session_id
        terminal_task: Optional[asyncio.Task[tuple[str, Dict[str, Any]]]] = None
        try:
            terminal_task = asyncio.get_running_loop().create_task(
                self._await_terminal(run)
            )
            while True:
                done, _ = await asyncio.wait({terminal_task}, timeout=REATTACH_WATCH_S)
                if done:
                    status, data = terminal_task.result()
                    result = self._result_from_terminal(status, data, run.started_at)
                    self._flush_checkpoint(run)
                    self._persist_terminal_attempt(run, result)
                    return
                probe = await self._probe_engine(run.engine_session_id, attempt)
                if probe.state is TurnState.RUNNING:
                    continue
                terminal_task.cancel()
                if probe.state is TurnState.FINISHED:
                    logger.info(
                        "reattached attempt %s finished unseen (%s); "
                        "backfilling terminal",
                        attempt.attempt_id,
                        probe.reason,
                    )
                    self._backfill(
                        attempt,
                        run.engine_session_id,
                        probe,
                        pre_trail=run.tool_trail,
                    )
                else:
                    logger.warning(
                        "reattached attempt %s: %s -> landing interrupted",
                        attempt.attempt_id,
                        probe.reason,
                    )
                    self._finalize_interrupted(attempt)
                return
        except asyncio.CancelledError:
            self._flush_checkpoint(run)
            if session_id in self._user_cancel_requests:
                self._persist_terminal_attempt(
                    run, {"status": "cancelled", "reason": "cancelled by user"}
                )
            else:
                logger.info(
                    "Leaving reattached attempt %s recoverable after cancellation",
                    attempt.attempt_id,
                )
            raise
        except Exception as exc:
            self._flush_checkpoint(run)
            logger.warning("reattached attempt %s failed: %s", attempt.attempt_id, exc)
            self._persist_terminal_attempt(
                run,
                {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            if terminal_task is not None and not terminal_task.done():
                terminal_task.cancel()
            self._deregister_run(run)

    def _deregister_run(self, run: AttemptRun) -> None:
        """Release one run's registrations and busy claim (idempotent)."""
        self._runs.pop(run.attempt.attempt_id, None)
        if self._active_by_session.get(run.session_id) == run.attempt.attempt_id:
            self._active_by_session.pop(run.session_id, None)
        self._active_tasks.pop(run.session_id, None)
        self._user_cancel_requests.discard(run.session_id)
        self._release_session(run.session_id)

    # -- branch 2: backfill ----------------------------------------------------

    def _backfill(
        self,
        attempt: Attempt,
        engine_session_id: Optional[str],
        probe: EngineTurnProbe,
        pre_trail: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Land a finished-while-down turn from engine state via T5's seams.

        Builds the D5-shaped terminal payload from the probe (finalized text
        per D4, engine timestamps for ``elapsed_ms``, provider/model from the
        final assistant message, run_dir regex-harvested from backtest tool
        output) and persists through ``_result_from_terminal`` +
        ``_persist_terminal_attempt`` — identical reply metadata
        enumeration, write ordering (transcript before attempt.json), FTS
        indexing at the recovery anchor and terminal bus event as a live
        turn (D6).

        Args:
            attempt: The attempt whose turn finished while the gateway was down.
            engine_session_id: The engine session that ran it.
            probe: The FINISHED classification carrying the backfill fields.
            pre_trail: Trail to persist instead of the probe's (a re-attached
                run's accumulated trail, which already includes pre-crash
                entries plus any post-re-attach tool events).
        """
        data: Dict[str, Any] = {
            "attempt_id": attempt.attempt_id,
            "status": "completed",
            "summary": probe.finalized_text,
            "run_dir": probe.run_dir,
            "elapsed_ms": probe.elapsed_ms,
            "provider": probe.provider,
            "model": probe.model,
        }
        result = self._result_from_terminal("completed", data, time.perf_counter())
        loop = asyncio.get_running_loop()
        done: asyncio.Future[tuple[str, Dict[str, Any]]] = loop.create_future()
        done.set_result(("completed", data))
        run = AttemptRun(
            attempt=attempt,
            session_id=attempt.session_id,
            started_at=time.perf_counter(),
            checkpoint=ResponseCheckpoint(self.store, attempt),
            done=done,
            engine_session_id=engine_session_id,
            tool_trail=list(probe.trail if pre_trail is None else pre_trail),
        )
        self._persist_terminal_attempt(run, result)
        logger.info(
            "backfilled terminal for attempt %s from engine state (%s)",
            attempt.attempt_id,
            probe.reason,
        )

    # -- branch 3: interrupted (native oracle alignment) ------------------------

    def _finalize_interrupted(self, attempt: Attempt) -> None:
        """Land one attempt with the EXISTING interrupted semantics.

        Per-attempt mirror of the native restart-recovery oracle
        (``agent/src/session/service.py:101-154``): the interrupted reply
        (durable partial surfaced, ``recovery_reason: "service_restart"``
        metadata) is appended BEFORE attempt.json, FTS-indexed at the
        recovery anchor, and a reply already committed by a process that
        died mid-terminal finishes from its own metadata instead of being
        mislabelled interrupted.

        Args:
            attempt: The attempt to land.
        """
        session_id = attempt.session_id
        attempt_id = attempt.attempt_id
        partial = self.store.get_partial_response(session_id, attempt_id)
        has_partial = bool(partial)
        existing_reply = self.store.get_message_for_attempt(session_id, attempt_id)
        if existing_reply is None:
            reply = Message(
                session_id=session_id,
                role="assistant",
                content=SessionService._format_interrupted_message(partial),
                linked_attempt_id=attempt_id,
                metadata={
                    "status": AttemptStatus.INTERRUPTED.value,
                    "partial": has_partial,
                    "recovery_reason": "service_restart",
                },
            )
            self.store.append_message(reply)
            self._search_index.index_message(session_id, "assistant", reply.content)
            attempt.mark_interrupted("service restarted before attempt completed")
        else:
            # The append-only reply is committed before attempt.json; finish
            # the second half from the reply's terminal metadata (native).
            reply_status = existing_reply.metadata.get("status")
            if reply_status == AttemptStatus.COMPLETED.value:
                attempt.mark_completed(summary=existing_reply.content)
            elif reply_status == AttemptStatus.CANCELLED.value:
                attempt.mark_cancelled(reason="cancelled by user")
            elif reply_status == AttemptStatus.FAILED.value:
                attempt.mark_failed(error="execution failed")
            else:
                attempt.mark_interrupted("service restarted before attempt completed")
        self.store.update_attempt(attempt)
        self.store.delete_partial_response(session_id, attempt_id)
