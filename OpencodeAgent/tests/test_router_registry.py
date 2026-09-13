"""Routing-table tests for the tenant router (plan T11).

Covers the resolution precedence the proxy depends on (token authoritative,
Host fallback, mismatch refused), Host normalization parity with the gateway's
``security.py:_host_without_port``, registry load failures, and the atomic
write used by provisioning. No docker, no network.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from router_harness import KEYS, stack
from router.registry import (
    RegistryError,
    Resolved,
    Tenant,
    TenantMismatch,
    TenantRegistry,
    UnknownHost,
    UnknownToken,
    normalize_host,
    token_hint,
    token_sha256,
    write_registry,
)

TENANT_A = Tenant(
    tenant_id="a",
    public_host="a.tenant.local",
    upstream="http://127.0.0.1:28081",
    container="vt-t11-a",
    volume="vt-t11-a-home",
)
TENANT_B = Tenant(
    tenant_id="b",
    public_host="b.tenant.local",
    upstream="http://127.0.0.1:28082",
    container="vt-t11-b",
    volume="vt-t11-b-home",
)
KEY_A = "key-a-secret"
KEY_B = "key-b-secret"


def make_registry() -> TenantRegistry:
    return TenantRegistry(
        {"a": TENANT_A, "b": TENANT_B},
        {token_sha256(KEY_A): "a", token_sha256(KEY_B): "b"},
    )


def write_two_tenant_registry(path: Path) -> Path:
    write_registry(
        path,
        {
            "a": {
                "tenant_id": "a",
                "public_host": "a.tenant.local",
                "upstream": "http://127.0.0.1:28081/",
                "container": "vt-t11-a",
                "volume": "vt-t11-a-home",
                "token_sha256": token_sha256(KEY_A),
                "token_hint": token_hint(KEY_A),
            },
            "b": {
                "tenant_id": "b",
                "public_host": "b.tenant.local",
                "upstream": "http://127.0.0.1:28082",
                "container": "vt-t11-b",
                "token_sha256": token_sha256(KEY_B),
            },
        },
    )
    return path


# --- resolution precedence --------------------------------------------------


def test_known_token_resolves_its_own_tenant() -> None:
    resolution = make_registry().resolve(token=KEY_B, host=None)

    assert isinstance(resolution, Resolved)
    assert resolution.tenant.tenant_id == "b"
    assert resolution.tenant.upstream == "http://127.0.0.1:28082"


def test_token_is_authoritative_over_an_absent_host() -> None:
    resolution = make_registry().resolve(token=KEY_A, host=None)

    assert resolution == Resolved(tenant=TENANT_A)


def test_registered_host_routes_without_a_token() -> None:
    resolution = make_registry().resolve(token=None, host="b.tenant.local")

    assert resolution == Resolved(tenant=TENANT_B)


def test_unknown_token_with_a_registered_host_falls_back_to_the_host() -> None:
    # The router does not judge credentials: it routes by Host and the tenant
    # gateway returns the 401 (business auth stays out of the router).
    resolution = make_registry().resolve(token="not-registered", host="a.tenant.local")

    assert resolution == Resolved(tenant=TENANT_A)


def test_unregistered_host_without_a_token_is_unknown_host() -> None:
    resolution = make_registry().resolve(token=None, host="evil.example")

    assert resolution == UnknownHost(host="evil.example")


def test_absent_host_and_absent_token_is_unknown_host() -> None:
    resolution = make_registry().resolve(token=None, host=None)

    assert resolution == UnknownHost(host="(absent)")


def test_unknown_token_without_a_routable_host_is_unknown_token() -> None:
    resolution = make_registry().resolve(token="not-registered", host="evil.example")

    assert resolution == UnknownToken(token_hint="ered")


def test_token_and_host_pointing_at_different_tenants_never_guess() -> None:
    resolution = make_registry().resolve(token=KEY_A, host="b.tenant.local")

    assert resolution == TenantMismatch(token_tenant="a", host_tenant="b")


def test_token_and_matching_host_resolve_once() -> None:
    resolution = make_registry().resolve(token=KEY_A, host="a.tenant.local:443")

    assert resolution == Resolved(tenant=TENANT_A)


# --- host normalization (parity with the gateway rule) ----------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A.Tenant.Local", "a.tenant.local"),
        ("a.tenant.local:8080", "a.tenant.local"),
        ("a.tenant.local.", "a.tenant.local"),
        ("  a.tenant.local  ", "a.tenant.local"),
        ("[::1]:8080", "[::1]"),
        ("", ""),
    ],
)
def test_normalize_host_strips_port_and_case(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


def test_resolution_is_port_insensitive_on_the_registered_host() -> None:
    assert make_registry().resolve(host="A.TENANT.LOCAL:28080") == Resolved(
        tenant=TENANT_A
    )


# --- token handling ---------------------------------------------------------


def test_token_digest_is_stable_and_hint_is_not_reversible() -> None:
    assert token_sha256(KEY_A) == token_sha256(KEY_A)
    assert len(token_sha256(KEY_A)) == 64
    assert token_sha256(KEY_A) != token_sha256(KEY_B)
    assert token_hint(KEY_A) == KEY_A[-4:]
    assert token_hint("abc") == "****"


# --- load / write -----------------------------------------------------------


def test_load_round_trips_a_written_registry(tmp_path: Path) -> None:
    path = write_two_tenant_registry(tmp_path / "tenant_registry.json")

    registry = TenantRegistry.load(path)

    assert set(registry.tenants) == {"a", "b"}
    assert registry.resolve(token=KEY_A) == Resolved(tenant=TENANT_A)
    # A trailing slash in the file is normalized away so URL joins stay correct.
    assert registry.tenants["a"].upstream == "http://127.0.0.1:28081"


def test_written_registry_never_contains_the_plaintext_token(tmp_path: Path) -> None:
    path = write_two_tenant_registry(tmp_path / "tenant_registry.json")

    raw = path.read_text(encoding="utf-8")

    assert KEY_A not in raw
    assert KEY_B not in raw
    assert token_sha256(KEY_A) in raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_write_is_readable_while_a_previous_version_exists(tmp_path: Path) -> None:
    path = write_two_tenant_registry(tmp_path / "tenant_registry.json")

    write_registry(path, json.loads(path.read_text(encoding="utf-8"))["tenants"])

    assert set(TenantRegistry.load(path).tenants) == {"a", "b"}
    assert not list(tmp_path.glob(".tenant_registry.json.tmp"))


def test_load_missing_file_raises_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="registry not found"):
        TenantRegistry.load(tmp_path / "absent.json")


def test_load_malformed_json_raises_registry_error(tmp_path: Path) -> None:
    path = tmp_path / "tenant_registry.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RegistryError, match="not valid JSON"):
        TenantRegistry.load(path)


def test_load_rejects_a_host_claimed_by_two_tenants() -> None:
    with pytest.raises(RegistryError, match="claimed by two tenants"):
        TenantRegistry(
            {"a": TENANT_A, "b": Tenant("b", "a.tenant.local", "http://x", "c-b")},
            {token_sha256(KEY_A): "a", token_sha256(KEY_B): "b"},
        )


def test_load_rejects_a_tenant_id_disagreeing_with_its_key() -> None:
    with pytest.raises(RegistryError, match="!= tenant_id"):
        TenantRegistry({"a": TENANT_B}, {token_sha256(KEY_B): "a"})


def test_load_rejects_a_token_owned_by_an_unknown_tenant() -> None:
    with pytest.raises(RegistryError, match="unknown tenant"):
        TenantRegistry(
            {"a": TENANT_A}, {token_sha256(KEY_A): "a", token_sha256(KEY_B): "ghost"}
        )


def test_load_rejects_a_tenant_without_a_token() -> None:
    with pytest.raises(RegistryError, match="exactly one token"):
        TenantRegistry({"a": TENANT_A, "b": TENANT_B}, {token_sha256(KEY_A): "a"})


def test_load_rejects_a_short_digest(tmp_path: Path) -> None:
    path = tmp_path / "tenant_registry.json"
    entry = {
        "tenant_id": "a",
        "public_host": "a.tenant.local",
        "upstream": "http://127.0.0.1:28081",
        "container": "vt-t11-a",
        "token_sha256": "abc",
    }
    path.write_text(
        json.dumps({"version": 1, "tenants": {"a": entry}}), encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="no valid token_sha256"):
        TenantRegistry.load(path)


def test_load_rejects_a_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "tenant_registry.json"
    path.write_text(
        json.dumps(
            {"tenants": {"a": {"tenant_id": "a", "token_sha256": token_sha256(KEY_A)}}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="missing key"):
        TenantRegistry.load(path)


def test_get_returns_none_for_an_unregistered_tenant() -> None:
    assert make_registry().get("ghost") is None
    assert make_registry().get("a") == TENANT_A


# --- the router's disk-backed table (hot reload / missing file) --------------


def registry_entry(tenant: Tenant, key: str) -> dict[str, object]:
    return {
        "tenant_id": tenant.tenant_id,
        "public_host": tenant.public_host,
        "upstream": tenant.upstream,
        "container": tenant.container,
        "token_sha256": token_sha256(key),
    }


def test_a_missing_registry_file_is_503_not_a_crash(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int]:
        async with stack(registry=None, registry_path=tmp_path / "absent.json") as rig:
            response = await rig.client.get("/sessions", headers=rig.auth("a"))
            health = await rig.client.get("/router-healthz")
            return response.status_code, health.json()["tenants"]

    status, tenants = asyncio.run(scenario())

    assert status == 503
    assert tenants == 0


def test_registry_hot_reloads_when_provisioning_writes_a_new_tenant(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, int]:
        path = tmp_path / "tenant_registry.json"
        write_registry(path, {"a": registry_entry(TENANT_A, KEYS["a"])})
        async with stack(registry=None, registry_path=path) as rig:
            before = (
                await rig.client.get("/sessions", headers=rig.auth("b"))
            ).status_code
            write_registry(
                path,
                {
                    "a": registry_entry(TENANT_A, KEYS["a"]),
                    "b": registry_entry(TENANT_B, KEYS["b"]),
                },
            )
            stat = path.stat()
            os.utime(path, (stat.st_atime, stat.st_mtime + 10))
            after = (
                await rig.client.get("/sessions", headers=rig.auth("b"))
            ).status_code
            return before, after

    before, after = asyncio.run(scenario())

    # A tenant provisioned while the router runs becomes routable with no
    # restart (the E2E depends on this).
    assert before == 401
    assert after == 200
