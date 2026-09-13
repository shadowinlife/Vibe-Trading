"""Steady-state E2E groups for the multi-tenant router (plan T11 acceptance).

Each function takes the :class:`~e2e_rig.Rig` and records into its recorder, so
T12 can add matrix groups without touching the driver. Groups are ordered by
cost: routing/header/fence checks are pure HTTP, the engine truth check is one
``docker exec``, and exactly ONE real model turn is spent (``--skip-model`` to
drop it). Lifecycle groups (hot reload / cold-start wake / fallback page) live
in :mod:`e2e_wake_checks`.

Unbuffered-ness is proven two ways, because a buffering proxy fails both:
headers must arrive on an idle stream (nothing to buffer), and during a real
turn the events must arrive SPREAD OUT rather than in one terminal burst.
"""

from __future__ import annotations

import time


from e2e_rig import Rig, TenantRig, docker_rss_mb, docker_state

# --- G1: routing table ------------------------------------------------------


def check_routing(rig: Rig) -> None:
    """Registered routes resolve; unregistered ones 404 without misrouting."""
    rec = rig.recorder
    tenants = list(rig.tenants.values())

    health = rig.request("GET", "/router-healthz")
    rec.check(
        "routing",
        "router healthz 200",
        health.status_code == 200,
        f"HTTP {health.status_code}",
    )
    rec.check(
        "routing",
        "healthz reports the registered tenant count",
        health.json().get("tenants") == len(tenants),
        f"tenants={health.json().get('tenants')}",
    )

    stray = rig.request("GET", "/sessions", host="unregistered.invalid")
    rec.check(
        "routing",
        "unregistered Host -> 404",
        stray.status_code == 404,
        f"HTTP {stray.status_code}",
    )
    rec.check(
        "routing",
        "unregistered Host is not misrouted (no tenant header, no upstream answer)",
        "x-vt-tenant" not in stray.headers
        and stray.json().get("detail") == "unknown tenant host",
        f"detail={stray.json().get('detail')}",
    )

    for tenant in tenants:
        own = rig.request("GET", "/sessions", tenant=tenant)
        rec.check(
            "routing",
            f"tenant {tenant.tenant_id}: own token+Host -> 200",
            own.status_code == 200
            and own.headers.get("x-vt-tenant") == tenant.tenant_id,
            f"HTTP {own.status_code} x-vt-tenant={own.headers.get('x-vt-tenant')}",
        )
        token_only = rig.request("GET", "/sessions", tenant=tenant, host="127.0.0.1")
        rec.check(
            "routing",
            f"tenant {tenant.tenant_id}: token alone routes (Host unregistered)",
            token_only.status_code == 200
            and token_only.headers.get("x-vt-tenant") == tenant.tenant_id,
            f"HTTP {token_only.status_code}",
        )

    if len(tenants) >= 2:
        first, second = tenants[0], tenants[1]
        crossed = rig.request("GET", "/sessions", tenant=first, host=second.public_host)
        rec.check(
            "routing",
            f"{first.tenant_id}'s token with {second.tenant_id}'s Host -> 403 (never guessed)",
            crossed.status_code == 403 and "x-vt-tenant" not in crossed.headers,
            f"HTTP {crossed.status_code}",
        )
        anonymous = rig.request("GET", "/sessions", tenant=first, auth=False)
        rec.check(
            "routing",
            "no token + registered Host is FORWARDED and the gateway rejects it (401)",
            anonymous.status_code == 401
            and anonymous.headers.get("x-vt-tenant") == first.tenant_id,
            f"HTTP {anonymous.status_code} — the router routes, the gateway authorizes",
        )
        wrong_key = rig.request(
            "GET",
            "/sessions",
            tenant=first,
            host=first.public_host,
            extra_headers={"Authorization": "Bearer nope"},
        )
        rec.check(
            "routing",
            "a wrong key on a registered Host still reaches the gateway's 401",
            wrong_key.status_code == 401,
            f"HTTP {wrong_key.status_code}",
        )


