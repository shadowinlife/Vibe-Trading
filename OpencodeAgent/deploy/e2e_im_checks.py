"""T12 matrix item 5 — IM non-cross-talk at the strongest feasible level.

No real bot credentials exist (T8/T9/T10/T11 fail-closed convention), so the
real-dual-bot smoke is USER-GATED (``real_dual_bot_smoke.py``). What is proven
here, without real platforms:

* **Level 1 (runtime)** — the T8 ``imlib`` MockChannel pattern, run as the
  production IM wiring (real ``MessageBus``/``ChannelManager``/
  ``ChannelRuntime`` + real bridge service via the production factory) INSIDE
  each tenant's own container against that tenant's own engine, two instances
  CONCURRENTLY, with distinct ``{channel, chat_id}`` pairs. Each probe injects
  one inbound message through ``BaseChannel._handle_message`` (the ingress all
  16 adapters use) and records every outbound its channel receives.
* **Level 2 (routing/store)** — the sessions the probes created must appear in
  the RIGHT tenant's gateway REST list (through the router) and in neither
  list for the other tenant; the session map and store on each volume carry
  only that tenant's ``{channel, chat_id}`` mapping.
* **Level 3 (container config)** — ``check_channels_config_isolation`` in
  :mod:`e2e_matrix_checks` (agent.json/env cross-greps).

Honesty note (mirrored into tenancy_report.md): the two probes live in
separate containers, so cross-delivery between them is structurally impossible
(no shared bus, no shared network, no shared filesystem) — the assertions
document that structural isolation empirically rather than exercising a shared
in-memory bus. The shared-bus crosstalk risk (one process hosting two tenants)
is exactly what D2's full-stack-per-tenant ruling removes, and T8's parity
suite pinned the single-process MockChannel semantics.

The probe source (``im_tenant_probe.py``) is piped over stdin — nothing is
written into the tenant image or its volume except the session state the
gateway itself would write.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from e2e_rig import Rig, TenantRig, docker

DEPLOY_DIR = Path(__file__).resolve().parent
PROBE_PATH = DEPLOY_DIR / "im_tenant_probe.py"
RESULT_SENTINEL = "__T12_PROBE_RESULT__"
CHAT_IDS = {"a": "t12-a-chat", "b": "t12-b-chat"}
PROMPT = (
    "Reply with exactly the single word OK. Do not call any tool, "
    "do not iterate, do not spawn subagents."
)


def parse_probe_result(stdout: str) -> dict[str, Any] | None:
    """Extract the probe's sentinel JSON from a docker-exec stdout capture."""
    for line in stdout.splitlines():
        if line.startswith(RESULT_SENTINEL):
            try:
                parsed = json.loads(line[len(RESULT_SENTINEL) :])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _run_probe(
    tenant: TenantRig, chat_id: str, wait_s: float, out_dir: Path
) -> dict[str, Any]:
    """Run one in-container IM probe; return its parsed result + raw logs."""
    command = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"VT_T12_TENANT={tenant.tenant_id}",
        "-e",
        f"VT_T12_CHAT_ID={chat_id}",
        "-e",
        f"VT_T12_PROMPT={PROMPT}",
        "-e",
        f"VT_T12_WAIT_S={wait_s}",
        tenant.container,
        "/opt/venv/bin/python3",
        "-",
    ]
    started = time.time()
    completed = subprocess.run(
        command,
        input=PROBE_PATH.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        check=False,
        timeout=wait_s + 240.0,
    )
    wall_s = round(time.time() - started, 3)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"im_probe_{tenant.tenant_id}.stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (out_dir / f"im_probe_{tenant.tenant_id}.stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    result = parse_probe_result(completed.stdout) or {}
    result["_exit_code"] = completed.returncode
    result["_wall_s"] = wall_s
    (out_dir / f"im_probe_{tenant.tenant_id}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def run_im_matrix(
    rig: Rig, out_dir: Path, *, wait_s: float = 300.0
) -> dict[str, dict[str, Any]]:
    """Run both tenant probes CONCURRENTLY and record the cross-talk matrix."""
    rec = rig.recorder
    tenants = list(rig.tenants.values())
    results: dict[str, dict[str, Any]] = {}
    threads: list[threading.Thread] = []

    def _worker(tenant: TenantRig) -> None:
        chat_id = CHAT_IDS.get(tenant.tenant_id, f"t12-{tenant.tenant_id}-chat")
        results[tenant.tenant_id] = _run_probe(tenant, chat_id, wait_s, out_dir)

    for tenant in tenants:
        thread = threading.Thread(target=_worker, args=(tenant,), daemon=True)
        threads.append(thread)
        thread.start()
        # Small stagger so the two turns genuinely OVERLAP (both in flight).
        time.sleep(1.0)
    for thread in threads:
        thread.join(timeout=wait_s + 300.0)

    for tenant in tenants:
        tid = tenant.tenant_id
        result = results.get(tid, {})
        chat_id = CHAT_IDS.get(tid, f"t12-{tid}-chat")
        if not rec.check(
            "m5-im",
            f"tenant {tid}: in-container IM probe completed the round trip",
            bool(result.get("round_trip")),
            f"exit={result.get('_exit_code')} wall={result.get('_wall_s')}s "
            f"error={str(result.get('error'))[:120]}",
        ):
            continue
        rec.measure(
            "im_first_response_s",
            float(result.get("im_first_response_s") or 0.0),
            tenant=tid,
            kind=result.get("first_outbound_kind"),
        )
        rec.measure(
            "im_terminal_s",
            float(result.get("im_terminal_s") or 0.0),
            tenant=tid,
        )
        cost = result.get("cost_usd")
        if isinstance(cost, (int, float)):
            rec.measure("im_turn_cost_usd", float(cost), unit="USD", tenant=tid)
        # Outbound isolation: every message this tenant's channel received
        # carries THIS tenant's {channel, chat_id} — nothing foreign.
        sent = result.get("sent") or []
        foreign = [
            item
            for item in sent
            if item.get("chat_id") != chat_id or item.get("channel") != "mockim"
        ]
        rec.check(
            "m5-im",
            f"tenant {tid}: outbound carries only its own {{channel, chat_id}}",
            bool(sent) and not foreign,
            f"sent={len(sent)} foreign={len(foreign)}",
        )
        # Routing: the inbound message mapped to a session in THIS tenant's store.
        session_map = result.get("session_map") or {}
        expected_key = f"mockim:{chat_id}"
        vt_session = session_map.get(expected_key, "")
        rec.check(
            "m5-im",
            f"tenant {tid}: session map holds only its own {{channel,chat_id}} key",
            set(session_map) == {expected_key},
            json.dumps(session_map)[:160],
        )
        store_sessions = result.get("store_sessions") or []
        rec.check(
            "m5-im",
            f"tenant {tid}: the IM session landed in its own store",
            vt_session in store_sessions,
            f"vt_session={vt_session[:24]} store={len(store_sessions)}",
        )

    # Cross-tenant assertions (both probes must have produced results).
    if len(results) == 2 and all(r.get("round_trip") for r in results.values()):
        first_tid, second_tid = (t.tenant_id for t in tenants[:2])
        for tid, other_tid in ((first_tid, second_tid), (second_tid, first_tid)):
            result = results[tid]
            other = results[other_tid]
            other_chat = CHAT_IDS.get(other_tid, f"t12-{other_tid}-chat")
            other_session = (other.get("session_map") or {}).get(
                f"mockim:{other_chat}", ""
            )
            sent_blob = json.dumps(result.get("sent") or [])
            rec.check(
                "m5-im",
                f"tenant {tid}: its channel never saw the other tenant's chat/session",
                other_chat not in sent_blob and other_session not in sent_blob,
                f"scanned {len(result.get('sent') or [])} outbound records",
            )
            # Container-level: the other tenant's chat id / session id appear
            # nowhere in this tenant's persisted state.
            tenant = rig.tenants[tid]
            hits = 0
            for marker in (other_chat, other_session):
                if not marker:
                    continue
                grepped = docker(
                    "exec",
                    tenant.container,
                    "/bin/sh",
                    "-c",
                    f"grep -rlF '{marker}' /home/opencode/.vibe-trading 2>/dev/null | wc -l",
                )
                hits += int(grepped.stdout.strip() or 0)
            rec.check(
                "m5-im",
                f"tenant {tid}: zero state hits for the other tenant's IM chat/session",
                hits == 0,
                f"hits={hits}",
            )
    return results


