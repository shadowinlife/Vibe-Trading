"""Typed transport errors for the opencode bridge.

Every failure the driver surfaces is one of these — callers (T5 service,
T6 recovery) match on type, never on message strings.
"""

from __future__ import annotations

__all__ = [
    "EnginePresumedDeadError",
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


class EnginePresumedDeadError(OpencodeBridgeError):
    """The event stream's bounded liveness budget declared the engine dead.

    Raised by :meth:`OpencodeDriver.events` (T8-1 fix) when reconnect
    cycles keep completing without ANY frame — the serve carries
    ``server.heartbeat`` bytes every ~10 s even inside a long-silent tool
    (T1 trace scenario g), so a bounded window of total byte silence and/or
    N consecutive frameless reconnect cycles is sound death evidence, while
    pure content silence is NOT. The service pump turns this into the
    existing no-hang path (``_fail_all_pending``): pending attempts land
    ``failed`` within the IM reply budget instead of hanging until the
    600 s polling timeout.
    """


class StreamDisconnected(Exception):
    """Internal marker: the SSE stream failed in a reconnectable way."""
