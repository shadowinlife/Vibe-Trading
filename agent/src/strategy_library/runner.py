"""Runtime composition of the two scheduled-strategy execution paths.

:class:`StrategyRunner` is the object the scheduled-research executor holds. It
reads the strategy row to learn which path a job takes -- a code backtest or a
workflow chat run -- and dispatches accordingly. Code runs are synchronous
(snapshot copy plus ``Popen``), so the async seam moves them off the event loop;
workflow runs go through :func:`dispatch_workflow`, which creates exactly one
session whose tool allowlist is the strategy set and whose ``config`` carries no
permissions.

Resolution is lazy: neither this module nor :func:`resolve_strategy_runner`
touches ``sys.modules`` or the session runtime at import time. The production
session lookup is the same host lookup the scheduled-research dispatcher uses;
tests install :func:`set_session_service_factory` to supply their own service.

:meth:`StrategyRunner.poll` is total: the executor's sweep calls it unguarded,
so any failure inside a concrete poll becomes a failed ``RunOutcome`` with
``error_kind="runner_error"`` instead of escaping into the tick.
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.strategy_library import store as strategy_store
from src.strategy_library.execute_code import CodeHandle, poll_code_run, start_code_run
from src.strategy_library.execute_workflow import (
    WorkflowHandle,
    poll_workflow_run,
    start_workflow_run,
)
from src.strategy_library.history import RunOutcome
from src.tools.redaction import redact_internal_paths, redact_text

if TYPE_CHECKING:
    from src.scheduled_research.models import ScheduledResearchJob
    from src.scheduled_research.strategy_execution import InFlightHandle

logger = logging.getLogger(__name__)

#: Longest diagnostic kept in a failed outcome; mirrors the executor's bound.
MAX_ERROR_CHARS = 1000
#: Newest messages scanned for the workflow's terminal assistant reply.
_MESSAGE_SCAN_LIMIT = 50

Dispatcher = Callable[..., Awaitable[str]]
SessionServiceFactory = Callable[[], Any]

__all__ = [
    "MAX_ERROR_CHARS",
    "StrategyRunner",
    "dispatch_workflow",
    "read_session",
    "resolve_session_service",
    "resolve_strategy_runner",
    "set_session_service_factory",
]

#: Test seam. ``None`` means "use the production host lookup"; a factory is
#: read on every call so a test may install one before or after a runner is
#: built, and it is never consulted at import time.
_SESSION_SERVICE_FACTORY: SessionServiceFactory | None = None

_RUNNER: "StrategyRunner | None" = None


def set_session_service_factory(factory: SessionServiceFactory | None) -> None:
    """Install (or clear) the process-wide SessionService factory.

    Args:
        factory: Zero-argument callable returning the live
            :class:`~src.session.service.SessionService`, or ``None`` to
            restore the production host lookup.
    """
    global _SESSION_SERVICE_FACTORY
    _SESSION_SERVICE_FACTORY = factory


def resolve_session_service() -> Any:
    """Return the live SessionService, or raise when the runtime is not up.

    Production resolution matches the scheduled-research dispatcher: the host
    module registered as ``api_server`` (or ``agent.api_server``) owns the
    service singleton. Tests replace the lookup with
    :func:`set_session_service_factory`.

    Raises:
        RuntimeError: When no session runtime is available.
    """
    factory = _SESSION_SERVICE_FACTORY
    if factory is not None:
        service = factory()
        if service is None:
            raise RuntimeError("Session runtime not enabled")
        return service
    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    getter = getattr(host, "_get_session_service", None) if host is not None else None
    service = getter() if callable(getter) else None
    if service is None:
        raise RuntimeError("Session runtime not enabled")
    return service


def read_session(session_id: str) -> tuple[str, str] | None:
    """Return ``(terminal status, reply text)`` once the assistant has replied.

    Mirrors the scheduled-research briefing reader: the newest assistant
    message carrying a status in its metadata is the attempt's terminal reply,
    and the same text the user sees is what the output contract is parsed
    against. Returns ``None`` while no such message exists (or while the
    session runtime is unavailable), so a live run is never mistaken for a
    finished one.
    """
    try:
        service = resolve_session_service()
    except RuntimeError:
        return None
    for message in reversed(service.get_messages(session_id, limit=_MESSAGE_SCAN_LIMIT)):
        if getattr(message, "role", None) != "assistant":
            continue
        status = (getattr(message, "metadata", None) or {}).get("status")
        if isinstance(status, str) and status:
            return status, getattr(message, "content", "") or ""
    return None


async def dispatch_workflow(prompt: str, *, title: str, tool_allowlist: Any) -> str:
    """Create one allowlisted strategy session and enqueue *prompt* into it.

    The allowlist reaches ``create_session`` only -- it is persisted on the
    session and applied when the tool registry is built, never written into
    the user-visible ``config`` where a later reader could treat it as a
    preference rather than an enforcement boundary.

    Args:
        prompt: Rendered six-section workflow prompt.
        title: Session title naming the strategy, version and run.
        tool_allowlist: Exact tool names the session may expose.

    Returns:
        The new session id.

    Raises:
        RuntimeError: When the session runtime is unavailable.
    """
    service = resolve_session_service()
    session = service.create_session(
        title=title, tool_allowlist=sorted(tool_allowlist)
    )
    logger.info("dispatching strategy workflow into session %s", session.session_id)
    await service.send_message(session.session_id, prompt)
    return session.session_id


def _safe_attr(handle: Any, name: str) -> Any:
    """Return ``handle.name`` or ``None``; a broken property is not fatal."""
    try:
        return getattr(handle, name, None)
    except Exception:
        return None


def _failure(handle: Any, exc: BaseException) -> RunOutcome:
    """Return a bounded, redaction-safe runner failure for *exc*."""
    text = redact_text(
        redact_internal_paths(f"{type(exc).__name__}: {exc}")
    ).replace("\x00", "")
    if len(text) > MAX_ERROR_CHARS:
        text = f"{text[: MAX_ERROR_CHARS - 3]}..."
    return RunOutcome(
        status="failed",
        error=text,
        error_kind="runner_error",
        run_dir=_safe_attr(handle, "run_dir"),
        session_id=_safe_attr(handle, "session_id"),
    )


def _code_handle(handle: Any) -> CodeHandle:
    """Rebuild the code runner's handle from the persisted five fields."""
    return CodeHandle(
        run_id=handle.run_id,
        kind="code",
        session_id=handle.session_id,
        run_dir=handle.run_dir,
        started_at=handle.started_at,
    )


