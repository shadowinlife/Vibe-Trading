"""Shared rig helpers for the T7 Web E2E (stdlib + playwright page objects).

Imported by ``conftest.py`` fixtures and the test modules. Kept separate so
the conftest stays fixture-only and the helpers are reusable from the
one-off smoke scripts.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

_TICKET_RE = re.compile(r"(ticket=)[^&\"']+")

#: The exact event-type list the frontend subscribes to (useSSE.ts
#: knownTypes) — the contract surface: an SSE frame whose type is not in
#: this list never reaches a handler, and a payload whose shape a handler
#: rejects is silently dropped (plan QA: silent drop = FAIL).
FRONTEND_KNOWN_SSE_TYPES = frozenset(
    {
        "text_delta",
        "reasoning_delta",
        "stream_reset",
        "thinking_done",
        "tool_call",
        "tool_result",
        "compact",
        "tool_heartbeat",
        "tool_progress",
        "llm_usage",
        "swarm.started",
        "swarm.event",
        "attempt.created",
        "attempt.started",
        "attempt.completed",
        "attempt.failed",
        "attempt.cancelled",
        "message.received",
        "session.created",
        "goal.created",
        "goal.evidence",
        "goal.updated",
        "mandate.proposal",
        "mandate.committed",
        "scheduled_research.proposal",
        "live.halted",
        "live.resumed",
        "live.action",
        "heartbeat",
        "done",
    }
)

#: EventSource-wrapper init script: records every frame the page's SSE
#: listeners receive into ``window.__sseLog`` (reset per navigation).
SSE_LOGGER_JS = """
(() => {
  try { window.localStorage.setItem("__KEY_NAME__", "__KEY_VALUE__"); } catch (e) {}
  const log = (window.__sseLog = []);
  const OrigES = window.EventSource;
  if (!OrigES) return;
  const Patched = function (url, config) {
    const es = new OrigES(url, config);
    log.push({ t: Date.now(), kind: "ctor", url: String(url) });
    const origAEL = es.addEventListener.bind(es);
    es.addEventListener = function (type, listener, options) {
      origAEL(type, function (ev) {
        try {
          log.push({
            t: Date.now(), kind: "event", type: type, url: String(url),
            lastEventId: ev.lastEventId || null,
            data: typeof ev.data === "string" ? ev.data : null,
          });
        } catch (e) {}
      }, options);
      return origAEL(type, listener, options);
    };
    return es;
  };
  Patched.prototype = OrigES.prototype;
  Patched.CONNECTING = OrigES.CONNECTING;
  Patched.OPEN = OrigES.OPEN;
  Patched.CLOSED = OrigES.CLOSED;
  window.EventSource = Patched;
})();
"""


def sse_logger_script(api_key: str) -> str:
    """The init script with the scratch API key baked into the localStorage set."""
    return SSE_LOGGER_JS.replace("__KEY_NAME__", "vibe_trading_api_auth_key").replace(
        "__KEY_VALUE__", api_key
    )


class GatewayApi:
    """Minimal REST client for the rig gateway (stdlib urllib + Bearer key)."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode()
                return response.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(payload)
            except json.JSONDecodeError:
                return exc.code, payload[:500]


# ---------------------------------------------------------------------------
# Page-level helpers (playwright sync API)
# ---------------------------------------------------------------------------


def screenshot(page, evidence_dir: Path, name: str) -> Path:
    path = evidence_dir / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    return path


def sse_log(page) -> List[Dict[str, Any]]:
    return page.evaluate("() => window.__sseLog || []")


def sse_events(page, event_type: str) -> List[Dict[str, Any]]:
    """Parsed payloads of every SSE frame of *event_type* the page received."""
    out: List[Dict[str, Any]] = []
    for entry in sse_log(page):
        if entry.get("kind") != "event" or entry.get("type") != event_type:
            continue
        try:
            out.append(json.loads(entry.get("data") or "null"))
        except json.JSONDecodeError:
            out.append({"_raw": entry.get("data")})
    return out


