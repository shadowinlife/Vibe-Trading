"""T7 single-tenant Web E2E — the eight mandatory assertion groups (live rig).

Proves the Phase-1 architecture claim: the UNCHANGED React frontend works
on the opencode engine (``VIBE_TRADING_ENGINE=opencode``) end to end. Every
assertion is programmatic (DOM / REST / SSE-frame level) per the plan QA
rule (以断言而非肉眼为准 — a frontend handler silently dropping a malformed
SSE shape is a FAIL); screenshots in the evidence dir are artifacts, never
the verdict.

Groups (plan T7, none skippable):
  g1 send -> full vt SSE vocabulary streams and renders
  g2 minimal backtest -> attempt.completed.run_dir -> /runs/{id} 200 + run card
  g3 mid-turn cancel -> stopped state, no zombie streaming
  g4 concurrent send while busy -> 409 semantics surfaced
  g5 refresh post-turn -> history restores with tool_trail rebuilt
  g6 reload mid-turn -> replay=active resumes live, no duplicate bubbles
  g7 >90s content-silent tool -> frontend watchdog NOT tripped (B3 defense)
  g8 upload -> analyze -> injected absolute-path resolution works (B6/D8②)

Run with the rig up (see README.md); skipped at collection unless
``ENGINE_BRIDGE_E2E=1`` (conftest).
"""

from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from tests.e2e_engine_bridge.riglib import (
    FRONTEND_KNOWN_SSE_TYPES,
    body_text,
    count_answer_bubbles,
    dump_sse,
    engine_session_id,
    open_session,
    screenshot,
    send_via_ui,
    sse_events,
    sse_log,
    sse_types,
    wait_answer_text,
    wait_for_sse,
)

# Cross-test handoffs (g2 -> g5). Single module, definition order = run order.
STATE: Dict[str, Any] = {}

NO_TODO = "Do not create a todo list. Do not spawn subagents."


def _i18n(i18n_en: Dict[str, Any], *path: str) -> str:
    node: Any = i18n_en
    for key in path:
        node = node[key]
    return str(node)


# ---------------------------------------------------------------------------
# Group 1 — send message: full vt SSE vocabulary streams and renders
# ---------------------------------------------------------------------------


def test_g1_send_message_streams_vt_vocabulary(
    page, api, rig_state, i18n_en, evidence_dir, recorder
):
    sid = open_session(page, api, "T7E2E G1 vocab")
    send_via_ui(page, "Reply with exactly this text and nothing else: E2E-OK-1")

    completed = wait_for_sse(page, "attempt.completed", 240)
    types = sse_types(page)

    for required in (
        "message.received",
        "attempt.created",
        "attempt.started",
        "text_delta",
        "attempt.completed",
    ):
        recorder.check(
            f"SSE vocabulary carries {required}", required in types, str(sorted(types))
        )
    recorder.check(
        "every streamed type is frontend-consumed (useSSE knownTypes)",
        types <= FRONTEND_KNOWN_SSE_TYPES,
        str(sorted(types - FRONTEND_KNOWN_SSE_TYPES)),
    )

    deltas = sse_events(page, "text_delta")
    recorder.check(
        "text_delta payload shape {delta:str}",
        len(deltas) >= 1 and all(isinstance(d.get("delta"), str) for d in deltas),
        f"n={len(deltas)}",
    )
    recorder.check(
        "text_delta carries attempt_id stamp", all(d.get("attempt_id") for d in deltas)
    )

    # reasoning_delta: qwen3.8-max emits reasoning parts on every turn (spike
    # §5a: 16/17 deltas were reasoning). If absent, verify against the ENGINE
    # whether reasoning parts existed — absent parts = N/A, present parts =
    # translator dropped them = FAIL.
    if "reasoning_delta" in types:
        tails = sse_events(page, "reasoning_delta")
        recorder.check(
            "reasoning_delta payload shape {tail:str}",
            all(isinstance(t.get("tail"), str) for t in tails),
        )
    else:
        engine_sid = engine_session_id(rig_state, sid)
        assert engine_sid, "vt->engine session mapping missing from scratch store"
        import urllib.request

        with urllib.request.urlopen(
            f"{rig_state['serve_url']}/session/{engine_sid}/message"
        ) as r:
            messages = json.loads(r.read().decode())
        reasoning_parts = [
            part
            for entry in messages
            for part in entry.get("parts", [])
            if part.get("type") == "reasoning" and (part.get("text") or "").strip()
        ]
        recorder.check(
            "reasoning_delta present or engine emitted no reasoning parts",
            not reasoning_parts,
            f"engine had {len(reasoning_parts)} reasoning parts but no reasoning_delta",
        )

    if "llm_usage" in types:
        recorder.info("llm_usage observed", sse_events(page, "llm_usage")[0])

    for field in (
        "attempt_id",
        "status",
        "summary",
        "run_dir",
        "elapsed_ms",
        "provider",
        "model",
    ):
        recorder.check(
            f"attempt.completed enumerates {field} (D5)",
            field in completed,
            str(sorted(completed)),
        )
    recorder.check(
        "attempt.completed status=completed", completed.get("status") == "completed"
    )

    wait_answer_text(page, "E2E-OK-1", 30)
    recorder.check(
        "answer bubble rendered in UI", count_answer_bubbles(page, "E2E-OK-1") == 1
    )

    dump_sse(page, evidence_dir, "g1")
    screenshot(page, evidence_dir, "g1-vocabulary")


