"""T12 reclaim-policy verification with an accelerated N (plan T12: 回收).

The mechanism under test is T11's: ``POST /router-admin/reclaim`` →
per-tenant evaluate (engine truth ``GET /session?limit=1&roots=true`` →
``time.updated``, proxy ledger fallback, fail closed) → fenced ``docker stop``.

The POLICY shape (idle N hours, unattended) is verified here with an
accelerated N: a scheduler thread hits the admin endpoint on a short interval
while the router runs with a short ``VT_ROUTER_IDLE_TTL_S``. The scheduler is
operator-side on purpose — the router parses ``VT_ROUTER_RECLAIM_INTERVAL_S``
but no in-router periodic loop consumes it (finding documented in
tenancy_report.md §Reclaim); production wiring is a cron/systemd timer or that
loop, and the verified behaviour below is identical either way:

* an IDLE tenant is stopped unattended (verdict ``reclaim=true``, fenced,
  ``drained`` recorded);
* a tenant with a LIVE TURN is kept — the engine truth check sees fresh
  ``time.updated`` while the proxy ledger alone would look idle;
* an inbound request wakes a stopped tenant (driver-owned ``measure_wake``
  samples, taken while the loop is paused so a re-stop cannot race the wake's
  own health polling).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from e2e_rig import Rig, TenantRig, docker, docker_state
from e2e_resource_checks import probe_engine_truth, run_web_turn
from tenancy_lib import classify_loop_passes, dumps

WakeHook = Callable[[Rig, "ReclaimPolicyLoop"], dict[str, Any]]


class ReclaimPolicyLoop:
    """Operator-side scheduler: one admin reclaim pass per interval."""

    def __init__(self, rig: Rig, interval_s: float) -> None:
        self._rig = rig
        self._interval_s = interval_s
        self.passes: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=120.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._paused.is_set():
                self.run_pass()
            self._stop.wait(self._interval_s)

    def run_pass(self) -> dict[str, Any]:
        """One reclaim pass, recorded (also usable single-shot from the driver)."""
        entry: dict[str, Any] = {"t": time.time()}
        try:
            response = self._rig.admin("POST", "/router-admin/reclaim", timeout=120.0)
            entry["status"] = response.status_code
            entry["decisions"] = (
                response.json() if response.status_code == 200 else response.text[:200]
            )
        except Exception as exc:  # noqa: BLE001 — the loop must survive router hiccups
            entry["error"] = f"{type(exc).__name__}: {exc}"
        self.passes.append(entry)
        return entry

    def wait_for_state(self, container: str, state: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if docker_state(container) == state:
                return True
            time.sleep(2.0)
        return False

    def first_stop_verdict(self, tenant_id: str) -> dict[str, Any] | None:
        """The first recorded pass that decided ``reclaim=true`` for *tenant_id*."""
        for entry in self.passes:
            decisions = entry.get("decisions")
            if not isinstance(decisions, list):
                continue
            for decision in decisions:
                if (
                    isinstance(decision, dict)
                    and decision.get("tenant") == tenant_id
                    and decision.get("reclaim") is True
                ):
                    return {**decision, "_pass_t": entry.get("t")}
        return None

    def verdicts_for(
        self, tenant_id: str, *, since_t: float, until_t: float
    ) -> list[dict[str, Any]]:
        """Verdicts for *tenant_id* from passes inside ``(since_t, until_t]``.

        The lower bound matters: passes from BEFORE the turn window (the R1
        idle stops) are a different experiment and must not be counted as
        mid-turn verdicts.
        """
        out = []
        for entry in self.passes:
            pass_t = entry.get("t", 0)
            if pass_t < since_t or pass_t > until_t:
                continue
            decisions = entry.get("decisions")
            if not isinstance(decisions, list):
                continue
            for decision in decisions:
                if isinstance(decision, dict) and decision.get("tenant") == tenant_id:
                    out.append({**decision, "_pass_t": pass_t})
        return out


def run_reclaim_phase(
    rig: Rig,
    out_dir: Path,
    *,
    idle_ttl_s: float,
    interval_s: float,
    wake_hook: WakeHook,
) -> dict[str, Any]:
    """The accelerated-N end-to-end: idle stop → wakes → live-turn keep → idle stop.

    ``wake_hook`` runs while the loop is PAUSED (between the first idle stops
    and the live-turn window) and returns whatever the driver measured — its
    ``{"turn_session": {"tenant": ..., "session_id": ...}}`` entry feeds the
    live turn so the wake sample and the turn setup are the same request.
    """
    rec = rig.recorder
    tenants = list(rig.tenants.values())
    first, second = tenants[0], tenants[1]
    summary: dict[str, Any] = {"idle_ttl_s": idle_ttl_s, "interval_s": interval_s}

    # Restart the router with the accelerated TTL (production default: 10800s).
    rig.router.stop()
    rig.router.settings_env["VT_ROUTER_IDLE_TTL_S"] = str(idle_ttl_s)
    rig.router.start()
    rec.note(
        f"router restarted with VT_ROUTER_IDLE_TTL_S={idle_ttl_s} "
        f"(accelerated N; production default 10800s = 3h)"
    )
    loop = ReclaimPolicyLoop(rig, interval_s)
    loop.start()
    truth_progression: list[dict[str, Any]] = []
    try:
        # R1 — idle tenants are stopped UNATTENDED by the policy loop.
        for tenant in (second, first):
            stopped = loop.wait_for_state(tenant.container, "exited", 180.0)
            verdict = loop.first_stop_verdict(tenant.tenant_id)
            rec.check(
                "reclaim",
                f"idle tenant {tenant.tenant_id} was STOPPED by the policy loop "
                f"(accelerated N={idle_ttl_s}s)",
                stopped,
                f"state={docker_state(tenant.container)} verdict={json.dumps(verdict, default=str)[:220]}",
            )
            if verdict:
                rec.check(
                    "reclaim",
                    f"tenant {tenant.tenant_id}: stop verdict backed by an activity truth, "
                    "fence drained",
                    verdict.get("truth_source") in ("engine", "proxy")
                    and verdict.get("drained") is not False,
                    f"truth={verdict.get('truth_source')} drained={verdict.get('drained')} "
                    f"reason={str(verdict.get('reason'))[:120]}",
                )
        summary["idle_stop_verdicts"] = {
            tenant.tenant_id: loop.first_stop_verdict(tenant.tenant_id)
            for tenant in (first, second)
        }

        # R2/R3 — driver-owned wake samples with the loop paused (a re-stop
        # must not race the wake's health polling; see module docstring).
        loop.pause()
        summary["wake_hook"] = wake_hook(rig, loop)

        # R4 — live-turn protection: a real turn on `first` UNDER the armed loop.
        turn_session = summary["wake_hook"].get("turn_session") or {}
        protection = run_live_turn_protection(
            rig,
            first,
            loop,
            truth_progression,
            session_id=str(turn_session.get("session_id") or ""),
        )
        summary["live_turn_protection"] = protection

        # R5 — after the turn lands, the same loop stops `first` as idle again.
        loop.resume()
        stopped_after = loop.wait_for_state(first.container, "exited", 240.0)
        rec.check(
            "reclaim",
            f"tenant {first.tenant_id}: idle AGAIN after its turn -> stopped by the loop",
            stopped_after,
            f"state={docker_state(first.container)}",
        )
    finally:
        loop.stop()
        summary["classification"] = {
            tenant.tenant_id: classify_loop_passes(loop.passes, tenant.tenant_id)
            for tenant in tenants
        }
        summary["passes"] = loop.passes
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "reclaim_verdicts.json").write_text(dumps(summary), encoding="utf-8")
        (out_dir / "midturn_truth_progression.json").write_text(
            dumps(truth_progression), encoding="utf-8"
        )
    return summary


def run_live_turn_protection(
    rig: Rig,
    tenant: TenantRig,
    loop: ReclaimPolicyLoop,
    truth_progression: list[dict[str, Any]],
    *,
    session_id: str,
) -> dict[str, Any]:
    """One real turn on *tenant* while the policy loop is armed (paused at entry).

    The turn starts under pause; the loop RESUMES when the first engine event
    lands (the engine has a fresh session write by then). Every verdict for
    this tenant during the turn window must KEEP it — and the keep must cite
    the ENGINE truth: with the turn running in-container the proxy ledger is
    stale by design (exactly the IM/cron shape the truth check exists for).
    """
    rec = rig.recorder
    result: dict[str, Any] = {"tenant": tenant.tenant_id, "session_id": session_id}
    if not session_id:
        rec.check(
            "reclaim", "live-turn setup: session available", False, "no session_id"
        )
        return result

    stop_probe = threading.Event()
    result["phase"] = "turn"

    def _probe_loop() -> None:
        while not stop_probe.is_set():
            updated, _raw = probe_engine_truth(tenant.container)
            truth_progression.append(
                {
                    "t": time.time(),
                    "updated_ms": updated,
                    "phase": result.get("phase", "?"),
                }
            )
            stop_probe.wait(3.0)

    prober = threading.Thread(target=_probe_loop, daemon=True)
    prober.start()
    payload: dict[str, Any] = {}

    def _run_turn() -> None:
        run_web_turn(
            rig,
            tenant,
            title="",
            session_id=session_id,
            timeout_s=300.0,
            payload=payload,
        )

    worker = threading.Thread(target=_run_turn, daemon=True)
    worker.start()
    resumed = False
    turn_started_at = time.time()
    deadline = time.monotonic() + 330.0
    while worker.is_alive() and time.monotonic() < deadline:
        if not resumed and payload.get("events"):
            loop.resume()
            resumed = True
            turn_started_at = time.time()
            result["resumed_at_first_event_s"] = round(
                payload["events"][0].get("t_rel", 0.0), 3
            )
        time.sleep(0.2)
    worker.join(timeout=30.0)
    if not resumed:
        loop.resume()
    terminal_at = time.time()
    result["phase"] = "done"
    stop_probe.set()
    prober.join(timeout=10.0)

    result["turn"] = {
        "terminal": payload.get("terminal"),
        "wall_s": payload.get("wall_s"),
        "events": len(payload.get("event_names") or []),
    }
    rec.check(
        "reclaim",
        f"tenant {tenant.tenant_id}: the live turn COMPLETED under the armed policy loop",
        payload.get("terminal") == "attempt.completed",
        f"terminal={payload.get('terminal')} wall={payload.get('wall_s')}s",
    )
    rec.check(
        "reclaim",
        f"tenant {tenant.tenant_id}: container stayed RUNNING through the turn",
        docker_state(tenant.container) == "running",
        str(docker_state(tenant.container)),
    )
    mid_verdicts = loop.verdicts_for(
        tenant.tenant_id, since_t=turn_started_at, until_t=terminal_at
    )
    mid_stops = [d for d in mid_verdicts if d.get("reclaim") is True]
    mid_keeps = [d for d in mid_verdicts if d.get("reclaim") is False]
    rec.check(
        "reclaim",
        f"tenant {tenant.tenant_id}: ZERO stop verdicts while the turn was live",
        not mid_stops,
        f"stops={len(mid_stops)} keeps={len(mid_keeps)}",
    )
    engine_keeps = [d for d in mid_keeps if d.get("truth_source") == "engine"]
    rec.check(
        "reclaim",
        f"tenant {tenant.tenant_id}: live-turn keeps cite the ENGINE truth "
        "(the proxy ledger alone was stale)",
        bool(engine_keeps),
        f"engine_keeps={len(engine_keeps)}/{len(mid_keeps)} "
        f"idle_ms={[d.get('idle_ms') for d in mid_keeps][:6]}",
    )
    during = [
        row
        for row in truth_progression
        if row.get("phase") == "turn" and isinstance(row.get("updated_ms"), int)
    ]
    if during:
        ages = [row["t"] * 1000 - row["updated_ms"] for row in during]
        result["truth_age_ms_during_turn"] = {
            "n": len(ages),
            "min": round(min(ages)),
            "max": round(max(ages)),
        }
        rec.check(
            "reclaim",
            "engine time.updated tracked the live turn (truth age stayed small)",
            max(ages) < 60_000,
            f"age_ms min={round(min(ages))} max={round(max(ages))} n={len(ages)}",
        )
    result["mid_turn_verdicts"] = mid_verdicts
    return result


def ensure_stopped(tenant: TenantRig) -> str:
    """Make sure the container is exited (clean wake sample); return its state."""
    state = docker_state(tenant.container)
    if state == "running":
        docker("stop", tenant.container, timeout=60.0)
        deadline = time.monotonic() + 60.0
        while (
            time.monotonic() < deadline and docker_state(tenant.container) != "exited"
        ):
            time.sleep(1.0)
    return str(docker_state(tenant.container))
