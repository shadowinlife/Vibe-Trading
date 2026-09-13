#!/usr/bin/env python3
"""T12 tenancy matrix + resource measurements (plan T12, Phase-3 exit gate).

Two-tenant acceptance on ONE host, extending T11's rig (``e2e_rig.py``):

    Phase M — isolation matrix (router TTL long, nothing reclaimed):
        M1 cross-key rejection (direct upstream, router bypassed)
        M2 browser-style POST via router -> 201, cross-site -> 403 (B2 recipe)
        M3 SSE ticket scoping (per-gateway, single-use, never cross-tenant)
        M4 session-list isolation (REST)
        M6 process/network/filesystem isolation (in-container probes)
        M5 IM: channels-config cross-greps + concurrent in-container
           MockChannel probes with distinct {channel, chat_id}
        idle-RSS window, ONE web turn on B with a silent-witness stream on A,
        the two concurrent IM turns, store-level isolation over the full state
    Phase R — reclaim policy with an accelerated N (short TTL + policy loop):
        idle tenants stopped unattended -> wake samples -> a LIVE TURN on A is
        kept by the engine truth check -> A stopped again once idle
    Phase W — final wake sample (>=3 samples total, distribution reported)

Cost discipline: at most 4 minimal real turns (2 per tenant); the serve-side
cost of every engine session is swept into ``cost.json`` afterwards.

Usage:
    python OpencodeAgent/deploy/e2e_tenancy_matrix.py \\
        --registry /tmp/vt-t12-rig/tenants/tenant_registry.json \\
        --tenants-dir /tmp/vt-t12-rig/tenants --tenants a,b \\
        --out .omo/evidence/opencode-engine-bridge-v2/t12-tenancy

Env-gated like T11's driver: needs a docker daemon + provisioned tenant
containers (see tenancy_report.md §Reproduction).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

DEPLOY_DIR = Path(__file__).resolve().parent
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

from e2e_im_checks import check_im_sessions_via_gateway, run_im_matrix  # noqa: E402
from e2e_matrix_checks import (  # noqa: E402
    StreamWitness,
    check_browser_post,
    check_cross_key_isolation,
    check_sse_ticket_scoping,
)
from e2e_matrix_evidence import record_rss, record_wakes, write_bundle  # noqa: E402
from e2e_reclaim_checks import ensure_stopped, run_reclaim_phase  # noqa: E402
from e2e_resource_checks import (  # noqa: E402
    RssSampler,
    check_web_turn,
    measure_wake,
    run_web_turn,
    sweep_engine_costs,
)
from e2e_rig import (  # noqa: E402
    Recorder,
    Rig,
    RouterProcess,
    TenantRig,
    docker_state,
    new_admin_key,
)
from e2e_state_checks import (  # noqa: E402
    check_channels_config_isolation,
    check_process_network_isolation,
    check_session_list_isolation,
    check_store_level_isolation,
)
from tenancy_lib import dumps, parse_llm_usage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run every phase and write the evidence bundle."""
    args = _parser().parse_args(argv)
    registry_path = args.registry.expanduser().resolve()
    tenants_dir = args.tenants_dir.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder()
    tenant_ids = [name.strip() for name in args.tenants.split(",") if name.strip()]

    admin_key = new_admin_key()
    router = RouterProcess(
        port=args.router_port,
        registry_path=registry_path,
        log_path=out_dir / "router.log",
        admin_key=admin_key,
        python_bin=args.python,
        settings_env={
            "VT_ROUTER_WAKE_TIMEOUT_S": str(args.wake_budget),
            "VT_ROUTER_IDLE_TTL_S": "86400",
            "VT_ROUTER_CONNECT_TIMEOUT_S": "5",
        },
    )
    client = httpx.Client(timeout=60.0)
    exit_code = 1
    aborted = False
    rss = RssSampler(
        containers={},  # filled after tenants load
        out_csv=out_dir / "rss_samples.csv",
        interval_s=args.rss_interval_s,
    )
    try:
        tenants = {
            name: TenantRig.load(registry_path, tenants_dir, name)
            for name in tenant_ids
        }
        rig = Rig(router=router, tenants=tenants, recorder=recorder, client=client)
        rss.containers = {tid: t.container for tid, t in tenants.items()}
        recorder.note(
            f"T12 tenancy matrix: tenants={tenant_ids} registry={registry_path}"
        )
        _preconditions(rig, args)
        router.start()
        recorder.note(f"router up on {router.base_url} (log {router.log_path})")

        _phase_m(rig, args, out_dir, tenants_dir, rss)
        _phase_r(rig, args, out_dir, rss)
        _phase_w(rig, args, out_dir)

        rss.stop()
        record_rss(recorder, rss, out_dir)
        record_wakes(recorder, out_dir)
        sweep_engine_costs(rig, out_dir)
        exit_code = 0 if recorder.passed == recorder.total else 1
    except Exception as exc:  # noqa: BLE001 — the driver reports and exits non-zero
        recorder.note(f"T12 matrix aborted: {type(exc).__name__}: {exc}")
        aborted = True
        exit_code = 1
        try:
            rss.stop()
        except Exception:  # noqa: BLE001
            pass
    finally:
        router.stop()
        client.close()
        write_bundle(
            recorder, out_dir, registry_path, args, tenant_ids, exit_code, aborted
        )
    verdict = "ABORTED" if aborted else ("PASSED" if exit_code == 0 else "FAILED")
    print(
        f"\n=== T12 TENANCY MATRIX {verdict}: "
        f"{recorder.passed}/{recorder.total} checks passed ==="
    )
    return exit_code