def sse_types(page) -> set[str]:
    return {
        entry["type"]
        for entry in sse_log(page)
        if entry.get("kind") == "event" and entry.get("type")
    }


def dump_sse(page, evidence_dir: Path, name: str) -> Path:
    path = evidence_dir / f"sse-{name}.json"
    entries = sse_log(page)
    redacted = [
        {**entry, "url": _TICKET_RE.sub(r"\1[REDACTED]", entry.get("url") or "")}
        for entry in entries
    ]
    path.write_text(json.dumps(redacted, indent=1), encoding="utf-8")
    return path


def wait_for_sse(
    page, event_type: str, timeout_s: float, predicate=None
) -> Dict[str, Any]:
    """Poll ``window.__sseLog`` until an event of *event_type* matches."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for payload in sse_events(page, event_type):
            if predicate is None or predicate(payload):
                return payload
        time.sleep(0.5)
    raise AssertionError(
        f"SSE event {event_type!r} not seen within {timeout_s}s "
        f"(log size: {len(sse_log(page))}, types: {sorted(sse_types(page))})"
    )


def open_session(page, api: GatewayApi, title: str) -> str:
    """Create a vt session via REST and open it in the page (SSE connects)."""
    status, body = api.request("POST", "/sessions", {"title": title})
    assert status in (200, 201), f"create session failed: {status} {body}"
    session_id = body["session_id"]
    page.goto(f"/agent?session={session_id}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => (window.__sseLog || []).some((e) => e.kind === 'ctor')",
        timeout=20_000,
    )
    return session_id


def send_via_ui(page, text: str) -> None:
    """Type into the composer and submit (the real user path)."""
    textarea = page.locator("textarea").first
    textarea.wait_for(state="visible", timeout=15_000)
    textarea.fill(text)
    page.locator("button[type=submit]").first.click()


def wait_answer_text(page, contains: str, timeout_s: float) -> None:
    """Wait until a rendered answer bubble contains *contains*."""
    page.wait_for_function(
        """(needle) => Array.from(document.querySelectorAll(".prose"))
             .some((el) => (el.textContent || "").includes(needle))""",
        arg=contains,
        timeout=timeout_s * 1000,
    )


def count_answer_bubbles(page, contains: str) -> int:
    return page.evaluate(
        """(needle) => Array.from(document.querySelectorAll(".prose"))
             .filter((el) => (el.textContent || "").includes(needle)).length""",
        contains,
    )


def body_text(page) -> str:
    return page.locator("body").inner_text()


def engine_session_id(rig_state: Dict[str, Any], vt_session_id: str) -> Optional[str]:
    """The opencode session id mapped from a vt session (scratch store)."""
    session_file = (
        Path(rig_state["vt_home"]) / "sessions" / vt_session_id / "session.json"
    )
    if not session_file.exists():
        return None
    data = json.loads(session_file.read_text(encoding="utf-8"))
    return (data.get("config") or {}).get("opencode_engine_session_id")


class GroupRecorder:
    """Collects per-assertion results and writes the group's evidence JSON."""

    def __init__(self, evidence_dir: Path, group: str) -> None:
        self.evidence_dir = evidence_dir
        self.group = group
        self.entries: List[Dict[str, Any]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.entries.append(
            {"assertion": name, "pass": bool(condition), "detail": detail}
        )
        assert condition, f"[{self.group}] assertion failed: {name} ({detail})"

    def info(self, name: str, value: Any) -> None:
        self.entries.append({"assertion": name, "pass": None, "detail": str(value)})

    def write(self, passed: bool) -> Path:
        path = self.evidence_dir / f"{self.group}-results.json"
        payload = {
            "group": self.group,
            "passed": passed,
            "assertions": self.entries,
            "finished_at": time.time(),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path