def check_im_sessions_via_gateway(rig: Rig, results: dict[str, dict[str, Any]]) -> None:
    """Level 2: the IM-created sessions are visible ONLY through their own gateway."""
    rec = rig.recorder
    im_sessions: dict[str, str] = {}
    for tid, result in results.items():
        chat_id = CHAT_IDS.get(tid, f"t12-{tid}-chat")
        im_sessions[tid] = (result.get("session_map") or {}).get(
            f"mockim:{chat_id}", ""
        )
    for tenant in rig.tenants.values():
        response = rig.request("GET", "/sessions?limit=200", tenant=tenant)
        ids = (
            [str(item.get("session_id")) for item in response.json()]
            if response.status_code == 200
            else []
        )
        own = im_sessions.get(tenant.tenant_id, "")
        rec.check(
            "m5-im",
            f"tenant {tenant.tenant_id}: its IM session is listed by its OWN gateway (via router)",
            bool(own) and own in ids,
            f"HTTP {response.status_code} own={own[:24]}",
        )
        foreign = [
            sid for tid, sid in im_sessions.items() if tid != tenant.tenant_id and sid
        ]
        leaked = [sid for sid in foreign if sid in ids]
        rec.check(
            "m5-im",
            f"tenant {tenant.tenant_id}: the other tenant's IM session is NOT in its list",
            not leaked,
            f"foreign={len(foreign)} leaked={leaked}",
        )
