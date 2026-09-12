"""opencode engine driver: the transport seam under the vt session bridge.

This module implements the two halves of work-plan T3 (D12):

* :class:`EngineDriver` — the thin four-primitive protocol
  (``create_session`` / ``prompt_async`` / ``abort`` / ``events``) that the
  bridge (T4 translator, T5 service) stands on. If the harness-evolution S3
  gate ever swaps the engine base, only the driver is replaced.
* :class:`OpencodeDriver` — the opencode implementation over the **legacy
  ``/session`` REST surface only** (D10; the ``/api/*`` preview surface is
  forbidden): ``POST /session``, ``POST /session/:id/prompt_async``,
  ``POST /session/:id/abort``, ``GET /session/:id/message``, ``GET /event``
  (SSE), plus ``GET /mcp`` and ``GET /experimental/tool/ids`` for the
  tool-name mapping table. ``/experimental/tool/ids`` is not part of the
  forbidden ``/api/*`` surface and is verified identically shaped on both
  pin candidates (1.18.18 / 1.18.30); if it disappears on a future pin the
  mapping degrades to prefix-stripping instead of failing (``tool_names``).

**Scope boundary**: the driver emits opencode-NATIVE events only
(:class:`~src.opencode_bridge.events.OpencodeEvent` = the parsed wire
payload, untouched). Translation to the vt SSE vocabulary (``text_delta`` /
``tool_call`` / attempt lifecycle / heartbeats / quiescence) is T4's
EventTranslator job — nothing here interprets event semantics. Unknown
event types pass through unharmed; undecodable frames are dropped without
killing the stream (version-drift defense, spike report §8: the translator
allowlists, the transport never filters).

**prompt_async -> subscribe timing (the plan-T3 documented choice):
SUBSCRIBE-FIRST.** The consumer (T5 service) begins iterating
:meth:`OpencodeDriver.events` once at startup — before any prompt is sent —
and the driver keeps that single connection alive across all sessions and
attempts (kimaki global-listener pattern). Rationale:

1. opencode-runtime's send-then-subscribe argument ("prompt_async returns
   on acceptance; deltas only start when the model emits") is true but
   racy for everything that precedes model output: ``session.status busy``,
   the user ``message.updated`` and early tool parts can all land between
   acceptance and a later subscription.
2. With the stream already up when ``prompt_async`` fires, the race is
   structurally impossible — no buffer and no dedup are needed in the
   driver, so the simpler of the two sanctioned options is also the
   lossless one.
3. Cost: one idle SSE connection per gateway, kept warm by opencode's
   ``server.heartbeat`` (10 s cadence, spike report §3/§7g).

Contract: exactly one consumer may iterate :meth:`OpencodeDriver.events`
per driver instance (a second concurrent iteration raises ``RuntimeError``);
fan-out to per-session translators is the consumer's job (T5).

**SSE robustness** (fixes opencode-runtime's no-backoff limitation, plan
§7.4 item 7): the event stream reconnects automatically with exponential
backoff 500 ms -> 30 s, reset to the floor on every received event. HTTP
401/403 on the stream fail fast instead of looping — an auth
misconfiguration cannot heal by retrying.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Protocol, runtime_checkable

import httpx

from src.config.accessor import get_env_config

from .client import DriverSettings, OpencodeHttpClient
from .errors import OpencodeHttpError, OpencodeResponseShapeError, StreamDisconnected
from .events import OpencodeEvent, decode_event
from .sse import SseFrameParser
from .tool_names import EMPTY_TOOL_NAME_MAP, ToolNameMap, build_tool_name_map

logger = logging.getLogger("opencode_bridge")

__all__ = ["DriverSettings", "EngineDriver", "OpencodeDriver"]

_SSE_HEADERS = {"Accept": "text/event-stream"}
_AUTH_FAILURE_STATUSES = frozenset({401, 403})


@runtime_checkable
class EngineDriver(Protocol):
    """Transport seam between the vt session bridge and an agent engine.

    Exactly four primitives (D12). Events are engine-native; semantic
    translation is the bridge's EventTranslator (T4), never the driver's.
    """

    async def create_session(self, title: str = "") -> str:
        """Create an engine session and return its engine-side id."""
        ...

    async def prompt_async(self, session_id: str, text: str) -> None:
        """Submit one user prompt; return once the engine accepted it."""
        ...

    async def abort(self, session_id: str) -> None:
        """Cancel the running attempt on *session_id*."""
        ...

    def events(self) -> AsyncIterator[OpencodeEvent]:
        """Yield the engine's native event stream (single consumer)."""
        ...


