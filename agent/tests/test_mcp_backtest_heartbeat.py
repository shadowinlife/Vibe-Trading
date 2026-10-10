"""MCP backtest tool heartbeat: progress notifications keep client timeouts reset.

opencode (and any MCP client with ``resetTimeoutOnProgress``) resets its 60s
callTool timeout on every progress notification, so the async ``backtest``
tool must emit ``ctx.report_progress`` heartbeats while the blocking
``run_backtest`` runs in a worker thread. These tests call the
decorated tool directly (``@mcp.tool`` returns the original coroutine, which
delegates to the module-level ``_backtest_impl``) with a fake Context
recording every report.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest

import mcp_server
import src.tools.backtest_tool as backtest_tool_mod


class _RecordingContext:
    """Stand-in for fastmcp Context recording report_progress calls."""

    def __init__(self) -> None:
        self.reports: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        self.reports.append((progress, total, message))


@pytest.fixture()
def fast_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_BACKTEST_HEARTBEAT_SECONDS", 0.05)


def test_slow_run_emits_repeated_increasing_heartbeats(
    fast_heartbeat, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_run(run_dir: str) -> str:
        del run_dir
        time.sleep(0.3)
        return '{"status": "ok"}'

    monkeypatch.setattr(backtest_tool_mod, "run_backtest", slow_run)
    ctx = _RecordingContext()

    result = asyncio.run(mcp_server.backtest("/tmp/run", ctx))

    assert result == '{"status": "ok"}'
    assert len(ctx.reports) >= 2
    values = [report[0] for report in ctx.reports]
    assert all(value > 0 for value in values)
    assert values == sorted(values)
    assert values[-1] > values[0]
    assert all(report[1] is None for report in ctx.reports)
    assert all(isinstance(report[2], str) and report[2] for report in ctx.reports)


def test_ctx_none_returns_identical_result(
    fast_heartbeat, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_run(run_dir: str) -> str:
        calls.append(run_dir)
        return '{"status": "ok", "run_dir": "%s"}' % run_dir

    monkeypatch.setattr(backtest_tool_mod, "run_backtest", fake_run)

    result = asyncio.run(mcp_server.backtest("/tmp/run", None))

    assert result == '{"status": "ok", "run_dir": "/tmp/run"}'
    assert calls == ["/tmp/run"]


def test_fast_run_returns_intact_with_at_most_one_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "_BACKTEST_HEARTBEAT_SECONDS", 30.0)

    def fast_run(run_dir: str) -> str:
        del run_dir
        return '{"status": "ok", "exit_code": 0}'

    monkeypatch.setattr(backtest_tool_mod, "run_backtest", fast_run)
    ctx = _RecordingContext()

    result = asyncio.run(mcp_server.backtest("/tmp/run", ctx))

    assert result == '{"status": "ok", "exit_code": 0}'
    assert len(ctx.reports) <= 1


def test_run_backtest_exception_propagates(
    fast_heartbeat, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding_run(run_dir: str) -> str:
        del run_dir
        time.sleep(0.12)
        raise RuntimeError("engine boom")

    monkeypatch.setattr(backtest_tool_mod, "run_backtest", exploding_run)
    ctx = _RecordingContext()

    with pytest.raises(RuntimeError, match="engine boom"):
        asyncio.run(mcp_server.backtest("/tmp/run", ctx))

    assert len(ctx.reports) >= 1


def test_decorated_tool_is_registered_async_and_schema_excludes_ctx() -> None:
    """``@mcp.tool`` returns the original callable; async must not change it.

    The ``ctx`` parameter is injected by FastMCP and must never appear in the
    tool's input schema, and the heartbeat constant is pinned at 20s (3x
    margin under the 60s MCP SDK default callTool timeout).
    """
    assert inspect.iscoroutinefunction(mcp_server.backtest)
    assert mcp_server._BACKTEST_HEARTBEAT_SECONDS == 20

    tools = asyncio.run(mcp_server.mcp.list_tools())
    registered = next(tool for tool in tools if tool.name == "backtest")
    assert "ctx" not in registered.parameters.get("properties", {})
    assert registered.parameters.get("required") == ["run_dir"]
