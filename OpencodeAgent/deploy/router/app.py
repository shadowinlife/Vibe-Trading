"""The router ASGI app: tenant resolution, the admin surface, and wiring.

Routing is the whole job (plan T11 Must-NOT: no business logic). A request is
resolved to a tenant by token, else by Host; the tenant gateway then
re-validates the same Bearer key against its own ``API_AUTH_KEY``, so a routing
decision is never an authorization decision.

Forwarding mechanics live in :mod:`router.proxy`, header hygiene in
:mod:`router.headers`, and the lifecycle recipes in :mod:`router.wake` /
:mod:`router.reclaim` / :mod:`router.fence`.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .backend import ContainerBackend, build_backend
from .config import RouterSettings
from .fence import TenantDisposed, TenantFence
from .headers import bearer_token
from .proxy import Forwarder, json_response
from .reclaim import ActivityLedger, ReclaimContext, reclaim_idle
from .registry import (
    RegistryError,
    Resolved,
    Tenant,
    TenantMismatch,
    TenantRegistry,
    UnknownHost,
    UnknownToken,
)
from .wake import (
    Awake,
    WakeOutcome,
    WakeTimedOut,
    WakeUnavailable,
    make_http_probe,
    wake_tenant,
)

LOGGER = logging.getLogger("vt.router")

HEALTHZ_PATH = "/router-healthz"
ADMIN_STATE_PATH = "/router-admin/state"
ADMIN_RECLAIM_PATH = "/router-admin/reclaim"
# Starlette defaults a function endpoint to GET only; a proxy must accept the
# whole method surface the tenant gateway serves.
PROXY_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")


@dataclass(slots=True)
class RouterDeps:
    """Injectable collaborators (``None`` = build the production default)."""

    registry: TenantRegistry | None = None
    backend: ContainerBackend | None = None
    client: httpx.AsyncClient | None = None
    fence: TenantFence = field(default_factory=TenantFence)
    ledger: ActivityLedger = field(default_factory=ActivityLedger)
    clock: Callable[[], float] = time.time


def create_app(
    settings: RouterSettings | None = None, *, deps: RouterDeps | None = None
) -> Starlette:
    """Build the router ASGI app (``python -m router.cli serve``)."""
    router = Router(settings or RouterSettings.from_env(), deps or RouterDeps())
    return Starlette(
        routes=[
            Route(HEALTHZ_PATH, router.healthz, methods=["GET"]),
            Route(ADMIN_STATE_PATH, router.admin_state, methods=["GET"]),
            Route(ADMIN_RECLAIM_PATH, router.admin_reclaim, methods=["POST"]),
            Route("/{path:path}", router.proxy, methods=PROXY_METHODS),
        ],
        lifespan=router.lifespan,
    )


class Router:
    """Routing table + admin surface; delegates forwarding to :class:`Forwarder`."""

    __slots__ = (
        "_backend",
        "_client",
        "_clock",
        "_fence",
        "_forwarder",
        "_hot_reload",
        "_ledger",
        "_owns_client",
        "_registry",
        "_registry_mtime",
        "_settings",
    )

    def __init__(self, settings: RouterSettings, deps: RouterDeps) -> None:
        self._settings = settings
        self._backend = deps.backend or build_backend(
            settings.backend,
            docker_bin=settings.docker_bin,
            serve_url=settings.serve_url,
        )
        self._fence = deps.fence
        self._ledger = deps.ledger
        self._clock = deps.clock
        self._owns_client = deps.client is None
        self._client = deps.client or httpx.AsyncClient(follow_redirects=False)
        self._forwarder = Forwarder(self._client, settings, self._wake)
        self._registry = deps.registry
        self._registry_mtime: float | None = None
        # An injected table is authoritative (tests); only a disk-loaded table
        # hot-reloads, so provisioning a new tenant needs no router restart.
        self._hot_reload = deps.registry is None
        if self._hot_reload:
            self._registry = self._load_registry()

    @contextlib.asynccontextmanager
    async def lifespan(self, app: Starlette) -> AsyncIterator[None]:
        """Own the shared httpx client's lifetime when we created it."""
        app.state.router = self
        try:
            yield
        finally:
            if self._owns_client:
                await self._client.aclose()

    # --- registry -----------------------------------------------------------

    def _load_registry(self) -> TenantRegistry | None:
        path = self._settings.registry_path
        try:
            registry = TenantRegistry.load(path)
        except RegistryError as exc:
            LOGGER.warning("registry load failed path=%s error=%s", path, exc)
            return None
        self._registry_mtime = _mtime(path)
        return registry

    def current_registry(self) -> TenantRegistry | None:
        """Return the registry, hot-reloading when the file changed on disk.

        Provisioning writes the table while the router runs, so a new tenant
        becomes routable without a restart. A failed reload keeps the last good
        table (writes are atomic, so this only happens on a corrupt file).
        """
        if not self._hot_reload:
            return self._registry
        if self._registry is None:
            self._registry = self._load_registry()
            return self._registry
        mtime = _mtime(self._settings.registry_path)
        if mtime is None or mtime == self._registry_mtime:
            return self._registry
        reloaded = self._load_registry()
        if reloaded is not None:
            self._registry = reloaded
            LOGGER.info("registry reloaded tenants=%d", len(reloaded.tenants))
        return self._registry

    # --- the router's own surface -------------------------------------------

    async def healthz(self, request: Request) -> JSONResponse:
        """Unauthenticated liveness for the router itself (no tenant data)."""
        registry = self.current_registry()
        return JSONResponse(
            {"status": "ok", "tenants": len(registry.tenants) if registry else 0}
        )

    async def admin_state(self, request: Request) -> Response:
        """Admin-only dump of the routing table and the activity ledger."""
        denied = self._deny_unless_admin(request)
        if denied is not None:
            return denied
        registry = self.current_registry()
        tenants = (
            {
                tenant_id: {
                    "public_host": tenant.public_host,
                    "upstream": tenant.upstream,
                    "container": tenant.container,
                    "inflight": self._fence.inflight(tenant_id),
                    "disposed": self._fence.is_disposed(tenant_id),
                }
                for tenant_id, tenant in registry.tenants.items()
            }
            if registry
            else {}
        )
        return JSONResponse(
            {"tenants": tenants, "activity_ms": self._ledger.snapshot()}
        )

    async def admin_reclaim(self, request: Request) -> Response:
        """Admin-only one-shot idle reclaim (the interval policy is T12's)."""
        denied = self._deny_unless_admin(request)
        if denied is not None:
            return denied
        registry = self.current_registry()
        if registry is None:
            return json_response(503, "tenant registry unavailable")
        decisions = await reclaim_idle(
            list(registry.tenants.values()), self._reclaim_context()
        )
        return JSONResponse(
            [
                {
                    "tenant": decision.tenant_id,
                    "reclaim": decision.reclaim,
                    "reason": decision.reason,
                    "truth_source": decision.truth.source,
                    "truth_updated_ms": decision.truth.updated_ms,
                    "idle_ms": decision.idle_ms,
                    "drained": decision.drained,
                }
                for decision in decisions
            ]
        )

    def _reclaim_context(self) -> ReclaimContext:
        return ReclaimContext(
            backend=self._backend,
            fence=self._fence,
            ledger=self._ledger,
            idle_ttl_ms=int(self._settings.idle_ttl_s * 1000),
            drain_timeout_s=self._settings.drain_timeout_s,
            clock=self._clock,
        )

    def _deny_unless_admin(self, request: Request) -> JSONResponse | None:
        expected = self._settings.admin_token_sha256
        if not expected:
            return json_response(
                403, "admin surface disabled (VT_ROUTER_ADMIN_TOKEN_SHA256 unset)"
            )
        token = bearer_token(request.headers) or ""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
        if not token or not hmac.compare_digest(digest, expected):
            return json_response(401, "invalid admin token")
        return None

    # --- proxy --------------------------------------------------------------

    async def proxy(self, request: Request) -> Response:
        """Resolve the tenant, admit the request, hand it to the forwarder."""
        registry = self.current_registry()
        if registry is None:
            return json_response(503, "tenant registry unavailable")
        resolution = registry.resolve(
            token=bearer_token(request.headers), host=request.headers.get("host")
        )
        match resolution:
            case Resolved(tenant=tenant):
                pass
            case UnknownHost(host=host):
                LOGGER.info("route rejected reason=unknown_host host=%s", host)
                return json_response(404, "unknown tenant host", {"host": host})
            case UnknownToken(token_hint=hint):
                LOGGER.info("route rejected reason=unknown_token token_hint=%s", hint)
                return json_response(
                    401, "token is not registered", {"token_hint": hint}
                )
            case TenantMismatch(token_tenant=by_token, host_tenant=by_host):
                LOGGER.warning(
                    "route rejected reason=tenant_mismatch token_tenant=%s host_tenant=%s",
                    by_token,
                    by_host,
                )
                return json_response(403, "token and host resolve to different tenants")
            case unreachable:
                assert_never(unreachable)

        self._ledger.touch(tenant.tenant_id)
        try:
            admission = await self._fence.enter(tenant.tenant_id)
        except TenantDisposed:
            return json_response(
                410, "tenant is being disposed", {"tenant": tenant.tenant_id}
            )
        return await self._forwarder.forward(request, tenant, admission)

    async def _wake(self, tenant: Tenant) -> WakeOutcome:
        """Wake-on-inbound: start the tenant container and wait for /health."""
        LOGGER.info(
            "wake begin tenant=%s container=%s", tenant.tenant_id, tenant.container
        )
        outcome = await wake_tenant(
            tenant, self._backend, make_http_probe(self._client), self._settings
        )
        match outcome:
            case Awake(elapsed_s=elapsed, started=started, state_before=state):
                LOGGER.info(
                    "wake ok tenant=%s elapsed_s=%.1f started=%s state_before=%s",
                    tenant.tenant_id,
                    elapsed,
                    started,
                    state,
                )
            case WakeTimedOut(elapsed_s=elapsed, started=started, state_before=state):
                LOGGER.warning(
                    "wake timed out tenant=%s elapsed_s=%.1f started=%s state_before=%s",
                    tenant.tenant_id,
                    elapsed,
                    started,
                    state,
                )
            case WakeUnavailable(reason=reason):
                LOGGER.warning(
                    "wake unavailable tenant=%s reason=%s", tenant.tenant_id, reason
                )
            case unreachable:
                assert_never(unreachable)
        return outcome


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
