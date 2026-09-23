"""Sandbox tenant reprovisioning (plan T17): the kill->rebind recovery.

D11's answer to a lost/evicted tenant sandbox: claim a NEW sandbox, wait for
its route, atomically rebind sandbox_id + upstream under the SAME token and
host, then kill the old sandbox best-effort. All sandbox-ctl calls are
scripted at the subprocess layer — no CLI, no gateway, no live cluster.
Also pins the operator CLI dispatch (flat docker form byte-identical, sandbox
subcommands exit 1 on clean failures) and the docker-fleet exclusion.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from provision_tenant import main as tenant_cli_main
from router import provision, sandbox_provision
from router.provision import (
    ProvisionError,
    SandboxSpec,
    TenantSpec,
    provision_sandbox,
    read_tenant_key,
    reprovision_sandbox,
)
from router.registry import TenantRegistry, render_e2b_upstream

CTL = "/fake/venv/bin/e2b-sandbox-ctl"
SID_OLD = "default--opencode-poc-z8n52"
SID_NEW = "default--opencode-poc-jwt9h"


class FakeCtlRun:
    """Scripted ``subprocess.run`` stand-in keyed by the CLI subcommand."""

    def __init__(self, script: dict[str, list[tuple[int, object]]]) -> None:
        self.script = {cmd: list(steps) for cmd, steps in script.items()}
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), kwargs))
        cmd = argv[1]
        steps = self.script.get(cmd)
        if steps is None:
            raise AssertionError(f"unscripted sandbox-ctl call: {argv}")
        code, payload = steps.pop(0) if len(steps) > 1 else steps[0]
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    def commands(self) -> list[str]:
        return [argv[1] for argv, _ in self.calls]


def fake_ctl(monkeypatch, script: dict[str, list[tuple[int, object]]]) -> FakeCtlRun:
    fake = FakeCtlRun(script)
    monkeypatch.setattr(sandbox_provision.subprocess, "run", fake)
    return fake


def make_sandbox_spec(
    tmp_path: Path,
    tenant_id: str = "acme",
    *,
    public_host: str | None = None,
    **overrides,
) -> SandboxSpec:
    fields: dict = {
        "tenant_id": tenant_id,
        "out_dir": tmp_path / "tenants",
        "registry_path": tmp_path / "tenants" / "tenant_registry.json",
        "public_host": public_host or f"{tenant_id}.t17.tenant.local",
        "ctl_bin": CTL,
    }
    fields.update(overrides)
    return SandboxSpec(**fields)


HAPPY_CREATE = {"sandbox_id": SID_OLD, "claim_s": 0.19}
HAPPY_ROUTE = {"ready": True, "route_ready_s": 120.3, "attempts": 13}


def error_doc(name: str, message: str) -> dict:
    return {"ok": False, "error": {"code": name, "message": message}}


# --- reprovision (kill -> rebind) ---------------------------------------------


def test_reprovision_rebinds_new_sandbox_keeps_token_and_kills_old(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE), (0, {"sandbox_id": SID_NEW, "claim_s": 0.2})],
            "route-wait": [
                (0, HAPPY_ROUTE),
                (0, {"ready": True, "route_ready_s": 70.1, "attempts": 8}),
            ],
            "kill": [(0, {"killed": True})],
        },
    )
    spec = make_sandbox_spec(tmp_path)
    provision_sandbox(spec)
    before = json.loads(spec.registry_path.read_text(encoding="utf-8"))["tenants"][
        "acme"
    ]
    key = read_tenant_key(spec.env_file)

    result = reprovision_sandbox(make_sandbox_spec(tmp_path, public_host=""))
    registry = TenantRegistry.load(spec.registry_path)
    entry = json.loads(spec.registry_path.read_text("utf-8"))["tenants"]["acme"]

    assert result.old_sandbox_id == SID_OLD
    assert result.sandbox_id == SID_NEW
    assert registry.resolve(token=key).tenant.upstream == render_e2b_upstream(SID_NEW)
    assert entry["token_sha256"] == before["token_sha256"]
    assert entry["public_host"] == before["public_host"]
    assert entry["created_at"] == before["created_at"]
    assert entry["container"] == SID_NEW
    assert fake.commands()[-1] == "kill"
    assert fake.calls[-1][0] == [CTL, "kill", SID_OLD]


def test_reprovision_records_old_kill_failure_as_not_fatal(
    monkeypatch, tmp_path: Path
) -> None:
    fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE), (0, {"sandbox_id": SID_NEW})],
            "route-wait": [(0, HAPPY_ROUTE), (0, HAPPY_ROUTE)],
            "kill": [(5, error_doc("operation_failed", "kill refused"))],
        },
    )
    spec = make_sandbox_spec(tmp_path)
    provision_sandbox(spec)

    result = reprovision_sandbox(spec)
    entry = json.loads(spec.registry_path.read_text("utf-8"))["tenants"]["acme"]

    assert result.old_kill_ok is False
    assert "kill failed (exit 5)" in result.old_kill_error
    assert entry["sandbox_id"] == SID_NEW


def test_reprovision_new_claim_failure_keeps_the_old_binding_intact(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch,
        {
            "create": [
                (0, HAPPY_CREATE),
                (3, error_doc("backend_unavailable", "dead")),
            ],
            "route-wait": [(0, HAPPY_ROUTE)],
            "kill": [(0, {"killed": True})],
        },
    )
    spec = make_sandbox_spec(tmp_path)
    provision_sandbox(spec)

    with pytest.raises(ProvisionError, match="old binding kept"):
        reprovision_sandbox(spec)

    entry = json.loads(spec.registry_path.read_text("utf-8"))["tenants"]["acme"]
    assert entry["sandbox_id"] == SID_OLD
    assert "kill" not in fake.commands()


def test_reprovision_route_wait_failure_keeps_old_binding_and_kills_new(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE), (0, {"sandbox_id": SID_NEW})],
            "route-wait": [
                (0, HAPPY_ROUTE),
                (5, error_doc("operation_failed", "route budget exhausted")),
            ],
            "kill": [(0, {"killed": True})],
        },
    )
    spec = make_sandbox_spec(tmp_path)
    provision_sandbox(spec)

    with pytest.raises(ProvisionError, match="old binding kept.*cleanup: killed"):
        reprovision_sandbox(spec)

    killed = [argv for argv, _ in fake.calls if argv[1] == "kill"]
    assert killed == [[CTL, "kill", SID_NEW]]
    entry = json.loads(spec.registry_path.read_text("utf-8"))["tenants"]["acme"]
    assert entry["sandbox_id"] == SID_OLD


def test_reprovision_refuses_a_docker_tenant_and_an_unknown_tenant(
    tmp_path: Path,
) -> None:
    docker_spec = TenantSpec(
        tenant_id="dock",
        public_host="dock.t17.tenant.local",
        host_port=28091,
        image="opencode-serve:v3.0.0-tenant",
        out_dir=tmp_path / "tenants",
        registry_path=tmp_path / "tenants" / "tenant_registry.json",
        container_prefix="vt-t17",
        volume_prefix="vt-t17",
        skip_volume=True,
    )
    provision.provision(docker_spec)

    with pytest.raises(ProvisionError, match="not an e2b tenant"):
        reprovision_sandbox(make_sandbox_spec(tmp_path, "dock"))
    with pytest.raises(ProvisionError, match="not registered"):
        reprovision_sandbox(make_sandbox_spec(tmp_path, "ghost"))


# --- fleet isolation -------------------------------------------------------------


def test_docker_fleet_file_excludes_sandbox_tenants(
    monkeypatch, tmp_path: Path
) -> None:
    fake_ctl(
        monkeypatch,
        {"create": [(0, HAPPY_CREATE)], "route-wait": [(0, HAPPY_ROUTE)]},
    )
    provision_sandbox(make_sandbox_spec(tmp_path, "acme"))
    provision.provision(
        TenantSpec(
            tenant_id="dock",
            public_host="dock.t17.tenant.local",
            host_port=28092,
            image="opencode-serve:v3.0.0-tenant",
            out_dir=tmp_path / "tenants",
            registry_path=tmp_path / "tenants" / "tenant_registry.json",
            container_prefix="vt-t17",
            volume_prefix="vt-t17",
            skip_volume=True,
        )
    )

    fleet = (tmp_path / "tenants" / "fleet.yml").read_text(encoding="utf-8")

    assert "dock" in fleet
    assert "acme" not in fleet


# --- operator CLI ---------------------------------------------------------------


def test_cli_sandbox_and_reprovision_subcommands(monkeypatch, tmp_path, capsys) -> None:
    fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE), (0, {"sandbox_id": SID_NEW})],
            "route-wait": [(0, HAPPY_ROUTE), (0, HAPPY_ROUTE)],
            "kill": [(0, {"killed": True})],
        },
    )
    out_dir = tmp_path / "tenants"

    code = tenant_cli_main(
        [
            "sandbox",
            "acme",
            "--public-host",
            "acme.t17.tenant.local",
            "--out-dir",
            str(out_dir),
            "--ctl-bin",
            CTL,
            "--print-key",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out.splitlines()
    payload = json.loads("\n".join(printed[:-1]))
    assert payload["backend"] == "e2b" and payload["sandbox_id"] == SID_OLD
    assert printed[-1].startswith("API_AUTH_KEY=")

    code = tenant_cli_main(
        ["reprovision-sandbox", "acme", "--out-dir", str(out_dir), "--ctl-bin", CTL]
    )
    assert code == 0
    entry = json.loads((out_dir / "tenant_registry.json").read_text("utf-8"))[
        "tenants"
    ]["acme"]
    assert entry["sandbox_id"] == SID_NEW


def test_cli_sandbox_error_paths_exit_1(monkeypatch, tmp_path, capsys) -> None:
    fake_ctl(
        monkeypatch, {"create": [(3, error_doc("backend_unavailable", "gateway dead"))]}
    )

    code = tenant_cli_main(
        [
            "sandbox",
            "acme",
            "--public-host",
            "acme.t17.tenant.local",
            "--out-dir",
            str(tmp_path),
            "--ctl-bin",
            CTL,
        ]
    )

    assert code == 1
    assert "create failed (exit 3)" in capsys.readouterr().err
    assert not (tmp_path / "tenant_registry.json").exists()


def test_cli_docker_flow_still_parses_the_flat_form(tmp_path, capsys) -> None:
    code = tenant_cli_main(
        [
            "--tenant",
            "a",
            "--public-host",
            "a.t17.tenant.local",
            "--host-port",
            "28099",
            "--out-dir",
            str(tmp_path),
            "--skip-volume",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["container"] == "vt-tenant-a"
