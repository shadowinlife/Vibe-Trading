"""SSE passthrough tests: the portal ``events.ts`` recipe at the HTTP level.

Split from ``test_router_proxy.py`` so each file stays reviewable. Pinned here:
the stream headers (``text/event-stream`` + ``X-Accel-Buffering: no`` +
no-transform cache control + no content-length), byte-for-byte in-order relay,
a synthetic ``router.error`` frame on upstream failure instead of a silent
death, and admission release when the stream ends.

``ASGITransport`` buffers, so *first-byte latency* is asserted by the compose
E2E (``deploy/e2e_multi_tenant.py``), not here.
"""

from __future__ import annotations

import asyncio
import json

from router_harness import sse_frames, sse_responder, stack


def test_sse_headers_disable_buffering_and_drop_content_length() -> None:
    async def scenario() -> dict[str, str]:
        responder = sse_responder([b"event: session.idle\ndata: {}\n\n"])
        async with stack(responder) as rig:
            response = await rig.client.get("/events", headers=rig.auth("a"))
            return dict(response.headers)

    headers = asyncio.run(scenario())

    assert headers["content-type"] == "text/event-stream"
    assert headers["x-accel-buffering"] == "no"
    assert headers["cache-control"] == "no-cache, no-transform"
    assert "content-length" not in headers


def test_sse_body_is_relayed_byte_for_byte_in_order() -> None:
    async def scenario() -> tuple[bytes, list[str]]:
        chunks = [
            b'event: text_delta\ndata: {"delta":"he"}\n\n',
            b'event: text_delta\ndata: {"delta":"llo"}\n\n',
            b'event: attempt.completed\ndata: {"status":"completed"}\n\n',
        ]
        async with stack(sse_responder(chunks)) as rig:
            response = await rig.client.get("/events", headers=rig.auth("a"))
            return response.content, sse_frames(response.content)

    body, frames = asyncio.run(scenario())

    assert body == b"".join(
        [
            b'event: text_delta\ndata: {"delta":"he"}\n\n',
            b'event: text_delta\ndata: {"delta":"llo"}\n\n',
            b'event: attempt.completed\ndata: {"status":"completed"}\n\n',
        ]
    )
    assert frames == ["text_delta", "text_delta", "attempt.completed"]


def test_sse_upstream_failure_injects_a_synthetic_error_event() -> None:
    async def scenario() -> tuple[list[str], dict[str, str], int]:
        chunks = [
            b'event: text_delta\ndata: {"delta":"partial"}\n\n',
            b"never sent",
            b"nor this",
        ]
        async with stack(sse_responder(chunks, fail_after=1)) as rig:
            response = await rig.client.get("/events", headers=rig.auth("a"))
            payload = [
                json.loads(line[6:])
                for line in response.content.decode().splitlines()
                if line.startswith("data:") and "router.error" in line
            ]
            return sse_frames(response.content), payload[0], response.status_code

    frames, error, status = asyncio.run(scenario())

    # The partial turn is delivered, then the failure is announced — never a
    # silent close that leaves the client waiting forever.
    assert frames == ["text_delta", "router.error"]
    assert error["type"] == "router.error"
    assert error["tenant"] == "a"
    assert error["reason"] == "ReadError"
    assert status == 200


def test_admission_is_released_after_an_sse_stream_ends() -> None:
    async def scenario() -> int:
        async with stack(sse_responder([b"event: session.idle\ndata: {}\n\n"])) as rig:
            await rig.client.get("/events", headers=rig.auth("a"))
            return rig.fence.inflight("a")

    assert asyncio.run(scenario()) == 0
