#!/usr/bin/env python3
"""Phase-0 spike driver: record raw opencode ``GET /event`` SSE traces.

Stands up nothing itself — it drives an already-running ``opencode serve``
(spike rig: local opencode 1.18.30 + OmO 4.19.4, workspace
``/tmp/oc-spike-workspace``, config rendered by
``OpencodeAgent/config/render_config.py`` into a scratch ``XDG_CONFIG_HOME``).

Scenarios (work plan T1, ``.omo/plans/opencode-engine-bridge-v2.md``):
  a  multi-tool turn      one vibe-trading MCP tool + one bash call
  b  mid-turn abort       abort via POST /session/:id/abort during a tool run
  c  OmO continuation    3-item todo list, stop-after-1 → stop-hook re-prompts
  d  subagent spawn      task tool → explore subagent (parentID chain)
  e  permission           bash=ask phase: permission event → POST response
  g  intra-tool silence   bash ``sleep 120`` (sizes tool_heartbeat synthesis)
  h  DELETE session       DELETE /session/:id availability (no LLM turn)

Measurements (f)/(f2)/(g)/(i) + assertions are derived by ``analyze_traces``.

Stdlib only (urllib + hand-rolled SSE frame parsing — httpx-sse is not a
project dependency). Traces are JSONL, one line per SSE frame::

    {"ts": <epoch_s>, "mono": <monotonic_s>, "type": <event type>, "data": <raw data payload, verbatim>}

``_recorder.*`` types are driver-injected boundary markers, not wire events.
Secret-shaped strings are redacted at write time.

Usage::

    python3 record_traces.py --base-url http://127.0.0.1:14096 \
        --out traces --scenarios a,b,c,d,g,h
    python3 record_traces.py --base-url http://127.0.0.1:14096 \
        --out traces --scenarios e          # phase B (serve restarted, bash=ask)
    python3 record_traces.py --out traces --analyze-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from analyze_traces import SCENARIO_FILES, ActivityScanner, analyze, sid_of

#: opencode /event sends ``data: {json}`` frames only — the event type lives
#: inside the JSON payload (``type`` field); there are no ``event:`` lines.
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1[REDACTED]"),
    (
        re.compile(r'(?i)("(?:api[_-]?key|password|secret|token)"\s*:\s*")[^"]+(")'),
        r"\1[REDACTED]\2",
    ),
]


def sanitize(raw: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        raw = pattern.sub(repl, raw)
    return raw


class OpencodeClient:
    """Legacy ``/session`` REST surface only (D10 — no ``/api/*``)."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = r.read().decode()
                return r.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode(errors="replace")
            return exc.code, payload[:500]

    def create_session(self, title: str) -> str:
        status, body = self._req("POST", "/session", {"title": title})
        assert status == 200 and isinstance(body, dict), f"create_session {status}: {body}"
        return body["id"]

    def prompt_async(self, sid: str, text: str) -> int:
        status, _ = self._req(
            "POST",
            f"/session/{sid}/prompt_async",
            {"parts": [{"type": "text", "text": text}]},
        )
        return status

    def abort(self, sid: str):
        return self._req("POST", f"/session/{sid}/abort", {})

    def respond_permission(self, sid: str, permission_id: str, response: str = "once"):
        return self._req(
            "POST",
            f"/session/{sid}/permissions/{permission_id}",
            {"response": response},
        )

    def delete_session(self, sid: str):
        return self._req("DELETE", f"/session/{sid}")

    def children(self, sid: str):
        return self._req("GET", f"/session/{sid}/children")


class SseRecorder:
    """Single persistent /event connection; routes frames to the active file.

    Kimaki global-listener pattern: one connection for the whole run, frames
    fan out by active scenario (scenarios run sequentially). Reconnects with
    0.5 s backoff and injects a ``_recorder.reconnect`` marker on gaps.
    """

    def __init__(self, base_url: str, out_dir: Path):
        self.base = base_url.rstrip("/")
        self.out_dir = out_dir
        self.frames: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._fh = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            with self._lock:
                if any(f["type"] == "server.connected" for f in self.frames):
                    return
            time.sleep(0.2)
        raise RuntimeError("SSE /event did not emit server.connected within 15 s")

    def stop(self) -> None:
        self._stop.set()
        self.close_scenario()

    def begin_scenario(self, key: str, sid: str) -> None:
        path = self.out_dir / SCENARIO_FILES[key]
        with self._lock:
            self._close_fh_locked()
            self.frames = []
            self._fh = path.open("w", encoding="utf-8")
            self._write_locked(self._marker("_recorder.scenario_start", {"scenario": key, "sessionID": sid}))

    def close_scenario(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._write_locked(self._marker("_recorder.scenario_end", {}))
            self._close_fh_locked()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.frames)

    @staticmethod
    def _marker(mtype: str, payload: dict) -> dict:
        return {
            "ts": time.time(),
            "mono": time.monotonic(),
            "type": mtype,
            "data": json.dumps(payload),
        }

    def _close_fh_locked(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def _write_locked(self, frame: dict) -> None:
        self.frames.append(frame)
        if self._fh is not None:
            self._fh.write(json.dumps(frame, ensure_ascii=False) + "\n")
            self._fh.flush()

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                self._read_stream()
                backoff = 0.5
            except Exception as exc:
                if self._stop.is_set():
                    return
                with self._lock:
                    self._write_locked(self._marker("_recorder.reconnect", {"error": sanitize(str(exc))[:200]}))
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _read_stream(self) -> None:
        req = urllib.request.Request(self.base + "/event", headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            data_lines: list[str] = []
            for raw in resp:
                if self._stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line == "" and data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        etype = json.loads(payload).get("type", "_unknown")
                    except json.JSONDecodeError:
                        etype = "_undecodable"
                    with self._lock:
                        self._write_locked(
                            {
                                "ts": time.time(),
                                "mono": time.monotonic(),
                                "type": etype,
                                "data": sanitize(payload),
                            }
                        )


def wait_turn_end(rec: SseRecorder, sid: str, *, quiescence: float, hard_timeout: float, on_frame=None) -> dict:
    """Wait for ``session.idle`` on *sid* + a quiescence window with no new work.

    Quiescence resets only on ActivityScanner-confirmed work (post-idle
    bookkeeping re-emissions must not reset it — see ActivityScanner).
    ``on_frame(frame, parsed_props)`` runs for every new sid frame (used by
    the abort trigger).
    """
    stats = {"idle_ts": [], "gaps_after_idle": [], "continuations": 0, "timed_out": False}
    start = time.monotonic()
    seen = 0
    idle_mono = None
    scanner = ActivityScanner()
    while time.monotonic() - start < hard_timeout:
        frames = rec.snapshot()
        while seen < len(frames):
            frame = frames[seen]
            seen += 1
            if sid_of(frame) != sid:
                continue
            activity = scanner.is_activity(frame)
            if idle_mono is not None and activity:
                stats["gaps_after_idle"].append(round(frame["mono"] - idle_mono, 3))
                stats["continuations"] += 1
                idle_mono = None
            if frame["type"] == "session.idle":
                idle_mono = frame["mono"]
                stats["idle_ts"].append(frame["ts"])
            if on_frame is not None:
                try:
                    props = json.loads(frame["data"]).get("properties", {})
                except json.JSONDecodeError:
                    props = {}
                on_frame(frame, props)
        if idle_mono is not None and time.monotonic() - idle_mono >= quiescence:
            return stats
        time.sleep(0.25)
    stats["timed_out"] = True
    return stats


def _find_permission_event(rec: SseRecorder, sid: str, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for frame in rec.snapshot():
            if "permission" not in frame["type"]:
                continue
            try:
                props = json.loads(frame["data"]).get("properties", {})
            except json.JSONDecodeError:
                continue
            if props.get("sessionID") not in (None, sid):
                continue
            pid = props.get("id") or props.get("permissionID") or (props.get("info") or {}).get("id")
            if pid:
                return {"frame": frame, "id": pid, "props": props}
        time.sleep(0.3)
    return None


PROMPTS = {
    "a": (
        "Call the vibe-trading_list_skills MCP tool once. Then run this exact "
        "bash command: echo hello-spike-a. Then reply with exactly: DONE_A"
    ),
    "b": (
        "Run this exact bash command: sleep 30. After it completes, write a "
        "very long story about the ocean, at least 2000 words."
    ),
    "c": (
        "Use your todo tool to create exactly 3 todos: (1) create file "
        "tmp/c{n}_1.txt containing C1, (2) create file tmp/c{n}_2.txt "
        "containing C2, (3) create file tmp/c{n}_3.txt containing C3. Then "
        "complete ONLY todo 1 and STOP — do not start todos 2 or 3 in this "
        "turn. Reply with exactly: DONE_C1"
    ),
    "d": (
        "Use the task tool to spawn an explore subagent with this prompt: "
        "'Find where SessionService is constructed in this workspace; "
        "report the file path and the function name.' When the subagent "
        "returns, reply with exactly: DONE_D"
    ),
    "e": "Run this exact bash command: echo permission-spike-ok. Then reply with exactly: DONE_E",
    "g": (
        "Run this exact bash command: sleep 120. Do not run anything else "
        "while it runs. After it completes, reply with exactly: DONE_G"
    ),
}


def scenario_a(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-a-multi-tool")
    rec.begin_scenario("a", sid)
    assert client.prompt_async(sid, PROMPTS["a"]) == 204
    stats = wait_turn_end(rec, sid, quiescence=15, hard_timeout=300)
    rec.close_scenario()
    return {"sessionID": sid, **stats}


def scenario_b(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-b-abort")
    rec.begin_scenario("b", sid)
    assert client.prompt_async(sid, PROMPTS["b"]) == 204
    triggered = {}

    def abort_once(frame, props):
        if "abort_done" in triggered:
            return
        part = props.get("part", {})
        if (
            frame["type"] == "message.part.updated"
            and part.get("type") == "tool"
            and (part.get("state") or {}).get("status") == "running"
        ):
            time.sleep(4.0)
            triggered["abort_status"], triggered["abort_body"] = client.abort(sid)
            triggered["abort_done"] = True

    stats = wait_turn_end(rec, sid, quiescence=12, hard_timeout=180, on_frame=abort_once)
    rec.close_scenario()
    return {"sessionID": sid, **stats, **triggered}


def scenario_c(client: OpencodeClient, rec: SseRecorder, run: int) -> dict:
    sid = client.create_session(f"spike-c-continuation-run{run}")
    rec.begin_scenario(f"c{run}", sid)
    assert client.prompt_async(sid, PROMPTS["c"].replace("{n}", str(run))) == 204
    stats = wait_turn_end(rec, sid, quiescence=25, hard_timeout=480)
    rec.close_scenario()
    return {"sessionID": sid, "run": run, **stats}


def scenario_d(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-d-subagent")
    rec.begin_scenario("d", sid)
    assert client.prompt_async(sid, PROMPTS["d"]) == 204
    stats = wait_turn_end(rec, sid, quiescence=20, hard_timeout=480)
    rec.close_scenario()
    status, kids = client.children(sid)
    return {"sessionID": sid, **stats, "children_status": status, "children": kids}


def scenario_e(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-e-permission")
    rec.begin_scenario("e", sid)
    assert client.prompt_async(sid, PROMPTS["e"]) == 204
    perm = _find_permission_event(rec, sid, timeout=120)
    result: dict = {"sessionID": sid, "permission_found": bool(perm)}
    if perm:
        result["permission_event_type"] = perm["frame"]["type"]
        result["permission_id"] = perm["id"]
        result["permission_props_keys"] = sorted(perm["props"].keys())
        time.sleep(3.0)
        result["respond_status"], body = client.respond_permission(sid, perm["id"])
        result["respond_body"] = body if not isinstance(body, dict) else "ok"
    stats = wait_turn_end(rec, sid, quiescence=15, hard_timeout=240)
    rec.close_scenario()
    return {**result, **stats}


def scenario_g(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-g-silent-tool")
    rec.begin_scenario("g", sid)
    assert client.prompt_async(sid, PROMPTS["g"]) == 204
    stats = wait_turn_end(rec, sid, quiescence=15, hard_timeout=300)
    rec.close_scenario()
    return {"sessionID": sid, **stats}


def scenario_h(client: OpencodeClient, rec: SseRecorder) -> dict:
    sid = client.create_session("spike-h-delete-me")
    rec.begin_scenario("h", sid)
    time.sleep(1.0)
    status, body = client.delete_session(sid)
    time.sleep(2.0)
    rec.close_scenario()
    deleted_event = any(f["type"] == "session.deleted" for f in rec.snapshot())
    return {
        "sessionID": sid,
        "delete_status": status,
        "delete_body": body if not isinstance(body, dict) else "ok",
        "session_deleted_event": deleted_event,
    }


RUNNERS = {
    "a": scenario_a,
    "b": scenario_b,
    "d": scenario_d,
    "e": scenario_e,
    "g": scenario_g,
    "h": scenario_h,
    "c": lambda c, r: [scenario_c(c, r, 1), scenario_c(c, r, 2)],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:14096")
    ap.add_argument("--out", required=True, help="traces output directory")
    ap.add_argument("--scenarios", default="", help="comma list: a,b,c,d,e,g,h")
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    prior = {}
    mfile = out_dir / "measurements.json"
    if mfile.exists():
        prior = json.loads(mfile.read_text()).get("scenarios", {})

    if args.analyze_only:
        m = analyze(out_dir, prior)
        print(json.dumps({k: m[k] for k in m if k != "scenarios"}, ensure_ascii=False, indent=1, default=str)[:3000])
        return 0

    keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
    client = OpencodeClient(args.base_url)
    rec = SseRecorder(args.base_url, out_dir)
    rec.start()
    results = dict(prior)
    try:
        for key in keys:
            runner = RUNNERS.get(key)
            if runner is None:
                print(f"unknown scenario {key!r}", file=sys.stderr)
                return 2
            print(f"[{time.strftime('%H:%M:%S')}] scenario {key} starting …", file=sys.stderr)
            out = runner(client, rec)
            if isinstance(out, list):
                for i, o in enumerate(out, 1):
                    results[f"{key}{i}"] = o
            else:
                results[key] = out
            print(
                f"[{time.strftime('%H:%M:%S')}] scenario {key} done: {json.dumps(out, default=str)[:300]}",
                file=sys.stderr,
            )
    finally:
        rec.stop()
    m = analyze(out_dir, results)
    print(
        json.dumps(
            {"f": m["f_idle_to_continuation"], "i": m["i_message_part_delta"]},
            ensure_ascii=False,
            indent=1,
            default=str,
        )[:2000]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
