#!/usr/bin/env python3
"""T9 IM-streaming live evidence runner (mock platform, real rig serve).

One command for the plan-T9 acceptance evidence ("双平台（mock+真）流式编辑
证据；关闭开关回退终态单条"). Reuses the T7 rig (start_rig.py, UNMODIFIED)
and T8's in-process IM stack helpers (tests/e2e_engine_bridge/imlib.py,
UNMODIFIED — the parallel T8 task owns that file; we only import it):

* REAL opencode serve (rig port 14097) + REAL bridge service (production
  startup sequence via imlib.build_bridge_service) + REAL ChannelRuntime +
  REAL ChannelManager (coalescing manager.py:323-367, dedup, `_streamed`
  skip) + a StreamingMockChannel adapter (T8's MockChannel subclassed with
  telegram-style progressive-edit send_delta recording — the mock platform
  stand-in; src/channels/ stays zero-diff);
* the T9 ImStreamProducer attached exactly like wiring.build_session_service
  does (the in-process stack injects the documented plumbing_resolver seam
  instead of the src.api.state singletons, which only the gateway process
  populates).

Scenarios (one stack, per-channel `streaming` switch flipped between turns —
the gate is re-checked at every attempt open by design):

  A. streaming ON  — short no-tool reply: progressive edits (create →
     edit* → finalize), edits << text_delta events (coalescing+throttle =
     edit-not-spam), NO duplicate final message (runtime final marked
     `_streamed` → manager skips send; proven via the producer's log line
     and the absence of any `message` record);
  B. streaming OFF — same prompt: ZERO send_delta calls, exactly ONE
     terminal message (the unchanged polling-contract fallback);
  C. streaming ON  — tool-heavy turn (bash echo; backtest/read_file MCP
     tools are governance-disabled per T7 FINDINGS #1): flush ordering —
     pre-tool text is edited into the preview BEFORE the tool boundary,
     segment/terminal ordering preserved, no duplicate final.

Hygiene: engine sessions are priced (GET /session/:id/message cost fields)
and DELETEd here (the in-process store is NOT the rig vt_home stop_rig.py
sweeps); cost.json + logs land in this evidence dir. Stdlib + repo imports.

Usage (rig must be up — see FINDINGS.md for the exact commands)::

    python3 run_t9_im_stream.py [--rig-root /tmp/vt-t9-rig] [--turn-timeout 240]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_RIG_ROOT = Path(os.environ.get("T9_RIG_ROOT", "/tmp/vt-t9-rig"))

# Scratch home MUST be set before any src.* import (helpers.py freezes dirs
# at import time). The user's real ~/.vibe-trading is never written.
os.environ.setdefault("VIBE_TRADING_HOME", str(DEFAULT_RIG_ROOT / "vt-home"))

REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from tests.e2e_engine_bridge import imlib, riglib  # noqa: E402

PROMPT_PLAIN = (
    "Reply with exactly two short sentences welcoming the user to the "
    "streaming test. Do not use any tools. Do not iterate. No todo list."
)
PROMPT_TOOL = (
    "First reply with exactly the sentence 'Checking now.' Then run exactly "
    "one bash command: echo t9-flush-check. Then reply with one short "
    "sentence containing the command output. Do not iterate. No todo list. "
    "No subagents."
)


# ---------------------------------------------------------------------------
# Mock platform adapter (telegram-style progressive editing, recorded)
# ---------------------------------------------------------------------------


class StreamingMockChannel(imlib.MockChannel):
    """T8's MockChannel + a recorded telegram-style ``send_delta``.

    Mirrors telegram.py:920 semantics: first delta of a (chat, stream) opens
    a preview message, subsequent deltas edit it, ``_stream_end`` finalizes
    with the accumulated text. Every adapter call is recorded (kind, t,
    stream_id, chars) — the record IS the evidence artifact.
    """

    def __init__(self, config: Any, bus: Any) -> None:
        super().__init__(config, bus)
        self.stream_log: List[Dict[str, Any]] = []
        self._bufs: Dict[tuple, Dict[str, Any]] = {}
        self._preview_seq = 0

    async def send(self, msg) -> None:
        self.stream_log.append(
            {
                "kind": "message",
                "t": time.time(),
                "chat_id": msg.chat_id,
                "content": msg.content,
                "metadata": dict(msg.metadata or {}),
            }
        )
        await super().send(msg)

    async def send_delta(
        self, chat_id: str, delta: str, metadata: Optional[dict] = None
    ) -> None:
        meta = dict(metadata or {})
        sid = meta.get("_stream_id")
        key = (chat_id, sid)
        if meta.get("_stream_end"):
            buf = self._bufs.pop(key, None)
            self.stream_log.append(
                {
                    "kind": "finalize",
                    "t": time.time(),
                    "chat_id": chat_id,
                    "stream_id": sid,
                    "resuming": bool(meta.get("_resuming")),
                    "text": (buf or {}).get("text", ""),
                    "edits": (buf or {}).get("edits", 0),
                }
            )
            return
        buf = self._bufs.get(key)
        if buf is None:
            self._preview_seq += 1
            buf = {
                "stream_id": sid,
                "text": "",
                "preview_id": f"preview-{self._preview_seq}",
                "edits": 0,
            }
            self._bufs[key] = buf
            self.stream_log.append(
                {
                    "kind": "preview_create",
                    "t": time.time(),
                    "chat_id": chat_id,
                    "stream_id": sid,
                    "preview_id": buf["preview_id"],
                }
            )
        buf["text"] += delta
        buf["edits"] += 1
        self.stream_log.append(
            {
                "kind": "preview_edit",
                "t": time.time(),
                "chat_id": chat_id,
                "stream_id": sid,
                "preview_id": buf["preview_id"],
                "chars": len(buf["text"]),
                "delta_chars": len(delta),
            }
        )


class LogCapture(logging.Handler):
    """Captures opencode_bridge log records (the `_streamed` mark proof)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[Dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {"t": record.created, "level": record.levelname, "msg": record.getMessage()}
        )


