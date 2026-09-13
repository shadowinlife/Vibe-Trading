#!/usr/bin/env python3
"""In-container IM probe for the T12 tenancy matrix (plan T12 item 5).

Piped into ONE tenant container via ``docker exec -i <c> /opt/venv/bin/python3 -``
by ``e2e_im_checks.py``; it is self-contained on purpose (only ``src.*`` from
the container's editable install + stdlib + httpx, all present in the T10
image). It assembles the SAME production IM wiring the gateway hosts
(``api/state.py`` + ``opencode_bridge.wiring`` + ``channels.runtime``), with
the T8 ``imlib.MockChannel`` pattern as the adapter:

* bridge service via the production factory ``build_session_service``
  (``OpencodeDriver.from_env`` — the container env carries
  ``OPENCODE_BASE_URL``/``OPENCODE_SERVER_PASSWORD``) + ``start_session_service``
  (tool map -> subscribe-first pumps -> reconcile);
* the REAL ``MessageBus`` / ``ChannelManager`` / ``ChannelRuntime`` (unchanged
  ``src/channels`` code), registered into ``src.api.state`` so the T9
  ``ImStreamProducer`` resolves its plumbing exactly like the gateway does;
* one inbound message injected through ``BaseChannel._handle_message`` (the
  ingress all 16 production adapters use) with a tenant-distinct
  ``{channel, chat_id}`` pair.

Sessions land in the tenant's REAL store (``SESSIONS_DIR`` under the home
volume), so the host can cross-check them through the tenant's own gateway
REST surface afterwards. The probe never touches another tenant: it cannot —
serve is loopback-bound inside this container (T10 port plan).

Configuration (env, set by ``docker exec -e``):
    VT_T12_TENANT    tenant id (evidence label only)
    VT_T12_CHAT_ID   this tenant's distinct mock chat id
    VT_T12_PROMPT    the minimal prompt (cost discipline: one short turn)
    VT_T12_WAIT_S    terminal-reply budget (default 300)
    VT_T12_STREAMING "1" (default) enables the T9 delta path; "0" terminal-only
    VT_T12_DRY       "1" builds+starts the stack but injects nothing (zero
                     model cost — wiring de-risk before spending a real turn)

Output: one sentinel line ``__T12_PROBE_RESULT__{...json...}`` on stdout.
Exit code 0 on a completed round trip, 1 otherwise (the JSON is printed
either way — the host parses the sentinel, not the exit code).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

RESULT_SENTINEL = "__T12_PROBE_RESULT__"
MOCK_CHANNEL_NAME = "mockim"  # T8 convention: never a real platform name


class ProbeMockChannel:
    """Defined at runtime (see ``_build_stack``) — placeholder for docs."""


async def _run() -> dict[str, Any]:
    tenant = os.environ.get("VT_T12_TENANT", "?")
    chat_id = os.environ["VT_T12_CHAT_ID"]
    prompt = os.environ.get(
        "VT_T12_PROMPT",
        "Reply with exactly the single word OK. Do not call any tool, "
        "do not iterate, do not spawn subagents.",
    )
    wait_s = float(os.environ.get("VT_T12_WAIT_S", "300"))
    streaming = os.environ.get("VT_T12_STREAMING", "1") == "1"
    sender = os.environ.get("VT_T12_SENDER", "t12-user")
    # Dry mode: build + start the stack, inject NOTHING (zero model cost) —
    # de-risks imports/wiring/reconcile before spending a real turn.
    dry = os.environ.get("VT_T12_DRY", "0") == "1"

    from src.api import state as api_state
    from src.api.helpers import RUNS_DIR, SESSIONS_DIR
    from src.channels.base import BaseChannel
    from src.channels.bus.events import OutboundMessage
    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime
    from src.opencode_bridge.wiring import build_session_service, start_session_service
    from src.session.events import EventBus
    from src.session.store import SessionStore

    class MockChannel(BaseChannel):
        """T8 imlib MockChannel + send_delta recording (T9 streaming path)."""

        name = MOCK_CHANNEL_NAME
        display_name = "MockIM (T12 tenancy probe)"

        def __init__(self, config: Any, bus: MessageBus) -> None:
            super().__init__(config, bus)
            self.sent: list[dict[str, Any]] = []
            self._queues: dict[str, asyncio.Queue] = {}

        async def start(self) -> None:
            self._running = True
            while self._running:
                await asyncio.sleep(0.05)

        async def stop(self) -> None:
            self._running = False

        def _record(
            self,
            kind: str,
            msg_chat: str,
            channel: str,
            content: str,
            metadata: dict[str, Any] | None,
        ) -> None:
            self.sent.append(
                {
                    "kind": kind,
                    "chat_id": msg_chat,
                    "channel": channel,
                    "t": time.time(),
                    "content_len": len(content or ""),
                    "content_head": (content or "")[:120],
                    "meta_flags": sorted(
                        key
                        for key, on in (metadata or {}).items()
                        if on and key.startswith("_")
                    ),
                    "session_id": (metadata or {}).get("session_id"),
                }
            )

        async def send(self, msg: OutboundMessage) -> None:
            self._record("final", msg.chat_id, msg.channel, msg.content, msg.metadata)
            queue = self._queues.setdefault(msg.chat_id, asyncio.Queue())
            await queue.put(msg)

        async def send_delta(
            self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None
        ) -> None:
            kind = "stream_end" if (metadata or {}).get("_stream_end") else "delta"
            self._record(kind, chat_id, MOCK_CHANNEL_NAME, delta, metadata)

    store = SessionStore(base_dir=SESSIONS_DIR)
    event_bus = EventBus()
    event_bus.set_loop(asyncio.get_running_loop())
    service = build_session_service(store, event_bus, RUNS_DIR)

    bus = MessageBus()
    mock = MockChannel(
        {"enabled": True, "allow_from": ["*"], "streaming": streaming}, bus
    )
    manager = ChannelManager({}, bus, session_service=service)
    manager.channels[MOCK_CHANNEL_NAME] = mock
    # The T9 ImStreamProducer resolves (bus, manager) from src.api.state —
    # the gateway sets these when its channel runtime is built; mirror that.
    api_state._channel_bus = bus
    api_state._channel_manager = manager

    runtime = ChannelRuntime(
        bus=bus,
        session_service=service,
        manager=manager,
        session_map_path=Path("/tmp/t12-im-session-map.json"),
        reply_timeout_s=600.0,
        poll_interval_s=0.25,
        operators=["t12-operator"],
    )

    result: dict[str, Any] = {
        "tenant": tenant,
        "chat_id": chat_id,
        "channel": MOCK_CHANNEL_NAME,
        "streaming": streaming,
        "store_dir": str(SESSIONS_DIR),
        "started_at": time.time(),
    }
    try:
        await start_session_service(service)
        await runtime.start(start_manager=True)
        result["stack_started"] = True
        if dry:
            result["dry_run"] = True
            result["round_trip"] = None
            result["store_sessions"] = sorted(
                path.name for path in Path(SESSIONS_DIR).iterdir() if path.is_dir()
            )
            return result

        t_inject = time.time()
        await mock._handle_message(
            sender_id=sender,
            chat_id=chat_id,
            content=prompt,
            metadata={"message_id": f"t12-{time.time_ns()}"},
            is_dm=True,
        )
        result["injected_at"] = t_inject

        first_outbound_at: float | None = None
        first_outbound_kind: str | None = None
        terminal_at: float | None = None
        deadline = t_inject + wait_s
        while time.time() < deadline:
            for record in mock.sent:
                if first_outbound_at is None:
                    first_outbound_at = record["t"]
                    first_outbound_kind = record["kind"]
                is_terminal_final = record["kind"] == "final" and (
                    "_channel_runtime" in record["meta_flags"]
                    or "error" in record["meta_flags"]
                )
                is_terminal_end = record["kind"] == "stream_end"
                if (is_terminal_final or is_terminal_end) and terminal_at is None:
                    terminal_at = record["t"]
            if terminal_at is not None:
                # Let a trailing final-after-stream_end land (bounded).
                await asyncio.sleep(2.0)
                break
            await asyncio.sleep(0.1)

        result["first_outbound_at"] = first_outbound_at
        result["first_outbound_kind"] = first_outbound_kind
        result["terminal_at"] = terminal_at
        result["im_first_response_s"] = (
            round(first_outbound_at - t_inject, 3) if first_outbound_at else None
        )
        result["im_terminal_s"] = (
            round(terminal_at - t_inject, 3) if terminal_at else None
        )
        result["sent"] = [
            {**record, "t_rel": round(record["t"] - t_inject, 3)}
            for record in mock.sent
        ]
        result["session_map"] = dict(runtime._session_map)
        result["engine_sessions"] = dict(service._engine_sessions)
        result["store_sessions"] = sorted(
            path.name for path in Path(SESSIONS_DIR).iterdir() if path.is_dir()
        )
        result["round_trip"] = terminal_at is not None
        result["cost_usd"] = await _engine_cost(service._engine_sessions)
    except Exception as exc:  # noqa: BLE001 — the probe reports, the host judges
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["round_trip"] = False
    finally:
        try:
            await runtime.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            await service.aclose()
        except Exception:  # noqa: BLE001
            pass
        result["finished_at"] = time.time()
    return result


async def _engine_cost(engine_sessions: dict[str, str]) -> float:
    """Sum the serve-side assistant cost of this probe's engine sessions."""
    import httpx

    base = os.environ.get("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    auth = ("opencode", password) if password else None
    total = 0.0
    async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:
        for engine_sid in engine_sessions.values():
            try:
                response = await client.get(f"{base}/session/{engine_sid}/message")
                messages = response.json() if response.status_code == 200 else []
            except Exception:  # noqa: BLE001 — cost is best-effort evidence
                continue
            for entry in messages if isinstance(messages, list) else []:
                info = entry.get("info") if isinstance(entry, dict) else None
                cost = info.get("cost") if isinstance(info, dict) else None
                if isinstance(cost, (int, float)):
                    total += float(cost)
    return round(total, 6)


def main() -> int:
    result = asyncio.run(_run())
    print(RESULT_SENTINEL + json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("round_trip") else 1


if __name__ == "__main__":
    raise SystemExit(main())
