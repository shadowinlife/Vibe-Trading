"""Per-session tool allowlist: registry enforcement and JSON persistence.

The allowlist is a security boundary. These tests pin both halves: the registry
the agent receives contains exactly the named tools (MCP included), and the
boundary survives a session.json round trip so ``send_message`` cannot lose it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.tools as tools_module
from src.agent.tools import BaseTool
from src.session.events import EventBus
from src.session.models import Session
from src.session.service import SessionService
from src.session.store import SessionStore
from src.tools import build_registry

pytestmark = pytest.mark.unit

# The default registry shape at the time the allowlist was introduced. The
# allowlist is opt-in: omitting it may not change this count.
LEGACY_DEFAULT_TOOL_COUNT = 121


# ---------------------------------------------------------------------------
# Registry enforcement
# ---------------------------------------------------------------------------


def test_omitting_the_allowlist_keeps_the_legacy_registry_shape() -> None:
    default = build_registry()
    explicit_none = build_registry(tool_allowlist=None)
    empty = build_registry(tool_allowlist=frozenset())

    # Tool discovery is process-global: stub BaseTool subclasses defined by
    # other test modules join it, so the count is exact only in a single-file
    # run. This is a floor against a shrinking default registry; the equality
    # across the three constructions is the contract.
    assert len(default) >= LEGACY_DEFAULT_TOOL_COUNT
    assert explicit_none.tool_names == default.tool_names
    assert empty.tool_names == default.tool_names


def test_default_registry_keeps_the_strategy_library_tools() -> None:
    """The strategy-library surface must stay registered without an allowlist."""
    registry = build_registry()

    assert {
        "promote_strategy",
        "evaluate_strategy",
        "list_strategy_library",
        "schedule_strategy",
    } <= set(registry.tool_names)


def test_allowlist_registry_contains_exactly_the_named_tools() -> None:
    registry = build_registry(tool_allowlist=frozenset({"backtest", "get_market_data"}))

    assert set(registry.tool_names) == {"backtest", "get_market_data"}
    for excluded in ("trading_place_order", "write_file", "bash"):
        assert excluded not in registry.tool_names
    exposed = {entry["function"]["name"] for entry in registry.get_definitions()}
    assert exposed == {"backtest", "get_market_data"}
    assert callable(registry.get("backtest").execute)


def test_allowlist_honours_the_strategy_tool_set() -> None:
    """The 21-name strategy allowlist from the spec resolves unchanged."""
    from src.strategy_library.spec import ALLOWED_STRATEGY_TOOLS

    registry = build_registry(tool_allowlist=ALLOWED_STRATEGY_TOOLS)

    assert set(registry.tool_names) == set(ALLOWED_STRATEGY_TOOLS)
    forbidden_prefixes = ("trading_", "etoro_", "connector_", "mcp_")
    assert not [
        name for name in registry.tool_names if name.startswith(forbidden_prefixes)
    ]


def test_unknown_allowlist_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="nonexistent_tool"):
        build_registry(tool_allowlist=frozenset({"backtest", "nonexistent_tool"}))


def test_allowlist_cannot_request_a_tool_the_shell_policy_excludes() -> None:
    """Fail closed: an unavailable name must not be silently dropped."""
    with pytest.raises(ValueError, match="bash"):
        build_registry(tool_allowlist=frozenset({"bash"}))


def test_allowlist_is_applied_after_mcp_tools_are_appended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RemoteSearchTool(BaseTool):
        name = "mcp_demo_search"
        description = "remote search"

        def execute(self, **kwargs: object) -> str:
            del kwargs
            return "{}"

    server = SimpleNamespace(
        url="https://mcp.example.test/sse",
        enabled_tools=["*"],
        auth=None,
    )
    agent_config = SimpleNamespace(mcp_servers={"demo": server})

    monkeypatch.setattr(
        "src.tools.mcp.resolve_mcp_server_tool_name_segments",
        lambda server_names, warn_callback=None: {
            name: name for name in server_names
        },
    )
    monkeypatch.setattr(
        "src.tools.mcp.build_mcp_tool_wrappers",
        lambda server_name, server_config, local_server_name: [_RemoteSearchTool()],
    )
    monkeypatch.setattr("src.live.registry.is_live_broker", lambda *_: False)

    unlimited = build_registry(agent_config=agent_config)
    assert "mcp_demo_search" in unlimited.tool_names

    restricted = build_registry(
        agent_config=agent_config,
        tool_allowlist=frozenset({"backtest"}),
    )
    assert set(restricted.tool_names) == {"backtest"}
    assert not [name for name in restricted.tool_names if name.startswith("mcp_")]


# ---------------------------------------------------------------------------
# Session model persistence
# ---------------------------------------------------------------------------


def test_session_tool_allowlist_survives_a_json_round_trip() -> None:
    session = Session(
        session_id="allowlist001",
        title="restricted",
        tool_allowlist=["backtest", "get_market_data"],
    )

    payload = json.loads(json.dumps(session.to_dict()))

    assert payload["tool_allowlist"] == ["backtest", "get_market_data"]
    assert Session.from_dict(payload) == session


def test_legacy_session_json_without_the_key_loads_as_unrestricted() -> None:
    legacy = {
        "session_id": "legacy000001",
        "title": "older session",
        "status": "active",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "last_attempt_id": None,
        "config": {},
        "owner": None,
    }

    assert Session.from_dict(legacy).tool_allowlist is None


# ---------------------------------------------------------------------------
# SessionService end-to-end
# ---------------------------------------------------------------------------


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


class _CapturingAgentLoop:
    """AgentLoop stub that records the registry it was handed."""

    last_registry = None

    def __init__(
        self,
        *,
        registry,
        llm,
        event_callback,
        max_iterations,
        persistent_memory,
        cancel_event=None,
    ) -> None:
        del llm, event_callback, max_iterations, persistent_memory, cancel_event
        _CapturingAgentLoop.last_registry = registry

    def run(self, *, user_message: str, history, session_id: str) -> dict[str, str]:
        del user_message, history, session_id
        return {"status": "success", "content": "ok"}


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionService:
    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    return SessionService(
        store=SessionStore(tmp_path / "sessions"),
        event_bus=EventBus(),
        runs_dir=tmp_path / "runs",
    )


def _install_agent_stubs(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict],
) -> None:
    """Replace the LLM/loop and record every build_registry call's kwargs."""
    real_build_registry = tools_module.build_registry

    def _recording_build_registry(**kwargs):
        captured.append(dict(kwargs))
        return real_build_registry(**kwargs)

    monkeypatch.setattr(tools_module, "build_registry", _recording_build_registry)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.agent.loop.AgentLoop", _CapturingAgentLoop)
    monkeypatch.setattr(
        "src.config.loader.load_runtime_agent_config",
        lambda overrides=None: SimpleNamespace(mcp_servers={}),
    )
    monkeypatch.setattr(
        "src.config.loader.sanitize_session_overrides",
        lambda overrides: dict(overrides),
    )
    monkeypatch.setattr(
        "src.config.accessor.get_env_config",
        lambda: SimpleNamespace(
            agent_tuning=SimpleNamespace(vibe_trading_agent_max_iterations=4)
        ),
    )


