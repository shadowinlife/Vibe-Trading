"""Wake-on-inbound: cold-start a stopped tenant container, or serve a fallback.

A tenant container that is stopped (scale-to-zero / reclaimed as idle) must
come back on its first inbound request. The plan's shape:

1. ask the backend for the container state;
2. start it when it is not running;
3. poll the gateway's unauthenticated ``/health`` until it answers — T10 made
   that endpoint answer only AFTER the lifespan preflight (bridge -> serve
   tool-map + reconcile) succeeds, so green means the full stack is wired;
4. on timeout, render a fallback page instead of a bare 502 (the caller adds
   ``Retry-After``); the client owns the retry, mirroring the SSE rule that
   retry belongs to the proxy/client contract, never to a silent reconnect.

``probe`` is injectable so the whole path is unit-testable without docker or a
network; the budgets come from :class:`~router.config.RouterSettings`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .backend import BackendUnavailable, ContainerBackend
from .config import RouterSettings
from .registry import Tenant

HealthProbe = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Awake:
    """The gateway answered /health within the budget."""

    elapsed_s: float
    started: bool
    state_before: str | None


@dataclass(frozen=True, slots=True)
class WakeTimedOut:
    """The budget expired with the gateway still silent -> fallback page."""

    elapsed_s: float
    started: bool
    state_before: str | None


@dataclass(frozen=True, slots=True)
class WakeUnavailable:
    """The backend could not be used at all (no docker, no such container)."""

    reason: str


WakeOutcome = Awake | WakeTimedOut | WakeUnavailable


def make_http_probe(client: httpx.AsyncClient) -> HealthProbe:
    """Build a /health probe over a shared client (connection errors = not ready)."""

    async def probe(url: str) -> bool:
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    return probe


async def wake_tenant(
    tenant: Tenant,
    backend: ContainerBackend,
    probe: HealthProbe,
    settings: RouterSettings,
) -> WakeOutcome:
    """Wake *tenant*'s container and wait for its gateway to become healthy.

    Four genuinely independent inputs: which tenant, how to control its
    container, how to observe its gateway, and the budgets — the health path,
    the timeout and the poll cadence all come from *settings* rather than being
    threaded as separate arguments.
    """
    health_url = tenant.upstream + settings.health_path
    timeout_s = settings.wake_timeout_s
    poll_interval_s = settings.wake_poll_interval_s
    started = time.monotonic()
    try:
        state = await backend.inspect_state(tenant.container)
        if state is None:
            return WakeUnavailable(reason=f"container {tenant.container!r} not found")
        did_start = False
        if state != "running":
            await backend.start(tenant.container)
            did_start = True
    except BackendUnavailable as exc:
        return WakeUnavailable(reason=str(exc))

    while True:
        if await probe(health_url):
            return Awake(
                elapsed_s=time.monotonic() - started,
                started=did_start,
                state_before=state,
            )
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            return WakeTimedOut(
                elapsed_s=time.monotonic() - started,
                started=did_start,
                state_before=state,
            )
        await asyncio.sleep(min(poll_interval_s, remaining))


def render_fallback_page(tenant_id: str, elapsed_s: float, retry_after_s: int) -> str:
    """Render the wake-timeout page (plain HTML, no assets, no JS)."""
    return _FALLBACK_TEMPLATE.format(
        tenant=tenant_id,
        elapsed=f"{elapsed_s:.1f}",
        retry_after=retry_after_s,
    )


_FALLBACK_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tenant {tenant} is starting</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:48px 24px;
background:#0f1115;color:#e6e6e6}}
main{{max-width:640px;margin:0 auto;background:#171a21;border:1px solid #262b36;
border-radius:12px;padding:32px}}
h1{{font-size:20px;margin:0 0 12px}}
p{{line-height:1.6;color:#b9c0cc;margin:0 0 12px}}
code{{background:#0f1115;padding:2px 6px;border-radius:4px;color:#8fd3ff}}
a{{color:#8fd3ff}}
</style>
</head>
<body>
<main>
<h1>Your workspace is starting</h1>
<p>Tenant <code>{tenant}</code> was idle and has been asked to start. It did not
become healthy within <code>{elapsed}s</code>, so the router stopped waiting.</p>
<p>A cold start loads the engine, its MCP servers and the plugin cache; the first
one can take several minutes. <a href="">Retry now</a>, or wait about
<code>{retry_after}s</code> and reload this page.</p>
<p>If it never comes up, the tenant container failed to start — check its logs.</p>
</main>
</body>
</html>
"""
