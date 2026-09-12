#!/usr/bin/env python3
"""T8 real-platform IM smoke (钉钉 or 飞书) — USER-GATED, tenant TEST bot only.

Pending step of plan T8 (真平台冒烟). No explicitly-test bot credentials were
found in the environment at authoring time (searched: ``OpencodeAgent/
.env.example`` — only a placeholder notification webhook; the user's
``~/.vibe-trading/.env`` / checkout ``agent/.env`` — no DINGTALK_*/FEISHU_*
channel credentials at all), and production credentials must NOT be used for
smokes (task rule). This script is therefore parameterized EXCLUSIVELY by the
env vars below and refuses to run without them; it never reads any config
file for credentials.

Required env (钉钉 Stream-mode TEST bot)::

    VT_T8_PLATFORM=dingtalk
    VT_T8_DINGTALK_CLIENT_ID=...        # TEST bot app key
    VT_T8_DINGTALK_CLIENT_SECRET=...    # TEST bot app secret
    VT_T8_ALLOW_FROM=<your sender id>   # comma-separated; defaults to *

…or (飞书 TEST bot)::

    VT_T8_PLATFORM=feishu
    VT_T8_FEISHU_APP_ID=...
    VT_T8_FEISHU_APP_SECRET=...
    VT_T8_ALLOW_FROM=<your open_id>

Also required: a running T8 rig (``run_t8_im_parity.py`` step 1, or
``start_rig.py --rig-root /tmp/vt-t8-rig``) for the opencode serve URL;
override with ``ENGINE_BRIDGE_E2E_RIG_STATE``. Optional: ``VT_T8_WAIT_S``
(default 600), ``VT_T8_EVIDENCE`` (default the t8-im evidence dir).

Flow: the SAME production wiring as the parity suite (real adapter loaded by
the ChannelManager, real ChannelRuntime, real bridge service on the live
serve), then a human sends the TEST bot DM ``reply with exactly SMOKE_OK``;
the script asserts the round trip (final reply persisted with the D6 metadata
enumeration and delivered through the adapter's real ``send`` — DingTalk
renders it as a sampleMarkdown card, dingtalk.py:672-680 — and
``send_with_receipt`` returns a receipt for a closing marker), prices +
DELETEs its engine sessions, and writes ``real-smoke-results.json`` into the
evidence dir. Adapter SDKs are lazy extras: a missing SDK exits with the
registry's install hint (``pip install 'vibe-trading-ai[dingtalk]'``).

NEVER sends to real channels/groups beyond the DM/group you message in; the
bot only replies where it is addressed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from tests.e2e_engine_bridge import stop_rig  # noqa: E402
from tests.e2e_engine_bridge.imlib import (  # noqa: E402
    EngineSessionRegistry,
    build_bridge_service,
    write_json,
)

RIG_STATE_PATH = Path(
    os.environ.get("ENGINE_BRIDGE_E2E_RIG_STATE", "/tmp/vt-t8-rig/rig_state.json")
)
EVIDENCE_DIR = Path(
    os.environ.get(
        "VT_T8_EVIDENCE",
        str(REPO_ROOT / ".omo" / "evidence" / "opencode-engine-bridge-v2" / "t8-im"),
    )
)
WAIT_S = float(os.environ.get("VT_T8_WAIT_S", "600"))


def _platform_config() -> tuple[str, Dict[str, Any]]:
    platform = os.environ.get("VT_T8_PLATFORM", "").strip().lower()
    allow_from = [
        item.strip()
        for item in os.environ.get("VT_T8_ALLOW_FROM", "*").split(",")
        if item.strip()
    ]
    if platform == "dingtalk":
        client_id = os.environ.get("VT_T8_DINGTALK_CLIENT_ID", "")
        client_secret = os.environ.get("VT_T8_DINGTALK_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise SystemExit(
                "VT_T8_DINGTALK_CLIENT_ID / VT_T8_DINGTALK_CLIENT_SECRET are "
                "required (tenant TEST bot only — production credentials are "
                "forbidden for smokes)"
            )
        return platform, {
            "enabled": True,
            "client_id": client_id,
            "client_secret": client_secret,
            "allow_from": allow_from,
        }
    if platform == "feishu":
        app_id = os.environ.get("VT_T8_FEISHU_APP_ID", "")
        app_secret = os.environ.get("VT_T8_FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            raise SystemExit(
                "VT_T8_FEISHU_APP_ID / VT_T8_FEISHU_APP_SECRET are required "
                "(tenant TEST bot only)"
            )
        return platform, {
            "enabled": True,
            "app_id": app_id,
            "app_secret": app_secret,
            "allow_from": allow_from,
            "streaming": False,
        }
    raise SystemExit("VT_T8_PLATFORM must be 'dingtalk' or 'feishu'")


async def _scenario() -> Dict[str, Any]:
    from src.channels.bus.events import OutboundMessage
    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime

    platform, section = _platform_config()
    rig_state = json.loads(RIG_STATE_PATH.read_text(encoding="utf-8"))
    out: Dict[str, Any] = {"platform": platform, "started_at": time.time()}
    store_dir = Path(rig_state["rig_root"]) / "im-stores" / f"real-{platform}"

    service, store, _driver = await build_bridge_service(
        rig_state["serve_url"], store_dir
    )
    registry = EngineSessionRegistry(EVIDENCE_DIR / "engine-sessions.jsonl")
    bus = MessageBus()
    manager = ChannelManager({platform: section}, bus, session_service=service)
    runtime = None
    try:
        status = manager.get_status().get(platform, {})
        out["adapter_status"] = status
        if not status.get("loaded"):
            raise SystemExit(
                f"{platform} adapter unavailable: {status.get('error')} "
                f"({status.get('install_hint')})"
            )
        runtime = ChannelRuntime(
            bus=bus,
            session_service=service,
            manager=manager,
            session_map_path=store_dir.parent / f"real-{platform}-map.json",
            reply_timeout_s=600.0,
            poll_interval_s=0.25,
        )
        await runtime.start(start_manager=True)
        print(
            f"\n[t8-real] {platform} adapter running. From an ALLOWED account, "
            "send the TEST bot DM:\n\n    reply with exactly SMOKE_OK\n\n"
            f"[t8-real] waiting up to {WAIT_S:.0f}s for the round trip..."
        )

        reply_seen = None
        deadline = time.time() + WAIT_S
        while time.time() < deadline and reply_seen is None:
            for sess in service.list_sessions(limit=50):
                for message in service.get_messages(sess.session_id, limit=50):
                    if message.role == "assistant" and "SMOKE_OK" in (
                        message.content or ""
                    ):
                        reply_seen = {
                            "session_id": sess.session_id,
                            "content": message.content,
                            "metadata": dict(message.metadata or {}),
                        }
                        break
                if reply_seen:
                    break
            await asyncio.sleep(2.0)
        out["round_trip"] = reply_seen
        if reply_seen is None:
            out["verdict"] = "FAIL: no assistant reply within the window"
            return out

        session = service.get_session(reply_seen["session_id"])
        chat_id = str((session.config or {}).get("channel_chat_id", ""))
        adapter = manager.get_channel(platform)
        assert adapter is not None
        receipt = await adapter.send_with_receipt(
            OutboundMessage(
                channel=platform,
                chat_id=chat_id,
                content="[t8-real] smoke complete — receipt marker",
            )
        )
        out["receipt"] = {
            "status": receipt.status,
            "provider_message_id": receipt.provider_message_id,
            "sent_at": receipt.sent_at,
        }
        out["verdict"] = "PASS" if receipt.status in ("accepted", "sent") else "FAIL"
        return out
    finally:
        if runtime is not None:
            await runtime.stop()
        for vt_sid, engine_sid in service._engine_sessions.items():
            registry.record(f"real-{platform}", vt_sid, engine_sid)
        await service.aclose()
        serve_url = rig_state["serve_url"]
        for engine_sid in service._engine_sessions.values():
            cost = stop_rig._session_cost(serve_url, engine_sid)
            code, _ = stop_rig._req(serve_url, "DELETE", f"/session/{engine_sid}")
            print(f"[t8-real] engine session {engine_sid}: cost=${cost} DELETE->{code}")


def main() -> int:
    if not RIG_STATE_PATH.exists():
        raise SystemExit(
            f"no rig state at {RIG_STATE_PATH} — start the T8 rig first "
            "(run_t8_im_parity.py or start_rig.py --rig-root /tmp/vt-t8-rig)"
        )
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(_scenario())
    result["finished_at"] = time.time()
    path = write_json(EVIDENCE_DIR / "real-smoke-results.json", result)
    print(f"[t8-real] verdict: {result.get('verdict')} -> {path}")
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
