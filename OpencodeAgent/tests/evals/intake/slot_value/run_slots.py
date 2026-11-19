#!/usr/bin/env python3
"""Drive the 24-cell slot matrix with bounded observation on two lanes.

Each cell is one value of one slot with the other five slots pinned; each cell
runs three times. A run is stopped as soon as its first three distinct
non-empty tool calls have been observed, or at the 300s timeout. The first
three calls are the signature: parameter compilation happens before the long
execution loop.

The CLI is a ``subprocess.Popen`` whose stdout is read line by line in a
reader thread; every parsed event is kept in the run record. On an early stop
or timeout the CLI process group is killed and the server-side session is
aborted through ``POST /session/{id}/abort``, so the observation window is
bounded. Stdlib only, by design.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import queue
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
import judge as judge_mod  # noqa: E402  (path inserted immediately above)

MATRIX_PATH = BASE_DIR / "matrix.json"
SPEC_PATH = BASE_DIR / "judge_spec.json"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
RESULTS_PATH = BASE_DIR / "results.json"
PILOTS_PATH = BASE_DIR / "pilots.json"
INTAKE_RUN_PATH = BASE_DIR.parent / "run.py"
REPEATS = 3
MAX_CALLS = 3
MAX_EXCERPT = 200
MAX_ERROR = 500


def _load_intake() -> Any:
    spec = importlib.util.spec_from_file_location("intake_run", INTAKE_RUN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


intake = _load_intake()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def excerpt(text: str) -> str:
    return " ".join(text.split())[:MAX_EXCERPT]


def stderr_tail(stderr: Any) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return (stderr or "")[-MAX_ERROR:]


def load_lanes(args: argparse.Namespace, matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spec = json.loads(Path(args.lanes_spec).read_text(encoding="utf-8"))
    raw = spec["observation"]["lanes"]
    lanes: dict[str, dict[str, Any]] = {}
    for name, config in raw.items():
        lanes[name] = {
            "name": name,
            "container": name,
            "port": config["port"],
            "recover_base": f"http://127.0.0.1:{config['port']}",
            "cells": list(config["cells"]),
        }
    assigned = [cell for lane in lanes.values() for cell in lane["cells"]]
    expected = [cell["id"] for cell in matrix["cells"]]
    if sorted(assigned) != sorted(expected):
        missing = sorted(set(expected) - set(assigned))
        extra = sorted(set(assigned) - set(expected))
        raise SystemExit(f"lane assignment mismatch: missing={missing} extra={extra}")
    if len(lanes) != 2 or any(len(lane["cells"]) != 12 for lane in lanes.values()):
        raise SystemExit("expected exactly 2 lanes with 12 cells each")
    return lanes


def build_command(lane: dict[str, Any], cell: dict[str, Any], repeat: int, args: argparse.Namespace) -> list[str]:
    title = f"{cell['id']}-r{repeat}"
    command = ["docker", "exec", "-w", args.dir, lane["container"], "opencode"]
    return command + [
        "run",
        "--attach",
        args.attach,
        "--dir",
        args.dir,
        "--format",
        "json",
        "--title",
        title,
        cell["prompt"],
    ]


def clean_workspace(lane: dict[str, Any], args: argparse.Namespace) -> None:
    if not args.clean:
        return
    command = [
        "docker",
        "exec",
        lane["container"],
        "bash",
        "-lc",
        "rm -rf /workspace/tmp/* /workspace/analysis/* /workspace/.vt-memory/* 2>/dev/null || true",
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        pass


def api_request(base: str, method: str, path: str, password: str, timeout: float = 20.0) -> Any:
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=b"" if method == "POST" else None,
        headers={"Authorization": f"Basic {token}"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None


def abort_session(lane: dict[str, Any], session_id: str, password: str) -> bool:
    try:
        api_request(lane["recover_base"], "POST", f"/session/{session_id}/abort", password)
        return True
    except Exception:
        return False


def abort_by_title(lane: dict[str, Any], title: str, password: str) -> bool:
    try:
        sessions = api_request(lane["recover_base"], "GET", "/session?directory=/workspace", password)
    except Exception:
        return False
    for session in sessions if isinstance(sessions, list) else []:
        if session.get("title") == title and session.get("id"):
            return abort_session(lane, session["id"], password)
    return False


def kill_group(proc: subprocess.Popen[str]) -> bool:
    if proc.poll() is not None:
        return False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    return True


def _read_stdout(stream: Any, out: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            out.put(line)
    finally:
        out.put(None)


def _read_stderr(stream: Any, sink: deque[str]) -> None:
    try:
        for line in stream:
            sink.append(line)
    except Exception:
        pass


def parse_line(line: str, calls: list[dict[str, Any]], texts: list[str], state: dict[str, Any]) -> None:
    try:
        objects = list(intake._walk(json.loads(line.strip())))
    except json.JSONDecodeError:
        return
    for obj in objects:
        name = intake._tool_name(obj)
        if name:
            tool_args = intake._tool_args(obj)
            if isinstance(tool_args, dict) and tool_args and len(calls) < MAX_CALLS:
                call = {"name": name, "args": judge_mod.canonical_args(tool_args)}
                if call not in calls:
                    calls.append(call)
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            texts.append(obj["text"])
        elif obj.get("role") == "assistant" and isinstance(obj.get("content"), str):
            texts.append(obj["content"])
        state["tokens"] = intake._merge_usage(state.get("tokens"), obj.get("tokens") or obj.get("usage"))
        if state.get("session_id") is None:
            state["session_id"] = intake.stream_session_id(line)


def run_repeat(
    lane: dict[str, Any], cell: dict[str, Any], repeat: int, args: argparse.Namespace, password: str
) -> dict[str, Any]:
    clean_workspace(lane, args)
    started_at, started = utcnow(), time.monotonic()
    command = build_command(lane, cell, repeat, args)
    title = f"{cell['id']}-r{repeat}"
    record: dict[str, Any] = {
        "cell": cell["id"],
        "slot_key": cell["slot_key"],
        "slot": cell["slot"],
        "value": cell["value"],
        "repeat": repeat,
        "lane": lane["name"],
        "started_at": started_at,
        "transcript": f"transcripts/{cell['id']}/{repeat}.json",
        "recovered": False,
    }
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        record.update(
            status="run_error",
            signature=None,
            signature_hash=None,
            k_calls=0,
            tokens=None,
            wall_s=round(time.monotonic() - started, 3),
            killed_by_early_stop=False,
            error=f"exec failed: {exc}"[:MAX_ERROR],
            finished_at=utcnow(),
        )
        return record
    assert proc.stdout is not None and proc.stderr is not None
    out: queue.Queue[str | None] = queue.Queue()
    err: deque[str] = deque(maxlen=80)
    stdout_thread = threading.Thread(target=_read_stdout, args=(proc.stdout, out), daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, args=(proc.stderr, err), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    calls: list[dict[str, Any]] = []
    texts: list[str] = []
    state: dict[str, Any] = {"tokens": None, "session_id": None}
    killed_early = timed_out = False
    deadline = started + args.timeout
    while True:
        if len(calls) >= MAX_CALLS:
            killed_early = kill_group(proc)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            kill_group(proc)
            break
        try:
            line = out.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if line is None:
            break
        parse_line(line, calls, texts, state)
    while True:
        try:
            line = out.get_nowait()
        except queue.Empty:
            break
        if line is None:
            break
        parse_line(line, calls, texts, state)
    if proc.poll() is None:
        kill_group(proc)
    wall_s = round(time.monotonic() - started, 3)
    stderr_thread.join(timeout=1)
    stdout_thread.join(timeout=1)
    stderr_text = "".join(err)
    session_id = state.get("session_id")
    if killed_early or timed_out or proc.returncode not in (0, None):
        aborted = abort_session(lane, session_id, password) if session_id else abort_by_title(lane, title, password)
    else:
        aborted = False
    calls = calls[:MAX_CALLS]
    signature = judge_mod.signature_of(calls)
    k_calls = len(signature)
    if k_calls >= 1 and (killed_early or timed_out):
        status = "ok_partial"
    elif k_calls >= 1 and proc.returncode == 0:
        status = "ok"
    elif k_calls >= 1:
        status = "ok_partial"
    else:
        status = "run_error"
    record.update(
        status=status,
        signature=signature,
        signature_hash=judge_mod.signature_hash(signature) if signature else None,
        k_calls=k_calls,
        calls=calls,
        tokens=state.get("tokens"),
        wall_s=wall_s,
        killed_by_early_stop=killed_early,
        finished_at=utcnow(),
    )
    if texts:
        record["text_excerpt"] = excerpt("\n".join(texts))
    if status == "run_error":
        reason = f"timeout after {args.timeout}s" if timed_out else f"exit={proc.returncode}"
        record["error"] = (
            f"{reason}; session={session_id or 'unknown'}; aborted={aborted}; stderr_tail={stderr_tail(stderr_text)}"
        )[-MAX_ERROR:]
    elif timed_out:
        record["error"] = f"timeout after {args.timeout}s with {k_calls} calls; aborted={aborted}"[:MAX_ERROR]
    return record


def repeat_record(record: dict[str, Any]) -> dict[str, Any]:
    item = {
        key: record.get(key)
        for key in (
            "repeat",
            "status",
            "signature",
            "signature_hash",
            "k_calls",
            "wall_s",
            "tokens",
            "recovered",
            "killed_by_early_stop",
            "lane",
            "transcript",
        )
    }
    item.update(
        {key: record[key] for key in ("reason", "error", "started_at", "finished_at") if record.get(key) is not None}
    )
    return item


def stats(records: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    values = [record for record in records.values() if record]
    usable = [record for record in values if record["status"] in judge_mod.USABLE_STATUSES]
    walls = [record["wall_s"] for record in usable if isinstance(record.get("wall_s"), (int, float))]
    return {
        "total": len(values),
        "ok": sum(record["status"] == "ok" for record in values),
        "ok_partial": sum(record["status"] == "ok_partial" for record in values),
        "run_error": sum(record["status"] == "run_error" for record in values),
        "incomplete": sum(record["status"] == "incomplete" for record in values),
        "early_stop_count": sum(bool(record.get("killed_by_early_stop")) for record in usable),
        "timeout_count": sum("timeout after" in (record.get("error") or "") for record in values),
        "recovered": sum(bool(record.get("recovered")) for record in values),
        "mean_wall_s": round(statistics.fmean(walls), 3) if walls else None,
        "median_wall_s": round(statistics.median(walls), 3) if walls else None,
    }


def write_results(
    args: argparse.Namespace, meta: dict[str, Any], records: dict[tuple[str, int], dict[str, Any]]
) -> None:
    cells: dict[str, Any] = {}
    for cell in args.matrix["cells"]:
        repeats = [
            repeat_record(records[(cell["id"], repeat)]) for repeat in range(REPEATS) if (cell["id"], repeat) in records
        ]
        cells[cell["id"]] = {
            "slot_key": cell["slot_key"],
            "slot": cell["slot"],
            "value": cell["value"],
            "repeats": repeats,
        }
    summary = stats(records)
    payload = {
        "meta": {
            **meta,
            "runs_total": summary["total"],
            "runs_ok": summary["ok"],
            "runs_ok_partial": summary["ok_partial"],
            "runs_run_error": summary["run_error"],
            "runs_incomplete": summary["incomplete"],
            "early_stop_count": summary["early_stop_count"],
            "timeout_count": summary["timeout_count"],
            "recovery_count": summary["recovered"],
            "mean_wall_s": summary["mean_wall_s"],
            "median_wall_s": summary["median_wall_s"],
            "cleanup": meta.get("cleanup"),
        },
        "cells": cells,
    }
    atomic_write(RESULTS_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_existing(matrix: dict[str, Any], records: dict[tuple[str, int], dict[str, Any]]) -> None:
    for cell in matrix["cells"]:
        for repeat in range(REPEATS):
            path = TRANSCRIPTS_DIR / cell["id"] / f"{repeat}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status") in judge_mod.USABLE_STATUSES + ("run_error",):
                    records[(cell["id"], repeat)] = record


def wait_ready(lanes: dict[str, dict[str, Any]], args: argparse.Namespace, password: str) -> bool:
    deadline = time.monotonic() + args.wait_ready
    pending = set(lanes)
    while pending:
        for name in list(pending):
            base = f"http://127.0.0.1:{lanes[name]['port']}"
            try:
                if api_request(base, "GET", "/config?directory=/workspace", password, timeout=10):
                    pending.discard(name)
            except Exception:
                pass
        if not pending:
            return True
        if time.monotonic() >= deadline:
            print(f"not ready: {sorted(pending)}", file=sys.stderr)
            return False
        time.sleep(5)
    return True


def run_pilot(args: argparse.Namespace, lanes: dict[str, dict[str, Any]], matrix: dict[str, Any], password: str) -> int:
    cell = next(cell for cell in matrix["cells"] if cell["id"] == args.pilot_cell)
    lane = lanes[args.pilot_lane]
    record = run_repeat(lane, cell, args.pilot_repeat, args, password)
    path = TRANSCRIPTS_DIR / "_pilots" / lane["name"] / f"{cell['id']}-r{args.pilot_repeat}.json"
    record["transcript"] = str(path.relative_to(BASE_DIR))
    atomic_write(path, json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    pilots = json.loads(PILOTS_PATH.read_text(encoding="utf-8")) if PILOTS_PATH.exists() else []
    pilots = [
        item
        for item in pilots
        if not (
            item.get("cell") == cell["id"]
            and item.get("repeat") == args.pilot_repeat
            and item.get("lane") == lane["name"]
        )
    ]
    pilots.append(record)
    atomic_write(PILOTS_PATH, json.dumps(pilots, ensure_ascii=False, indent=2) + "\n")
    print(
        f"[pilot] {cell['id']} on {lane['name']}: status={record['status']} k={record['k_calls']} "
        f"wall={record.get('wall_s')} early={record.get('killed_by_early_stop')} sig={record.get('signature_hash')}",
        file=sys.stderr,
    )
    return 0 if record["k_calls"] >= 1 and (record.get("wall_s") or 999) < 120 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lanes-spec", default=str(SPEC_PATH), help="judge_spec.json with observation.lanes")
    parser.add_argument("--attach", default="http://localhost:4096", help="server URL inside each container")
    parser.add_argument("--dir", default="/workspace", help="working directory inside the container")
    parser.add_argument("--username", default=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"))
    parser.add_argument("--password", default=os.environ.get("OPENCODE_SERVER_PASSWORD", ""))
    parser.add_argument("--image", default="opencode-serve:v3.10.0-mymain")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--budget-s", type=float, default=9000.0)
    parser.add_argument("--workers-per-lane", type=int, default=1)
    parser.add_argument("--wait-ready", type=float, default=0.0)
    parser.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--only", action="append", metavar="CELL", help="restrict the batch to a cell id")
    parser.add_argument("--pilot", action="store_true", help="run one pilot repeat and exit")
    parser.add_argument("--pilot-lane", help="lane name for --pilot")
    parser.add_argument("--pilot-cell", help="cell id for --pilot")
    parser.add_argument("--pilot-repeat", type=int, default=0)
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    args.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    matrix = args.matrix
    lanes = load_lanes(args, matrix)
    if args.only and (unknown := sorted(set(args.only) - {cell["id"] for cell in matrix["cells"]})):
        parser.error(f"unknown --only cells: {unknown}")
    if args.workers_per_lane < 1 or args.timeout <= 0 or args.budget_s <= 0:
        parser.error("--workers-per-lane, --timeout and --budget-s must be positive")
    if args.wait_ready:
        if not wait_ready(lanes, args, args.password):
            return 2
    if args.pilot:
        if not args.pilot_lane or not args.pilot_cell:
            parser.error("--pilot requires --pilot-lane and --pilot-cell")
        return run_pilot(args, lanes, matrix, args.password)

    meta: dict[str, Any] = {
        "schema_version": 2,
        "experiment": matrix["experiment"],
        "judge_spec_sha256": judge_mod.file_sha256(SPEC_PATH),
        "matrix_sha256": judge_mod.file_sha256(MATRIX_PATH),
        "repeats_per_cell": REPEATS,
        "image": args.image,
        "containers": list(lanes),
        "lanes": {name: {"port": lane["port"], "cells": lane["cells"]} for name, lane in lanes.items()},
        "workers_per_lane": args.workers_per_lane,
        "per_run_timeout_s": args.timeout,
        "batch_wall_budget_s": args.budget_s,
        "started_at": utcnow(),
        "finished_at": None,
        "cleanup": None,
    }
    if RESULTS_PATH.exists():
        old = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        old_meta = old.get("meta") or {}
        for key in ("judge_spec_sha256", "matrix_sha256", "repeats_per_cell", "started_at", "cleanup"):
            if key in old_meta:
                meta[key] = old_meta[key]
        if old_meta.get("judge_spec_sha256") != meta["judge_spec_sha256"]:
            print("judge_spec.json changed since results.json was first written; the run is void", file=sys.stderr)
            return 2
    deadline = datetime.fromisoformat(meta["started_at"]).timestamp() + args.budget_s
    records: dict[tuple[str, int], dict[str, Any]] = {}
    load_existing(matrix, records)
    if args.summary:
        print(json.dumps(stats(records), indent=2), file=sys.stderr)
        return 0

    tasks: list[tuple[str, dict[str, Any], int]] = []
    for name, lane in lanes.items():
        for repeat in range(REPEATS):
            for cell_id in lane["cells"]:
                if args.only and cell_id not in args.only:
                    continue
                existing = records.get((cell_id, repeat))
                if existing and existing["status"] in judge_mod.USABLE_STATUSES + ("run_error",):
                    continue
                cell = next(cell for cell in matrix["cells"] if cell["id"] == cell_id)
                tasks.append((name, cell, repeat))
    print(
        f"[plan] {len(tasks)} repeats to run, {len(records)} already recorded, "
        f"deadline={datetime.fromtimestamp(deadline, timezone.utc).isoformat(timespec='seconds')}",
        file=sys.stderr,
    )
    if not tasks and not args.finalize:
        print("[plan] nothing to do; use --finalize to close the batch", file=sys.stderr)
        return 0

    lock = threading.Lock()

    def worker(lane_name: str, cell: dict[str, Any], repeat: int) -> None:
        lane = lanes[lane_name]
        if time.time() >= deadline:
            record = {
                "cell": cell["id"],
                "slot_key": cell["slot_key"],
                "slot": cell["slot"],
                "value": cell["value"],
                "repeat": repeat,
                "lane": lane_name,
                "status": "incomplete",
                "signature": None,
                "signature_hash": None,
                "k_calls": 0,
                "wall_s": None,
                "tokens": None,
                "recovered": False,
                "killed_by_early_stop": False,
                "reason": "incomplete(budget)",
                "started_at": utcnow(),
                "finished_at": utcnow(),
                "transcript": f"transcripts/{cell['id']}/{repeat}.json",
            }
        else:
            record = run_repeat(lane, cell, repeat, args, args.password)
        with lock:
            records[(cell["id"], repeat)] = record
            atomic_write(
                TRANSCRIPTS_DIR / cell["id"] / f"{repeat}.json",
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            )
            write_results(args, meta, records)
        print(
            f"[{cell['id']} r{repeat} {lane_name}] {record['status']} "
            f"k={record.get('k_calls')} wall={record.get('wall_s')} "
            f"early={record.get('killed_by_early_stop')} sig={record.get('signature_hash')}",
            file=sys.stderr,
        )

    with (
        ThreadPoolExecutor(max_workers=args.workers_per_lane) as pool_a,
        ThreadPoolExecutor(max_workers=args.workers_per_lane) as pool_b,
    ):
        pools = {name: pool_a if index == 0 else pool_b for index, name in enumerate(lanes)}
        futures = [pools[name].submit(worker, name, cell, repeat) for name, cell, repeat in tasks]
        for future in futures:
            future.result()

    missing = [
        (cell, repeat) for cell in matrix["cells"] for repeat in range(REPEATS) if (cell["id"], repeat) not in records
    ]
    if missing:
        now = time.time()
        for cell, repeat in missing:
            reason = "incomplete(budget)" if now >= deadline else "incomplete(not_run)"
            record = {
                "cell": cell["id"],
                "slot_key": cell["slot_key"],
                "slot": cell["slot"],
                "value": cell["value"],
                "repeat": repeat,
                "lane": "unassigned",
                "status": "incomplete",
                "signature": None,
                "signature_hash": None,
                "k_calls": 0,
                "wall_s": None,
                "tokens": None,
                "recovered": False,
                "killed_by_early_stop": False,
                "reason": reason,
                "started_at": utcnow(),
                "finished_at": utcnow(),
                "transcript": f"transcripts/{cell['id']}/{repeat}.json",
            }
            records[(cell["id"], repeat)] = record
            atomic_write(
                TRANSCRIPTS_DIR / cell["id"] / f"{repeat}.json",
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            )
    meta["finished_at"] = utcnow()
    write_results(args, meta, records)
    summary = stats(records)
    print(f"[done] {summary}", file=sys.stderr)
    not_run = sum(1 for record in records.values() if record.get("reason") == "incomplete(not_run)")
    return 1 if not_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
