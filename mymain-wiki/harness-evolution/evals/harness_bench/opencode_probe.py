"""opencode-serve drivability probe — single-task round-trip protocol.

This is the exact protocol todo 4's baseline session bridge reuses:

    health -> session creation -> multi-turn dialog -> tool call
           -> result retrieval (message listing + verification)

Verified against opencode-serve v2.1.1-mymain (OpenCode server API, see
``GET /doc``): ``POST /api/session`` creates a session, ``POST
/api/session/{id}/prompt`` admits a turn asynchronously, and ``GET
/api/session/{id}/message`` lists messages whose assistant entries carry a
``content`` array of text/reasoning/tool parts. A legacy fallback
(``POST /session``, ``POST /session/{id}/message``) is kept for older
servers; the probe records which dialect it used.

The probe is read-only against the service: one throwaway session, two short
turns (the second forces a tool call through the Vibe-Trading MCP
``sentiment`` tool, which needs no credentials), then verification. All steps
use bounded timeouts; expected failures are recorded, never raised.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUTS: dict[str, float] = {
    "connect": 10.0,
    "health": 15.0,
    "session_create": 30.0,
    "message_turn": 300.0,  # per-turn budget incl. polling for completion
    "message_list": 30.0,
    "poll_interval": 5.0,
}

TURN1_PROMPT = (
    "This is an automated drivability probe, turn 1 of 2. "
    "Reply with exactly: PROBE_OK"
)
TURN2_PROMPT = (
    "Turn 2 of 2. Use the vibe-trading sentiment tool (sentiment_score mode) "
    "to score the text 'Stock market rallies strongly on positive earnings'. "
    "Then reply with exactly one line: SCORE=<the numeric score value>"
)
TURN1_MARKER = "PROBE_OK"
TURN2_MARKER = "SCORE="
TOOL_HINT = "sentiment"


class BasicAuth:
    def __init__(self, password: str):
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        self.header = f"Basic {token}"


def http_request(
    url: str,
    auth: BasicAuth | None,
    timeout: float,
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, Any, str]:
    """Bounded HTTP request. Returns (status, parsed_json_or_None, raw_head)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth is not None:
        req.add_header("Authorization", auth.header)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, None, raw[:2000]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, None, str(exc)[:500]
    try:
        return status, json.loads(raw), raw[:2000]
    except json.JSONDecodeError:
        return status, None, raw[:2000]


def _unwrap(body: Any) -> Any:
    """Newer opencode APIs wrap payloads as {"data": ...}."""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _iter_parts(message: Any) -> list[dict]:
    """Normalize both API dialects into a flat list of part-like dicts."""
    parts: list[dict] = []
    if not isinstance(message, dict):
        return parts
    for key in ("content", "parts"):  # v2 assistant content / legacy parts
        value = message.get(key)
        if isinstance(value, list):
            parts.extend(p for p in value if isinstance(p, dict))
    if not parts and (message.get("type") or message.get("text") is not None):
        parts.append(message)
    return parts


def _part_text(part: dict) -> str:
    text = part.get("text")
    return text if isinstance(text, str) else ""


def _part_is_tool(part: dict) -> bool:
    if part.get("type") == "tool":
        return True
    tool_name = part.get("tool") or part.get("toolName") or part.get("tool_name")
    return isinstance(tool_name, str) and bool(tool_name)


def _tool_names(parts: list[dict]) -> list[str]:
    names: list[str] = []
    for part in parts:
        for key in ("tool", "toolName", "tool_name", "name"):
            value = part.get(key)
            if isinstance(value, str) and value:
                names.append(value)
    return names


class _Dialect:
    """Endpoint dialect discovered at runtime (v2 /api prefix or legacy)."""

    def __init__(self, base: str, auth: BasicAuth, t: dict[str, float]):
        self.base = base.rstrip("/")
        self.auth = auth
        self.t = t
        self.prefix = ""  # "" = legacy, "/api" = current server API

    def create_session(self) -> tuple[str | None, str]:
        for prefix in ("/api", ""):
            status, body, raw = http_request(
                f"{self.base}{prefix}/session",
                self.auth,
                self.t["session_create"],
                "POST",
                {},
            )
            data = _unwrap(body)
            if status in (200, 201) and isinstance(data, dict) and data.get("id"):
                self.prefix = prefix
                return str(data["id"]), f"POST {prefix}/session -> {status}"
        return None, f"session creation failed; last body head: {raw[:200]}"

    def send_prompt(self, session_id: str, text: str) -> tuple[bool, str]:
        if self.prefix == "/api":
            status, body, raw = http_request(
                f"{self.base}/api/session/{session_id}/prompt",
                self.auth,
                self.t["session_create"],
                "POST",
                {"prompt": {"text": text}},
            )
            admitted = status in (200, 201)
            return admitted, f"POST /api/session/../prompt -> {status}: {raw[:150]}"
        status, body, raw = http_request(
            f"{self.base}/session/{session_id}/message",
            self.auth,
            self.t["message_turn"],
            "POST",
            {"parts": [{"type": "text", "text": text}]},
        )
        return status in (200, 201), f"POST /session/../message -> {status}"

    def list_messages(self, session_id: str) -> tuple[list[dict], str]:
        status, body, raw = http_request(
            f"{self.base}{self.prefix}/session/{session_id}/message",
            self.auth,
            self.t["message_list"],
        )
        data = _unwrap(body)
        if status == 200 and isinstance(data, list):
            return [m for m in data if isinstance(m, dict)], f"{len(data)} messages"
        return [], f"GET messages -> {status}: {raw[:150]}"

    def poll_until(
        self, session_id: str, markers: list[str], need_tool: bool
    ) -> tuple[bool, list[dict], str]:
        """Poll the message list until markers (+tool leg) appear or budget."""
        deadline = time.monotonic() + self.t["message_turn"]
        detail = "budget exhausted"
        while time.monotonic() < deadline:
            messages, detail = self.list_messages(session_id)
            parts: list[dict] = []
            for message in messages:
                parts.extend(_iter_parts(message))
            text = "\n".join(_part_text(p) for p in parts)
            markers_ok = all(m in text for m in markers)
            tool_ok = (not need_tool) or any(_part_is_tool(p) for p in parts)
            if markers_ok and tool_ok:
                return True, parts, f"verified: {detail}"
            time.sleep(self.t["poll_interval"])
        return False, [], detail


