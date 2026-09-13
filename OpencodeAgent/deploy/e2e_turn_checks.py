"""Engine-facing E2E groups: one real model turn + the reclaim truth check.

Split from :mod:`e2e_checks` because these two are the only groups that touch
the engine (and the only ones that cost money): exactly ONE minimal model turn
per run, and one ``docker exec`` probe of the container-internal opencode serve.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from e2e_rig import Rig, TenantRig, docker, try_json
from router.backend import DockerCliBackend
from router.reclaim import SESSION_PROBE_PATH, engine_last_updated_ms
from router.registry import Tenant

# --- G5: one real model turn through the router -----------------------------


def check_model_turn(rig: Rig, tenant: TenantRig) -> None:
    """One minimal turn: proves the gateway answers with real streamed content."""
    rec = rig.recorder
    prompt = (
        "Reply with exactly the single word OK. Do not call any tool, "
        "do not iterate, do not spawn subagents."
    )
    created = rig.request(
        "POST", "/sessions", tenant=tenant, json_body={"title": "T11 router model turn"}
    )
    if not rec.check(
        "model",
        "POST /sessions -> 201",
        created.status_code == 201,
        f"HTTP {created.status_code}",
    ):
        return
    session_id = str(created.json()["session_id"])

    events: list[tuple[float, str]] = []
    response = rig.open_stream(
        f"/sessions/{session_id}/events", tenant=tenant, timeout=300.0
    )
    stream_started = time.monotonic()
    first_event_at: float | None = None
    try:
        sent = rig.request(
            "POST",
            f"/sessions/{session_id}/messages",
            tenant=tenant,
            json_body={"content": prompt},
        )
        rec.check(
            "model",
            "POST /messages accepted",
            sent.status_code in (200, 201, 202),
            f"HTTP {sent.status_code}",
        )
        buffer = b""
        for chunk in response.iter_raw():
            arrived = time.monotonic() - stream_started
            buffer += chunk
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                name = _frame_event_name(frame)
                if name:
                    if first_event_at is None:
                        first_event_at = arrived
                        rec.measure(
                            "sse_first_event_s", arrived, tenant=tenant.tenant_id
                        )
                    events.append((arrived, name))
                if name in ("attempt.completed", "attempt.failed", "router.error"):
                    break
            if events and events[-1][1] in (
                "attempt.completed",
                "attempt.failed",
                "router.error",
            ):
                break
    finally:
        response.close()

    names = [name for _, name in events]
    rec.check(
        "model",
        "events streamed through the router",
        bool(events),
        f"{len(events)} events: {sorted(set(names))}",
    )
    rec.check(
        "model",
        "text_delta relayed",
        "text_delta" in names,
        f"{names.count('text_delta')} deltas",
    )
    terminal = [
        name for name in names if name in ("attempt.completed", "attempt.failed")
    ]
    rec.check(
        "model",
        "terminal event relayed",
        terminal == ["attempt.completed"],
        str(terminal or names[-3:]),
    )
    rec.check(
        "model",
        "no synthetic router error",
        "router.error" not in names,
        str(names[-3:]),
    )

    spread = (events[-1][0] - events[0][0]) if len(events) > 1 else 0.0
    rec.measure(
        "sse_event_spread_s", spread, tenant=tenant.tenant_id, events=len(events)
    )
    rec.check(
        "sse",
        "events arrived SPREAD OUT, not in one buffered burst",
        spread > 0.5 and first_event_at is not None and first_event_at < spread,
        f"spread={spread:.2f}s first_event={first_event_at}",
    )

    transcript = rig.request("GET", f"/sessions/{session_id}/messages", tenant=tenant)
    if transcript.status_code == 200:
        roles = [message.get("role") for message in transcript.json()]
        rec.check(
            "model",
            "transcript persisted behind the router (user+assistant)",
            "user" in roles and "assistant" in roles,
            f"roles={roles}",
        )


# --- G6: the idle-reclaim truth check against the LIVE engine ---------------


def check_engine_truth(rig: Rig, tenant: TenantRig, evidence_dir: Path) -> None:
    """Validate the opencode-router truth check against the pinned serve."""
    rec = rig.recorder
    probe = docker(
        "exec",
        tenant.container,
        "/bin/sh",
        "-c",
        'curl -sf --max-time 10 ${OPENCODE_SERVER_PASSWORD:+-u "opencode:$OPENCODE_SERVER_PASSWORD"} '
        '"http://127.0.0.1:4096/session?limit=1&roots=true"',
    )
    raw = probe.stdout.strip()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"engine_session_probe_{tenant.tenant_id}.json").write_text(
        json.dumps(
            {
                "container": tenant.container,
                "returncode": probe.returncode,
                "body": try_json(raw),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not rec.check(
        "reclaim",
        "engine GET /session?limit=1&roots=true answered",
        probe.returncode == 0,
        raw[:160],
    ):
        return
    sessions = try_json(raw)
    has_sessions = isinstance(sessions, list) and bool(sessions)
    rec.check(
        "reclaim",
        "the engine answered with the documented list shape",
        isinstance(sessions, list),
        f"root sessions={len(sessions) if isinstance(sessions, list) else 'n/a'}",
    )

    backend = DockerCliBackend()
    updated = asyncio.run(
        engine_last_updated_ms(
            backend,
            Tenant(
                tenant.tenant_id, tenant.public_host, tenant.upstream, tenant.container
            ),
        )
    )
    if has_sessions:
        rec.check(
            "reclaim",
            f"router parsed time.updated from the live engine ({SESSION_PROBE_PATH})",
            isinstance(updated, int) and updated > 1_600_000_000_000,
            f"time.updated={updated}",
        )
    else:
        # The bridge creates the opencode session lazily on the first message,
        # so an idle tenant legitimately has no root sessions. The parser must
        # then report "no truth" (None) rather than invent a timestamp.
        rec.note("engine reported zero root sessions (no turn yet on this tenant)")
        rec.check(
            "reclaim",
            "an empty session list yields no engine truth (None)",
            updated is None,
            f"got {updated}",
        )

    decisions = rig.admin("POST", "/router-admin/reclaim", timeout=120.0).json()
    by_tenant = {decision["tenant"]: decision for decision in decisions}
    expected_source = "engine" if has_sessions else "proxy"
    rec.check(
        "reclaim",
        f"admin reclaim resolved an activity truth for every tenant (expected source: {expected_source})",
        all(decision["truth_source"] in ("engine", "proxy") for decision in decisions)
        and by_tenant[tenant.tenant_id]["truth_source"] == expected_source,
        json.dumps({key: value["truth_source"] for key, value in by_tenant.items()}),
    )
    rec.check(
        "reclaim",
        "with a long idle TTL nothing is reclaimed (truth check fails safe)",
        all(decision["reclaim"] is False for decision in decisions),
        json.dumps({key: value["reason"] for key, value in by_tenant.items()})[:300],
    )


def _frame_event_name(frame: bytes) -> str:
    for line in frame.decode("utf-8", "replace").splitlines():
        if line.startswith("event:"):
            return line[len("event:") :].strip()
    return ""
