"""T12 isolation matrix — state-level items (plan T12: 双租户同机验收).

The matrix items that inspect persisted state and container internals:

    m4  A 的会话列表不含 B          (REST listing + volume/store-level greps)
    m5  IM 互不串话 — config level   (agent.json/env carry zero foreign material;
        the runtime level lives in :mod:`e2e_im_checks`)
    m6  跨租户 opencode/MCP 进程不可互访 (docker topology, in-container network
        probes, PID-namespace listing, filesystem cross-greps)

Every verdict is an observed docker inspect field, in-container command
result, or HTTP status — nothing is estimated (plan T12 Must-NOT). Secrets
are never written to evidence: cross-greps use in-container commands and
record hit COUNTS only.
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e_rig import Rig, TenantRig, docker, try_json


def _pair(rig: Rig) -> tuple[TenantRig, TenantRig]:
    tenants = list(rig.tenants.values())
    if len(tenants) < 2:
        raise RuntimeError("the isolation matrix needs at least two tenants")
    return tenants[0], tenants[1]


# --- M4: session-list isolation (REST + store level) --------------------------


def check_session_list_isolation(
    rig: Rig, sessions: dict[str, str]
) -> dict[str, list[str]]:
    """Each tenant's REST session list contains none of the other's sessions."""
    rec = rig.recorder
    own_ids: dict[str, list[str]] = {}
    listings: dict[str, list[str]] = {}
    for tenant in rig.tenants.values():
        response = rig.request("GET", "/sessions?limit=200", tenant=tenant)
        ids = (
            [str(item.get("session_id")) for item in response.json()]
            if response.status_code == 200
            else []
        )
        listings[tenant.tenant_id] = ids
        own_ids[tenant.tenant_id] = ids
        marker = sessions.get(tenant.tenant_id, "")
        rec.check(
            "m4",
            f"tenant {tenant.tenant_id}: GET /sessions 200 and lists its own marker session",
            response.status_code == 200 and (marker in ids if marker else bool(ids)),
            f"HTTP {response.status_code} sessions={len(ids)}",
        )
    for tenant in rig.tenants.values():
        foreign = [
            sid
            for other, ids in listings.items()
            if other != tenant.tenant_id
            for sid in ids
        ]
        leaked = [sid for sid in foreign if sid in listings[tenant.tenant_id]]
        rec.check(
            "m4",
            f"tenant {tenant.tenant_id}: session list contains NONE of the other tenant's",
            not leaked,
            f"foreign={len(foreign)} leaked={leaked[:3]}",
        )
    return own_ids


def check_store_level_isolation(
    rig: Rig, own_ids: dict[str, list[str]], evidence_dir: Path
) -> None:
    """Store level: a tenant's volume carries zero of the other's session state."""
    rec = rig.recorder
    report: dict[str, object] = {}
    for tenant in rig.tenants.values():
        others = [
            sid
            for tid, ids in own_ids.items()
            if tid != tenant.tenant_id
            for sid in ids
        ]
        store_root = "/home/opencode/.vibe-trading/sessions"
        listing = docker(
            "exec", tenant.container, "/bin/sh", "-c", f"ls -1 {store_root} 2>/dev/null"
        )
        on_disk = listing.stdout.split()
        foreign_dirs = [sid for sid in others if sid in on_disk]
        hits = 0
        for sid in others:
            grepped = docker(
                "exec",
                tenant.container,
                "/bin/sh",
                "-c",
                f"grep -rl '{sid}' /home/opencode/.vibe-trading 2>/dev/null | wc -l",
            )
            hits += int(grepped.stdout.strip() or 0)
        own_present = [sid for sid in own_ids[tenant.tenant_id] if sid in on_disk]
        rec.check(
            "m4",
            f"tenant {tenant.tenant_id}: no foreign session dir on its volume",
            not foreign_dirs,
            f"disk_dirs={len(on_disk)} foreign={foreign_dirs[:3]}",
        )
        rec.check(
            "m4",
            f"tenant {tenant.tenant_id}: zero grep hits for foreign session ids in its state",
            hits == 0,
            f"hits={hits} over {len(others)} foreign ids",
        )
        rec.check(
            "m4",
            f"tenant {tenant.tenant_id}: its own REST sessions exist on its own volume",
            len(own_present) == len(own_ids[tenant.tenant_id]),
            f"{len(own_present)}/{len(own_ids[tenant.tenant_id])}",
        )
        report[tenant.tenant_id] = {
            "disk_dirs": len(on_disk),
            "foreign_dirs": foreign_dirs,
            "foreign_grep_hits": hits,
            "own_present": len(own_present),
            "own_total": len(own_ids[tenant.tenant_id]),
        }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "store_isolation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


# --- M6: cross-tenant process / network / filesystem isolation ----------------


def check_process_network_isolation(
    rig: Rig, evidence_dir: Path, own_ids: dict[str, list[str]]
) -> None:
    """From inside A: B's serve/gateway/MCP are unreachable; volumes carry no B state."""
    rec = rig.recorder
    first, second = _pair(rig)
    topology: dict[str, object] = {}

    def _inspect(container: str) -> dict:
        completed = docker("inspect", container)
        parsed = try_json(completed.stdout)
        return parsed[0] if isinstance(parsed, list) and parsed else {}

    inspects = {t.tenant_id: _inspect(t.container) for t in (first, second)}
    for tenant in (first, second):
        info = inspects[tenant.tenant_id]
        networks = info.get("NetworkSettings", {}).get("Networks", {})
        topology[tenant.tenant_id] = {
            "container": tenant.container,
            "networks": {
                name: {"ip": net.get("IPAddress"), "gateway": net.get("Gateway")}
                for name, net in networks.items()
            },
            "published_ports": info.get("NetworkSettings", {}).get("Ports", {}),
        }
    shared = set(topology[first.tenant_id]["networks"]) & set(
        topology[second.tenant_id]["networks"]
    )
    rec.check(
        "m6",
        "docker topology: the tenants share NO network (separate compose networks)",
        not shared,
        f"a={sorted(topology[first.tenant_id]['networks'])} "
        f"b={sorted(topology[second.tenant_id]['networks'])} shared={sorted(shared)}",
    )
    for tenant in (first, second):
        ports = topology[tenant.tenant_id]["published_ports"]
        rec.check(
            "m6",
            f"tenant {tenant.tenant_id}: serve 4096 has NO published port (internal-only)",
            not any(str(binding).startswith("4096") for binding in ports),
            f"published={sorted(ports)}",
        )

    victim_ip = ""
    victim_networks = (
        inspects[second.tenant_id].get("NetworkSettings", {}).get("Networks", {})
    )
    for net in victim_networks.values():
        victim_ip = net.get("IPAddress") or victim_ip
    attacker = first
    victim = second

    def _in_container(script: str, timeout: float = 30.0):
        return docker(
            "exec", attacker.container, "/bin/sh", "-c", script, timeout=timeout
        )

    # DNS: the victim's container name does not resolve from the attacker.
    dns = _in_container(f"getent hosts {victim.container} || echo NX")
    rec.check(
        "m6",
        f"inside {attacker.tenant_id}: victim container name does not resolve (DNS)",
        "NX" in dns.stdout and victim.container not in dns.stdout,
        dns.stdout.strip()[:120],
    )
    # Network: victim's container IP on 8080 (gateway) and 4096 (serve).
    for port, label in ((8080, "gateway"), (4096, "opencode serve")):
        probe = _in_container(
            f"curl -s -m 4 -o /dev/null -w '%{{http_code}}' http://{victim_ip}:{port}/ "
            "|| echo CONN_FAIL"
        )
        out = probe.stdout.strip()
        rec.check(
            "m6",
            f"inside {attacker.tenant_id}: victim {label} {victim_ip}:{port} unreachable",
            "CONN_FAIL" in out or out in ("000", ""),
            f"result={out!r}",
        )
    # PID namespace: only the attacker's own stack is visible inside A.
    procs = _in_container(
        "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null; echo; done"
    )
    lines = [line for line in procs.stdout.splitlines() if line.strip()]
    serve_count = sum(1 for line in lines if "opencode" in line and "serve" in line)
    rec.check(
        "m6",
        f"inside {attacker.tenant_id}: exactly ONE opencode serve visible (its own)",
        serve_count == 1,
        f"procs={len(lines)} serve_lines={serve_count}",
    )
    rec.check(
        "m6",
        f"inside {attacker.tenant_id}: no victim process cmdline visible",
        not any(victim.container in line for line in lines),
        f"{len(lines)} cmdlines scanned",
    )
    # Filesystem: the attacker's volume carries zero of the victim's state.
    markers = {
        "victim_public_host": victim.public_host,
        "victim_placeholder": f"PROVISION_PLACEHOLDER_{victim.tenant_id.upper()}",
        "victim_session_ids": own_ids.get(victim.tenant_id, []),
    }
    fs_hits: dict[str, int] = {}
    host_probe = _in_container(
        f"grep -rlF '{victim.public_host}' /home/opencode 2>/dev/null | wc -l"
    )
    fs_hits["victim_public_host"] = int(host_probe.stdout.strip() or 0)
    ph_probe = _in_container(
        f"grep -rlF '{markers['victim_placeholder']}' /home/opencode 2>/dev/null | wc -l"
    )
    fs_hits["victim_placeholder"] = int(ph_probe.stdout.strip() or 0)
    sid_hits = 0
    for sid in markers["victim_session_ids"]:
        sid_probe = _in_container(
            f"grep -rlF '{sid}' /home/opencode 2>/dev/null | wc -l"
        )
        sid_hits += int(sid_probe.stdout.strip() or 0)
    fs_hits["victim_session_ids"] = sid_hits
    rec.check(
        "m6",
        f"inside {attacker.tenant_id}: zero filesystem hits for victim markers",
        all(value == 0 for value in fs_hits.values()),
        json.dumps(fs_hits),
    )
    # Honest probe of the HOST-published port from inside the attacker (the
    # public, key-gated surface — M1 proved 401 without the victim's key).
    gw_probe = _in_container(
        "GW=$(ip route | awk '/default/ {print $3; exit}'); "
        f"curl -s -m 4 -o /dev/null -w '%{{http_code}}' http://$GW:{victim.host_port}/health "
        "|| echo CONN_FAIL"
    )
    topology["host_published_port_probe_from_attacker"] = {
        "target_port": victim.host_port,
        "result": gw_probe.stdout.strip(),
        "note": (
            "the victim's PUBLIC key-gated surface as published on the docker "
            "host — equivalent to any external client; M1 proves 401 without "
            "the victim's key. Internal surfaces (4096, container-net 8080) "
            "are the isolation boundary and are unreachable above."
        ),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "network_topology.json").write_text(
        json.dumps(topology, indent=2, default=str) + "\n", encoding="utf-8"
    )


