"""Unit tests for the EventTranslator text/tool translation layer.

Covers plan T4/D5 semantics: delta passthrough vs fallback snapshot
diffing, the mandatory partID->part.type join (condition ②), the 600-char
reasoning rolling tail, 200-char preview/argument truncation, bare-name
mapping via T3's ToolNameMap, run_dir extraction, llm_usage synthesis,
allowlist drift defense, and the held-event join buffer. Lifecycle
(quiescence/abort/terminals) lives in
``test_opencode_bridge_translator_lifecycle.py``; real-trace golden replay
in ``test_opencode_bridge_translator_golden.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging

from src.opencode_bridge.tool_names import ToolNameMap
from src.opencode_bridge.translator.text import (
    OMO_DIRECTIVE_PREFIX,
    REASONING_TAIL_CHARS,
    is_omo_directive,
)
from tests.test_opencode_bridge_translator_harness import (
    TranslatorBench,
    wire_event,
)

SID = "ses_unit"
AID = "att_unit"
TMAP = ToolNameMap(
    prefixed_to_bare={"vibe-trading_backtest": "backtest"},
    server_prefixes=("vibe-trading_",),
)


def msg(sid: str, mid: str, role: str, **info_extra) -> object:
    info = {"id": mid, "role": role, "sessionID": sid, "time": {"created": 1}}
    info.update(info_extra)
    return wire_event("message.updated", sessionID=sid, info=info)


def part(sid: str, pid: str, mid: str, ptype: str, **extra) -> object:
    payload = {"id": pid, "messageID": mid, "sessionID": sid, "type": ptype}
    payload.update(extra)
    return wire_event("message.part.updated", sessionID=sid, part=payload)


def delta(sid: str, mid: str, pid: str, text: str) -> object:
    return wire_event(
        "message.part.delta",
        sessionID=sid,
        messageID=mid,
        partID=pid,
        field="text",
        delta=text,
    )


def tool_part(
    sid: str, pid: str, mid: str, tool: str, call_id: str, status: str, **state_extra
) -> object:
    state = {"status": status}
    state.update(state_extra)
    return part(sid, pid, mid, "tool", tool=tool, callID=call_id, state=state)


async def open_turn(bench: TranslatorBench, *, sid: str = SID, aid: str = AID) -> None:
    """Announce an attempt plus the opening user/assistant messages."""
    bench.translator.note_attempt(sid, aid)
    await bench.feed(msg(sid, "msg_u", "user"), at=1000.0)
    await bench.feed(msg(sid, "msg_a", "assistant"), at=1001.0)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# text passthrough + the mandatory partID->part.type join (condition ②)
# ---------------------------------------------------------------------------


def test_text_delta_is_verbatim_passthrough_with_iter_and_attempt_id() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                part(SID, "prt_t", "msg_a", "text", text="", time={"start": 1}),
                at=1002.0,
            )
            await bench.feed(delta(SID, "msg_a", "prt_t", "Hel"), at=1002.1)
            await bench.feed(delta(SID, "msg_a", "prt_t", "lo✓"), at=1002.2)
        return bench.out

    out = run(scenario())
    deltas = [e for e in out if e.type == "text_delta"]
    # Zero diff computation: the wire deltas pass through byte-identical.
    assert [e.data["delta"] for e in deltas] == ["Hel", "lo✓"]
    assert all(
        e.data == {"delta": e.data["delta"], "iter": 1, "attempt_id": AID}
        for e in deltas
    )


def test_reasoning_deltas_carry_field_text_but_route_to_reasoning_delta() -> None:
    """Mandatory condition ②: field:"text" does NOT mean chat text."""

    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                part(SID, "prt_r", "msg_a", "reasoning", text="", time={"start": 1}),
                at=1002.0,
            )
            # field is "text" on the wire even for reasoning parts:
            await bench.feed(
                delta(SID, "msg_a", "prt_r", "chain of thought"), at=1002.1
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["reasoning_delta"]
    data = out[0].data
    assert data["tail"] == "chain of thought"
    assert data["iter"] == 1 and data["chars"] == 16 and data["attempt_id"] == AID


def test_fallback_diffing_only_when_deltas_absent() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            # No deltas at all: cumulative snapshots must be diffed.
            await bench.feed(part(SID, "prt_t", "msg_a", "text", text="A"), at=1002.0)
            await bench.feed(part(SID, "prt_t", "msg_a", "text", text="AB"), at=1002.5)
            await bench.feed(part(SID, "prt_t", "msg_a", "text", text="ABC"), at=1003.0)
        return bench.out

    out = run(scenario())
    assert [e.data["delta"] for e in out if e.type == "text_delta"] == ["A", "B", "C"]


def test_snapshot_after_deltas_never_reemits() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(part(SID, "prt_t", "msg_a", "text", text=""), at=1002.0)
            await bench.feed(delta(SID, "msg_a", "prt_t", "Hi"), at=1002.1)
            # Cumulative snapshot re-emission (bookkeeping) after passthrough:
            await bench.feed(
                part(
                    SID,
                    "prt_t",
                    "msg_a",
                    "text",
                    text="Hi",
                    time={"start": 1, "end": 2},
                ),
                at=1002.5,
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["text_delta"]
    assert out[0].data["delta"] == "Hi"


def test_user_message_parts_never_echo_into_chat() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            bench.translator.note_attempt(SID, AID)
            await bench.feed(msg(SID, "msg_u", "user"), at=1000.0)
            await bench.feed(
                part(SID, "prt_u", "msg_u", "text", text="the prompt itself"), at=1000.1
            )
            await bench.feed(
                part(SID, "prt_u", "msg_u", "text", text="the prompt itself grows"),
                at=1000.2,
            )
        return bench.out

    assert run(scenario()) == []


def test_summary_compaction_messages_are_filtered_from_chat() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(msg(SID, "msg_s", "assistant", summary=True), at=1002.0)
            await bench.feed(part(SID, "prt_s", "msg_s", "text", text=""), at=1002.1)
            await bench.feed(
                delta(SID, "msg_s", "prt_s", "compaction summary text"), at=1002.2
            )
            await bench.feed(
                part(SID, "prt_s", "msg_s", "text", text="compaction summary text"),
                at=1002.3,
            )
        return bench.out

    assert run(scenario()) == []


def test_omo_directive_user_message_suppressed_and_detected() -> None:
    assert is_omo_directive(OMO_DIRECTIVE_PREFIX + " - TODO CONTINUATION]\ngo")
    assert not is_omo_directive("regular user text")

    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            bench.translator.note_attempt(SID, AID)
            await bench.feed(msg(SID, "msg_u2", "user"), at=1000.0)
            await bench.feed(
                part(
                    SID,
                    "prt_u2",
                    "msg_u2",
                    "text",
                    text=OMO_DIRECTIVE_PREFIX + " - TODO CONTINUATION]\nContinue.",
                ),
                at=1000.1,
            )
        return bench.out

    assert run(scenario()) == []


def test_delta_before_part_snapshot_is_held_then_flushed_in_order() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            # Deltas arrive before any snapshot for the part (drift defense):
            await bench.feed(delta(SID, "msg_a", "prt_x", "one"), at=1002.0)
            await bench.feed(delta(SID, "msg_a", "prt_x", "two"), at=1002.1)
            assert bench.out == []  # held: partID->type join unresolved
            await bench.feed(part(SID, "prt_x", "msg_a", "text", text=""), at=1002.2)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["text_delta", "text_delta"]
    assert [e.data["delta"] for e in out] == ["one", "two"]


def test_held_reasoning_delta_flushes_to_reasoning_not_text() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(delta(SID, "msg_a", "prt_r", "thought"), at=1002.0)
            await bench.feed(
                part(SID, "prt_r", "msg_a", "reasoning", text=""), at=1002.1
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["reasoning_delta"]
    assert out[0].data["tail"] == "thought"


# ---------------------------------------------------------------------------
# reasoning tail semantics (Agent.tsx:694-699 replace-semantics)
# ---------------------------------------------------------------------------


def test_reasoning_tail_rolls_at_600_chars_with_cumulative_chars() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                part(SID, "prt_r", "msg_a", "reasoning", text=""), at=1002.0
            )
            t = 1002.0
            for i in range(7):  # 700 chars total, one emit per throttle window
                t += 1.1
                await bench.feed(
                    delta(SID, "msg_a", "prt_r", f"{i:03d}" * 33 + "x"), at=t
                )
        return bench.out

    out = run(scenario())
    emits = [e for e in out if e.type == "reasoning_delta"]
    assert len(emits) == 7  # spaced > 1 s apart: none throttled
    last = emits[-1].data
    assert last["chars"] == 700
    assert len(last["tail"]) == REASONING_TAIL_CHARS
    full = "".join(f"{i:03d}" * 33 + "x" for i in range(7))
    assert last["tail"] == full[-REASONING_TAIL_CHARS:]


def test_reasoning_throttled_to_one_emit_per_second_first_immediate() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                part(SID, "prt_r", "msg_a", "reasoning", text=""), at=1002.0
            )
            await bench.feed(delta(SID, "msg_a", "prt_r", "a"), at=1002.0)  # immediate
            await bench.feed(delta(SID, "msg_a", "prt_r", "b"), at=1002.5)  # throttled
            await bench.feed(delta(SID, "msg_a", "prt_r", "c"), at=1003.2)  # emitted
        return bench.out

    out = run(scenario())
    emits = [e.data for e in out if e.type == "reasoning_delta"]
    assert [e["tail"] for e in emits] == ["a", "abc"]
    assert [e["chars"] for e in emits] == [1, 3]


# ---------------------------------------------------------------------------
# tool state machine
# ---------------------------------------------------------------------------


def test_tool_call_and_result_payload_shapes() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "prt_1",
                    "msg_a",
                    "bash",
                    "call_1",
                    "running",
                    input={"command": "echo x"},
                    time={"start": 1000},
                ),
                at=1002.0,
            )
            await bench.feed(
                tool_part(
                    SID,
                    "prt_1",
                    "msg_a",
                    "bash",
                    "call_1",
                    "completed",
                    input={"command": "echo x"},
                    output="x\n",
                    title="echo x",
                    time={"start": 1000, "end": 2250},
                ),
                at=1003.5,
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["tool_call", "tool_result"]
    call, result = out[0].data, out[1].data
    assert call == {
        "tool": "bash",
        "arguments": {"command": "echo x"},
        "iter": 1,
        "call_id": "call_1",
        "attempt_id": AID,
    }
    assert result == {
        "tool": "bash",
        "status": "ok",
        "elapsed_ms": 1250,
        "preview": "x\n",
        "call_id": "call_1",
        "attempt_id": AID,
    }


def test_preview_comes_from_output_not_title_and_truncates_at_200() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "t",
                    "c1",
                    "completed",
                    input={},
                    output="O" * 300,
                    title="TITLE-IGNORED",
                ),
                at=1002.0,
            )
        return bench.out

    out = run(scenario())
    result = [e for e in out if e.type == "tool_result"][0].data
    assert result["preview"] == "O" * 200
    assert "TITLE-IGNORED" not in json.dumps(result)


def test_tool_call_argument_values_truncate_at_200_chars() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "t",
                    "c1",
                    "running",
                    input={"big": "v" * 300, "n": 42},
                ),
                at=1002.0,
            )
        return bench.out

    out = run(scenario())
    args = out[0].data["arguments"]
    assert args == {"big": "v" * 200, "n": "42"}


def test_bare_name_mapping_uses_injected_t3_tool_map() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "vibe-trading_backtest",
                    "c1",
                    "running",
                    input={},
                ),
                at=1002.0,
            )
            # Runtime map replacement (driver.load_tool_mapping finished late):
            bench.translator.tool_map = ToolNameMap(
                prefixed_to_bare={}, server_prefixes=("vibe-trading_",)
            )
            await bench.feed(
                tool_part(
                    SID,
                    "p2",
                    "msg_a",
                    "vibe-trading_get_market_data",
                    "c2",
                    "running",
                    input={},
                ),
                at=1003.0,
            )
        return bench.out

    out = run(scenario())
    assert [e.data["tool"] for e in out] == ["backtest", "get_market_data"]


def test_tool_call_once_per_call_id_and_result_deduped() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            for t in (1002.0, 1002.5, 1003.0):  # running snapshot re-emissions
                await bench.feed(
                    tool_part(
                        SID,
                        "p",
                        "msg_a",
                        "bash",
                        "c1",
                        "running",
                        input={"command": "sleep 1"},
                    ),
                    at=t,
                )
            for t in (1004.0, 1004.5):  # completed re-emissions
                await bench.feed(
                    tool_part(
                        SID,
                        "p",
                        "msg_a",
                        "bash",
                        "c1",
                        "completed",
                        input={},
                        output="done",
                    ),
                    at=t,
                )
        return bench.out

    assert [e.type for e in run(scenario())] == ["tool_call", "tool_result"]


def test_sparse_pending_then_completed_emits_paired_call_and_result() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(SID, "p", "msg_a", "bash", "c1", "pending", input={}, raw=""),
                at=1002.0,
            )
            assert bench.out == []  # pending carries no arguments yet
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "bash",
                    "c1",
                    "completed",
                    input={"command": "ls"},
                    output="f",
                ),
                at=1003.0,
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["tool_call", "tool_result"]
    assert out[0].data["arguments"] == {"command": "ls"}
    assert out[1].data["call_id"] == "c1"


def test_error_status_maps_to_error_and_elapsed_falls_back_to_clock() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(SID, "p", "msg_a", "bash", "c1", "running", input={}),
                at=1002.0,
            )
            await bench.feed(
                tool_part(
                    SID, "p", "msg_a", "bash", "c1", "error", input={}, output="boom"
                ),
                at=1004.0,
            )
        return bench.out

    out = run(scenario())
    result = [e for e in out if e.type == "tool_result"][0].data
    assert result["status"] == "error"
    assert result["elapsed_ms"] == 2000  # translator-clock span fallback


def test_run_dir_extracted_from_backtest_output_and_stashed_to_terminal() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            output = json.dumps({"status": "ok", "run_dir": "/runs/run_20260912_abc"})
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "vibe-trading_backtest",
                    "c1",
                    "running",
                    input={},
                ),
                at=1002.0,
            )
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "vibe-trading_backtest",
                    "c1",
                    "completed",
                    input={},
                    output=output,
                ),
                at=1003.0,
            )
            await bench.feed(
                msg(
                    SID,
                    "msg_a",
                    "assistant",
                    finish="stop",
                    time={"created": 1, "completed": 2},
                ),
                at=1003.5,
            )
            await bench.feed(
                part(SID, "prt_t", "msg_a", "text", text="done"), at=1003.6
            )
            await bench.feed(wire_event("session.idle", sessionID=SID), at=1004.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    completed = [e for e in out if e.type == "attempt.completed"][0].data
    assert completed["run_dir"] == "/runs/run_20260912_abc"
    assert completed["summary"] == "done"


def test_run_dir_prose_form_and_non_backtest_tools_ignored() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p1",
                    "msg_a",
                    "vibe-trading_backtest",
                    "c1",
                    "completed",
                    input={},
                    output="Run directory: /runs/r1 ok",
                ),
                at=1002.0,
            )
            await bench.feed(
                tool_part(
                    SID,
                    "p2",
                    "msg_a",
                    "bash",
                    "c2",
                    "completed",
                    input={},
                    output='{"run_dir": "/runs/ignored"}',
                ),
                at=1003.0,
            )
            await bench.feed(wire_event("session.idle", sessionID=SID), at=1004.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    completed = [e for e in out if e.type == "attempt.completed"][0].data
    assert completed["run_dir"] == "/runs/r1"


def test_run_dir_harvested_from_bash_runner_command() -> None:
    """Production governance disables the backtest MCP tool — the live path
    is ``python -m backtest.runner <run_dir>`` through the bash tool, whose
    stdout carries metrics only (T7 E2E finding). The runner CLI argument is
    the run_dir source there."""

    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p1",
                    "msg_a",
                    "bash",
                    "c1",
                    "completed",
                    input={
                        "command": "python -m backtest.runner /runs/r_bash 2>&1 | tail -25"
                    },
                    output='{"total_return": 0.1}',
                ),
                at=1002.0,
            )
            await bench.feed(wire_event("session.idle", sessionID=SID), at=1003.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    completed = [e for e in out if e.type == "attempt.completed"][0].data
    assert completed["run_dir"] == "/runs/r_bash"


def test_run_dir_bash_harvest_ignores_unrelated_commands() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p1",
                    "msg_a",
                    "bash",
                    "c1",
                    "completed",
                    input={"command": "ls /runs && cat config.json"},
                    output="ok",
                ),
                at=1002.0,
            )
            await bench.feed(wire_event("session.idle", sessionID=SID), at=1003.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    completed = [e for e in out if e.type == "attempt.completed"][0].data
    assert completed["run_dir"] is None


# ---------------------------------------------------------------------------
# llm_usage / stream_reset / allowlist
# ---------------------------------------------------------------------------


def test_llm_usage_synthesized_once_per_assistant_message() -> None:
    tokens = {
        "total": 100,
        "input": 30,
        "output": 20,
        "reasoning": 5,
        "cache": {"read": 45, "write": 0},
    }

    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            for _ in range(3):  # duplicate message.updated emissions are normal
                await bench.feed(
                    msg(
                        SID,
                        "msg_a",
                        "assistant",
                        tokens=tokens,
                        finish="stop",
                        time={"created": 1, "completed": 2},
                    )
                )
        return bench.out

    out = run(scenario())
    usage = [e for e in out if e.type == "llm_usage"]
    assert len(usage) == 1
    assert usage[0].data == {
        "input_tokens": 30,
        "output_tokens": 20,
        "total_tokens": 100,
        "iter": 1,
        "attempt_id": AID,
    }


def test_llm_usage_total_falls_back_to_input_plus_output() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                msg(SID, "msg_a", "assistant", tokens={"input": 7, "output": 3})
            )
        return bench.out

    out = run(scenario())
    assert out[0].data["total_tokens"] == 10


def test_stream_reset_best_effort_from_retry_status() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                wire_event(
                    "session.status",
                    sessionID=SID,
                    status={"type": "retry", "attempt": 2, "message": "rate limited"},
                ),
                at=1002.0,
            )
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["stream_reset"]
    assert out[0].data == {
        "iter": 1,
        "reason": "engine_retry",
        "retry_attempt": 2,
        "message": "rate limited",
        "attempt_id": AID,
    }


def test_unknown_event_types_ignored_with_single_warning(caplog) -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(wire_event("brand.new.type", sessionID=SID, x=1))
            await bench.feed(wire_event("brand.new.type", sessionID=SID, x=2))
            await bench.feed(wire_event("another.unknown", sessionID=SID))
        return bench.out

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        out = run(scenario())
    assert out == []
    warnings = [
        r.message for r in caplog.records if "unknown opencode event type" in r.message
    ]
    assert len(warnings) == 2  # one per type, not per event


def test_known_irrelevant_types_are_silent() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            for etype in (
                "server.heartbeat",
                "todo.updated",
                "file.edited",
                "session.diff",
                "tui.toast.show",
            ):
                await bench.feed(wire_event(etype, sessionID=SID))
        return bench.out

    assert run(scenario()) == []


def test_events_for_unannounced_sessions_emit_nothing() -> None:
    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await bench.feed(msg(SID, "msg_a", "assistant"), at=1000.0)
            await bench.feed(part(SID, "prt_t", "msg_a", "text", text="hi"), at=1000.1)
            await bench.feed(wire_event("session.idle", sessionID=SID), at=1000.2)
            await bench.advance(20.0)
        return bench.out

    assert run(scenario()) == []
