"""T14 goal session-binding E2E + model-compliance sampling (live rig).

Proves the F4 mitigation end to end on the opencode engine: the D8①
gateway-context injection (``service.py::_build_prompt_injection``) makes the
model pass ``session_id=`` to the vibe-trading MCP goal tools, so MCP-side
goal writes land on the vt session's goal and the Web UI goal panel sees
them.

Binding chain under test:
  gateway injection -> engine prompt -> model calls add_goal_evidence with
  session_id -> MCP ``_resolve_session_id`` honours the explicit id (the
  fallback chain stays FROZEN, mcp_server.py:350-389) -> GoalStore rejects a
  wrong session (store.py ``_require_mutable_goal``) -> REST re-fetch of
  ``/sessions/{sid}/goal`` shows the evidence -> panel renders after reload.

"Panel visible" is DEFINED as REST-refetch-visible (plan T14): MCP-side goal
writes cross the process boundary and do NOT emit gateway EventBus events,
so ``goal.*`` SSE stays silent during an agent turn — degradation item 12.
The silence is recorded as evidence, never asserted as a failure (a future
wiring that emits the events would be an improvement, not a regression).

Compliance sampling (plan T14: >=10 turns, <80% -> escalation): each turn
uses a FRESH session so the injection is the ONLY hint — no conversation
history to copy ``session_id`` from. Per turn we record whether the first
``add_goal_evidence`` call carried the correct ``session_id`` (raw engine
tool-part input) and whether the evidence landed (REST re-fetch — valid
ground truth because the GoalStore validates session ownership).

Run with the T7 rig up (README.md); skipped at collection unless
``ENGINE_BRIDGE_E2E=1`` (conftest). Knob: ``T14_COMPLIANCE_TURNS`` (default
12 >= the plan's 10-turn minimum).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional

from tests.e2e_engine_bridge.riglib import (
    dump_sse,
    engine_session_id,
    open_session,
    screenshot,
    send_via_ui,
    sse_types,
    wait_for_sse,
)

#: Single-shot cost discipline clause (T7 convention).
NO_TODO = "Do not create a todo list. Do not spawn subagents. Do not iterate."

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

E2E_EVIDENCE_TEXT = "T14-E2E-EVIDENCE goal binding verified"

#: The panel toggle's i18n aria-label (en.json agent.activeResearchGoal).
PANEL_ARIA = "Active research goal"


# ---------------------------------------------------------------------------
# Helpers (REST + engine-side inspection)
# ---------------------------------------------------------------------------


def _serve_get(rig_state: Dict[str, Any], path: str) -> Any:
    with urllib.request.urlopen(rig_state["serve_url"] + path, timeout=30) as r:
        return json.loads(r.read().decode())


def engine_messages(rig_state: Dict[str, Any], vt_sid: str) -> List[Dict[str, Any]]:
    esid = engine_session_id(rig_state, vt_sid)
    if not esid:
        return []
    return _serve_get(rig_state, f"/session/{esid}/message")


def engine_tool_parts(
    rig_state: Dict[str, Any], vt_sid: str, tool_suffix: str
) -> List[Dict[str, Any]]:
    """Engine tool parts whose tool id ends with *tool_suffix*, in order.

    MCP tools reach the model as ``vibe-trading_<name>`` (server prefix); the
    suffix match covers both the prefixed and a bare form.
    """
    parts: List[Dict[str, Any]] = []
    for entry in engine_messages(rig_state, vt_sid):
        for part in entry.get("parts", []):
            if part.get("type") == "tool" and str(part.get("tool", "")).endswith(
                tool_suffix
            ):
                parts.append(part)
    return parts


def engine_user_prompts(rig_state: Dict[str, Any], vt_sid: str) -> List[str]:
    texts: List[str] = []
    for entry in engine_messages(rig_state, vt_sid):
        info = entry.get("info") or {}
        if info.get("role") != "user":
            continue
        for part in entry.get("parts", []):
            if part.get("type") == "text" and part.get("text"):
                texts.append(str(part["text"]))
    return texts


def create_goal(api, sid: str, objective: str) -> Dict[str, Any]:
    status, body = api.request(
        "POST",
        f"/sessions/{sid}/goal",
        {
            "objective": objective,
            "ui_summary": objective[:80],
            "criteria": [
                "One evidence note recorded via the agent-side MCP goal tools"
            ],
        },
    )
    assert status == 201, f"goal create failed: {status} {body}"
    return body["goal"]


def wait_terminal_message(api, sid: str, attempt_id: str, timeout_s: float) -> Dict:
    """Poll REST until the assistant message for *attempt_id* is persisted.

    The bridge persists the assistant Message only at the terminal (D4), so
    its appearance with a terminal ``metadata.status`` is the completion
    signal for browserless turns.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status, messages = api.request("GET", f"/sessions/{sid}/messages?limit=50")
        if status == 200:
            for m in messages:
                if m.get("role") != "assistant":
                    continue
                if m.get("linked_attempt_id") != attempt_id:
                    continue
                meta = m.get("metadata") or {}
                if meta.get("status") in TERMINAL_STATUSES:
                    return m
        time.sleep(2.0)
    raise AssertionError(
        f"no terminal assistant message for attempt {attempt_id} within {timeout_s}s"
    )


