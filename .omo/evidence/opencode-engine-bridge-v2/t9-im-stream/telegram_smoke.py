#!/usr/bin/env python3
"""T9 real-platform (Telegram-style edit) streaming smoke — READY-TO-RUN.

Status: PENDING — requires an explicitly-TEST bot token (plan T9: 真平台证据
仅用测试 bot；production credentials and real channels/groups are NEVER
used). Everything else (rig, in-process stack, assertions) is in place.

What it does: drives ONE real turn through the REAL, UNCHANGED
``src/channels/telegram.py`` adapter (progressive ``edit_message_text``
edits — the reference ``send_delta`` implementation) against the live rig
serve, with the T9 ``ImStreamProducer`` attached exactly as
``wiring.build_session_service`` does. The tester watches the single
Telegram message edit itself into shape; the script asserts the adapter-side
contract mechanically and writes ``telegram-smoke-results.json``.

Prerequisites:
  1. A TEST bot via @BotFather (throwaway; never the production token).
  2. The tester's numeric chat id with that bot (send it /start first;
     resolve via https://api.telegram.org/bot<TOKEN>/getUpdates).
  3. The T9 rig up: python3 agent/tests/e2e_engine_bridge/start_rig.py \
         --rig-root /tmp/vt-t9-rig --serve-port 14097 --gateway-port 18081

Usage:
    TELEGRAM_TEST_BOT_TOKEN=... TELEGRAM_TEST_CHAT_ID=... \
    TELEGRAM_TEST_SENDER_ID=... \
        python3 telegram_smoke.py [--rig-root /tmp/vt-t9-rig]

Safety rails (fail-closed):
  * token/chat/sender come ONLY from env/CLI — the user's real agent.json is
    never read;
  * allow_from is pinned to the single tester id (no wildcard);
  * one turn, one short prompt; the engine session is DELETEd afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_RIG_ROOT = Path(os.environ.get("T9_RIG_ROOT", "/tmp/vt-t9-rig"))
os.environ.setdefault("VIBE_TRADING_HOME", str(DEFAULT_RIG_ROOT / "vt-home"))

REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from tests.e2e_engine_bridge import imlib, riglib  # noqa: E402

PROMPT = (
    "Reply with exactly three short sentences about progressive editing. "
    "Do not use any tools. Do not iterate. No todo list."
)


async def main_async(rig_root: Path, turn_timeout: float) -> int:
    token = os.environ.get("TELEGRAM_TEST_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_TEST_CHAT_ID", "").strip()
    sender_id = os.environ.get("TELEGRAM_TEST_SENDER_ID", "").strip()
    if not token or not chat_id or not sender_id:
        print(
            "PENDING: set TELEGRAM_TEST_BOT_TOKEN / TELEGRAM_TEST_CHAT_ID / "
            "TELEGRAM_TEST_SENDER_ID (TEST bot only — see module docstring)."
        )
        return 2

    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime
    from src.channels.telegram import TelegramChannel
    from src.opencode_bridge.im_stream import ImStreamProducer

    rig_state = json.loads((rig_root / "rig_state.json").read_text(encoding="utf-8"))
    serve_url = rig_state["serve_url"]

    service, store, driver = await imlib.build_bridge_service(
        serve_url, rig_root / "tg-smoke-store" / "sessions"
    )
    bus = MessageBus()
    telegram = TelegramChannel(
        {
            "enabled": True,
            "bot_token": token,
            "streaming": True,
            "allow_from": [sender_id],  # pinned: the single tester, never "*"
            "rich_messages": False,  # keep the classic edit path observable
        },
        bus,
    )
    manager = ChannelManager({}, bus, session_service=service)
    manager.channels["telegram"] = telegram
    producer = ImStreamProducer(
        store=store, plumbing_resolver=lambda: (bus, manager)
    ).attach(service)
    runtime = ChannelRuntime(
        bus=bus,
        session_service=service,
        manager=manager,
        session_map_path=rig_root / "tg-smoke-sessions" / "sessions.json",
        reply_timeout_s=imlib.PROD_REPLY_TIMEOUT_S,
        poll_interval_s=imlib.PROD_POLL_INTERVAL_S,
        operators=[sender_id],
    )

    # Adapter-call spy (records only; the REAL adapter code path runs).
    calls: List[Dict[str, Any]] = []
    real_send_delta = telegram.send_delta
    real_send = telegram.send

    async def spy_delta(cid: str, delta: str, metadata: Optional[dict] = None):
        calls.append(
            {"kind": "delta", "t": time.time(), "meta": dict(metadata or {})}
        )
        return await real_send_delta(cid, delta, metadata)

    async def spy_send(msg):
        calls.append({"kind": "send", "t": time.time(), "content": msg.content})
        return await real_send(msg)

    telegram.send_delta = spy_delta  # type: ignore[method-assign]
    telegram.send = spy_send  # type: ignore[method-assign]

    rec = riglib.GroupRecorder(HERE, "telegram-smoke")
    await runtime.start(start_manager=True)
    exit_code = 0
    try:
        print(
            f"[t9-tg] injecting one turn into chat {chat_id} — WATCH the "
            "Telegram chat: a single message should edit itself into shape."
        )
        await telegram._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=PROMPT,
            metadata={"message_id": f"t9tg-{time.time_ns()}"},
            is_dm=True,
        )
        session_id = None
        attempt_id = None
        status = None
        deadline = time.time() + turn_timeout
        while time.time() < deadline:
            session_id = runtime._session_map.get(f"telegram:{chat_id}")
            if session_id:
                attempts = [
                    a for a in store.list_attempts() if a.session_id == session_id
                ]
                if attempts:
                    attempt_id = attempts[-1].attempt_id
                    status = imlib.attempt_status(store, attempt_id)
                    if status in imlib._TERMINAL_STATUSES:
                        break
            await asyncio.sleep(0.25)
        await asyncio.sleep(2.0)

        deltas = [c for c in calls if c["kind"] == "delta"]
        ends = [c for c in deltas if c["meta"].get("_stream_end")]
        sends = [c for c in calls if c["kind"] == "send"]
        rec.check("attempt_completed", status == "completed", str(status))
        rec.check("stream_deltas_delivered", len(deltas) - len(ends) >= 2, str(len(deltas)))
        rec.check("stream_end_delivered", len(ends) == 1, str(len(ends)))
        rec.check(
            "single_bubble (no duplicate final send)",
            sends == [],
            f"{len(sends)} channel.send calls: {[s['content'][:50] for s in sends]}",
        )
        rec.write(True)
        print("[t9-tg] PASS — evidence: telegram-smoke-results.json")
    except AssertionError as exc:
        rec.write(False)
        exit_code = 1
        print(f"[t9-tg] FAIL — {exc}")
    finally:
        engine_ids = list(service._engine_sessions.values())
        await runtime.stop()
        await service.aclose()
        # Price + DELETE the engine session (spike §7h hygiene).
        for engine_id in engine_ids:
            try:
                request = urllib.request.Request(
                    f"{serve_url}/session/{engine_id}", method="DELETE"
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    print(f"[t9-tg] DELETE {engine_id} -> {response.status}")
            except Exception as exc:  # noqa: BLE001 - hygiene best-effort
                print(f"[t9-tg] DELETE {engine_id} failed: {exc}")
        del producer
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig-root", type=Path, default=DEFAULT_RIG_ROOT)
    parser.add_argument("--turn-timeout", type=float, default=240.0)
    args = parser.parse_args()
    return asyncio.run(main_async(args.rig_root, args.turn_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