def test_create_session_persists_a_sorted_deduplicated_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, monkeypatch)

    session = service.create_session(
        title="restricted",
        tool_allowlist=["get_market_data", "backtest", "backtest"],
    )

    raw = (tmp_path / "sessions" / session.session_id / "session.json").read_text(
        encoding="utf-8"
    )
    assert json.loads(raw)["tool_allowlist"] == ["backtest", "get_market_data"]
    assert service.store.get_session(session.session_id).tool_allowlist == [
        "backtest",
        "get_market_data",
    ]


def test_create_session_normalises_an_empty_allowlist_to_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, monkeypatch)

    session = service.create_session(tool_allowlist=[])

    assert session.tool_allowlist is None
    assert service.store.get_session(session.session_id).tool_allowlist is None


def test_send_message_enforces_the_persisted_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    _install_agent_stubs(monkeypatch, captured)
    service = _service(tmp_path, monkeypatch)
    session = service.create_session(
        title="restricted",
        tool_allowlist=["backtest", "get_market_data"],
    )

    async def scenario() -> None:
        await service.send_message(session.session_id, "run the strategy")
        await service._active_tasks[session.session_id]

    asyncio.run(scenario())

    assert len(captured) == 1
    assert captured[0]["tool_allowlist"] == frozenset(
        {"backtest", "get_market_data"}
    )
    handed_to_agent = _CapturingAgentLoop.last_registry
    assert set(handed_to_agent.tool_names) == {"backtest", "get_market_data"}
    for excluded in ("trading_place_order", "write_file", "bash", "mcp_demo_search"):
        assert excluded not in handed_to_agent.tool_names


def test_send_message_rehydrates_the_allowlist_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A memory-only allowlist must be lost: send_message reloads the session."""
    captured: list[dict] = []
    _install_agent_stubs(monkeypatch, captured)
    service = _service(tmp_path, monkeypatch)
    session = service.create_session(title="memory-only")
    session.tool_allowlist = ["backtest"]

    async def scenario() -> None:
        await service.send_message(session.session_id, "run the strategy")
        await service._active_tasks[session.session_id]

    asyncio.run(scenario())

    assert captured[0]["tool_allowlist"] is None
    # Same shared-discovery floor as the legacy-shape test above.
    assert len(_CapturingAgentLoop.last_registry) >= LEGACY_DEFAULT_TOOL_COUNT