# ---------------------------------------------------------------------------
# Stack (imlib.build_im_stack variant: streaming adapter + T9 producer)
# ---------------------------------------------------------------------------


@dataclass
class T9Stack:
    bus: Any
    mock: StreamingMockChannel
    manager: Any
    runtime: Any
    service: Any
    driver: Any
    store: Any
    recorder: Any
    producer: Any
    log_capture: LogCapture = field(default_factory=LogCapture)


async def build_t9_stack(serve_url: str, root: Path) -> T9Stack:
    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime
    from src.opencode_bridge.im_stream import ImStreamProducer

    service, store, driver = await imlib.build_bridge_service(
        serve_url, root / "im-store" / "sessions"
    )
    bus = MessageBus()
    mock = StreamingMockChannel(
        {"enabled": True, "allow_from": ["*"], "streaming": True}, bus
    )
    manager = ChannelManager({}, bus, session_service=service)
    manager.channels[imlib.MOCK_CHANNEL_NAME] = mock
    # The production attach point is wiring.build_session_service (covered by
    # unit+wiring tests); in-process we inject the resolver seam directly.
    producer = ImStreamProducer(
        store=store, plumbing_resolver=lambda: (bus, manager)
    ).attach(service)
    runtime = ChannelRuntime(
        bus=bus,
        session_service=service,
        manager=manager,
        session_map_path=root / "im-sessions" / "sessions.json",
        reply_timeout_s=imlib.PROD_REPLY_TIMEOUT_S,
        poll_interval_s=imlib.PROD_POLL_INTERVAL_S,
        operators=["t9-operator"],
    )
    recorder = imlib.BusRecorder(service.event_bus)
    capture = LogCapture()
    bridge_logger = logging.getLogger("opencode_bridge")
    bridge_logger.setLevel(logging.INFO)  # else INFO never reaches the handler
    bridge_logger.addHandler(capture)
    await runtime.start(start_manager=True)
    return T9Stack(
        bus=bus,
        mock=mock,
        manager=manager,
        runtime=runtime,
        service=service,
        driver=driver,
        store=store,
        recorder=recorder,
        producer=producer,
        log_capture=capture,
    )