# ---------------------------------------------------------------------------
# Group 2 — backtest turn: run_dir -> /runs/{id} 200 + run card renders
# ---------------------------------------------------------------------------

BACKTEST_PROMPT = (
    "Run ONE minimal backtest exactly as specified below, then stop. "
    "Cost discipline: do NOT iterate, do NOT optimize, do NOT re-run, "
    "do NOT spawn subagents, do NOT create a todo list.\n"
    "Spec:\n"
    "- data: yfinance daily bars for AAPL.US only, 2024-01-01 to 2024-06-30\n"
    "- strategy: SMA crossover fast=5 slow=20; long (full equity) when fast>slow, else flat\n"
    "- create the run directory under the runs root with config.json and "
    "code/signal_engine.py, then run the backtest exactly once via the "
    "standard backtest runner\n"
    "After it completes, reply with ONLY the run_dir path."
)


def test_g2_backtest_run_dir_and_run_card(page, api, evidence_dir, recorder):
    sid = open_session(page, api, "T7E2E G2 backtest")
    send_via_ui(page, BACKTEST_PROMPT)

    completed = wait_for_sse(page, "attempt.completed", 900)
    run_dir = completed.get("run_dir")
    recorder.check(
        "attempt.completed carries run_dir", bool(run_dir), str(completed)[:300]
    )
    run_id = Path(str(run_dir)).name

    status, run = api.request("GET", f"/runs/{run_id}")
    recorder.check("GET /runs/{id} -> 200", status == 200, f"got {status}")
    recorder.check("run payload has data", isinstance(run, dict) and bool(run))

    types = sse_types(page)
    recorder.check("tool_call streamed", "tool_call" in types, str(sorted(types)))
    recorder.check("tool_result streamed", "tool_result" in types)
    tool_calls = sse_events(page, "tool_call")
    # Production governance (frozen vibe-trading-tools.json) DISABLES the
    # backtest MCP tool — the live path is the runner CLI through bash. Both
    # forms count; bare names only (prefix stripped, D5).
    backtest_exec = [
        tc
        for tc in tool_calls
        if tc.get("tool") == "backtest"
        or (
            tc.get("tool") == "bash"
            and "backtest.runner" in json.dumps(tc.get("arguments") or {})
        )
    ]
    recorder.check(
        "backtest execution observed (MCP tool or bash runner CLI)",
        bool(backtest_exec),
        str([tc.get("tool") for tc in tool_calls]),
    )
    recorder.check(
        "no MCP-prefixed tool names leaked",
        all(
            not str(tc.get("tool", "")).startswith("vibe-trading") for tc in tool_calls
        ),
    )
    tool_results = sse_events(page, "tool_result")
    recorder.check(
        "tool_result payload shape {tool,status,preview,elapsed_ms}",
        all(
            {"tool", "status", "preview"} <= set(tr)
            and isinstance(tr.get("preview"), str)
            for tr in tool_results
        ),
    )
    recorder.check(
        "run produced artifacts on disk",
        (Path(str(run_dir)) / "run_card.json").exists(),
        str(run_dir),
    )

    # Run card: RunCompleteCard renders a link to /runs/{id} (Agent.tsx:876).
    page.wait_for_selector(f'a[href*="/runs/{run_id}"]', timeout=60_000)
    recorder.check("run card link rendered in UI", True)

    wait_answer_text(page, run_id, 30)
    recorder.check("answer bubble mentions the run", True)

    STATE["g2"] = {"sid": sid, "run_id": run_id, "run_dir": str(run_dir)}
    dump_sse(page, evidence_dir, "g2")
    screenshot(page, evidence_dir, "g2-run-card")


