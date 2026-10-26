#!/usr/bin/env python3
"""Dedicated in-box HTTP entry for RM-triggered strategy runs.

This app exists so the box can answer the two RM service routes without
exposing the full VT REST surface and without starting a second F9 scheduler.
It mounts ``/health`` plus ``POST /strategy-runs`` and
``GET /strategy-runs/{vt_run_id}`` and nothing else — there is deliberately no
SPA fallback or catch-all, so an unregistered path stays a framework ``404``.

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
import logging
from datetime import datetime, timezone

from fastapi import FastAPI

# Side-effect import: registers the host module that the strategy runner's
# session-service lookup reads. Never call api_server._run_startup_preflight()
# from here — it starts the F9 scheduler, which this entry must not run.
import api_server  # noqa: F401
from src.api.strategy_run_auth import log_token_configuration
from src.api.strategy_run_routes import ServiceProvider, register_strategy_run_routes

logger = logging.getLogger("strategy_run_entry")

SERVICE_NAME = "strategy-run entry"

__all__ = ["SERVICE_NAME", "create_app", "main", "serve_main"]


def _health_payload() -> dict[str, str]:
    """Minimal equivalent of the main API's ``/health`` payload."""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def create_app(service_provider: ServiceProvider | None = None) -> FastAPI:
    """Build the dedicated entry app.

    Args:
        service_provider: Optional callable returning the strategy-run
            service; tests inject a fake. ``None`` selects the lazy
            production provider (``src.strategy_runs.service``).

    Returns:
        An app carrying ``/health`` and exactly the two service routes.
    """
    app = FastAPI(
        title="Vibe-Trading Strategy Run Entry",
        description="RM-triggered strategy execution entry",
        version=api_server.APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Unauthenticated liveness probe for the entry process."""
        return _health_payload()

    register_strategy_run_routes(app, service_provider)
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
