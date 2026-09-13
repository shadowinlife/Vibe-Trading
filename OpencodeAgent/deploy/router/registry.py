"""Tenant registry: ``token -> tenant -> upstream`` and ``Host -> tenant``.

The router's only auth job is token -> tenant resolution (plan T11 Must-NOT: no
business logic — the tenant gateway re-validates the same Bearer key against
its own ``API_AUTH_KEY``). Two consequences shape this module:

* The registry stores a **SHA-256 of each token, never the token itself**. The
  client's ``Authorization`` header is forwarded verbatim, so the router never
  needs the plaintext; a registry leak is not a credential leak.
* Resolution returns a **tagged union** (:data:`Resolution`) that the proxy
  matches exhaustively, so a new outcome cannot be silently swallowed.

``provision_tenant.py`` owns writes (:func:`write_registry`); the router only
reads. ``tenants`` is the single source of truth — the token and Host indexes
are derived at load time, so they can never drift from it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Self

REGISTRY_VERSION = 1


class RegistryError(RuntimeError):
    """The registry file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Tenant:
    """One tenant's routing record."""

    tenant_id: str
    public_host: str
    upstream: str
    container: str
    volume: str = ""

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> Self:
        """Parse one ``tenants`` entry (boundary parse, not validation)."""
        try:
            return cls(
                tenant_id=str(payload["tenant_id"]),
                public_host=normalize_host(str(payload["public_host"])),
                upstream=str(payload["upstream"]).rstrip("/"),
                container=str(payload["container"]),
                volume=str(payload.get("volume", "")),
            )
        except KeyError as exc:
            raise RegistryError(f"tenant entry is missing key {exc}") from exc


# --- Resolution outcomes (matched exhaustively by the proxy) -----------------


@dataclass(frozen=True, slots=True)
class Resolved:
    """Exactly one tenant owns this request."""

    tenant: Tenant


@dataclass(frozen=True, slots=True)
class UnknownHost:
    """No token routed the request and the Host is not registered -> 404."""

    host: str


@dataclass(frozen=True, slots=True)
class UnknownToken:
    """A token was presented but is not in the registry -> 401."""

    token_hint: str


@dataclass(frozen=True, slots=True)
class TenantMismatch:
    """Token and Host both resolve, to DIFFERENT tenants -> 403 (never guess)."""

    token_tenant: str
    host_tenant: str


Resolution = Resolved | UnknownHost | UnknownToken | TenantMismatch


def token_sha256(token: str) -> str:
    """Return the registry key for a plaintext token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    """Return a non-reversible suffix for logs (never the token itself)."""
    return token[-4:] if len(token) > 4 else "****"


def normalize_host(host: str) -> str:
    """Lowercase a Host header and strip its port (mirrors the gateway rule).

    The gateway's ``security.py:_host_without_port`` normalizes the same way
    before matching ``API_ALLOWED_HOSTS``, so router and gateway agree on what
    a Host is.
    """
    value = (host or "").strip().lower().rstrip(".")
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


class TenantRegistry:
    """Immutable routing table with derived token/Host indexes."""

    __slots__ = ("_by_host", "_by_token", "_tenants")

    def __init__(
        self, tenants: Mapping[str, Tenant], tokens: Mapping[str, str]
    ) -> None:
        """Index *tenants* (id -> record) and *tokens* (sha256 -> tenant id).

        Raises:
            RegistryError: A key/Host/token is claimed by two tenants, or a
                tenant id disagrees with its own map key.
        """
        by_host: dict[str, str] = {}
        for tenant_id, tenant in tenants.items():
            if tenant.tenant_id != tenant_id:
                raise RegistryError(
                    f"tenant key {tenant_id!r} != tenant_id {tenant.tenant_id!r}"
                )
            if tenant.public_host in by_host:
                raise RegistryError(
                    f"host {tenant.public_host!r} is claimed by two tenants"
                )
            by_host[tenant.public_host] = tenant_id
        for digest, tenant_id in tokens.items():
            if tenant_id not in tenants:
                raise RegistryError(f"token for unknown tenant {tenant_id!r}")
        if len(set(tokens.values())) != len(tenants):
            raise RegistryError("every tenant must own exactly one token")
        self._tenants: Mapping[str, Tenant] = dict(tenants)
        self._by_host: Mapping[str, str] = by_host
        self._by_token: Mapping[str, str] = dict(tokens)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Read and index ``tenant_registry.json``."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RegistryError(f"registry not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry is not valid JSON ({path}): {exc}") from exc
        if not isinstance(payload, dict):
            raise RegistryError("registry root must be an object")
        entries = payload.get("tenants")
        if not isinstance(entries, dict):
            raise RegistryError("registry 'tenants' must be an object")
        tenants: dict[str, Tenant] = {}
        tokens: dict[str, str] = {}
        for tenant_id, entry in entries.items():
            if not isinstance(entry, dict):
                raise RegistryError(f"tenant {tenant_id!r} must be an object")
            digest = str(entry.get("token_sha256", "")).lower()
            if len(digest) != 64:
                raise RegistryError(f"tenant {tenant_id!r} has no valid token_sha256")
            owner = tokens.setdefault(digest, tenant_id)
            if owner != tenant_id:
                raise RegistryError(
                    f"token_sha256 of {tenant_id!r} collides with {owner!r}"
                )
            tenants[tenant_id] = Tenant.from_json(entry)
        return cls(tenants, tokens)

    @property
    def tenants(self) -> Mapping[str, Tenant]:
        """All registered tenants keyed by tenant id."""
        return self._tenants

    def get(self, tenant_id: str) -> Tenant | None:
        """Return one tenant, or ``None`` when unregistered."""
        return self._tenants.get(tenant_id)

    def resolve(
        self, *, token: str | None = None, host: str | None = None
    ) -> Resolution:
        """Resolve a request to a tenant.

        Precedence (documented, tested):

        1. A **known** token is authoritative. If a registered Host points at a
           different tenant, that is a cross-tenant probe -> :class:`TenantMismatch`.
        2. Otherwise (no token, or a token this router does not know) a
           **registered Host** routes the request. The tenant gateway still owns
           the auth verdict, so a bad token gets its 401 from there.
        3. Neither key resolves: 401 when a token was presented (it is not
           routable), 404 when only an unregistered Host was presented.
        """
        host_key = normalize_host(host or "")
        by_host = self._by_host.get(host_key) if host_key else None
        if token:
            by_token = self._by_token.get(token_sha256(token))
            if by_token is not None:
                if by_host is not None and by_host != by_token:
                    return TenantMismatch(token_tenant=by_token, host_tenant=by_host)
                tenant = self._tenants[by_token]
                return Resolved(tenant=tenant)
            if by_host is None:
                return UnknownToken(token_hint=token_hint(token))
        if by_host is not None:
            return Resolved(tenant=self._tenants[by_host])
        return UnknownHost(host=host_key or "(absent)")


def write_registry(path: Path, tenants: Mapping[str, dict[str, Any]]) -> None:
    """Atomically write the registry file (provisioning side).

    Written through a same-directory temporary file so a reader (the router
    hot-reloading its table) never observes a partial document.
    """
    payload = {"version": REGISTRY_VERSION, "tenants": dict(tenants)}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o600)
    # Fail fast on a table this router could not read back.
    TenantRegistry.load(path)