def goal_snapshot(api, sid: str) -> tuple[int, Any]:
    return api.request("GET", f"/sessions/{sid}/goal")


def evidence_texts(snapshot: Dict[str, Any]) -> List[str]:
    return [str(e.get("text") or "") for e in snapshot.get("evidence", [])]


def kickoff_prompt(goal_id: str, note: str) -> str:
    """Minimal goal-touching prompt: one tool call, no research work.

    Deliberately does NOT mention session_id — the D8① injection is the only
    hint (the compliance question under test).
    """
    return (
        f"Record exactly ONE evidence note to research goal {goal_id} using "
        f"the add_goal_evidence tool, with text '{note}', goal_id "
        f"'{goal_id}' and expected_goal_id '{goal_id}'. Call the tool exactly "
        f"once, then reply with exactly: DONE. {NO_TODO}"
    )


def score_turn(
    rig_state: Dict[str, Any], api, sid: str, note: str, msg: Optional[Dict]
) -> Dict[str, Any]:
    """One compliance row: raw tool input + REST-refetch ground truth."""
    parts = engine_tool_parts(rig_state, sid, "add_goal_evidence")
    first = parts[0] if parts else None
    state = (first or {}).get("state") or {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    passed_raw = inp.get("session_id")
    passed = isinstance(passed_raw, str) and passed_raw.strip() != ""
    correct = passed and passed_raw.strip() == sid
    status, snapshot = goal_snapshot(api, sid)
    landed = status == 200 and any(note in t for t in evidence_texts(snapshot))
    return {
        "session_id": sid,
        "tool_calls": len(parts),
        "first_call_passed_session_id": passed,
        "first_call_session_id_correct": correct,
        "first_call_status": state.get("status"),
        "attempt_status": ((msg or {}).get("metadata") or {}).get("status"),
        "evidence_landed_via_rest": landed,
    }


# ---------------------------------------------------------------------------
# Test 1 — goal panel E2E: MCP-side evidence visible via REST re-fetch
# ---------------------------------------------------------------------------


def test_goal_panel_e2e_mcp_evidence_visible_via_rest(
    page, api, rig_state, evidence_dir, recorder
):
    sid = open_session(page, api, "T14 goal binding E2E")
    goal = create_goal(api, sid, "T14: verify goal session binding end to end")
    goal_id = goal["goal_id"]

    # The REST-created goal emits goal.created on the gateway bus (the
    # REST-originated path is live); the panel toggle appears without reload.
    toggle = page.locator(f"button[aria-label='{PANEL_ARIA}']")
    toggle.wait_for(state="visible", timeout=20_000)
    recorder.check("goal panel renders after REST goal creation", True)

    send_via_ui(page, kickoff_prompt(goal_id, E2E_EVIDENCE_TEXT))
    completed = wait_for_sse(page, "attempt.completed", 300)
    recorder.check(
        "kickoff turn completed",
        completed.get("status") == "completed",
        str(completed)[:200],
    )

    # D8① injection reached the engine prompt, live (not just unit-tested).
    prompts = engine_user_prompts(rig_state, sid)
    injected = [p for p in prompts if p.startswith("[gateway context]")]
    recorder.check("engine prompt carries the injection block", bool(injected))
    recorder.check(
        "injection names vt_session_id and the session_id= instruction",
        bool(injected)
        and f"vt_session_id={sid}" in injected[0]
        and f"session_id='{sid}'" in injected[0],
    )

    # D3 transcript ownership: the REST transcript stays raw.
    status, messages = api.request("GET", f"/sessions/{sid}/messages?limit=50")
    user_contents = [m["content"] for m in messages if m.get("role") == "user"]
    recorder.check(
        "transcript user message is raw (no injection block)",
        status == 200
        and user_contents
        and all("[gateway context]" not in c for c in user_contents),
    )

    # Engine-side: the model called add_goal_evidence with the correct id.
    parts = engine_tool_parts(rig_state, sid, "add_goal_evidence")
    recorder.check("add_goal_evidence tool call observed", bool(parts))
    state = (parts[0] if parts else {}).get("state") or {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    recorder.check(
        "model passed session_id=<vt sid> (injection was the only hint)",
        str(inp.get("session_id", "")).strip() == sid,
        json.dumps(inp)[:300],
    )
    recorder.check("tool call completed", state.get("status") == "completed")

    # Degradation item 12 (honest): MCP-side goal writes emit no gateway
    # EventBus events, so goal.* SSE stays silent during the agent turn.
    # Recorded, never asserted as failure — silence is the EXPECTED state.
    types = sse_types(page)
    goal_frames = sorted(t for t in types if t.startswith("goal."))
    recorder.info(
        "goal.* SSE frames during the agent turn (expected: only goal.created "
        "from the REST creation; NONE from the MCP-side evidence write — "
        "degradation item 12)",
        goal_frames,
    )
    dump_sse(page, evidence_dir, "goal-e2e")

    # The plan's binding definition of panel-visible: REST re-fetch.
    status, snapshot = goal_snapshot(api, sid)
    recorder.check(
        "REST re-fetch shows the MCP-written evidence",
        status == 200 and any(E2E_EVIDENCE_TEXT in t for t in evidence_texts(snapshot)),
        f"status={status}",
    )
    (evidence_dir / "goal-refetch.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # The open page did NOT live-update (no goal.evidence SSE): the toggle's
    # evidence-count badge stays absent until a reload (observation).
    toggle_text_before = toggle.inner_text()
    recorder.info(
        "panel toggle before reload (stale — no goal.* SSE from MCP write)",
        toggle_text_before.replace("\n", " | "),
    )
    recorder.info(
        "badge absent before reload",
        not re.search(r"\d+\s+evidence", toggle_text_before),
    )
    screenshot(page, evidence_dir, "goal-panel-stale-before-reload")

    # Reload -> session-load REST fetch -> panel renders the evidence.
    page.reload(wait_until="domcontentloaded")
    toggle = page.locator(f"button[aria-label='{PANEL_ARIA}']")
    toggle.wait_for(state="visible", timeout=20_000)
    toggle.click()
    page.wait_for_function(
        """(needle) => Array.from(document.querySelectorAll("div"))
             .some((el) => (el.textContent || "").includes(needle))""",
        arg=E2E_EVIDENCE_TEXT,
        timeout=15_000,
    )
    recorder.check("goal panel renders the evidence after reload", True)
    screenshot(page, evidence_dir, "goal-panel-evidence-after-reload")


# ---------------------------------------------------------------------------
# Test 2 — compliance sampling: >=10 fresh-session minimal goal turns
# ---------------------------------------------------------------------------


def test_compliance_sampling_session_id_passthrough(
    api, rig_state, evidence_dir, recorder
):
    n = int(os.environ.get("T14_COMPLIANCE_TURNS", "12"))
    assert n >= 10, "plan T14 requires >=10 sampled turns"
    rows: List[Dict[str, Any]] = []
    for i in range(1, n + 1):
        note = f"T14-C{i:02d} compliance sample note"
        status, body = api.request(
            "POST", "/sessions", {"title": f"T14 compliance {i:02d}"}
        )
        assert status in (200, 201), f"session create failed: {status} {body}"
        sid = body["session_id"]
        goal = create_goal(api, sid, f"T14 compliance sample {i:02d}: record one note")
        st, res = api.request(
            "POST",
            f"/sessions/{sid}/messages",
            {"content": kickoff_prompt(goal["goal_id"], note)},
        )
        assert st == 200, f"kickoff failed: {st} {res}"
        msg = None
        try:
            msg = wait_terminal_message(api, sid, res["attempt_id"], 300)
        except AssertionError as exc:
            rows.append(
                {
                    "turn": i,
                    "session_id": sid,
                    "goal_id": goal["goal_id"],
                    "note": note,
                    "error": str(exc),
                    "first_call_passed_session_id": False,
                    "first_call_session_id_correct": False,
                    "evidence_landed_via_rest": False,
                }
            )
            continue
        row = {"turn": i, "goal_id": goal["goal_id"], "note": note}
        row.update(score_turn(rig_state, api, sid, note, msg))
        rows.append(row)
        print(f"[t14] turn {i:02d}: {row}")

    compliant = sum(1 for r in rows if r.get("first_call_session_id_correct"))
    landed = sum(1 for r in rows if r.get("evidence_landed_via_rest"))
    rate = compliant / len(rows) if rows else 0.0
    summary = {
        "turns": len(rows),
        "session_id_passed_correctly_first_call": compliant,
        "evidence_landed_via_rest": landed,
        "passthrough_rate": round(rate, 4),
        "escalation_gate": ">=0.80 per plan T14; below -> alias-mapping escalation",
        "escalation_required": rate < 0.8,
        "rows": rows,
    }
    (evidence_dir / "compliance.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = [
        "# T14 compliance sampling — session_id passthrough (injection is the only hint)",
        "",
        f"Rate: **{compliant}/{len(rows)} = {rate:.0%}** (gate: >=80%). Landed via REST: {landed}/{len(rows)}.",
        "",
        "| turn | session_id | tool calls | 1st call passed sid | sid correct | evidence landed | attempt |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r.get('turn')} | {r.get('session_id', '')[:12]}… | {r.get('tool_calls', 0)} "
            f"| {r.get('first_call_passed_session_id')} | {r.get('first_call_session_id_correct')} "
            f"| {r.get('evidence_landed_via_rest')} | {r.get('attempt_status') or r.get('error', '')} |"
        )
    (evidence_dir / "compliance.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    recorder.info("compliance rate", f"{compliant}/{len(rows)} = {rate:.0%}")
    recorder.check(
        "every turn where the first call carried the correct session_id landed evidence",
        landed >= compliant,
        f"landed={landed} compliant={compliant}",
    )
    recorder.check(
        "session_id passthrough rate >= 80% (plan T14 escalation gate)",
        rate >= 0.8,
        f"rate={rate:.0%} — below 80% requires the alias-mapping escalation",
    )
