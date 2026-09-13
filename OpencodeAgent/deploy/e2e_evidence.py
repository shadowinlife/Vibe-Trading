"""Evidence bundle + rig teardown for the multi-tenant E2E.

Kept out of the driver so the driver stays a readable phase list. Everything
written here is SANITIZED: the registry snapshot drops ``token_sha256``, and
the generated ``tenant.env`` files (which hold the plaintext scratch keys) are
never copied into the evidence directory.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from e2e_rig import Recorder, docker
from router.registry import write_registry

GHOST_TENANTS = ("c", "d")


def teardown(
    recorder: Recorder, registry_path: Path, sleepy_container: str, tenants_dir: Path
) -> None:
    """Remove the throwaway tenants; the real ones stay provisioned for T12."""
    docker("rm", "-f", sleepy_container)
    deregister(registry_path, GHOST_TENANTS)
    for tenant_id in GHOST_TENANTS:
        shutil.rmtree(tenants_dir / tenant_id, ignore_errors=True)
    recorder.note(
        f"{sleepy_container} removed; ghost tenants de-registered; real tenants left provisioned"
    )


def deregister(registry_path: Path, tenant_ids: tuple[str, ...] | list[str]) -> None:
    """Drop tenants from the registry (atomic rewrite through the router's writer)."""
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = {
        key: value
        for key, value in payload["tenants"].items()
        if key not in tuple(tenant_ids)
    }
    write_registry(registry_path, entries)


def write_bundle(
    recorder: Recorder,
    out_dir: Path,
    registry_path: Path,
    *,
    router_port: int,
    image: str,
    tenant_ids: list[str],
    exit_code: int,
    aborted: bool,
) -> None:
    """Write the sanitized registry snapshot, wake timings and results JSON."""
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    sanitized = {
        "version": payload.get("version"),
        "tenants": {
            key: {
                field: value
                for field, value in entry.items()
                if field != "token_sha256"
            }
            for key, entry in payload.get("tenants", {}).items()
        },
        "note": "token_sha256 stripped; generated keys are scratch and stay in the 0600 tenant.env files",
    }
    (out_dir / "registry_snapshot.json").write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    timings = [
        item
        for item in recorder.measurements
        if "wake" in item["name"] or "sse" in item["name"]
    ]
    (out_dir / "wake_timings.json").write_text(
        json.dumps(timings, indent=2) + "\n", encoding="utf-8"
    )
    recorder.dump(
        out_dir / "e2e_results.json",
        router_port=router_port,
        tenants=tenant_ids,
        image=image,
        exit_code=exit_code,
        aborted=aborted,
        all_passed=exit_code == 0 and not aborted,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
