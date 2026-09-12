"""Typed transport errors for the opencode bridge.

Every failure the driver surfaces is one of these — callers (T5 service,
T6 recovery) match on type, never on message strings.
"""

from __future__ import annotations

__all__ = [
    "OpencodeBridgeError",
    "OpencodeConnectionError",
    "OpencodeHttpError",
    "OpencodeResponseShapeError",
    "StreamDisconnected",
]


class OpencodeBridgeError(Exception):
    """Base error for the opencode bridge transport layer."""


class OpencodeConnectionError(OpencodeBridgeError):
    """The opencode serve is unreachable (refused / DNS / timeout).

    Raised loudly by the REST primitives — a missing serve must never be
    silent (plan T3 QA failure scenario).
    """


class OpencodeHttpError(OpencodeBridgeError):
    """The serve answered with a non-2xx status."""

    def __init__(
        self, method: str, path: str, status_code: int, body_excerpt: str
    ) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        super().__init__(f"{method} {path} -> HTTP {status_code}: {body_excerpt}")


class OpencodeResponseShapeError(OpencodeBridgeError):
    """The serve answered 2xx with a body the bridge cannot use."""


class StreamDisconnected(Exception):
    """Internal marker: the SSE stream failed in a reconnectable way."""