# ---------------------------------------------------------------------------
# Group 3 — mid-turn cancel: stopped state, no zombie streaming
# ---------------------------------------------------------------------------


def test_g3_mid_turn_cancel(page, api, i18n_en, evidence_dir, recorder):
    sid = open_session(page, api, "T7E2E G3 cancel")
    send_via_ui(
        page,
        "Run this exact bash command: sleep 30. After it completes, reply with "
        f"exactly CANCEL-DONE. {NO_TODO}",
    )
    wait_for_sse(page, "tool_call", 180)

    page.locator("button.bg-destructive").first.click()
    cancelled = wait_for_sse(page, "attempt.cancelled", 60)
    recorder.check(
        "attempt.cancelled payload {attempt_id,status}",
        cancelled.get("status") == "cancelled" and bool(cancelled.get("attempt_id")),
        str(cancelled),
    )

    stopped_label = _i18n(i18n_en, "agent", "activity", "stopped")
    page.wait_for_selector(
        f"div[role=status]:has-text('{stopped_label}')", timeout=30_000
    )
    recorder.check("UI shows the stopped activity state", True)
    recorder.check(
        "composer unlocked (status idle, stop button gone)",
        page.locator("button.bg-destructive").count() == 0,
    )

    deltas_at_cancel = len(sse_events(page, "text_delta"))
    time.sleep(
        12
    )  # past quiescence: late engine events must be dropped bridge-side (D4)
    recorder.check(
        "no zombie streaming after cancel (no post-terminal text_delta)",
        len(sse_events(page, "text_delta")) == deltas_at_cancel,
        f"{deltas_at_cancel} -> {len(sse_events(page, 'text_delta'))}",
    )

    status, messages = api.request("GET", f"/sessions/{sid}/messages")
    assert status == 200
    assistants = [m for m in messages if m["role"] == "assistant"]
    recorder.check("cancelled reply persisted", bool(assistants))
    recorder.check(
        "persisted reply metadata.status == cancelled (D6)",
        (assistants[-1].get("metadata") or {}).get("status") == "cancelled",
        str(assistants[-1].get("metadata")),
    )

    dump_sse(page, evidence_dir, "g3")
    screenshot(page, evidence_dir, "g3-cancelled")


# ---------------------------------------------------------------------------
# Group 4 — concurrent second send while busy: 409 semantics surfaced
# ---------------------------------------------------------------------------


def test_g4_concurrent_send_409(page, api, evidence_dir, recorder):
    sid = open_session(page, api, "T7E2E G4 busy")
    send_via_ui(
        page,
        "Run this exact bash command: sleep 20. Then reply with exactly "
        f"B409-DONE. {NO_TODO}",
    )
    wait_for_sse(page, "attempt.started", 180)

    status, body = api.request(
        "POST", f"/sessions/{sid}/messages", {"content": "second concurrent send"}
    )
    recorder.check(
        "concurrent send while busy -> HTTP 409", status == 409, f"got {status}: {body}"
    )
    recorder.check(
        "409 detail surfaces the busy semantics",
        "in progress" in json.dumps(body).lower(),
    )

    recorder.check(
        "UI busy state surfaced: stop button visible",
        page.locator("button.bg-destructive").count() >= 1,
    )
    recorder.check(
        "UI busy state surfaced: composer read-only",
        page.locator("textarea").first.get_attribute("readonly") is not None,
    )
    screenshot(page, evidence_dir, "g4-busy-409")

    api.request("POST", f"/sessions/{sid}/cancel")
    wait_for_sse(page, "attempt.cancelled", 60)
    recorder.check("session drained after cancel (cleanup)", True)
    dump_sse(page, evidence_dir, "g4")


