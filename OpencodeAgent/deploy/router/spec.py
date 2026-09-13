"""Typed inputs/outputs of tenant provisioning.

Split out so :mod:`router.templates` (rendering) and :mod:`router.provision`
(orchestration) can both depend on the shapes without depending on each other.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .templates import COMPOSE_FILENAME


@dataclass(frozen=True, slots=True)
class TenantSpec:
    """Everything provisioning needs for one tenant (parsed once at the CLI)."""

    tenant_id: str
    public_host: str
    host_port: int
    image: str
    out_dir: Path
    registry_path: Path
    platform: str = "linux/amd64"
    container_prefix: str = "vt-tenant"
    volume_prefix: str = "vt-tenant"
    upstream: str = ""
    sse_timeout_s: int = 90
    base_env: Path | None = None
    skip_volume: bool = False
    rotate_key: bool = False

    @property
    def container_name(self) -> str:
        return f"{self.container_prefix}-{self.tenant_id}"

    @property
    def home_volume(self) -> str:
        return f"{self.volume_prefix}-{self.tenant_id}-home"

    @property
    def cron_volumes(self) -> tuple[str, str]:
        return (
            f"{self.volume_prefix}-{self.tenant_id}-cron-state",
            f"{self.volume_prefix}-{self.tenant_id}-cron-logs",
        )

    @property
    def effective_upstream(self) -> str:
        """Router-side upstream: explicit, else this host's published port."""
        return (self.upstream or f"http://127.0.0.1:{self.host_port}").rstrip("/")

    @property
    def tenant_dir(self) -> Path:
        return self.out_dir / self.tenant_id

    @property
    def env_file(self) -> Path:
        return self.tenant_dir / "tenant.env"

    @property
    def agent_json(self) -> Path:
        return self.tenant_dir / "agent.json"

    @property
    def compose_file(self) -> Path:
        return self.tenant_dir / COMPOSE_FILENAME


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """Machine-readable provisioning outcome (the E2E driver consumes it)."""

    tenant_id: str
    container: str
    upstream: str
    public_host: str
    home_volume: str
    env_file: str
    compose_file: str
    registry: str
    key_hint: str
    reused_key: bool
    volume_seeded: bool
    docker_commands: tuple[str, ...]

    def to_json(self) -> str:
        """Serialize for the CLI / E2E driver (never contains the key)."""
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)
