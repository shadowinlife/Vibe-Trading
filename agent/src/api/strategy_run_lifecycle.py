"""Process lifecycle for the opt-in RM strategy-run entry.

``VT_STRATEGY_RUN_ENABLED`` mounts ``/strategy-runs`` on the full API server, so
that app's lifespan must own the service: a mounted route whose sweeper never
starts would accept runs and leave them ``running`` forever. The dedicated
``agent/strategy_run_server.py`` entry starts the same process-wide singleton
(``build_service`` is a singleton); the two entry points never run together.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["start_strategy_run_service", "stop_strategy_run_service"]

_service: Any | None = None


def _build_strategy_run_service() -> Any:
    """Build the process-wide RM entry service (one per process)."""
    from src.strategy_runs.service import build_service

    return build_service()


async def start_strategy_run_service() -> None:
    """Start the process-wide RM entry service."""
    global _service
    service = _build_strategy_run_service()
    await service.start()
    _service = service
    logging.getLogger(__name__).info("strategy-run service started")


async def stop_strategy_run_service() -> None:
    """Stop the entry service; safe when it never started."""
    global _service
    service, _service = _service, None
    if service is not None:
        await service.stop()