async def run_turn(stack: T9Stack, chat_id: str, prompt: str, timeout_s: float) -> Dict[str, Any]:
    """Inject one inbound message; wait for the attempt terminal + settle."""
    log_mark = len(stack.log_capture.records)
    delta_mark = len(stack.recorder.events)
    adapter_mark = len(stack.mock.stream_log)
    t0 = time.time()
    await stack.mock.inject(
        prompt, sender_id="t9-user", chat_id=chat_id, message_id=f"t9-{time.time_ns()}"
    )
    session_id = None
    deadline = t0 + timeout_s
    attempt_id = None
    status = None
    while time.time() < deadline:
        session_id = stack.runtime._session_map.get(f"{imlib.MOCK_CHANNEL_NAME}:{chat_id}")
        if session_id:
            attempts = [
                a
                for a in stack.store.list_attempts()
                if a.session_id == session_id
            ]
            if attempts:
                attempt_id = attempts[-1].attempt_id
                status = imlib.attempt_status(stack.store, attempt_id)
                if status in imlib._TERMINAL_STATUSES:
                    break
        await asyncio.sleep(0.25)
    await asyncio.sleep(1.5)  # let the outbound dispatcher drain (coalescing window)
    reply = None
    if session_id:
        for message in reversed(stack.store.get_messages(session_id, limit=50)):
            if message.role == "assistant" and (
                attempt_id is None or message.linked_attempt_id == attempt_id
            ):
                reply = message
                break
    return {
        "chat_id": chat_id,
        "session_id": session_id,
        "attempt_id": attempt_id,
        "status": status,
        "seconds": round(time.time() - t0, 2),
        "reply_content": reply.content if reply else None,
        "log_mark": log_mark,
        "delta_mark": delta_mark,
        "adapter_mark": adapter_mark,
    }


