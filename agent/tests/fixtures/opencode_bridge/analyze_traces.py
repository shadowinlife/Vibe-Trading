#!/usr/bin/env python3
"""Trace analysis for the Phase-0 opencode-bridge spike corpus.

Consumes the JSONL traces written by ``record_traces.py`` and derives the T1
measurements — (f) idle→continuation gaps, (f2) natural-completion→next-event
gaps, (g) intra-tool silence windows, (i) ``message.part.delta`` shape — plus
the per-scenario assertions. Writes ``measurements.json`` into the trace dir.

Shared with the driver: ``SCENARIO_FILES`` (corpus schema), ``sid_of``,
``parsed``, ``ActivityScanner`` (the quiescence-filter semantics the spike
established — see its docstring; the Phase-1 translator reuses this logic).
"""

from __future__ import annotations

import json
from pathlib import Path

SCENARIO_FILES = {
    "a": "scenario_a_multi_tool.jsonl",
    "b": "scenario_b_abort.jsonl",
    "c1": "scenario_c_continuation_run1.jsonl",
    "c2": "scenario_c_continuation_run2.jsonl",
    "d": "scenario_d_subagent.jsonl",
    "e": "scenario_e_permission.jsonl",
    "g": "scenario_g_silent_tool.jsonl",
    "h": "measurement_h_delete_session.jsonl",
}


def sid_of(frame: dict) -> str | None:
    try:
        props = json.loads(frame["data"]).get("properties", {})
    except json.JSONDecodeError:
        return None
    return props.get("sessionID") or (props.get("info") or {}).get("sessionID")


def parsed(frame: dict) -> dict:
    try:
        return json.loads(frame["data"])
    except json.JSONDecodeError:
        return {}


class ActivityScanner:
    """Decide whether a frame proves the session resumed WORK.

    Empirical (trace a, opencode 1.18.30): within ~12 ms after session.idle
    the server re-emits bookkeeping — session.updated, session.diff, and a
    message.updated for the ORIGINAL user message. A quiescence timer that
    resets on any of these never expires. A real continuation (OmO stop-hook
    re-prompt) is a message/part event for a message id never seen before, a
    session.status busy, session.error, or a permission/question event.
    server.heartbeat (exactly 10 s cadence) is stream keep-alive, not work.
    """

    def __init__(self):
        self._seen_msgs: set[str] = set()

    def is_activity(self, frame: dict) -> bool:
        etype = frame["type"]
        try:
            props = json.loads(frame["data"]).get("properties", {})
        except json.JSONDecodeError:
            return False
        if etype == "message.updated":
            mid = (props.get("info") or {}).get("id")
            if mid is None:
                return False
            novel = mid not in self._seen_msgs
            self._seen_msgs.add(mid)
            return novel
        if etype in ("message.part.updated", "message.part.delta"):
            mid = props.get("messageID") or (props.get("part") or {}).get("messageID")
            if mid is None:
                return False
            novel = mid not in self._seen_msgs
            self._seen_msgs.add(mid)
            return novel
        if etype == "session.error":
            return True
        if "permission" in etype or "question" in etype:
            return True
        if etype == "session.status":
            return (props.get("status") or {}).get("type") == "busy"
        return False


def load_trace(path: Path) -> list[dict]:
    frames = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                frames.append(json.loads(line))
    return frames


def _scenario_sid(frames: list[dict]) -> str | None:
    for frame in frames:
        if frame["type"] == "_recorder.scenario_start":
            return parsed(frame).get("sessionID")
    return None


def _delta_shape(out_dir: Path) -> dict:
    delta_shapes: dict[str, int] = {}
    delta_fields: dict[str, int] = {}
    for path in sorted(out_dir.glob("*.jsonl")):
        for frame in load_trace(path):
            if frame["type"] != "message.part.delta":
                continue
            props = parsed(frame).get("properties", {})
            key = ",".join(sorted(props.keys()))
            delta_shapes[key] = delta_shapes.get(key, 0) + 1
            fld = str(props.get("field"))
            delta_fields[fld] = delta_fields.get(fld, 0) + 1
    return {
        "exists": bool(delta_shapes),
        "property_key_sets": delta_shapes,
        "field_values": delta_fields,
    }


