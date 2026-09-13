"""E2E rig for the multi-tenant router (plan T11 acceptance; T12 extends it).

Owns the three things a multi-tenant E2E needs and nothing else:

* the **router process** (real uvicorn, so streaming/first-byte behaviour is
  the production one — the unit tests run through ``ASGITransport``, which
  buffers and therefore cannot measure it);
* the **tenant table** (id, public Host, upstream, container, API key) read
  back from what ``provision_tenant.py`` produced;
* a **check/measurement recorder** that writes the evidence JSON.

Tenant count is parameterized: T12's isolation matrix reuses this rig by
passing more tenants, not by rewriting it.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEPLOY_DIR = Path(__file__).resolve().parent
DEFAULT_PYTHON = sys.executable
# Started through the CLI (not `uvicorn --factory`) so the router's own logger
# is configured: the wake/routing trail is the E2E's evidence.
ROUTER_COMMAND = ("router.cli", "serve")


@dataclass(frozen=True, slots=True)
class TenantRig:
    """One provisioned tenant as the E2E sees it."""

    tenant_id: str
    public_host: str
    upstream: str
    container: str
    host_port: int
    api_key: str

    @classmethod
    def load(
        cls, registry_path: Path, tenants_dir: Path, tenant_id: str
    ) -> "TenantRig":
        """Rebuild a tenant from the registry entry + its generated env file."""
        from router.provision import read_tenant_key
        from router.registry import TenantRegistry

        entry = json.loads(registry_path.read_text(encoding="utf-8"))["tenants"][
            tenant_id
        ]
        registry = TenantRegistry.load(registry_path)
        tenant = registry.tenants[tenant_id]
        return cls(
            tenant_id=tenant_id,
            public_host=tenant.public_host,
            upstream=tenant.upstream,
            container=tenant.container,
            host_port=int(entry.get("host_port", 0)),
            api_key=read_tenant_key(tenants_dir / tenant_id / "tenant.env"),
        )


@dataclass(slots=True)
class Recorder:
    """Check results + timing measurements, serialized to evidence JSON."""

    checks: list[dict[str, object]] = field(default_factory=list)
    measurements: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, group: str, name: str, condition: bool, detail: str = "") -> bool:
        self.checks.append(
            {"group": group, "name": name, "pass": bool(condition), "detail": detail}
        )
        print(
            f"  [{'PASS' if condition else 'FAIL'}] {group}/{name}"
            + (f" — {detail}" if detail else "")
        )
        return bool(condition)

    def measure(
        self, name: str, value: float, unit: str = "s", **extra: object
    ) -> None:
        self.measurements.append(
            {"name": name, "value": round(value, 3), "unit": unit, **extra}
        )
        print(
            f"  [MEASURE] {name} = {value:.3f}{unit}" + (f" {extra}" if extra else "")
        )

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  [note] {text}")

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check["pass"])

    @property
    def total(self) -> int:
        return len(self.checks)

    def dump(self, path: Path, **extra: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "passed": self.passed,
                    "total": self.total,
                    "all_passed": self.passed == self.total,
                    "checks": self.checks,
                    "measurements": self.measurements,
                    "notes": self.notes,
                    **extra,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


class RouterProcess:
    """The router under test, as a real uvicorn subprocess."""

    def __init__(
        self,
        *,
        port: int,
        registry_path: Path,
        log_path: Path,
        admin_key: str,
        python_bin: str = DEFAULT_PYTHON,
        settings_env: dict[str, str] | None = None,
    ) -> None:
        self.port = port
        self.registry_path = registry_path
        self.log_path = log_path
        self.admin_key = admin_key
        self.python_bin = python_bin
        self.settings_env = settings_env or {}
        self.process: subprocess.Popen[bytes] | None = None
        self.booted_at = 0.0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        from router.registry import token_sha256

        environment = dict(os.environ)
        environment.update(
            {
                "VT_ROUTER_REGISTRY": str(self.registry_path),
                "VT_ROUTER_HOST": "127.0.0.1",
                "VT_ROUTER_PORT": str(self.port),
                "VT_ROUTER_ADMIN_TOKEN_SHA256": token_sha256(self.admin_key),
                "PYTHONPATH": str(DEPLOY_DIR),
                # The log is a file, not a tty: without this the subprocess
                # block-buffers stderr and the check that greps the log races
                # the flush.
                "PYTHONUNBUFFERED": "1",
            }
        )
        environment.update(self.settings_env)
        return environment

    def start(self, *, ready_timeout_s: float = 30.0) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("ab")
        handle.write(
            f"\n===== router boot {time.strftime('%H:%M:%S')} env={self.settings_env} =====\n".encode()
        )
        handle.flush()
        self.process = subprocess.Popen(
            [
                self.python_bin,
                "-m",
                *ROUTER_COMMAND,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(DEPLOY_DIR),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.booted_at = time.monotonic()
        deadline = time.monotonic() + ready_timeout_s
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"router exited early ({self.process.returncode}); see {self.log_path}"
                )
            try:
                if (
                    httpx.get(
                        f"{self.base_url}/router-healthz", timeout=2.0
                    ).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(
            f"router did not become ready in {ready_timeout_s}s; see {self.log_path}"
        )

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.process = None
            return
        # Own process, own process group: SIGTERM the group so uvicorn's
        # reloader/children cannot survive, then confirm before SIGKILL.
        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            self.process.wait(timeout=5)
        self.process = None

    def log_text(self) -> str:
        return (
            self.log_path.read_text(encoding="utf-8", errors="replace")
            if self.log_path.exists()
            else ""
        )


@dataclass(slots=True)
class Rig:
    """The whole E2E rig: router + tenants + recorder + docker helpers."""

    router: RouterProcess
    tenants: dict[str, TenantRig]
    recorder: Recorder
    client: httpx.Client

    def headers(
        self, tenant: TenantRig | None, *, host: str | None = None, auth: bool = True
    ) -> dict[str, str]:
        """Headers for one request through the router (public Host preserved)."""
        headers: dict[str, str] = {}
        if tenant is not None and auth:
            headers["Authorization"] = f"Bearer {tenant.api_key}"
        effective_host = (
            host if host is not None else (tenant.public_host if tenant else None)
        )
        if effective_host:
            headers["Host"] = effective_host
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        tenant: TenantRig | None = None,
        host: str | None = None,
        auth: bool = True,
        json_body: object | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """One buffered request through the router, with the tenant's public Host."""
        return self.client.request(
            method,
            f"{self.router.base_url}{path}",
            headers=self._merged_headers(tenant, host, auth, extra_headers),
            json=json_body,
            content=content,
            timeout=timeout,
        )

    def open_stream(
        self,
        path: str,
        *,
        tenant: TenantRig | None = None,
        host: str | None = None,
        auth: bool = True,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 180.0,
    ) -> httpx.Response:
        """Open a streaming request; the caller owns closing the response."""
        built = self.client.build_request(
            "GET",
            f"{self.router.base_url}{path}",
            headers=self._merged_headers(tenant, host, auth, extra_headers),
            timeout=timeout,
        )
        return self.client.send(built, stream=True)

    def _merged_headers(
        self,
        tenant: TenantRig | None,
        host: str | None,
        auth: bool,
        extra: dict[str, str] | None,
    ) -> dict[str, str]:
        merged = self.headers(tenant, host=host, auth=auth)
        merged.update(extra or {})
        return merged

    def admin(self, method: str, path: str, timeout: float = 60.0) -> httpx.Response:
        """Call a router admin endpoint with the rig's admin key."""
        return self.client.request(
            method,
            f"{self.router.base_url}{path}",
            headers={"Authorization": f"Bearer {self.router.admin_key}"},
            timeout=timeout,
        )


def docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout
    )


def docker_state(container: str) -> str | None:
    completed = docker("inspect", "-f", "{{.State.Status}}", container)
    return completed.stdout.strip() if completed.returncode == 0 else None


def docker_rss_mb(container: str) -> float:
    completed = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", container)
    raw = completed.stdout.strip().split("/")[0].strip()
    for suffix, factor in (
        ("GiB", 1024.0),
        ("MiB", 1.0),
        ("KiB", 1 / 1024),
        ("B", 1 / 1024 / 1024),
    ):
        if raw.endswith(suffix):
            return float(raw[: -len(suffix)]) * factor
    return 0.0


def new_admin_key() -> str:
    """A scratch admin key for one E2E run (only its sha256 reaches the router)."""
    return f"e2e-admin-{secrets.token_hex(8)}"


def try_json(raw: str) -> object:
    """Parse JSON, falling back to the truncated raw text (evidence dumps)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw[:2000]
