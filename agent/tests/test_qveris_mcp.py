"""Contract tests for issue #964 — QVeris tools exposed over MCP.

The three QVeris agent tools (``qveris_search`` / ``qveris_inspect`` /
``qveris_execute``) follow the key-gated contract of ``get_macro_series`` /
``iwencai_search``, registration-time gated since the B1 exposure work: an
unconfigured install never sees them in ``tools/list``, and a call-path
misfire still receives an actionable, setup-naming JSON envelope from the
tool's own ``execute()`` — never the registry's generic "Tool not found".

The cached ``mcp_server._registry`` bakes in ``check_available()`` results, so
every behavioral test resets it to ``None`` before and after the call to force
a rebuild against the monkeypatched config path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_server
from src.tools import qveris_tool as qt

AGENT_DIR = Path(__file__).resolve().parents[1]

# fastmcp wraps the tool; reach the raw callable.
_qveris_search = getattr(mcp_server.qveris_search, "fn", None) or getattr(
    mcp_server.qveris_search, "__wrapped__", mcp_server.qveris_search
)
_qveris_inspect = getattr(mcp_server.qveris_inspect, "fn", None) or getattr(
    mcp_server.qveris_inspect, "__wrapped__", mcp_server.qveris_inspect
)
_qveris_execute = getattr(mcp_server.qveris_execute, "fn", None) or getattr(
    mcp_server.qveris_execute, "__wrapped__", mcp_server.qveris_execute
)


@pytest.fixture(autouse=True)
def qveris_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(qt, "QVERIS_CONFIG_PATH", tmp_path / "qveris.json")
    monkeypatch.delenv("QVERIS_API_KEY", raising=False)
    monkeypatch.delenv("QVERIS_BASE_URL", raising=False)
    qt._SESSION_SPEND.clear()
    getattr(qt, "_SESSION_RESERVED", {}).clear()
    return tmp_path / "qveris.json"


@pytest.fixture
def fresh_mcp_registry():
    """Force mcp_server to rebuild its cached registry under the test config."""
    mcp_server._registry = None
    yield
    mcp_server._registry = None


_KEYLESS_SURFACE_SNIPPET = (
    "import asyncio, json, os, sys, tempfile; sys.path.insert(0, '.');\n"
    "_tmp = tempfile.mkdtemp(prefix='vibe-qveris-anchor-');\n"
    "os.environ['VIBE_TRADING_HOME'] = _tmp; os.environ['HOME'] = _tmp;\n"
    "import mcp_server as m;\n"
    "print(json.dumps([t.name for t in asyncio.run(m.mcp.list_tools())]))"
)


def test_mcp_server_gates_qveris_tools_at_registration() -> None:
    """B1: without paid routing the qveris wrappers are not disclosed at all.

    Measured in a keyless child interpreter: the gate is applied at module
    import time against the process environment, so an in-process assertion
    would depend on the host machine's credentials. The configured branch is
    covered deterministically by tests/test_mcp_exposure_gates.py.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("QVERIS_API_KEY", "FRED_API_KEY", "VIBE_TRADING_IWENCAI_KEY", "VIBE_TW_STOCK_DB")
    }
    proc = subprocess.run(
        [sys.executable, "-c", _KEYLESS_SURFACE_SNIPPET],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    tools = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not {"qveris_search", "qveris_inspect", "qveris_execute"} & set(tools)

    # The module docstring states the keyless surface count; it must agree
    # with the surface a fresh install sees, whatever they say.
    claimed = re.search(r"Surfaces (\d+) tools", mcp_server.__doc__ or "")
    assert claimed, "mcp_server's module docstring no longer states a tool count"
    assert len(tools) == int(
        claimed.group(1)
    ), f"Module docstring says {claimed.group(1)} tools, keyless surface has {len(tools)}."


def test_qveris_search_unconfigured_returns_actionable_error(
    fresh_mcp_registry,
) -> None:
    """Without QVeris routing the MCP call surfaces the setup hint, not 'Tool not found'."""
    payload = json.loads(_qveris_search("US listed options chain implied volatility Greeks AAPL"))

    assert payload.get("ok") is False
    assert payload.get("error") != "Tool 'qveris_search' not found"
    assert "QVERIS_API_KEY" in payload.get("error", "")


def test_qveris_search_configured_paid_delegates(fresh_mcp_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyed paid-mode install delegates to the registered tool end to end."""
    qt.save_qveris_config(qt.QVerisConfig(enabled=True, api_key="sk-live", mode="paid"))
    canned = {
        "search_id": "srch_1",
        "results": [
            {
                "tool_id": "tool_options_iv",
                "name": "US options IV surface",
                "provider": "options-data-co",
                "expected_cost": "1 credit",
                "stats": {"success_rate": 0.98},
            }
        ],
        "remaining_credits": 99.0,
    }

    class FakeClient:
        def search(self, query, *, limit=20, session_id=None):
            return canned

    monkeypatch.setattr(qt.QVerisSearchTool, "_client", lambda self: FakeClient())

    payload = json.loads(_qveris_search("options iv greeks", limit=5))

    assert payload["ok"] is True
    assert payload["search_id"] == "srch_1"
    assert payload["results"][0]["tool_id"] == "tool_options_iv"
    assert payload["results"][0]["provider"] == "options-data-co"
    assert payload["results"][0]["expected_cost"] == "1 credit"
    assert payload["results"][0]["stats"] == {"success_rate": 0.98}
    assert payload["remaining_credits"] == 99.0


def test_qveris_execute_unconfigured_returns_actionable_error(
    fresh_mcp_registry,
) -> None:
    """qveris_execute must also surface the actionable envelope, never 'Tool not found'."""
    payload = json.loads(_qveris_execute(tool_id="tool_1", parameters={"x": 1}))

    assert payload.get("ok") is False
    assert payload.get("error") != "Tool 'qveris_execute' not found"
    assert "QVeris is not configured" in payload.get("error", "")
