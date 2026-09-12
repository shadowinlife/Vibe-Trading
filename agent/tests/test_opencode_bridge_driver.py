"""Unit tests for the OpencodeDriver REST primitives (driver.py + client.py).

All HTTP goes through ``httpx.MockTransport`` — no live serve (live E2E is
T7). Pins: request paths/bodies of the legacy ``/session`` surface, the
Basic-Auth recipe, the typed error envelopes (connection vs HTTP vs shape),
the tool-mapping startup fetch with its degraded modes, and ``from_env``
wiring through the EnvConfig schema.
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from src.config.accessor import reset_env_config
from src.opencode_bridge.driver import OpencodeDriver
from src.opencode_bridge.errors import (
    OpencodeConnectionError,
    OpencodeHttpError,
    OpencodeResponseShapeError,
)

BASE = "http://serve.test"


def make_driver(handler, **kwargs) -> OpencodeDriver:
    return OpencodeDriver(BASE, transport=httpx.MockTransport(handler), **kwargs)


def test_create_session_returns_id_and_sends_title() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "ses_abc123", "slug": "x"})

    driver = make_driver(handler)
    session_id = asyncio.run(driver.create_session("spike-a"))
    assert session_id == "ses_abc123"
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/session"
    assert json.loads(seen[0].content) == {"title": "spike-a"}


def test_create_session_without_title_sends_empty_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "ses_x"})

    asyncio.run(make_driver(handler).create_session())
    assert json.loads(seen[0].content) == {}


def test_create_session_rejects_body_without_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"slug": "no-id-here"})

    with pytest.raises(OpencodeResponseShapeError):
        asyncio.run(make_driver(handler).create_session())


def test_prompt_async_sends_text_part_and_accepts_204() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    result = asyncio.run(make_driver(handler).prompt_async("ses_1", "hello"))
    assert result is None
    assert seen[0].url.path == "/session/ses_1/prompt_async"
    assert json.loads(seen[0].content) == {"parts": [{"type": "text", "text": "hello"}]}


def test_abort_posts_to_abort_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=True)

    asyncio.run(make_driver(handler).abort("ses_1"))
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/session/ses_1/abort"


def test_messages_passes_through_dict_entries_and_drops_others() -> None:
    payload = [{"info": {"id": "msg_1"}, "parts": []}, "junk", 42]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/session/ses_1/message"
        return httpx.Response(200, json=payload)

    entries = asyncio.run(make_driver(handler).messages("ses_1"))
    assert entries == [{"info": {"id": "msg_1"}, "parts": []}]


def test_messages_rejects_non_list_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"info": "not a list"})

    with pytest.raises(OpencodeResponseShapeError):
        asyncio.run(make_driver(handler).messages("ses_1"))


def test_non_2xx_raises_http_error_with_envelope_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(OpencodeHttpError) as excinfo:
        asyncio.run(make_driver(handler).create_session())
    assert excinfo.value.status_code == 500
    assert excinfo.value.path == "/session"
    assert excinfo.value.method == "POST"


def test_unreachable_serve_raises_connection_error_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(OpencodeConnectionError, match="unreachable"):
        asyncio.run(make_driver(handler).create_session())


def test_password_sets_basic_auth_header_with_default_username() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "ses_x"})

    asyncio.run(make_driver(handler, password="s3cret").create_session())
    expected = base64.b64encode(b"opencode:s3cret").decode()
    assert seen[0].headers["authorization"] == f"Basic {expected}"


def test_no_password_sends_no_auth_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "ses_x"})

    asyncio.run(make_driver(handler).create_session())
    assert "authorization" not in seen[0].headers


def test_load_tool_mapping_builds_table_from_both_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(200, json={"vibe-trading": {"status": "connected"}})
        if request.url.path == "/experimental/tool/ids":
            return httpx.Response(200, json=["bash", "vibe-trading_list_skills"])
        raise AssertionError(f"unexpected path {request.url.path}")

    driver = make_driver(handler)
    mapping = asyncio.run(driver.load_tool_mapping())
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"
    assert driver.bare_tool_name("vibe-trading_list_skills") == "list_skills"
    assert driver.tool_map is mapping


def test_load_tool_mapping_degrades_when_ids_endpoint_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(200, json={"vibe-trading": {"status": "connected"}})
        return httpx.Response(404, text="no such route")

    driver = make_driver(handler)
    mapping = asyncio.run(driver.load_tool_mapping())
    # Exact table lost, prefix stripping still resolves (version-drift mode).
    assert mapping.prefixed_to_bare == {}
    assert mapping.bare("vibe-trading_list_skills") == "list_skills"


def test_load_tool_mapping_propagates_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(OpencodeConnectionError):
        asyncio.run(make_driver(handler).load_tool_mapping())


def test_bare_tool_name_before_load_returns_name_unchanged() -> None:
    driver = make_driver(lambda request: httpx.Response(200, json={}))
    assert driver.bare_tool_name("vibe-trading_list_skills") == (
        "vibe-trading_list_skills"
    )


def test_from_env_wires_base_url_and_password(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:14096")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "env-secret")
    reset_env_config()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "ses_x"})

    try:
        driver = OpencodeDriver.from_env(transport=httpx.MockTransport(handler))
        assert driver.base_url == "http://127.0.0.1:14096"
        asyncio.run(driver.create_session())
        expected = base64.b64encode(b"opencode:env-secret").decode()
        assert seen[0].headers["authorization"] == f"Basic {expected}"
    finally:
        reset_env_config()


def test_aclose_closes_lazily_created_client() -> None:
    driver = make_driver(lambda request: httpx.Response(200, json={"id": "s"}))
    asyncio.run(driver.create_session())

    async def close_and_probe() -> bool:
        client = driver._http.http()
        await driver.aclose()
        return client.is_closed

    assert asyncio.run(close_and_probe()) is True
