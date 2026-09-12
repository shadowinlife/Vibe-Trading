"""Engine-state query + parsing for startup recovery (plan T6, helper split).

Reads the opencode ``GET /session/:id/message`` payload (the ``{info, parts}``
entries :meth:`~src.opencode_bridge.driver.OpencodeDriver.messages` passes
through untouched) and answers the one question reconciliation asks: what
happened to the turn behind a pending/running vt attempt while the gateway was
down? The answer is a :class:`TurnState` plus everything branch 2 (backfill)
and branch 1 (re-attach trail rebuild) need, derived ONLY from engine state —
nothing here fabricates content the engine did not report.

Plan-anchored semantics: natural completion (D4) = ``info.time.completed``
set AND ``info.finish != "tool-calls"`` (tool-calls means the ReAct loop
continues); finalized text (D4) = the LAST natural completion's text parts
only (reasoning never enters chat text, ``info.summary is True`` compaction
messages are filtered, D5); OmO continuation directives (D4) are skipped
when matching the prompt; a natural completion newer than the continuation
grace (OmO re-prompts 6.4 s after idle, spike §5c) is AMBIGUOUS -> RUNNING
(the re-attach watch timer re-probes it); run_dir is regex-harvested with
T4's frozen patterns (import is reuse) and trail entries mirror the native
``_record_tool_trail_event`` shape (D5/D6).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.session.models import Attempt

from .errors import OpencodeBridgeError, OpencodeHttpError
from .translator.text import is_omo_directive
from .translator.tools import ARG_VALUE_CHARS, PREVIEW_CHARS, _RUN_DIR_PATTERNS

logger = logging.getLogger("opencode_bridge")

__all__ = ["EngineTurnProbe", "TurnState", "probe_engine", "probe_turn"]


class TurnState(str, Enum):
    """Reconciliation branch for one pending/running attempt.

    ``RUNNING`` -> branch 1 (re-attach); ``FINISHED`` -> branch 2 (backfill);
    every other state -> branch 3 (existing interrupted semantics).
    """

    #: The engine is still working on the turn (or ambiguously may continue).
    RUNNING = "running"
    #: The turn reached its final natural completion while the gateway was down.
    FINISHED = "finished"
    #: The engine has no such session (404), is unreachable, or the mapping
    #: / driver query is unavailable.
    SESSION_GONE = "session_gone"
    #: The engine session exists but never received a user prompt for the
    #: attempt (gateway died before ``prompt_async``); never re-send (T6).
    NO_PROMPT = "no_prompt"
    #: The last real user message does not carry the attempt's prompt —
    #: engine and store disagree; land interrupted rather than guess (D3).
    PROMPT_MISMATCH = "prompt_mismatch"


@dataclass(frozen=True, slots=True)
class EngineTurnProbe:
    """One attempt's reconciled engine state.

    Attributes:
        state: The reconciliation branch to take.
        reason: Human-readable cause (logs and the reconciliation report).
        finalized_text: D4 finalized text of the turn (branch 2 summary).
        provider: ``info.providerID`` of the final natural completion.
        model: ``info.modelID`` of the final natural completion.
        elapsed_ms: Turn span from the engine's own timestamps (matched user
            ``time.created`` -> final ``time.completed``); ``None`` if absent.
        run_dir: Regex-harvested backtest run directory, if any.
        trail: Rebuilt native-shaped tool-trail entries for the turn.
    """

    state: TurnState
    reason: str = ""
    finalized_text: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    elapsed_ms: Optional[int] = None
    run_dir: Optional[str] = None
    trail: List[Dict[str, Any]] = field(default_factory=list)


def _identity(name: str) -> str:
    return name


def _as_mapping(value: Any) -> Mapping[Any, Any]:
    return value if isinstance(value, Mapping) else {}


def _entry_info(entry: Any) -> Mapping[Any, Any]:
    return _as_mapping(_as_mapping(entry).get("info"))


def _entry_parts(entry: Any) -> Sequence[Any]:
    parts = _as_mapping(entry).get("parts")
    return (
        parts
        if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes))
        else ()
    )


def _message_text(entry: Any) -> str:
    """Concatenate an entry's text parts (reasoning never enters chat text)."""
    chunks: List[str] = []
    for part in _entry_parts(entry):
        part = _as_mapping(part)
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "".join(chunks)


def _is_transcript_visible(info: Mapping[Any, Any]) -> bool:
    """Filter ``summary is True`` compaction messages (D5)."""
    return info.get("summary") is not True


