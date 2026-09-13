"""Provisioning/wake E2E groups: hot reload, cold start, fallback page.

These are the groups that touch the container lifecycle, split from
:mod:`e2e_checks` (steady-state routing/SSE/model/reclaim) so each module stays
reviewable. They are the plan T11 QA scenarios:

* happy — a tenant provisioned while the router runs becomes routable with no
  restart; a stopped tenant is woken by its first inbound request and the
  gateway answers that same request;
* failure — a container that starts but never serves produces the timeout
  fallback page (503 + ``Retry-After``), never a silent hang or a misroute.
"""

from __future__ import annotations

import time

import httpx

from e2e_rig import Rig, TenantRig, docker, docker_state

FALLBACK_MARKER = "Your workspace is starting"


# --- G3a: registry hot reload (provision while the router runs) -------------


def check_hot_reload(
    rig: Rig, tenant_id: str, host_suffix: str, provision, deregister
) -> None:
    """A tenant provisioned while the router runs becomes routable, no restart."""
    rec = rig.recorder
    public_host = f"{tenant_id}.{host_suffix}"
    before = rig.request("GET", "/sessions", host=public_host)
    rec.check(
        "hot-reload",
        f"tenant {tenant_id} is unroutable before provisioning",
        before.status_code == 404 and "x-vt-tenant" not in before.headers,
        f"HTTP {before.status_code}",
    )

    ghost = provision()
    if ghost is None:
        rec.check(
            "hot-reload", "ghost tenant provisioned", False, "provisioning failed"
        )
        return
    _wait_for_registry(rig, tenant_id, expect=True)
    after = rig.request("GET", "/sessions", tenant=ghost, timeout=90.0)
    rec.check(
        "hot-reload",
        "the router picked the new tenant up WITHOUT a restart",
        after.status_code != 404,
        f"HTTP {after.status_code} (404 would mean the table was not reloaded)",
    )
    rec.check(
        "hot-reload",
        "a new tenant with no container gets the fallback page, not a misroute",
        after.status_code == 503 and FALLBACK_MARKER in after.text,
        f"HTTP {after.status_code}",
    )
    rec.check(
        "hot-reload",
        "the router reported the missing container (wake unavailable)",
        f"wake unavailable tenant={tenant_id}" in rig.router.log_text(),
        "log line present",
    )

    deregister()
    _wait_for_registry(rig, tenant_id, expect=False)
    removed = rig.request("GET", "/sessions", host=public_host)
    rec.check(
        "hot-reload",
        "de-registering makes it 404 again",
        removed.status_code == 404,
        f"HTTP {removed.status_code}",
    )


def _wait_for_registry(
    rig: Rig, tenant_id: str, *, expect: bool, timeout_s: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = rig.admin("GET", "/router-admin/state").json()
        if (tenant_id in state["tenants"]) is expect:
            return
        time.sleep(0.2)


# --- G3b: wake-on-inbound cold start ----------------------------------------


def check_cold_start_wake(rig: Rig, tenant: TenantRig, *, budget_s: float) -> None:
    """Stop the tenant, then let one inbound request wake it (QA happy path)."""
    rec = rig.recorder
    stopped = docker("stop", tenant.container)
    rec.check(
        "wake",
        f"docker stop {tenant.container}",
        stopped.returncode == 0,
        stopped.stderr.strip()[:120],
    )
    rec.check(
        "wake",
        "container is exited before the request",
        docker_state(tenant.container) == "exited",
        str(docker_state(tenant.container)),
    )

    started = time.monotonic()
    response = rig.request(
        "POST",
        "/sessions",
        tenant=tenant,
        json_body={"title": "T11 cold-start wake"},
        timeout=budget_s,
    )
    elapsed = time.monotonic() - started
    rec.measure(
        "cold_start_wake_s",
        elapsed,
        tenant=tenant.tenant_id,
        status=response.status_code,
    )

    rec.check(
        "wake",
        "the inbound request woke the container and the gateway answered",
        response.status_code == 201,
        f"HTTP {response.status_code} in {elapsed:.1f}s",
    )
    rec.check(
        "wake",
        "the response is a real session (gateway answered, not the router)",
        (
            bool(response.json().get("session_id"))
            if response.status_code == 201
            else False
        ),
        str(response.json())[:160],
    )
    rec.check(
        "wake",
        "the container is running afterwards",
        docker_state(tenant.container) == "running",
        str(docker_state(tenant.container)),
    )
    rec.check(
        "wake",
        "the router logged the wake",
        f"wake ok tenant={tenant.tenant_id}" in rig.router.log_text(),
        "log line present",
    )
    rec.check(
        "wake",
        "the router started the container itself",
        f"wake begin tenant={tenant.tenant_id}" in rig.router.log_text(),
        "log line present",
    )

    healthy = _wait_for_health(tenant, timeout_s=120.0)
    rec.check(
        "wake",
        "the woken gateway stays healthy",
        healthy,
        f"/health reachable={healthy}",
    )


def _wait_for_health(tenant: TenantRig, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{tenant.upstream}/health", timeout=5.0).status_code < 400:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


# --- G4: wake timeout fallback page -----------------------------------------


def check_wake_timeout_fallback(
    rig: Rig, tenant: TenantRig, *, wake_timeout_s: float
) -> None:
    """A container that starts but never serves must produce the fallback page."""
    rec = rig.recorder
    started = time.monotonic()
    response = rig.request(
        "GET", "/sessions", tenant=tenant, timeout=wake_timeout_s + 60.0
    )
    elapsed = time.monotonic() - started
    rec.measure(
        "wake_timeout_s", elapsed, tenant=tenant.tenant_id, status=response.status_code
    )

    rec.check(
        "fallback",
        "wake timeout -> 503",
        response.status_code == 503,
        f"HTTP {response.status_code} after {elapsed:.1f}s",
    )
    rec.check(
        "fallback",
        "Retry-After is set",
        bool(response.headers.get("retry-after")),
        str(response.headers.get("retry-after")),
    )
    rec.check(
        "fallback",
        "the fallback page renders",
        FALLBACK_MARKER in response.text
        and response.headers.get("content-type", "").startswith("text/html"),
        response.headers.get("content-type", ""),
    )
    rec.check(
        "fallback",
        f"the page names tenant {tenant.tenant_id}",
        f"<code>{tenant.tenant_id}</code>" in response.text,
        "tenant id present",
    )
    rec.check(
        "fallback",
        "the page is not cached",
        response.headers.get("cache-control") == "no-store",
        str(response.headers.get("cache-control")),
    )
    rec.check(
        "fallback",
        "the router waited roughly the configured budget",
        elapsed >= wake_timeout_s * 0.8,
        f"{elapsed:.1f}s >= {wake_timeout_s * 0.8:.1f}s",
    )
    rec.check(
        "fallback",
        "the router logged the timeout",
        f"wake timed out tenant={tenant.tenant_id}" in rig.router.log_text(),
        "log line present",
    )
