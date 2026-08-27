"""Stdio-transport exposure checks: gates must hold on the real JSON-RPC path.

test_mcp_exposure_gates.py measures the gate through in-process import; this
suite walks the actual MCP wire protocol (initialize -> tools/list ->
tools/call) in a spawned server, the path external clients (opencode, Claude
Desktop, Cursor) take.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"

GATED_TOOLS = {
    "get_macro_series",
    "iwencai_search",
    "qveris_search",
    "qveris_inspect",
    "qveris_execute",
    "trading_connections",
    "trading_select_connection",
    "trading_check",
    "trading_account",
    "trading_positions",
    "trading_orders",
    "trading_quote",
    "trading_history",
    "reap_stale_runs",
    "refresh_strategy_evidence",
}

_CREDENTIAL_GATES = (
    "FRED_API_KEY",
    "VIBE_TRADING_IWENCAI_KEY",
    "QVERIS_API_KEY",
    "VIBE_TW_STOCK_DB",
)

TIMEOUT = 30.0


def _pump(stream, queue: Queue) -> None:
    try:
        for line in iter(stream.readline, b""):
            queue.put(line)
    finally:
        queue.put(None)


def _send(proc: subprocess.Popen, payload: dict) -> None:
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _wait(queue: Queue, want_id: int) -> dict:
    start = time.time()
    while time.time() - start < TIMEOUT:
        try:
            line = queue.get(timeout=0.5)
        except Empty:
            continue
        if line is None:
            raise AssertionError("server closed stdout before answering")
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - skip non-JSON-RPC log lines
            continue
        if obj.get("id") == want_id:
            return obj
    raise AssertionError(f"timed out waiting for response id={want_id}")


def test_stdio_keyless_surface_and_category_output(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_GATES}
    env["VIBE_TRADING_HOME"] = str(tmp_path)
    env["HOME"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, str(AGENT_DIR / "mcp_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=AGENT_DIR,
        env=env,
        text=False,
    )
    queue: Queue = Queue()
    threading.Thread(target=_pump, args=(proc.stdout, queue), daemon=True).start()
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "exposure-test", "version": "0"},
                },
            },
        )
        _wait(queue, 1)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _wait(queue, 2)["result"]["tools"]
        names = {tool["name"] for tool in listed}
        assert not GATED_TOOLS & names, sorted(GATED_TOOLS & names)
        assert "list_skills" in names

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_skills", "arguments": {}},
            },
        )
        call = _wait(queue, 3)["result"]
        text = call["content"][0]["text"]
        rows = json.loads(text)
        assert rows and all(set(row) == {"name", "description", "category"} for row in rows)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
