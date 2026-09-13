"""Shared test harness for the tenant router (no docker, no network).

The router is exercised through ``httpx.ASGITransport`` while its upstream
client is an ``httpx.MockTransport`` standing in for one tenant gateway, so a
test controls both sides of the proxy: what the client sends and what the
gateway answers.

Note on streaming: ``ASGITransport`` buffers the whole ASGI response, so these
tests assert SSE *content* (headers, byte-for-byte relay, injected error
event) — the unbuffered first-byte behaviour needs a real server and is
asserted by the compose E2E (``deploy/e2e_multi_tenant.py``).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from router.app import RouterDeps, create_app
from router.backend import BackendUnavailable
from router.config import RouterSettings
from router.fence import TenantFence
from router.reclaim import ActivityLedger
from router.registry import Tenant, TenantRegistry, token_sha256

Responder = Callable[[httpx.Request], httpx.Response]

_UNSET = object()

TENANT_A = Tenant(
    "a", "a.tenant.local", "http://tenant-a.internal:8080", "vt-t11-a", "vt-t11-a-home"
)
TENANT_B = Tenant(
    "b", "b.tenant.local", "http://tenant-b.internal:8080", "vt-t11-b", "vt-t11-b-home"
)
TENANTS = (TENANT_A, TENANT_B)
KEYS = {"a": "key-a-secret", "b": "key-b-secret"}
UNUSED_REGISTRY = Path("no-such-registry.json")


class StubBackend:
    """Container backend double: per-container state transitions, no docker.

    ``state`` is the default for containers without an explicit record, so a
    multi-tenant pass can stop one container without changing another's verdict.
    ``gateway_up`` is the mock gateway's own liveness (one responder stands in
    for every tenant), consulted by the health probe and forwarded requests.
    """

    def __init__(
        self,
        state: str | None = "running",
        engine_body: str | None = None,
        start_heals: bool = True,
        inspect_error: str | None = None,
    ) -> None:
        self.state = state
        self.states: dict[str, str | None] = {}
        self.engine_body = engine_body
        self.start_heals = start_heals
        self.inspect_error = inspect_error
        self.gateway_up = state == "running"
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.engine_calls: list[tuple[str, str]] = []

    def state_of(self, container: str) -> str | None:
        return self.states.get(container, self.state)

    async def inspect_state(self, container: str) -> str | None:
        if self.inspect_error:
            raise BackendUnavailable(self.inspect_error)
        return self.state_of(container)

    async def start(self, container: str) -> None:
        self.started.append(container)
        self.states[container] = "running"
        self.gateway_up = self.start_heals

    async def stop(self, container: str) -> None:
        self.stopped.append(container)
        self.states[container] = "exited"
        self.gateway_up = False

    async def engine_get(self, container: str, path: str) -> str | None:
        self.engine_calls.append((container, path))
        return self.engine_body


class MockGateway:
    """One tenant gateway behind ``httpx.MockTransport``.

    ``fail_first`` raises once on the first request, which is how the Docker
    published-port proxy behaves right after ``docker stop`` (it accepts the
    connection and then resets it) — a different exception class than the
    connection refusal a fully torn-down port produces.
    """

    def __init__(
        self,
        responder: Responder,
        backend: StubBackend | None = None,
        fail_first: Exception | None = None,
    ) -> None:
        self.responder = responder
        self.backend = backend
        self.fail_first = fail_first
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_first is not None:
            failure, self.fail_first = self.fail_first, None
            raise failure
        if self.backend is not None and not self.backend.gateway_up:
            raise httpx.ConnectError("connection refused", request=request)
        return self.responder(request)

    def last(self) -> httpx.Request:
        return self.requests[-1]


@dataclass(slots=True)
class Stack:
    """Everything a proxy test needs to drive and inspect one router."""

    client: httpx.AsyncClient
    gateway: MockGateway
    backend: StubBackend
    fence: TenantFence
    ledger: ActivityLedger
    settings: RouterSettings

    def auth(self, tenant_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {KEYS[tenant_id]}"}


def json_responder(
    payload: dict[str, Any] | None = None, status: int = 200
) -> Responder:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json=payload if payload is not None else {"ok": True}
        )

    return respond


def sse_responder(chunks: Iterable[bytes], fail_after: int | None = None) -> Responder:
    """Answer an SSE stream; optionally die mid-stream after *fail_after* chunks."""

    async def body() -> AsyncIterator[bytes]:
        for index, chunk in enumerate(chunks):
            if fail_after is not None and index == fail_after:
                raise httpx.ReadError("upstream died mid-stream")
            yield chunk

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "cache-control": "no-cache",
                # Deliberately bogus: the router must drop content-length for SSE.
                "content-length": "999999",
            },
            content=body(),
        )

    return respond


@contextlib.asynccontextmanager
async def stack(
    responder: Responder | None = None,
    *,
    backend: StubBackend | None = None,
    tenants: tuple[Tenant, ...] = TENANTS,
    registry_path: Path | None = None,
    registry: TenantRegistry | Any = _UNSET,
    clock: Callable[[], float] | None = None,
    fail_first: Exception | None = None,
    **settings_kwargs: Any,
) -> AsyncIterator[Stack]:
    """Build a router + mock gateway pair and tear both clients down.

    ``registry=_UNSET`` (the default) injects a table built from *tenants*;
    ``registry=None`` leaves the router disk-backed, which is how the
    hot-reload and missing-registry paths are exercised.
    """
    stub = backend or StubBackend()
    gateway = MockGateway(responder or json_responder(), stub, fail_first=fail_first)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(gateway.handler))
    table = (
        TenantRegistry(
            {tenant.tenant_id: tenant for tenant in tenants},
            {
                token_sha256(KEYS[tenant.tenant_id]): tenant.tenant_id
                for tenant in tenants
            },
        )
        if registry is _UNSET
        else registry
    )
    fence = TenantFence()
    ledger = ActivityLedger(clock=clock or time.time)
    settings = RouterSettings(
        registry_path=registry_path or UNUSED_REGISTRY,
        wake_timeout_s=settings_kwargs.pop("wake_timeout_s", 5.0),
        **settings_kwargs,
    )
    app = create_app(
        settings,
        deps=RouterDeps(
            registry=table,
            backend=stub,
            client=upstream,
            fence=fence,
            ledger=ledger,
            clock=clock or time.time,
        ),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://router.local"
    )
    try:
        yield Stack(
            client=client,
            gateway=gateway,
            backend=stub,
            fence=fence,
            ledger=ledger,
            settings=settings,
        )
    finally:
        await client.aclose()
        await upstream.aclose()


def sse_frames(body: bytes) -> list[str]:
    """Split an SSE body into its ``event:`` names, in arrival order."""
    return [
        line.split(":", 1)[1].strip()
        for line in body.decode("utf-8").splitlines()
        if line.startswith("event:")
    ]