def sse_counts(stack: T9Stack, mark: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for event in stack.recorder.events[mark:]:
        counts[event["type"]] = counts.get(event["type"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_a(stack: T9Stack, turn: Dict[str, Any], rec: riglib.GroupRecorder) -> None:
    log = stack.mock.stream_log[turn["adapter_mark"] :]
    sse = sse_counts(stack, turn["delta_mark"])
    logs = stack.log_capture.records[turn["log_mark"] :]
    imlib.write_json(HERE / "streaming-on-adapter-log.json", log)
    creates = [r for r in log if r["kind"] == "preview_create"]
    edits = [r for r in log if r["kind"] == "preview_edit"]
    finals = [r for r in log if r["kind"] == "finalize" and not r["resuming"]]
    messages = [r for r in log if r["kind"] == "message"]
    rec.info("sse_event_counts", sse)
    rec.info("adapter_call_counts", {k: sum(1 for r in log if r["kind"] == k) for k in {r["kind"] for r in log}})
    rec.check("attempt_completed", turn["status"] == "completed", str(turn["status"]))
    rec.check("preview_created", len(creates) >= 1, f"{len(creates)} creates")
    rec.check("progressive_edits", len(edits) >= 2, f"{len(edits)} edits")
    rec.check(
        "edits_not_spam (send_delta << text_delta events)",
        sse.get("text_delta", 0) > len(edits) + len(creates),
        f"text_delta={sse.get('text_delta', 0)} adapter_deltas={len(edits) + len(creates)}",
    )
    rec.check("single_terminal_finalize", len(finals) == 1, f"{len(finals)} finals")
    rec.check(
        "no_duplicate_final_message (zero channel.send records)",
        messages == [],
        f"{len(messages)} message records: {[m['content'][:60] for m in messages]}",
    )
    rec.check(
        "runtime_final_marked_streamed (producer log)",
        any("marked _streamed" in r["msg"] for r in logs),
        json.dumps([r["msg"] for r in logs if "_streamed" in r["msg"]]),
    )
    if finals:
        normalized = " ".join((turn["reply_content"] or "").split())
        rec.check(
            "finalized_stream_text == persisted reply",
            " ".join(finals[0]["text"].split()) == normalized,
            f"stream={finals[0]['text'][:80]!r} reply={(turn['reply_content'] or '')[:80]!r}",
        )


def scenario_b(stack: T9Stack, turn: Dict[str, Any], rec: riglib.GroupRecorder) -> None:
    log = stack.mock.stream_log[turn["adapter_mark"] :]
    logs = stack.log_capture.records[turn["log_mark"] :]
    imlib.write_json(HERE / "streaming-off-adapter-log.json", log)
    stream_records = [r for r in log if r["kind"] != "message"]
    messages = [r for r in log if r["kind"] == "message"]
    rec.check("attempt_completed", turn["status"] == "completed", str(turn["status"]))
    rec.check(
        "zero_stream_publications (switch OFF)",
        stream_records == [],
        f"{len(stream_records)} stream records",
    )
    rec.check(
        "exactly_one_terminal_message",
        len(messages) == 1,
        f"{len(messages)} messages",
    )
    if messages:
        rec.check(
            "terminal_message_unmarked (_streamed absent)",
            "_streamed" not in messages[0]["metadata"],
            json.dumps(messages[0]["metadata"]),
        )
        rec.check(
            "terminal_message == persisted reply",
            messages[0]["content"] == turn["reply_content"],
            (messages[0]["content"] or "")[:80],
        )
    rec.check(
        "no_streamed_mark_logged",
        not any("marked _streamed" in r["msg"] for r in logs),
        "",
    )


def scenario_c(stack: T9Stack, turn: Dict[str, Any], rec: riglib.GroupRecorder) -> None:
    log = stack.mock.stream_log[turn["adapter_mark"] :]
    sse = sse_counts(stack, turn["delta_mark"])
    imlib.write_json(HERE / "tool-order-adapter-log.json", log)
    tool_calls = stack.recorder.events[turn["delta_mark"] :]
    first_tool_t = next(
        (e["t"] for e in tool_calls if e["type"] == "tool_call"), None
    )
    finals = [r for r in log if r["kind"] == "finalize" and not r["resuming"]]
    resuming = [r for r in log if r["kind"] == "finalize" and r["resuming"]]
    messages = [r for r in log if r["kind"] == "message"]
    rec.info("sse_event_counts", sse)
    rec.check("attempt_completed", turn["status"] == "completed", str(turn["status"]))
    rec.check("tool_call_observed", sse.get("tool_call", 0) >= 1, str(sse))
    pre_tool_edits = [
        r
        for r in log
        if r["kind"] == "preview_edit"
        and first_tool_t is not None
        and r["t"] - stack.recorder.t0 <= first_tool_t + 1.0
    ]
    rec.check(
        "pre_tool_text_flushed_before_tool_boundary",
        len(pre_tool_edits) >= 1,
        f"first_tool_t={first_tool_t} pre_tool_edits={len(pre_tool_edits)}",
    )
    rec.check("single_terminal_finalize", len(finals) == 1, f"{len(finals)} finals")
    # Flush ordering across segments: ALL finalized text (intermediate
    # _resuming segments + the terminal one) concatenated in emission order
    # must keep pre-tool text before post-tool text.
    finalized = [r for r in log if r["kind"] == "finalize"]
    ordered_text = "".join(r["text"] for r in finalized)
    rec.check(
        "flush_order: pre-tool text precedes post-tool text",
        "Checking now." in ordered_text
        and ordered_text.find("Checking now.") < ordered_text.find("t9-flush-check"),
        ordered_text[:200],
    )
    rec.check(
        "intermediate_segments_marked_resuming",
        all(r["resuming"] for r in resuming),
        f"{len(resuming)} intermediate finalizes",
    )
    rec.check(
        "no_duplicate_final_message",
        messages == [],
        f"{len(messages)} message records",
    )


# ---------------------------------------------------------------------------
# Engine-session hygiene + cost (in-process store is outside stop_rig's sweep)
# ---------------------------------------------------------------------------


def _req(base: str, method: str, path: str, timeout: float = 15.0):
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except OSError:
        return None, None


def cleanup_and_cost(serve_url: str, engine_ids: List[str]) -> Dict[str, Any]:
    costs: Dict[str, float] = {}
    for engine_id in engine_ids:
        status, messages = _req(serve_url, "GET", f"/session/{engine_id}/message")
        total = 0.0
        if status == 200 and isinstance(messages, list):
            for entry in messages:
                info = entry.get("info") if isinstance(entry, dict) else None
                cost = info.get("cost") if isinstance(info, dict) else None
                if isinstance(cost, (int, float)):
                    total += float(cost)
        costs[engine_id] = round(total, 6)
        dstatus, _ = _req(serve_url, "DELETE", f"/session/{engine_id}")
        print(f"[t9] DELETE engine session {engine_id} -> {dstatus}")
    report = {
        "engine_sessions": engine_ids,
        "cost_usd_per_session": costs,
        "cost_usd_total": round(sum(costs.values()), 6),
        "note": (
            "serve-side model cost of the THREE T9 in-process turns (A: streaming ON, "
            "B: streaming OFF, C: tool-heavy). Engine sessions DELETEd here — the "
            "in-process store is not the rig vt_home that stop_rig.py sweeps."
        ),
    }
    imlib.write_json(HERE / "cost.json", report)
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(rig_root: Path, turn_timeout: float) -> int:
    state_path = rig_root / "rig_state.json"
    if not state_path.exists():
        print(f"[t9] no rig_state.json at {state_path} — start the rig first")
        return 2
    rig_state = json.loads(state_path.read_text(encoding="utf-8"))
    serve_url = rig_state["serve_url"]
    root = Path(rig_root)
    stack = await build_t9_stack(serve_url, root)
    exit_code = 0
    turns: Dict[str, Any] = {}
    try:
        for name, chat_id, prompt, streaming, fn in (
            ("streaming-on", "t9-on", PROMPT_PLAIN, True, scenario_a),
            ("streaming-off", "t9-off", PROMPT_PLAIN, False, scenario_b),
            ("tool-order", "t9-tool", PROMPT_TOOL, True, scenario_c),
        ):
            stack.mock.config["streaming"] = streaming
            rec = riglib.GroupRecorder(HERE, name)
            print(f"[t9] scenario {name} (streaming={streaming}) ...")
            try:
                turn = await run_turn(stack, chat_id, prompt, turn_timeout)
                turns[name] = turn
                fn(stack, turn, rec)
                rec.write(True)
                print(f"[t9] scenario {name}: PASS ({turn['seconds']}s)")
            except AssertionError as exc:
                rec.write(False)
                exit_code = 1
                print(f"[t9] scenario {name}: FAIL — {exc}")
        imlib.write_json(HERE / "turns.json", turns)
        imlib.write_json(HERE / "sse-events.json", stack.recorder.events)
    finally:
        engine_ids = list(stack.service._engine_sessions.values())
        await stack.runtime.stop()
        logging.getLogger("opencode_bridge").removeHandler(stack.log_capture)
        imlib.write_json(HERE / "producer-log.json", stack.log_capture.records)
        await stack.service.aclose()
        if engine_ids:
            cleanup_and_cost(serve_url, engine_ids)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--turn-timeout", type=float, default=240.0)
    args = parser.parse_args()
    return asyncio.run(main_async(args.rig_root, args.turn_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
