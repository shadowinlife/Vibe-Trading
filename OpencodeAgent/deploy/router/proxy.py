"""Request forwarding: the proxy hop, the SSE relay, and the wake retry.

The SSE relay copies the portal ``events.ts`` recipe in semantics:

* bytes are relayed as they arrive (``aiter_raw``, no buffering, no decoding);
* the client's disconnect becomes an upstream cancel — closing the upstream
  response in the ``finally`` tears the connection down instead of leaving the
  tenant streaming into a void;
* an upstream failure mid-stream is announced by a synthetic ``router.error``
  frame rather than a silent close;
* retry ownership stays with the proxy/client contract: an established stream
  is NEVER silently reconnected (that would duplicate events). The only retry
  the router performs is the connection-establishment retry after a wake.

Admission ownership: ``forward`` releases the fence admission on every
non-streaming exit path, and transfers it to the SSE body iterator otherwise —
a stream outlives its handler, so the handler cannot release it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from .config import RouterSettings
from .fence import Admission
from .headers import (
    buffered_response_headers,
    forward_headers,
    is_sse,
    sse_response_headers,
    synthetic_error_event,
    upstream_url,
)
from .registry import Tenant
from .wake import Awake, WakeOutcome, WakeTimedOut, render_fallback_page

LOGGER = logging.getLogger("vt.router")

WAKE_RETRY_AFTER_S = 30
WakeFn = Callable[[Tenant], Awaitable[WakeOutcome]]


@dataclass(frozen=True, slots=True)
class UpstreamRequest:
    """One forwarded request, replayable across the post-wake retry."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


class Forwarder:
    """Sends admitted requests upstream and shapes the response."""

    __slots__ = ("_client", "_settings", "_wake")

    def __init__(
        self, client: httpx.AsyncClient, settings: RouterSettings, wake: WakeFn
    ) -> None:
        self._client = client
        self._settings = settings
        self._wake = wake

    async def forward(
        self, request: Request, tenant: Tenant, admission: Admission
    ) -> Response:
        """Forward one admitted request; the SSE path transfers the admission."""
        transferred = False
        upstream: httpx.Response | None = None
        try:
            spec = UpstreamRequest(
                method=request.method,
                url=upstream_url(tenant, request),
                headers=tuple(forward_headers(request)),
                # Buffered so the post-wake retry can replay it; a streamed
                # request body is gone after the first send.
                body=await request.body(),
            )
            try:
                upstream = await self._send(spec)
            except httpx.TransportError as first_error:
                # No response at all, so the tenant may be asleep. The trigger
                # is ANY transport failure, not just a connection refusal:
                # Docker's published-port proxy accepts-then-resets for a while
                # after `docker stop`, which surfaces as ReadError /
                # RemoteProtocolError with an empty message. The wake asks the
                # backend for the real container state, so a healthy tenant is
                # never restarted by mistake.
                outcome = await self._wake(tenant)
                if not isinstance(outcome, Awake):
                    return self._fallback_response(tenant, outcome)
                try:
                    upstream = await self._send(spec)
                except httpx.HTTPError as retry_error:
                    LOGGER.warning(
                        "upstream unreachable after wake tenant=%s error=%s:%s",
                        tenant.tenant_id,
                        type(retry_error).__name__,
                        retry_error,
                    )
                    return json_response(
                        502,
                        "upstream unreachable after wake",
                        {
                            "tenant": tenant.tenant_id,
                            "first_error": f"{type(first_error).__name__}: {first_error}",
                            "retry_error": f"{type(retry_error).__name__}: {retry_error}",
                        },
                    )
            except httpx.HTTPError as send_error:
                # A request-level failure (bad upstream URL, etc.) that waking
                # cannot fix: report it instead of letting it surface as a 500.
                LOGGER.warning(
                    "upstream request failed tenant=%s error=%s:%s",
                    tenant.tenant_id,
                    type(send_error).__name__,
                    send_error,
                )
                return json_response(
                    502,
                    "upstream request failed",
                    {
                        "tenant": tenant.tenant_id,
                        "error": f"{type(send_error).__name__}: {send_error}",
                    },
                )

            if is_sse(upstream.headers):
                transferred = True
                return StreamingResponse(
                    self._sse_body(upstream, admission, tenant.tenant_id),
                    status_code=upstream.status_code,
                    headers=sse_response_headers(upstream.headers, tenant.tenant_id),
                )
            payload = await upstream.aread()
            return Response(
                content=payload,
                status_code=upstream.status_code,
                headers=buffered_response_headers(upstream.headers, tenant.tenant_id),
            )
        finally:
            if not transferred:
                if upstream is not None:
                    with contextlib.suppress(httpx.HTTPError):
                        await upstream.aclose()
                await release(admission)

    async def _send(self, spec: UpstreamRequest) -> httpx.Response:
        built = self._client.build_request(
            spec.method,
            spec.url,
            headers=list(spec.headers),
            content=spec.body,
            timeout=_timeout(self._settings),
        )
        return await self._client.send(built, stream=True)

    def _fallback_response(self, tenant: Tenant, outcome: WakeOutcome) -> Response:
        elapsed = outcome.elapsed_s if isinstance(outcome, WakeTimedOut) else 0.0
        return Response(
            content=render_fallback_page(tenant.tenant_id, elapsed, WAKE_RETRY_AFTER_S),
            status_code=503,
            media_type="text/html; charset=utf-8",
            headers={
                "Retry-After": str(WAKE_RETRY_AFTER_S),
                "Cache-Control": "no-store",
                "x-vt-tenant": tenant.tenant_id,
            },
        )

    async def _sse_body(
        self, upstream: httpx.Response, admission: Admission, tenant_id: str
    ) -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected (uvicorn cancels the response task or closes
            # the generator): closing the upstream stream below IS the cancel
            # propagation (portal events.ts: cancel() -> abort.abort()).
            LOGGER.info("sse client disconnect tenant=%s", tenant_id)
            raise
        except httpx.HTTPError as exc:
            LOGGER.warning(
                "sse upstream failure tenant=%s error=%s:%s",
                tenant_id,
                type(exc).__name__,
                exc,
            )
            yield synthetic_error_event(tenant_id, exc)
        finally:
            await release(admission)
            # Shielded: a cancelled generator must still tear the stream down,
            # and the shield lets the close finish after the cancel lands.
            with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
                await asyncio.shield(upstream.aclose())


async def release(admission: Admission) -> None:
    """Release an admission even while the surrounding task is being cancelled."""
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.shield(admission.release())


def _timeout(settings: RouterSettings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.connect_timeout_s,
        read=settings.read_timeout_s or None,
        write=settings.write_timeout_s,
        pool=settings.connect_timeout_s,
    )


def json_response(
    status_code: int, detail: str, extra: dict[str, object] | None = None
) -> JSONResponse:
    """One uniform error envelope for every router-originated rejection."""
    return JSONResponse(
        status_code=status_code, content={"detail": detail, **(extra or {})}
    )