# ---------------------------------------------------------------------------
# Group 5 — refresh post-turn: history restores with tool_trail rebuilt
# ---------------------------------------------------------------------------


def _find_g2_session(api) -> Dict[str, str]:
    """Locate the newest completed G2 backtest session via REST (g5 rerun path)."""
    status, sessions = api.request("GET", "/sessions")
    assert status == 200
    for item in sessions:
        if not str(item.get("title", "")).startswith("T7E2E G2"):
            continue
        sid = item["session_id"]
        _, messages = api.request("GET", f"/sessions/{sid}/messages")
        for message in messages or []:
            run_id = (message.get("metadata") or {}).get("run_id")
            if (
                message.get("role") == "assistant"
                and run_id
                and message.get("tool_trail")
            ):
                return {"sid": sid, "run_id": run_id}
    pytest.fail("no completed T7E2E G2 backtest session found via REST")


def test_g5_refresh_restores_history_and_tool_trail(
    page, api, i18n_en, evidence_dir, recorder
):
    g2 = STATE.get("g2") or _find_g2_session(api)
    sid, run_id = g2["sid"], g2["run_id"]

    status, messages = api.request("GET", f"/sessions/{sid}/messages")
    assert status == 200
    users = [m for m in messages if m["role"] == "user"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    recorder.check("user message persisted", bool(users))
    recorder.check(
        "transcript keeps RAW user content (no injection block, D3)",
        "[gateway context]" not in users[0]["content"],
        users[0]["content"][:120],
    )
    trail = assistants[-1].get("tool_trail") or []
    recorder.check(
        "assistant reply persisted with tool_trail", bool(trail), f"n={len(trail)}"
    )
    recorder.check(
        "tool_trail entries carry tool+status (native shape)",
        all(isinstance(e, dict) and e.get("tool") for e in trail),
        str(trail[:2]),
    )
    recorder.check(
        "reply metadata.run_id matches (run card source)",
        (assistants[-1].get("metadata") or {}).get("run_id") == run_id,
    )

    # Hard refresh into the session: history + tool timeline + run card rebuild.
    page.goto(f"/agent?session={sid}", wait_until="domcontentloaded")
    page.wait_for_selector(f'a[href*="/runs/{run_id}"]', timeout=30_000)
    recorder.check("run card restored after refresh", True)

    # The UI renders trail steps with LOCALIZED tool labels (tools.ts:
    # i18n tools.<name> -> TOOL_LABELS -> humanized); assert the label of a
    # tool that is actually in the persisted trail.
    labels = i18n_en.get("tools") or {}
    expected_label = next(
        (labels[t.get("tool")] for t in trail if t.get("tool") in labels), None
    )
    recorder.check(
        "trail has a localizable tool label",
        expected_label is not None,
        str([t.get("tool") for t in trail]),
    )
    activity = page.locator("div[role=status]").first
    activity.wait_for(state="visible", timeout=30_000)
    activity.locator("button").first.click()  # expand the archived activity
    page.wait_for_function(
        """(needle) => Array.from(document.querySelectorAll("div[role=status]"))
             .some((el) => (el.textContent || "").includes(needle))""",
        arg=expected_label,
        timeout=15_000,
    )
    recorder.check(
        f"tool_trail rebuilt in the UI (step row '{expected_label}' visible)", True
    )

    dump_sse(page, evidence_dir, "g5")
    screenshot(page, evidence_dir, "g5-refresh-history")


# ---------------------------------------------------------------------------
# Group 6 — reload mid-turn: replay=active resumes live, no duplicate bubbles
# ---------------------------------------------------------------------------


def test_g6_replay_active_mid_turn_reconnect(page, api, evidence_dir, recorder):
    open_session(page, api, "T7E2E G6 replay")
    send_via_ui(
        page,
        "Run this exact bash command: sleep 12. Then reply with exactly "
        f"REPLAY-OK. {NO_TODO}",
    )
    wait_for_sse(page, "tool_call", 180)
    dump_sse(page, evidence_dir, "g6-pre-reload")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => (window.__sseLog || []).some((e) => e.kind === 'ctor' && e.url.includes('replay=active'))",
        timeout=20_000,
    )
    recorder.check("reconnect opens SSE with replay=active", True)

    completed = wait_for_sse(page, "attempt.completed", 240)
    recorder.check(
        "live stream resumed to terminal after reconnect",
        completed.get("status") == "completed",
    )
    post_reload_events = [e for e in sse_log(page) if e.get("kind") == "event"]
    recorder.check("post-reload SSE frames received", len(post_reload_events) > 0)

    wait_answer_text(page, "REPLAY-OK", 30)
    bubbles = count_answer_bubbles(page, "REPLAY-OK")
    recorder.check(
        "exactly one answer bubble (no duplicates)", bubbles == 1, f"count={bubbles}"
    )

    dump_sse(page, evidence_dir, "g6-post-reload")
    screenshot(page, evidence_dir, "g6-replay-active")


