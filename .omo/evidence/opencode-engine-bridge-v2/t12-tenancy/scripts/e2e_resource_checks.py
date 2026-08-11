"""T12 resource measurements: RSS windows, web turns, wake samples, costs.

REAL measurements only (plan T12 Must-NOT: 估算值不得冒充实测值): every number
comes from ``docker stats`` sampling over a window, a timed HTTP request
through the router, or the engine's own cost fields — each traceable to a raw
file in the evidence dir.

Cost discipline: the driver spends at most a handful of minimal real turns
(one short prompt each); ``sweep_engine_costs`` reads the serve-side cost of
every engine session afterwards so the bill is measured, not estimated.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Any

from e2e_rig import Rig, TenantRig, docker, docker_rss_mb, docker_state, try_json
from tenancy_lib import WAKE_MEASUREMENT

MINIMAL_PROMPT = (
    "Reply with exactly the single word OK. Do not call any tool, "
    "do not iterate, do not spawn subagents."
)
TERMINAL_EVENTS = ("attempt.completed", "attempt.failed", "router.error")

_COST_SNIPPET = r"""
import json, os, pathlib, httpx
base = os.environ.get("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/")
password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
auth = ("opencode", password) if password else None
root = pathlib.Path(os.environ.get("VIBE_TRADING_HOME", "/home/opencode/.vibe-trading")) / "sessions"
out = {}
with httpx.Client(auth=auth, timeout=15.0) as client:
    for session_dir in sorted(root.glob("*")):
        meta = session_dir / "session.json"
        if not meta.exists():
            continue
        try:
            cfg = json.loads(meta.read_text()).get("config") or {}
        except Exception:
            continue
        engine_sid = cfg.get("opencode_engine_session_id")
        if not engine_sid:
            continue
        cost, tin, tout = 0.0, 0, 0
        try:
            r = client.get(f"{base}/session/{engine_sid}/message")
            messages = r.json() if r.status_code == 200 else []
        except Exception:
            messages = []
        for entry in messages if isinstance(messages, list) else []:
            info = entry.get("info") if isinstance(entry, dict) else None
            if not isinstance(info, dict):
                continue
            if isinstance(info.get("cost"), (int, float)):
                cost += float(info["cost"])
            tokens = info.get("tokens") or {}
            if isinstance(tokens, dict):
                tin += int(tokens.get("input") or 0)
                tout += int(tokens.get("output") or 0)
        out[session_dir.name] = {
            "engine_session_id": engine_sid,
            "cost_usd": round(cost, 6),
            "tokens_input": tin,
            "tokens_output": tout,
        }
print("__T12_COST__" + json.dumps(out))
"""


class RssSampler:
    """Background ``docker stats`` sampler (windowed, not single-snapshot)."""

    def __init__(
        self,
        containers: dict[str, str],
        out_csv: Path,
        interval_s: float = 2.5,
    ) -> None:
        self.containers = containers
        self.out_csv = out_csv
        self.interval_s = interval_s
        self.label = "boot"
        self.rows: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, label: str) -> None:
        self.label = label
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_label(self, label: str) -> None:
        self.label = label

    def _loop(self) -> None:
        while not self._stop.is_set():
            stamped = time.time()
            label = self.label
            for tenant_id, container in self.containers.items():
                # Stopped containers report residual cgroup readings (~30MiB) —
                # record only running-state samples so windows stay honest.
                if docker_state(container) != "running":
                    continue
                mem = docker_rss_mb(container)
                if mem > 0:
                    self.rows.append(
                        {
                            "t": round(stamped, 3),
                            "tenant": tenant_id,
                            "label": label,
                            "container": container,
                            "mem_mib": round(mem, 1),
                        }
                    )
            self._stop.wait(self.interval_s)

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15.0)
            self._thread = None
        self.dump()
        return self.rows

    def dump(self) -> None:
        self.out_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["t", "tenant", "label", "container", "mem_mib"]
            )
            writer.writeheader()
            writer.writerows(self.rows)


def run_web_turn(
    rig: Rig,
    tenant: TenantRig,
    *,
    title: str,
    session_id: str = "",
    timeout_s: float = 300.0,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One minimal real turn through the router; returns the raw event trail.

    With ``session_id`` the turn reuses an existing session (the reclaim phase
    turns the wake sample's session into the live turn); otherwise a fresh
    session is created. ``payload`` lets a caller pass a dict that is mutated
    IN PLACE while the turn runs — the reclaim phase watches it from another
    thread to resume the policy loop on the first streamed event. The caller
    owns any further concurrency (witness streams, RSS samplers) around this
    blocking call.
    """
    if payload is None:
        payload = {}
    payload["tenant"] = tenant.tenant_id
    if session_id:
        payload["session_id"] = session_id
        payload["create_status"] = 201  # reused (created by the caller)
    else:
        created = rig.request(
            "POST", "/sessions", tenant=tenant, json_body={"title": title}
        )
        payload["create_status"] = created.status_code
        if created.status_code != 201:
            payload["error"] = created.text[:200]
            return payload
        session_id = str(created.json().get("session_id", ""))
        payload["session_id"] = session_id

    events: list[dict[str, Any]] = []
    payload["events"] = events  # live list: watchers see appends as they land
    response = rig.open_stream(
        f"/sessions/{session_id}/events", tenant=tenant, timeout=timeout_s
    )
    payload["stream_status"] = response.status_code
    started = time.monotonic()
    try:
        sent = rig.request(
            "POST",
            f"/sessions/{session_id}/messages",
            tenant=tenant,
            json_body={"content": MINIMAL_PROMPT},
        )
        payload["message_status"] = sent.status_code
        buffer = b""
        for chunk in response.iter_raw():
            arrived = time.monotonic() - started
            buffer += chunk
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                name, data = _parse_frame(frame)
                if name:
                    events.append(
                        {"t_rel": round(arrived, 3), "event": name, "data": data}
                    )
                if name in TERMINAL_EVENTS:
                    break
            if events and events[-1]["event"] in TERMINAL_EVENTS:
                break
    finally:
        response.close()
    payload["event_names"] = [item["event"] for item in events]
    payload["wall_s"] = round(time.monotonic() - started, 3)
    payload["first_event_s"] = events[0]["t_rel"] if events else None
    payload["terminal"] = events[-1]["event"] if events else None
    return payload


