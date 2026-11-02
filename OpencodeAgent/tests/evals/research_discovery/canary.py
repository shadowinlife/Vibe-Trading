"""Isolated OpenCode/MCP hook probe with a deterministic loopback model stub.

Run in an existing image with --network none, no port publication and no real
credentials. This verifies hook mechanics, not user approval or model quality.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ModelStub(BaseHTTPRequestHandler):
    """Emit one deterministic MCP tool call followed by a final answer."""

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body["messages"]
        prompt = " ".join(
            str(message.get("content", ""))
            for message in messages
            if message["role"] == "user"
        )
        finished = any(message["role"] == "tool" for message in messages)
        name = "probe_blocked" if "BLOCK_CASE" in prompt else "probe_echo"
        tool_call = {
            "id": "canary-call",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps({"session_id": "model-invented"}),
            },
        }
        chunks = [
            {
                "id": "canary",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            },
            {
                "id": "canary",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "delta": (
                            {"content": "canary complete"}
                            if finished
                            else {"tool_calls": [dict(tool_call, index=0)]}
                        ),
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "canary",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if finished else "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format: str, *args: object) -> None:
        pass


def _mcp_server(log: Path) -> None:
    from fastmcp import FastMCP

    server = FastMCP("discovery-canary")

    @server.tool
    def blocked(session_id: str = "") -> str:
        """A test-only side-effect counter. The hook must prevent this call."""
        with log.open("a") as handle:
            handle.write(
                json.dumps({"tool": "blocked", "session_id": session_id}) + "\n"
            )
        return "unexpected execution"

    @server.tool
    def echo(session_id: str = "") -> str:
        """Record the trusted OpenCode session ID received through the hook."""
        with log.open("a") as handle:
            handle.write(json.dumps({"tool": "echo", "session_id": session_id}) + "\n")
        return session_id

    server.run(transport="stdio")


def run() -> int:
    """Probe the installed OpenCode binary without external model/data access."""
    if shutil.which("opencode") is None:
        raise RuntimeError(
            "OpenCode binary required; use the documented existing image"
        )
    fixture = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="vt-discovery-canary-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        config_dir = root / "config"
        config_dir.mkdir()
        plugins = config_dir / "plugins"
        plugins.mkdir()
        shutil.copyfile(fixture / "canary_plugin.ts", plugins / "canary_plugin.ts")
        sdk_source = Path.home() / ".config" / "opencode" / "node_modules"
        if not (sdk_source / "@opencode-ai" / "plugin").is_dir():
            raise RuntimeError(
                "Pre-baked plugin SDK missing; will not install at runtime"
            )
        (config_dir / "node_modules").symlink_to(sdk_source)
        log = root / "calls.jsonl"
        http = ThreadingHTTPServer(("127.0.0.1", 0), ModelStub)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "probe/fixture",
            "provider": {
                "probe": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {
                        "baseURL": f"http://127.0.0.1:{http.server_port}/v1",
                        "apiKey": uuid.uuid4().hex,
                    },
                    "models": {
                        "fixture": {
                            "name": "Deterministic offline fixture",
                            "tool_call": True,
                        }
                    },
                }
            },
            "mcp": {
                "probe": {
                    "type": "local",
                    "command": [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--mcp",
                        str(log),
                    ],
                }
            },
            "permission": {"*": "deny", "probe_*": "allow"},
            "agent": {"build": {"model": "probe/fixture", "steps": 4}},
        }
        (config_dir / "opencode.json").write_text(json.dumps(config))
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "LD_LIBRARY_PATH"}
        }
        env.update(
            {
                "OPENCODE_CONFIG_DIR": str(config_dir),
                "XDG_CONFIG_HOME": str(root / "xdg-config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            }
        )
        outputs: list[str] = []
        try:
            for case in ("BLOCK_CASE", "ECHO_CASE", "ECHO_CASE_SECOND_SESSION"):
                result = subprocess.run(
                    [
                        "opencode",
                        "run",
                        "--format",
                        "json",
                        "--agent",
                        "build",
                        "--model",
                        "probe/fixture",
                        case,
                    ],
                    cwd=project,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if result.returncode:
                    raise RuntimeError(
                        f"OpenCode probe failed: {result.stderr[-2000:]}"
                    )
                outputs.append(result.stdout)
        finally:
            http.shutdown()
            http.server_close()
            worker.join()
        rows = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        if any(row["tool"] == "blocked" for row in rows):
            raise AssertionError("MCP side effect ran despite before hook")
        if "discovery-canary-blocked" not in outputs[0]:
            raise AssertionError("No observed explicit hook rejection")
        echoes = [row for row in rows if row["tool"] == "echo"]
        if len(echoes) != 2 or len({row["session_id"] for row in echoes}) != 2:
            raise AssertionError("Expected two distinct session-bound MCP echoes")
        for output, row in zip(outputs[1:], echoes):
            if row["session_id"] == "model-invented" or row["session_id"] not in output:
                raise AssertionError(
                    "MCP argument not bound to the actual OpenCode session"
                )
        print(
            json.dumps(
                {
                    "blocked_side_effects": 0,
                    "explicit_rejection": True,
                    "session_bound_echoes": len(echoes),
                    "distinct_sessions": 2,
                    "scope": "hook mechanics only; approvals/restart/compaction/OMO unverified",
                }
            )
        )
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--mcp":
        _mcp_server(Path(sys.argv[2]))
    else:
        raise SystemExit(run())