def _idle_continuation_gaps(out_dir: Path) -> dict:
    f_samples: list[dict] = []
    for path in sorted(out_dir.glob("scenario_c_*.jsonl")):
        frames = load_trace(path)
        sid = _scenario_sid(frames)
        idle_mono = None
        scanner = ActivityScanner()
        for frame in frames:
            if frame["type"].startswith("_recorder") or sid_of(frame) != sid:
                continue
            activity = scanner.is_activity(frame)
            if frame["type"] == "session.idle":
                idle_mono = frame["mono"]
                continue
            if idle_mono is not None and activity:
                f_samples.append(
                    {"trace": path.name, "gap_s": round(frame["mono"] - idle_mono, 3), "next_event": frame["type"]}
                )
                idle_mono = None
    return _gap_stats(f_samples)


def _natural_completion_gaps(out_dir: Path) -> dict:
    # (f2) Kimaki warns OmO continuations may keep the session busy WITHOUT an
    # intervening session.idle — then the QUIESCENCE_S-relevant gap starts at
    # the last natural completion (time.completed && finish != "tool-calls").
    f2_samples: list[dict] = []
    for path in sorted(out_dir.glob("scenario_*.jsonl")):
        frames = load_trace(path)
        sid = _scenario_sid(frames)
        nc_mono = None
        scanner = ActivityScanner()
        for f in frames:
            if f["type"].startswith("_recorder") or sid_of(f) != sid:
                continue
            activity = scanner.is_activity(f)
            if f["type"] == "message.updated":
                info = parsed(f).get("properties", {}).get("info", {})
                if (
                    info.get("role") == "assistant"
                    and (info.get("time") or {}).get("completed")
                    and info.get("finish") not in (None, "tool-calls")
                ):
                    nc_mono = f["mono"]
                    continue
            if (
                nc_mono is not None
                and activity
                and f["type"]
                in (
                    "message.updated",
                    "message.part.updated",
                    "message.part.delta",
                )
            ):
                f2_samples.append({"trace": path.name, "gap_s": round(f["mono"] - nc_mono, 3), "next_event": f["type"]})
                nc_mono = None
    return _gap_stats(f2_samples)


