"""T19-B1 regression: wake-on-inbound for gateway shapes that ANSWER asleep.

The kruise data-plane gateway (e2b direct-serve form) breaks the docker-form
wake composition in two layers, both measured live in
``.omo/evidence/task-19-integration-e2e.json``:

* **L1** — a paused/absent sandbox answers with an ordinary HTTP 502
  ("API Error: healthy sandbox … not found") instead of producing no response,
  so the TransportError-only trigger never fires. ``wake_on_statuses`` widens
  the trigger; the empty default keeps the docker form byte-identical.
* **L2** — the readiness probe polls ``/health`` UNAUTHENTICATED and required
  status<400, but serve (PID1, pool basic auth) 401s every route, so the probe
  can never go green. ``health_accept_unauthorized`` counts 401 as ready on
  that form (a 401 can only originate from serve — gateway route errors are
  502/404/503); the default keeps the strict docker contract.

Also pins the two env-boundary parsers (comma list, boolean; malformed fails
fast) and the composed e2b recipe end to end through the real ASGI app.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from router.config import RouterSettings
from router.wake import make_http_probe
from router_harness import Responder, StubBackend, stack

PAUSED_BODY = "API Error: healthy sandbox default--opencode-poc-x7q2m not found"


def sleepy_gateway(
    *, woke_after: int = 1, health_status: int = 200
) -> tuple[Responder, dict[str, object]]:
    """Kruise-shape gateway: 502 until the sandbox woke, then serves.

    ``health_status`` stands in for the serve-auth matrix: the direct-serve
    form answers even /health with 401 when unauthenticated.
    """
    seen: dict[str, object] = {"business": 0, "health": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            seen["health"] = int(str(seen["health"])) + 1
            return httpx.Response(health_status, json={"status": "auth required"})
        seen["business"] = int(str(seen["business"])) + 1
        if int(str(seen["business"])) <= woke_after:
            return httpx.Response(502, text=PAUSED_BODY)
        return httpx.Response(
            201, json={"id": "ses_1", "title": json.loads(request.content)["title"]}
        )

    return respond, seen


def paused_backend() -> StubBackend:
    """Stopped sandbox whose gateway route still ANSWERS (with 502)."""
    backend = StubBackend(state="exited")
    backend.gateway_up = True
    return backend


# --- L1: the response-status trigger ----------------------------------------


def test_status_trigger_wakes_and_replays_the_post_body() -> None:
    async def scenario() -> dict[str, object]:
        responder, seen = sleepy_gateway()
        backend = paused_backend()
        payload = json.dumps({"title": "t19b-wake-replay"}).encode()
        async with stack(
            responder,
            backend=backend,
            wake_on_statuses=frozenset({502}),
            wake_timeout_s=5.0,
        ) as rig:
            response = await rig.client.post(
                "/session",
                headers={**rig.auth("a"), "content-type": "application/json"},
                content=payload,
            )
            attempts = [r for r in rig.gateway.requests if r.url.path == "/session"]
            return {
                "status": response.status_code,
                "body": response.json(),
                "started": backend.started,
                "attempts": len(attempts),
                "first_body": attempts[0].content,
                "replay_body": attempts[-1].content,
                "health_probed": seen["health"],
                "paths": [r.url.path for r in rig.gateway.requests],
            }

    result = asyncio.run(scenario())

    assert result["status"] == 201
    assert result["body"] == {"id": "ses_1", "title": "t19b-wake-replay"}
    assert result["started"] == ["vt-t11-a"]
    assert result["attempts"] == 2
    assert result["health_probed"] >= 1
    # 502 attempt -> health probe -> replayed attempt, body intact.
    assert result["paths"] == ["/session", "/health", "/session"]
    assert result["first_body"] == result["replay_body"]


def test_default_config_passes_502_through_untouched() -> None:
    async def scenario() -> tuple[int, str, list[str], int]:
        responder, _ = sleepy_gateway()
        backend = paused_backend()
        async with stack(responder, backend=backend) as rig:
            response = await rig.client.get("/session", headers=rig.auth("a"))
            return (
                response.status_code,
                response.text,
                backend.started,
                len(rig.gateway.requests),
            )

    # Byte-identical docker-form default: the configured set is EMPTY, so a
    # 502 response is an ordinary upstream answer — passed through, never woken.
    status, body, started, requests = asyncio.run(scenario())

    assert status == 502
    assert body == PAUSED_BODY
    assert started == []
    assert requests == 1


def test_status_not_in_the_list_passes_through() -> None:
    async def scenario() -> tuple[int, list[str]]:
        responder, _ = sleepy_gateway()
        backend = paused_backend()
        async with stack(
            responder, backend=backend, wake_on_statuses=frozenset({503})
        ) as rig:
            response = await rig.client.get("/session", headers=rig.auth("a"))
            return response.status_code, backend.started

    assert asyncio.run(scenario()) == (502, [])


def test_status_trigger_with_wake_timeout_serves_the_fallback_page() -> None:
    async def scenario() -> tuple[int, str, str, list[str]]:
        responder, _ = sleepy_gateway(woke_after=10**6, health_status=401)
        backend = paused_backend()
        async with stack(
            responder,
            backend=backend,
            wake_on_statuses=frozenset({502}),
            wake_timeout_s=0.05,
            wake_poll_interval_s=0.01,
        ) as rig:
            response = await rig.client.get("/session", headers=rig.auth("a"))
            return (
                response.status_code,
                response.headers["retry-after"],
                response.text,
                backend.started,
            )

    status, retry_after, page, started = asyncio.run(scenario())

    assert status == 503
    assert retry_after == "30"
    assert "Your workspace is starting" in page
    assert started == ["vt-t11-a"]


# --- L2: the 401-tolerant probe ----------------------------------------------


def probe_verdict(status: int, *, accept_unauthorized: bool) -> bool:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))

    async def run() -> bool:
        async with httpx.AsyncClient(transport=transport) as client:
            probe = make_http_probe(
                client, health_accept_unauthorized=accept_unauthorized
            )
            return await probe("http://tenant.internal/health")

    return asyncio.run(run())


@pytest.mark.parametrize("accept", [False, True])
@pytest.mark.parametrize(("status", "expected"), [(200, True), (503, False)])
def test_probe_healthy_verdicts_are_unchanged_by_the_knob(
    status: int, expected: bool, accept: bool
) -> None:
    assert probe_verdict(status, accept_unauthorized=accept) is expected


def test_probe_rejects_401_by_default_keeps_docker_contract() -> None:
    assert probe_verdict(401, accept_unauthorized=False) is False


def test_probe_accepts_401_only_with_the_knob_on() -> None:
    assert probe_verdict(401, accept_unauthorized=True) is True


def test_composed_e2b_recipe_wakes_through_an_authenticated_only_serve() -> None:
    """Both knobs together: 502 trigger + 401-everywhere serve = working wake."""

    async def scenario() -> dict[str, object]:
        responder, seen = sleepy_gateway(health_status=401)
        backend = paused_backend()
        payload = json.dumps({"title": "e2b-composed"}).encode()
        async with stack(
            responder,
            backend=backend,
            wake_on_statuses=frozenset({502}),
            health_accept_unauthorized=True,
            wake_timeout_s=5.0,
        ) as rig:
            response = await rig.client.post(
                "/session",
                headers={**rig.auth("a"), "content-type": "application/json"},
                content=payload,
            )
            return {
                "status": response.status_code,
                "title": response.json()["title"],
                "started": backend.started,
                "health_probed": seen["health"],
            }

    result = asyncio.run(scenario())

    assert result["status"] == 201
    assert result["title"] == "e2b-composed"
    assert result["started"] == ["vt-t11-a"]
    assert result["health_probed"] >= 1


# --- env-boundary parsing -----------------------------------------------------


def settings_from(env: dict[str, str]) -> RouterSettings:
    return RouterSettings.from_env({"VT_ROUTER_REGISTRY": "r.json", **env})


def test_wake_on_statuses_default_is_empty_and_typed() -> None:
    settings = settings_from({})
    assert settings.wake_on_statuses == frozenset()
    assert settings.health_accept_unauthorized is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("502", frozenset({502})),
        ("502,504,404", frozenset({502, 504, 404})),
        (" 502 , 503 ", frozenset({502, 503})),
        ("502,,", frozenset({502})),
        ("", frozenset()),
        ("   ", frozenset()),
    ],
)
def test_wake_on_statuses_comma_list_parsing(
    raw: str, expected: frozenset[int]
) -> None:
    assert settings_from({"VT_ROUTER_WAKE_ON_STATUSES": raw}).wake_on_statuses == (
        expected
    )


def test_wake_on_statuses_malformed_fails_fast() -> None:
    with pytest.raises(ValueError, match="VT_ROUTER_WAKE_ON_STATUSES"):
        settings_from({"VT_ROUTER_WAKE_ON_STATUSES": "502,abc"})


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_health_accept_unauthorized_truthy_spellings(raw: str) -> None:
    assert (
        settings_from(
            {"VT_ROUTER_HEALTH_ACCEPT_UNAUTHORIZED": raw}
        ).health_accept_unauthorized
        is True
    )


@pytest.mark.parametrize("raw", ["false", "0", "off", "", "  "])
def test_health_accept_unauthorized_falsy_spellings(raw: str) -> None:
    assert (
        settings_from(
            {"VT_ROUTER_HEALTH_ACCEPT_UNAUTHORIZED": raw}
        ).health_accept_unauthorized
        is False
    )


def test_health_accept_unauthorized_malformed_fails_fast() -> None:
    with pytest.raises(ValueError, match="VT_ROUTER_HEALTH_ACCEPT_UNAUTHORIZED"):
        settings_from({"VT_ROUTER_HEALTH_ACCEPT_UNAUTHORIZED": "maybe"})
