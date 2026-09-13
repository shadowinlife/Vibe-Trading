#!/usr/bin/env python3
"""Two-tenant router E2E (plan T11 acceptance: 开通两租户后 registry/路由/唤醒全链路).

Drives the real chain against the T10 tenant containers:

    provision_tenant.py -> tenant_registry.json -> router (uvicorn) -> tenant
    gateway (container) -> opencode serve -> model

Phases (ordered so the expensive ones run once):

0. re-provision tenant B through the CLI — idempotency + the "provision new
   tenant" link of the acceptance chain;
1. routing table, SSE passthrough + fence, ONE real model turn, the live engine
   truth check, registry hot reload with a freshly provisioned ghost tenant,
   then the cold-start wake (stop B, one inbound request wakes it);
2. restart the router with a short wake budget and prove the timeout fallback
   page against a container that starts but never serves.

T12 extends this by passing more tenants and adding matrix groups — the rig,
the recorder and the measurement hooks are reusable as-is.

Usage:
    python OpencodeAgent/deploy/e2e_multi_tenant.py \\
        --registry /tmp/vt-t11-rig/tenants/tenant_registry.json \\
        --tenants-dir /tmp/vt-t11-rig/tenants --tenants a,b \\
        --out .omo/evidence/opencode-engine-bridge-v2/t11-router

Env-gated: it needs a docker daemon and provisioned tenant containers, so the
pytest wrapper (``OpencodeAgent/tests/test_router_e2e.py``) skips unless
``VT_T11_E2E=1``.
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

from e2e_checks import (  # noqa: E402 — path bootstrap must run first
    check_routing,
    check_sse_headers_and_fence,
    measure_resources,
)
from e2e_turn_checks import check_engine_truth, check_model_turn  # noqa: E402
from e2e_evidence import deregister, teardown, write_bundle  # noqa: E402
from e2e_provisioning import provision_ghost, reprovision  # noqa: E402
from e2e_wake_checks import (  # noqa: E402
    check_cold_start_wake,
    check_hot_reload,
    check_wake_timeout_fallback,
)
from e2e_rig import (  # noqa: E402
    Recorder,
    Rig,
    RouterProcess,
    TenantRig,
    docker,
    new_admin_key,
)

GHOST_TENANT = "c"
SLEEPY_TENANT = "d"


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
    sleepy_container = f"{args.container_prefix}-{SLEEPY_TENANT}"
    exit_code = 1
    aborted = False
    try:
        recorder.note(f"tenants under test: {tenant_ids} (registry {registry_path})")
        reprovision(recorder, args, tenants_dir, registry_path, tenant_ids)
        tenants = {
            name: TenantRig.load(registry_path, tenants_dir, name)
            for name in tenant_ids
        }
        rig = Rig(router=router, tenants=tenants, recorder=recorder, client=client)
        router.start()
        recorder.note(f"router up on {router.base_url} (log {router.log_path})")

        _phase1(rig, args, out_dir, tenants_dir, registry_path)
        _phase2(rig, args, out_dir, tenants_dir, registry_path, sleepy_container)

        exit_code = 0 if recorder.passed == recorder.total else 1
    except Exception as exc:  # noqa: BLE001 — the driver reports and exits non-zero
        recorder.note(f"E2E aborted: {type(exc).__name__}: {exc}")
        aborted = True
        exit_code = 1
    finally:
        router.stop()
        client.close()
        teardown(recorder, registry_path, sleepy_container, tenants_dir)
        write_bundle(
            recorder,
            out_dir,
            registry_path,
            router_port=args.router_port,
            image=args.image,
            tenant_ids=tenant_ids,
            exit_code=exit_code,
            aborted=aborted,
        )
    verdict = "ABORTED" if aborted else ("PASSED" if exit_code == 0 else "FAILED")
    print(
        f"\n=== T11 ROUTER E2E {verdict}: {recorder.passed}/{recorder.total} checks passed ==="
    )
    return exit_code


# --- phases -----------------------------------------------------------------


def _phase1(
    rig: Rig,
    args: argparse.Namespace,
    out_dir: Path,
    tenants_dir: Path,
    registry_path: Path,
) -> None:
    tenants = list(rig.tenants.values())
    print("\n--- G1 routing table ---")
    check_routing(rig)

    print("\n--- G8 resource sample (idle) ---")
    if args.measure_resources:
        measure_resources(rig, "idle")
    else:
        rig.recorder.note("resource sampling skipped (pass --measure-resources)")

    print("\n--- G2/G7 SSE passthrough + fence ---")
    check_sse_headers_and_fence(rig, tenants[0])

    print("\n--- G5 one real model turn ---")
    if args.skip_model:
        rig.recorder.note("model turn skipped (--skip-model)")
    else:
        turn_started = time.monotonic()
        check_model_turn(rig, tenants[0])
        rig.recorder.measure(
            "model_turn_wall_s",
            time.monotonic() - turn_started,
            tenant=tenants[0].tenant_id,
        )

    print("\n--- G6 idle-reclaim truth check against the live engine ---")
    check_engine_truth(rig, tenants[0], out_dir)

    print("\n--- G3a registry hot reload (live provisioning of a ghost tenant) ---")
    check_hot_reload(
        rig,
        GHOST_TENANT,
        args.host_suffix,
        provision=lambda: provision_ghost(
            rig.recorder, args, tenants_dir, registry_path, GHOST_TENANT
        ),
        deregister=lambda: deregister(registry_path, (GHOST_TENANT,)),
    )

    print("\n--- G3b cold-start wake (QA happy path) ---")
    wake_target = tenants[-1]
    if args.measure_resources:
        measure_resources(rig, "before_wake")
    check_cold_start_wake(rig, wake_target, budget_s=args.wake_budget + 60.0)

    print("\n--- G8 resource sample (after wake) ---")
    if args.measure_resources:
        measure_resources(rig, "after_wake")


def _phase2(
    rig: Rig,
    args: argparse.Namespace,
    out_dir: Path,
    tenants_dir: Path,
    registry_path: Path,
    sleepy_container: str,
) -> None:
    """Restart the router with a short wake budget and prove the fallback page."""
    print("\n--- G4 wake timeout fallback page ---")
    created = docker(
        "create",
        "--name",
        sleepy_container,
        "--platform",
        args.platform,
        "--entrypoint",
        "/bin/sleep",
        args.image,
        "600",
    )
    if created.returncode != 0 and "Conflict" in created.stderr:
        docker("rm", "-f", sleepy_container)
        created = docker(
            "create",
            "--name",
            sleepy_container,
            "--platform",
            args.platform,
            "--entrypoint",
            "/bin/sleep",
            args.image,
            "600",
        )
    rig.recorder.check(
        "fallback",
        f"a container that starts but never serves was created ({sleepy_container})",
        created.returncode == 0,
        created.stderr.strip()[:160],
    )
    sleepy = provision_ghost(
        rig.recorder,
        args,
        tenants_dir,
        registry_path,
        SLEEPY_TENANT,
        container_prefix=args.container_prefix,
    )
    if sleepy is None:
        return

    rig.router.stop()
    rig.router.settings_env["VT_ROUTER_WAKE_TIMEOUT_S"] = str(args.short_wake_timeout)
    rig.router.start()
    rig.recorder.note(
        f"router restarted with VT_ROUTER_WAKE_TIMEOUT_S={args.short_wake_timeout}"
    )
    check_wake_timeout_fallback(rig, sleepy, wake_timeout_s=args.short_wake_timeout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="e2e_multi_tenant.py", description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--tenants-dir", type=Path, required=True)
    parser.add_argument(
        "--tenants",
        default="a,b",
        help="comma-separated tenant ids (parameterized for T12)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".omo/evidence/opencode-engine-bridge-v2/t11-router"),
    )
    parser.add_argument("--router-port", type=int, default=28080)
    parser.add_argument(
        "--base-port", type=int, default=28081, help="host port of the first tenant"
    )
    parser.add_argument("--host-suffix", default="t11.tenant.local")
    parser.add_argument("--container-prefix", default="vt-t11")
    parser.add_argument("--volume-prefix", default="vt-t11")
    parser.add_argument("--image", default="opencode-serve:v3.0.0-tenant")
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--base-env", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--wake-budget",
        type=float,
        default=420.0,
        help="router wake timeout for phase 1",
    )
    parser.add_argument(
        "--short-wake-timeout", type=float, default=6.0, help="phase-2 wake timeout"
    )
    parser.add_argument(
        "--skip-model", action="store_true", help="skip the single real model turn"
    )
    parser.add_argument(
        "--measure-resources",
        action="store_true",
        help="sample per-container RSS (T12 measurement hook)",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