def _gap_stats(samples: list[dict]) -> dict:
    gaps = sorted(s["gap_s"] for s in samples)
    return {
        "n": len(gaps),
        "samples": samples,
        "min_s": gaps[0] if gaps else None,
        "max_s": gaps[-1] if gaps else None,
        "p50_s": gaps[len(gaps) // 2] if gaps else None,
    }


def _tool_silence_windows(out_dir: Path, m: dict) -> list[dict]:
    g_windows: list[dict] = []
    for path in sorted(out_dir.glob("*.jsonl")):
        frames = [f for f in load_trace(path) if not f["type"].startswith("_recorder")]
        running: dict[str, float] = {}
        for frame in frames:
            if frame["type"] != "message.part.updated":
                continue
            part = parsed(frame).get("properties", {}).get("part", {})
            if part.get("type") != "tool":
                continue
            pid, status = part.get("id"), (part.get("state") or {}).get("status")
            tool = part.get("tool") or (part.get("state") or {}).get("title") or "?"
            if status == "running" and pid not in running:
                running[pid] = frame["mono"]
                g_windows.append(
                    {
                        "trace": path.name,
                        "partID": pid,
                        "tool": str(tool),
                        "sid": sid_of(frame),
                        "start_mono": frame["mono"],
                        "end_mono": None,
                    }
                )
            elif status in ("completed", "error") and pid in running:
                for w in g_windows:
                    if w["partID"] == pid and w["end_mono"] is None:
                        w["end_mono"] = frame["mono"]
        for w in g_windows:
            if w["end_mono"] is None or "events_inside" in w:
                continue
            inside_frames = [f for f in frames if sid_of(f) == w["sid"] and w["start_mono"] < f["mono"] < w["end_mono"]]
            inside = [f["mono"] for f in inside_frames]
            types_inside: dict[str, int] = {}
            for f in inside_frames:
                types_inside[f["type"]] = types_inside.get(f["type"], 0) + 1
            w["events_inside"] = len(inside)
            w["types_inside"] = types_inside
            marks = [w["start_mono"], *sorted(inside), w["end_mono"]]
            w["max_gap_s"] = round(max(b - a for a, b in zip(marks, marks[1:])), 3)
            w["duration_s"] = round(w["end_mono"] - w["start_mono"], 3)
        hb = [f["mono"] for f in frames if f["type"] == "server.heartbeat"]
        if len(hb) > 1 and path.name.startswith("scenario_g"):
            m.setdefault(
                "g_stream_keepalive",
                {
                    "trace": path.name,
                    "heartbeat_interval_s": round((hb[-1] - hb[0]) / (len(hb) - 1), 2),
                    "heartbeats": len(hb),
                },
            )
    return [{k: v for k, v in w.items() if k not in ("start_mono", "end_mono")} for w in g_windows]


def _error_idle_check(wire: list[dict]) -> list[bool]:
    out = []
    for i, f in enumerate(wire):
        if f["type"] == "session.error":
            followed = any(x["type"] == "session.idle" and sid_of(x) == sid_of(f) for x in wire[i + 1 : i + 6])
            out.append(followed)
    return out


def _task_part_evidence(wire: list[dict]) -> list[dict]:
    ev = []
    for f in wire:
        if f["type"] != "message.part.updated":
            continue
        part = parsed(f).get("properties", {}).get("part", {})
        if part.get("type") == "tool" and str(part.get("tool", "")).endswith("task"):
            state = part.get("state") or {}
            meta = state.get("metadata") or {}
            ev.append(
                {
                    "partID": part.get("id"),
                    "status": state.get("status"),
                    "metadata_sessionId": meta.get("sessionId"),
                    "metadata_keys": sorted(meta.keys()),
                }
            )
    return ev


def _injected_user_evidence(wire: list[dict]) -> list[dict]:
    ev = []
    for f in wire:
        if f["type"] != "message.updated":
            continue
        info = parsed(f).get("properties", {}).get("info", {})
        if info.get("role") == "user":
            ev.append(
                {
                    "messageID": info.get("id"),
                    "synthetic": info.get("synthetic"),
                    "time_created": (info.get("time") or {}).get("created"),
                }
            )
    return ev


def _scenario_assertions(out_dir: Path) -> dict:
    assertions: dict[str, dict] = {}
    for key, fname in SCENARIO_FILES.items():
        path = out_dir / fname
        if not path.exists():
            continue
        wire = [f for f in load_trace(path) if not f["type"].startswith("_recorder")]
        completions = []
        for f in wire:
            if f["type"] == "message.updated":
                info = parsed(f).get("properties", {}).get("info", {})
                if info.get("role") == "assistant" and (info.get("time") or {}).get("completed"):
                    completions.append(
                        {"finish": info.get("finish"), "error": info.get("error"), "messageID": info.get("id")}
                    )
        a: dict = {
            "frames": len(wire),
            "event_types": sorted({f["type"] for f in wire}),
            "natural_completions": completions,
            "idle_count": sum(1 for f in wire if f["type"] == "session.idle"),
            "session_error_followed_by_idle": _error_idle_check(wire),
            "delta_count": sum(1 for f in wire if f["type"] == "message.part.delta"),
            "status_transitions": [
                parsed(f).get("properties", {}).get("status", {}).get("type")
                for f in wire
                if f["type"] == "session.status"
            ],
        }
        if key == "d":
            a["task_parts"] = _task_part_evidence(wire)
        if key.startswith("c"):
            a["injected_user_messages"] = _injected_user_evidence(wire)
        assertions[key] = a
    return assertions


def analyze(out_dir: Path, results: dict) -> dict:
    m: dict = {"scenarios": results}
    m["i_message_part_delta"] = _delta_shape(out_dir)
    m["f_idle_to_continuation"] = _idle_continuation_gaps(out_dir)
    m["f2_natural_completion_to_next_event"] = _natural_completion_gaps(out_dir)
    m["g_intra_tool_silence"] = _tool_silence_windows(out_dir, m)
    m["assertions"] = _scenario_assertions(out_dir)
    (out_dir / "measurements.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return m
