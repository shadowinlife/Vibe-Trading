"""Mount the F9 strategy REST surfaces on the full API application.

The strategy library is always mounted. The schedule and run routers stay
opt-in because the dedicated in-box entry (``agent/strategy_run_server.py``) is
their production mount point: a second scheduling source or a second run entry
on the full API surface would split ownership of time-based triggering.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from src.api.strategy_library_routes import register_strategy_library_routes

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def _enabled(name: str) -> bool:
    """Return True when the default-off operator switch *name* is on.

    Args:
        name: Environment variable backing the switch.

    Returns:
        True for ``1``/``true``/``yes``/``on`` (case-insensitive), else False.
    """
    raw = os.environ.get(name, "")  # noqa: env-gate — default-off operator switch
    return raw.lower() in _ENABLED_VALUES


STRATEGY_SCHEDULE_ROUTES_ENABLED = _enabled("VT_STRATEGY_SCHEDULE_ROUTES_ENABLED")
STRATEGY_RUN_ENABLED = _enabled("VT_STRATEGY_RUN_ENABLED")


def register_strategy_surfaces(app: FastAPI) -> None:
    """Register the strategy library and the two opt-in F9 routers.

    Args:
        app: FastAPI application that receives the routes.
    """
    register_strategy_library_routes(app)
    if STRATEGY_SCHEDULE_ROUTES_ENABLED:
        from src.api.strategy_schedule_routes import register_strategy_schedule_routes

        register_strategy_schedule_routes(app)
    if STRATEGY_RUN_ENABLED:
        from src.api.strategy_run_routes import register_strategy_run_routes

        register_strategy_run_routes(app)
