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


#: Dedicated sandbox pool provisioned for the opencode PoC (gate D11).
DEFAULT_SANDBOX_TEMPLATE = "opencode-poc"

#: The CLI's own create request-timeout is 320 s (sandbox-ctl README); the
#: outer kill must sit above it so the CLI's JSON verdict explains a failure.
DEFAULT_SANDBOX_CREATE_TIMEOUT_S = 360.0

#: Fresh-claim route propagation is a gateway property with an observed floor
#: band of 65-177 s (T8/T9/T15/T16) — 180 s is the CLI default and the budget
#: provisioning waits with.
DEFAULT_ROUTE_WAIT_BUDGET_S = 180


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Everything sandbox provisioning needs for one tenant (plan T17).

    Mirrors :class:`TenantSpec` without its docker-only fields: no host port,
    no compose artifacts, no volume. ``public_host`` is required to provision
    and ignored by reprovisioning (which keeps the existing host binding).
    """

    tenant_id: str
    out_dir: Path
    registry_path: Path
    public_host: str = ""
    template: str = DEFAULT_SANDBOX_TEMPLATE
    ctl_bin: str = ""  # "" -> env VT_ROUTER_E2B_CTL_BIN -> stable venv default
    route_wait_budget_s: int = DEFAULT_ROUTE_WAIT_BUDGET_S
    create_timeout_s: float = DEFAULT_SANDBOX_CREATE_TIMEOUT_S
    rotate_key: bool = False

    @property
    def tenant_dir(self) -> Path:
        return self.out_dir / self.tenant_id

    @property
    def env_file(self) -> Path:
        return self.tenant_dir / "tenant.env"


@dataclass(frozen=True, slots=True)
class SandboxProvisionResult:
    """Machine-readable outcome of provisioning a sandbox tenant."""

    tenant_id: str
    backend: str
    sandbox_id: str
    upstream: str
    public_host: str
    registry: str
    env_file: str
    key_hint: str
    reused_key: bool
    template: str
    claim_s: float | None
    route_ready_s: float | None
    route_attempts: int | None
    ttl_note: str

    def to_json(self) -> str:
        """Serialize for the CLI (never contains the key)."""
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class SandboxReprovisionResult:
    """Outcome of the sanctioned kill->rebind recovery (D11, plan T17).

    The token and host bindings are unchanged by construction; the old
    sandbox's kill is best-effort and its verdict is recorded here — a failed
    kill never reverts the new binding.
    """

    tenant_id: str
    backend: str
    old_sandbox_id: str
    sandbox_id: str
    upstream: str
    public_host: str
    registry: str
    key_hint: str
    template: str
    old_kill_ok: bool
    old_kill_error: str
    claim_s: float | None
    route_ready_s: float | None
    route_attempts: int | None
    ttl_note: str

    def to_json(self) -> str:
        """Serialize for the CLI (never contains the key)."""
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)
