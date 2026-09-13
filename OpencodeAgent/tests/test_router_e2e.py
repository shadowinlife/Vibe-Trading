"""Env-gated wrapper around the two-tenant compose E2E.

The default suite stays green with no docker daemon and no provisioned fleet:
the compose-E2E test skips unless ``VT_T11_E2E=1`` plus the rig paths are set.
The one test that always runs pins the property that makes that possible — the
router package builds an ASGI app without touching docker.

Run the gated E2E (needs the T10 image + two provisioned tenants):

    VT_T11_E2E=1 \\
    VT_T11_REGISTRY=/tmp/vt-t11-rig/tenants/tenant_registry.json \\
    VT_T11_TENANTS_DIR=/tmp/vt-t11-rig/tenants \\
    pytest OpencodeAgent/tests/test_router_e2e.py -q
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

E2E_ENABLED = os.environ.get("VT_T11_E2E") == "1"
RIG_REASON = (
    "set VT_T11_E2E=1 with VT_T11_REGISTRY/VT_T11_TENANTS_DIR to run the compose E2E"
)


def test_router_builds_an_app_without_a_docker_daemon(tmp_path: Path) -> None:
    """The package must be importable and constructible with no daemon present.

    ``build_backend`` only resolves the docker binary path (lazily, per call),
    so constructing the app never shells out — which is what keeps the default
    suite docker-free.
    """
    from router.app import create_app
    from router.config import RouterSettings

    app = create_app(RouterSettings(registry_path=tmp_path / "absent.json"))

    assert app is not None
    assert [route.path for route in app.routes].count("/{path:path}") == 1


def test_cli_show_reports_a_provisioned_registry(tmp_path: Path) -> None:
    """``router.cli show`` reads a registry without docker (operator surface)."""
    from router.cli import main
    from router.provision import TenantSpec, provision

    spec = TenantSpec(
        tenant_id="cli",
        public_host="cli.tenant.local",
        host_port=29999,
        image="opencode-serve:v3.0.0-tenant",
        out_dir=tmp_path,
        registry_path=tmp_path / "tenant_registry.json",
        skip_volume=True,
    )
    provision(spec)

    os.environ["VT_ROUTER_REGISTRY"] = str(spec.registry_path)
    try:
        exit_code = main(["show"])
    finally:
        os.environ.pop("VT_ROUTER_REGISTRY", None)

    assert exit_code == 0


@pytest.mark.skipif(not E2E_ENABLED, reason=RIG_REASON)
@pytest.mark.skipif(
    shutil.which("docker") is None, reason="the compose E2E needs a docker daemon"
)
def test_two_tenant_registry_routing_and_wake_chain(tmp_path: Path) -> None:
    """The plan T11 acceptance: 开通两租户后 registry/路由/唤醒全链路脚本通过."""
    import e2e_multi_tenant

    registry = os.environ.get("VT_T11_REGISTRY")
    tenants_dir = os.environ.get("VT_T11_TENANTS_DIR")
    if not registry or not tenants_dir:
        pytest.skip(
            "VT_T11_REGISTRY and VT_T11_TENANTS_DIR must point at a provisioned rig"
        )
    argv = [
        "--registry",
        registry,
        "--tenants-dir",
        tenants_dir,
        "--tenants",
        os.environ.get("VT_T11_TENANTS", "a,b"),
        "--router-port",
        os.environ.get("VT_T11_ROUTER_PORT", "28080"),
        "--out",
        os.environ.get("VT_T11_OUT", str(tmp_path / "t11-e2e")),
    ]
    base_env = os.environ.get("VT_T11_BASE_ENV")
    if base_env:
        argv += ["--base-env", base_env]
    if os.environ.get("VT_T11_SKIP_MODEL") == "1":
        argv.append("--skip-model")

    assert e2e_multi_tenant.main(argv) == 0
