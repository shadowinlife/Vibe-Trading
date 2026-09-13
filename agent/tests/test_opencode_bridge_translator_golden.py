"""Golden replay tests: every T1 trace through the EventTranslator.

Replays the Phase-0 spike corpus (opencode 1.18.30 + OmO 4.19.4,
``agent/tests/fixtures/opencode_bridge/traces/``) frame-by-frame under
virtual time — the clock follows each frame's ``mono`` value and big gaps
are stepped in 1 s wakes, so timer-driven emissions (heartbeats,
quiescence terminals) are deterministic and no test sleeps in real time.

The pinned sequences/payloads below were derived from the traces and
verified against plan D4/D5 and the frontend consumers (``Agent.tsx``,
``useSSE.ts``). They are the drift alarm promised by D10: when the CLI
pin changes (T10), re-record and diff.

Mandated golden additions (Oracle round 2, plan T4):
* scenario g — real 120.75 s silent tool: 40 heartbeats at exact 3 s
  schedule (B3, frontend 90 s watchdog);
* terminal payload field-by-field assertions (service.py:408-414);
* post-terminal late events dropped + WARNING logged.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from src.opencode_bridge.events import decode_event
from src.opencode_bridge.tool_names import ToolNameMap
from tests.test_opencode_bridge_translator_harness import (
    TranslatorBench,
    load_trace,
    run_trace_scenario,
    scenario_sid,
    wire_event,
)

TMAP = ToolNameMap(
    prefixed_to_bare={"vibe-trading_list_skills": "list_skills"},
    server_prefixes=("vibe-trading_",),
)


def payloads(events, event_type: str) -> list[dict]:
    return [e.data for e in events if e.type == event_type]


def terminals(events) -> list:
    return [e for e in events if e.type.startswith("attempt.")]


def wire_frames(name: str) -> tuple[list[dict], str, list[dict]]:
    frames = load_trace(name)
    sid = scenario_sid(frames)
    wire = [f for f in frames if not f["type"].startswith("_recorder")]
    return frames, sid, wire


def props_of(frame: dict) -> dict:
    return json.loads(frame["data"]).get("properties", {})


async def feed_wire(
    bench: TranslatorBench, wire: list[dict], stop_at: float | None = None
) -> None:
    """Feed wire frames at their recorded virtual times (1 s gap stepping)."""
    for frame in wire:
        if stop_at is not None and frame["mono"] > stop_at:
            break
        gap = frame["mono"] - bench.clock()
        if gap > 1.0:
            await bench.advance(gap - 1.0)
        event = decode_event(frame["data"])
        assert event is not None
        await bench.feed(event, at=frame["mono"])


# ---------------------------------------------------------------------------
# (a) multi-tool turn
# ---------------------------------------------------------------------------


def test_golden_scenario_a_multi_tool_sequence_and_payloads() -> None:
    bench, sid, wire = run_trace_scenario("scenario_a_multi_tool.jsonl", tool_map=TMAP)
    out = bench.out

    assert bench.types() == [
        "reasoning_delta",  # iter 1 thinking (throttled to one emit)
        "tool_call",  # vibe-trading_list_skills -> bare name
        "tool_result",
        "tool_call",  # bash echo
        "tool_result",
        "llm_usage",  # round-1 assistant message tokens
        "reasoning_delta",  # iter 2
        "text_delta",  # "DONE_A"
        "llm_usage",
        "attempt.completed",
    ]

    calls, results = payloads(out, "tool_call"), payloads(out, "tool_result")
    assert calls[0] == {
        "tool": "list_skills",
        "arguments": {},  # MCP tool inputs are not exposed by the engine
        "iter": 1,
        "call_id": "call_ecbb0813d97049e19f49a24f",
        "attempt_id": "att_golden",
    }
    assert calls[1]["tool"] == "bash"
    assert calls[1]["arguments"] == {"command": "echo hello-spike-a"}
    assert results[0]["status"] == "ok"
    assert results[0]["elapsed_ms"] == 323  # engine state.time end-start
    assert results[1]["elapsed_ms"] == 1397
    assert results[1]["preview"] == "hello-spike-a\n"
    for call, result in zip(calls, results):
        assert call["call_id"] == result["call_id"]  # pairs complete

    # Preview is state.output's first 200 chars — recomputed from the trace,
    # never state.title (which is "" for MCP tools, spike §5a).
    mcp_output = None
    for frame in wire:
        if frame["type"] != "message.part.updated":
            continue
        part = props_of(frame).get("part", {})
        if part.get("callID") == "call_ecbb0813d97049e19f49a24f":
            state = part.get("state") or {}
            if state.get("status") == "completed":
                mcp_output = state.get("output")
    assert isinstance(mcp_output, str)
    assert results[0]["preview"] == mcp_output[:200]

    # Mandatory condition ②: chain-of-thought never leaks into chat text.
    assert "".join(d["delta"] for d in payloads(out, "text_delta")) == "DONE_A"
    reasoning = payloads(out, "reasoning_delta")
    assert reasoning[0]["iter"] == 1 and reasoning[1]["iter"] == 2
    assert all(len(r["tail"]) <= 600 for r in reasoning)

    assert payloads(out, "llm_usage")[0] == {
        "input_tokens": 717,
        "output_tokens": 50,
        "total_tokens": 61212,
        "iter": 1,
        "attempt_id": "att_golden",
    }

    # Terminal payload field-by-field (D5 enumeration, service.py:408-414).
    completed = payloads(out, "attempt.completed")[0]
    assert set(completed) == {
        "attempt_id",
        "status",
        "summary",
        "run_dir",
        "elapsed_ms",
        "provider",
        "model",
    }
    assert completed["attempt_id"] == "att_golden"
    assert completed["status"] == "completed"
    assert completed["summary"] == "DONE_A"  # frontend prefers d.summary
    assert completed["run_dir"] is None
    assert completed["provider"] == "alibaba-cn"
    assert completed["model"] == "qwen3.8-max"
    assert completed["elapsed_ms"] > 0
    # Every emitted event carries the attempt stamp (service.py:502 parity).
    assert all(e.data.get("attempt_id") == "att_golden" for e in out)


def test_golden_scenario_a_post_terminal_late_events_dropped_with_warning(
    caplog,
) -> None:
    """Mandated addition: late same-attempt events after the done archive."""
    frames, sid, wire = wire_frames("scenario_a_multi_tool.jsonl")

    async def scenario():
        async with TranslatorBench(tool_map=TMAP, t0=frames[0]["mono"]) as bench:
            bench.translator.note_attempt(sid, "att_golden")
            await feed_wire(bench, wire)
            await bench.advance(8.05)
            assert bench.types()[-1] == "attempt.completed"
            count_at_terminal = len(bench.out)
            # A late delta for the SAME attempt (duplicate-bubble hazard):
            await bench.feed(
                wire_event(
                    "message.part.delta",
                    sessionID=sid,
                    messageID="msg_095658220001lNSYGzWTqZzJak",
                    partID="prt_late",
                    field="text",
                    delta="late leak",
                ),
                at=bench.clock() + 0.5,
            )
            return bench.out, count_at_terminal

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        out, count_at_terminal = asyncio.run(scenario())
    assert len(out) == count_at_terminal  # dropped, no duplicate bubble
    assert any("dropping late" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (b) mid-turn abort
# ---------------------------------------------------------------------------


def test_golden_scenario_b_abort_cancels_and_tolerates_aftermath(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="opencode_bridge"):
        bench, sid, wire = run_trace_scenario(
            "scenario_b_abort.jsonl", tool_map=TMAP, abort_at_error=True
        )
    out = bench.out

    assert bench.types() == [
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",
        "reasoning_delta",  # ~9 s of thinking, 1 s throttle
        "tool_call",  # bash sleep 30
        "tool_heartbeat",  # one 3 s beat before the abort at +4 s
        "attempt.cancelled",
    ]
    assert payloads(out, "tool_call")[0]["arguments"] == {
        "command": "sleep 30",
        "timeout": "60000",
    }
    assert payloads(out, "tool_heartbeat")[0]["elapsed_s"] == 3.0
    cancelled = payloads(out, "attempt.cancelled")[0]
    assert cancelled == {"attempt_id": "att_golden", "status": "cancelled"}
    # Abort is unambiguously distinct from natural completion (S3):
    assert not payloads(out, "attempt.completed")
    assert not payloads(out, "attempt.failed")
    # Double idle + error + late snapshots are tolerated WITHOUT warnings.
    assert not [r for r in caplog.records if "dropping late" in r.message]


# ---------------------------------------------------------------------------
# (c) OmO todo-continuation (mandatory condition ①: 6.4 s re-prompt)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trace",
    ["scenario_c_continuation_run1.jsonl", "scenario_c_continuation_run2.jsonl"],
)
def test_golden_scenario_c_continuation_single_terminal(trace: str) -> None:
    bench, sid, wire = run_trace_scenario(trace, tool_map=TMAP)
    out = bench.out

    # Exactly ONE terminal for the whole continued turn (2 idles in trace):
    assert [e.type for e in terminals(out)] == ["attempt.completed"]
    completed = payloads(out, "attempt.completed")[0]
    # Summary = LAST natural completion (round 2), not round 1's "DONE_C1":
    assert completed["summary"].startswith("All 3 todos complete")
    assert "DONE_C1" not in completed["summary"]
    # Both rounds streamed inside the same attempt:
    text = "".join(d["delta"] for d in payloads(out, "text_delta"))
    assert text.startswith("DONE_C1")
    assert completed["summary"] in text
    # The OmO directive never reaches transcript-facing output (D4):
    assert all("SYSTEM DIRECTIVE" not in json.dumps(e.data) for e in out)
    # Iterations advance across the continuation:
    iters = [d["iter"] for d in payloads(out, "text_delta")]
    assert iters[0] < iters[-1]
    # Tool pairs stay complete across the 10 tool rounds:
    calls, results = payloads(out, "tool_call"), payloads(out, "tool_result")
    assert len(calls) == len(results) == 10
    assert [c["call_id"] for c in calls] == [r["call_id"] for r in results]


def test_golden_scenario_c_no_terminal_at_falsified_3s_quiescence() -> None:
    """Plan T4 failure QA on the REAL trace: idle -> re-prompt -> no terminal.

    The re-prompt arrives 6.4 s after idle#1 (spike §7f) — inside the 8 s
    window, outside the falsified 3 s one. Advance past BOTH boundaries
    and assert the translator stayed silent.
    """
    frames, sid, wire = wire_frames("scenario_c_continuation_run1.jsonl")
    idle1 = next(
        f["mono"]
        for f in wire
        if f["type"] == "session.idle" and props_of(f).get("sessionID") == sid
    )
    user_ids = {
        props_of(f).get("info", {}).get("id")
        for f in wire
        if f["type"] == "message.updated"
        and props_of(f).get("info", {}).get("role") == "user"
        and f["mono"] < idle1
    }
    # The injected continuation user message (novel id) ~6.4 s after idle#1:
    reprompt = next(
        f
        for f in wire
        if f["type"] == "message.updated"
        and f["mono"] > idle1 + 5.0
        and props_of(f).get("info", {}).get("role") == "user"
        and props_of(f).get("info", {}).get("id") not in user_ids
    )
    assert 6.0 < reprompt["mono"] - idle1 < 7.0  # trace-derived 6.4 s gap

    async def scenario():
        async with TranslatorBench(tool_map=TMAP, t0=frames[0]["mono"]) as bench:
            bench.translator.note_attempt(sid, "att_golden")
            await feed_wire(bench, wire, stop_at=reprompt["mono"])
            # Past the falsified 3.0 s AND the real 8.0 s window from idle#1:
            await bench.advance(idle1 + 12.0 - bench.clock())
            return bench.out

    out = asyncio.run(scenario())
    assert [e.type for e in terminals(out)] == []


# ---------------------------------------------------------------------------
# (d) subagent spawn (mandatory condition ③)
# ---------------------------------------------------------------------------


def test_golden_scenario_d_child_events_dropped_parent_idle_scoped() -> None:
    bench, sid, wire = run_trace_scenario("scenario_d_subagent.jsonl", tool_map=TMAP)
    out = bench.out

    assert bench.types() == [
        "reasoning_delta",
        "reasoning_delta",
        "tool_call",  # task(explore)
        "tool_heartbeat",
        "tool_heartbeat",
        "tool_heartbeat",
        "tool_heartbeat",
        "tool_heartbeat",  # 16.5 s subagent run
        "tool_result",
        "llm_usage",
        "reasoning_delta",
        "text_delta",  # "DONE_D"
        "llm_usage",
        "attempt.completed",
    ]
    # The child's 176 deltas / 14 messages / 2 idles produced NOTHING:
    assert "".join(d["delta"] for d in payloads(out, "text_delta")) == "DONE_D"
    completed = payloads(out, "attempt.completed")[0]
    assert completed["summary"] == "DONE_D"
    # Parent terminal only after the PARENT idle (child idles arrived 14 s
    # earlier and must never terminate the turn — condition ③):
    parent_idle = max(
        f["mono"]
        for f in wire
        if f["type"] == "session.idle" and props_of(f).get("sessionID") == sid
    )
    child_idles = [
        f["mono"]
        for f in wire
        if f["type"] == "session.idle" and props_of(f).get("sessionID") != sid
    ]
    assert child_idles and max(child_idles) < parent_idle - 10
    task_call = payloads(out, "tool_call")[0]
    assert task_call["tool"] == "task"
    assert all(len(v) <= 200 for v in task_call["arguments"].values())
    task_result = payloads(out, "tool_result")[0]
    assert task_result["status"] == "ok"
    assert task_result["elapsed_ms"] == 16487
    assert task_result["preview"].startswith("Task completed in 16s.")
    assert [b["elapsed_s"] for b in payloads(out, "tool_heartbeat")] == [
        3.0,
        6.0,
        9.0,
        12.0,
        15.0,
    ]


# ---------------------------------------------------------------------------
# (e) permission ask/reply (not in the vt vocabulary)
# ---------------------------------------------------------------------------


def test_golden_scenario_e_permission_events_never_surface() -> None:
    bench, sid, wire = run_trace_scenario("scenario_e_permission.jsonl", tool_map=TMAP)
    out = bench.out

    assert bench.types() == [
        "reasoning_delta",
        "tool_call",
        "tool_heartbeat",  # 3.3 s permission-blocked window
        "tool_result",
        "llm_usage",
        "reasoning_delta",
        "text_delta",
        "llm_usage",
        "attempt.completed",
    ]
    assert not any("permission" in e.type for e in out)
    assert payloads(out, "tool_call")[0]["arguments"] == {
        "command": "echo permission-spike-ok"
    }
    completed = payloads(out, "attempt.completed")[0]
    assert completed["summary"] == "DONE_E"
    assert completed["status"] == "completed"


# ---------------------------------------------------------------------------
# (g) silent long tool — B3 heartbeat mandate (real 120.75 s silence)
# ---------------------------------------------------------------------------


def test_golden_scenario_g_heartbeats_bridge_the_120s_silence() -> None:
    bench, sid, wire = run_trace_scenario("scenario_g_silent_tool.jsonl", tool_map=TMAP)
    out = bench.out

    beats = payloads(out, "tool_heartbeat")
    # floor(121.826 s running window / 3 s) = 40 beats, exact schedule:
    assert [b["elapsed_s"] for b in beats] == [round(3.0 * i, 2) for i in range(1, 41)]
    assert all(b["tool"] == "bash" for b in beats)
    assert all(b["call_id"] == "call_c939afae76934b26b8eaa78d" for b in beats)
    assert all(b["attempt_id"] == "att_golden" for b in beats)
    # ~3 s cadence: the frontend 90 s watchdog never sees a silent stretch.
    gaps = {
        round(b2["elapsed_s"] - b1["elapsed_s"], 6) for b1, b2 in zip(beats, beats[1:])
    }
    assert gaps == {3.0}

    result = payloads(out, "tool_result")[0]
    assert result["elapsed_ms"] == 121819
    assert result["preview"] == "(no output)"
    assert result["status"] == "ok"
    completed = payloads(out, "attempt.completed")[0]
    assert completed["summary"] == "DONE_G"
    non_beat_types = [t for t in bench.types() if t != "tool_heartbeat"]
    assert non_beat_types == [
        "reasoning_delta",
        "reasoning_delta",
        "tool_call",
        "tool_result",
        "llm_usage",
        "reasoning_delta",
        "text_delta",
        "llm_usage",
        "attempt.completed",
    ]


# ---------------------------------------------------------------------------
# (h) DELETE /session — no attempt, no output, no crash
# ---------------------------------------------------------------------------


def test_golden_scenario_h_delete_session_is_inert() -> None:
    bench, sid, wire = run_trace_scenario(
        "measurement_h_delete_session.jsonl", announce=False
    )
    assert bench.out == []


# ---------------------------------------------------------------------------
# run_dir golden (spike §9: source untested in the corpus — synthetic output)
# ---------------------------------------------------------------------------


def test_golden_run_dir_flows_from_synthetic_backtest_output_to_terminal() -> None:
    run_dir = "/home/tenant/.vibe-trading/runs/run_20260912_momentum"
    output = json.dumps(
        {
            "status": "ok",
            "exit_code": 0,
            "stdout": "backtest finished",
            "artifacts": {"metrics": f"{run_dir}/artifacts/metrics.csv"},
            "run_dir": run_dir,
        }
    )

    def bt_part(status: str, **state_extra) -> object:
        state = {"status": status, "input": {"run_dir": run_dir}}
        state.update(state_extra)
        return wire_event(
            "message.part.updated",
            sessionID="ses_bt",
            part={
                "id": "p1",
                "messageID": "a",
                "sessionID": "ses_bt",
                "type": "tool",
                "tool": "vibe-trading_backtest",
                "callID": "c1",
                "state": state,
            },
        )

    async def scenario():
        async with TranslatorBench(tool_map=TMAP, t0=5000.0) as bench:
            bench.translator.note_attempt("ses_bt", "att_bt")
            await bench.feed(
                wire_event(
                    "message.updated",
                    sessionID="ses_bt",
                    info={
                        "id": "u",
                        "role": "user",
                        "sessionID": "ses_bt",
                        "time": {"created": 1},
                    },
                ),
                at=5000.0,
            )
            await bench.feed(
                wire_event(
                    "message.updated",
                    sessionID="ses_bt",
                    info={
                        "id": "a",
                        "role": "assistant",
                        "sessionID": "ses_bt",
                        "time": {"created": 2},
                        "providerID": "alibaba-cn",
                        "modelID": "qwen3.8-max",
                    },
                ),
                at=5001.0,
            )
            await bench.feed(bt_part("running", time={"start": 1000}), at=5002.0)
            await bench.feed(
                bt_part("completed", output=output, time={"start": 1000, "end": 61000}),
                at=5062.0,
            )
            await bench.feed(
                wire_event(
                    "message.part.updated",
                    sessionID="ses_bt",
                    part={
                        "id": "p2",
                        "messageID": "a",
                        "sessionID": "ses_bt",
                        "type": "text",
                        "text": "Backtest done.",
                    },
                ),
                at=5063.0,
            )
            await bench.feed(
                wire_event(
                    "message.updated",
                    sessionID="ses_bt",
                    info={
                        "id": "a",
                        "role": "assistant",
                        "sessionID": "ses_bt",
                        "time": {"created": 2, "completed": 3},
                        "finish": "stop",
                    },
                ),
                at=5063.5,
            )
            await bench.feed(wire_event("session.idle", sessionID="ses_bt"), at=5064.0)
            await bench.advance(8.05)
        return bench.out

    out = asyncio.run(scenario())
    completed = payloads(out, "attempt.completed")[0]
    # The run card's only source (Agent.tsx:876-877):
    assert completed["run_dir"] == run_dir
    assert completed["summary"] == "Backtest done."
    beats = payloads(out, "tool_heartbeat")
    assert [b["elapsed_s"] for b in beats] == [round(3.0 * i, 2) for i in range(1, 21)]
    result = payloads(out, "tool_result")[0]
    assert result["elapsed_ms"] == 60000
    assert result["preview"] == output[:200]
