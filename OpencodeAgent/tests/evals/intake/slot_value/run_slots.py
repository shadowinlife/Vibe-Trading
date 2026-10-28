#!/usr/bin/env python3
"""Drive the 24-cell slot decision-value matrix and record compact evidence.

Each cell is one value of one slot with the other five slots pinned; each cell
runs three times because the model is non-deterministic. A run's evidence is
its tool-call signature (tool name + canonical arguments), wall time, tokens
and at most a 200-character text excerpt. Raw model prose never enters the
repository.

The CLI streaming race is handled by the same parser and session-API recovery
used by ``../run.py``; this module imports them instead of carrying a second
implementation. Stdlib only, by design.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
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
INTAKE_RUN_PATH = BASE_DIR.parent / "run.py"
REPEATS = 3
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
    collapsed = " ".join(text.split())
    return collapsed[:MAX_EXCERPT]


def stderr_tail(stderr: Any) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return (stderr or "")[-MAX_ERROR:]


def title_for(cell: dict[str, Any], repeat: int) -> str:
    return f"{cell['id']}-r{repeat}"


def build_command(args: argparse.Namespace, cell: dict[str, Any], repeat: int) -> list[str]:
    command = ["docker", "exec", "-w", args.dir, args.container, "opencode"] if args.container else ["opencode"]
    return command + [
        "run",
        "--attach",
        args.attach,
        "--dir",
        args.dir,
        "--format",
        "json",
        "--title",
        title_for(cell, repeat),
        cell["prompt"],
    ]


def api_request(args: argparse.Namespace, method: str, path: str, password: str) -> Any:
    token = base64.b64encode(f"{args.username}:{password}".encode()).decode()
    request = urllib.request.Request(
        args.recover_base.rstrip("/") + path,
        data=b"" if method == "POST" else None,
        headers={"Authorization": f"Basic {token}"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"null")


def abort_session(args: argparse.Namespace, session_id: str, password: str) -> bool:
    try:
        api_request(args, "POST", f"/session/{session_id}/abort", password)
        return True
    except Exception:
        return False


def abort_by_title(args: argparse.Namespace, title: str, password: str) -> bool:
    try:
        sessions = api_request(args, "GET", "/session?directory=/workspace", password)
    except Exception:
        return False
    for session in sessions if isinstance(sessions, list) else []:
        if session.get("title") == title and session.get("id"):
            return abort_session(args, session["id"], password)
    return False


def clean_workspace(args: argparse.Namespace) -> None:
    if not args.clean or not args.container:
        return
    command = [
        "docker",
        "exec",
        args.container,
        "bash",
        "-lc",
        "rm -rf /workspace/tmp/* /workspace/analysis/* 2>/dev/null || true",
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        pass


def run_repeat(args: argparse.Namespace, cell: dict[str, Any], repeat: int, password: str) -> dict[str, Any]:
    started_at, started = utcnow(), time.monotonic()
    clean_workspace(args)
    command = build_command(args, cell, repeat)
    record: dict[str, Any] = {
        "cell": cell["id"],
        "slot_key": cell["slot_key"],
        "slot": cell["slot"],
        "value": cell["value"],
        "repeat": repeat,
        "started_at": started_at,
        "transcript": f"transcripts/{cell['id']}/{repeat}.json",
        "recovered": False,
    }
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        session_id = intake.stream_session_id(partial)
        aborted = (
            abort_session(args, session_id, password)
            if session_id
            else abort_by_title(args, title_for(cell, repeat), password)
        )
        record.update(
            status="run_error",
            signature=None,
            signature_hash=None,
            tokens=None,
            wall_s=round(time.monotonic() - started, 3),
            error=(
                f"timeout after {args.timeout}s; session={session_id or 'unknown'}; "
                f"aborted={aborted}; stderr_tail={stderr_tail(exc.stderr)}"
            )[-MAX_ERROR:],
            finished_at=utcnow(),
        )
        return record
    except OSError as exc:
        record.update(
            status="run_error",
            signature=None,
            signature_hash=None,
            tokens=None,
            wall_s=round(time.monotonic() - started, 3),
            error=f"exec failed: {exc}"[:MAX_ERROR],
            finished_at=utcnow(),
        )
        return record
    wall_s = round(time.monotonic() - started, 3)
    stream = intake.parse_stream(proc.stdout)
    recovered = False
    if not stream["calls"] and not stream["text"]:
        session_id = intake.stream_session_id(proc.stdout)
        if session_id:
            stream = intake.recover_stream(args.recover_base, session_id, args.username, password)
            recovered = bool(stream["calls"] or stream["text"])
    record.update(finished_at=utcnow(), wall_s=wall_s, tokens=stream.get("tokens"), recovered=recovered)
    if (proc.returncode == 0 or recovered) and (stream["calls"] or stream["text"]):
        calls = [
            {"name": call["name"], "args": judge_mod.canonical_args(call["args"])}
            for call in stream["calls"]
            if isinstance(call.get("args"), dict) and call["args"]
        ]
        signature = judge_mod.signature_of(calls)
        record.update(status="ok", calls=calls, signature=signature, signature_hash=judge_mod.signature_hash(signature))
        if stream["text"]:
            record["text_excerpt"] = excerpt(stream["text"])
        return record
    reason = f"exit={proc.returncode}" if proc.returncode else "no parseable assistant text or tool call"
    session_id = intake.stream_session_id(proc.stdout)
    aborted = abort_session(args, session_id, password) if session_id else False
    record.update(
        status="run_error",
        signature=None,
        signature_hash=None,
        error=f"{reason}; session={session_id or 'unknown'}; aborted={aborted}; "
        f"stderr_tail={stderr_tail(proc.stderr)}"[-MAX_ERROR:],
    )
    return record


def repeat_record(record: dict[str, Any]) -> dict[str, Any]:
    item = {
        key: record.get(key)
        for key in ("repeat", "status", "signature", "signature_hash", "wall_s", "tokens", "recovered", "transcript")
    }
    item.update(
        {key: record[key] for key in ("reason", "error", "started_at", "finished_at") if record.get(key) is not None}
    )
    return item


def stats(records: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    values = list(records.values())
    walls = [
        record["wall_s"]
        for record in values
        if record["status"] == "ok" and isinstance(record.get("wall_s"), (int, float))
    ]
    return {
        "total": len(values),
        "ok": sum(record["status"] == "ok" for record in values),
        "run_error": sum(record["status"] == "run_error" for record in values),
        "incomplete": sum(record["status"] == "incomplete" for record in values),
        "timeouts": sum("timeout after" in (record.get("error") or "") for record in values),
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
            "runs_run_error": summary["run_error"],
            "runs_incomplete": summary["incomplete"],
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
                if record.get("status") in ("ok", "run_error"):
                    records[(cell["id"], repeat)] = record


def wait_ready(args: argparse.Namespace, password: str) -> bool:
    url = args.health_url
    token = base64.b64encode(f"{args.username}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    deadline = time.monotonic() + args.wait_ready
    while True:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--container", default="intake-slot", help="container running opencode serve")
    parser.add_argument("--attach", default="http://localhost:4096", help="server URL inside the container")
    parser.add_argument("--dir", default="/workspace", help="working directory inside the container")
    parser.add_argument("--username", default=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"))
    parser.add_argument("--password", default=os.environ.get("OPENCODE_SERVER_PASSWORD", ""))
    parser.add_argument("--image", default="opencode-serve:v3.10.0-mymain")
    parser.add_argument("--port", type=int, default=4097)
    parser.add_argument(
        "--recover-base",
        default="http://127.0.0.1:4097",
        help="server URL reachable from this host (session API and abort)",
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:4097/config?directory=/workspace")
    parser.add_argument("--wait-ready", type=float, default=0.0, help="wait up to N seconds for /config")
    parser.add_argument("--timeout", type=float, default=240.0, help="per-run timeout in seconds")
    parser.add_argument("--budget-s", type=float, default=10800.0, help="whole-batch wall budget")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--clean",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="clear /workspace/tmp and /workspace/analysis before each run",
    )
    parser.add_argument("--only", action="append", metavar="CELL", help="restrict to a cell id (repeatable)")
    parser.add_argument("--finalize", action="store_true", help="fill missing repeats and exit")
    parser.add_argument("--summary", action="store_true", help="print recorded counts and exit")
    args = parser.parse_args(argv)
    args.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    matrix = args.matrix
    if args.only and (unknown := sorted(set(args.only) - {cell["id"] for cell in matrix["cells"]})):
        parser.error(f"unknown --only cells: {unknown}")
    if args.workers < 1 or args.timeout <= 0 or args.budget_s <= 0:
        parser.error("--workers, --timeout and --budget-s must be positive")

    meta: dict[str, Any] = {
        "schema_version": 1,
        "experiment": matrix["experiment"],
        "judge_spec_sha256": judge_mod.file_sha256(SPEC_PATH),
        "matrix_sha256": judge_mod.file_sha256(MATRIX_PATH),
        "repeats_per_cell": REPEATS,
        "image": args.image,
        "container": args.container,
        "attach": args.attach,
        "port": args.port,
        "dir": args.dir,
        "per_run_timeout_s": args.timeout,
        "batch_wall_budget_s": args.budget_s,
        "workers": args.workers,
        "workspace_clean_between_runs": args.clean,
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

    if args.wait_ready:
        if not wait_ready(args, args.password):
            print(f"server not ready at {args.health_url} within {args.wait_ready}s", file=sys.stderr)
            return 2
    if args.summary:
        print(json.dumps(stats(records), indent=2), file=sys.stderr)
        return 0

    tasks: list[tuple[dict[str, Any], int]] = []
    for slot in matrix["slots"]:
        cells = [cell for cell in matrix["cells"] if cell["slot_key"] == slot["key"]]
        for repeat in range(REPEATS):
            for cell in cells:
                if args.only and cell["id"] not in args.only:
                    continue
                existing = records.get((cell["id"], repeat))
                if existing and existing["status"] in ("ok", "run_error"):
                    continue
                tasks.append((cell, repeat))
    print(
        f"[plan] {len(tasks)} repeats to run, {len(records)} already recorded, "
        f"deadline={datetime.fromtimestamp(deadline, timezone.utc).isoformat(timespec='seconds')}",
        file=sys.stderr,
    )
    if not tasks and not args.finalize:
        print("[plan] nothing to do; use --finalize to close the batch", file=sys.stderr)
        return 0

    lock = threading.Lock()
    stop = threading.Event()

    def worker(cell: dict[str, Any], repeat: int) -> None:
        if stop.is_set() or time.time() >= deadline:
            record = {
                "cell": cell["id"],
                "slot_key": cell["slot_key"],
                "slot": cell["slot"],
                "value": cell["value"],
                "repeat": repeat,
                "status": "incomplete",
                "signature": None,
                "signature_hash": None,
                "wall_s": None,
                "tokens": None,
                "recovered": False,
                "reason": "incomplete(budget)",
                "started_at": utcnow(),
                "finished_at": utcnow(),
                "transcript": f"transcripts/{cell['id']}/{repeat}.json",
            }
        else:
            record = run_repeat(args, cell, repeat, args.password)
        with lock:
            records[(cell["id"], repeat)] = record
            atomic_write(
                TRANSCRIPTS_DIR / cell["id"] / f"{repeat}.json",
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            )
            write_results(args, meta, records)
        print(
            f"[{cell['id']} r{repeat}] {record['status']} wall={record.get('wall_s')} "
            f"sig={record.get('signature_hash')} recovered={record.get('recovered')}",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, cell, repeat) for cell, repeat in tasks]
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
                "status": "incomplete",
                "signature": None,
                "signature_hash": None,
                "wall_s": None,
                "tokens": None,
                "recovered": False,
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
