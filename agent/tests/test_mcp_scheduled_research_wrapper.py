"""T13 — the scheduled_research MCP wrapper contract.

Locks the three cross-module contracts the wrapper exists to satisfy:

* the 200-char preview constraint (plan D5 + the IM/Web relay regex
  ``sessions_routes._SCHEDULED_PROPOSAL_ID_RE``): a propose_* result must
  carry ``"proposal_id": "srp_..."`` inside its first 200 characters, or the
  confirmation card / IM confirm flow can never see the id;
* session binding: the proposal binds to the session id the wrapper resolved
  (explicit id wins — the ``_resolve_session_id`` fallback chain is FROZEN,
  mcp_server.py:350-389), because ``runtime.py``'s confirm intercept looks
  the proposal up by the vt session id (``latest_pending_for_session``);
* dual-surface description sync (repo AGENTS.md convention): the MCP
  description must carry the tool class's description verbatim, so the agent
  surface and the MCP surface never drift apart silently.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import mcp_server
from src.api.sessions_routes import _SCHEDULED_PROPOSAL_ID_RE
from src.scheduled_research.proposals import latest_pending_for_session
from src.tools.scheduled_research_tool import ScheduledResearchTool

PREVIEW_LEN = 200  # plan D5: preview = state.output[:200]


class _ToolRegistryStub:
    """Execute against the REAL tool class; record the forwarded params.

    Mirrors ``ToolRegistry.execute``'s guarantee (src/agent/tools.py): a tool
    failure becomes a JSON error envelope, never a raised exception.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tool = ScheduledResearchTool()

    def execute(self, name: str, params: dict[str, Any]) -> str:
        self.calls.append((name, params))
        try:
            return self._tool.execute(**params)
        except Exception as exc:  # noqa: BLE001 - registry contract
            return json.dumps(
                {"status": "error", "tool": name, "error": str(exc)},
                ensure_ascii=False,
            )


@pytest.fixture()
def registry(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    stub = _ToolRegistryStub()
    monkeypatch.setattr(mcp_server, "_get_registry", lambda: stub)
    return stub


def _draft() -> dict[str, Any]:
    return {
        "title": "Morning scan",
        "source": {"kind": "prompt", "prompt": "Summarize the market."},
        "schedule": {"expression": "0 8 * * *", "timezone": "Asia/Shanghai"},
        "end_at": None,
        "delivery": {"mode": "in_app"},
    }


def _mcp_tool():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    return next(t for t in tools if t.name == "scheduled_research")


def test_wrapper_is_registered_with_its_call_schema() -> None:
    tool = _mcp_tool()
    props = (tool.parameters or {}).get("properties", {})
    assert {"action", "job_id", "draft", "session_id"} <= set(props)
    assert (tool.parameters or {}).get("required") == ["action"]


def test_propose_create_puts_proposal_id_inside_the_200_char_preview(
    registry,
) -> None:
    """Golden: the relay regex must hit inside output[:200] (D5 preview)."""
    out = mcp_server.scheduled_research(
        action="propose_create", draft=_draft(), session_id="s-golden"
    )
    preview = out[:PREVIEW_LEN]
    match = _SCHEDULED_PROPOSAL_ID_RE.search(preview)
    assert match, f"no proposal_id in preview: {preview!r}"
    payload = json.loads(out)
    assert payload["type"] == "scheduled_research.proposal"
    assert payload["proposal_id"] == match.group(1)
    assert payload["status"] == "pending"


def test_proposal_binds_the_explicit_session_id(registry) -> None:
    """The confirm intercept finds the proposal by the vt session id."""
    out = mcp_server.scheduled_research(
        action="propose_create", draft=_draft(), session_id="s-im-chat"
    )
    proposal_id = json.loads(out)["proposal_id"]
    proposal = latest_pending_for_session("s-im-chat")
    assert proposal is not None
    assert proposal["proposal_id"] == proposal_id
    assert latest_pending_for_session("some-other-session") is None


def test_session_fallback_never_binds_an_empty_id(registry, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_mcp_session_id", None)
    out = mcp_server.scheduled_research(action="propose_create", draft=_draft())
    proposal_id = json.loads(out)["proposal_id"]
    forwarded = registry.calls[-1][1]
    assert forwarded["session_id"].strip() != ""
    assert (
        latest_pending_for_session(forwarded["session_id"])["proposal_id"]
        == proposal_id
    )


def test_read_actions_forward_params_and_drop_blanks(registry) -> None:
    mcp_server.scheduled_research(action="get_job", job_id="  job-1  ")
    name, params = registry.calls[-1]
    assert name == "scheduled_research"
    assert params["action"] == "get_job"
    assert params["job_id"] == "job-1"
    assert "draft" not in params

    mcp_server.scheduled_research(action="status", job_id="")
    _, params = registry.calls[-1]
    assert params["action"] == "status"
    assert "job_id" not in params


def test_unsupported_action_returns_the_error_envelope_not_a_raise(
    registry,
) -> None:
    out = mcp_server.scheduled_research(action="launch_missiles")
    payload = json.loads(out)
    assert payload["status"] == "error"
    assert "launch_missiles" in payload["error"]


def test_mcp_description_carries_the_tool_class_description_verbatim() -> None:
    """Dual-surface sync (AGENTS.md): one description, two surfaces."""
    normalize = lambda text: " ".join(text.split())  # noqa: E731
    mcp_description = normalize(_mcp_tool().description or "")
    class_description = normalize(ScheduledResearchTool.description)
    assert mcp_description.startswith(class_description), (
        "the MCP wrapper docstring and ScheduledResearchTool.description "
        "drifted apart — sync both surfaces (repo AGENTS.md convention)"
    )


def test_wrapper_proposal_commits_through_the_im_confirm_intercept(
    registry, tmp_path, monkeypatch
) -> None:
    """The chain the IM confirm flow rides, minus the model (deterministic).

    Wrapper propose_create (session-bound) -> proposal on disk -> the
    UNCHANGED ``ChannelRuntime._handle_scheduled_confirmation`` intercept
    (runtime.py:271-320, channels/ zero-diff) commits it on an exact "确认"
    reply -> the job record exists. Mirrors the native-engine committed test
    (test_scheduled_research_confirmation.py) with the MCP wrapper as the
    proposal source instead of the agent-side tool.
    """
    from src.channels.bus.events import InboundMessage
    from src.channels.bus.queue import MessageBus
    from src.channels.runtime import ChannelRuntime
    from src.scheduled_research import proposals
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    monkeypatch.setattr(proposals, "default_store", lambda: store)
    monkeypatch.setattr(
        proposals,
        "scheduler_status",
        lambda: {"enabled": True, "running": True, "executable": True},
    )

    out = mcp_server.scheduled_research(
        action="propose_create", draft=_draft(), session_id="s-im-chain"
    )
    proposal_id = json.loads(out)["proposal_id"]
    assert store.load() == {}  # propose never touches the job store

    bus = MessageBus()
    runtime = ChannelRuntime(
        bus=bus,
        session_service=None,
        manager=None,
        session_map_path=tmp_path / "channel_sessions.json",
    )
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="c", content="确认"
    )

    async def scenario() -> bool:
        return await runtime._handle_scheduled_confirmation(msg, "s-im-chain")

    assert asyncio.run(scenario()) is True

    jobs = store.load()
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job.title == "Morning scan"

    committed = proposals._read(proposal_id)
    assert committed["status"] == "committed"
    assert committed["committed_job_id"] == job.id

    sent = bus.outbound.get_nowait()
    assert sent.metadata.get("scheduled_research_confirmation") is True
    assert job.id in sent.content
