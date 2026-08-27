"""MCP exposure-gate tests (PLAN-B1/B2/B3): unavailable tools register only when available.

The agent registry gates unavailable tools at registration time
(``build_registry`` skips classes whose ``check_available()`` is False); the
MCP surface applies the same gates at startup (``mcp_server._apply_exposure_gates``):
credential-gated tools (B1), connector-gated trading_* tools (B2), and the
two ops tools that leave the default research surface (B3). Each scenario runs
in a child interpreter because the gates are applied at module import time
against the process environment and runtime root, and import caches would leak
in-process state between scenarios.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]

KEY_GATED_TOOLS = {
    "get_macro_series",
    "iwencai_search",
    "qveris_search",
    "qveris_inspect",
    "qveris_execute",
}

TRADING_TOOLS = {
    "trading_connections",
    "trading_select_connection",
    "trading_check",
    "trading_account",
    "trading_positions",
    "trading_orders",
    "trading_quote",
    "trading_history",
}

OPS_TOOLS = {"reap_stale_runs", "refresh_strategy_evidence"}

# Credential env vars that gate tool registration; cleared in every scenario so
# the measured surface never depends on the host machine's configured keys.
_CREDENTIAL_GATES = (
    "FRED_API_KEY",
    "VIBE_TRADING_IWENCAI_KEY",
    "QVERIS_API_KEY",
    "VIBE_TW_STOCK_DB",
)

_LIST_TOOLS_SNIPPET = (
    "import asyncio, json, sys; sys.path.insert(0, '.');"
    "import mcp_server as m;"
    "print(json.dumps([t.name for t in asyncio.run(m.mcp.list_tools())]))"
)


def _list_mcp_tools(
    *,
    extra_env: dict[str, str] | None = None,
    home: Path | None = None,
) -> list[str]:
    """Return the MCP tool names measured in a clean child interpreter.

    Args:
        extra_env: Credential env vars to inject on top of the cleared base.
        home: Optional HOME/VIBE_TRADING_HOME override (isolates both the
            qveris config under ~/.vibe-trading and the connector state under
            the runtime root).

    Returns:
        Tool names exactly as the MCP server exposes them.
    """
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_GATES}
    if home is not None:
        env["HOME"] = str(home)
        env["VIBE_TRADING_HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _LIST_TOOLS_SNIPPET],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_keyless_surface_hides_every_gated_tool(tmp_path: Path) -> None:
    """Without credentials or a connector, none of the gated tools is disclosed."""
    tools = _list_mcp_tools(home=tmp_path)
    hidden = KEY_GATED_TOOLS | TRADING_TOOLS | OPS_TOOLS
    assert not hidden & set(tools), sorted(hidden & set(tools))
    # Always-on sentinels stay registered: the gates remove only gated tools.
    assert {"get_market_data", "backtest", "list_skills", "web_search", "run_swarm"} <= set(tools)
    assert len(tools) == 59


def test_fred_and_iwencai_keys_restore_only_their_tools(tmp_path: Path) -> None:
    """A credential restores exactly its own tool; qveris also needs paid mode."""
    tools = _list_mcp_tools(
        extra_env={"FRED_API_KEY": "test-key", "VIBE_TRADING_IWENCAI_KEY": "test-key"},
        home=tmp_path,
    )
    assert "get_macro_series" in tools
    assert "iwencai_search" in tools
    assert not {"qveris_search", "qveris_inspect", "qveris_execute"} & set(tools)
    assert len(tools) == 61


def test_qveris_paid_mode_restores_the_marketplace_tools(tmp_path: Path) -> None:
    """QVeris tools need BOTH the API key and the paid-mode switch."""
    (tmp_path / ".vibe-trading").mkdir()
    (tmp_path / ".vibe-trading" / "qveris.json").write_text(
        json.dumps({"enabled": True, "mode": "paid"}), encoding="utf-8"
    )
    tools = _list_mcp_tools(
        extra_env={
            "FRED_API_KEY": "test-key",
            "VIBE_TRADING_IWENCAI_KEY": "test-key",
            "QVERIS_API_KEY": "test-key",
        },
        home=tmp_path,
    )
    assert KEY_GATED_TOOLS <= set(tools)
    assert len(tools) == 64


def test_qveris_key_without_paid_mode_stays_hidden(tmp_path: Path) -> None:
    """The key alone is not enough: free mode keeps the marketplace hidden."""
    (tmp_path / ".vibe-trading").mkdir()
    (tmp_path / ".vibe-trading" / "qveris.json").write_text(
        json.dumps({"enabled": True, "mode": "free"}), encoding="utf-8"
    )
    tools = _list_mcp_tools(extra_env={"QVERIS_API_KEY": "test-key"}, home=tmp_path)
    assert not {"qveris_search", "qveris_inspect", "qveris_execute"} & set(tools)


def test_configured_connector_restores_the_trading_tools(tmp_path: Path) -> None:
    """The selection marker (``connector use``) flips the trading family back on."""
    (tmp_path / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "ibkr-paper-local"}), encoding="utf-8"
    )
    tools = _list_mcp_tools(home=tmp_path)
    assert TRADING_TOOLS <= set(tools)
    assert len(tools) == 67


def test_ops_tools_stay_off_even_with_a_configured_connector(tmp_path: Path) -> None:
    """B3: ops tools leave the default MCP surface regardless of configuration."""
    (tmp_path / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "ibkr-paper-local"}), encoding="utf-8"
    )
    tools = _list_mcp_tools(home=tmp_path)
    assert not OPS_TOOLS & set(tools)