# --- phases -------------------------------------------------------------------


def _preconditions(rig: Rig, args: argparse.Namespace) -> None:
    """Both tenant containers must be running and healthy before the matrix."""
    for tenant in rig.tenants.values():
        state = docker_state(tenant.container)
        if state != "running":
            raise RuntimeError(
                f"tenant container {tenant.container} is {state!r} — start the rig "
                f"(docker compose -f <tenants>/{tenant.tenant_id}/docker-compose.yml up -d)"
            )
        deadline = time.monotonic() + args.health_wait_s
        healthy = False
        while time.monotonic() < deadline:
            try:
                if (
                    httpx.get(f"{tenant.upstream}/health", timeout=5.0).status_code
                    < 400
                ):
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2.0)
        if not healthy:
            raise RuntimeError(
                f"tenant {tenant.tenant_id} gateway never became healthy"
            )
        rig.recorder.note(
            f"tenant {tenant.tenant_id}: container running, /health green"
        )


def _phase_m(
    rig: Rig,
    args: argparse.Namespace,
    out_dir: Path,
    tenants_dir: Path,
    rss: RssSampler,
) -> None:
    tenants = list(rig.tenants.values())
    first, second = tenants[0], tenants[1]

    print("\n--- M1 cross-key rejection (direct upstream) ---")
    check_cross_key_isolation(rig)

    print("\n--- M2 browser-style POST via router (B2 recipe) ---")
    sessions_m2 = check_browser_post(rig)

    print("\n--- M3 SSE ticket scoping ---")
    check_sse_ticket_scoping(rig, sessions_m2)

    print("\n--- M4 session-list isolation (REST) ---")
    own_ids = check_session_list_isolation(rig, sessions_m2)

    print(f"\n--- RSS idle window ({args.idle_window_s}s) ---")
    rss.start("idle")
    time.sleep(args.idle_window_s)

    print("\n--- M6 process/network/filesystem isolation ---")
    check_process_network_isolation(rig, out_dir, own_ids)

    print("\n--- M5 (config level) channels-config isolation ---")
    check_channels_config_isolation(rig, tenants_dir)

    if args.skip_model:
        rig.recorder.note("model turns skipped (--skip-model)")
        rss.set_label("settled")
        return

    print("\n--- Turn 1: web turn on B with a silent witness stream on A ---")
    rss.set_label("active-web-b")
    witness_session = sessions_m2.get(first.tenant_id, "")
    with StreamWitness(rig, first, witness_session) as witness:
        rig.recorder.check(
            "m3",
            f"witness stream on {first.tenant_id} opened (200) before {second.tenant_id}'s turn",
            witness.status == 200,
            f"HTTP {witness.status}",
        )
        payload = run_web_turn(rig, second, title="T12 web turn (active RSS + witness)")
        check_web_turn(rig.recorder, payload, group="turn")
        if payload.get("wall_s") is not None:
            rig.recorder.measure(
                "web_turn_wall_s", float(payload["wall_s"]), tenant=second.tenant_id
            )
        usage = parse_llm_usage(payload.get("events") or [])
        (out_dir / "web_turn_b.json").write_text(dumps(payload), encoding="utf-8")
        if usage:
            (out_dir / "web_turn_llm_usage.json").write_text(
                dumps(usage), encoding="utf-8"
            )
    foreign_frames = [
        (t, chunk[:200].decode("utf-8", "replace"))
        for t, chunk in witness.frames
        if b"event:" in chunk
    ]
    session_leak = payload.get("session_id", "") and payload["session_id"] in (
        witness.raw.decode("utf-8", "replace")
    )
    rig.recorder.check(
        "m3",
        f"{first.tenant_id}'s SSE connection received ZERO events during {second.tenant_id}'s turn",
        not foreign_frames and not session_leak,
        f"frames={len(witness.frames)} event_frames={len(foreign_frames)} "
        f"bytes={len(witness.raw)} session_leak={bool(session_leak)}",
    )

    print("\n--- Turn 2+3: concurrent in-container IM probes (M5 runtime level) ---")
    rss.set_label("active-im")
    results = run_im_matrix(rig, out_dir, wait_s=args.im_wait_s)
    check_im_sessions_via_gateway(rig, results)

    print("\n--- M4 store-level isolation over the full state (web + IM sessions) ---")
    refreshed: dict[str, list[str]] = {}
    for tenant in tenants:
        response = rig.request("GET", "/sessions?limit=200", tenant=tenant)
        refreshed[tenant.tenant_id] = (
            [str(item.get("session_id")) for item in response.json()]
            if response.status_code == 200
            else []
        )
    check_store_level_isolation(rig, refreshed, out_dir)
    rss.set_label("settled")


