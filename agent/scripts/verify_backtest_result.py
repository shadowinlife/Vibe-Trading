"""Verify the inline summary of one successful MCP ``backtest`` result.

Runs a real ``backtest`` call over stdio and asserts the consumer contract:
an inline ``summary`` with ``schema_version`` ``"1.0"``, every required key,
a non-empty ``equity_preview`` of at most 50 points, and the whole result
inside the implementation's 48 KiB budget (4 KiB under the host's 50 KiB cap).

Usage::

    RUN_DIR=~/.vibe-trading/runs/<run> \\
    VT_MCP_CMD="python /path/to/agent/mcp_server.py" \\
    python agent/scripts/verify_backtest_result.py

``--run-dir`` and ``--mcp-cmd`` override ``RUN_DIR`` and ``VT_MCP_CMD``. When
``VT_MCP_CMD`` is unset, the script starts this repository's own
``agent/mcp_server.py`` with the current interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

BUDGET_BYTES = 48 * 1024
BUDGET_LINES = 2000
MAX_PREVIEW_POINTS = 50
REQUIRED_SUMMARY_KEYS = (
    "run_id",
    "metrics",
    "equity_preview",
    "codes",
    "start_date",
    "end_date",
    "interval",
    "artifact_paths",
)


def _default_mcp_cmd() -> str:
    server = Path(__file__).resolve().parents[1] / "mcp_server.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(server))}"


async def _call_backtest(run_dir: str, mcp_cmd: str) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    argv = shlex.split(mcp_cmd)
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("backtest", {"run_dir": run_dir})
    return "\n".join(
        block.text
        for block in result.content
        if getattr(block, "text", None) is not None
    )


def _summary_failures(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return ["summary missing"]
    failures: list[str] = []
    if summary.get("schema_version") != "1.0":
        failures.append(f"schema_version={summary.get('schema_version')!r}, want '1.0'")
    missing = [key for key in REQUIRED_SUMMARY_KEYS if key not in summary]
    if missing:
        failures.append(f"summary keys missing: {missing}")
    preview = summary.get("equity_preview")
    if not isinstance(preview, list) or not 0 < len(preview) <= MAX_PREVIEW_POINTS:
        failures.append(
            f"equity_preview must be 1..{MAX_PREVIEW_POINTS} points, "
            f"got {type(preview).__name__} with {len(preview) if isinstance(preview, list) else '?'}"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        default=os.environ.get("RUN_DIR", ""),
        help="Run directory a successful backtest will complete in.",
    )
    parser.add_argument(
        "--mcp-cmd",
        default=os.environ.get("VT_MCP_CMD") or _default_mcp_cmd(),
        help="Command that starts the VT MCP server over stdio.",
    )
    args = parser.parse_args()
    if not args.run_dir:
        parser.error("--run-dir or RUN_DIR is required")

    raw = asyncio.run(_call_backtest(args.run_dir, args.mcp_cmd))
    print(raw)
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL envelope is not valid JSON: {exc}", file=sys.stderr)
        return 1

    summary = envelope.get("summary")
    failures = _summary_failures(summary)
    size = len(raw.encode("utf-8"))
    lines = raw.count("\n") + 1
    if size > BUDGET_BYTES:
        failures.append(f"{size} bytes > {BUDGET_BYTES}")
    if lines > BUDGET_LINES:
        failures.append(f"{lines} lines > {BUDGET_LINES}")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    omitted = summary.get("omitted_fields", []) if isinstance(summary, dict) else []
    print(f"PASS summary schema 1.0 bytes={size} lines={lines} omitted={omitted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