# ---------------------------------------------------------------------------
# Group 7 — LONG turn with >90s content-silent gap (B3 E2E defense line)
# ---------------------------------------------------------------------------


def test_g7_long_silent_tool_beats_watchdog(page, api, i18n_en, evidence_dir, recorder):
    open_session(page, api, "T7E2E G7 long-silent")
    send_via_ui(
        page,
        "Run this exact bash command: sleep 105. Do not run any other command. "
        f"After it completes, reply with exactly LONG-OK. {NO_TODO}",
    )
    wait_for_sse(page, "tool_call", 180)
    tool_seen_at = time.monotonic()

    watchdog_s = 90  # VIBE_TRADING_SSE_TIMEOUT default (settings_routes.py:397)
    time.sleep(watchdog_s + 8)

    heartbeats = sse_events(page, "tool_heartbeat")
    recorder.check(
        "tool_heartbeat synthesized through the silent gap",
        len(heartbeats) >= 20,
        f"n={len(heartbeats)}",
    )
    elapsed = [float(h.get("elapsed_s") or 0) for h in heartbeats]
    recorder.check(
        "heartbeat elapsed_s crosses the 90s watchdog window",
        bool(elapsed) and max(elapsed) >= watchdog_s,
        f"max={max(elapsed) if elapsed else None}",
    )
    beats = [e for e in sse_log(page) if e.get("type") == "tool_heartbeat"]
    gaps = [(beats[i + 1]["t"] - beats[i]["t"]) / 1000.0 for i in range(len(beats) - 1)]
    if gaps:
        median_gap = statistics.median(gaps)
        recorder.check(
            "heartbeat cadence ~3s (D5)",
            1.5 <= median_gap <= 6.0,
            f"median={median_gap:.2f}s",
        )

    timeout_label = (
        _i18n(i18n_en, "agent", "activity", "timeout").split("{{elapsed}}")[0].strip()
    )
    timed_out_toast = _i18n(i18n_en, "agent", "executionTimedOut")
    text_now = body_text(page)
    recorder.check(
        "watchdog did NOT timeout-archive the turn",
        timeout_label not in text_now and timed_out_toast not in text_now,
    )
    recorder.check(
        "still streaming at t+98s (stop button present)",
        page.locator("button.bg-destructive").count() >= 1,
    )
    recorder.info("silent-gap dwell seconds", round(time.monotonic() - tool_seen_at, 1))
    screenshot(page, evidence_dir, "g7-mid-silent-gap")

    completed = wait_for_sse(page, "attempt.completed", 240)
    recorder.check(
        "post-terminal events not lost (attempt.completed after the gap)",
        completed.get("status") == "completed",
    )
    wait_answer_text(page, "LONG-OK", 60)
    recorder.check("final answer rendered after the silent gap", True)

    text_final = body_text(page)
    recorder.check(
        "turn archived done, never timeout",
        timeout_label not in text_final and timed_out_toast not in text_final,
    )
    dump_sse(page, evidence_dir, "g7")
    screenshot(page, evidence_dir, "g7-final")


