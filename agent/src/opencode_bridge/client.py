"""httpx plumbing shared by the opencode bridge driver.

Owns the lazy ``httpx.AsyncClient`` (Basic Auth from config), the typed
connection-error envelope for every REST call (plan T3 QA: an unreachable
serve must surface as a clear ``OpencodeConnectionError``, never silently),
and the transport-tuning defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import (
    OpencodeConnectionError,
    OpencodeHttpError,
    OpencodeResponseShapeError,
)

logger = logging.getLogger("opencode_bridge")

__all__ = ["DriverSettings", "OpencodeHttpClient"]


@dataclass(frozen=True, slots=True)
class DriverSettings:
    """Transport tuning for the opencode bridge.

    Attributes:
        request_timeout_s: Overall timeout for one REST primitive.
        connect_timeout_s: TCP connect timeout.
        stream_read_timeout_s: Read timeout on the SSE stream. opencode
            emits ``server.heartbeat`` every 10 s (spike report §7g; the T1
            traces' max observed inter-frame gap is 10.01 s), so 20 s
            (2x cadence) without ANY byte means the connection is dead and
            must cycle. Sized >= ``liveness_silence_window_s`` so the first
            silent read-timeout cycle (~20 s without a byte) trips the
            window check, bounding frozen-serve detection at ~20 s — inside
            the T8 <30 s engine-death budget.
        reconnect_initial_backoff_s: Backoff floor after a disconnect.
        reconnect_max_backoff_s: Backoff ceiling (plan T3: 500 ms -> 30 s).
        liveness_max_silent_cycles: Consecutive /event connection cycles
            that may complete without delivering ANY frame before the
            stream declares the engine dead (``EnginePresumedDeadError``,
            T8-1). With the default backoff floor, a SIGKILLed serve (EOF +
            connection-refused) is detected in ~3.5 s.
        liveness_silence_window_s: Wall-clock window without ANY received
            frame after which a cycle end declares the engine dead. Must
            stay > the 10 s heartbeat cadence (no false positives on a
            healthy stream — scenario g proves heartbeats bridge a 120.75 s
            content silence) and <= ``stream_read_timeout_s`` so the first
            silent read-timeout cycle trips it.
    """

    request_timeout_s: float = 30.0
    connect_timeout_s: float = 10.0
    stream_read_timeout_s: float = 20.0
    reconnect_initial_backoff_s: float = 0.5
    reconnect_max_backoff_s: float = 30.0
    liveness_max_silent_cycles: int = 4
    liveness_silence_window_s: float = 15.0


class OpencodeHttpClient:
    """One opencode serve's HTTP surface: auth, lazy client, error envelope."""

    def __init__(
        self,
        base_url: str,
        password: str = "",
        *,
        username: str = "opencode",
        settings: DriverSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind a client to one opencode serve.

        Args:
            base_url: Serve root, e.g. ``http://127.0.0.1:4096``.
            password: ``OPENCODE_SERVER_PASSWORD`` value; empty means the
                serve is unsecured (loopback dev) and no auth header is sent.
            username: Basic-auth username (opencode's default: ``opencode``).
            settings: Transport tuning overrides.
            transport: httpx transport injection seam (tests).
        """
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._username = username
        self._settings = settings or DriverSettings()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        """The serve root this client talks to."""
        return self._base_url

    @property
    def settings(self) -> DriverSettings:
        """The effective transport tuning."""
        return self._settings

    def http(self) -> httpx.AsyncClient:
        """Return the lazily created ``httpx.AsyncClient``."""
        if self._client is None:
            settings = self._settings
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=(
                    httpx.BasicAuth(self._username, self._password)
                    if self._password
                    else None
                ),
                timeout=httpx.Timeout(
                    settings.request_timeout_s, connect=settings.connect_timeout_s
                ),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` if one was created."""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def request(
        self, method: str, path: str, *, json_body: Any | None = None
    ) -> httpx.Response:
        """One REST call wrapped in the connection-error envelope.

        Raises:
            OpencodeConnectionError: The serve is unreachable (connect
                refused / DNS / timeout / mid-response protocol error).
        """
        try:
            return await self.http().request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            raise OpencodeConnectionError(
                f"opencode serve at {self._base_url} unreachable "
                f"({method} {path}): {exc}"
            ) from exc

    async def request_json(
        self, method: str, path: str, *, json_body: Any | None = None
    ) -> Any:
        """One REST call; enforce 2xx and decode the JSON body.

        Returns:
            The decoded body, or ``None`` for an empty body (HTTP 204).

        Raises:
            OpencodeConnectionError: The serve is unreachable.
            OpencodeHttpError: The serve returned a non-2xx status.
            OpencodeResponseShapeError: A 2xx body is not valid JSON.
        """
        response = await self.request(method, path, json_body=json_body)
        if not 200 <= response.status_code < 300:
            raise OpencodeHttpError(
                method, path, response.status_code, response.text[:200]
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise OpencodeResponseShapeError(f"{method} {path}: non-JSON body") from exc