def _completed_ms(info: Mapping[Any, Any]) -> Optional[int]:
    moment = _as_mapping(info.get("time")).get("completed")
    return (
        moment
        if isinstance(moment, (int, float)) and not isinstance(moment, bool)
        else None
    )


def _created_ms(info: Mapping[Any, Any]) -> Optional[int]:
    moment = _as_mapping(info.get("time")).get("created")
    return (
        moment
        if isinstance(moment, (int, float)) and not isinstance(moment, bool)
        else None
    )


def _match_prompt_entry(
    entries: Sequence[Any], prompt: str
) -> tuple[Optional[int], Optional[str]]:
    """Locate the engine-side user message carrying *prompt*.

    Returns:
        ``(index, failure_state)`` — the matching entry's index, or ``None``
        plus the branch-3 :class:`TurnState` explaining why.
    """
    last_user: Optional[int] = None
    for index, entry in enumerate(entries):
        info = _entry_info(entry)
        if info.get("role") != "user" or not _is_transcript_visible(info):
            continue
        if is_omo_directive(_message_text(entry)):
            continue
        last_user = index
    if last_user is None:
        return None, TurnState.NO_PROMPT
    text = _message_text(entries[last_user])
    if prompt and not text.endswith(prompt):
        return None, TurnState.PROMPT_MISMATCH
    return last_user, None


def _harvest_run_dir(entries: Sequence[Any]) -> Optional[str]:
    """run_dir from backtest tool output (T4 ``_harvest``: last wins)."""
    run_dir: Optional[str] = None
    for entry in entries:
        for part in _entry_parts(entry):
            part = _as_mapping(part)
            if part.get("type") != "tool":
                continue
            tool = part.get("tool")
            state = _as_mapping(part.get("state"))
            output = state.get("output")
            if not isinstance(tool, str) or "backtest" not in tool:
                continue
            if not isinstance(output, str):
                continue
            for pattern in _RUN_DIR_PATTERNS:
                match = pattern.search(output)
                if match:
                    run_dir = match.group(1)
                    break
    return run_dir


