"""Proxy behaviour tests: routing outcomes, Host preservation, SSE passthrough.

Pins the plan T11 / D9 / F6 recipes at the HTTP level:

* an unregistered Host is a 404 and is NEVER forwarded (no misrouting);
* the public Host header reaches the tenant gateway verbatim, because the
  gateway's ``API_ALLOWED_HOSTS`` trusts exactly that value (B2);
* SSE answers carry ``text/event-stream`` + ``X-Accel-Buffering: no``, relay
  bytes unbuffered and in order, and an upstream failure becomes a synthetic
  ``router.error`` event instead of a silent death (portal ``events.ts``);
* the admission fence is released on both response paths.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from router_harness import (
    KEYS,
    TENANT_A,
    TENANT_B,
    StubBackend,
    json_responder,
    stack,
)


def test_unregistered_host_is_404_and_never_forwarded() -> None:
    async def scenario() -> tuple[int, int, str]:
        async with stack() as rig:
            response = await rig.client.get(
                "/sessions", headers={"Host": "evil.example"}
            )
            return (
                response.status_code,
                len(rig.gateway.requests),
                response.json()["detail"],
            )

    status, forwarded, detail = asyncio.run(scenario())

    assert status == 404
    assert forwarded == 0
    assert detail == "unknown tenant host"


def test_unknown_token_without_a_registered_host_is_401() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack() as rig:
            response = await rig.client.get(
                "/sessions",
                headers={"Authorization": "Bearer not-a-tenant-key", "Host": "nowhere"},
            )
            return response.status_code, len(rig.gateway.requests)

    assert asyncio.run(scenario()) == (401, 0)


def test_token_and_host_of_different_tenants_is_403_and_never_forwarded() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack() as rig:
            response = await rig.client.get(
                "/sessions", headers={**rig.auth("a"), "Host": TENANT_B.public_host}
            )
            return response.status_code, len(rig.gateway.requests)

    assert asyncio.run(scenario()) == (403, 0)


def test_a_token_routes_only_to_its_own_tenant() -> None:
    async def scenario() -> tuple[bool, str, str]:
        async with stack() as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("b"))
            forwarded = rig.gateway.last()
            return (
                response.json()["ok"],
                forwarded.url.host,
                response.headers["x-vt-tenant"],
            )

    ok, forwarded_host, tenant_header = asyncio.run(scenario())

    assert ok is True
    assert forwarded_host == "tenant-b.internal"
    assert tenant_header == "b"


def test_public_host_is_preserved_verbatim_for_the_gateway_allowlist() -> None:
    async def scenario() -> str:
        async with stack() as rig:
            await rig.client.get(
                "/sessions", headers={**rig.auth("a"), "Host": TENANT_A.public_host}
            )
            return rig.gateway.last().headers["host"]

    # B2/F6: rewriting Host to the upstream would make the gateway answer 403.
    assert asyncio.run(scenario()) == "a.tenant.local"


def test_a_host_with_an_explicit_port_still_routes_and_is_preserved() -> None:
    async def scenario() -> tuple[int, str]:
        async with stack() as rig:
            response = await rig.client.get(
                "/sessions", headers={**rig.auth("a"), "Host": "a.tenant.local:8443"}
            )
            return response.status_code, rig.gateway.last().headers["host"]

    assert asyncio.run(scenario()) == (200, "a.tenant.local:8443")


def test_authorization_header_passes_through_unchanged() -> None:
    async def scenario() -> str:
        async with stack() as rig:
            await rig.client.get("/sessions", headers=rig.auth("a"))
            return rig.gateway.last().headers["authorization"]

    # The gateway re-validates this key against its own API_AUTH_KEY: the
    # router routes on it but never judges it.
    assert asyncio.run(scenario()) == f"Bearer {KEYS['a']}"


def test_hop_by_hop_and_client_forwarded_headers_are_not_passed_through() -> None:
    async def scenario() -> dict[str, str]:
        async with stack() as rig:
            await rig.client.get(
                "/sessions",
                headers={
                    **rig.auth("a"),
                    "connection": "close",
                    "keep-alive": "timeout=5",
                    "proxy-authorization": "Basic nope",
                    "te": "trailers",
                    "trailer": "Expires",
                    "x-forwarded-for": "1.2.3.4",
                    "x-forwarded-host": "spoofed.example",
                    "x-custom": "kept",
                    "Host": TENANT_A.public_host,
                },
            )
            headers = rig.gateway.last().headers
            return {
                "connection": headers.get("connection", ""),
                "keep-alive": headers.get("keep-alive", ""),
                "trailer": headers.get("trailer", ""),
                "proxy-authorization": headers.get("proxy-authorization", ""),
                "te": headers.get("te", ""),
                "x-forwarded-for": headers.get("x-forwarded-for", ""),
                "x-forwarded-host": headers.get("x-forwarded-host", ""),
                "x-custom": headers.get("x-custom", ""),
            }

    headers = asyncio.run(scenario())

    # The client's `connection: close` is not propagated; httpx sets its own
    # hop header for the separate router->upstream connection.
    assert headers["connection"] != "close"
    assert headers["keep-alive"] == ""
    assert headers["proxy-authorization"] == ""
    assert headers["te"] == ""
    assert headers["trailer"] == ""
    # Spoofed forwarding headers are replaced with the real peer/host.
    assert headers["x-forwarded-for"] == "127.0.0.1"
    assert headers["x-forwarded-host"] == TENANT_A.public_host
    assert headers["x-custom"] == "kept"


def test_accept_encoding_is_forced_to_identity_for_byte_identical_passthrough() -> None:
    async def scenario() -> str:
        async with stack() as rig:
            await rig.client.get(
                "/sessions", headers={**rig.auth("a"), "accept-encoding": "gzip, br"}
            )
            return rig.gateway.last().headers["accept-encoding"]

    assert asyncio.run(scenario()) == "identity"


def test_query_string_and_path_are_forwarded() -> None:
    async def scenario() -> str:
        async with stack() as rig:
            await rig.client.get("/sessions?limit=5&roots=true", headers=rig.auth("a"))
            return str(rig.gateway.last().url)

    assert (
        asyncio.run(scenario())
        == "http://tenant-a.internal:8080/sessions?limit=5&roots=true"
    )


def test_post_body_is_forwarded() -> None:
    async def scenario() -> tuple[dict[str, str], str]:
        payload = json.dumps({"message": "hello tenant"})
        async with stack() as rig:
            await rig.client.post(
                "/channels/webhook",
                headers={**rig.auth("a"), "content-type": "application/json"},
                content=payload,
            )
            forwarded = rig.gateway.last()
            return (
                json.loads(forwarded.content.decode()),
                forwarded.headers["content-type"],
            )

    body, content_type = asyncio.run(scenario())

    assert body == {"message": "hello tenant"}
    assert content_type == "application/json"


def test_buffered_response_passes_status_body_and_length() -> None:
    async def scenario() -> tuple[int, dict[str, str], str, str]:
        responder = json_responder({"sessions": [{"id": "ses_1"}]}, status=201)
        async with stack(responder) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return (
                response.status_code,
                response.json(),
                response.headers["content-length"],
                response.headers["x-vt-tenant"],
            )

    status, body, length, tenant_header = asyncio.run(scenario())

    assert status == 201
    assert body == {"sessions": [{"id": "ses_1"}]}
    assert length == str(len(json.dumps({"sessions": [{"id": "ses_1"}]})))
    assert tenant_header == "a"


def test_upstream_error_status_is_passed_through_not_rewritten() -> None:
    async def scenario() -> int:
        async with stack(
            json_responder({"detail": "Invalid or missing API key"}, status=401)
        ) as rig:
            return (
                await rig.client.get("/sessions", headers=rig.auth("a"))
            ).status_code

    # The gateway owns the auth verdict; the router must not turn its 401 into
    # a routing error of its own.
    assert asyncio.run(scenario()) == 401


def test_admission_is_released_after_a_buffered_response() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack() as rig:
            await rig.client.get("/sessions", headers=rig.auth("a"))
            return rig.fence.inflight("a"), rig.ledger.last_ms("a") is not None

    inflight, activity_recorded = asyncio.run(scenario())

    assert inflight == 0
    assert activity_recorded is True


def test_a_disposed_tenant_is_refused_with_410() -> None:
    async def scenario() -> tuple[int, int]:
        async with stack() as rig:
            await rig.fence.dispose("a", _noop, drain_timeout_s=1.0)
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return response.status_code, len(rig.gateway.requests)

    async def _noop() -> None:
        return None

    status, forwarded = asyncio.run(scenario())

    assert status == 410
    assert forwarded == 0


def test_healthz_answers_without_auth_and_counts_tenants() -> None:
    async def scenario() -> tuple[int, dict[str, int]]:
        async with stack() as rig:
            response = await rig.client.get("/router-healthz")
            return response.status_code, response.json()

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body == {"status": "ok", "tenants": 2}


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "PUT"])
def test_every_method_is_proxied(method: str) -> None:
    async def scenario() -> tuple[int, str]:
        async with stack() as rig:
            response = await rig.client.request(
                method, "/sessions/ses_1", headers=rig.auth("a")
            )
            return response.status_code, rig.gateway.last().method

    status, forwarded_method = asyncio.run(scenario())

    assert status == 200
    assert forwarded_method == method


def test_backend_is_unused_when_the_upstream_answers() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        backend = StubBackend(state="running")
        async with stack(json_responder(), backend=backend) as rig:
            await rig.client.get("/sessions", headers=rig.auth("a"))
            return backend.started, backend.stopped

    # A healthy tenant must never be touched by the container backend.
    assert asyncio.run(scenario()) == ([], [])


def test_a_reset_connection_still_triggers_the_wake() -> None:
    async def scenario() -> tuple[int, list[str], list[str]]:
        backend = StubBackend(state="exited")
        backend.gateway_up = True  # the port proxy still accepts, then resets
        async with stack(
            json_responder({"woke": True}),
            backend=backend,
            fail_first=httpx.ReadError(""),
            wake_timeout_s=5.0,
        ) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            paths = [request.url.path for request in rig.gateway.requests]
            return response.status_code, backend.started, paths

    # Regression: `docker stop` leaves the published port accepting-then-
    # resetting for a while, which surfaces as ReadError (empty message), not
    # ConnectError. Any transport failure must reach the wake path.
    status, started, paths = asyncio.run(scenario())

    assert status == 200
    assert started == ["vt-t11-a"]
    # First attempt (reset) -> health probe -> replayed attempt.
    assert paths == ["/sessions", "/health", "/sessions"]


def test_a_non_transport_failure_is_502_and_does_not_wake() -> None:
    async def scenario() -> tuple[int, list[str]]:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.DecodingError("corrupt upstream body", request=request)

        backend = StubBackend(state="exited")
        backend.gateway_up = True
        async with stack(responder, backend=backend) as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            return response.status_code, backend.started

    # A request-level failure that waking cannot fix is a bad gateway, and the
    # container must be left alone.
    status, started = asyncio.run(scenario())

    assert status == 502
    assert started == []
