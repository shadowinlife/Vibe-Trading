"""Control-plane scheduling client for RM agent strategy schedules.

The six assistant tools land in :mod:`src.agent_schedules.tools`; this package
exposes only the HTTP seam they share. See :mod:`src.agent_schedules.client`
for the route contract, token hygiene and frozen error taxonomy.
"""

from __future__ import annotations

from src.agent_schedules.client import (
    API_PREFIX,
    DEFAULT_TIMEOUT_S,
    DEFAULT_TOKEN_FILE,
    AgentScheduleClient,
    InvalidRequest,
    RmUnreachable,
    ScheduleError,
    ScheduleLimitReached,
    ScheduleNotFound,
    SchedulingUnavailable,
    Unauthorized,
    load_client,
)

__all__ = [
    "API_PREFIX",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOKEN_FILE",
    "AgentScheduleClient",
    "InvalidRequest",
    "RmUnreachable",
    "ScheduleError",
    "ScheduleLimitReached",
    "ScheduleNotFound",
    "SchedulingUnavailable",
    "Unauthorized",
    "load_client",
]