def _rebuild_tool_trail(
    entries: Sequence[Any], bare_name: Callable[[str], str]
) -> List[Dict[str, Any]]:
    """Rebuild native-shaped tool-trail entries from the turn's tool parts.

    Mirrors ``_record_tool_trail_event``'s record shape using the engine's
    own timestamps (real state, never fabricated). A ``running`` part yields
    a ``status="running"`` entry so a post-re-attach ``tool_result``
    consolidates onto it by ``call_id`` (native matching semantics).
    """
    trail: List[Dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    for entry in entries:
        for part in _entry_parts(entry):
            part = _as_mapping(part)
            if part.get("type") != "tool":
                continue
            state = _as_mapping(part.get("state"))
            status = state.get("status")
            if not isinstance(status, str) or status not in {
                "running",
                "completed",
                "error",
            }:
                continue  # pending (no arguments yet) / unknown: wait
            tool_raw = part.get("tool")
            tool = bare_name(tool_raw) if isinstance(tool_raw, str) else ""
            if not tool:
                continue
            call_id = part.get("callID") or part.get("id")
            raw_input = state.get("input")
            arguments: Dict[str, str] = (
                {
                    str(key): str(value)[:ARG_VALUE_CHARS]
                    for key, value in raw_input.items()
                }
                if isinstance(raw_input, Mapping)
                else {}
            )
            part_time = _as_mapping(state.get("time"))
            start = part_time.get("start")
            end = part_time.get("end")
            record: Dict[str, Any] = {
                "tool": tool,
                "arguments": arguments,
                "timestamp": start if isinstance(start, int) else now_ms,
            }
            if isinstance(call_id, str) and call_id:
                record["call_id"] = call_id
            if status == "running":
                record["status"] = "running"
            else:
                record["status"] = "ok" if status == "completed" else "error"
                if isinstance(start, int) and isinstance(end, int):
                    record["elapsed_ms"] = max(0, int(end - start))
                output = state.get("output")
                record["preview"] = (
                    output[:PREVIEW_CHARS] if isinstance(output, str) else ""
                )
            trail.append(record)
    return trail


def probe_turn(
    entries: Sequence[Any],
    prompt: str,
    *,
    now_ms: Optional[int] = None,
    continuation_grace_ms: int,
    bare_name: Callable[[str], str] = _identity,
) -> EngineTurnProbe:
    """Classify one attempt's turn from the engine's message list.

    Args:
        entries: ``GET /session/:id/message`` payload (``{info, parts}``).
        prompt: The vt attempt's raw prompt (the engine-side user message is
            the D8 injection block followed by exactly this text).
        now_ms: Current epoch ms (default: local clock; gateway and engine
            share a host in every deployed form, D2/T10).
        continuation_grace_ms: A natural completion newer than this is
            ambiguous (OmO stop-hook may still re-prompt) -> RUNNING.
        bare_name: opencode tool id -> bare vt name mapper; identity when
            the driver exposes none.

    Returns:
        The :class:`EngineTurnProbe` deciding the recovery branch.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    index, failure = _match_prompt_entry(entries, prompt)
    if index is None:
        state = failure or TurnState.NO_PROMPT
        return EngineTurnProbe(
            state=state,
            reason=(
                "engine session carries no user prompt for this attempt"
                if state is TurnState.NO_PROMPT
                else "engine session's last user message does not match the attempt prompt"
            ),
        )
    turn_entries = [entry for entry in entries[index + 1 :]]
    trail = _rebuild_tool_trail(turn_entries, bare_name)
    assistants = [
        entry
        for entry in turn_entries
        if _entry_info(entry).get("role") == "assistant"
        and _is_transcript_visible(_entry_info(entry))
    ]
    if not assistants:
        # Prompt accepted, no assistant message yet: the run is live.
        return EngineTurnProbe(
            state=TurnState.RUNNING,
            reason="prompt accepted, no output yet",
            trail=trail,
        )

    last_info = _entry_info(assistants[-1])
    completed = _completed_ms(last_info)
    if completed is None:
        return EngineTurnProbe(
            state=TurnState.RUNNING,
            reason="assistant message still in flight",
            trail=trail,
        )
    if last_info.get("finish") == "tool-calls":
        return EngineTurnProbe(
            state=TurnState.RUNNING,
            reason="ReAct loop continues (finish=tool-calls)",
            trail=trail,
        )
    if now_ms - completed < continuation_grace_ms:
        return EngineTurnProbe(
            state=TurnState.RUNNING,
            reason="natural completion inside the OmO continuation window (ambiguous)",
            trail=trail,
        )

    created = _created_ms(_entry_info(entries[index]))
    elapsed = max(0, int(completed - created)) if isinstance(created, int) else None
    provider = last_info.get("providerID")
    model = last_info.get("modelID")
    return EngineTurnProbe(
        state=TurnState.FINISHED,
        reason="turn reached its final natural completion while the gateway was down",
        finalized_text=_message_text(assistants[-1]),
        provider=provider if isinstance(provider, str) and provider else None,
        model=model if isinstance(model, str) and model else None,
        elapsed_ms=elapsed,
        run_dir=_harvest_run_dir(turn_entries),
        trail=trail,
    )


async def probe_engine(
    driver: Any,
    engine_session_id: Optional[str],
    attempt: Attempt,
    *,
    continuation_grace_s: float,
) -> EngineTurnProbe:
    """Query one attempt's engine session and classify its turn.

    Any inability to CONFIRM a live run (no persisted mapping, driver
    without ``messages``, 404, unreachable serve, transport error) yields a
    branch-3 probe: the attempt lands interrupted rather than hanging a
    polling consumer (IM ``_wait_for_reply`` budget; plan T6 QA failure
    scenario "opencode also died").

    Args:
        driver: The bridge driver (``messages()`` / ``bare_tool_name``).
        engine_session_id: Persisted engine session id, if any.
        attempt: The attempt being reconciled (its prompt anchors matching).
        continuation_grace_s: OmO continuation ambiguity window.

    Returns:
        The :class:`EngineTurnProbe` deciding the recovery branch.
    """
    if engine_session_id is None:
        return EngineTurnProbe(
            state=TurnState.SESSION_GONE,
            reason="no persisted engine-session mapping for the session",
        )
    messages_fn = getattr(driver, "messages", None)
    if messages_fn is None:
        return EngineTurnProbe(
            state=TurnState.SESSION_GONE,
            reason="driver cannot query engine messages",
        )
    try:
        entries = await messages_fn(engine_session_id)
    except OpencodeHttpError as exc:
        reason = (
            "engine has no such session (404)"
            if exc.status_code == 404
            else f"engine returned HTTP {exc.status_code} for the session"
        )
        return EngineTurnProbe(state=TurnState.SESSION_GONE, reason=reason)
    except OpencodeBridgeError as exc:
        return EngineTurnProbe(
            state=TurnState.SESSION_GONE,
            reason=f"engine state unavailable: {exc}",
        )
    bare_name = getattr(driver, "bare_tool_name", None)
    return probe_turn(
        entries,
        attempt.prompt,
        continuation_grace_ms=int(continuation_grace_s * 1000),
        bare_name=bare_name if callable(bare_name) else str,
    )
