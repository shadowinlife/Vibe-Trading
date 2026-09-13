#!/usr/bin/env python3
"""T12 real dual-bot IM smoke — USER-GATED (plan T12 matrix item 5, real level).

The automated matrix proves IM non-cross-talk with in-container MockChannel
probes (``e2e_im_checks.py``). This script is the PENDING real-platform level:
two tenants, two REAL test bots (钉钉 and/or 飞书), one human sender — extending
T8's ``real_platform_smoke.py`` pattern to two tenants.

No bot credentials exist in this environment (T8/T9/T10/T11 fail-closed
convention), so the script REFUSES to run without them and never reads any
config file for credentials — everything comes from the env below:

    # tenant A bot (platform: dingtalk | feishu)
    VT_T12_A_PLATFORM=dingtalk
    VT_T12_A_DINGTALK_CLIENT_ID=...        # TEST bot only, never production
    VT_T12_A_DINGTALK_CLIENT_SECRET=...
    VT_T12_A_ALLOW_FROM=<your sender id>   # optional, default *
    # tenant B bot
    VT_T12_B_PLATFORM=feishu
    VT_T12_B_FEISHU_APP_ID=...
    VT_T12_B_FEISHU_APP_SECRET=...
    VT_T12_B_ALLOW_FROM=<your open_id>

    # rig location (defaults match the T12 rig)
    VT_T12_TENANTS_DIR=/tmp/vt-t12-rig/tenants
    VT_T12_A_CONTAINER=vt-t12-a
    VT_T12_B_CONTAINER=vt-t12-b
    VT_T12_WAIT_S=600
    VT_T12_EVIDENCE=.omo/evidence/opencode-engine-bridge-v2/t12-tenancy

Flow: for each tenant, an in-container smoke stack is started INSIDE that
tenant's own container (docker exec, stdin-piped — serve stays internal, the
host never touches it): the REAL adapter is loaded by the production
``ChannelManager`` from the credentials above, wired to the real
``ChannelRuntime`` + bridge service against the tenant's own engine, with the
tenant's REAL session store. A human then DMs each bot:

    bot A:  reply with exactly SMOKE_OK_A
    bot B:  reply with exactly SMOKE_OK_B

Each side asserts its own round trip; the host cross-asserts that neither
side ever saw the OTHER tenant's marker (non-cross-talk at the real level).
Verdicts + timings land in ``real-dual-smoke-results.json`` under
``VT_T12_EVIDENCE``. Adapter SDKs are lazy extras inside the image
(``[channels]``); a missing SDK is reported with the registry's install hint
instead of failing silently.

Cost: two real model turns on the operator's bot credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

RESULT_SENTINEL = "__T12_SMOKE_RESULT__"

_IN_CONTAINER = r"""
import asyncio, json, os, time
from pathlib import Path

SENTINEL = "__T12_SMOKE_RESULT__"
cfg = json.loads(os.environ["VT_T12_SMOKE_CONFIG"])
tenant = cfg["tenant"]; platform = cfg["platform"]; marker = cfg["marker"]
wait_s = float(cfg["wait_s"]); chat_hint = cfg.get("chat_hint", "")

