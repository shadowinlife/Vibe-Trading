"""Sandbox tenant records in the registry (plan T17).

Pins the schema extension (``backend``/``sandbox_id``, optional and defaulting
to the docker shape so existing tables load unchanged), the network-path-agnostic
upstream template (pure configuration: default = the PoC port-forward form,
production = a config swap, zero path assumptions in code), and the proof that
wake/reclaim/fence consume an e2b-backed entry UNCHANGED — they never see a
difference beyond ``build_backend`` selecting the e2b implementation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from router.backend import E2bSandboxBackend, build_backend
from router.config import RouterSettings
from router.fence import TenantDisposed, TenantFence
from router.registry import (
    DEFAULT_E2B_UPSTREAM_TEMPLATE,
    E2B_UPSTREAM_TEMPLATE_ENV,
    RegistryError,
    Resolved,
    Tenant,
    TenantRegistry,
    UnknownToken,
    render_e2b_upstream,
    token_sha256,
    write_registry,
)
from router.reclaim import ActivityLedger, ReclaimContext, evaluate_tenant, reclaim_idle
from router.sandbox_provision import _DEFAULT_CTL_BIN
from router.wake import Awake, wake_tenant
from router_harness import KEYS, StubBackend, json_responder, stack

SID = "default--opencode-poc-z8n52"
KEY_S = "key-sandbox-secret"

E2B_TENANT = Tenant(
    tenant_id="s",
    public_host="s.tenant.local",
    upstream=render_e2b_upstream(SID),
    container=SID,
    volume="",
    backend="e2b",
    sandbox_id=SID,
)


def sandbox_registry_dict(tenant: Tenant = E2B_TENANT) -> dict[str, dict[str, object]]:
    return {
        tenant.tenant_id: {
            "tenant_id": tenant.tenant_id,
            "public_host": tenant.public_host,
            "upstream": tenant.upstream,
            "container": tenant.container,
            "volume": tenant.volume,
            "backend": tenant.backend,
            "sandbox_id": tenant.sandbox_id,
            "token_sha256": token_sha256(KEY_S),
            "token_hint": KEY_S[-4:],
        }
    }


# --- schema extension + backward compatibility --------------------------------


def test_legacy_entries_load_with_the_docker_defaults(tmp_path: Path) -> None:
    path = tmp_path / "tenant_registry.json"
    write_registry(
        path,
        {
            "a": {
                "tenant_id": "a",
                "public_host": "a.tenant.local",
                "upstream": "http://127.0.0.1:28081",
                "container": "vt-t11-a",
                "volume": "vt-t11-a-home",
                "token_sha256": token_sha256(KEYS["a"]),
            }
        },
    )

    tenant = TenantRegistry.load(path).tenants["a"]

    assert tenant.backend == "docker"
    assert tenant.sandbox_id == ""
    assert (tenant.container, tenant.volume) == ("vt-t11-a", "vt-t11-a-home")


def test_e2b_entry_round_trips_and_resolves_by_token_and_host(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tenant_registry.json"
    write_registry(path, sandbox_registry_dict())

    registry = TenantRegistry.load(path)

    assert registry.resolve(token=KEY_S) == Resolved(tenant=E2B_TENANT)
    assert registry.resolve(host="s.tenant.local") == Resolved(tenant=E2B_TENANT)
    assert registry.resolve(token="wrong") == UnknownToken(token_hint="rong")


def test_e2b_entry_requires_a_sandbox_id(tmp_path: Path) -> None:
    entry = sandbox_registry_dict()[E2B_TENANT.tenant_id] | {"sandbox_id": ""}
    path = tmp_path / "tenant_registry.json"
    path.write_text(json.dumps({"version": 1, "tenants": {"s": entry}}), "utf-8")

    with pytest.raises(RegistryError, match="no sandbox_id"):
        TenantRegistry.load(path)


def test_e2b_container_handle_must_equal_the_sandbox_id(tmp_path: Path) -> None:
    entry = sandbox_registry_dict()[E2B_TENANT.tenant_id] | {"container": "vt-stale-a"}
    path = tmp_path / "tenant_registry.json"
    path.write_text(json.dumps({"version": 1, "tenants": {"s": entry}}), "utf-8")

    with pytest.raises(RegistryError, match="container must equal sandbox_id"):
        TenantRegistry.load(path)


def test_e2b_entry_requires_a_resolvable_upstream(tmp_path: Path) -> None:
    entry = sandbox_registry_dict()[E2B_TENANT.tenant_id] | {"upstream": ""}
    path = tmp_path / "tenant_registry.json"
    path.write_text(json.dumps({"version": 1, "tenants": {"s": entry}}), "utf-8")

    with pytest.raises(RegistryError, match="no resolvable upstream"):
        TenantRegistry.load(path)


def test_an_unknown_backend_is_refused(tmp_path: Path) -> None:
    entry = sandbox_registry_dict()[E2B_TENANT.tenant_id] | {"backend": "nomad"}
    path = tmp_path / "tenant_registry.json"
    path.write_text(json.dumps({"version": 1, "tenants": {"s": entry}}), "utf-8")

    with pytest.raises(RegistryError, match="unknown backend"):
        TenantRegistry.load(path)


def test_the_written_registry_never_carries_the_plaintext_token(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tenant_registry.json"
    write_registry(path, sandbox_registry_dict())

    raw = path.read_text(encoding="utf-8")

    assert KEY_S not in raw
    assert token_sha256(KEY_S) in raw
    assert "api_auth_key" not in raw.lower()


# --- network-path-agnostic upstream template -----------------------------------


def test_default_template_is_the_documented_poc_form(monkeypatch) -> None:
    monkeypatch.delenv(E2B_UPSTREAM_TEMPLATE_ENV, raising=False)

    assert (
        DEFAULT_E2B_UPSTREAM_TEMPLATE
        == "http://localhost:8888/kruise/{sandbox_id}/4096"
    )
    assert (
        render_e2b_upstream("ns--pool-x1")
        == "http://localhost:8888/kruise/ns--pool-x1/4096"
    )


def test_the_template_is_pure_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        E2B_UPSTREAM_TEMPLATE_ENV,
        "https://sandbox-peering.internal.example/{sandbox_id}/4096",
    )

    assert (
        render_e2b_upstream("ns--pool-x1")
        == "https://sandbox-peering.internal.example/ns--pool-x1/4096"
    )


def test_a_template_without_the_placeholder_is_a_config_error(monkeypatch) -> None:
    monkeypatch.setenv(E2B_UPSTREAM_TEMPLATE_ENV, "http://gateway.internal/4096")

    with pytest.raises(RegistryError, match=r"must contain"):
        render_e2b_upstream("ns--pool-x1")


def test_an_explicit_template_argument_never_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv(E2B_UPSTREAM_TEMPLATE_ENV, "http://from-env/{sandbox_id}")

    assert (
        render_e2b_upstream("s1", template="http://from-arg/{sandbox_id}")
        == "http://from-arg/s1"
    )


# --- consumer zero-change proofs (wake / reclaim / fence) ----------------------


def make_registry() -> TenantRegistry:
    return TenantRegistry({"s": E2B_TENANT}, {token_sha256(KEY_S): "s"})


def test_wake_uses_the_rendered_upstream_and_the_sandbox_handle() -> None:
    async def scenario() -> tuple[Awake, list[str], list[str]]:
        backend = StubBackend(state="exited")
        probes: list[str] = []

        async def probe(url: str) -> bool:
            probes.append(url)
            return True

        settings = RouterSettings(registry_path=Path("unused.json"))
        outcome = await wake_tenant(E2B_TENANT, backend, probe, settings)
        assert isinstance(outcome, Awake)
        return outcome, backend.started, probes

    outcome, started, probes = asyncio.run(scenario())

    assert started == [SID]
    assert probes == [f"http://localhost:8888/kruise/{SID}/4096/health"]
    assert outcome.started is True


def test_reclaim_stops_the_sandbox_through_the_backend_protocol() -> None:
    async def scenario() -> tuple[str, list[str], list[str]]:
        backend = StubBackend(
            state="running",
            engine_body=json.dumps([{"time": {"updated": 0}}]),
        )
        context = ReclaimContext(
            backend=backend,
            fence=TenantFence(),
            ledger=ActivityLedger(clock=lambda: 10_000.0),
            idle_ttl_ms=1,
        )
        decision = await evaluate_tenant(E2B_TENANT, context)
        assert decision.reclaim is True
        decisions = await reclaim_idle([E2B_TENANT], context)
        return (
            decisions[0].truth.source,
            backend.stopped,
            [handle for handle, _ in backend.engine_calls],
        )

    source, stopped, engine_handles = asyncio.run(scenario())

    assert source == "engine"
    assert stopped == [SID]
    assert set(engine_handles) == {SID}


def test_proxy_routes_a_token_to_the_sandbox_upstream_without_a_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, str]:
        path = tmp_path / "tenant_registry.json"
        write_registry(path, sandbox_registry_dict())
        seen: dict[str, str] = {}

        def respond(request):
            seen["url"] = str(request.url)
            return json_responder()(request)

        async with stack(respond, registry=None, registry_path=path, tenants=()) as rig:
            response = await rig.client.get(
                "/sessions", headers={"Authorization": f"Bearer {KEY_S}"}
            )
            return response.status_code, seen.get("url", "")

    status, url = asyncio.run(scenario())

    assert status == 200
    assert url.startswith(f"http://localhost:8888/kruise/{SID}/4096")


def test_fence_disposal_is_keyed_by_tenant_and_blind_to_the_backend() -> None:
    async def scenario() -> int:
        async with stack(registry=make_registry()) as rig:
            await rig.fence.dispose(E2B_TENANT.tenant_id, lambda: asyncio.sleep(0))
            with pytest.raises(TenantDisposed):
                await rig.fence.enter(E2B_TENANT.tenant_id)
            return (
                await rig.client.get(
                    "/sessions", headers={"Authorization": f"Bearer {KEY_S}"}
                )
            ).status_code

    assert asyncio.run(scenario()) == 410


def test_the_only_e2b_knowledge_consumers_need_is_the_backend_name_dispatch() -> None:
    backend = build_backend("e2b")

    assert isinstance(backend, E2bSandboxBackend)
    assert _DEFAULT_CTL_BIN == E2bSandboxBackend._DEFAULT_CTL
    for name in ("inspect_state", "start", "stop", "engine_get"):
        assert callable(getattr(backend, name))
