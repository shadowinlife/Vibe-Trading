"""Provisioning tests: what one ``provision_tenant.py`` run must produce.

Everything here runs with ``skip_volume=True`` — no docker, no network — so the
default suite stays green on a machine without a daemon (the compose E2E in
``deploy/e2e_multi_tenant.py`` covers the volume seeding and the live chain).

Pinned contracts:
* the generated env carries the fail-closed auth key, the tenant's
  ``API_ALLOWED_HOSTS`` (B2/F6), the D11 ``LANGCHAIN_*`` seeds and
  ``VIBE_TRADING_SSE_TIMEOUT`` (the frontend watchdog input);
* the channels section holds PLACEHOLDERS only and every adapter stays
  disabled, and the file still validates against the agent's own config model;
* ``opencode.json`` is NOT rendered here — the existing tmpl/render_config
  pipeline owns it (``entrypoint.sh``), so provisioning must not fork it;
* the registry stores a token digest, never the plaintext key;
* re-running is idempotent (same key, same bytes, operator edits preserved).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from router.provision import (
    HOME_SKELETON,
    PASSTHROUGH_ENV,
    ProvisionResult,
    TenantSpec,
    provision,
    read_tenant_key,
)
from router.registry import Resolved, TenantRegistry, UnknownToken, token_sha256

HEX64 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_PATTERN = re.compile(r"^__PROVISION_PLACEHOLDER_[A-Z0-9_]+__$")


def make_spec(
    tmp_path: Path,
    tenant_id: str = "a",
    host_port: int = 28081,
    *,
    public_host: str | None = None,
    base_env: Path | None = None,
    upstream: str = "",
    sse_timeout_s: int = 90,
) -> TenantSpec:
    return TenantSpec(
        tenant_id=tenant_id,
        public_host=public_host or f"{tenant_id}.t11.tenant.local",
        host_port=host_port,
        image="opencode-serve:v3.0.0-tenant",
        out_dir=tmp_path / "tenants",
        registry_path=tmp_path / "tenants" / "tenant_registry.json",
        container_prefix="vt-t11",
        volume_prefix="vt-t11",
        upstream=upstream,
        sse_timeout_s=sse_timeout_s,
        base_env=base_env,
        skip_volume=True,
    )


def write_base_env(tmp_path: Path) -> Path:
    path = tmp_path / "base.env"
    path.write_text(
        "\n".join(
            [
                "# operator scratch env",
                "DASHSCOPE_API_KEY=sk-shared-model-key",
                "DASHSCOPE_BASE_URL=https://model.example/v1",
                "CLICKHOUSE_HOST=ch.example",
                "CLICKHOUSE_PASSWORD=ch-secret",
                "TUSHARE_TOKEN=tushare-token",
                # Must NEVER be passed through to a tenant:
                "API_AUTH_KEY=operator-own-key",
                "OPENCODE_SERVER_PASSWORD=operator-own-password",
                "API_ALLOWED_HOSTS=operator.example",
                "DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=secret",
                "TELEGRAM_BOT_TOKEN=123:operator-bot",
                "SOME_UNRELATED_VAR=nope",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


# --- secrets ----------------------------------------------------------------


def test_generated_api_key_is_a_64_hex_secret_and_is_reused_on_rerun(
    tmp_path: Path,
) -> None:
    spec = make_spec(tmp_path)

    first = provision(spec)
    second = provision(spec)

    key = read_tenant_key(spec.env_file)
    assert HEX64.match(key)
    assert first.reused_key is False
    assert second.reused_key is True
    assert first.key_hint == second.key_hint == key[-4:]
    assert key not in json.dumps(json.loads(second.to_json()))


def test_rotate_key_replaces_the_key_and_the_registry_digest(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    provision(spec)
    before = read_tenant_key(spec.env_file)

    provision(replace(spec, rotate_key=True))
    after = read_tenant_key(spec.env_file)
    registry = TenantRegistry.load(spec.registry_path)

    assert before != after
    assert registry.resolve(token=after) == Resolved(tenant=registry.tenants["a"])
    # The rotated key is gone from the table: a presented-but-unknown token
    # with no routable Host is a 401, never a route to the new key's tenant.
    assert registry.resolve(token=before) == UnknownToken(token_hint=before[-4:])


def test_env_file_is_owner_read_only(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)

    provision(spec)

    assert spec.env_file.stat().st_mode & 0o777 == 0o600


# --- rendered env -----------------------------------------------------------


def test_env_seeds_the_fail_closed_auth_recipe(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)

    provision(spec)
    env = parse_env(spec.env_file)

    assert HEX64.match(env["API_AUTH_KEY"])
    assert env["OPENCODE_SERVER_PASSWORD"]
    # B2/F6: the router preserves the public Host, so the gateway must trust it.
    assert spec.public_host in env["API_ALLOWED_HOSTS"].split(",")
    assert env["VIBE_TRADING_CHANNELS_AUTO_START"] == "false"


def test_env_seeds_the_d11_langchain_stack_and_the_sse_watchdog_input(
    tmp_path: Path,
) -> None:
    spec = make_spec(tmp_path, sse_timeout_s=120)

    provision(spec)
    env = parse_env(spec.env_file)

    assert env["LANGCHAIN_PROVIDER"] == "dashscope"
    assert env["LANGCHAIN_MODEL_NAME"] == "qwen3.8-max"
    assert env["LANGCHAIN_TEMPERATURE"] == "0.3"
    # settings_routes.py:397 reads this for the frontend's SSE watchdog.
    assert env["VIBE_TRADING_SSE_TIMEOUT"] == "120"


def test_base_env_passthrough_is_allowlisted_and_never_carries_tenant_secrets(
    tmp_path: Path,
) -> None:
    spec = make_spec(tmp_path, base_env=write_base_env(tmp_path))

    provision(spec)
    env = parse_env(spec.env_file)

    assert env["DASHSCOPE_API_KEY"] == "sk-shared-model-key"
    assert env["CLICKHOUSE_HOST"] == "ch.example"
    assert env["TUSHARE_TOKEN"] == "tushare-token"
    # The operator's own credentials and bot tokens stay out of the tenant.
    assert env["API_AUTH_KEY"] != "operator-own-key"
    assert env["OPENCODE_SERVER_PASSWORD"] != "operator-own-password"
    assert spec.public_host in env["API_ALLOWED_HOSTS"]
    assert "DINGTALK_WEBHOOK" not in env
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "SOME_UNRELATED_VAR" not in env
    assert set(PASSTHROUGH_ENV).isdisjoint(
        {"API_AUTH_KEY", "OPENCODE_SERVER_PASSWORD", "API_ALLOWED_HOSTS"}
    )


def test_a_missing_base_env_is_not_an_error(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, base_env=tmp_path / "absent.env")

    result = provision(spec)

    assert result.tenant_id == "a"
    assert "DASHSCOPE_API_KEY" not in parse_env(spec.env_file)


# --- channels config --------------------------------------------------------


def test_channels_section_holds_placeholders_only_and_starts_nothing(
    tmp_path: Path,
) -> None:
    spec = make_spec(tmp_path, tenant_id="acme")

    provision(spec)
    payload = json.loads(spec.agent_json.read_text(encoding="utf-8"))
    channels = payload["channels"]

    for name in ("telegram", "dingtalk", "feishu"):
        assert channels[name]["enabled"] is False
    credentials = [
        channels["telegram"]["token"],
        channels["dingtalk"]["client_id"],
        channels["dingtalk"]["client_secret"],
        channels["feishu"]["app_id"],
        channels["feishu"]["app_secret"],
    ]
    assert all(PLACEHOLDER_PATTERN.match(value) for value in credentials)
    assert all("ACME" in value for value in credentials)
    # Fail-closed operator list: IM /pairing is rejected until the tenant names
    # its operators.
    assert channels["operators"] == []


def test_rendered_channels_config_validates_against_the_agent_config_model(
    tmp_path: Path,
) -> None:
    from src.config.schema import AgentConfig

    spec = make_spec(tmp_path)
    provision(spec)

    config = AgentConfig.model_validate(
        json.loads(spec.agent_json.read_text(encoding="utf-8"))
    )
    dumped = config.channels.model_dump(mode="json")

    assert dumped["telegram"]["enabled"] is False
    assert dumped["dingtalk"]["client_id"].startswith("__PROVISION_PLACEHOLDER_")
    assert dumped["operators"] == []


def test_provisioning_does_not_render_opencode_json(tmp_path: Path) -> None:
    # The tmpl/render_config pipeline (entrypoint.sh) owns opencode.json; a
    # second renderer here would fork the tool-governance surface.
    spec = make_spec(tmp_path)

    provision(spec)

    assert not list(spec.tenant_dir.glob("*opencode*.json"))
    assert ".opencode" in HOME_SKELETON


def test_home_skeleton_covers_the_b5_volume_layout(tmp_path: Path) -> None:
    # T10's B5 ruling: every stateful path lives under the volume-mounted home.
    assert ".vibe-trading" in HOME_SKELETON
    assert ".vibe-trading/.vt-memory" in HOME_SKELETON
    assert ".opencode" in HOME_SKELETON
    assert ".local/share/opencode" in HOME_SKELETON
    assert all(not path.startswith("/") for path in HOME_SKELETON)


# --- compose ----------------------------------------------------------------


def test_compose_pins_the_t10_port_and_volume_conventions(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, host_port=28081)

    provision(spec)
    compose = spec.compose_file.read_text(encoding="utf-8")

    assert "image: opencode-serve:v3.0.0-tenant" in compose
    assert "container_name: vt-t11-a" in compose
    # The gateway's 8080 is the single published port; serve's 4096 stays
    # container-internal (it appears only as the bridge's base URL).
    assert '"28081:8080"' in compose
    assert "4096:4096" not in compose
    assert '- "4096' not in compose
    assert "OPENCODE_BASE_URL=http://127.0.0.1:4096" in compose
    assert "VIBE_TRADING_HOME=/home/opencode/.vibe-trading" in compose
    assert "VT_MEMORY_BASE_DIR=/home/opencode/.vibe-trading/.vt-memory" in compose
    assert "VIBE_TRADING_ENGINE=opencode" in compose
    assert "vt-t11-a-home:/home/opencode" in compose
    assert str(spec.env_file) in compose
    assert "external: true" in compose
    assert "curl -sf http://localhost:8080/health" in compose


def test_compose_is_valid_yaml_with_external_volumes(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    spec = make_spec(tmp_path)

    provision(spec)
    document = yaml.safe_load(spec.compose_file.read_text(encoding="utf-8"))

    service = document["services"]["vt-t11-a"]
    assert service["ports"] == ["28081:8080"]
    assert set(document["volumes"]) == {
        "vt-t11-a-home",
        "vt-t11-a-cron-state",
        "vt-t11-a-cron-logs",
    }
    assert all(volume["external"] is True for volume in document["volumes"].values())
    assert document["name"] == "vt-t11-a"


# --- registry ---------------------------------------------------------------


def test_registry_entry_routes_by_both_token_and_host(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)

    result = provision(spec)
    registry = TenantRegistry.load(spec.registry_path)
    key = read_tenant_key(spec.env_file)

    tenant = registry.tenants["a"]
    assert registry.resolve(token=key) == Resolved(tenant=tenant)
    assert registry.resolve(host=spec.public_host) == Resolved(tenant=tenant)
    assert tenant.container == "vt-t11-a"
    assert tenant.upstream == result.upstream
    entry = json.loads(spec.registry_path.read_text(encoding="utf-8"))["tenants"]["a"]
    assert entry["token_sha256"] == token_sha256(key)
    assert key not in spec.registry_path.read_text(encoding="utf-8")
    assert spec.registry_path.stat().st_mode & 0o777 == 0o600


def test_two_tenants_land_in_one_registry_and_one_fleet_file(tmp_path: Path) -> None:
    spec_a = make_spec(tmp_path, tenant_id="a", host_port=28081)
    spec_b = make_spec(tmp_path, tenant_id="b", host_port=28082)

    provision(spec_a)
    provision(spec_b)
    registry = TenantRegistry.load(spec_a.registry_path)
    fleet = (tmp_path / "tenants" / "fleet.yml").read_text(encoding="utf-8")

    assert set(registry.tenants) == {"a", "b"}
    assert registry.tenants["a"].upstream == "http://127.0.0.1:28081"
    assert registry.tenants["b"].upstream == "http://127.0.0.1:28082"
    assert registry.tenants["a"].public_host != registry.tenants["b"].public_host
    assert str(spec_a.compose_file) in fleet
    assert str(spec_b.compose_file) in fleet


def test_rerun_is_byte_identical_and_preserves_operator_edits(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    provision(spec)
    compose_before = spec.compose_file.read_bytes()
    env_before = spec.env_file.read_bytes()
    # An operator filled in a real bot credential after provisioning.
    edited = json.loads(spec.agent_json.read_text(encoding="utf-8"))
    edited["channels"]["telegram"]["token"] = "123:real-bot-token"
    edited["channels"]["telegram"]["enabled"] = True
    spec.agent_json.write_text(json.dumps(edited, indent=2), encoding="utf-8")

    provision(spec)

    assert spec.compose_file.read_bytes() == compose_before
    assert spec.env_file.read_bytes() == env_before
    after = json.loads(spec.agent_json.read_text(encoding="utf-8"))
    assert after["channels"]["telegram"]["token"] == "123:real-bot-token"
    assert after["channels"]["telegram"]["enabled"] is True


def test_an_explicit_upstream_overrides_the_host_port_default(tmp_path: Path) -> None:
    spec = make_spec(tmp_path, upstream="http://vt-t11-a:8080/")

    result = provision(spec)
    registry = TenantRegistry.load(spec.registry_path)

    assert result.upstream == "http://vt-t11-a:8080"
    assert registry.tenants["a"].upstream == "http://vt-t11-a:8080"


def test_result_serializes_without_leaking_the_key(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)

    result: ProvisionResult = provision(spec)
    payload = json.loads(result.to_json())

    assert payload["tenant_id"] == "a"
    assert payload["container"] == "vt-t11-a"
    assert payload["home_volume"] == "vt-t11-a-home"
    assert payload["volume_seeded"] is False
    assert payload["docker_commands"] == []
    assert read_tenant_key(spec.env_file) not in result.to_json()
