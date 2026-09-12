"""Unit tests for the EventTranslator attempt-lifecycle state machine.

Covers plan D4 + the Phase-0 mandatory conditions: the 8.0 s quiescence
default (condition ①, 3.0 falsified at 6.4 s), timer reset on genuine
activity vs the post-idle bookkeeping novelty filter, sessionID-scoped
idle handling (condition ③), child-session drop, abort semantics
(bridge-recorded state, idle != completed), session.error -> failed with
the kimaki-#74 synthetic-idle guard, post-terminal drop guard, heartbeat
scheduling, and the translator's public API surface.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.opencode_bridge.translator import EventTranslator
from src.opencode_bridge.translator.tools import HEARTBEAT_INTERVAL_S
from tests.test_opencode_bridge_translator_harness import (
    TranslatorBench,
    wire_event,
)
from tests.test_opencode_bridge_translator import (  # noqa: F401 - builders
    AID,
    SID,
    TMAP,
    delta,
    msg,
    open_turn,
    part,
    run,
    tool_part,
)


def idle(sid: str = SID) -> object:
    return wire_event("session.idle", sessionID=sid)


def status(kind: str, sid: str = SID) -> object:
    return wire_event("session.status", sessionID=sid, status={"type": kind})


def error(name: str = "BoomError", message: str = "bang", sid: str = SID) -> object:
    return wire_event(
        "session.error",
        sessionID=sid,
        error={"name": name, "data": {"message": message}},
    )


def completed_msg(mid: str = "msg_a", finish: str | None = "stop", **extra) -> object:
    info_extra = {"finish": finish, "time": {"created": 1, "completed": 2}, **extra}
    return msg(SID, mid, "assistant", **info_extra)


async def settle_turn(bench: TranslatorBench, *, text: str = "FINAL") -> None:
    """A minimal natural completion: assistant text + finish=stop."""
    await bench.feed(part(SID, "prt_t", "msg_a", "text", text=text), at=1002.0)
    await bench.feed(completed_msg(), at=1002.5)


# ---------------------------------------------------------------------------
# quiescence timer (mandatory condition ①)
# ---------------------------------------------------------------------------


def test_quiescence_default_is_8s_and_3s_falsified_boundary_holds() -> None:
    async def scenario():
        async with TranslatorBench() as bench:  # default quiescence_s
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            await bench.advance(6.4)  # OmO re-prompt moment: 3.0 s would fire
            mid = list(bench.out)
            await bench.advance(1.59)  # 7.99 s: still armed
            before = list(bench.out)
            await bench.advance(0.02)  # 8.01 s: expires
        return mid, before, bench.out

    mid, before, out = run(scenario())
    assert [e.type for e in mid if e.type.startswith("attempt.")] == []
    assert [e.type for e in before if e.type.startswith("attempt.")] == []
    terminals = [e for e in out if e.type == "attempt.completed"]
    assert len(terminals) == 1


def test_novel_message_resets_quiescence_no_false_terminal() -> None:
    """Plan T4 failure QA: idle -> new part -> NO terminal emitted."""

    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            # OmO continuation: a NOVEL user message 6.4 s after the idle.
            await bench.feed(msg(SID, "msg_cont", "user"), at=1009.4)
            await bench.feed(msg(SID, "msg_a2", "assistant"), at=1009.5)
            await bench.advance(20.0)  # far past the original deadline
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == []


def test_bookkeeping_reemission_does_not_reset_quiescence() -> None:
    """Spike §4: post-idle re-emits of KNOWN messages must not wedge the timer."""

    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            # ~12 ms later the server re-emits the ORIGINAL user message:
            await bench.feed(msg(SID, "msg_u", "user"), at=1003.012)
            await bench.feed(
                wire_event("session.updated", sessionID=SID, info={"id": SID}),
                at=1003.02,
            )
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]


def test_busy_status_and_permission_events_cancel_quiescence() -> None:
    async def scenario(cancel_event):
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            await bench.feed(cancel_event, at=1010.0)
            await bench.advance(20.0)
        return bench.out

    for cancel in (
        status("busy"),
        wire_event("permission.asked", sessionID=SID, id="per_1"),
    ):
        out = run(scenario(cancel))
        assert [e.type for e in out if e.type.startswith("attempt.")] == []


def test_double_idle_rearms_and_yields_single_terminal() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            await bench.feed(idle(), at=1003.25)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]


# ---------------------------------------------------------------------------
# sessionID scoping + child sessions (mandatory condition ③)
# ---------------------------------------------------------------------------


def test_idle_of_other_session_never_arms_terminal() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle("ses_other"), at=1003.0)
            await bench.advance(30.0)
        return bench.out

    assert [e.type for e in run(scenario()) if e.type.startswith("attempt.")] == []


def test_child_session_idles_and_content_are_dropped() -> None:
    child = "ses_child"

    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(
                wire_event(
                    "session.created",
                    sessionID=child,
                    info={"id": child, "parentID": SID},
                ),
                at=1001.5,
            )
            # Child streams its own content + idles on the same /event stream:
            await bench.feed(msg(child, "msg_c", "assistant"), at=1002.0)
            await bench.feed(
                part(child, "prt_c", "msg_c", "text", text="child text"), at=1002.1
            )
            await bench.feed(delta(child, "msg_c", "prt_c", "child delta"), at=1002.2)
            await bench.feed(idle(child), at=1002.3)
            await bench.advance(14.0)  # child idled 14 s before the parent
            assert [e.type for e in bench.out if e.type.startswith("attempt.")] == []
            await settle_turn(bench)
            await bench.feed(idle(), at=1020.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]
    assert all(
        "child" not in str(e.data.get("delta", ""))
        for e in out
        if e.type == "text_delta"
    )


def test_child_registered_from_task_part_metadata_at_completion() -> None:
    child = "ses_task_child"

    async def scenario():
        async with TranslatorBench(tool_map=TMAP) as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(SID, "p", "msg_a", "task", "c1", "running", input={}),
                at=1002.0,
            )
            # Child events BEFORE registration are inert (no attempt for them):
            await bench.feed(delta(child, "m", "p", "early"), at=1002.5)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "task",
                    "c1",
                    "completed",
                    input={},
                    output="Task completed in 5s.",
                    metadata={"sessionId": child},
                ),
                at=1003.0,
            )
            await bench.feed(idle(child), at=1003.5)
            await bench.advance(14.0)
            assert [e.type for e in bench.out if e.type.startswith("attempt.")] == []
            await bench.feed(idle(), at=1020.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]


# ---------------------------------------------------------------------------
# abort (D4: bridge-recorded state, idle != completed)
# ---------------------------------------------------------------------------


def test_note_abort_emits_cancelled_immediately_and_aftermath_is_tolerated(
    caplog,
) -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "bash",
                    "c1",
                    "running",
                    input={"command": "sleep 30"},
                ),
                at=1002.0,
            )
            bench.translator.note_abort(SID)
            await bench.settle()
            # Engine aftermath (spike §5b): error, tool error snapshot,
            # assistant error message, double idle — all post-terminal.
            await bench.feed(error("MessageAbortedError", "Aborted"), at=1004.0)
            await bench.feed(
                tool_part(
                    SID, "p", "msg_a", "bash", "c1", "error", input={}, output=""
                ),
                at=1004.5,
            )
            await bench.feed(
                completed_msg(finish=None, error={"name": "MessageAbortedError"}),
                at=1004.6,
            )
            await bench.feed(idle(), at=1004.7)
            await bench.feed(idle(), at=1005.0)
            await bench.advance(20.0)
        return bench.out

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.cancelled"
    ]
    cancelled = [e for e in out if e.type == "attempt.cancelled"][0].data
    assert cancelled == {"attempt_id": AID, "status": "cancelled"}
    # Abort aftermath is expected, not a hazard: no duplicate-bubble warnings.
    assert not [r for r in caplog.records if "dropping late" in r.message]
    # The running tool's heartbeat schedule died with the terminal.
    assert [e.type for e in out if e.type == "tool_heartbeat"] == []


def test_note_abort_without_active_attempt_is_noop() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            bench.translator.note_abort("ses_ghost")
            await open_turn(bench)
            bench.translator.note_abort(SID)  # cancels the live attempt
            await bench.settle()
            bench.translator.note_abort(SID)  # second abort: already terminal
            await bench.settle()
        return bench.out

    out = run(scenario())
    assert [e.type for e in out] == ["attempt.cancelled"]


# ---------------------------------------------------------------------------
# failure path (session.error) + kimaki #74 synthetic idle
# ---------------------------------------------------------------------------


def test_session_error_then_idle_fails_with_error_string() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(error("ProviderAuthError", "key expired"), at=1002.0)
            await bench.feed(idle(), at=1002.2)
            await bench.advance(1.0)
        return bench.out

    out = run(scenario())
    failed = [e for e in out if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].data == {
        "attempt_id": AID,
        "error": "ProviderAuthError: key expired",
    }


def test_session_error_without_idle_gets_synthetic_idle_failed() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(error(), at=1002.0)
            await bench.advance(7.9)
            assert not [e for e in bench.out if e.type.startswith("attempt.")]
            await bench.advance(0.2)  # kimaki #74 guard fires at quiescence_s
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == ["attempt.failed"]


def test_novel_activity_after_error_recovers_to_completed() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(error(), at=1002.0)
            # Engine recovers (e.g. continue_loop_on_deny): novel activity
            # clears the parked failure before any idle arrives.
            await bench.feed(msg(SID, "msg_u2", "user"), at=1003.0)
            await bench.feed(status("busy"), at=1003.1)
            await settle_turn(bench)
            await bench.feed(idle(), at=1010.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    assert [e.type for e in out if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]


# ---------------------------------------------------------------------------
# post-terminal guard (duplicate-bubble hazard, Agent.tsx:855-889)
# ---------------------------------------------------------------------------


def test_post_terminal_late_content_dropped_with_warning(caplog) -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            await bench.advance(8.05)
            assert bench.types()[-1] == "attempt.completed"
            count_at_terminal = len(bench.out)
            await bench.feed(
                part(SID, "prt_t", "msg_a", "text", text="FINAL late"), at=1012.0
            )
            await bench.feed(delta(SID, "msg_a", "prt_t", "late delta"), at=1012.1)
            return bench.out, count_at_terminal

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        out, count_at_terminal = run(scenario())
    assert len(out) == count_at_terminal  # both late events dropped
    late_warnings = [r for r in caplog.records if "dropping late" in r.message]
    assert len(late_warnings) == 2  # part.updated + delta


# ---------------------------------------------------------------------------
# heartbeats (B3) + terminal payload assembly
# ---------------------------------------------------------------------------


def test_heartbeats_every_3s_while_tool_running_then_stop() -> None:
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
                at=1000.0,
            )
            await bench.advance(10.0)
            await bench.feed(
                tool_part(
                    SID,
                    "p",
                    "msg_a",
                    "vibe-trading_backtest",
                    "c1",
                    "completed",
                    input={},
                    output="{}",
                ),
                at=1010.0,
            )
            await bench.advance(10.0)
        return bench.out

    out = run(scenario())
    beats = [e for e in out if e.type == "tool_heartbeat"]
    assert [b.data["elapsed_s"] for b in beats] == [3.0, 6.0, 9.0]
    assert all(
        b.data["tool"] == "backtest"
        and b.data["call_id"] == "c1"
        and b.data["attempt_id"] == AID
        for b in beats
    )
    assert HEARTBEAT_INTERVAL_S == 3.0


def test_completed_payload_enumerates_all_d5_fields() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(
                completed_msg(providerID="alibaba-cn", modelID="qwen3.8-max"), at=1002.4
            )
            await bench.feed(
                part(SID, "prt_t", "msg_a", "text", text="the answer"), at=1002.0
            )
            await bench.feed(idle(), at=1003.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    data = [e for e in out if e.type == "attempt.completed"][0].data
    assert set(data) == {
        "attempt_id",
        "status",
        "summary",
        "run_dir",
        "elapsed_ms",
        "provider",
        "model",
    }
    assert data["attempt_id"] == AID
    assert data["status"] == "completed"
    assert data["summary"] == "the answer"
    assert data["run_dir"] is None
    assert data["provider"] == "alibaba-cn"
    assert data["model"] == "qwen3.8-max"
    assert data["elapsed_ms"] == 11000  # note_attempt@1000.0 -> terminal@1011.0


def test_summary_is_last_natural_completion_not_tool_call_rounds() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(part(SID, "prt_1", "msg_a", "text", text="ONE"), at=1002.0)
            await bench.feed(completed_msg(finish="stop"), at=1002.5)
            # Continuation round: tool-calls rounds never settle the text...
            await bench.feed(msg(SID, "msg_a2", "assistant"), at=1009.0)
            await bench.feed(
                part(SID, "prt_2", "msg_a2", "text", text="TWO"), at=1009.5
            )
            await bench.feed(
                msg(
                    SID,
                    "msg_a2",
                    "assistant",
                    finish="tool-calls",
                    time={"created": 1, "completed": 2},
                ),
                at=1010.0,
            )
            # ...and an aborted message (finish null) never settles it either.
            await bench.feed(msg(SID, "msg_a3", "assistant"), at=1011.0)
            await bench.feed(
                part(SID, "prt_3", "msg_a3", "text", text="THREE"), at=1011.5
            )
            await bench.feed(
                msg(
                    SID,
                    "msg_a3",
                    "assistant",
                    finish=None,
                    time={"created": 1, "completed": 2},
                    error={"name": "X"},
                ),
                at=1012.0,
            )
            await bench.feed(msg(SID, "msg_a4", "assistant"), at=1013.0)
            await bench.feed(
                part(SID, "prt_4", "msg_a4", "text", text="FOUR"), at=1013.5
            )
            await bench.feed(completed_msg(mid="msg_a4"), at=1014.0)
            await bench.feed(idle(), at=1015.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    data = [e for e in out if e.type == "attempt.completed"][0].data
    assert data["summary"] == "FOUR"


def test_new_attempt_after_terminal_resets_state() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await settle_turn(bench, text="first")
            await bench.feed(idle(), at=1003.0)
            await bench.advance(8.05)
            first_done = len(bench.out)
            bench.translator.note_attempt(SID, "att_2")
            await bench.feed(msg(SID, "msg_u", "user"), at=1020.0)
            await bench.feed(msg(SID, "msg_b", "assistant"), at=1020.5)
            await bench.feed(
                part(SID, "prt_b", "msg_b", "text", text="second"), at=1021.0
            )
            await bench.feed(
                msg(
                    SID,
                    "msg_b",
                    "assistant",
                    finish="stop",
                    time={"created": 1, "completed": 2},
                ),
                at=1021.5,
            )
            await bench.feed(idle(), at=1022.0)
            await bench.advance(8.05)
        return bench.out, first_done

    out, first_done = run(scenario())
    second = out[first_done:]
    assert [e.type for e in second if e.type.startswith("attempt.")] == [
        "attempt.completed"
    ]
    terminal = [e for e in second if e.type == "attempt.completed"][0].data
    assert terminal["attempt_id"] == "att_2"
    assert terminal["summary"] == "second"
    assert all(
        e.data.get("attempt_id") == "att_2" for e in second if e.type == "text_delta"
    )


def test_note_attempt_replacing_active_attempt_warns(caplog) -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            bench.translator.note_attempt(SID, "att_replacement")
            await bench.settle()

    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        run(scenario())
    assert any("replaces active attempt" in r.message for r in caplog.records)


def test_multi_session_streams_stay_independent() -> None:
    async def scenario():
        async with TranslatorBench() as bench:
            bench.translator.note_attempt("ses_1", "att_s1")
            bench.translator.note_attempt("ses_2", "att_s2")
            done = {"created": 1, "completed": 2}
            await bench.feed(msg("ses_1", "u1", "user"), at=1000.0)
            await bench.feed(msg("ses_1", "a1", "assistant"), at=1000.5)
            await bench.feed(part("ses_1", "p1", "a1", "text", text="one"), at=1001.0)
            await bench.feed(
                msg("ses_1", "a1", "assistant", finish="stop", time=done), at=1001.4
            )
            await bench.feed(msg("ses_2", "u2", "user"), at=1001.1)
            await bench.feed(msg("ses_2", "a2", "assistant"), at=1001.2)
            await bench.feed(part("ses_2", "p2", "a2", "text", text="two"), at=1001.3)
            await bench.feed(
                msg("ses_2", "a2", "assistant", finish="stop", time=done), at=1001.5
            )
            await bench.feed(idle("ses_1"), at=1002.0)
            await bench.advance(8.05)
            assert [e.type for e in bench.out if e.type.startswith("attempt.")] == [
                "attempt.completed"
            ]
            await bench.feed(idle("ses_2"), at=1011.0)
            await bench.advance(8.05)
        return bench.out

    out = run(scenario())
    terminals = [e for e in out if e.type.startswith("attempt.")]
    assert [t.data["attempt_id"] for t in terminals] == ["att_s1", "att_s2"]
    assert [t.data["summary"] for t in terminals] == ["one", "two"]


def test_session_deleted_forgets_context_and_cascaded_children() -> None:
    child = "ses_child"

    async def scenario():
        async with TranslatorBench() as bench:
            await open_turn(bench)
            await bench.feed(
                wire_event(
                    "session.created",
                    sessionID=child,
                    info={"id": child, "parentID": SID},
                ),
                at=1001.5,
            )
            await bench.feed(
                wire_event("session.deleted", sessionID=SID, info={"id": SID}),
                at=1002.0,
            )
            await settle_turn(bench)
            await bench.feed(idle(), at=1003.0)
            await bench.advance(30.0)
        return bench.out

    assert [e.type for e in run(scenario()) if e.type.startswith("attempt.")] == []


# ---------------------------------------------------------------------------
# public API surface
# ---------------------------------------------------------------------------


def test_quiescence_s_must_be_positive() -> None:
    with pytest.raises(ValueError):
        EventTranslator(quiescence_s=0.0)


def test_unsupported_child_events_policy_degrades_to_drop_with_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="opencode_bridge"):
        EventTranslator(child_events="keep")
    assert any("child_events" in r.message for r in caplog.records)


def test_second_concurrent_consumer_is_rejected() -> None:
    async def scenario():
        bench = TranslatorBench()
        await bench.__aenter__()
        second = bench.translator.events()
        with pytest.raises(RuntimeError):
            await second.__anext__()
        await second.aclose()
        await bench.__aexit__(None, None, None)

    run(scenario())


def test_aclose_ends_iteration_and_later_feeds_are_ignored() -> None:
    async def scenario():
        bench = TranslatorBench()
        collector = asyncio.ensure_future(bench._collect())
        await asyncio.sleep(0)
        await bench.translator.aclose()
        await asyncio.wait_for(collector, timeout=2.0)
        await bench.translator.feed(wire_event("session.idle", sessionID=SID))
        await bench.translator.aclose()  # idempotent
        await asyncio.sleep(0)
        return bench.out

    assert run(scenario()) == []
