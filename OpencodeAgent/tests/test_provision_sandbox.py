"""Sandbox tenant provisioning (plan T17) — mocked sandbox-ctl, no live calls.

``provision_sandbox`` drives the e2b-sandbox-ctl CLI via subprocess; every
test here scripts canned JSON + exit codes at the ``subprocess.run`` layer
(the T16 mocking pattern moved to the sync side), so the suite stays
docker-free, cluster-free and credential-free.

Pinned contracts:
* create -> route-wait -> registry entry {token_sha256, backend=e2b,
  sandbox_id, container=sandbox_id, rendered upstream}; token plaintext only
  in the 0600 tenant.env;
* error paths write NOTHING into the registry (create exit 3), and a
  route-wait exit 5 kills the just-claimed sandbox first (no orphan leak);
* reprovision (the kill->rebind recovery), fleet exclusion and the operator
  CLI dispatch are pinned in ``test_reprovision_sandbox.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from router import sandbox_provision
from router.provision import (
    ProvisionError,
    SandboxSpec,
    provision_sandbox,
    read_tenant_key,
)
from router.registry import (
    E2B_UPSTREAM_TEMPLATE_ENV,
    TenantRegistry,
    render_e2b_upstream,
    token_sha256,
)

CTL = "/fake/venv/bin/e2b-sandbox-ctl"
SID_OLD = "default--opencode-poc-z8n52"


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


# --- happy path ---------------------------------------------------------------


def test_provision_sandbox_claims_waits_then_registers(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(E2B_UPSTREAM_TEMPLATE_ENV, raising=False)
    fake = fake_ctl(
        monkeypatch,
        {"create": [(0, HAPPY_CREATE)], "route-wait": [(0, HAPPY_ROUTE)]},
    )
    spec = make_sandbox_spec(tmp_path)

    result = provision_sandbox(spec)
    registry = TenantRegistry.load(spec.registry_path)
    entry = json.loads(spec.registry_path.read_text(encoding="utf-8"))["tenants"][
        "acme"
    ]

    assert fake.commands()[:2] == ["create", "route-wait"]
    assert fake.calls[0][0] == [CTL, "create", "--template", "opencode-poc"]
    assert fake.calls[1][0] == [CTL, "route-wait", SID_OLD, "--budget", "180"]
    assert result.sandbox_id == SID_OLD
    assert result.backend == "e2b"
    assert result.upstream == render_e2b_upstream(SID_OLD)
    assert result.claim_s == 0.19
    assert result.route_ready_s == 120.3
    assert registry.tenants["acme"].container == SID_OLD
    assert entry["token_sha256"] == token_sha256(read_tenant_key(spec.env_file))
    assert entry["volume"] == ""
    assert spec.env_file.stat().st_mode & 0o777 == 0o600
    assert read_tenant_key(spec.env_file) not in spec.registry_path.read_text("utf-8")


def test_provision_is_refused_for_an_already_registered_tenant(
    monkeypatch, tmp_path: Path
) -> None:
    fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE)],
            "route-wait": [(0, HAPPY_ROUTE)],
            "kill": [(0, {"killed": True})],
        },
    )
    spec = make_sandbox_spec(tmp_path)
    provision_sandbox(spec)

    with pytest.raises(ProvisionError, match="already registered"):
        provision_sandbox(spec)


def test_route_wait_budget_and_timeouts_reach_the_cli(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch,
        {"create": [(0, HAPPY_CREATE)], "route-wait": [(0, HAPPY_ROUTE)]},
    )
    spec = make_sandbox_spec(tmp_path, route_wait_budget_s=200, create_timeout_s=400)

    provision_sandbox(spec)

    create_kwargs, route_kwargs = fake.calls[0][1], fake.calls[1][1]
    assert create_kwargs["timeout"] == 400
    # the outer kill must sit ABOVE the CLI's own budget (STATE.md lesson).
    assert route_kwargs["timeout"] > 200


def test_custom_template_env_flows_into_the_stored_upstream(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        E2B_UPSTREAM_TEMPLATE_ENV, "https://vpc-gw.internal/{sandbox_id}/4096"
    )
    fake_ctl(
        monkeypatch,
        {"create": [(0, HAPPY_CREATE)], "route-wait": [(0, HAPPY_ROUTE)]},
    )
    spec = make_sandbox_spec(tmp_path)

    result = provision_sandbox(spec)

    assert result.upstream == f"https://vpc-gw.internal/{SID_OLD}/4096"


# --- error paths: no partial entries, no orphans --------------------------------


def test_create_exit_3_fails_clean_with_no_partial_registry_entry(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch, {"create": [(3, error_doc("backend_unavailable", "gateway dead"))]}
    )
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(
        ProvisionError, match="create failed \\(exit 3\\).*gateway dead"
    ):
        provision_sandbox(spec)

    assert not spec.registry_path.exists()
    assert not spec.env_file.exists()
    assert fake.commands() == ["create"]


def test_route_wait_exit_5_kills_the_sandbox_and_writes_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    fake = fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE)],
            "route-wait": [
                (5, error_doc("operation_failed", "route budget exhausted"))
            ],
            "kill": [(0, {"killed": True})],
        },
    )
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(ProvisionError, match="route-wait failed.*cleanup: killed"):
        provision_sandbox(spec)

    assert fake.commands()[-1] == "kill"
    assert fake.calls[-1][0] == [CTL, "kill", SID_OLD]
    assert not spec.registry_path.exists()
    assert not spec.env_file.exists()


def test_route_wait_cleanup_kill_failure_is_recorded_in_the_error(
    monkeypatch, tmp_path: Path
) -> None:
    fake_ctl(
        monkeypatch,
        {
            "create": [(0, HAPPY_CREATE)],
            "route-wait": [(5, error_doc("operation_failed", "exhausted"))],
            "kill": [(4, error_doc("sandbox_absent", "already gone"))],
        },
    )
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(
        ProvisionError, match="cleanup: e2b-sandbox-ctl kill failed \\(exit 4\\)"
    ):
        provision_sandbox(spec)

    assert not spec.registry_path.exists()


def test_broken_json_contract_on_zero_exit_is_never_success(
    monkeypatch, tmp_path: Path
) -> None:
    fake_ctl(monkeypatch, {"create": [(0, "not-json-at-all")]})
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(ProvisionError, match="create failed \\(exit 125\\)"):
        provision_sandbox(spec)

    assert not spec.registry_path.exists()


def test_hung_cli_create_is_reported_as_unknown_never_as_success(
    monkeypatch, tmp_path: Path
) -> None:
    def raise_timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0.0))

    monkeypatch.setattr(sandbox_provision.subprocess, "run", raise_timeout)
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(ProvisionError, match=r"create failed \(exit 124\)"):
        provision_sandbox(spec)

    assert not spec.registry_path.exists()


def test_missing_ctl_binary_is_a_clean_failure(monkeypatch, tmp_path: Path) -> None:
    def raise_oserror(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(sandbox_provision.subprocess, "run", raise_oserror)
    spec = make_sandbox_spec(tmp_path)

    with pytest.raises(ProvisionError, match="cannot exec"):
        provision_sandbox(spec)

    assert not spec.registry_path.exists()
