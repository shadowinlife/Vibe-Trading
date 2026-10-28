"""Control-plane HTTP client for RM agent strategy schedules.

The RM control plane owns the schedule table, run history and due-time loop;
this box only relays user intent. This module is the narrow seam: seven RM
routes, one bearer token read from a mounted ``0600`` file, and a frozen error
taxonomy that never renders a failed call as a successful schedule.

Identity is the token alone — RM writes ``<sandbox_id>.<hmac>`` to
``RM_AGENT_TOKEN_FILE`` (default ``~/.config/rm/agent-token``):

* no self-asserted identity header (notably no ``x-sandbox-id``) is sent;
* the token never enters a URL, query, log line, exception or client ``repr``;
* configuration failures share one fixed detail, so a caller cannot probe;
* RM error bodies are untrusted: the exact token and any ``Bearer …`` fragment
  is redacted before a bounded detail is stored.

Every non-2xx raises. Requests are never retried, redirects are never
followed, and the token is never cached to disk.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

#: Prefix shared by every RM agent scheduling route.
API_PREFIX = "/api/v1/agent/strategy-schedules"

#: Default location of the ``0600`` token file RM writes at provision time.
#: The tilde is expanded when :func:`load_client` runs, never at import.
DEFAULT_TOKEN_FILE = "~/.config/rm/agent-token"

#: Default whole-request budget in seconds.
DEFAULT_TIMEOUT_S = 10.0

_MAX_DETAIL_CHARS = 500
_UNCONFIGURED_DETAIL = "scheduling is not configured"
_REDACTED = "[redacted]"
_BEARER_RE = re.compile(r"bearer\s+\S+", re.IGNORECASE)
#: Characters that would change the meaning of a schedule id inside a URL.
_UNSAFE_ID_CHARS = frozenset("/\\?#%")

_EMPTY = object()
_NOT_JSON = object()


class ScheduleError(Exception):
    """Base class for every RM scheduling failure.

    ``code`` is the stable machine-readable value the tool layer returns as
    ``{"ok": false, "error": code, "detail": detail}``. ``detail`` is bounded,
    NUL-free and redacted; it never carries the token.
    """

    code: str = "schedule_error"

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


class SchedulingUnavailable(ScheduleError):
    """Scheduling is not configured here, or RM reports its scheduler off."""

    code = "scheduling_unavailable"


class Unauthorized(ScheduleError):
    """RM rejected the bearer token with 401 or 403."""

    code = "unauthorized"


class ScheduleNotFound(ScheduleError):
    """RM has no schedule with the requested id (404)."""

    code = "not_found"


class ScheduleLimitReached(ScheduleError):
    """RM refused a create because the schedule quota is exhausted (409)."""

    code = "schedule_limit_reached"


class InvalidRequest(ScheduleError):
    """RM rejected the request shape (409 other, 422, other 4xx)."""

    code = "invalid_request"


class RmUnreachable(ScheduleError):
    """RM could not be reached, answered 5xx, or answered outside the contract."""

    code = "rm_unreachable"


def _bound_detail(raw: str, token: str) -> str:
    """Return *raw* bounded to :data:`_MAX_DETAIL_CHARS` with secrets removed."""
    cleaned = raw.replace("\x00", "")
    if token:
        cleaned = cleaned.replace(token, _REDACTED)
    cleaned = _BEARER_RE.sub(f"Bearer {_REDACTED}", cleaned).strip()
    return cleaned[:_MAX_DETAIL_CHARS]


def _parse_json(raw: str) -> Any:
    """Decode *raw* as JSON, returning ``_EMPTY`` or ``_NOT_JSON`` sentinels."""
    stripped = raw.strip()
    if not stripped:
        return _EMPTY
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return _NOT_JSON


def _marker_text(parsed: Any, keys: tuple[str, ...]) -> str:
    """Join the string values of *keys* in a decoded JSON object."""
    if not isinstance(parsed, dict):
        return ""
    values: list[str] = []
    for key in keys:
        value = parsed.get(key)
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def _detail_text(parsed: Any, raw_text: str) -> str:
    """Return RM's own ``detail``/``error`` text, else the whole body."""
    if isinstance(parsed, dict):
        for key in ("detail", "error"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw_text


def _schedule_id(value: str) -> str:
    """Return *value* as a URL path segment, or raise :class:`InvalidRequest`.

    Refuses empty ids, ``.``/``..``, path separators, URL delimiters and control
    characters before any request is built.
    """
    if (
        not value
        or value in {".", ".."}
        or any(char in _UNSAFE_ID_CHARS for char in value)
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise InvalidRequest("invalid_request", "schedule id is not a safe path segment")
    return value


def _dict_rows(items: list[Any]) -> list[dict[str, Any]]:
    """Return *items* as dicts, refusing a list whose members are not objects."""
    if not all(isinstance(item, dict) for item in items):
        raise RmUnreachable("rm_unreachable")
    return list(items)


def _expand_home(raw: str, environ: Mapping[str, str]) -> str:
    """Expand a leading ``~`` against *environ* so tests own the home directory."""
    if raw != "~" and not raw.startswith("~/"):
        return raw
    try:
        home = environ.get("HOME") or str(Path.home())
    except RuntimeError:
        raise SchedulingUnavailable("scheduling_unavailable", _UNCONFIGURED_DETAIL) from None
    return home + raw[1:]


def _load_base_url(environ: Mapping[str, str]) -> str:
    """Return the validated RM base URL, stripping trailing slashes."""
    raw = (environ.get("RM_AGENT_API_URL") or "").strip()
    if not raw or any(char.isspace() for char in raw):
        raise SchedulingUnavailable("scheduling_unavailable", _UNCONFIGURED_DETAIL)
    parts = urlsplit(raw)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise SchedulingUnavailable("scheduling_unavailable", _UNCONFIGURED_DETAIL)
    return raw.rstrip("/")


def _load_token(environ: Mapping[str, str]) -> str:
    """Read the bearer token, treating every failure as "not configured"."""
    raw_path = (environ.get("RM_AGENT_TOKEN_FILE") or "").strip() or DEFAULT_TOKEN_FILE
    path = Path(_expand_home(raw_path, environ))
    try:
        token = path.read_text(encoding="utf-8").rstrip("\r\n ")
    except (OSError, UnicodeError):
        raise SchedulingUnavailable("scheduling_unavailable", _UNCONFIGURED_DETAIL) from None
    if not token or any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise SchedulingUnavailable("scheduling_unavailable", _UNCONFIGURED_DETAIL)
    return token


@dataclass(frozen=True, slots=True, repr=False)
class AgentScheduleClient:
    """Synchronous client for the seven RM agent scheduling routes.

    ``token`` is a secret: it is never rendered by ``repr``/``str``.
    ``transport`` is the ``httpx.MockTransport`` test seam.
    """

    base_url: str
    token: str
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT_S
    transport: httpx.BaseTransport | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))
        object.__setattr__(self, "token", self.token.rstrip("\r\n "))

    def __repr__(self) -> str:
        return f"AgentScheduleClient(base_url={self.base_url!r}, token={_REDACTED!r})"

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create one schedule; *payload* is forwarded to RM unchanged."""
        return self._object(self._send("POST", API_PREFIX, json_body=payload))

    def list(self) -> list[dict[str, Any]]:
        """Return RM's schedules as a list of objects."""
        response = self._send("GET", API_PREFIX)
        text = self._read_text(response)
        self._raise_on_status(response.status_code, text)
        parsed = _parse_json(text)
        if isinstance(parsed, list):
            return _dict_rows(parsed)
        if isinstance(parsed, dict):
            for key in ("schedules", "items"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return _dict_rows(value)
        raise RmUnreachable("rm_unreachable")

    def get(self, schedule_id: str) -> dict[str, Any]:
        """Return one schedule by id."""
        return self._object(self._send("GET", self._item_path(schedule_id)))

    def pause(self, schedule_id: str) -> dict[str, Any]:
        """Disable one schedule without deleting its history."""
        return self._object(
            self._send("POST", self._item_path(schedule_id, "pause"), json_body={})
        )

    def resume(self, schedule_id: str) -> dict[str, Any]:
        """Re-enable one paused schedule."""
        return self._object(
            self._send("POST", self._item_path(schedule_id, "resume"), json_body={})
        )

    def retarget(self, schedule_id: str, strategy_version: str) -> dict[str, Any]:
        """Pin one schedule to *strategy_version*, keeping the schedule id."""
        return self._object(
            self._send(
                "POST",
                self._item_path(schedule_id, "retarget"),
                json_body={"strategy_version": strategy_version},
            )
        )

    def delete(self, schedule_id: str) -> dict[str, Any]:
        """Delete one schedule; an empty 200/204 body reads as ``{}``."""
        response = self._send("DELETE", self._item_path(schedule_id))
        return self._object(
            response, allow_empty=response.status_code in {200, 204}
        )

    def _item_path(self, schedule_id: str, action: str = "") -> str:
        """Build the item route, validating the id before any request."""
        identifier = _schedule_id(schedule_id)
        suffix = f"/{action}" if action else ""
        return f"{API_PREFIX}/{identifier}{suffix}"

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Send one request; any httpx failure becomes :class:`RmUnreachable`."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                return client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                )
        except httpx.HTTPError:
            raise RmUnreachable("rm_unreachable") from None

    @staticmethod
    def _read_text(response: httpx.Response) -> str:
        """Decode the body once; callers never log it."""
        return response.content.decode("utf-8", "replace")

    def _object(
        self, response: httpx.Response, *, allow_empty: bool = False
    ) -> dict[str, Any]:
        """Return a JSON object body, raising for any non-2xx or bad shape."""
        text = self._read_text(response)
        self._raise_on_status(response.status_code, text)
        parsed = _parse_json(text)
        if parsed is _EMPTY:
            if allow_empty:
                return {}
            raise RmUnreachable("rm_unreachable")
        if not isinstance(parsed, dict):
            raise RmUnreachable("rm_unreachable")
        return parsed

    def _raise_on_status(self, status: int, text: str) -> None:
        """Raise the frozen error for a non-2xx *status*; return on success."""
        if 200 <= status < 300:
            return
        parsed = _parse_json(text)
        if status in {401, 403}:
            raise Unauthorized("unauthorized")
        if status == 404:
            raise ScheduleNotFound("not_found")
        if status == 503:
            if "scheduler_disabled" in text:
                raise SchedulingUnavailable("scheduling_unavailable")
            raise RmUnreachable("rm_unreachable")
        if 400 <= status < 500:
            if status == 409 and "schedule_limit_reached" in _marker_text(
                parsed, ("error", "code", "detail")
            ):
                raise ScheduleLimitReached("schedule_limit_reached")
            raise InvalidRequest(
                "invalid_request", _bound_detail(_detail_text(parsed, text), self.token)
            )
        raise RmUnreachable("rm_unreachable")


def load_client(
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> AgentScheduleClient:
    """Build a client from ``RM_AGENT_API_URL`` and ``RM_AGENT_TOKEN_FILE``.

    ``environ`` defaults to :data:`os.environ`. Every configuration failure
    raises :class:`SchedulingUnavailable` with one fixed detail that names
    neither the path nor the token.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    return AgentScheduleClient(
        base_url=_load_base_url(source),
        token=_load_token(source),
        transport=transport,
    )


__all__ = [
    "API_PREFIX",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOKEN_FILE",
    "AgentScheduleClient",
    "InvalidRequest",
    "RmUnreachable",
    "ScheduleError",
    "ScheduleLimitReached",
    "ScheduleNotFound",
    "SchedulingUnavailable",
    "Unauthorized",
    "load_client",
]