# ---------------------------------------------------------------------------
# Group 8 — upload -> analyze: injected absolute-path resolution (B6 / D8②)
# ---------------------------------------------------------------------------

CSV_NAME = "t7e2e_data.csv"
CSV_ROWS = 10
CSV_CONTENT = (
    "symbol,close\n" + "".join(f"AAPL,{100 + i}.5\n" for i in range(CSV_ROWS))
).encode()


def test_g8_upload_analysis_absolute_path(page, api, rig_state, evidence_dir, recorder):
    sid = open_session(page, api, "T7E2E G8 upload")

    csv_path = evidence_dir / CSV_NAME
    csv_path.write_bytes(CSV_CONTENT)
    page.set_input_files("input[type=file]", str(csv_path))
    page.wait_for_selector(f"button[aria-label*='{CSV_NAME}']", timeout=30_000)
    recorder.check("upload accepted, attachment chip visible", True)

    send_via_ui(
        page,
        "Read the uploaded CSV file via its ABSOLUTE path (per the gateway "
        "context) with a file-reading tool, then reply with exactly: "
        "ROWS=<number of data rows> COLS=<comma-separated header column "
        f"names>. {NO_TODO}",
    )
    completed = wait_for_sse(page, "attempt.completed", 360)
    recorder.check(
        "upload-analysis turn completed", completed.get("status") == "completed"
    )

    # Governance note (T7 E2E finding): the frozen vibe-trading-tools.json
    # DISABLES the vt MCP read_file tool — the engine reads uploads with its
    # native read tool. B6's defense is the injected ABSOLUTE-path resolution
    # (the relative form misses against the engine workspace), so the
    # assertion is: SOME read-class tool consumed the absolute uploads path.
    uploads_dir = str(Path(rig_state["vt_home"]) / "uploads")
    tool_calls = sse_events(page, "tool_call")
    read_calls = [
        tc
        for tc in tool_calls
        if tc.get("tool") in ("read", "read_file", "read_document", "bash")
        and uploads_dir in json.dumps(tc.get("arguments") or {})
    ]
    recorder.check(
        "a read-class tool consumed the INJECTED ABSOLUTE uploads path (B6/D8②)",
        bool(read_calls),
        json.dumps([(tc.get("tool"), tc.get("arguments")) for tc in tool_calls])[:500],
    )
    read_tools = {tc.get("tool") for tc in read_calls}
    read_results = [
        tr for tr in sse_events(page, "tool_result") if tr.get("tool") in read_tools
    ]
    recorder.check(
        "the read succeeded (engine could actually read the file)",
        any(tr.get("status") == "ok" for tr in read_results),
        str([(tr.get("tool"), tr.get("status")) for tr in read_results]),
    )

    wait_answer_text(page, f"ROWS={CSV_ROWS}", 60)
    recorder.check("answer reports the correct row count", True)

    status, messages = api.request("GET", f"/sessions/{sid}/messages")
    assert status == 200
    users = [m for m in messages if m["role"] == "user"]
    recorder.check(
        "transcript keeps the relative uploads/<name> envelope (D3)",
        bool(users)
        and re.search(r"uploads/[\w.-]+\.csv", users[0]["content"]) is not None
        and CSV_NAME in users[0]["content"],
        (users[0]["content"][:200] if users else "no user message"),
    )
    recorder.check(
        "no gateway injection block in the transcript",
        bool(users) and "[gateway context]" not in users[0]["content"],
    )

    dump_sse(page, evidence_dir, "g8")
    screenshot(page, evidence_dir, "g8-upload-analysis")
