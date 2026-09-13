"""Wake-on-inbound and admin-surface tests (plan T11).

The cold-start path end to end at the HTTP level: an unreachable upstream
starts the tenant container, waits for ``/health``, and REPLAYS the original
request (body included); when the container never becomes healthy the router
answers a fallback page with ``Retry-After`` instead of a bare 502.

Also pins the admin surface: disabled without a configured token (fail closed),
401 on a wrong token, and a reclaim pass whose verdicts come from the engine
truth check rather than from proxy-side activity.
"""

from __future__ import annotations

import asyncio
import json
import httpx

from router.registry import token_sha256
from router_harness import Responder, StubBackend, json_responder, stack

ADMIN_KEY = "admin-secret"
NOW_MS = 1_800_000_000_000


def frozen_clock() -> float:
    """Fixed wall clock so reclaim idleness is deterministic."""
    return NOW_MS / 1000


def sessions_payload(updated_ms: int) -> str:
    return json.dumps(
        [{"id": "ses_1", "time": {"created": updated_ms, "updated": updated_ms}}]
    )


def gateway_responder() -> Responder:
    """Answer everything; ``MockGateway`` raises ConnectError while it is down."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"delivered": True})

    return respond


def forwarded_paths(
    rig_requests: list[httpx.Request], path: str
) -> list[httpx.Request]:
    return [request for request in rig_requests if request.url.path == path]


def test_a_stopped_tenant_is_woken_and_the_request_replayed_with_its_body() -> None:
    async def scenario() -> dict[str, object]:
        backend = StubBackend(state="exited")
        payload = json.dumps({"channel": "telegram", "text": "wake up"}).encode()
        async with stack(
            gateway_responder(), backend=backend, wake_timeout_s=5.0
        ) as rig:
            response = await rig.client.post(
                "/channels/webhook",
                headers={**rig.auth("a"), "content-type": "application/json"},
                content=payload,
            )
            webhooks = forwarded_paths(rig.gateway.requests, "/channels/webhook")
            return {
                "status": response.status_code,
                "body": response.json(),
                "started": backend.started,
                "attempts": len(webhooks),
                "first_body": webhooks[0].content,
                "replay_body": webhooks[-1].content,
                "health_probed": bool(forwarded_paths(rig.gateway.requests, "/health")),
                "tenant": response.headers["x-vt-tenant"],
            }

    result = asyncio.run(scenario())

    assert result["status"] == 200
    assert result["body"] == {"delivered": True}
    assert result["started"] == ["vt-t11-a"]
    assert result["attempts"] == 2
    assert result["health_probed"] is True
    assert result["tenant"] == "a"
    # The retry replays the ORIGINAL body: a webhook must not be delivered
    # empty just because the tenant was asleep on the first attempt.
    assert result["first_body"] == result["replay_body"]
    assert json.loads(result["replay_body"].decode()) == {
        "channel": "telegram",
        "text": "wake up",
    }


def test_a_running_but_unhealthy_tenant_is_polled_not_restarted() -> None:
    async def scenario() -> tuple[int, list[str]]:
        backend = StubBackend(state="running")
        backend.gateway_up = False  # container runs, gateway not listening yet
        async with stack(
            gateway_responder(),
            backend=backend,
            wake_timeout_s=0.05,
            wake_poll_interval_s=0.01,
        ) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return response.status_code, backend.started

    # start() is never called (the container already runs), so the health poll
    # runs out and the fallback page answers.
    status, started = asyncio.run(scenario())

    assert status == 503
    assert started == []


def test_wake_timeout_serves_the_fallback_page_with_retry_after() -> None:
    async def scenario() -> tuple[int, str, str, str]:
        backend = StubBackend(state="exited", start_heals=False)
        async with stack(
            gateway_responder(),
            backend=backend,
            wake_timeout_s=0.05,
            wake_poll_interval_s=0.01,
        ) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return (
                response.status_code,
                response.headers["retry-after"],
                response.headers["content-type"],
                response.text,
            )

    status, retry_after, content_type, page = asyncio.run(scenario())

    assert status == 503
    assert retry_after == "30"
    assert content_type.startswith("text/html")
    assert "Your workspace is starting" in page
    assert "Tenant <code>a</code>" in page
    assert "become healthy within" in page


def test_a_missing_container_serves_the_fallback_page_without_starting_anything() -> (
    None
):
    async def scenario() -> tuple[int, list[str], str]:
        backend = StubBackend(state=None)
        async with stack(
            gateway_responder(), backend=backend, wake_timeout_s=0.05
        ) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return response.status_code, backend.started, response.text

    status, started, page = asyncio.run(scenario())

    assert status == 503
    assert started == []
    assert "Your workspace is starting" in page


def test_an_unusable_docker_backend_serves_the_fallback_page() -> None:
    async def scenario() -> tuple[int, str]:
        backend = StubBackend(state="exited", inspect_error="docker socket is gone")
        async with stack(
            gateway_responder(), backend=backend, wake_timeout_s=0.05
        ) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return response.status_code, response.text

    status, page = asyncio.run(scenario())

    assert status == 503
    assert "Your workspace is starting" in page


def test_the_fallback_page_names_the_tenant_and_is_not_cached() -> None:
    async def scenario() -> tuple[str, str]:
        backend = StubBackend(state=None)
        async with stack(gateway_responder(), backend=backend) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("b"))
            return response.headers["cache-control"], response.text

    cache_control, page = asyncio.run(scenario())

    assert cache_control == "no-store"
    assert "Tenant <code>b</code>" in page


# --- admin surface ----------------------------------------------------------


def test_admin_surface_is_disabled_without_a_configured_token() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack(json_responder()) as rig:
            reclaim = await rig.client.post("/router-admin/reclaim")
            state = await rig.client.get("/router-admin/state")
            return reclaim.status_code, state.status_code

    assert asyncio.run(scenario()) == (403, 403)


def test_admin_surface_rejects_a_wrong_token() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack(
            json_responder(), admin_token_sha256=token_sha256(ADMIN_KEY)
        ) as rig:
            reclaim = await rig.client.post(
                "/router-admin/reclaim", headers={"Authorization": "Bearer wrong"}
            )
            state = await rig.client.get(
                "/router-admin/state", headers={"Authorization": "Bearer wrong"}
            )
            return reclaim.status_code, state.status_code

    assert asyncio.run(scenario()) == (401, 401)


def test_admin_reclaim_stops_only_the_tenant_the_engine_reports_as_idle() -> None:
    class PerTenantBackend(StubBackend):
        async def engine_get(self, container: str, path: str) -> str | None:
            self.engine_calls.append((container, path))
            if container == "vt-t11-b":
                return sessions_payload(NOW_MS - 1_000)
            return sessions_payload(NOW_MS - 600_000)

    async def scenario() -> (
        tuple[list[dict[str, object]], list[str], list[tuple[str, str]]]
    ):
        backend = PerTenantBackend(state="running")
        async with stack(
            json_responder(),
            backend=backend,
            clock=frozen_clock,
            admin_token_sha256=token_sha256(ADMIN_KEY),
            idle_ttl_s=60.0,
        ) as rig:
            response = await rig.client.post(
                "/router-admin/reclaim",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            )
            return response.json(), backend.stopped, backend.engine_calls

    decisions, stopped, engine_calls = asyncio.run(scenario())

    by_tenant = {decision["tenant"]: decision for decision in decisions}
    # Tenant a's engine last updated 600s ago (> 60s TTL) -> reclaimed.
    assert by_tenant["a"]["reclaim"] is True
    assert by_tenant["a"]["truth_source"] == "engine"
    assert by_tenant["a"]["idle_ms"] == 600_000
    assert by_tenant["a"]["drained"] is True
    # Tenant b's engine updated 1s ago -> kept, even though the proxy ledger is
    # empty for it (the truth check is what saves an SSE-only tenant).
    assert by_tenant["b"]["reclaim"] is False
    assert by_tenant["b"]["idle_ms"] == 1_000
    assert stopped == ["vt-t11-a"]
    assert engine_calls == [
        ("vt-t11-a", "/session?limit=1&roots=true"),
        ("vt-t11-b", "/session?limit=1&roots=true"),
    ]


def test_admin_state_reports_the_routing_table_and_inflight_counts() -> None:
    async def scenario() -> dict[str, object]:
        async with stack(
            json_responder(), admin_token_sha256=token_sha256(ADMIN_KEY)
        ) as rig:
            response = await rig.client.get(
                "/router-admin/state", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
            return response.json()

    state = asyncio.run(scenario())

    assert set(state["tenants"]) == {"a", "b"}
    assert state["tenants"]["a"]["public_host"] == "a.tenant.local"
    assert state["tenants"]["a"]["upstream"] == "http://tenant-a.internal:8080"
    assert state["tenants"]["a"]["inflight"] == 0
    assert state["tenants"]["a"]["disposed"] is False
    assert state["activity_ms"] == {}
    # No token material of any kind leaves the admin surface.
    assert "token" not in json.dumps(state)


def test_admin_state_records_proxy_activity_after_a_request() -> None:
    async def scenario() -> dict[str, int]:
        async with stack(
            json_responder(),
            clock=frozen_clock,
            admin_token_sha256=token_sha256(ADMIN_KEY),
        ) as rig:
            await rig.client.get("/sessions", headers=rig.auth("a"))
            response = await rig.client.get(
                "/router-admin/state", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
            return response.json()["activity_ms"]

    assert asyncio.run(scenario()) == {"a": NOW_MS}
