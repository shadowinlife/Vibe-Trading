"""Factory wiring for the opencode engine bridge (plan T7, D1).

The single integration point between the gateway and the bridge internals,
so ``api/state.py`` stays a thin switch (plan: <=15 changed lines) and
``api_server.py`` stays under its 400-line cap:

* :func:`build_session_service` — synchronous construction for the
  ``_get_session_service()`` factory (``VIBE_TRADING_ENGINE=opencode``);
  the native construction path never imports this package.
* :func:`preflight_engine_bridge` — the async gateway-startup sequence the
  T6 recovery docstring contracts (``api_server._run_startup_preflight``
  awaits it before uvicorn accepts traffic): tool-name map load, then
  ``service.start()`` so the driver's SUBSCRIBE-FIRST stream is up before
  the first prompt can race it (T3 contract), then ``await
  service.reconcile()`` (three-branch engine-liveness reconciliation).
* :func:`stop_engine_bridge` — shutdown drain (cascade deletes, pumps,
  HTTP client) hooked into the api_server lifespan ``finally``. The hook
  imports this package unconditionally at shutdown (line budget): the
  import is side-effect-free and the drain itself is a no-op for the
  native service, which has no ``aclose`` seam.

Startup posture: a serve that is DOWN at startup is loud — the driver's
``OpencodeConnectionError`` from ``load_tool_mapping`` propagates and
blocks gateway startup (supervisord-style supervisors restart until the
engine is reachable; reconcile never runs against a dead engine). HTTP or
shape failures on the mapping endpoints degrade INSIDE
``load_tool_mapping`` (prefix-stripping fallback), never fatal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from src.config.accessor import get_env_config
from src.session.events import EventBus
from src.session.store import SessionStore

from .recovery import ReconciliationReport, RecoverableOpencodeSessionService

logger = logging.getLogger("opencode_bridge")

__all__ = [
    "build_session_service",
    "preflight_engine_bridge",
    "start_session_service",
    "stop_engine_bridge",
]


def build_session_service(
    store: SessionStore,
    event_bus: EventBus,
    runs_dir: Path,
) -> RecoverableOpencodeSessionService:
    """Construct the bridge session service from ``EnvConfig`` (D1 factory).

    The driver reads ``OPENCODE_BASE_URL`` / ``OPENCODE_SERVER_PASSWORD``
    via :meth:`OpencodeDriver.from_env`; the REAL T4 translator is injected
    here with the T1-calibrated quiescence window
    (``OPENCODE_BRIDGE_QUIESCENCE_S``, default 8.0) and the child-events
    policy (``OPENCODE_BRIDGE_CHILD_EVENTS``, default ``drop``). Its
    tool-name map starts empty and is replaced by
    :func:`start_session_service` once the serve answers (the translator's
    ``_bare`` indirection picks runtime map updates up, T4 contract).

    Args:
        store: Session persistence store (reused unmodified, D6).
        event_bus: SSE event bus (reused unmodified, D6).
        runs_dir: Root runs directory (construction-shape parity with the
            native service, ``api/state.py``).

    Returns:
        The fully wired :class:`RecoverableOpencodeSessionService`
        (recovery deferred to its :meth:`reconcile`, T6 contract).
    """
    from .driver import OpencodeDriver
    from .im_stream import ImStreamProducer
    from .translator import EventTranslator

    config = get_env_config().opencode_bridge
    driver = OpencodeDriver.from_env()
    translator = EventTranslator(
        quiescence_s=config.opencode_bridge_quiescence_s,
        child_events=config.opencode_bridge_child_events,
        tool_map=driver.tool_map,
    )
    service = RecoverableOpencodeSessionService(
        store=store,
        event_bus=event_bus,
        runs_dir=runs_dir,
        driver=driver,
        translator=translator,
    )
    # T9: the IM streaming producer taps translated events (per-channel
    # `streaming` gate; inert for web sessions and while channels are down).
    ImStreamProducer(store=store).attach(service)
    return service


async def preflight_engine_bridge(get_service: Callable[[], Any]) -> None:
    """api_server preflight hook: start the bridge session service.

    Args:
        get_service: ``state._get_session_service`` — returns ``None`` when
            ``ENABLE_SESSION_RUNTIME`` is false (bridge start skipped).
    """
    service = get_service()
    if service is not None:
        await start_session_service(service)


async def start_session_service(
    service: RecoverableOpencodeSessionService,
) -> ReconciliationReport:
    """Run the gateway-startup sequence for a built bridge service.

    Order is contract: (1) tool-name map, (2) subscribe-first pumps,
    (3) reconciliation — all before the lifespan startup completes, so no
    request can race a half-wired bridge (T6: ``await service.reconcile()``
    at startup, before serving traffic).

    Args:
        service: The service built by :func:`build_session_service`.

    Returns:
        The reconciliation report (logged as startup evidence).

    Raises:
        OpencodeConnectionError: The serve is unreachable at startup
            (loud by design — see the module docstring).
    """
    tool_map = await service._driver.load_tool_mapping()
    service._translator.tool_map = tool_map
    await service.start()
    report = await service.reconcile()
    logger.info(
        "opencode bridge started (base_url=%s): %d reattached, %d backfilled, "
        "%d interrupted",
        service._driver.base_url,
        len(report.reattached),
        len(report.backfilled),
        len(report.interrupted),
    )
    return report


async def stop_engine_bridge() -> None:
    """api_server shutdown hook: drain the bridge service if one was built.

    Reads the singleton from ``src.api.state`` (works in both host-module
    spellings — ``python3 api_server.py`` runs as ``__main__``, so the
    ``set_host_attr`` writeback never lands on the host). No-op for the
    native ``SessionService`` (no ``aclose`` seam).
    """
    from src.api import state as _state

    service: Optional[Any] = getattr(_state, "_session_service", None)
    aclose = getattr(service, "aclose", None)
    if aclose is not None:
        await aclose()