def probe_session_round_trip(
    base_url: str,
    password: str,
    timeouts: dict[str, float] | None = None,
) -> dict:
    """Run the full single-task round-trip against a running opencode-serve.

    Returns::

        {
          "ok": bool,                # every phase passed
          "phases": {phase: {"ok": bool, "detail": str}},
          "session_id": str | None,
          "tool_call_observed": bool,
          "tool_names": [str],
          "assistant_errors": int,   # assistant messages ending in error
          "elapsed_seconds": float,
        }
    """
    t = {**DEFAULT_TIMEOUTS, **(timeouts or {})}
    auth = BasicAuth(password)
    dialect = _Dialect(base_url, auth, t)
    phases: dict[str, dict[str, Any]] = {}
    session_id: str | None = None
    tool_seen = False
    tool_names: list[str] = []
    started = time.monotonic()

    def record(phase: str, ok: bool, detail: str) -> bool:
        phases[phase] = {"ok": ok, "detail": detail[:500]}
        return ok

    status, _, _ = http_request(f"{base_url.rstrip('/')}/health", auth, t["health"])
    if not record("health", status == 200, f"GET /health -> {status}"):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)

    session_id, detail = dialect.create_session()
    if not record("session_create", session_id is not None, detail):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)

    admitted, detail = dialect.send_prompt(session_id, TURN1_PROMPT)
    if not record("turn1_dialog", admitted, detail):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)
    ok, parts, detail = dialect.poll_until(session_id, [TURN1_MARKER], False)
    if not record("turn1_dialog", ok, f"turn1 marker: {detail}"):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)

    admitted, detail = dialect.send_prompt(session_id, TURN2_PROMPT)
    if not record("turn2_tool_call", admitted, detail):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)
    ok, parts, detail = dialect.poll_until(session_id, [TURN2_MARKER], True)
    tool_seen = any(_part_is_tool(p) for p in parts)
    tool_names = _tool_names([p for p in parts if _part_is_tool(p)])
    if not record(
        "turn2_tool_call",
        ok,
        f"turn2 marker+tool: {detail}; tools={tool_names[:5]}",
    ):
        return _finish(phases, session_id, started, t, tool_seen, tool_names)

    messages, detail = dialect.list_messages(session_id)
    all_parts: list[dict] = []
    assistant_errors = 0
    for message in messages:
        all_parts.extend(_iter_parts(message))
        if message.get("type") == "assistant" and (
            message.get("error") or message.get("finish") == "error"
        ):
            assistant_errors += 1
    full_text = "\n".join(_part_text(p) for p in all_parts)
    tool_seen = tool_seen or any(_part_is_tool(p) for p in all_parts)
    tool_names = tool_names or _tool_names([p for p in all_parts if _part_is_tool(p)])
    record(
        "result_retrieval",
        bool(messages),
        f"{detail}; messages={len(messages)} assistant_errors={assistant_errors}",
    )
    record(
        "verification",
        TURN1_MARKER in full_text and TURN2_MARKER in full_text and tool_seen,
        (
            f"turn1_marker={TURN1_MARKER in full_text} "
            f"turn2_marker={TURN2_MARKER in full_text} "
            f"tool_call_observed={tool_seen} tools={tool_names[:5]}"
        ),
    )
    result = _finish(phases, session_id, started, t, tool_seen, tool_names)
    result["assistant_errors"] = assistant_errors
    return result


def _finish(
    phases: dict,
    session_id: str | None,
    started: float,
    timeouts: dict[str, float],
    tool_seen: bool,
    tool_names: list[str],
) -> dict:
    return {
        "ok": bool(phases) and all(p["ok"] for p in phases.values()),
        "phases": phases,
        "session_id": session_id,
        "tool_call_observed": tool_seen,
        "tool_names": tool_names,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "timeouts_seconds": timeouts,
    }