async def main() -> dict:
    from src.api import state as api_state
    from src.api.helpers import RUNS_DIR, SESSIONS_DIR
    from src.channels.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime
    from src.opencode_bridge.wiring import build_session_service, start_session_service
    from src.session.events import EventBus
    from src.session.store import SessionStore

    store = SessionStore(base_dir=SESSIONS_DIR)
    event_bus = EventBus(); event_bus.set_loop(asyncio.get_running_loop())
    service = build_session_service(store, event_bus, RUNS_DIR)
    bus = MessageBus()
    manager = ChannelManager({platform: cfg["section"]}, bus, session_service=service)
    api_state._channel_bus = bus; api_state._channel_manager = manager
    runtime = ChannelRuntime(
        bus=bus, session_service=service, manager=manager,
        session_map_path=Path("/tmp/t12-smoke-map.json"),
        reply_timeout_s=600.0, poll_interval_s=0.25,
    )
    out = {"tenant": tenant, "platform": platform, "marker": marker,
           "started_at": time.time()}
    try:
        status = manager.get_status().get(platform, {})
        out["adapter_status"] = status
        if not status.get("loaded"):
            out["verdict"] = "FAIL"
            out["error"] = (f"{platform} adapter unavailable: {status.get('error')} "
                            f"({status.get('install_hint')})")
            return out
        await start_session_service(service)
        await runtime.start(start_manager=True)
        print(f"[t12-smoke:{tenant}] {platform} adapter live — DM the bot: "
              f"'reply with exactly {marker}'", flush=True)
        deadline = time.time() + wait_s
        reply = None
        while time.time() < deadline and reply is None:
            for sess in service.list_sessions(limit=50):
                for message in service.get_messages(sess.session_id, limit=50):
                    if message.role == "assistant" and marker in (message.content or ""):
                        reply = {"session_id": sess.session_id,
                                 "content": (message.content or "")[:200]}
                        break
                if reply:
                    break
            await asyncio.sleep(2.0)
        out["round_trip"] = reply
        out["verdict"] = "PASS" if reply else "FAIL"
        out["session_map"] = dict(runtime._session_map)
        return out
    except Exception as exc:
        out["verdict"] = "FAIL"; out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        out["finished_at"] = time.time()
        try:
            await runtime.stop()
        except Exception:
            pass
        try:
            await service.aclose()
        except Exception:
            pass

