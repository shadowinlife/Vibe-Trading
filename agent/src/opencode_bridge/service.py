"""OpencodeSessionService — the SessionService seam over an opencode driver.

Work-plan T5 (D6 full contract). The native ``src/session/service.py`` is the
behavioral oracle: this class re-implements its seven seam methods
(``create_session`` / ``get_session`` / ``list_sessions`` / ``delete_session``
/ ``send_message`` / ``get_messages`` / ``cancel_current``) plus the
``.store`` and ``.event_bus`` attributes, so every consumer — frontend SSE
routes, the 16 IM adapters via ChannelRuntime, the scheduler, the OpenBB
adapter — keeps working unchanged when ``VIBE_TRADING_ENGINE=opencode`` (the
``api/state.py`` factory switch is T7).

Architecture (D3/D4/D12)::

    send_message ─> _run_attempt task ─> driver.prompt_async(injected prompt)
                          │ awaits the terminal future
    driver.events() ─pump─> translator.feed()
    translator.events() ─dispatch─> EventBus emit + checkpoint + tool_trail
                          └─> terminal event resolves the attempt future

The service owns the driver+translator lifecycle (pump/dispatch tasks in
:class:`~src.opencode_bridge.service_persistence.BridgeEventPlumbing`, lazy
on first send or eager via :meth:`start`, torn down by :meth:`aclose`).
Attempt identity (``attempt_id``) is assigned here, never by the engine.
``messages.jsonl`` stays the user-visible transcript — raw user content; the
D8 injection block goes ONLY to the engine — while the opencode store is the
engine-context truth (D3 dual-ownership rule).

Documented T5 adjudications:

* ``include_shell_tools`` (D6): recorded in ``session.config`` for parity but
  IGNORED at the engine level — opencode tool governance lives in the
  server-generated config (D9 render_config, research-only D7), never in
  per-attempt scope.
* ``translator.note_attempt`` / ``note_abort`` receive the ENGINE (opencode)
  session id: the translator correlates raw ``/event`` stream events by
  ``properties.sessionID`` and cannot route without it. Translated events are
  routed back by ``data["attempt_id"]`` (preferred — D5 payloads carry it),
  falling back to ``data["session_id"]`` (vt) or ``data["sessionID"]``
  (engine passthrough); the service stamps ``attempt_id`` onto every event it
  publishes (native ``event_callback`` parity).
* Reply metadata mirrors the native enumeration exactly (``status`` /
  ``elapsed_ms`` / ``run_id`` / ``metrics`` / ``provider`` /
  ``configured_model`` / ``model`` / ``model_source`` / ``reasoning_effort``)
  — no invented keys; failed and cancelled attempts ALSO carry
  ``metadata["status"]`` (D6/B-fix).
* Startup recovery marks pending/running attempts interrupted exactly like
  the native constructor; T6 adds engine-liveness reconciliation on top.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.paths import get_uploads_dir
from src.session.checkpoint import ResponseCheckpoint
from src.session.events import EventBus
from src.session.models import Attempt, Message, Principal, Session
from src.session.search import get_shared_index
from src.session.service import SessionBusyError
from src.session.store import SessionStore

from ._types import EventTranslatorLike, VtEventLike
from .driver import EngineDriver
from .service_persistence import AttemptRun, BridgeEventPlumbing, BridgePersistence

logger = logging.getLogger("opencode_bridge")

__all__ = ["EventTranslatorLike", "OpencodeSessionService", "VtEventLike"]


class OpencodeSessionService(BridgePersistence, BridgeEventPlumbing):
    """SessionService seam-contract implementation on the opencode driver.

    Attributes:
        store: Session persistence store (reused unmodified, D6).
        event_bus: SSE event bus (reused unmodified, D6).
        runs_dir: Root runs directory — construction-shape parity with the
            native service (``api/state.py:64``); run artifacts themselves are
            written by the engine side.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
        runs_dir: Path,
        driver: EngineDriver,
        translator: EventTranslatorLike,
    ) -> None:
        """Initialize the bridge service.

        Args:
            store: Session persistence store.
            event_bus: SSE event bus.
            runs_dir: Root runs directory.
            driver: Engine transport (T3 ``EngineDriver`` four primitives).
            translator: Event translator satisfying ``EventTranslatorLike``.
        """
        self.store = store
        self.event_bus = event_bus
        self.runs_dir = runs_dir
        self._driver = driver
        self._translator = translator
        # vt session id <-> engine session id. Engine sessions are created
        # lazily on the first attempt so session-only consumers (OpenBB
        # ephemeral sessions that never send) do not leak engine sessions.
        self._engine_sessions: Dict[str, str] = {}
        self._vt_by_engine: Dict[str, str] = {}
        self._runs: Dict[str, AttemptRun] = {}
        self._active_by_session: Dict[str, str] = {}
        self._active_tasks: Dict[str, asyncio.Task] = {}
        # Only cancels requested through cancel_current are user cancels;
        # event-loop shutdown must leave attempts recoverable (native parity).
        self._user_cancel_requests: set[str] = set()
        self._inflight: set[str] = set()
        self._inflight_lock = threading.Lock()
        self._pump_task: Optional[asyncio.Task] = None
        self._dispatch_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._search_index = get_shared_index()
        self._recover_interrupted_attempts()

    def _reserve_session(self, session_id: str) -> None:
        """Claim a session for one in-flight run.

        Args:
            session_id: Session to claim.

        Raises:
            SessionBusyError: If the session is already claimed. Callers
                surface this as HTTP 409 (busy -> 409 semantics, D6).
        """
        with self._inflight_lock:
            if session_id in self._inflight:
                raise SessionBusyError(
                    f"Session {session_id} already has a run in progress"
                )
            self._inflight.add(session_id)

    def _release_session(self, session_id: str) -> None:
        """Release a session claim. Safe to call when no claim is held."""
        with self._inflight_lock:
            self._inflight.discard(session_id)

    def create_session(
        self,
        title: str = "",
        config: Optional[Dict[str, Any]] = None,
        owner: Optional[Principal] = None,
    ) -> Session:
        """Create a new session (native contract; engine session is lazy).

        Args:
            title: Session title.
            config: Session configuration.
            owner: Principal the session belongs to, or ``None`` when the
                creation path has no request context.

        Returns:
            The newly created Session.
        """
        session = Session(title=title, config=config or {}, owner=owner)
        self.store.create_session(session)
        self._search_index.index_session(session.session_id, title)
        self.event_bus.emit(
            session.session_id,
            "session.created",
            {"session_id": session.session_id, "title": title},
        )
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Return a session by ID."""
        return self.store.get_session(session_id)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """List all sessions."""
        return self.store.list_sessions(limit)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and drop its engine-session bookkeeping.

        The engine-side cascade (DELETE on the opencode session, D3 ownership
        rule) is T6's recovery/ownership module — the T3 driver surface has no
        delete primitive yet.
        """
        engine_session_id = self._engine_sessions.pop(session_id, None)
        if engine_session_id is not None:
            self._vt_by_engine.pop(engine_session_id, None)
        self.event_bus.clear(session_id)
        return self.store.delete_session(session_id)

    def get_messages(self, session_id: str, limit: int = 100) -> list[Message]:
        """Return the message history."""
        return self.store.get_messages(session_id, limit)

    async def send_message(
        self,
        session_id: str,
        content: str,
        role: str = "user",
        *,
        include_shell_tools: bool = False,
    ) -> Dict[str, Any]:
        """Send a message to a session and trigger one engine run.

        Positional-call compatible (``scheduled_routes.py:88`` and the IM
        relay call positionally). The transcript stores the RAW *content*;
        the D8 gateway-context injection is prepended only to the engine
        prompt (D3: messages.jsonl is the user-visible transcript).

        Args:
            session_id: Session ID.
            content: Message content.
            role: Message role; only ``user`` triggers an attempt.
            include_shell_tools: Recorded in ``session.config`` for parity,
                ignored by the engine (module-docstring adjudication, D6).

        Returns:
            Dictionary containing message_id and attempt_id.

        Raises:
            ValueError: If the session does not exist.
            SessionBusyError: If the session already has a run in progress
                (HTTP 409 at the API layer).
        """
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Claim the session before persisting anything (native parity): two
        # concurrent sends must not both store a message and create an attempt.
        if role == "user":
            self._reserve_session(session_id)
        handed_off = False

        try:
            message = Message(session_id=session_id, role=role, content=content)
            self.store.append_message(message)
            self._search_index.index_message(session_id, role, content)
            self.event_bus.emit(
                session_id,
                "message.received",
                {
                    "message_id": message.message_id,
                    "role": role,
                    "content": content,
                },
            )

            if role != "user":
                return {"message_id": message.message_id}

            attempt = Attempt(
                session_id=session_id,
                parent_attempt_id=session.last_attempt_id,
                prompt=content,
            )
            self.store.create_attempt(attempt)
            session.config["include_shell_tools"] = include_shell_tools
            session.last_attempt_id = attempt.attempt_id
            session.updated_at = datetime.now().isoformat()
            self.store.update_session(session)
            self.event_bus.emit(
                session_id,
                "attempt.created",
                {"attempt_id": attempt.attempt_id, "prompt": content},
            )

            self._ensure_pumps()
            task = asyncio.create_task(self._run_attempt(session, attempt))
            self._active_tasks[session_id] = task
            # _run_attempt now owns the claim and releases it in its finally.
            handed_off = True
            return {
                "message_id": message.message_id,
                "attempt_id": attempt.attempt_id,
            }
        finally:
            if role == "user" and not handed_off:
                self._release_session(session_id)

    def cancel_current(self, session_id: str) -> bool:
        """Cancel the in-flight attempt for a session (native contract).

        Two paths mirroring the native loop/task split: once the engine
        session is attached, flag the abort to the translator and POST the
        engine abort — the terminal ``attempt.cancelled`` event then flows
        through the normal persistence path; before attachment, cancel the
        attempt task directly.

        Args:
            session_id: Session ID.

        Returns:
            Whether cancellation was signalled.
        """
        attempt_id = self._active_by_session.get(session_id)
        run = self._runs.get(attempt_id) if attempt_id else None
        if run is not None and run.engine_session_id is not None:
            self._user_cancel_requests.add(session_id)
            self._translator.note_abort(run.engine_session_id)
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.create_task(self._abort_engine(run, run.engine_session_id))
            return True
        task = self._active_tasks.get(session_id)
        if task is not None and not task.done():
            self._user_cancel_requests.add(session_id)
            task.cancel()
            return True
        return False

    async def _engine_session_for(self, session: Session) -> str:
        """Return the engine session id for a vt session, creating it once."""
        existing = self._engine_sessions.get(session.session_id)
        if existing is not None:
            return existing
        engine_session_id = await self._driver.create_session(title=session.title or "")
        self._engine_sessions[session.session_id] = engine_session_id
        self._vt_by_engine[engine_session_id] = session.session_id
        return engine_session_id

    def _build_prompt_injection(self, session: Session) -> str:
        """Build the D8 gateway-context block prepended to every engine prompt.

        Part ① binds the session-bound MCP tools (goal tools +
        scheduled_research, whose proposals the IM/Web confirm surfaces look
        up by vt session id) to the vt session (the MCP
        ``_resolve_session_id`` fallback chain is untouched — an explicit
        ``session_id=`` always wins). Part ② resolves ``uploads/<name>``
        relative paths to the absolute uploads directory because the engine's
        native read resolves against its own workspace and would miss them
        (B6). Extension point: append further blocks here (T14 verifies the
        goal E2E; blocks go to the engine only, never into the transcript).

        Args:
            session: Session the prompt belongs to.

        Returns:
            The injection block, terminated by a blank line.
        """
        blocks = [
            "[gateway context]\n"
            f"vt_session_id={session.session_id}\n"
            "When calling session-bound tools (start_research_goal, "
            "add_goal_evidence, update_research_goal_status, "
            "get_research_goal, scheduled_research), pass session_id="
            f"'{session.session_id}' so goal and proposal state binds to "
            "this conversation.",
            "Uploaded files: a relative path uploads/<name> in this "
            f"conversation resolves to {get_uploads_dir()}/<name>. Read it "
            "via that ABSOLUTE path with a file-reading tool (the built-in "
            "read tool works); the relative form resolves against your "
            "workspace and will miss the file.",
        ]
        return "\n\n".join(blocks) + "\n\n"

    async def _run_attempt(self, session: Session, attempt: Attempt) -> None:
        """Execute one attempt against the engine and persist its terminal.

        This coroutine owns the in-flight claim taken in :meth:`send_message`
        (the finally is the only release path, native parity). The terminal
        outcome arrives as a translator event resolved onto ``run.done`` by
        the dispatch loop; prompt-acceptance failures and a dead engine
        stream land in the exception paths below, so the attempt never hangs
        (T5 QA failure scenario).

        Args:
            session: Owning session.
            attempt: The pending attempt record.
        """
        run = AttemptRun(
            attempt=attempt,
            session_id=session.session_id,
            started_at=time.perf_counter(),
            checkpoint=ResponseCheckpoint(self.store, attempt),
            done=asyncio.get_running_loop().create_future(),
        )
        self._runs[attempt.attempt_id] = run
        self._active_by_session[session.session_id] = attempt.attempt_id
        try:
            attempt.mark_running()
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session.session_id,
                "attempt.started",
                {"attempt_id": attempt.attempt_id},
            )

            engine_session_id = await self._engine_session_for(session)
            run.engine_session_id = engine_session_id
            self._translator.note_attempt(engine_session_id, attempt.attempt_id)

            prompt = self._build_prompt_injection(session) + attempt.prompt
            await self._driver.prompt_async(engine_session_id, prompt)

            status, data = await self._await_terminal(run)
            result = self._result_from_terminal(status, data, run.started_at)
            self._flush_checkpoint(run)
            self._persist_terminal_attempt(run, result)
        except asyncio.CancelledError:
            self._flush_checkpoint(run)
            if session.session_id in self._user_cancel_requests:
                # cancel_current() cancelled this task before the engine
                # session was attached. Persist the user cancel (reply +
                # terminal event, native main-path shape) so IM polling and
                # the scheduled-briefing reader see a terminal transcript.
                self._persist_terminal_attempt(
                    run,
                    {"status": "cancelled", "reason": "cancelled by user"},
                )
            else:
                logger.info(
                    "Leaving attempt %s recoverable after service task " "cancellation",
                    attempt.attempt_id,
                )
            raise
        except Exception as exc:
            self._flush_checkpoint(run)
            logger.warning(
                "opencode bridge attempt %s failed: %s", attempt.attempt_id, exc
            )
            self._persist_terminal_attempt(
                run,
                {
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
        finally:
            self._runs.pop(attempt.attempt_id, None)
            self._active_tasks.pop(session.session_id, None)
            self._active_by_session.pop(session.session_id, None)
            self._user_cancel_requests.discard(session.session_id)
            self._release_session(session.session_id)

    async def aclose(self) -> None:
        """Stop the plumbing tasks and close translator + driver (shutdown).

        Pending attempts are left recoverable on disk — the next startup's
        recovery (T6 reconciliation) finishes them, native shutdown parity.
        """
        tasks = [
            task
            for task in (self._pump_task, self._dispatch_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # Shutdown path: a dying pump already logged and failed its
                # pending attempts; cancellation is the expected outcome.
                pass
        self._pump_task = None
        self._dispatch_task = None
        await self._translator.aclose()
        driver_aclose = getattr(self._driver, "aclose", None)
        if driver_aclose is not None:
            await driver_aclose()
