"""Internal halves of the opencode bridge session service (T5 split).

Pre-approved plan split (``service.py`` + ``service_persistence.py``; the
public class stays in ``service.py``). This module owns the implementation
halves the public class composes:

:class:`BridgePersistence` — write-side parity with the native
``src/session/service.py`` oracle:

* startup recovery of attempts interrupted by a gateway restart (native
  ``_recover_interrupted_attempts`` parity — T6 layers opencode liveness
  reconciliation on top of exactly this baseline),
* the terminal-event -> result-dict mapping,
* terminal attempt persistence: the reply ``Message`` (``metadata["status"]``
  on success AND failure, D6/B-fix — ``scheduled_routes._read_scheduled_briefing``
  reads it), attempt.json update, partial-response cleanup, FTS indexing, and
  the terminal bus event with the native payload enumeration (native
  ``service.py:408-414`` anchor).

:class:`BridgeEventPlumbing` — the driver/translator pump + dispatch tasks:
``driver.events()`` -> ``translator.feed()`` -> vt-event routing (checkpoint,
tool trail, bus publish, terminal-future resolution) with the no-hang
guarantee (a dead pump or translator fails every pending attempt).

User-visible shapes (reply text, tool-trail entries, interrupted-notice text,
metrics loading) are delegated to the native ``SessionService`` statics so the
bridge can never drift from the protected-zone oracle; importing is reuse, the
protected files themselves are never modified.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from src.session.checkpoint import ResponseCheckpoint
from src.session.models import Attempt, AttemptStatus, Message
from src.session.service import SessionService

if TYPE_CHECKING:
    from src.session.events import EventBus
    from src.session.search import SessionSearchIndex
    from src.session.store import SessionStore

    from ._types import EventTranslatorLike, VtEventLike
    from .driver import EngineDriver

logger = logging.getLogger("opencode_bridge")

__all__ = [
    "TERMINAL_EVENT_TO_STATUS",
    "AttemptRun",
    "BridgeEventPlumbing",
    "BridgePersistence",
]

#: Terminal attempt status -> SSE event name (native ``_TERMINAL_EVENTS``
#: vocabulary; cancellation is its own event so the UI can distinguish a user
#: stop from a failure, live and on reload).
_TERMINAL_EVENTS = {
    "completed": "attempt.completed",
    "cancelled": "attempt.cancelled",
    "failed": "attempt.failed",
}

#: Translator terminal event name -> attempt status key.
TERMINAL_EVENT_TO_STATUS = {
    "attempt.completed": "completed",
    "attempt.cancelled": "cancelled",
    "attempt.failed": "failed",
}

#: Reply-metadata runtime keys, copied verbatim from the native service so the
#: frontend/IM see the identical enumeration.
_RUNTIME_METADATA_KEYS = (
    "provider",
    "configured_model",
    "model",
    "model_source",
    "reasoning_effort",
)


@dataclass
class AttemptRun:
    """Per-attempt bridge state shared by the attempt task and the dispatch loop.

    Attributes:
        attempt: The vt attempt record (service-owned identity).
        session_id: Owning vt session id.
        started_at: ``time.perf_counter()`` when the attempt task began.
        checkpoint: Durable partial-response checkpoint (native parity).
        done: Resolved by the dispatch loop with ``(status_key, event_data)``
            when the translator emits the attempt's terminal event.
        engine_session_id: opencode-side session id once attached.
        tool_trail: Compact tool-call history accumulated from translator
            ``tool_call``/``tool_result`` events (native trail shape).
    """

    attempt: Attempt
    session_id: str
    started_at: float
    checkpoint: ResponseCheckpoint
    done: asyncio.Future[tuple[str, Dict[str, Any]]]
    engine_session_id: Optional[str] = None
    tool_trail: List[Dict[str, Any]] = field(default_factory=list)


class BridgePersistence:
    """Write-side contract parity mixin for OpencodeSessionService.

    The host class supplies ``store``, ``event_bus`` and ``_search_index``.
    This is an implementation detail of the bridge package — consumers import
    :class:`~src.opencode_bridge.service.OpencodeSessionService` only.
    """

    store: SessionStore
    event_bus: EventBus
    _search_index: SessionSearchIndex

    def _recover_interrupted_attempts(self) -> None:
        """Finalize attempts that could not outlive the previous process.

        Mirrors the native constructor recovery (native ``service.py:101-154``
        anchor): a freshly constructed service has no live engine attachment,
        so any attempt still pending/running belongs to the previous process
        and becomes an explicit interrupted terminal, with the durable partial
        response surfaced in the transcript. T6 extends this with opencode
        liveness reconciliation (reattach / complete / interrupted).
        """
        recoverable = {AttemptStatus.PENDING, AttemptStatus.RUNNING}
        for attempt in self.store.list_attempts():
            if attempt.status not in recoverable:
                continue
            partial = self.store.get_partial_response(
                attempt.session_id, attempt.attempt_id
            )
            has_partial = bool(partial)
            existing_reply = self.store.get_message_for_attempt(
                attempt.session_id, attempt.attempt_id
            )
            if existing_reply is None:
                reply = Message(
                    session_id=attempt.session_id,
                    role="assistant",
                    content=SessionService._format_interrupted_message(partial),
                    linked_attempt_id=attempt.attempt_id,
                    metadata={
                        "status": AttemptStatus.INTERRUPTED.value,
                        "partial": has_partial,
                        "recovery_reason": "service_restart",
                    },
                )
                self.store.append_message(reply)
                self._search_index.index_message(
                    attempt.session_id, "assistant", reply.content
                )
                attempt.mark_interrupted("service restarted before attempt completed")
            else:
                # The append-only reply is committed before attempt.json. If a
                # process exited between those writes, finish the second half
                # from the reply's terminal metadata instead of mislabelling a
                # complete response as interrupted.
                reply_status = existing_reply.metadata.get("status")
                if reply_status == AttemptStatus.COMPLETED.value:
                    attempt.mark_completed(summary=existing_reply.content)
                elif reply_status == AttemptStatus.CANCELLED.value:
                    attempt.mark_cancelled(reason="cancelled by user")
                elif reply_status == AttemptStatus.FAILED.value:
                    attempt.mark_failed(error="execution failed")
                else:
                    attempt.mark_interrupted(
                        "service restarted before attempt completed"
                    )
            self.store.update_attempt(attempt)
            self.store.delete_partial_response(attempt.session_id, attempt.attempt_id)

    @staticmethod
    def _flush_checkpoint(run: AttemptRun) -> None:
        """Flush the partial-response checkpoint, never masking the run outcome."""
        try:
            run.checkpoint.flush()
        except OSError as exc:
            logger.warning(
                "Could not flush response checkpoint for attempt %s: %s",
                run.attempt.attempt_id,
                exc,
            )

    @staticmethod
    def _result_from_terminal(
        status: str, data: Dict[str, Any], started_at: float
    ) -> Dict[str, Any]:
        """Map a translator terminal event onto the native result-dict shape.

        Args:
            status: Terminal status key (``completed``/``cancelled``/``failed``).
            data: The terminal event payload (D5 field enumeration).
            started_at: Local attempt start (``time.perf_counter()``) used when
                the engine did not report ``elapsed_ms``.

        Returns:
            The result dict the native persistence flow consumes.
        """
        result: Dict[str, Any] = {}
        if status == "completed":
            result["status"] = "success"
            result["content"] = data.get("summary") or ""
            result["run_dir"] = data.get("run_dir")
        elif status == "cancelled":
            result["status"] = "cancelled"
            result["reason"] = (
                data.get("error") or data.get("reason") or "cancelled by user"
            )
        else:
            result["status"] = "failed"
            result["reason"] = data.get("error") or "unknown"

        elapsed = data.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            result["elapsed_ms"] = max(0, int(elapsed))
        else:
            result["elapsed_ms"] = max(
                0, round((time.perf_counter() - started_at) * 1000)
            )
        for key in _RUNTIME_METADATA_KEYS:
            if data.get(key) is not None:
                result[key] = data[key]
        if result.get("run_dir"):
            metrics = SessionService._load_metrics(Path(result["run_dir"]))
            if metrics:
                result["metrics"] = metrics
        return result

    def _persist_terminal_attempt(
        self, run: AttemptRun, result: Dict[str, Any]
    ) -> None:
        """Persist one terminal attempt exactly like the native service does.

        Order is contract: append the reply to the transcript BEFORE
        attempt.json (startup recovery finishes a process interrupted between
        the two writes), then delete the partial checkpoint, index FTS, and
        emit the terminal bus event with the native payload enumeration.

        Args:
            run: The attempt's bridge state.
            result: Native-shaped result dict (from :meth:`_result_from_terminal`
                or a local failure/cancel path).
        """
        attempt = run.attempt
        session_id = run.session_id
        status = result.get("status")
        if status == "success":
            attempt.mark_completed(summary=result.get("content", ""))
        elif status == "cancelled":
            attempt.mark_cancelled(reason=result.get("reason", "cancelled by user"))
        else:
            attempt.mark_failed(error=result.get("reason", "unknown"))
        attempt.run_dir = result.get("run_dir")
        if result.get("metrics"):
            attempt.metrics = result["metrics"]

        reply_metadata: Dict[str, Any] = {}
        if attempt.run_dir:
            reply_metadata["run_id"] = Path(attempt.run_dir).name
        reply_metadata["status"] = attempt.status.value
        if attempt.metrics:
            reply_metadata["metrics"] = attempt.metrics
        reply_metadata["elapsed_ms"] = result.get(
            "elapsed_ms",
            max(0, round((time.perf_counter() - run.started_at) * 1000)),
        )
        for key in _RUNTIME_METADATA_KEYS:
            value = result.get(key)
            if value is not None:
                reply_metadata[key] = value

        reply = Message(
            session_id=session_id,
            role="assistant",
            content=SessionService._format_result_message(attempt),
            linked_attempt_id=attempt.attempt_id,
            metadata=reply_metadata,
            tool_trail=(
                run.tool_trail if attempt.status == AttemptStatus.COMPLETED else []
            ),
        )
        self.store.append_message(reply)
        # The append-only transcript is the user-visible source of truth:
        # commit it before attempt.json (native ordering, recovery contract).
        self.store.update_attempt(attempt)
        self.store.delete_partial_response(session_id, attempt.attempt_id)
        self._search_index.index_message(session_id, "assistant", reply.content)
        self.event_bus.emit(
            session_id,
            _TERMINAL_EVENTS.get(attempt.status.value, "attempt.failed"),
            {
                "attempt_id": attempt.attempt_id,
                "status": attempt.status.value,
                "summary": attempt.summary,
                "error": attempt.error,
                "run_dir": attempt.run_dir,
                **{
                    key: reply_metadata[key]
                    for key in ("elapsed_ms", *_RUNTIME_METADATA_KEYS)
                    if key in reply_metadata
                },
            },
        )


_TOOL_TRAIL_EVENTS = frozenset({"tool_call", "tool_result"})


class BridgeEventPlumbing:
    """Driver/translator pump + dispatch mixin for OpencodeSessionService.

    The host class supplies ``_driver``, ``_translator``, ``event_bus``,
    ``_runs``, ``_active_by_session``, ``_vt_by_engine`` and ``_loop``.
    """

    _driver: EngineDriver
    _translator: EventTranslatorLike
    event_bus: EventBus
    _runs: Dict[str, AttemptRun]
    _active_by_session: Dict[str, str]
    _vt_by_engine: Dict[str, str]
    _loop: Optional[asyncio.AbstractEventLoop]
    _pump_task: Optional[asyncio.Task]
    _dispatch_task: Optional[asyncio.Task]

    #: Optional async tap on every ROUTED vt event (T9 IM streaming producer,
    #: attached by ``wiring.build_session_service``). Receives ``(event,
    #: vt_session_id)`` after the ``attempt_id`` stamp and BEFORE terminal
    #: resolution, so stream publications (incl. ``_stream_end``) reach the
    #: channel bus before the runtime's polled final message can be published.
    #: Exceptions are contained here: an IM-side failure must never kill the
    #: web dispatch loop.
    vt_event_observer: Optional[Callable[["VtEventLike", str], Awaitable[None]]] = None

    async def start(self) -> None:
        """Eagerly start the pump + dispatch tasks (idempotent).

        Optional — ``send_message`` starts them lazily. T7's factory wiring
        can call this at gateway startup so the driver's subscribe-first
        contract holds before the first prompt.
        """
        self._ensure_pumps()

    def _ensure_pumps(self) -> None:
        """(Re)start the two plumbing tasks on the running loop."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = loop.create_task(
                self._pump_driver_events(), name="opencode-bridge-pump"
            )
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = loop.create_task(
                self._dispatch_translator_events(),
                name="opencode-bridge-dispatch",
            )

    async def _pump_driver_events(self) -> None:
        """``driver.events()`` -> ``translator.feed()``: one persistent stream.

        A pump that dies (auth failure, double consumer) fails every pending
        attempt so no transcript hangs; cancellation (shutdown) leaves
        attempts recoverable on disk (native shutdown parity).
        """
        try:
            async for event in self._driver.events():
                await self._translator.feed(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("opencode event pump failed: %s", exc)
            self._fail_all_pending(f"engine event stream lost: {exc}")

    async def _dispatch_translator_events(self) -> None:
        """``translator.events()`` -> bus publish + persistence side effects."""
        try:
            async for event in self._translator.events():
                await self._handle_vt_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("opencode bridge event dispatch failed: %s", exc)
            self._fail_all_pending(f"event translation failed: {exc}")

    def _fail_all_pending(self, error: str) -> None:
        """Resolve every in-flight attempt as failed (no-hang guarantee)."""
        for run in list(self._runs.values()):
            if not run.done.done():
                run.done.set_result(("failed", {"error": error}))

    async def _await_terminal(self, run: AttemptRun) -> tuple[str, Dict[str, Any]]:
        """Await the attempt's terminal, racing the plumbing tasks.

        A pump/dispatch task that has already exited can never deliver the
        terminal event, so waiting on the future alone would hang an attempt
        registered after the failure sweep (the sweep only resolves runs that
        existed at failure time). A cancelled guard is shutdown, not failure:
        keep waiting so the attempt task's own cancellation leaves it
        recoverable on disk (native shutdown parity).

        Args:
            run: The attempt's bridge state.

        Returns:
            ``(status_key, terminal_event_data)``.
        """
        guards = {
            task for task in (self._pump_task, self._dispatch_task) if task is not None
        }
        while not run.done.done():
            if not guards:
                return await run.done
            finished, _ = await asyncio.wait(
                {run.done, *guards}, return_when=asyncio.FIRST_COMPLETED
            )
            if run.done.done():
                break
            alive = set()
            for task in guards:
                if task in finished and not task.cancelled():
                    return ("failed", {"error": "engine event stream lost"})
                if not task.done():
                    alive.add(task)
            guards = alive
        return run.done.result()

    async def _handle_vt_event(self, event: VtEventLike) -> None:
        """Route one translated event: observer tap, checkpoint, trail, bus, terminal."""
        event_type = event.type
        data = event.data
        run = self._route_run(data)
        if run is None:
            # D4: events matching no active attempt (post-terminal stragglers,
            # restart leftovers, version drift) are dropped with a warning.
            logger.warning(
                "dropping bridge event %r: no active attempt matches", event_type
            )
            return
        data["attempt_id"] = run.attempt.attempt_id

        observer = self.vt_event_observer
        if observer is not None:
            try:
                await observer(event, run.session_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "vt_event_observer failed for %r", event_type, exc_info=True
                )

        terminal_status = TERMINAL_EVENT_TO_STATUS.get(event_type)
        if terminal_status is not None:
            if run.done.done():
                logger.warning(
                    "dropping duplicate terminal event %r for attempt %s",
                    event_type,
                    run.attempt.attempt_id,
                )
                return
            run.done.set_result((terminal_status, data))
            return

        try:
            run.checkpoint.handle_event(event_type, data)
        except OSError as exc:
            # A checkpoint failure must not hide the live response (native).
            logger.warning(
                "Could not checkpoint response for attempt %s: %s",
                run.attempt.attempt_id,
                exc,
            )
        if event_type in _TOOL_TRAIL_EVENTS:
            SessionService._record_tool_trail_event(run.tool_trail, event_type, data)
        self.event_bus.emit(run.session_id, event_type, data)

    def _route_run(self, data: Dict[str, Any]) -> Optional[AttemptRun]:
        """Resolve an event payload to its active run (routing precedence:
        ``attempt_id`` -> vt ``session_id`` -> engine ``sessionID``)."""
        attempt_id = data.get("attempt_id")
        if isinstance(attempt_id, str):
            run = self._runs.get(attempt_id)
            if run is not None:
                return run
        session_id = data.get("session_id")
        if isinstance(session_id, str):
            active = self._active_by_session.get(session_id)
            if active is not None:
                return self._runs.get(active)
        engine_id = data.get("sessionID")
        if isinstance(engine_id, str):
            vt_id = self._vt_by_engine.get(engine_id)
            if vt_id is not None:
                active = self._active_by_session.get(vt_id)
                if active is not None:
                    return self._runs.get(active)
        return None

    async def _abort_engine(self, run: AttemptRun, engine_session_id: str) -> None:
        """Deliver the engine abort; force the terminal on transport failure.

        The user intent is unambiguous: if the abort request itself fails
        (serve unreachable), resolve the attempt as cancelled locally instead
        of leaving the transcript waiting on an engine that may be dead.
        """
        try:
            await self._driver.abort(engine_session_id)
        except Exception as exc:
            logger.warning(
                "engine abort failed for attempt %s: %s",
                run.attempt.attempt_id,
                exc,
            )
            if not run.done.done():
                run.done.set_result(("cancelled", {"error": "cancelled by user"}))
