"""Header hygiene for the proxy hop (pure functions, no I/O).

Two rules dominate:

* **Hop-by-hop headers never cross a proxy** (RFC 9110) — they belong to one
  transport connection, so both directions strip them.
* **The public Host is preserved** (plan D9 / F6): the tenant gateway trusts
  the public host through ``API_ALLOWED_HOSTS`` and rejects anything else with
  403 (``security.py:_reject_untrusted_loopback_host``), so rewriting Host to
  the upstream would break every forwarded request.

``accept-encoding`` is forced to ``identity`` so the passthrough is
byte-identical on both the streamed (SSE) and the buffered response path — a
gzip'd upstream body would otherwise be relayed raw on one path and decoded on
the other.
"""

from __future__ import annotations

import json

import httpx
from starlette.datastructures import Headers
from starlette.requests import Request

from .registry import Tenant

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Inbound forwarding headers are stripped and re-set from the real peer, so a
# client cannot spoof its own chain into the tenant's access log.
STRIPPED_REQUEST_HEADERS = HOP_BY_HOP | {
    "host",
    "accept-encoding",
    "content-length",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
}
SSE_CONTENT_TYPE = "text/event-stream"
TENANT_HEADER = "x-vt-tenant"


def bearer_token(headers: Headers) -> str | None:
    """Return the Bearer credential, or ``None`` when absent/not a Bearer."""
    scheme, _, credential = headers.get("authorization", "").partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential:
        return None
    return credential


def upstream_url(tenant: Tenant, request: Request) -> str:
    """Join the tenant upstream with the request's path and query string."""
    query = request.url.query
    return f"{tenant.upstream}{request.url.path or '/'}" + (
        f"?{query}" if query else ""
    )


def forward_headers(request: Request) -> list[tuple[str, str]]:
    """Build the upstream request headers, preserving the public Host."""
    host = request.headers.get("host", "")
    client_ip = request.client.host if request.client else ""
    forwarded: list[tuple[str, str]] = []
    for raw_key, raw_value in request.headers.raw:
        key = raw_key.decode("latin-1")
        if key.lower() in STRIPPED_REQUEST_HEADERS:
            continue
        forwarded.append((key, raw_value.decode("latin-1")))
    forwarded.append(("host", host))
    forwarded.append(("accept-encoding", "identity"))
    forwarded.append(("x-forwarded-for", client_ip))
    forwarded.append(("x-forwarded-host", host))
    return forwarded


def is_sse(headers: httpx.Headers) -> bool:
    """Whether the upstream answered with a server-sent-events stream."""
    return headers.get("content-type", "").lower().startswith(SSE_CONTENT_TYPE)


def buffered_response_headers(headers: httpx.Headers, tenant_id: str) -> dict[str, str]:
    """Headers for a fully buffered upstream response (length preserved)."""
    out = _passthrough(headers, tenant_id)
    length = headers.get("content-length")
    if length:
        out["content-length"] = length
    return out


def sse_response_headers(headers: httpx.Headers, tenant_id: str) -> dict[str, str]:
    """SSE headers per the portal ``events.ts`` recipe: no buffering, no length."""
    out = _passthrough(headers, tenant_id)
    out.update(
        {
            "content-type": SSE_CONTENT_TYPE,
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",
        }
    )
    return out


def synthetic_error_event(tenant_id: str, exc: Exception) -> bytes:
    """One SSE frame announcing an upstream failure instead of a silent death."""
    payload = {
        "type": "router.error",
        "tenant": tenant_id,
        "reason": type(exc).__name__,
        "detail": str(exc)[:200],
    }
    return f"event: router.error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode(
        "utf-8"
    )


def _passthrough(headers: httpx.Headers, tenant_id: str) -> dict[str, str]:
    out = {
        key: value
        for key, value in headers.multi_items()
        if key.lower() not in HOP_BY_HOP and key.lower() != "content-length"
    }
    out[TENANT_HEADER] = tenant_id
    return out
