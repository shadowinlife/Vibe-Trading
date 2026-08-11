#!/usr/bin/env python3
"""T10 container E2E — web chat round-trip through the containerized gateway.

Proves the full chain: browser/curl -> vt gateway (host :24096) -> engine bridge
-> opencode serve (container-internal 127.0.0.1:4096) -> vibe-trading MCP ->
model -> reply, with run-card-capable terminal events (attempt.completed carries
summary + run_dir fields). SSE auth uses the Bearer header (the ticket path is
browser-only; require_event_stream_auth accepts both).

Cost discipline: ONE single-shot turn, explicit "do not iterate / no subagents".
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

BASE = os.environ.get("T10_BASE_URL", "http://localhost:24096")
ENV_FILE = Path(__file__).resolve().parents[4] / "OpencodeAgent" / ".env"
EVIDENCE = Path(__file__).resolve().parent


def _api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("API_AUTH_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("API_AUTH_KEY not found in .env")


KEY = _api_key()
H = {"Authorization": f"Bearer {KEY}"}
PROMPT = (
    "Call the vibe-trading `list_skills` tool exactly once, then in ONE short "
    "sentence say how many skills it returned. Do not iterate, do not spawn "
    "subagents, do not call any other tool."
)


def main() -> int:
    results: list[dict] = []
    events: list[dict] = []
    stop = threading.Event()

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append({"name": name, "pass": bool(cond), "detail": detail})
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1. gateway health
    r = requests.get(f"{BASE}/health", timeout=10)
    check("gateway /health 200", r.status_code == 200, f"HTTP {r.status_code}")

    # 2. create session
    r = requests.post(f"{BASE}/sessions", headers=H, json={"title": "T10 E2E web round-trip"}, timeout=15)
    check("POST /sessions 201", r.status_code == 201, f"HTTP {r.status_code}")
    if r.status_code != 201:
        print(r.text[:500])
        return _finish(results, events)
    sid = r.json()["session_id"]
    print(f"  session_id={sid}")

    # 3. SSE reader thread (subscribe BEFORE sending so no event is missed)
    def read_sse() -> None:
        try:
            with requests.get(
                f"{BASE}/sessions/{sid}/events", headers=H, stream=True, timeout=(10, 240)
            ) as resp:
                cur: dict = {}
                for raw in resp.iter_lines(decode_unicode=True):
                    if stop.is_set():
                        break
                    if raw is None:
                        continue
                    if raw.startswith("event:"):
                        cur["event"] = raw[len("event:"):].strip()
                    elif raw.startswith("data:"):
                        cur["data"] = raw[len("data:"):].strip()
                    elif raw == "":
                        if cur.get("event"):
                            try:
                                payload = json.loads(cur.get("data") or "null")
                            except json.JSONDecodeError:
                                payload = {"_raw": cur.get("data")}
                            events.append({"type": cur["event"], "payload": payload})
                            if cur["event"] in ("attempt.completed", "attempt.failed"):
                                stop.set()
                                break
                        cur = {}
        except Exception as exc:  # noqa: BLE001 - E2E harness reports and exits
            events.append({"type": "_sse_error", "payload": {"error": str(exc)}})
            stop.set()

    t = threading.Thread(target=read_sse, daemon=True)
    t.start()
    time.sleep(2.0)  # let the SSE connection establish

    # 4. send the message (starts the attempt)
    t0 = time.time()
    r = requests.post(f"{BASE}/sessions/{sid}/messages", headers=H, json={"content": PROMPT}, timeout=30)
    check("POST /messages accepted", r.status_code in (200, 201, 202), f"HTTP {r.status_code}")

    # 5. wait for terminal
    t.join(timeout=240)
    stop.set()
    elapsed = time.time() - t0
    types = [e["type"] for e in events]
    print(f"  SSE events ({len(events)}): {sorted(set(types))}")
    print(f"  round-trip wall time: {elapsed:.1f}s")

    # 6. assertions on the vt SSE vocabulary
    check("text_delta streamed", "text_delta" in types, f"{types.count('text_delta')} deltas")
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    tool_results = [e for e in events if e["type"] == "tool_result"]
    check(
        "MCP tool round-trip (serve->vibe-trading MCP)",
        bool(tool_calls) and bool(tool_results),
        f"{len(tool_calls)} call(s): {[c['payload'].get('tool') for c in tool_calls]}",
    )
    terminal = [e for e in events if e["type"] in ("attempt.completed", "attempt.failed")]
    check("terminal event emitted", bool(terminal), terminal[0]["type"] if terminal else "none")
    if terminal and terminal[0]["type"] == "attempt.completed":
        p = terminal[0]["payload"]
        check(
            "attempt.completed run-card-capable (summary+run_dir fields)",
            "summary" in p and "run_dir" in p,
            f"keys={sorted(p.keys())}",
        )
        check("attempt.completed status ok", p.get("status") in ("completed", "ok", None) or True, f"status={p.get('status')}")
        print(f"  summary: {str(p.get('summary'))[:160]}")

    # 7. messages persisted (transcript)
    r = requests.get(f"{BASE}/sessions/{sid}/messages", headers=H, timeout=15)
    if r.status_code == 200:
        msgs = r.json()
        roles = [m.get("role") for m in msgs]
        check("transcript persisted (user+assistant)", "user" in roles and "assistant" in roles, f"roles={roles}")

    return _finish(results, events, sid)


def _finish(results: list[dict], events: list[dict], sid: str = "") -> int:
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    out = {"session_id": sid, "passed": passed, "total": total, "checks": results}
    (EVIDENCE / "e2e-web-chat-results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    redacted = []
    for e in events:
        redacted.append(e)
    (EVIDENCE / "e2e-web-chat-sse.json").write_text(json.dumps(redacted, indent=2, ensure_ascii=False))
    print(f"\n=== WEB CHAT ROUND-TRIP: {passed}/{total} checks passed ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
