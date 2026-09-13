"""T13 — IM confirm-flow E2E on the opencode engine (live rig, real model).

The chain under test (plan T13 + D7's two-surface contract, IM side; the
Web SSE side is the ``scheduled_research.proposal`` relay frame in
``sessions_routes.py:238-262``, pinned by its own committed tests):

  IM user message -> ChannelRuntime -> bridge -> opencode engine -> the model
  calls the NEW MCP wrapper ``scheduled_research(action=propose_create,
  session_id=<vt sid>)`` (the D8① gateway-context injection is the only
  session_id hint — the prompt never mentions it) -> the proposal lands on
  disk bound to the vt session (MCP process, rig vt-home) -> the UNCHANGED
  runtime appends the confirmation card to the reply (runtime.py:203-217) ->
  ``proposal_id`` sits inside the FIRST 200 CHARS of the raw engine tool-part
  output (D5 preview truncation + the relay regex constraint, asserted on the
  live wire) -> the user replies exactly "确认" -> the intercept commits
  (runtime.py:271-320, outside the model) -> the scheduled job record exists.

Executor gating is REAL, not stubbed: ``commit_proposal`` requires
``scheduler_status()["executable"]``; the test enables the executor the same
way the gateway startup does (``VIBE_TRADING_ENABLE_SCHEDULER`` env +
``scheduled_routes._get_scheduled_research_executor().start()``). The job's
cron (``0 8 * * *``) never comes due inside the test window, so no dispatch
fires.

Run through the T13 runner (ports 14098/18082, /tmp/vt-t13-rig — T8's
14096/18080 and T11's 28080-28082 are never touched)::

    python3 agent/tests/e2e_engine_bridge/run_t13_confirm_flow.py

Gated on ``ENGINE_BRIDGE_E2E=1``; cost discipline: ONE model turn (the
"确认" commit is intercepted outside the model), single-shot prompt clause.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tests.e2e_engine_bridge.imlib import (
    build_im_stack,
    close_im_stack,
    copy_store_artifacts,
    engine_session_id_for,
    write_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

RIG_ENABLED = os.environ.get("ENGINE_BRIDGE_E2E") == "1"
RIG_STATE_PATH = Path(
    os.environ.get("ENGINE_BRIDGE_E2E_RIG_STATE", "/tmp/vt-t13-rig/rig_state.json")
)
EVIDENCE_DIR = Path(
    os.environ.get(
        "ENGINE_BRIDGE_E2E_EVIDENCE",
        str(
            REPO_ROOT
            / ".omo"
            / "evidence"
            / "opencode-engine-bridge-v2"
            / "t13-wrapper"
        ),
    )
)

#: The relay's own regex (sessions_routes.py:192) — the consumer contract.
SRP_RE = re.compile(r'"proposal_id"\s*:\s*"(srp_[0-9a-f]{32})"')
PREVIEW_LEN = 200  # plan D5: preview = state.output[:200]

PROPOSE_TIMEOUT_S = float(os.environ.get("T13_PROPOSE_TIMEOUT_S", "420"))

CHAT_ID = "t13-confirm-chat"

PROMPT = (
    "Use the scheduled_research tool exactly once with action='propose_create' "
    "to propose a scheduled research job with this draft: title "
    "'T13 confirm-flow probe', source kind 'prompt' with prompt 'Summarize "
    "today's market moves', schedule expression '0 8 * * *' with timezone "
    "'Asia/Shanghai', end_at null, delivery mode 'in_app'. Then reply with "
    "exactly: PROPOSED. Do not create a todo list. Do not spawn subagents. "
    "Do not iterate."
)

requires_rig = pytest.mark.skipif(
    not RIG_ENABLED,
    reason="T13 confirm-flow E2E needs the live rig: ENGINE_BRIDGE_E2E=1 + "
    "run_t13_confirm_flow.py (ports 14098/18082)",
)


@pytest.fixture(scope="module")
def rig_state() -> Dict[str, Any]:
    if not RIG_STATE_PATH.exists():
        pytest.fail(f"rig state {RIG_STATE_PATH} missing — run the T13 runner")
    state = json.loads(RIG_STATE_PATH.read_text(encoding="utf-8"))
    # Port-ownership guard (rig isolation): this suite only ever touches its
    # own rig — never T8's 14096/18080 or T11's 28080-28082.
    assert state["serve_url"].endswith(":14098"), f"unexpected serve: {state}"
    assert state["gateway_url"].endswith(":18082"), f"unexpected gateway: {state}"
    return state


@pytest.fixture(scope="module")
def evidence_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


@pytest.fixture()
def scheduler_enabled(monkeypatch, rig_state):
    """Enable the REAL executor path + align the runtime root with the rig.

    ``VIBE_TRADING_HOME`` must point at the rig's vt-home: the MCP server
    process (spawned by the rig's opencode serve) writes proposals there, and
    this pytest process must read/commit the SAME files. The conftest autouse
    ``_reset_env_config`` fixture makes the env var effective on the next
    ``get_env_config()``.
    """
    from src.api import scheduled_routes

    monkeypatch.setenv("VIBE_TRADING_HOME", rig_state["vt_home"])
    monkeypatch.setenv("VIBE_TRADING_ENABLE_SCHEDULER", "true")
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", None)
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_executor", None)
    yield
    # The executor singleton (if started inside the scenario) is stopped by
    # the scenario itself; the monkeypatched module globals restore on teardown.


def _serve_get(rig_state: Dict[str, Any], path: str) -> Any:
    with urllib.request.urlopen(rig_state["serve_url"] + path, timeout=30) as r:
        return json.loads(r.read().decode())


def _engine_tool_parts(
    rig_state: Dict[str, Any], stack, vt_sid: str, tool_suffix: str
) -> List[Dict[str, Any]]:
    """Raw engine tool parts whose tool id ends with *tool_suffix* (T14 pattern)."""
    esid = engine_session_id_for(stack, vt_sid)
    if not esid:
        return []
    parts: List[Dict[str, Any]] = []
    for entry in _serve_get(rig_state, f"/session/{esid}/message"):
        for part in entry.get("parts", []):
            if part.get("type") == "tool" and str(part.get("tool", "")).endswith(
                tool_suffix
            ):
                parts.append(part)
    return parts


@requires_rig
def test_im_confirm_flow_live_model(rig_state, evidence_dir, scheduler_enabled) -> None:
    from src.api import scheduled_routes
    from src.scheduled_research.proposals import latest_pending_for_session
    from src.scheduled_research.store import ScheduledResearchJobStore

    scratch_root = Path(rig_state["rig_root"]) / "im-stores"
    scratch_root.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {"checks": {}}

    def check(name: str, cond: bool, detail: str = "") -> None:
        out["checks"][name] = {"pass": bool(cond), "detail": detail}
        assert cond, f"[t13-confirm] {name}: {detail}"

    async def scenario() -> None:
        stack = await build_im_stack(
            serve_url=rig_state["serve_url"],
            store_dir=scratch_root / "t13-confirm",
            session_map_path=scratch_root / "t13-confirm-map.json",
            registry_path=evidence_dir / "engine-sessions.jsonl",
            scenario="t13-confirm",
        )
        executor = None
        try:
            # The real gateway-startup executor path (scheduled_routes.py:163-167).
            executor = scheduled_routes._get_scheduled_research_executor()
            executor.start()

            t0 = time.time()
            await stack.mock.inject(PROMPT, chat_id=CHAT_ID, message_id="t13-propose-1")
            reply = await stack.mock.next_for(CHAT_ID, timeout_s=PROPOSE_TIMEOUT_S)
            out["propose_elapsed_s"] = round(time.time() - t0, 2)
            out["reply_content"] = reply.content
            vt_sid = reply.metadata.get("session_id")
            out["vt_session_id"] = vt_sid
            check(
                "reply carries the vt session id",
                isinstance(vt_sid, str) and bool(vt_sid),
            )

            # 1. The runtime appended the confirmation card (runtime.py:203-217,
            #    unchanged channels/ code) — proof the proposal bound to the vt
            #    session and the intercept surface saw it.
            check(
                "confirmation card appended to the IM reply",
                "[Scheduled research confirmation · create]" in reply.content
                and "confirm" in reply.content,
                reply.content[-400:],
            )

            # 2. The model called the NEW wrapper and passed session_id from the
            #    D8① injection (the prompt never mentions session_id).
            parts = _engine_tool_parts(rig_state, stack, vt_sid, "scheduled_research")
            check("scheduled_research tool call observed on the engine", bool(parts))
            state = (parts[0] if parts else {}).get("state") or {}
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            out["tool_input"] = inp
            check(
                "model passed session_id=<vt sid> (injection was the only hint)",
                str(inp.get("session_id", "")).strip() == vt_sid,
                json.dumps(inp)[:300],
            )
            check(
                "action=propose_create reached the wrapper",
                str(inp.get("action", "")) == "propose_create",
                json.dumps(inp)[:200],
            )

            # 3. D5 preview contract on the live wire: proposal_id inside the
            #    FIRST 200 CHARS of the raw tool output (the relay regex's reach).
            raw_output = str(state.get("output") or "")
            out["tool_output_head"] = raw_output[:400]
            preview = raw_output[:PREVIEW_LEN]
            match = SRP_RE.search(preview)
            check(
                "proposal_id inside the 200-char preview (relay regex hits)",
                match is not None,
                preview,
            )

            # 4. The proposal is on disk, pending, bound to the vt session.
            proposal = latest_pending_for_session(vt_sid)
            check("pending proposal bound to the vt session", proposal is not None)
            proposal_id = (proposal or {}).get("proposal_id")
            out["proposal"] = proposal
            check(
                "preview proposal_id == on-disk proposal",
                match is not None
                and proposal is not None
                and match.group(1) == proposal_id,
            )

            # 5. The user confirms with the exact Chinese token; the intercept
            #    commits OUTSIDE the model (no second engine turn).
            turns_before = len(stack.service.get_messages(vt_sid, limit=200))
            await stack.mock.inject("确认", chat_id=CHAT_ID, message_id="t13-confirm-1")
            confirm_reply = await stack.mock.next_for(CHAT_ID, timeout_s=60)
            out["confirm_reply_content"] = confirm_reply.content
            check(
                "commit acknowledgement delivered",
                "Scheduled research job created" in confirm_reply.content,
                confirm_reply.content[:200],
            )
            check(
                "confirm was intercepted outside the model (no new turn)",
                len(stack.service.get_messages(vt_sid, limit=200)) == turns_before,
            )

            # 6. The task record exists (the plan's "assert the task record").
            job_id_m = re.search(r"created:\s*(\S+)", confirm_reply.content)
            job_id = job_id_m.group(1) if job_id_m else None
            store = ScheduledResearchJobStore()  # rig vt-home via env
            job = store.get(job_id) if job_id else None
            out["job"] = job.to_dict() if job else None
            check("job id parsed from the acknowledgement", job_id is not None)
            check("scheduled job record exists in the store", job is not None)
            if job is not None:
                check(
                    "job carries the proposed title",
                    job.title == "T13 confirm-flow probe",
                    job.title,
                )
                check(
                    "job is pending with the proposed cron",
                    job.status.value == "pending" and job.schedule == "0 8 * * *",
                    f"{job.status.value}/{job.schedule}",
                )

            copy_store_artifacts(stack, evidence_dir, "t13-confirm")
        finally:
            if executor is not None:
                await executor.stop()
            await close_im_stack(stack)

    asyncio.run(scenario())
    write_json(evidence_dir / "t13-confirm-flow-results.json", out)
    failed = [k for k, v in out["checks"].items() if not v["pass"]]
    assert not failed, f"[t13-confirm] failed checks: {failed}"
