"""Router configuration — the single environment boundary.

The router is a deploy-side process, NOT part of ``agent/src``, so it does not
import the gateway's ``EnvConfig`` (that would couple the thin proxy to the
business surface it must stay out of). Every environment read in this package
happens in :meth:`RouterSettings.from_env`; every other module receives typed
values by injection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Self

ENV_PREFIX = "VT_ROUTER_"

# Container-internal opencode serve (T10 port plan: serve 4096 stays internal,
# the gateway on 8080 is the single public port). The reclaim truth check
# reaches it through ``docker exec``, never through a published port.
DEFAULT_SERVE_URL = "http://127.0.0.1:4096"


@dataclass(frozen=True, slots=True)
class RouterSettings:
    """Typed router settings (immutable; built once at the env boundary)."""

    registry_path: Path
    host: str = "0.0.0.0"
    port: int = 28080
    # Connect stays short so an unreachable upstream falls into the wake path
    # fast. Read is infinite by design: SSE streams and long model turns have
    # no bounded response time — liveness comes from client-disconnect cancel
    # propagation, not from a read deadline. ``0.0`` means "no read timeout".
    connect_timeout_s: float = 5.0
    write_timeout_s: float = 60.0
    read_timeout_s: float = 0.0
    wake_timeout_s: float = 300.0
    wake_poll_interval_s: float = 1.0
    drain_timeout_s: float = 30.0
    idle_ttl_s: float = 10800.0
    # 0 disables the periodic reclaim loop. The plan assigns the reclaim
    # POLICY (idle N hours) to T12; the truth check lives here.
    reclaim_interval_s: float = 0.0
    # Empty disables the admin surface entirely (fail closed).
    admin_token_sha256: str = ""
    backend: str = "docker-cli"
    log_level: str = "INFO"
    docker_bin: str = "docker"
    serve_url: str = DEFAULT_SERVE_URL
    health_path: str = "/health"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Parse ``VT_ROUTER_*`` variables into typed settings."""
        raw: Mapping[str, str] = os.environ if env is None else env
        registry = _str(raw, "REGISTRY", "tenant_registry.json")
        return cls(
            registry_path=Path(registry).expanduser(),
            host=_str(raw, "HOST", "0.0.0.0"),
            port=_int(raw, "PORT", 28080),
            connect_timeout_s=_float(raw, "CONNECT_TIMEOUT_S", 5.0),
            write_timeout_s=_float(raw, "WRITE_TIMEOUT_S", 60.0),
            read_timeout_s=_float(raw, "READ_TIMEOUT_S", 0.0),
            wake_timeout_s=_float(raw, "WAKE_TIMEOUT_S", 300.0),
            wake_poll_interval_s=_float(raw, "WAKE_POLL_INTERVAL_S", 1.0),
            drain_timeout_s=_float(raw, "DRAIN_TIMEOUT_S", 30.0),
            idle_ttl_s=_float(raw, "IDLE_TTL_S", 10800.0),
            reclaim_interval_s=_float(raw, "RECLAIM_INTERVAL_S", 0.0),
            admin_token_sha256=_str(raw, "ADMIN_TOKEN_SHA256", ""),
            backend=_str(raw, "BACKEND", "docker-cli"),
            log_level=_str(raw, "LOG_LEVEL", "INFO").upper(),
            docker_bin=_str(raw, "DOCKER_BIN", "docker"),
            serve_url=_str(raw, "SERVE_URL", DEFAULT_SERVE_URL),
            health_path=_str(raw, "HEALTH_PATH", "/health"),
        )


def _lookup(raw: Mapping[str, str], name: str, default: str) -> str:
    value = raw.get(ENV_PREFIX + name, "")
    return value.strip() if value.strip() else default


def _str(raw: Mapping[str, str], name: str, default: str) -> str:
    return _lookup(raw, name, default)


def _int(raw: Mapping[str, str], name: str, default: int) -> int:
    return int(_lookup(raw, name, str(default)))


def _float(raw: Mapping[str, str], name: str, default: float) -> float:
    return float(_lookup(raw, name, str(default)))
