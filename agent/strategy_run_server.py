#!/usr/bin/env python3
"""Dedicated in-box HTTP entry for RM-triggered strategy runs and state.

This app exists so the box can answer the RM service routes without exposing
the full VT REST surface and without starting a second F9 scheduler. It mounts
``/health``, ``POST /strategy-runs``, ``GET /strategy-runs/{vt_run_id}``,
``POST /state/export`` and ``POST /state/import`` and nothing else — there is
deliberately no SPA fallback or catch-all, so an unregistered path (including
``/strategy-schedules``) stays a framework ``404``. No scheduler is started and
no lifespan beyond this module's own is executed.

``api_server`` is imported at module scope on purpose and is load-bearing:
:func:`src.strategy_library.runner.resolve_session_service` resolves the
process-wide ``SessionService`` only through ``sys.modules["api_server"]`` (or
``"agent.api_server"``). Importing the module registers it there and builds
the FastAPI app; it does **not** run its lifespan, so
``_run_startup_preflight`` — the sole production site that starts the
scheduled-research executor — never runs in this process.
"""

from __future__ import annotations

import argparse
import inspect
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

# Side-effect import: registers the host module that the strategy runner's
# session-service lookup reads. Never call api_server._run_startup_preflight()
# from here — it starts the F9 scheduler, which this entry must not run.
import api_server  # noqa: F401
from src.api.state_routes import register_state_routes
from src.api.strategy_run_auth import log_token_configuration, token_configured
from src.api.strategy_run_routes import (
    ServiceProvider,
    default_service_provider,
    register_strategy_run_routes,
)

logger = logging.getLogger("strategy_run_entry")

SERVICE_NAME = "strategy-run entry"

__all__ = ["SERVICE_NAME", "create_app", "main", "serve_main"]


def _health_payload() -> dict[str, str | bool]:
    """Minimal equivalent of the main API's ``/health`` payload."""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "token_configured": token_configured(),
    }


async def _call_lifecycle(service: object, method_name: str) -> None:
    """Await ``service.<method_name>()`` when the service implements it.

    Fake services used by the route tests expose only ``submit``/``status``;
    they are valid providers and simply have no lifecycle.
    """
    method = getattr(service, method_name, None)
    if not callable(method):
        return
    outcome = method()
    if inspect.isawaitable(outcome):
        await outcome


def create_app(service_provider: ServiceProvider | None = None) -> FastAPI:
    """Build the dedicated entry app.

    The lifespan resolves the provider once, starts the service's sweeper (when
    it has one) and stops it on shutdown. Production construction failure is
    fatal: an entry whose ledger cannot be opened must not answer ``/health``
    and look serviceable. An injected provider (a test seam) keeps the
    route-level contract instead — its construction failure answers ``503`` per
    request — so the existing frozen route tests still exercise that path.

    Args:
        service_provider: Optional callable returning the strategy-run
            service; tests inject a fake. ``None`` selects the lazy
            production provider (``src.strategy_runs.service``).

    Returns:
        An app carrying ``/health``, the two strategy-run routes and the two
        state routes.
    """
    provider = (
        service_provider if service_provider is not None else default_service_provider
    )
    strict_startup = service_provider is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202 — Starlette lifespan type
        try:
            service = provider()
        except Exception as exc:  # noqa: BLE001 — startup gate, not request state
            logger.error(
                "strategy-run service unavailable at startup (%s)", type(exc).__name__
            )
            if strict_startup:
                raise
            service = None
        if service is not None:
            app.state.strategy_run_service = service
            await _call_lifecycle(service, "start")
            logger.info("strategy-run service started")
        try:
            yield
        finally:
            if service is not None:
                try:
                    await _call_lifecycle(service, "stop")
                except Exception:  # noqa: BLE001 — shutdown must finish
                    logger.error("strategy-run service did not stop cleanly")
                logger.info("strategy-run service stopped")

    app = FastAPI(
        title="Vibe-Trading Strategy Run Entry",
        description="RM-triggered strategy execution entry",
        version=api_server.APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def _service_for_request() -> object:
        """Return the lifespan-resolved service, resolving lazily if needed."""
        service = getattr(app.state, "strategy_run_service", None)
        if service is not None:
            return service
        service = provider()
        app.state.strategy_run_service = service
        return service

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str | bool]:
        """Unauthenticated liveness probe for the entry process."""
        return _health_payload()

    register_strategy_run_routes(app, _service_for_request)
    register_state_routes(app)
    log_token_configuration()
    return app


def serve_main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, announce readiness and serve until stopped."""
    parser = argparse.ArgumentParser(description="Vibe-Trading strategy-run entry")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Listen port (default 8000)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    app = create_app()

    import uvicorn

    class _ReadyMarkerServer(uvicorn.Server):
        """Announce readiness only after the socket is bound and startup ran."""

        async def startup(self, sockets=None):  # noqa: ANN001
            await super().startup(sockets=sockets)
            if self.started:
                print(
                    f"strategy-run entry listening on {args.host}:{args.port}",
                    flush=True,
                )

    _ReadyMarkerServer(uvicorn.Config(app, host=args.host, port=args.port)).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Alias kept for symmetry with ``api_server.serve_main``."""
    return serve_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
