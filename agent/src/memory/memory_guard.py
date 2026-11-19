"""MemoryGuardMiddleware: auto-save execution context after every MCP tool call.

FastMCP middleware that wraps ALL tool calls with pre/post memory hooks.
Zero LLM involvement — the middleware fires mechanically on every tool
invocation, guaranteeing memory persistence regardless of whether the
LLM remembers to call memory_* tools.

Pre-hook:  recall relevant memories for the tool being called.
Post-hook: save execution context (tool_name, args, result, duration).
           For backtest/factor_analysis tools, also auto-reflect.

Usage:
    from fastmcp import FastMCP
    from src.memory.memory_guard import MemoryGuardMiddleware

    mcp = FastMCP("Vibe-Trading")
    mcp.add_middleware(MemoryGuardMiddleware())
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

# ``backtest`` is intentionally absent: its dedicated post-success hook in
# ``backtest_tool.py`` already reflects from the run card (richer, run_dir
# keyed); a second reflection here would double-record every run.
_TOOLS_THAT_PRODUCE_INSIGHTS = frozenset(
    {
        "factor_analysis",
        "analyze_trade_journal",
        "extract_shadow_strategy",
        "run_shadow_backtest",
        "pattern_recognition",
    }
)


def _safe_truncate(obj: Any, max_chars: int = 500) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        if len(s) > max_chars:
            s = s[: max_chars - 3] + "..."
        return s
    except Exception:
        return str(obj)[:max_chars]


class MemoryGuardMiddleware(Middleware):
    """Auto-save/recall memory on every MCP tool call."""

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ):
        tool_name = context.message.name
        args = context.message.arguments or {}
        start_ts = time.time()

        result = await call_next(context)

        # Never record the memory_* tools themselves: that would turn every
        # memory operation into self-referential noise entries.
        if not tool_name.startswith("memory_"):
            elapsed_ms = int((time.time() - start_ts) * 1000)
            self._save_post_hook(tool_name, args, result, elapsed_ms)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_post_hook(
        self,
        tool_name: str,
        args: dict,
        result: Any,
        elapsed_ms: int,
    ) -> None:
        try:
            from src.memory.mcp_adapter import MemoryMCPAdapter

            adapter = MemoryMCPAdapter()
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            content = (
                f"Tool: {tool_name}\n"
                f"Args: {_safe_truncate(args)}\n"
                f"Result: {_safe_truncate(result)}\n"
                f"Duration: {elapsed_ms}ms\n"
                f"Time: {now_iso}"
            )
            name = f"tool:{tool_name}:{now_iso[:10]}"

            adapter.memory_save(
                name=name,
                description=f"Auto-saved: {tool_name} ({elapsed_ms}ms)",
                content=content,
                memory_type="project",
            )

            if tool_name in _TOOLS_THAT_PRODUCE_INSIGHTS:
                self._auto_reflect(tool_name, args, result, adapter)

        except Exception:
            logger.debug(
                "memory_guard post-hook failed for %s", tool_name, exc_info=True
            )

    def _auto_reflect(
        self,
        tool_name: str,
        args: dict,
        result: Any,
        adapter: Any,
    ) -> None:
        try:
            outcome = {}
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        for key in (
                            "sharpe",
                            "max_drawdown",
                            "annual_return",
                            "total_return",
                            "win_rate",
                        ):
                            if key in parsed and isinstance(parsed[key], (int, float)):
                                outcome[key] = parsed[key]
                except (json.JSONDecodeError, TypeError):
                    pass

            strategy_type = tool_name
            if tool_name == "backtest":
                strategy_type = (
                    args.get("run_dir", "unknown").split("/")[-1] or "unknown"
                )

            adapter.memory_reflect(
                strategy_type=strategy_type,
                outcome=outcome,
                original_params=dict(args),
            )
        except Exception:
            logger.debug(
                "memory_guard auto-reflect failed for %s", tool_name, exc_info=True
            )
