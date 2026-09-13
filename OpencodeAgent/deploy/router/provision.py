"""Tenant provisioning: secrets, home volume, channels config, registry entry.

One command turns a tenant id + public host into a runnable T10 tenant
container plus its routing record:

1. **secrets** — generate ``API_AUTH_KEY`` (the tenant gateway's fail-closed
   key, D9/B2) and ``OPENCODE_SERVER_PASSWORD``; both are reused on re-run so a
   re-provision never invalidates a running tenant.
2. **home volume** — create the named volumes and seed the B5 skeleton
   (everything under the volume-mounted ``/home/opencode``), matching T10's
   layout so Settings/``.env``, the runtime root, ``.vt-memory`` and the
   opencode state all live in the volume.
3. **channels config** — render the ``agent.json`` ``channels`` section with
   tenant bot credential PLACEHOLDERS and ``enabled: false`` (see
   :mod:`router.templates`). Real credentials are never written here.
4. **env seeds** — ``LANGCHAIN_*`` (D11) and ``VIBE_TRADING_SSE_TIMEOUT``
   (the frontend watchdog input, ``settings_routes.py:397``).
5. **registry** — register ``token(sha256) -> tenant -> upstream`` and
   ``Host -> tenant`` atomically; the router hot-reloads it.

Re-running is safe: secrets are reused, generated files are deterministic, and
``agent.json`` is written once so operator-filled credentials survive.
"""

from __future__ import annotations

import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .registry import (
    RegistryError,
    TenantRegistry,
    token_hint,
    token_sha256,
    write_registry,
)
from .spec import ProvisionResult, TenantSpec
from .templates import (
    FLEET_FILENAME,
    HOME_SKELETON,
    PASSTHROUGH_ENV,
    read_dotenv,
    render_channels,
    render_compose,
    render_env,
    render_fleet,
)

__all__ = [
    "HOME_SKELETON",
    "PASSTHROUGH_ENV",
    "ProvisionResult",
    "TenantSpec",
    "provision",
    "read_tenant_key",
]


def provision(spec: TenantSpec) -> ProvisionResult:
    """Provision (or re-provision) one tenant. Idempotent."""
    api_key, serve_password, reused = _secrets(spec)
    spec.tenant_dir.mkdir(parents=True, exist_ok=True)
    _write(spec.env_file, render_env(spec, api_key, serve_password), mode=0o600)
    # agent.json is a SEED, written once: an operator who filled in real bot
    # credentials must never have them replaced by placeholders on a re-run.
    if not spec.agent_json.exists():
        _write(
            spec.agent_json,
            json.dumps(render_channels(spec), indent=2, ensure_ascii=False) + "\n",
        )
    _write(spec.compose_file, render_compose(spec))
    commands: tuple[str, ...] = () if spec.skip_volume else _seed_volume(spec)
    _register(spec, api_key)
    return ProvisionResult(
        tenant_id=spec.tenant_id,
        container=spec.container_name,
        upstream=spec.effective_upstream,
        public_host=spec.public_host,
        home_volume=spec.home_volume,
        env_file=str(spec.env_file),
        compose_file=str(spec.compose_file),
        registry=str(spec.registry_path),
        key_hint=token_hint(api_key),
        reused_key=reused,
        volume_seeded=not spec.skip_volume,
        docker_commands=commands,
    )


def read_tenant_key(env_file: Path) -> str:
    """Return the tenant's ``API_AUTH_KEY`` from its generated env file.

    The registry only ever holds the SHA-256, so this file is the one place the
    plaintext key exists — read it here rather than re-deriving anything.
    """
    key = read_dotenv(env_file).get("API_AUTH_KEY", "")
    if not key:
        raise RuntimeError(f"no API_AUTH_KEY in {env_file}")
    return key


def _secrets(spec: TenantSpec) -> tuple[str, str, bool]:
    """Return ``(api_key, serve_password, reused)`` — stable across re-runs."""
    existing = read_dotenv(spec.env_file)
    if existing.get("API_AUTH_KEY") and not spec.rotate_key:
        return (
            existing["API_AUTH_KEY"],
            existing.get("OPENCODE_SERVER_PASSWORD") or secrets.token_hex(16),
            True,
        )
    return secrets.token_hex(32), secrets.token_hex(16), False


def _seed_volume(spec: TenantSpec) -> tuple[str, ...]:
    """Create the volumes and seed the B5 home skeleton + agent.json."""
    cron_state, cron_logs = spec.cron_volumes
    commands = [
        _docker("volume", "create", volume)
        for volume in (spec.home_volume, cron_state, cron_logs)
    ]
    skeleton = " ".join(f"/home/opencode/{path}" for path in HOME_SKELETON)
    # agent.json arrives on stdin, not a file bind mount: Docker Desktop does
    # not reliably share single files, and /tmp is a symlink on macOS. It is
    # written only when absent, so a re-provision never clobbers real bot
    # credentials an operator filled in inside the volume.
    script = (
        f"mkdir -p {skeleton} && "
        "if [ -e /home/opencode/.vibe-trading/agent.json ]; then cat > /dev/null; "
        "else cat > /home/opencode/.vibe-trading/agent.json; fi && "
        "chown -R opencode:opencode /home/opencode"
    )
    commands.append(
        _docker(
            "run",
            "--rm",
            "-i",
            "--platform",
            spec.platform,
            "--user",
            "root",
            "--entrypoint",
            "/bin/bash",
            "-v",
            f"{spec.home_volume}:/home/opencode",
            spec.image,
            "-c",
            script,
            stdin_text=spec.agent_json.read_text(encoding="utf-8"),
        )
    )
    return tuple(commands)


def _docker(*args: str, stdin_text: str | None = None) -> str:
    completed = subprocess.run(
        ["docker", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"docker {args[0]} failed ({completed.returncode}): {completed.stderr.strip()[:400]}"
        )
    return " ".join(["docker", *args])


def _register(spec: TenantSpec, api_key: str) -> None:
    """Merge this tenant into the registry and rewrite it atomically."""
    entries = _read_registry_entries(spec.registry_path)
    existing = entries.get(spec.tenant_id, {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries[spec.tenant_id] = {
        "tenant_id": spec.tenant_id,
        "public_host": spec.public_host,
        "upstream": spec.effective_upstream,
        "container": spec.container_name,
        "volume": spec.home_volume,
        "token_sha256": token_sha256(api_key),
        "token_hint": token_hint(api_key),
        "host_port": spec.host_port,
        "image": spec.image,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    write_registry(spec.registry_path, entries)
    _write_fleet(spec)


def _write_fleet(spec: TenantSpec) -> None:
    """Rewrite the fleet compose (all registered tenants) for one-command up."""
    try:
        registry = TenantRegistry.load(spec.registry_path)
    except RegistryError:
        return
    _write(spec.out_dir / FLEET_FILENAME, render_fleet(spec.out_dir, registry.tenants))


def _read_registry_entries(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("tenants", {}) if isinstance(payload, dict) else {}
    return {
        key: dict(value) for key, value in entries.items() if isinstance(value, dict)
    }


def _write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