def _workflow_handle(handle: Any) -> WorkflowHandle:
    """Rebuild a workflow handle; its output contract comes from the cache."""
    return WorkflowHandle(
        run_id=handle.run_id,
        kind="workflow",
        session_id=handle.session_id,
        run_dir=handle.run_dir,
        started_at=handle.started_at,
    )


class StrategyRunner:
    """Dispatch scheduled strategy jobs onto the path their artifact names."""

    def __init__(
        self,
        *,
        dispatcher: Dispatcher | None = None,
        session_reader: Callable[[str], tuple[str, str] | None] | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        """Bind the runner to the workflow dispatcher and session reader.

        Args:
            dispatcher: Async workflow dispatcher; defaults to
                :func:`dispatch_workflow`.
            session_reader: Synchronous ``(status, text)`` reader for workflow
                polling; defaults to :func:`read_session`.
            runtime_root: Explicit strategy runtime root (tests); defaults to
                the process runtime root at call time.
        """
        self._dispatcher = dispatch_workflow if dispatcher is None else dispatcher
        self._read_session = read_session if session_reader is None else session_reader
        self._runtime_root = runtime_root

    async def start(self, job: ScheduledResearchJob) -> Any:
        """Begin the run *job* names and return its handle without waiting.

        A code strategy starts in a worker thread because the snapshot copy
        and the child spawn are synchronous; a workflow strategy dispatches
        the pinned spec into one chat session and returns immediately.

        Raises:
            LookupError: When the strategy is not in the library.
            ValueError: When the job names no strategy or an unknown kind.
        """
        strategy_id = job.strategy_id
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("a strategy job needs a non-empty strategy id")
        artifact = strategy_store.get(strategy_id)
        if artifact is None:
            raise LookupError(f"strategy {strategy_id!r} is not in the library")
        if artifact.kind == "code":
            return await asyncio.to_thread(
                start_code_run, job, runtime_root=self._runtime_root
            )
        if artifact.kind == "workflow":
            return await start_workflow_run(
                job, self._dispatcher, runtime_root=self._runtime_root
            )
        raise ValueError(
            f"strategy {strategy_id!r} has unsupported kind {artifact.kind!r}"
        )

    def poll(self, handle: InFlightHandle) -> RunOutcome | None:
        """Return the terminal outcome, or ``None`` while the run is live.

        Never raises: the executor's sweep calls this without a guard, so an
        unreadable handle, an unknown kind or a failing reader becomes a
        ``runner_error`` outcome instead of aborting the whole sweep.
        """
        try:
            kind = getattr(handle, "kind", "")
            if kind == "code":
                return poll_code_run(
                    _code_handle(handle), runtime_root=self._runtime_root
                )
            if kind == "workflow":
                return poll_workflow_run(
                    _workflow_handle(handle), read_session=self._read_session
                )
            raise ValueError(f"cannot poll a strategy handle of kind {kind!r}")
        except Exception as exc:
            logger.error(
                "strategy run %s poll failed",
                _safe_attr(handle, "run_id"),
                exc_info=True,
            )
            return _failure(handle, exc)


def resolve_strategy_runner() -> StrategyRunner:
    """Return the process-wide runner used by the production executor.

    Lazy and idempotent: nothing is imported or constructed until the executor
    is built, and repeated calls return the same instance. The session runtime
    is still resolved per dispatch, so installing a factory later takes effect.
    """
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = StrategyRunner()
    return _RUNNER