# --- M5 (config level): channels config carries zero cross-tenant material ----


def check_channels_config_isolation(rig: Rig, tenants_dir: Path) -> None:
    """A's channels config/env carries zero of B's credentials or mappings."""
    rec = rig.recorder
    first, second = _pair(rig)
    for tenant, other in ((first, second), (second, first)):
        agent_json = docker(
            "exec",
            tenant.container,
            "/bin/sh",
            "-c",
            "cat /home/opencode/.vibe-trading/agent.json 2>/dev/null",
        )
        parsed = try_json(agent_json.stdout)
        channels = parsed.get("channels", {}) if isinstance(parsed, dict) else {}
        raw = agent_json.stdout
        rec.check(
            "m5-config",
            f"tenant {tenant.tenant_id}: agent.json channels are fail-closed placeholders",
            isinstance(parsed, dict)
            and channels.get("operators") == []
            and all(
                section.get("enabled") is False
                for name, section in channels.items()
                if isinstance(section, dict) and "enabled" in section
            )
            and f"PROVISION_PLACEHOLDER_{tenant.tenant_id.upper()}" in raw,
            f"operators={channels.get('operators')} placeholders={'yes' if raw else 'no'}",
        )
        foreign_markers = [
            f"PROVISION_PLACEHOLDER_{other.tenant_id.upper()}",
            other.public_host,
            other.api_key,
        ]
        leaked = [m for m in foreign_markers if m and m in raw]
        rec.check(
            "m5-config",
            f"tenant {tenant.tenant_id}: agent.json carries ZERO of the other tenant's material",
            not leaked,
            f"markers_checked={len(foreign_markers)} leaked={len(leaked)}",
        )
        env_dump = docker("exec", tenant.container, "/bin/sh", "-c", "env").stdout
        env_leaked = [
            label
            for label, value in (
                ("other_api_key", other.api_key),
                ("other_public_host", other.public_host),
            )
            if value and value in env_dump
        ]
        rec.check(
            "m5-config",
            f"tenant {tenant.tenant_id}: container env carries ZERO of the other tenant's secrets",
            not env_leaked,
            f"leaked={env_leaked}",
        )
        rec.check(
            "m5-config",
            f"tenant {tenant.tenant_id}: env API_ALLOWED_HOSTS names only its own host",
            f"API_ALLOWED_HOSTS={tenant.public_host},localhost" in env_dump,
            (
                "exact provisioned value present"
                if f"API_ALLOWED_HOSTS={tenant.public_host}" in env_dump
                else "check env render"
            ),
        )
        # Host-side scratch env file (the compose env_file source).
        env_file = tenants_dir / tenant.tenant_id / "tenant.env"
        if env_file.exists():
            text = env_file.read_text(encoding="utf-8")
            rec.check(
                "m5-config",
                f"tenant {tenant.tenant_id}: tenant.env has no foreign key/host",
                other.api_key not in text and other.public_host not in text,
                "cross-grep clean",
            )
