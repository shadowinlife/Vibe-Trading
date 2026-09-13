"""Provisioning steps of the E2E: the idempotent re-run and the ghost tenant.

Both drive the REAL ``provision_tenant.py`` CLI as a subprocess, so the E2E
proves the operator surface (argv in, artifacts + registry entry out) rather
than an in-process shortcut.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from e2e_rig import Recorder, TenantRig, try_json
from router.provision import read_tenant_key
from router.registry import Resolved, TenantRegistry

DEPLOY_DIR = Path(__file__).resolve().parent
GHOST_PORT = 28099  # nothing listens here; used only as an outbound target


def reprovision(
    recorder: Recorder,
    args: argparse.Namespace,
    tenants_dir: Path,
    registry_path: Path,
    tenant_ids: list[str],
) -> None:
    """Re-run provisioning for the wake tenant: idempotent, key preserved."""
    target = tenant_ids[-1]
    before = read_tenant_key(tenants_dir / target / "tenant.env")
    command = [
        args.python,
        str(DEPLOY_DIR / "provision_tenant.py"),
        "--tenant",
        target,
        "--public-host",
        f"{target}.{args.host_suffix}",
        "--host-port",
        str(args.base_port + tenant_ids.index(target)),
        "--out-dir",
        str(tenants_dir),
        "--registry",
        str(registry_path),
        "--container-prefix",
        args.container_prefix,
        "--volume-prefix",
        args.volume_prefix,
    ]
    if args.base_env:
        command += ["--base-env", str(args.base_env)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    parsed = try_json(completed.stdout)
    payload = parsed if isinstance(parsed, dict) else {}
    recorder.check(
        "provision",
        f"re-provisioning tenant {target} succeeds (idempotent re-run)",
        completed.returncode == 0 and bool(payload),
        completed.stderr.strip()[:200] or f"key_hint={payload.get('key_hint')}",
    )
    after = read_tenant_key(tenants_dir / target / "tenant.env")
    recorder.check(
        "provision",
        "the re-run REUSED the tenant's API_AUTH_KEY (no silent rotation)",
        before == after and payload.get("reused_key") is True,
        f"reused_key={payload.get('reused_key')}",
    )
    resolution = TenantRegistry.load(registry_path).resolve(token=after)
    recorder.check(
        "provision",
        "the registry still resolves the reused key to the same tenant",
        isinstance(resolution, Resolved) and resolution.tenant.tenant_id == target,
        f"resolution={type(resolution).__name__}",
    )


def provision_ghost(
    recorder: Recorder,
    args: argparse.Namespace,
    tenants_dir: Path,
    registry_path: Path,
    tenant_id: str,
    container_prefix: str = "vt-t11-ghost",
) -> TenantRig | None:
    """Provision a throwaway tenant (no volume, unreachable upstream)."""
    completed = subprocess.run(
        [
            args.python,
            str(DEPLOY_DIR / "provision_tenant.py"),
            "--tenant",
            tenant_id,
            "--public-host",
            f"{tenant_id}.{args.host_suffix}",
            "--host-port",
            str(GHOST_PORT),
            "--out-dir",
            str(tenants_dir),
            "--registry",
            str(registry_path),
            "--container-prefix",
            container_prefix,
            "--volume-prefix",
            container_prefix,
            "--skip-volume",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        recorder.note(f"ghost provisioning failed: {completed.stderr.strip()[:200]}")
        return None
    return TenantRig.load(registry_path, tenants_dir, tenant_id)