class OpencodeDriver:
    """:class:`EngineDriver` over the opencode legacy ``/session`` surface.

    One instance owns one persistent ``/event`` connection (kimaki
    global-listener pattern) and one lazily created ``httpx.AsyncClient``.
    See the module docstring for the subscribe-first contract and the
    reconnect policy.
    """

    def __init__(
        self,
        base_url: str,
        password: str = "",
        *,
        username: str = "opencode",
        settings: DriverSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind a driver to one opencode serve.

        Args:
            base_url: Serve root, e.g. ``http://127.0.0.1:4096``.
            password: ``OPENCODE_SERVER_PASSWORD`` value; empty means the
                serve is unsecured (loopback dev) and no auth header is sent.
            username: Basic-auth username (opencode's default: ``opencode``).
            settings: Transport tuning overrides (:class:`DriverSettings`).
            transport: httpx transport injection seam (tests).
        """
        self._http = OpencodeHttpClient(
            base_url,
            password,
            username=username,
            settings=settings,
            transport=transport,
        )
        self._tool_map = EMPTY_TOOL_NAME_MAP
        self._stream_active = False
        self._closed = False

    @classmethod
    def from_env(
        cls,
        *,
        settings: DriverSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OpencodeDriver:
        """Build a driver from the ``EnvConfig`` schema (no scattered getenv).

        Args:
            settings: Optional transport tuning overrides.
            transport: Optional httpx transport injection seam (tests).
        """
        config = get_env_config().opencode_bridge
        return cls(
            base_url=config.opencode_base_url,
            password=config.opencode_server_password,
            settings=settings,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        """The serve root this driver talks to."""
        return self._http.base_url

    async def aclose(self) -> None:
        """Close the HTTP client; an active event stream stops reconnecting."""
        self._closed = True
        await self._http.aclose()

    # -- EngineDriver primitives --------------------------------------------

    async def create_session(self, title: str = "") -> str:
        """Create an opencode session and return its ``ses_...`` id.

        Args:
            title: Optional session title (surfaces in opencode's own UI).

        Returns:
            The opencode session id.

        Raises:
            OpencodeConnectionError: The serve is unreachable.
            OpencodeHttpError: The serve returned a non-2xx status.
            OpencodeResponseShapeError: The body carries no session id.
        """
        body = await self._http.request_json(
            "POST", "/session", json_body={"title": title} if title else {}
        )
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            raise OpencodeResponseShapeError(
                "POST /session: response carries no session 'id'"
            )
        return body["id"]

    async def prompt_async(self, session_id: str, text: str) -> None:
        """Submit one user text prompt; returns on acceptance (HTTP 204).

        The run's progress arrives on the event stream — the consumer must
        already be iterating :meth:`events` (subscribe-first, module
        docstring). A prompt that fails *after* acceptance is reported by
        the serve as a ``session.error`` event, not by this method.

        Raises:
            OpencodeConnectionError: The serve is unreachable.
            OpencodeHttpError: Acceptance failed (e.g. unknown session).
        """
        await self._http.request_json(
            "POST",
            f"/session/{session_id}/prompt_async",
            json_body={"parts": [{"type": "text", "text": text}]},
        )

    async def abort(self, session_id: str) -> None:
        """Abort the running attempt; terminal events follow on the stream.

        Spike report §5b: abort produces ``session.error``
        (MessageAbortedError) plus a double ``session.idle`` — interpreting
        that sequence is T4's job; this method only delivers the request.
        """
        await self._http.request_json(
            "POST", f"/session/{session_id}/abort", json_body={}
        )

    async def events(self) -> AsyncIterator[OpencodeEvent]:
        """Yield opencode-native events from the single persistent SSE stream.

        Reconnects with exponential backoff (500 ms -> 30 s, reset on every
        received event) until the driver is closed or the consumer stops
        iterating. Undecodable frames and unknown event types never
        terminate the stream.

        Raises:
            RuntimeError: Another consumer is already iterating this
                driver's event stream (one connection per driver instance).
            OpencodeHttpError: The stream got HTTP 401/403 — an auth
                misconfiguration that retrying cannot fix.
        """
        if self._stream_active:
            raise RuntimeError(
                "events() already has an active consumer on this driver "
                "instance; one persistent /event connection per driver "
                "(kimaki global-listener pattern) — fan out from the single "
                "consumer instead"
            )
        self._stream_active = True
        backoff = self._http.settings.reconnect_initial_backoff_s
        try:
            while not self._closed:
                try:
                    async for event in self._read_stream():
                        backoff = self._http.settings.reconnect_initial_backoff_s
                        yield event
                except asyncio.CancelledError:
                    raise
                except OpencodeHttpError:
                    raise
                except (httpx.HTTPError, StreamDisconnected, RuntimeError) as exc:
                    if self._closed:
                        break
                    logger.warning(
                        "opencode /event stream failed (%s); " "reconnecting in %.2fs",
                        exc,
                        backoff,
                    )
                if self._closed:
                    break
                await asyncio.sleep(backoff)
                backoff = min(
                    backoff * 2.0, self._http.settings.reconnect_max_backoff_s
                )
        finally:
            self._stream_active = False

    async def _read_stream(self) -> AsyncIterator[OpencodeEvent]:
        """One /event connection lifetime: yields events until it drops."""
        settings = self._http.settings
        stream_timeout = httpx.Timeout(
            settings.request_timeout_s,
            connect=settings.connect_timeout_s,
            read=settings.stream_read_timeout_s,
        )
        async with self._http.http().stream(
            "GET", "/event", headers=_SSE_HEADERS, timeout=stream_timeout
        ) as response:
            if response.status_code in _AUTH_FAILURE_STATUSES:
                await response.aread()
                raise OpencodeHttpError(
                    "GET", "/event", response.status_code, response.text[:200]
                )
            if response.status_code != 200:
                raise StreamDisconnected(f"GET /event -> HTTP {response.status_code}")
            parser = SseFrameParser()
            async for chunk in response.aiter_text():
                for frame in parser.feed(chunk):
                    event = decode_event(frame.data)
                    if event is not None:
                        yield event
        logger.info("opencode /event stream ended cleanly; will reconnect")

    # -- beyond the protocol: message history + tool-name mapping ------------

    async def messages(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch a session's message list (``GET /session/:id/message``).

        Used by startup recovery (T6) to reconcile attempts that finished
        while the gateway was down. Entries are the serve's ``{info,
        parts}`` objects, passed through untouched.
        """
        body = await self._http.request_json("GET", f"/session/{session_id}/message")
        if not isinstance(body, list):
            raise OpencodeResponseShapeError(
                f"GET /session/{session_id}/message: expected a list, "
                f"got {type(body).__name__}"
            )
        entries: list[dict[str, Any]] = []
        for entry in body:
            if isinstance(entry, dict):
                entries.append(entry)
            else:
                logger.warning(
                    "GET /session/%s/message: dropped non-object entry",
                    session_id,
                )
        return entries

    async def load_tool_mapping(self) -> ToolNameMap:
        """Fetch the MCP tool list and build the prefixed->bare name table.

        Call once at service startup (or before the first tool event needs
        a bare name). Connection failures propagate (a serve that is down
        at startup must be loud); HTTP/shape failures on either source
        degrade the table with a warning — :meth:`bare_tool_name` then
        falls back to dynamic server-prefix stripping.

        Returns:
            The freshly built
            :class:`~src.opencode_bridge.tool_names.ToolNameMap`.
        """
        mcp_payload = await self._fetch_mapping_source("/mcp")
        ids_payload = await self._fetch_mapping_source("/experimental/tool/ids")
        self._tool_map = build_tool_name_map(mcp_payload, ids_payload)
        logger.info(
            "opencode tool-name mapping loaded: %d exact entries, "
            "%d server prefixes",
            len(self._tool_map.prefixed_to_bare),
            len(self._tool_map.server_prefixes),
        )
        return self._tool_map

    async def _fetch_mapping_source(self, path: str) -> Any:
        """GET one mapping source; non-2xx / non-JSON degrade to ``None``."""
        response = await self._http.request("GET", path)
        if not 200 <= response.status_code < 300:
            logger.warning(
                "GET %s -> HTTP %d; tool-name mapping degrades",
                path,
                response.status_code,
            )
            return None
        try:
            return response.json()
        except ValueError:
            logger.warning(
                "GET %s returned a non-JSON body; tool-name mapping degrades",
                path,
            )
            return None

    @property
    def tool_map(self) -> ToolNameMap:
        """The loaded mapping table (empty until :meth:`load_tool_mapping`)."""
        return self._tool_map

    def bare_tool_name(self, name: str) -> str:
        """Strip the MCP server prefix from an opencode tool id.

        Args:
            name: Tool name as seen in events, e.g.
                ``vibe-trading_get_market_data``.

        Returns:
            The bare vt tool name (``get_market_data``), or *name*
            unchanged for builtins (``bash``) and unmapped names.
        """
        return self._tool_map.bare(name)
