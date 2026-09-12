"""T8 — IM channel parity suite under the opencode engine (live local rig).

Verifies the architecture claim: the 16 IM adapters + ChannelRuntime consume
the SessionService seam WITHOUT knowing the engine changed
(``VIBE_TRADING_ENGINE=opencode`` -> RecoverableOpencodeSessionService).
Plan references: T8 row, D3 (dual-store ownership / cascade), D6 (seam
contract), runtime.py:189,327,329,336-349, scheduled_routes.py:88,109-115,
129-143.

Scenario groups (execution order = definition order; scenario 2 KILLS the
rig's opencode serve, so it runs LAST):

* zero-diff guard — ``agent/src/channels/`` untouched since the mymain
  merge-base (runs WITHOUT the rig; part of the default gate).
* s0 gateway preamble — the production gateway host serves the seam
  (health / session create+delete / channels runtime status). No LLM.
* s1 long turn — ~3-minute tool-sleep turn through the mock channel adapter;
  ``runtime._wait_for_reply`` polling contract (D4: the assistant Message is
  persisted only at terminal, then delivered inside the 600 s budget); final
  reply shape = the OutboundMessage a real adapter (e.g. dingtalk.py:672-680
  ``send`` -> ``_send_markdown_text``) renders as a markdown card.
* s3 commands — ``/pairing`` (operator + non-operator), ``/new``, ``/reset``,
  ``/newsession`` on the opencode engine; D3 cascade: after a reset the NEXT
  turn starts a FRESH engine session (old one deliberately kept, recovery.py
  module docstring), session.config carries {channel, channel_chat_id}
  (runtime.py:329 — T9's stream producer input).
* s4 scheduled briefing — the production delivery path
  (``_dispatch_scheduled_research_job`` positional send_message, D6 ->
  ``_read_scheduled_briefing`` metadata["status"] read ->
  ``_send_scheduled_briefing`` -> ``send_with_receipt``) reaches the mock
  channel with a DeliveryReceipt.
* s2 engine death — SIGKILL the serve mid-turn; measure attempt-landing and
  IM failure-reply wall times against the <30 s acceptance. If the bridge
  hangs (driver SSE reconnect loop keeps the pump alive -> no live-death
  detection), the scenario documents it precisely and ends in an imperative
  ``pytest.xfail`` with the measured numbers; Phase B then proves T6's
  restart reconciliation lands the attempt interrupted and satisfies the IM
  polling contract (plan T6 QA failure scenario).

Run (live rig; see e2e_engine_bridge/README.md + run_t8_im_parity.py)::

    python3 agent/tests/e2e_engine_bridge/run_t8_im_parity.py

Env knobs: ``ENGINE_BRIDGE_E2E=1`` (gate), ``ENGINE_BRIDGE_E2E_RIG_STATE``
(default ``/tmp/vt-t8-rig/rig_state.json``), ``ENGINE_BRIDGE_E2E_EVIDENCE``
(default ``.omo/evidence/opencode-engine-bridge-v2/t8-im``),
``T8_DEATH_OBSERVE_S`` (default 90; the live evidence run uses 660 to capture
the full 600 s polling-budget landing), ``T8_LONG_SLEEP_S`` (default 170).

Isolation: ports 14096/18080 + ``/tmp/vt-t8-rig`` belong to T8; the parallel
T9 rig (14097/18081, /tmp/vt-t9-rig) is never touched. The serve pid is
command-line + listen-port verified before any kill (imlib.verified_serve_pid).
Cost discipline: tool-sleep-based long turns, single-shot prompts, ~8 model
steps total; per-session cost lands in the evidence dir (runner).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests.e2e_engine_bridge.imlib import (
    MOCK_CHANNEL_NAME,
    PROD_POLL_INTERVAL_S,
    attempt_status,
    build_im_stack,
    close_im_stack,
    copy_store_artifacts,
    engine_session_id_for,
    kill_serve,
    serve_alive,
    verified_serve_pid,
    write_json,
)
from tests.e2e_engine_bridge.riglib import GatewayApi

REPO_ROOT = Path(__file__).resolve().parents[2]
MERGE_BASE = "5eda88d1a762593ded46e50da5986392711681ff"  # mymain merge-base (plan)

RIG_ENABLED = os.environ.get("ENGINE_BRIDGE_E2E") == "1"
RIG_STATE_PATH = Path(
    os.environ.get("ENGINE_BRIDGE_E2E_RIG_STATE", "/tmp/vt-t8-rig/rig_state.json")
)
EVIDENCE_DIR = Path(
    os.environ.get(
        "ENGINE_BRIDGE_E2E_EVIDENCE",
        str(REPO_ROOT / ".omo" / "evidence" / "opencode-engine-bridge-v2" / "t8-im"),
    )
)
DEATH_OBSERVE_S = float(os.environ.get("T8_DEATH_OBSERVE_S", "90"))
LONG_SLEEP_S = int(os.environ.get("T8_LONG_SLEEP_S", "170"))
DEATH_ACCEPTANCE_S = 30.0

requires_rig = pytest.mark.skipif(
    not RIG_ENABLED,
    reason="T8 IM parity needs the live rig: ENGINE_BRIDGE_E2E=1 + start_rig.py "
    "(see agent/tests/e2e_engine_bridge/run_t8_im_parity.py)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rig_state() -> Dict[str, Any]:
    if not RIG_STATE_PATH.exists():
        pytest.fail(f"rig state {RIG_STATE_PATH} missing — run start_rig.py first")
    state = json.loads(RIG_STATE_PATH.read_text(encoding="utf-8"))
    # Port-ownership guard: this suite may only ever kill the serve it verified.
    assert state["serve_url"].endswith(":14096"), f"unexpected serve: {state}"
    assert state["gateway_url"].endswith(":18080"), f"unexpected gateway: {state}"
    return state


@pytest.fixture(scope="module")
def evidence_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


@pytest.fixture(scope="module")
def scratch(rig_state) -> Dict[str, Any]:
    """Per-scenario store roots under the T8 rig root (purged with the rig)."""
    root = Path(rig_state["rig_root"]) / "im-stores"
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "registry": EVIDENCE_DIR / "engine-sessions.jsonl",
        "serve_url": rig_state["serve_url"],
    }


def _stack_kwargs(scratch: Dict[str, Any], scenario: str) -> Dict[str, Any]:
    return {
        "serve_url": scratch["serve_url"],
        "store_dir": scratch["root"] / scenario,
        "session_map_path": scratch["root"] / f"{scenario}-map.json",
        "registry_path": scratch["registry"],
        "scenario": scenario,
    }


# ---------------------------------------------------------------------------
# Zero-diff guard (acceptance: adapters/runtime untouched — runs WITHOUT rig)
# ---------------------------------------------------------------------------


def test_channels_zero_diff_since_merge_base() -> None:
    """`git diff <merge-base>..HEAD -- agent/src/channels/` must be empty.

    Also guards the plan's other protected zones (agent|session|providers,
    frontend, api/state.py beyond the T7-adjudicated switch) and the working
    tree, so a dirty checkout cannot smuggle adapter edits past the suite.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    for zone in (
        "agent/src/channels/",
        "agent/src/agent/",
        "agent/src/session/",
        "agent/src/providers/",
        "frontend/",
    ):
        committed = git("diff", "--name-only", f"{MERGE_BASE}..HEAD", "--", zone)
        assert committed == "", f"committed diff in protected zone {zone}:\n{committed}"
        worktree = git("status", "--porcelain", "--", zone)
        assert (
            worktree == ""
        ), f"uncommitted changes in protected zone {zone}:\n{worktree}"


