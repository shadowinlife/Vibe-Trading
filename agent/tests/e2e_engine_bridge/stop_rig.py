#!/usr/bin/env python3
"""Tear down the T7 E2E rig: engine-session cleanup, cost report, process kill.

Hygiene contract (spike §2/§7h): the rig shares the user's opencode data
dir (auth pickup), so every engine session the rig created is DELETEd
before the serve stops. Session ids are read from the scratch
``VIBE_TRADING_HOME/sessions/*/session.json`` mappings
(``opencode_engine_session_id``) — never a blanket delete, the user's own
opencode sessions share the store.

Also writes ``cost.json`` (per-engine-session model cost summed from the
assistant messages, best-effort) into ``--evidence-dir`` when given.

Usage::

    python3 stop_rig.py [--rig-root /tmp/vt-e2e-rig] [--evidence-dir DIR] [--purge]

``--purge`` removes the whole rig root (scratch home/xdg/workspace/logs)
after the processes are down. The evidence dir must live OUTSIDE the rig
root (it does by default: ``.omo/evidence/...``). Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_RIG_ROOT = Path("/tmp/vt-e2e-rig")


def _req(base: str, method: str, path: str, timeout: float = 10.0):
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except OSError:
        return None, None


def _engine_session_ids(vt_home: Path) -> list[str]:
    ids: list[str] = []
    for session_file in sorted(vt_home.glob("sessions/*/session.json")):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        engine_id = (data.get("config") or {}).get("opencode_engine_session_id")
        if isinstance(engine_id, str) and engine_id:
            ids.append(engine_id)
    return ids


def _session_cost(serve_url: str, engine_id: str) -> float:
    status, messages = _req(serve_url, "GET", f"/session/{engine_id}/message")
    if status != 200 or not isinstance(messages, list):
        return 0.0
    total = 0.0
    for entry in messages:
        info = entry.get("info") if isinstance(entry, dict) else None
        cost = info.get("cost") if isinstance(info, dict) else None
        if isinstance(cost, (int, float)):
            total += float(cost)
    return total


def cleanup_engine_sessions(state: dict, evidence_dir: Path | None) -> None:
    serve_url = state["serve_url"]
    vt_home = Path(state["vt_home"])
    engine_ids = _engine_session_ids(vt_home)
    costs: dict[str, float] = {}
    for engine_id in engine_ids:
        costs[engine_id] = _session_cost(serve_url, engine_id)
        status, _ = _req(serve_url, "DELETE", f"/session/{engine_id}")
        print(f"[rig] DELETE engine session {engine_id} -> {status}")
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "engine_sessions": engine_ids,
            "cost_usd_per_session": costs,
            "cost_usd_total": round(sum(costs.values()), 6),
            "note": (
                "serve-side model cost of rig sessions (assistant message "
                "cost fields); excludes the gateway auto-title ChatLLM calls"
            ),
        }
        (evidence_dir / "cost.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            f"[rig] cost report: total ${report['cost_usd_total']} -> {evidence_dir / 'cost.json'}"
        )


def _kill(pid: int, label: str) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        print(f"[rig] {label} pid={pid} already gone")
        return
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            print(f"[rig] {label} pid={pid} terminated")
            return
        time.sleep(0.3)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        print(f"[rig] {label} pid={pid} SIGKILLed")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--purge", action="store_true")
    args = parser.parse_args()

    state_path = args.rig_root / "rig_state.json"
    if not state_path.exists():
        print("[rig] no rig_state.json — nothing to stop")
        return 0
    state = json.loads(state_path.read_text(encoding="utf-8"))

    cleanup_engine_sessions(state, args.evidence_dir)
    _kill(state["gateway_pid"], "gateway")
    _kill(state["serve_pid"], "serve")
    state_path.unlink()

    if args.purge:
        shutil.rmtree(args.rig_root, ignore_errors=True)
        print(f"[rig] purged {args.rig_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