def _parse_frame(frame: bytes) -> tuple[str, dict[str, Any] | None]:
    name = ""
    data: dict[str, Any] | None = None
    for line in frame.decode("utf-8", "replace").splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            parsed = try_json(line[len("data:") :].strip())
            data = parsed if isinstance(parsed, dict) else None
    return name, data


def check_web_turn(rec: Any, payload: dict[str, Any], group: str = "turn") -> None:
    """Record the standard turn assertions over a run_web_turn payload."""
    rec.check(
        group,
        f"tenant {payload.get('tenant')}: POST /sessions -> 201",
        payload.get("create_status") == 201,
        f"HTTP {payload.get('create_status')}",
    )
    names = payload.get("event_names") or []
    rec.check(
        group,
        f"tenant {payload.get('tenant')}: message accepted",
        payload.get("message_status") in (200, 201, 202),
        f"HTTP {payload.get('message_status')}",
    )
    rec.check(
        group,
        f"tenant {payload.get('tenant')}: turn streamed to attempt.completed",
        payload.get("terminal") == "attempt.completed" and "text_delta" in names,
        f"terminal={payload.get('terminal')} events={len(names)} "
        f"wall={payload.get('wall_s')}s",
    )


def probe_engine_truth(container: str) -> tuple[int | None, Any]:
    """Sync engine truth probe: newest root session's time.updated (ms)."""
    completed = docker(
        "exec",
        container,
        "/bin/sh",
        "-c",
        'curl -sf --max-time 8 ${OPENCODE_SERVER_PASSWORD:+-u "opencode:$OPENCODE_SERVER_PASSWORD"} '
        '"http://127.0.0.1:4096/session?limit=1&roots=true"',
    )
    parsed = try_json(completed.stdout.strip())
    sessions = parsed if isinstance(parsed, list) else None
    if not sessions or not isinstance(sessions[0], dict):
        return None, parsed
    stamp = (sessions[0].get("time") or {}).get("updated")
    return (int(stamp) if isinstance(stamp, (int, float)) else None), parsed


def measure_wake(
    rig: Rig, tenant: TenantRig, *, trigger: str, budget_s: float = 480.0
) -> dict[str, Any]:
    """One cold-start wake sample: stopped container -> router wake -> 201."""
    rec = rig.recorder
    state_before = docker_state(tenant.container)
    started = time.monotonic()
    response = rig.request(
        "POST",
        "/sessions",
        tenant=tenant,
        json_body={"title": f"T12 wake sample ({trigger})"},
        timeout=budget_s,
    )
    elapsed = time.monotonic() - started
    rec.measure(
        WAKE_MEASUREMENT,
        elapsed,
        tenant=tenant.tenant_id,
        trigger=trigger,
        state_before=state_before,
        status=response.status_code,
    )
    payload = {
        "tenant": tenant.tenant_id,
        "trigger": trigger,
        "state_before": state_before,
        "elapsed_s": round(elapsed, 3),
        "status": response.status_code,
        "session_id": (
            str(response.json().get("session_id", ""))
            if response.status_code == 201
            else ""
        ),
    }
    rec.check(
        "wake",
        f"wake sample ({trigger}): stopped -> inbound -> 201 with a real session",
        response.status_code == 201 and bool(payload["session_id"]),
        f"HTTP {response.status_code} in {elapsed:.1f}s (state_before={state_before})",
    )
    rec.check(
        "wake",
        f"wake sample ({trigger}): container running afterwards",
        docker_state(tenant.container) == "running",
        str(docker_state(tenant.container)),
    )
    return payload


def sweep_engine_costs(rig: Rig, out_dir: Path) -> dict[str, Any]:
    """Read the serve-side cost/tokens of every engine session, per tenant."""
    report: dict[str, Any] = {"tenants": {}, "total_cost_usd": 0.0}
    for tenant in rig.tenants.values():
        completed = docker(
            "exec",
            tenant.container,
            "/bin/sh",
            "-c",
            "/opt/venv/bin/python3 -c " + _shell_quote(_COST_SNIPPET),
            timeout=120.0,
        )
        per_session: dict[str, Any] = {}
        for line in completed.stdout.splitlines():
            if line.startswith("__T12_COST__"):
                try:
                    per_session = json.loads(line[len("__T12_COST__") :])
                except json.JSONDecodeError:
                    per_session = {}
        cost = round(
            sum(
                float(item.get("cost_usd") or 0.0)
                for item in per_session.values()
                if isinstance(item, dict)
            ),
            6,
        )
        report["tenants"][tenant.tenant_id] = {
            "sessions": per_session,
            "cost_usd": cost,
            "stderr_head": completed.stderr.strip()[:200],
        }
        report["total_cost_usd"] = round(report["total_cost_usd"] + cost, 6)
    report["note"] = (
        "serve-side assistant message cost fields (same source T8's stop_rig "
        "used); IM probe costs are cross-checked in im_probe_*.json"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cost.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"
