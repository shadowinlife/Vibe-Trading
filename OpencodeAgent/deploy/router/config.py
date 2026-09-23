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
    # T19-B1(L1): gateway shapes that ANSWER an asleep tenant with an ordinary
    # HTTP response (the kruise data-plane gateway 502s a paused/absent
    # sandbox) instead of producing no response at all (docker's refused/
    # reset port -> TransportError). A response status in this set triggers
    # the same wake-and-replay path. EMPTY = transport-error-only trigger,
    # i.e. the docker form unchanged.
    wake_on_statuses: frozenset[int] = frozenset()
    # T19-B1(L2): the direct-serve form (opencode serve as PID1, pool template
    # basic auth) answers EVERY route with 401 when unauthenticated, so the
    # wake probe's status<400 contract can never go green. On that form a 401
    # can only originate from serve itself (gateway route errors are 502/404/
    # 503, T19 evidence), so 401 proves serve is up. Off by default: the
    # docker/T10-gateway form's /health never 401s (public 200/503), so the
    # strict contract stays exact there.
    health_accept_unauthorized: bool = False

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
            wake_on_statuses=_status_set(raw, "WAKE_ON_STATUSES"),
            health_accept_unauthorized=_bool(raw, "HEALTH_ACCEPT_UNAUTHORIZED"),
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


def _status_set(raw: Mapping[str, str], name: str) -> frozenset[int]:
    """Comma list of HTTP statuses; blank/absent = empty set, junk fails fast."""
    value = _lookup(raw, name, "")
    statuses: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            statuses.append(int(part))
        except ValueError as exc:
            raise ValueError(
                f"{ENV_PREFIX}{name} must be a comma list of HTTP statuses, got {value!r}"
            ) from exc
    return frozenset(statuses)


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _bool(raw: Mapping[str, str], name: str) -> bool:
    value = _lookup(raw, name, "").lower()
    if not value:
        return False
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{ENV_PREFIX}{name} must be a boolean (true/false/1/0/yes/no/on/off),"
        f" got {value!r}"
    )
