"""Bounded MCP stdio client used by the harness_bench eval scaffolding.

This module reuses, verbatim in spirit, the subprocess spawn pattern of the
existing MCP smoke test (``agent/tests/test_mcp_server_smoke.py``): the server
is started as ``python -c "from mcp_server import main; main()"`` with stdio
transport, then driven over newline-delimited JSON-RPC:

    initialize -> notifications/initialized -> tools/list [-> tools/call]

The client is a context manager; leaving it always kills and reaps the child,
so no MCP subprocess can outlive a capture or a soak iteration. All waits are
bounded by explicit timeouts — a hung server surfaces as ``McpSpawnError``,
never as an infinite block.

Research-only: nothing here places orders or touches product runtime code.
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
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[3]

#: Generous bound — covers cold imports of fastmcp plus the tool-registry
#: build on slow runners (matches the smoke test's INIT_TIMEOUT).
DEFAULT_INIT_TIMEOUT = 30.0

#: tools/call bound for network-free tools (matches the smoke test).
DEFAULT_CALL_TIMEOUT = 15.0


class McpSpawnError(RuntimeError):
    """The MCP subprocess failed to spawn or answer within its timeout."""


def _reader(stream, q: Queue) -> None:
    """Pump every line from *stream* into *q*; signal EOF with ``None``."""
    try:
        for line in iter(stream.readline, b""):
            q.put(line)
    finally:
        q.put(None)


class McpStdioClient:
    """Drive ``mcp_server.main()`` over stdio JSON-RPC with bounded waits.

    Args:
        env_overrides: Extra environment for the child. Keys mapped to
            ``None`` are deleted from the child environment (used to pin
            credential state deterministically).
        init_timeout: Bound for ``initialize`` and ``tools/list``.
        call_timeout: Bound for ``tools/call``.
        python: Interpreter for the child (default: this interpreter).
        cwd: Working directory for the child (default: ``agent/``).
    """

    def __init__(
        self,
        env_overrides: dict[str, str | None] | None = None,
        init_timeout: float = DEFAULT_INIT_TIMEOUT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        python: str | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._env_overrides = dict(env_overrides or {})
        self._init_timeout = init_timeout
        self._call_timeout = call_timeout
        self._python = python or sys.executable
        self._cwd = str(cwd or AGENT_DIR)
        self._proc: subprocess.Popen | None = None
        self._queue: Queue = Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 0

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "McpStdioClient":
        env = os.environ.copy()
        env["PYTHONPATH"] = AGENT_DIR.__str__() + os.pathsep + env.get("PYTHONPATH", "")
        # Force unbuffered stdio in the child so its responses reach our
        # reader without being held in libc/Python buffers.
        env["PYTHONUNBUFFERED"] = "1"
        for key, value in self._env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        self._proc = subprocess.Popen(
            [self._python, "-c", "from mcp_server import main; main()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env=env,
            cwd=self._cwd,
        )
        self._reader = threading.Thread(
            target=_reader, args=(self._proc.stdout, self._queue), daemon=True
        )
        self._reader.start()
        return self

    def close(self) -> None:
        """Kill and reap the child; idempotent, never raises."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- JSON-RPC plumbing ---------------------------------------------------

    def _send(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        payload = (json.dumps(obj) + "\n").encode("utf-8")
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._next_id += 1
        want_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": want_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        start = time.time()
        while time.time() - start < timeout:
            try:
                line = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if line is None:
                raise McpSpawnError(f"EOF from MCP server while awaiting {method}")
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, ValueError):
                # The server may emit non-JSON-RPC log lines on stdout in some
                # environments; skip and keep scanning for our response.
                continue
            if obj.get("id") == want_id:
                if "error" in obj:
                    raise McpSpawnError(f"{method} returned error: {obj['error']}")
                return obj
        raise McpSpawnError(f"no response to {method} within {timeout:.0f}s")

    # -- protocol steps ------------------------------------------------------

    def initialize(self) -> dict:
        """Run the initialize handshake plus the initialized notification."""
        response = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "harness-bench", "version": "1"},
            },
            self._init_timeout,
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def list_tools(self) -> list[dict]:
        """Return the ``tools/list`` catalogue (initialize first)."""
        response = self._request("tools/list", None, self._init_timeout)
        return list(response.get("result", {}).get("tools") or [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Invoke ``tools/call`` and return the structured payload dict.

        Unwraps FastMCP's ``result.content[0].text`` JSON envelope (and the
        double-wrapped ``{"result": "<json>"}`` variant some tools use), the
        same way the smoke test does.
        """
        response = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            self._call_timeout,
        )
        result = response.get("result", {})
        content = result.get("content") or []
        if not content:
            raise McpSpawnError(f"tools/call {name} returned no content: {response}")
        data = json.loads(content[0].get("text", ""))
        if (
            isinstance(data, dict)
            and set(data.keys()) == {"result"}
            and isinstance(data["result"], str)
        ):
            try:
                data = json.loads(data["result"])
            except (json.JSONDecodeError, ValueError):
                pass
        return data


def spawn_and_list_tools(
    env_overrides: dict[str, str | None] | None = None,
    init_timeout: float = DEFAULT_INIT_TIMEOUT,
) -> list[dict]:
    """One-shot helper: spawn the server, handshake, return ``tools/list``.

    The subprocess is always torn down before returning.
    """
    with McpStdioClient(
        env_overrides=env_overrides, init_timeout=init_timeout
    ) as client:
        client.initialize()
        return client.list_tools()