out = asyncio.run(main())
print(SENTINEL + json.dumps(out, ensure_ascii=False, default=str))
"""


def _platform_section(prefix: str) -> tuple[str, dict[str, Any], list[str]]:
    """Parse one tenant's bot credentials from the env (fail-closed)."""
    platform = os.environ.get(f"VT_T12_{prefix}_PLATFORM", "").strip().lower()
    allow_from = [
        item.strip()
        for item in os.environ.get(f"VT_T12_{prefix}_ALLOW_FROM", "*").split(",")
        if item.strip()
    ]
    problems: list[str] = []
    section: dict[str, Any] = {"enabled": True, "allow_from": allow_from}
    if platform == "dingtalk":
        client_id = os.environ.get(f"VT_T12_{prefix}_DINGTALK_CLIENT_ID", "")
        client_secret = os.environ.get(f"VT_T12_{prefix}_DINGTALK_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            problems.append(
                f"VT_T12_{prefix}_DINGTALK_CLIENT_ID / _CLIENT_SECRET are required"
            )
        section.update({"client_id": client_id, "client_secret": client_secret})
    elif platform == "feishu":
        app_id = os.environ.get(f"VT_T12_{prefix}_FEISHU_APP_ID", "")
        app_secret = os.environ.get(f"VT_T12_{prefix}_FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            problems.append(f"VT_T12_{prefix}_FEISHU_APP_ID / _APP_SECRET are required")
        section.update({"app_id": app_id, "app_secret": app_secret, "streaming": False})
    else:
        problems.append(f"VT_T12_{prefix}_PLATFORM must be 'dingtalk' or 'feishu'")
    return platform, section, problems


def _run_tenant_smoke(
    container: str, config: dict[str, Any], evidence_dir: Path
) -> dict[str, Any]:
    """Pipe the in-container smoke into one tenant container; parse its result."""
    env = {
        **os.environ,
        "VT_T12_SMOKE_CONFIG": json.dumps(config, ensure_ascii=False),
    }
    env_args: list[str] = []
    for key, value in env.items():
        if key.startswith("VT_T12_"):
            env_args += ["-e", f"{key}={value}"]
    completed = subprocess.run(
        ["docker", "exec", "-i", *env_args, container, "/opt/venv/bin/python3", "-"],
        input=_IN_CONTAINER,
        capture_output=True,
        text=True,
        check=False,
        timeout=float(config["wait_s"]) + 300.0,
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"real-smoke-{config['tenant']}.log").write_text(
        completed.stdout + "\n--- stderr ---\n" + completed.stderr, encoding="utf-8"
    )
    for line in completed.stdout.splitlines():
        if line.startswith(RESULT_SENTINEL):
            try:
                parsed = json.loads(line[len(RESULT_SENTINEL) :])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return {
        "tenant": config["tenant"],
        "verdict": "FAIL",
        "error": f"no result sentinel (exit={completed.returncode})",
        "stderr_head": completed.stderr.strip()[:400],
    }


def main(argv: list[str] | None = None) -> int:
    """Run both tenant smokes concurrently and cross-assert non-cross-talk."""
    parser = argparse.ArgumentParser(prog="real_dual_bot_smoke.py", description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(
            os.environ.get(
                "VT_T12_EVIDENCE",
                ".omo/evidence/opencode-engine-bridge-v2/t12-tenancy",
            )
        ),
    )
    args = parser.parse_args(argv)
    evidence_dir = args.evidence.expanduser().resolve()

    wait_s = float(os.environ.get("VT_T12_WAIT_S", "600"))
    specs: list[dict[str, Any]] = []
    problems: list[str] = []
    for prefix, container_default in (("A", "vt-t12-a"), ("B", "vt-t12-b")):
        platform, section, section_problems = _platform_section(prefix)
        problems.extend(section_problems)
        specs.append(
            {
                "tenant": prefix.lower(),
                "container": os.environ.get(
                    f"VT_T12_{prefix}_CONTAINER", container_default
                ),
                "platform": platform,
                "section": section,
                "marker": f"SMOKE_OK_{prefix}",
                "wait_s": wait_s,
            }
        )
    if problems:
        print("REFUSING TO RUN (fail-closed, T8 convention):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nSet TEST-bot credentials for BOTH tenants (see this file's "
            "docstring). Production credentials are forbidden for smokes.",
            file=sys.stderr,
        )
        return 2

    results: dict[str, dict[str, Any]] = {}
    threads = []
    for spec in specs:

        def _worker(spec: dict[str, Any]) -> None:
            results[spec["tenant"]] = _run_tenant_smoke(
                spec["container"], spec, evidence_dir
            )

        thread = threading.Thread(target=_worker, args=(spec,), daemon=True)
        threads.append(thread)
        thread.start()
        time.sleep(1.0)
    print(
        "\n[t12-dual] DM each bot from an ALLOWED account:\n"
        f"    bot A ({specs[0]['platform']}): reply with exactly SMOKE_OK_A\n"
        f"    bot B ({specs[1]['platform']}): reply with exactly SMOKE_OK_B\n"
        f"[t12-dual] waiting up to {wait_s:.0f}s per tenant ..."
    )
    for thread in threads:
        thread.join(timeout=wait_s + 360.0)

    cross_ok = True
    for spec in specs:
        result = results.get(spec["tenant"], {"verdict": "FAIL", "error": "no result"})
        other = "B" if spec["tenant"] == "a" else "A"
        blob = json.dumps(result, ensure_ascii=False)
        if f"SMOKE_OK_{other}" in blob:
            cross_ok = False
            result["cross_talk"] = f"saw the other tenant's marker SMOKE_OK_{other}"
        result["verdict"] = result.get("verdict") if cross_ok else "FAIL (cross-talk)"
    report = {
        "specs": [
            {k: v for k, v in spec.items() if k != "section"} | {"section": "redacted"}
            for spec in specs
        ],
        "results": results,
        "cross_talk_free": cross_ok,
        "verdict": (
            "PASS"
            if cross_ok and all(r.get("verdict") == "PASS" for r in results.values())
            else "FAIL"
        ),
        "finished_at": time.time(),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / "real-dual-smoke-results.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"[t12-dual] verdict: {report['verdict']} -> {path}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
