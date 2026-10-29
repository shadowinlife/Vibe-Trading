"""Tests for the memory MCP adapter (src.memory.mcp_adapter) and the
mcp_server registration gating of the memory lifecycle tools.

EnvConfig singleton hygiene is handled by the shared autouse
``_reset_env_config`` fixture in ``agent/tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.memory.lifecycle import MemoryLifecycle
from src.memory.mcp_adapter import MemoryMCPAdapter
from src.memory.persistent import PersistentMemory

AGENT_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture()
def adapter(tmp_path: Path) -> MemoryMCPAdapter:
    """Adapter wired to a tmp_path-backed memory store."""
    memory = PersistentMemory(memory_dir=tmp_path)
    return MemoryMCPAdapter(memory=memory, lifecycle=MemoryLifecycle(memory))


class _BrokenMemory:
    """Stub whose every relevant method raises, to exercise never-raise."""

    def add(self, *args, **kwargs):
        raise RuntimeError("boom-add")

    def find_relevant(self, *args, **kwargs):
        raise RuntimeError("boom-recall")

    def list_entries(self):
        raise RuntimeError("boom-list")


class TestMemorySave:
    def test_save_ok_envelope(self, adapter: MemoryMCPAdapter, tmp_path: Path) -> None:
        result = adapter.memory_save("note one", "a summary", "body text")
        assert result["status"] == "ok"
        assert result["saved"].endswith(".md")
        assert (tmp_path / result["saved"]).exists()

    def test_duplicate_save_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VT_MEMORY_QUALITY", "1")
        memory = PersistentMemory(memory_dir=tmp_path)
        adapter = MemoryMCPAdapter(memory=memory, lifecycle=MemoryLifecycle(memory))
        assert adapter.memory_save("dup", "desc", "same body")["status"] == "ok"
        second = adapter.memory_save("dup", "desc", "same body")
        assert second == {"status": "skipped", "reason": "duplicate"}

    def test_invalid_type_returns_error_envelope(
        self, adapter: MemoryMCPAdapter
    ) -> None:
        # PersistentMemory.add raises ValueError for unknown memory types;
        # the adapter must convert it into an error envelope.
        result = adapter.memory_save("bad", "desc", "body", memory_type="nope")
        assert result["status"] == "error"
        assert "memory_type must be one of" in result["error"]


class TestMemoryRecall:
    def test_recall_delegates_and_shapes_results(
        self, adapter: MemoryMCPAdapter
    ) -> None:
        adapter.memory_save(
            "momentum research", "summary", "momentum lookback insights " * 30
        )
        result = adapter.memory_recall("momentum lookback")
        assert result["status"] == "ok"
        assert len(result["results"]) == 1
        item = result["results"][0]
        assert set(item) == {
            "title",
            "type",
            "snippet",
            "quality_score",
            "importance",
        }
        assert item["title"] == "momentum research"
        assert len(item["snippet"]) <= 500

    def test_type_filter_applied_client_side(self, adapter: MemoryMCPAdapter) -> None:
        adapter.memory_save("proj note", "d", "shared keyword alpha", "project")
        adapter.memory_save("user note", "d", "shared keyword alpha", "user")
        result = adapter.memory_recall("shared keyword alpha", type_filter="user")
        assert result["status"] == "ok"
        assert [r["type"] for r in result["results"]] == ["user"]

    def test_track_access_failure_does_not_break_recall(
        self, adapter: MemoryMCPAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter.memory_save("tracked", "d", "trackable keyword body " * 20)

        def _boom(entry):
            raise RuntimeError("track boom")

        monkeypatch.setattr(adapter._lifecycle, "track_access", _boom)
        result = adapter.memory_recall("trackable keyword")
        assert result["status"] == "ok"
        assert len(result["results"]) == 1


class TestMemoryReinforce:
    def test_unknown_event_lists_valid_events(self, adapter: MemoryMCPAdapter) -> None:
        result = adapter.memory_reinforce("x", "not_an_event")
        assert result["status"] == "error"
        assert "not_an_event" in result["error"]
        assert result["valid_events"] == [
            "passive_decay",
            "task_failure",
            "task_success",
            "user_confirm",
            "user_reject",
        ]

    def test_reinforce_ok_when_quality_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VT_MEMORY_QUALITY", "1")
        memory = PersistentMemory(memory_dir=tmp_path)
        adapter = MemoryMCPAdapter(memory=memory, lifecycle=MemoryLifecycle(memory))
        adapter.memory_save("boost me", "d", "content body")
        result = adapter.memory_reinforce("boost me", "task_success", source="user")
        assert result == {"status": "ok", "name": "boost me", "event": "task_success"}

    def test_reinforce_skipped_when_quality_disabled(
        self, adapter: MemoryMCPAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VT_MEMORY_QUALITY", raising=False)
        monkeypatch.delenv("VT_MEMORY", raising=False)
        result = adapter.memory_reinforce("anything", "task_success")
        assert result == {"status": "skipped", "reason": "not reinforced"}


class TestMemoryReflect:
    def test_flag_off_returns_skipped_with_hint(
        self, adapter: MemoryMCPAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VT_MEMORY_REFLECTIONS", raising=False)
        monkeypatch.delenv("VT_MEMORY", raising=False)
        result = adapter.memory_reflect("momentum", {"sharpe": 1.0}, {"lookback": 20})
        assert result["status"] == "skipped"
        assert "VT_MEMORY_REFLECTIONS" in result["reason"]

    def test_flag_on_saves_lesson(
        self, adapter: MemoryMCPAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Redirect the default reflections dir (Path.home based) to tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("VT_MEMORY_REFLECTIONS", "1")
        result = adapter.memory_reflect("momentum", {"sharpe": 1.2}, {"lookback": 20})
        assert result["status"] == "ok"
        assert result["lesson_id"].startswith("lesson_")
        store = tmp_path / ".vibe-trading" / "memory" / "reflections"
        assert (store / "momentum.jsonl").exists()


class TestMemoryStatus:
    def test_status_stats(self, adapter: MemoryMCPAdapter) -> None:
        adapter.memory_save("one", "d", "body one")
        adapter.memory_save("two", "d", "body two")
        result = adapter.memory_status()
        assert result["status"] == "ok"
        assert result["entry_count"] == 2
        assert 0.0 <= result["avg_quality"] <= 1.0
        assert 0.0 <= result["avg_importance"] <= 1.0
        assert isinstance(result["gc_pending"], int)

    def test_status_guards_each_stat(self) -> None:
        adapter = MemoryMCPAdapter(
            memory=_BrokenMemory(), lifecycle=object()  # type: ignore[arg-type]
        )
        result = adapter.memory_status()
        assert result["status"] == "ok"
        assert result["entry_count"] == 0
        assert result["gc_pending"] == 0


class TestNeverRaise:
    """Every adapter method converts internal exceptions to error envelopes."""

    def test_all_methods_error_envelope_on_internal_failure(self) -> None:
        adapter = MemoryMCPAdapter(
            memory=_BrokenMemory(), lifecycle=object()  # type: ignore[arg-type]
        )
        assert adapter.memory_save("n", "d", "c")["status"] == "error"
        assert adapter.memory_recall("q")["status"] == "error"
        assert adapter.memory_reinforce("n", "task_success")["status"] == "error"
        # memory_status guards per-stat and stays ok even on broken backends.
        assert adapter.memory_status()["status"] == "ok"


# ---------------------------------------------------------------------------
# mcp_server registration gating (VT_MEMORY_MCP_TOOLS)
# ---------------------------------------------------------------------------

_MEMORY_TOOL_NAMES = {
    "memory_save",
    "memory_recall",
    "memory_reinforce",
    "memory_reflect",
    "memory_status",
}


def _import_mcp_server():
    """Import agent/mcp_server.py without executing main()."""
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    if "mcp_server" in sys.modules:
        return sys.modules["mcp_server"]
    return importlib.import_module("mcp_server")


def test_env_memory_tools_flag_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _import_mcp_server()
    monkeypatch.delenv("VT_MEMORY_MCP_TOOLS", raising=False)
    assert mod._env_memory_tools_enabled() is False

    from src.config.accessor import reset_env_config

    monkeypatch.setenv("VT_MEMORY_MCP_TOOLS", "1")
    reset_env_config()
    assert mod._env_memory_tools_enabled() is True


def test_memory_tools_absent_by_default() -> None:
    """With the flag unset, memory tools must not appear in tools/list."""
    mod = _import_mcp_server()
    tools = asyncio.run(mod.mcp.list_tools())
    registered = {t.name for t in tools}
    assert _MEMORY_TOOL_NAMES & registered == set()


@pytest.mark.parametrize("flag_on", [True, False])
def test_registration_gating_in_subprocess(flag_on: bool) -> None:
    """Importing mcp_server with the flag on/off gates tool registration.

    Runs in a subprocess because module-level registration happens exactly
    once per interpreter and must not pollute the in-process FastMCP.
    """
    script = (
        "import asyncio, json, sys\n"
        f"sys.path.insert(0, {str(AGENT_DIR)!r})\n"
        "import mcp_server\n"
        "tools = asyncio.run(mcp_server.mcp.list_tools())\n"
        "print(json.dumps(sorted(t.name for t in tools)))\n"
    )
    env = dict(os.environ)
    env.pop("VT_MEMORY", None)
    if flag_on:
        env["VT_MEMORY_MCP_TOOLS"] = "1"
    else:
        env.pop("VT_MEMORY_MCP_TOOLS", None)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=str(AGENT_DIR),
    )
    assert proc.returncode == 0, proc.stderr
    registered = set(json.loads(proc.stdout.strip().splitlines()[-1]))
    if flag_on:
        assert _MEMORY_TOOL_NAMES <= registered
    else:
        assert _MEMORY_TOOL_NAMES & registered == set()