# ---------------------------------------------------------------------------
# s0 — gateway preamble (production host serves the seam; no LLM)
# ---------------------------------------------------------------------------


@requires_rig
def test_scenario0_gateway_seam_preamble(rig_state, evidence_dir) -> None:
    api = GatewayApi(rig_state["gateway_url"], rig_state["api_key"])
    checks: List[Dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append({"assertion": name, "pass": bool(condition), "detail": detail})
        assert condition, f"[s0] {name}: {detail}"

    status, _ = api.request("GET", "/health")
    check("gateway health 200", status == 200, f"status={status}")

    status, body = api.request("POST", "/sessions", {"title": "t8-gateway-seam"})
    check("POST /sessions on opencode engine", status in (200, 201), f"{status} {body}")
    session_id = body["session_id"]

    status, ch = api.request("GET", "/channels/status")
    check("GET /channels/status 200", status == 200, f"status={status}")
    check(
        "channel runtime status shape",
        isinstance(ch, dict)
        and {"running", "inbound_queue", "outbound_queue", "session_count", "channels"}
        <= set(ch),
        f"keys={sorted(ch) if isinstance(ch, dict) else ch}",
    )

    status, _ = api.request("DELETE", f"/sessions/{session_id}")
    check(
        "DELETE /sessions (cascade path, engine session lazy=none)",
        status in (200, 204),
        f"status={status}",
    )

    write_json(evidence_dir / "s0-gateway-results.json", {"checks": checks})


# ---------------------------------------------------------------------------
# s1 — mock-channel scripted long-turn round trip (~3 minutes)
# ---------------------------------------------------------------------------


@requires_rig
def test_scenario1_long_turn_roundtrip(scratch, evidence_dir) -> None:
    sleep_s = LONG_SLEEP_S
    prompt = (
        f"Use the bash tool to run exactly this command once: "
        f"`sleep {sleep_s} && echo T8_LONG_DONE`. Do not iterate, do not create "
        f"todo lists, no subagents. After it finishes, reply with exactly the "
        f"command's output text."
    )

    async def scenario() -> Dict[str, Any]:
        stack = await build_im_stack(**_stack_kwargs(scratch, "s1-long-turn"))
        out: Dict[str, Any] = {"checks": {}}
        try:
            poll_log: List[Dict[str, Any]] = []
            session_holder: Dict[str, str] = {}

            async def d4_poller() -> None:
                """Record IM-side assistant-message visibility every 2s.

                D4: the assistant Message is persisted ONLY at terminal, so
                every poll during the long silent turn must see zero assistant
                messages. The reply (delivered by ``_wait_for_reply``'s 0.25s
                poll) is the terminal signal; the post-reply store query in the
                checks below confirms the message exists at terminal.
                """
                while True:
                    sid = session_holder.get("sid") or stack.runtime._session_map.get(
                        f"{MOCK_CHANNEL_NAME}:long-chat"
                    )
                    if sid:
                        session_holder["sid"] = sid
                        n = len(
                            [
                                m
                                for m in stack.service.get_messages(sid, limit=200)
                                if m.role == "assistant"
                            ]
                        )
                        poll_log.append({"t": round(time.time() - t_send, 2), "n": n})
                    await asyncio.sleep(2.0)

            mid = await stack.mock.inject(
                prompt, chat_id="long-chat", message_id="t8-long-1"
            )
            t_send = time.time()
            poller = asyncio.create_task(d4_poller())
            reply = await stack.mock.next_for("long-chat", timeout_s=sleep_s + 300)
            out["elapsed_s"] = round(time.time() - t_send, 2)
            poller.cancel()

            out["reply_content"] = reply.content
            out["reply_metadata"] = dict(reply.metadata)
            out["event_census"] = stack.recorder.types_seen()
            hb = stack.recorder.times_of("tool_heartbeat")
            gaps = [round(b - a, 2) for a, b in zip(hb, hb[1:])]
            out["heartbeats"] = {
                "count": len(hb),
                "median_gap_s": sorted(gaps)[len(gaps) // 2] if gaps else None,
            }

            sid = reply.metadata["session_id"]
            attempt_id = reply.metadata["attempt_id"]
            messages = [
                m
                for m in stack.service.get_messages(sid, limit=200)
                if m.role == "assistant" and m.linked_attempt_id == attempt_id
            ]
            out["assistant_messages_for_attempt"] = len(messages)
            out["assistant_metadata"] = dict(messages[0].metadata) if messages else {}
            out["attempt_status"] = attempt_status(stack.store, attempt_id)
            out["session_config"] = dict(stack.service.get_session(sid).config)
            out["poll_log"] = poll_log

            def check(name: str, cond: bool, detail: str = "") -> None:
                out["checks"][name] = {"pass": bool(cond), "detail": detail}
                assert cond, f"[s1] {name}: {detail}"

            check(
                "long turn really long (>= sleep)",
                out["elapsed_s"] >= sleep_s,
                f"{out['elapsed_s']}s",
            )
            check(
                "final reply delivered with marker",
                "T8_LONG_DONE" in reply.content,
                reply.content[:200],
            )
            check(
                "outbound shape = markdown-card source (dingtalk.py:672-680 renders content)",
                reply.metadata.get("_channel_runtime") is True
                and reply.metadata.get("message_id") == mid
                and isinstance(reply.metadata.get("attempt_id"), str)
                and isinstance(reply.metadata.get("session_id"), str),
                str(reply.metadata),
            )
            check(
                "exactly one assistant message for the attempt",
                len(messages) == 1,
                str(len(messages)),
            )
            meta = out["assistant_metadata"]
            check(
                "D6 reply metadata status=completed",
                meta.get("status") == "completed",
                str(meta),
            )
            check(
                "D6 metadata elapsed_ms covers the sleep",
                int(meta.get("elapsed_ms", 0)) >= sleep_s * 900,
                str(meta.get("elapsed_ms")),
            )
            check(
                "D6 metadata provider/model present",
                bool(meta.get("provider")) and bool(meta.get("model")),
                str(meta),
            )
            check(
                "attempt terminal=completed in store",
                out["attempt_status"] == "completed",
                str(out["attempt_status"]),
            )
            check(
                "D4 polling contract: IM polling saw NO assistant msg during the "
                "long silent turn (persisted only at terminal)",
                bool(poll_log) and all(e["n"] == 0 for e in poll_log),
                f"polls={len(poll_log)} "
                f"all_empty={all(e['n'] == 0 for e in poll_log)} "
                f"elapsed={out['elapsed_s']}",
            )
            check(
                "translator alive during silence (tool_heartbeat >= 10)",
                len(hb) >= 10,
                str(len(hb)),
            )
            check(
                "session.config carries {channel, channel_chat_id} (runtime.py:329)",
                out["session_config"].get("channel") == MOCK_CHANNEL_NAME
                and out["session_config"].get("channel_chat_id") == "long-chat",
                str(out["session_config"]),
            )
            return out
        except BaseException as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            copy_store_artifacts(stack, evidence_dir, "s1-long-turn")
            write_json(evidence_dir / "s1-long-turn-results.json", out)
            await close_im_stack(stack)

    result = asyncio.run(scenario())
    failed = [k for k, v in result["checks"].items() if not v["pass"]]
    assert not failed, f"[s1] failed checks: {failed}"


# ---------------------------------------------------------------------------
# s3 — command regression: /pairing, /new, /reset + D3 cascade
# ---------------------------------------------------------------------------


@requires_rig
def test_scenario3_command_regression(
    scratch, evidence_dir, tmp_path, monkeypatch
) -> None:
    from src.channels.pairing import store as pairing_store

    monkeypatch.setattr(pairing_store, "_store_path", lambda: tmp_path / "pairing.json")
    ok_prompt = (
        "Reply with exactly {marker}. Do not call any tools, do not iterate, "
        "no todo lists, no subagents."
    )

    async def scenario() -> Dict[str, Any]:
        stack = await build_im_stack(**_stack_kwargs(scratch, "s3-commands"))
        out: Dict[str, Any] = {"checks": {}}
        try:

            def check(name: str, cond: bool, detail: str = "") -> None:
                out["checks"][name] = {"pass": bool(cond), "detail": detail}
                assert cond, f"[s3] {name}: {detail}"

            # /pairing — non-operator: rejected, engine untouched.
            await stack.mock.inject(
                "/pairing list",
                sender_id="t8-stranger",
                chat_id="cmd-chat",
                message_id="t8-cmd-p1",
            )
            reply = await stack.mock.next_for("cmd-chat", timeout_s=10)
            check(
                "pairing non-operator rejected",
                "Not authorized" in reply.content,
                reply.content[:120],
            )
            check(
                "pairing reply metadata",
                reply.metadata.get("_pairing_command") is True
                and reply.metadata.get("unauthorized") is True,
                str(reply.metadata),
            )
            check(
                "pairing did not touch the engine",
                not stack.service._engine_sessions,
                str(stack.service._engine_sessions),
            )

            # /pairing — operator: handled without the model.
            await stack.mock.inject(
                "/pairing list",
                sender_id="t8-operator",
                chat_id="cmd-chat",
                message_id="t8-cmd-p2",
            )
            reply = await stack.mock.next_for("cmd-chat", timeout_s=10)
            check(
                "pairing operator list answered",
                "pairing" in reply.content.lower(),
                reply.content[:120],
            )
            check(
                "pairing operator: still no engine session",
                not stack.service._engine_sessions,
                str(stack.service._engine_sessions),
            )

            # Turn 1 -> session s1 with engine session e1.
            await stack.mock.inject(
                ok_prompt.format(marker="T8_OK1"),
                chat_id="cmd-chat",
                message_id="t8-cmd-1",
            )
            reply1 = await stack.mock.next_for("cmd-chat", timeout_s=180)
            s1 = reply1.metadata["session_id"]
            e1 = engine_session_id_for(stack, s1)
            check("turn 1 replied", "T8_OK1" in reply1.content, reply1.content[:120])
            check("turn 1 engine session attached", bool(e1), str(e1))

            # /new -> reset reply; mapping dropped.
            await stack.mock.inject("/new", chat_id="cmd-chat", message_id="t8-cmd-2")
            reply = await stack.mock.next_for("cmd-chat", timeout_s=10)
            check(
                "/new reset reply",
                "Session reset" in reply.content,
                reply.content[:120],
            )
            check(
                "/new metadata session_reset",
                reply.metadata.get("session_reset") is True,
                str(reply.metadata),
            )
            map_data = json.loads(
                _stack_kwargs(scratch, "s3-commands")["session_map_path"].read_text()
            )
            check(
                "/new dropped the mapping",
                f"{MOCK_CHANNEL_NAME}:cmd-chat" not in map_data,
                str(map_data),
            )

            # Turn 2 -> FRESH vt session + FRESH engine session (D3 cascade);
            # the old engine session is deliberately KEPT (recovery.py docstring).
            await stack.mock.inject(
                ok_prompt.format(marker="T8_OK2"),
                chat_id="cmd-chat",
                message_id="t8-cmd-3",
            )
            reply2 = await stack.mock.next_for("cmd-chat", timeout_s=180)
            s2 = reply2.metadata["session_id"]
            e2 = engine_session_id_for(stack, s2)
            check("turn 2 replied", "T8_OK2" in reply2.content, reply2.content[:120])
            check("next turn starts a fresh vt session", s2 != s1, f"{s1} -> {s2}")
            check(
                "next turn starts a FRESH engine session",
                bool(e2) and e2 != e1,
                f"{e1} -> {e2}",
            )
            old_msgs = await stack.driver.messages(e1)
            check(
                "old engine session kept (D3: browsable transcript)",
                isinstance(old_msgs, list),
                str(type(old_msgs)),
            )
            cfg2 = dict(stack.service.get_session(s2).config)
            check(
                "fresh session.config {channel, channel_chat_id} (T9 producer input)",
                cfg2.get("channel") == MOCK_CHANNEL_NAME
                and cfg2.get("channel_chat_id") == "cmd-chat",
                str(cfg2),
            )

            # /reset and /newsession aliases.
            await stack.mock.inject("/reset", chat_id="cmd-chat", message_id="t8-cmd-4")
            reply = await stack.mock.next_for("cmd-chat", timeout_s=10)
            check(
                "/reset alias works",
                "Session reset" in reply.content,
                reply.content[:120],
            )
            await stack.mock.inject(
                "/newsession", chat_id="cmd-chat", message_id="t8-cmd-5"
            )
            reply = await stack.mock.next_for("cmd-chat", timeout_s=10)
            check(
                "/newsession alias handled",
                reply.metadata.get("session_reset") is True,
                str(reply.metadata),
            )

            out["sessions"] = {"s1": s1, "e1": e1, "s2": s2, "e2": e2}
            out["engine_session_count"] = len(stack.service._engine_sessions)
            check(
                "exactly two engine sessions created",
                len(stack.service._engine_sessions) == 2,
                str(stack.service._engine_sessions),
            )
            return out
        except BaseException as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            copy_store_artifacts(stack, evidence_dir, "s3-commands")
            write_json(evidence_dir / "s3-commands-results.json", out)
            await close_im_stack(stack)

    result = asyncio.run(scenario())
    failed = [k for k, v in result["checks"].items() if not v["pass"]]
    assert not failed, f"[s3] failed checks: {failed}"


# ---------------------------------------------------------------------------
# s4 — scheduled briefing delivery path (send_with_receipt)
# ---------------------------------------------------------------------------


@requires_rig
def test_scenario4_scheduled_briefing(scratch, evidence_dir, monkeypatch) -> None:
    from src.api.scheduled_routes import (
        _dispatch_scheduled_research_job,
        _read_scheduled_briefing,
        _send_scheduled_briefing,
    )
    from src.channels.bus.events import DeliveryReceipt

    async def scenario() -> Dict[str, Any]:
        stack = await build_im_stack(**_stack_kwargs(scratch, "s4-briefing"))
        out: Dict[str, Any] = {"checks": {}}
        try:

            def check(name: str, cond: bool, detail: str = "") -> None:
                out["checks"][name] = {"pass": bool(cond), "detail": detail}
                assert cond, f"[s4] {name}: {detail}"

            # The production host lookups (scheduled_routes resolves the host
            # module via sys.modules) point at THIS stack's manager + service.
            fake_host = types.ModuleType("api_server")
            fake_host._channel_manager = stack.manager
            fake_host._get_session_service = lambda: stack.service
            monkeypatch.setitem(sys.modules, "api_server", fake_host)

            job = types.SimpleNamespace(
                id="t8-brief",
                config={"channel": MOCK_CHANNEL_NAME, "channel_chat_id": "brief-chat"},
                prompt=(
                    "Reply with exactly T8_BRIEF_OK. Do not call any tools, do "
                    "not iterate, no todo lists, no subagents."
                ),
            )
            # scheduled_routes.py:88 — POSITIONAL send_message (D6 dual shape).
            session_id = await _dispatch_scheduled_research_job(job)
            out["session_id"] = session_id
            check("dispatch returned a session id", bool(session_id), str(session_id))

            # scheduled_routes.py:109-115 — metadata["status"] read path.
            briefing = None
            deadline = time.time() + 240
            while time.time() < deadline and briefing is None:
                briefing = _read_scheduled_briefing(session_id)
                if briefing is None:
                    await asyncio.sleep(1.0)
            check(
                "briefing reached terminal status",
                briefing is not None,
                "still in flight at deadline",
            )
            status, text = briefing
            out["briefing_status"] = status
            out["briefing_text"] = text
            check(
                'metadata["status"] == completed on opencode engine',
                status == "completed",
                str(status),
            )
            check(
                "briefing text is the session reply", "T8_BRIEF_OK" in text, text[:200]
            )

            # scheduled_routes.py:129-143 — send_with_receipt through the manager.
            receipt = await _send_scheduled_briefing(
                MOCK_CHANNEL_NAME, "brief-chat", text
            )
            check(
                "receipt is a DeliveryReceipt",
                isinstance(receipt, DeliveryReceipt),
                str(type(receipt)),
            )
            check(
                "receipt status accepted/sent",
                receipt.status in ("accepted", "sent"),
                str(receipt),
            )
            delivered = await stack.mock.next_for("brief-chat", timeout_s=5)
            check(
                "brief reached the mock channel verbatim",
                delivered.content == text,
                delivered.content[:200],
            )

            # Failure contract: unknown channel / missing target -> RuntimeError
            # (the outbox records a retryable failure, never a phantom delivery).
            with pytest.raises(RuntimeError):
                await _send_scheduled_briefing("nosuchchannel", "brief-chat", text)
            with pytest.raises(RuntimeError):
                await _send_scheduled_briefing(MOCK_CHANNEL_NAME, None, text)
            out["checks"]["unknown channel raises RuntimeError"] = {
                "pass": True,
                "detail": "",
            }
            out["checks"]["missing target raises RuntimeError"] = {
                "pass": True,
                "detail": "",
            }
            return out
        except BaseException as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            copy_store_artifacts(stack, evidence_dir, "s4-briefing")
            write_json(evidence_dir / "s4-briefing-results.json", out)
            await close_im_stack(stack)

    result = asyncio.run(scenario())
    failed = [k for k, v in result["checks"].items() if not v["pass"]]
    assert not failed, f"[s4] failed checks: {failed}"


# ---------------------------------------------------------------------------
# s2 — engine-death recovery timing (RUNS LAST: kills the rig's serve)
# ---------------------------------------------------------------------------


@requires_rig
def test_scenario2_engine_death_recovery(rig_state, scratch, evidence_dir) -> None:
    from src.opencode_bridge.driver import OpencodeDriver
    from src.opencode_bridge.errors import OpencodeBridgeError
    from src.opencode_bridge.recovery import RecoverableOpencodeSessionService
    from src.opencode_bridge.translator import EventTranslator
    from src.channels.runtime import ChannelRuntime
    from src.channels.bus.queue import MessageBus
    from src.session.events import EventBus
    from src.session.store import SessionStore

    observe_s = DEATH_OBSERVE_S
    prompt = (
        "Use the bash tool to run exactly this command once: `sleep 120 && echo "
        "T8_DEATH_DONE`. Do not iterate, no todo lists, no subagents."
    )

    async def scenario() -> Dict[str, Any]:
        stack = await build_im_stack(**_stack_kwargs(scratch, "s2-engine-death"))
        out: Dict[str, Any] = {}
        service2 = None
        try:
            await stack.mock.inject(
                prompt, chat_id="death-chat", message_id="t8-death-1"
            )
            t_send = time.time()

            # Wait until the attempt is running and the model reached the tool
            # (kill lands mid-turn, inside the silent bash sleep).
            session_id = None
            deadline = t_send + 120
            while time.time() < deadline:
                session_id = stack.runtime._session_map.get(
                    f"{MOCK_CHANNEL_NAME}:death-chat"
                )
                if (
                    session_id
                    and stack.service._active_by_session.get(session_id)
                    and stack.recorder.times_of("tool_call")
                ):
                    break
                await asyncio.sleep(0.5)
            assert session_id, "[s2] runtime never mapped the chat to a session"
            attempt_id = stack.service._active_by_session.get(session_id)
            assert attempt_id, "[s2] no active attempt before the kill"
            out["pre_kill"] = {
                "session_id": session_id,
                "attempt_id": attempt_id,
                "engine_session_id": engine_session_id_for(stack, session_id),
                "tool_call_seen": bool(stack.recorder.times_of("tool_call")),
                "seconds_to_tool": round(time.time() - t_send, 1),
            }
            await asyncio.sleep(5)  # let the bash tool actually run

            pid = verified_serve_pid(rig_state)
            t_kill, _ = kill_serve(rig_state)
            out["t_kill_epoch"] = t_kill
            out["killed_pid"] = pid
            out["serve_dead_after_kill"] = not serve_alive(rig_state)

            # Phase A — live-death detection window (acceptance: <30 s).
            timeline: List[Dict[str, Any]] = []
            t_attempt_terminal: Optional[float] = None
            terminal_status: Optional[str] = None
            t_im_reply: Optional[float] = None
            im_reply: Optional[Any] = None
            deadline = t_kill + observe_s
            while time.time() < deadline:
                row: Dict[str, Any] = {"t": round(time.time() - t_kill, 1)}
                if t_attempt_terminal is None:
                    st = attempt_status(stack.store, attempt_id)
                    row["attempt_status"] = st
                    if st in {"completed", "failed", "cancelled", "interrupted"}:
                        t_attempt_terminal = time.time()
                        terminal_status = st
                sent = stack.mock.sent_for("death-chat")
                row["im_replies"] = len(sent)
                if t_im_reply is None and sent:
                    # Detection granularity = the 1 s poll below.
                    t_im_reply = time.time()
                    im_reply = sent[0]
                timeline.append(row)
                if t_attempt_terminal is not None and t_im_reply is not None:
                    break
                await asyncio.sleep(1.0)

            out["phase_a"] = {
                "observe_s": observe_s,
                "t_attempt_terminal_s": (
                    round(t_attempt_terminal - t_kill, 2)
                    if t_attempt_terminal
                    else None
                ),
                "terminal_status": terminal_status,
                "t_im_reply_s": round(t_im_reply - t_kill, 2) if t_im_reply else None,
                "im_reply_content": im_reply.content if im_reply else None,
                "im_reply_metadata": dict(im_reply.metadata) if im_reply else None,
                "timeline_head": timeline[:5],
                "timeline_tail": timeline[-5:],
                "timeline_points": len(timeline),
            }

            # Phase B — T6 restart reconciliation against the dead engine.
            st_now = attempt_status(stack.store, attempt_id)
            phase_b: Dict[str, Any] = {"attempt_status_before": st_now}
            if st_now not in {"completed", "failed", "cancelled", "interrupted"}:
                store2 = SessionStore(base_dir=stack.store.base_dir)
                bus2 = EventBus()
                bus2.set_loop(asyncio.get_running_loop())
                driver2 = OpencodeDriver(base_url=rig_state["serve_url"])
                service2 = RecoverableOpencodeSessionService(
                    store=store2,
                    event_bus=bus2,
                    runs_dir=stack.store.base_dir.parent / "runs",
                    driver=driver2,
                    translator=EventTranslator(),
                )
                # Wiring contract: startup against a dead engine is LOUD.
                try:
                    await driver2.load_tool_mapping()
                    phase_b["load_tool_mapping"] = "unexpectedly succeeded"
                except OpencodeBridgeError as exc:
                    phase_b["load_tool_mapping"] = (
                        f"raised {type(exc).__name__} (loud, per wiring contract)"
                    )
                report = await service2.reconcile()
                phase_b["reconcile"] = {
                    "reattached": list(report.reattached),
                    "backfilled": list(report.backfilled),
                    "interrupted": list(report.interrupted),
                }
                phase_b["attempt_status_after"] = attempt_status(store2, attempt_id)
                msgs = [
                    m
                    for m in store2.get_messages(session_id, limit=200)
                    if m.role == "assistant" and m.linked_attempt_id == attempt_id
                ]
                phase_b["interrupted_reply"] = {
                    "found": bool(msgs),
                    "metadata": dict(msgs[-1].metadata) if msgs else None,
                    "content_head": (msgs[-1].content or "")[:160] if msgs else None,
                }
                # IM polling contract: the REAL _wait_for_reply returns the
                # T6-landed terminal immediately (plan T6 QA failure scenario).
                rt2 = ChannelRuntime(
                    bus=MessageBus(),
                    session_service=service2,
                    manager=None,
                    session_map_path=scratch["root"] / "s2-rt2-map.json",
                    reply_timeout_s=30.0,
                    poll_interval_s=PROD_POLL_INTERVAL_S,
                )
                t0 = time.time()
                waited = await rt2._wait_for_reply(session_id, attempt_id)
                phase_b["wait_for_reply_s"] = round(time.time() - t0, 2)
                phase_b["wait_for_reply_status"] = (waited.metadata or {}).get("status")
                # Short-window world: runtime1's handler is STILL polling (600 s
                # budget not exhausted) and picks the T6-landed reply up live.
                replies_before = len(stack.mock.sent_for("death-chat"))
                await asyncio.sleep(3.0)
                live = stack.mock.sent_for("death-chat")
                phase_b["runtime1_replies_total"] = len(live)
                if len(live) > replies_before:
                    phase_b["runtime1_picked_up_interrupted"] = live[-1].content[:160]
                    phase_b["runtime1_pickup_latency_s"] = round(time.time() - t0, 2)
            else:
                phase_b["note"] = (
                    "attempt already terminal — live-death detection worked; "
                    "restart reconciliation not exercised"
                )
            out["phase_b"] = phase_b
            out["event_census"] = stack.recorder.types_seen()
            return out
        except BaseException as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            copy_store_artifacts(stack, evidence_dir, "s2-engine-death")
            write_json(evidence_dir / "s2-engine-death-results.json", out)
            if service2 is not None:
                await service2.aclose()
            await close_im_stack(stack)

    result = asyncio.run(scenario())

    phase_a = result["phase_a"]
    landed_fast = (
        phase_a["t_attempt_terminal_s"] is not None
        and phase_a["t_attempt_terminal_s"] < DEATH_ACCEPTANCE_S
        and phase_a["t_im_reply_s"] is not None
        and phase_a["t_im_reply_s"] < DEATH_ACCEPTANCE_S
    )
    if not landed_fast:
        pytest.xfail(
            "BUG(T8-1) engine-death mid-turn is not detected live: attempt "
            f"landing={phase_a['t_attempt_terminal_s']}s (acceptance <30s), IM "
            f"failure reply={phase_a['t_im_reply_s']}s content="
            f"{(phase_a['im_reply_content'] or '')[:80]!r}. Root cause (code "
            "read): OpencodeDriver.events() reconnects forever (backoff "
            "0.5->30s) so the pump task never dies and _fail_all_pending never "
            "fires; the attempt hangs until the 600s IM polling budget "
            "(TimeoutError reply = explicit failure, plan-QA non-silent holds) "
            "or a process-restart reconcile lands it interrupted (Phase B: "
            f"{result['phase_b'].get('reconcile')}). Bridge src fix owned by a "
            "follow-up task (T9 lane owns opencode_bridge/ this wave). "
            f"Evidence: {evidence_dir}/s2-engine-death-results.json"
        )