def _phase_r(
    rig: Rig, args: argparse.Namespace, out_dir: Path, rss: RssSampler
) -> None:
    print("\n--- Phase R: reclaim policy with an accelerated N ---")
    rss.set_label("reclaim")
    tenants = list(rig.tenants.values())
    first, second = tenants[0], tenants[1]
    if args.skip_model:
        rig.recorder.note(
            "reclaim phase skipped (--skip-model: no live turn to protect)"
        )
        return

    def wake_hook(inner_rig: Rig, loop) -> dict:  # type: ignore[no-untyped-def]
        """Wake samples 1+2 with the policy loop paused (see reclaim module)."""
        out: dict[str, object] = {}
        out["wake_b"] = measure_wake(
            inner_rig,
            second,
            trigger="policy-idle-stop",
            budget_s=args.wake_budget + 60,
        )
        wake_a = measure_wake(
            inner_rig, first, trigger="policy-idle-stop", budget_s=args.wake_budget + 60
        )
        out["wake_a"] = wake_a
        out["turn_session"] = {
            "tenant": first.tenant_id,
            "session_id": wake_a.get("session_id", ""),
        }
        return out

    run_reclaim_phase(
        rig,
        out_dir,
        idle_ttl_s=args.reclaim_ttl_s,
        interval_s=args.reclaim_interval_s,
        wake_hook=wake_hook,
    )


def _phase_w(rig: Rig, args: argparse.Namespace, out_dir: Path) -> None:
    print("\n--- Phase W: final wake samples (>=3 total) ---")
    samples = {}
    for tenant in rig.tenants.values():
        state = ensure_stopped(tenant)
        rig.recorder.note(f"phase W: {tenant.container} state before sample: {state}")
        samples[tenant.tenant_id] = measure_wake(
            rig, tenant, trigger="phase-w-stop", budget_s=args.wake_budget + 60
        )
    (out_dir / "wake_sample_manual.json").write_text(dumps(samples), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e_tenancy_matrix.py", description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--tenants-dir", type=Path, required=True)
    parser.add_argument("--tenants", default="a,b")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".omo/evidence/opencode-engine-bridge-v2/t12-tenancy"),
    )
    parser.add_argument("--router-port", type=int, default=28080)
    parser.add_argument("--host-suffix", default="t12.tenant.local")
    parser.add_argument("--image", default="opencode-serve:v3.0.0-tenant")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--health-wait-s", type=float, default=600.0)
    parser.add_argument("--idle-window-s", type=float, default=60.0)
    parser.add_argument("--rss-interval-s", type=float, default=2.5)
    parser.add_argument("--im-wait-s", type=float, default=300.0)
    parser.add_argument("--wake-budget", type=float, default=420.0)
    parser.add_argument("--reclaim-ttl-s", type=float, default=15.0)
    parser.add_argument("--reclaim-interval-s", type=float, default=4.0)
    parser.add_argument("--skip-model", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