# --- G2/G7: SSE passthrough + fence observability (no model cost) -----------


def check_sse_headers_and_fence(rig: Rig, tenant: TenantRig) -> str:
    """An idle stream must deliver headers immediately and hold one admission."""
    rec = rig.recorder
    created = rig.request(
        "POST", "/sessions", tenant=tenant, json_body={"title": "T11 router SSE probe"}
    )
    if not rec.check(
        "sse",
        "POST /sessions through the router -> 201",
        created.status_code == 201,
        f"HTTP {created.status_code}",
    ):
        return ""
    session_id = str(created.json().get("session_id", ""))

    started = time.monotonic()
    response = rig.open_stream(f"/sessions/{session_id}/events", tenant=tenant)
    headers_at = time.monotonic() - started
    try:
        rec.measure(
            "sse_time_to_headers_s",
            headers_at,
            tenant=tenant.tenant_id,
            session_id=session_id,
        )
        rec.check(
            "sse",
            "headers arrive on an IDLE stream (a buffering proxy would hold them)",
            headers_at < 2.0,
            f"{headers_at:.3f}s",
        )
        rec.check(
            "sse",
            "content-type is text/event-stream",
            response.headers.get("content-type", "").startswith("text/event-stream"),
            response.headers.get("content-type", ""),
        )
        rec.check(
            "sse",
            "X-Accel-Buffering: no (portal events.ts recipe)",
            response.headers.get("x-accel-buffering") == "no",
            str(response.headers.get("x-accel-buffering")),
        )
        rec.check(
            "sse",
            "cache-control disables caching/transform",
            "no-cache" in response.headers.get("cache-control", ""),
            response.headers.get("cache-control", ""),
        )
        rec.check(
            "sse",
            "no content-length on a stream",
            "content-length" not in response.headers,
            str(response.headers.get("content-length")),
        )
        rec.check(
            "sse",
            "the router labels the tenant it routed to",
            response.headers.get("x-vt-tenant") == tenant.tenant_id,
            str(response.headers.get("x-vt-tenant")),
        )

        state = rig.admin("GET", "/router-admin/state").json()
        rec.check(
            "fence",
            "an open stream holds exactly one admission (in-flight)",
            state["tenants"][tenant.tenant_id]["inflight"] == 1,
            f"inflight={state['tenants'][tenant.tenant_id]['inflight']}",
        )
        rec.check(
            "fence",
            "proxy-side activity was recorded for the tenant",
            state["activity_ms"].get(tenant.tenant_id, 0) > 0,
            f"activity_ms={state['activity_ms'].get(tenant.tenant_id)}",
        )
    finally:
        response.close()

    # Client disconnect must release the admission (cancel propagation upstream).
    released = False
    for _ in range(40):
        time.sleep(0.25)
        after = rig.admin("GET", "/router-admin/state").json()
        if after["tenants"][tenant.tenant_id]["inflight"] == 0:
            released = True
            break
    rec.check(
        "fence",
        "client disconnect releases the admission",
        released,
        "inflight back to 0",
    )
    rec.check(
        "fence",
        "the router logged the disconnect (cancel propagated, not a silent close)",
        f"sse client disconnect tenant={tenant.tenant_id}" in rig.router.log_text(),
        "log line present" if released else "log missing",
    )
    return session_id


# --- G8: resource measurements (T12 extends these) --------------------------


def measure_resources(rig: Rig, label: str) -> None:
    for tenant in rig.tenants.values():
        state = docker_state(tenant.container)
        if state != "running":
            rig.recorder.note(
                f"resource sample skipped for {tenant.container} (state={state})"
            )
            continue
        rig.recorder.measure(
            f"rss_mb[{label}]",
            docker_rss_mb(tenant.container),
            unit="MiB",
            container=tenant.container,
        )


# --- helpers ----------------------------------------------------------------


def _frame_event_name(frame: bytes) -> str:
    for line in frame.decode("utf-8", "replace").splitlines():
        if line.startswith("event:"):
            return line[len("event:") :].strip()
    return ""
