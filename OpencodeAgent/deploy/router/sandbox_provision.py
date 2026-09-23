"""Sandbox (e2b) tenant provisioning for the router registry (plan T17).

Gate D11 shape (a): runner lifecycle, pause-only, dedicated pool
``opencode-poc``. Split out of :mod:`router.provision` (line-cap discipline);
:mod:`router.provision` re-exports everything here so ``router.provision``
stays the single CLI seam and existing docker imports are untouched.

The recipe is a pure shell-out to ``e2b-sandbox-ctl`` (decision D7 — the e2b
SDK never enters the router): ``create --template <pool>`` claims a pool
sandbox, ``route-wait`` gates on the gateway route propagating (observed floor
band 65-177 s, so the budget defaults to 180 s), then one registry entry is
written — token digest, ``backend: "e2b"``, sandbox id, upstream rendered from
the network-path-agnostic template (see :mod:`router.registry`).

TTL note (handoff to T18, deliberately NOT implemented here): the claimed
sandbox carries the CLI create TTL (end_at = create + timeout, default
1800 s). Keepalive / TTL-refresh and idle auto-pause wiring is T18 scope.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import (
    BACKEND_E2B,
    render_e2b_upstream,
    read_registry_entries,
    token_hint,
    token_sha256,
    write_registry,
)
from .spec import SandboxProvisionResult, SandboxReprovisionResult, SandboxSpec
from .templates import read_dotenv, render_sandbox_env

__all__ = [
    "ProvisionError",
    "SandboxProvisionResult",
    "SandboxReprovisionResult",
    "SandboxSpec",
    "provision_sandbox",
    "reprovision_sandbox",
]

_TTL_NOTE = (
    "sandbox carries the CLI create TTL (default end_at = create + 1800s); "
    "keepalive/TTL refresh and idle auto-pause are T18 scope"
)

#: Same stable-venv default the T16 backend resolves; the two are pinned
#: equal by a test so they cannot drift.
_DEFAULT_CTL_BIN = "~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl"

_KILL_TIMEOUT_S = 60.0


class ProvisionError(RuntimeError):
    """A provisioning step failed; the registry is left untouched."""


def provision_sandbox(spec: SandboxSpec) -> SandboxProvisionResult:
    """Claim a pool sandbox, wait for its route, register it. No partial entry.

    Every failure path leaves the registry untouched: a create failure writes
    nothing, and a route-wait failure kills the claimed sandbox first so a
    half-provisioned tenant never leaks a pool member.
    """
    if not spec.public_host.strip():
        raise ProvisionError("sandbox provisioning needs a public_host")
    entries = read_registry_entries(spec.registry_path)
    if spec.tenant_id in entries:
        raise ProvisionError(
            f"tenant {spec.tenant_id!r} already registered; a re-claim is a "
            "reprovision_sandbox (kill->rebind), not a re-provision"
        )
    ctl = _resolve_ctl(spec)
    api_key, reused = _sandbox_secrets(spec)
    sandbox_id, claim_s = _claim_sandbox(ctl, spec)
    try:
        route = _wait_for_route(ctl, spec, sandbox_id)
    except ProvisionError as exc:
        cleanup = _kill_note(ctl, sandbox_id)
        raise ProvisionError(
            f"{exc}; claimed sandbox cleanup: {cleanup or 'killed'}"
        ) from exc
    upstream = render_e2b_upstream(sandbox_id)
    spec.tenant_dir.mkdir(parents=True, exist_ok=True)
    _write(spec.env_file, render_sandbox_env(spec.tenant_id, api_key), mode=0o600)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries[spec.tenant_id] = {
        "tenant_id": spec.tenant_id,
        "public_host": spec.public_host,
        "upstream": upstream,
        "container": sandbox_id,
        "volume": "",
        "backend": BACKEND_E2B,
        "sandbox_id": sandbox_id,
        "token_sha256": token_sha256(api_key),
        "token_hint": token_hint(api_key),
        "template": spec.template,
        "created_at": now,
        "updated_at": now,
    }
    write_registry(spec.registry_path, entries)
    return SandboxProvisionResult(
        tenant_id=spec.tenant_id,
        backend=BACKEND_E2B,
        sandbox_id=sandbox_id,
        upstream=upstream,
        public_host=spec.public_host,
        registry=str(spec.registry_path),
        env_file=str(spec.env_file),
        key_hint=token_hint(api_key),
        reused_key=reused,
        template=spec.template,
        claim_s=claim_s,
        route_ready_s=route.get("route_ready_s"),
        route_attempts=route.get("attempts"),
        ttl_note=_TTL_NOTE,
    )


def reprovision_sandbox(spec: SandboxSpec) -> SandboxReprovisionResult:
    """The sanctioned kill->rebind recovery (D11: sandbox lost / evicted).

    sandbox_id and upstream change; the token and host bindings do not. The
    old sandbox's kill is best-effort: its outcome is recorded in the result,
    and a failure never reverts the freshly-bound registry entry.
    """
    entries = read_registry_entries(spec.registry_path)
    entry = entries.get(spec.tenant_id)
    if entry is None:
        raise ProvisionError(
            f"tenant {spec.tenant_id!r} is not registered; nothing to reprovision"
        )
    if str(entry.get("backend", "docker")) != BACKEND_E2B:
        raise ProvisionError(
            f"tenant {spec.tenant_id!r} is not an e2b tenant; reprovisioning a "
            "docker tenant through the sandbox path is refused"
        )
    ctl = _resolve_ctl(spec)
    api_key = read_dotenv(spec.env_file).get("API_AUTH_KEY", "")
    if not api_key:
        raise ProvisionError(f"no API_AUTH_KEY in {spec.env_file}; provision first")
    old_sandbox_id = str(entry.get("sandbox_id", ""))
    try:
        sandbox_id, claim_s = _claim_sandbox(ctl, spec)
    except ProvisionError as exc:
        raise ProvisionError(
            f"{exc}; old binding kept ({old_sandbox_id!r} untouched)"
        ) from exc
    try:
        route = _wait_for_route(ctl, spec, sandbox_id)
    except ProvisionError as exc:
        cleanup = _kill_note(ctl, sandbox_id)
        raise ProvisionError(
            f"{exc}; old binding kept ({old_sandbox_id!r} untouched), "
            f"new sandbox cleanup: {cleanup or 'killed'}"
        ) from exc
    upstream = render_e2b_upstream(sandbox_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries[spec.tenant_id] = {
        **entry,
        "upstream": upstream,
        "container": sandbox_id,
        "sandbox_id": sandbox_id,
        "backend": BACKEND_E2B,
        "template": spec.template,
        "updated_at": now,
    }
    try:
        write_registry(spec.registry_path, entries)
    except Exception as exc:
        cleanup = _kill_note(ctl, sandbox_id)
        raise ProvisionError(
            f"registry rebind failed, old binding kept ({old_sandbox_id!r} "
            f"untouched), new sandbox cleanup: {cleanup or 'killed'}: {exc}"
        ) from exc
    kill_error = (
        _kill_note(ctl, old_sandbox_id) if old_sandbox_id else "no old id recorded"
    )
    return SandboxReprovisionResult(
        tenant_id=spec.tenant_id,
        backend=BACKEND_E2B,
        old_sandbox_id=old_sandbox_id,
        sandbox_id=sandbox_id,
        upstream=upstream,
        public_host=str(entry.get("public_host", "")),
        registry=str(spec.registry_path),
        key_hint=token_hint(api_key),
        template=spec.template,
        old_kill_ok=kill_error is None,
        old_kill_error=kill_error or "",
        claim_s=claim_s,
        route_ready_s=route.get("route_ready_s"),
        route_attempts=route.get("attempts"),
        ttl_note=_TTL_NOTE,
    )


def _resolve_ctl(spec: SandboxSpec) -> str:
    """Explicit argument > env > the stable sandbox-ctl venv (T16 order)."""
    ctl = (
        spec.ctl_bin or os.environ.get("VT_ROUTER_E2B_CTL_BIN", "") or _DEFAULT_CTL_BIN
    )
    return os.path.expanduser(ctl)


def _sandbox_secrets(spec: SandboxSpec) -> tuple[str, bool]:
    """Return ``(api_key, reused)`` — the token is stable across re-runs."""
    existing = read_dotenv(spec.env_file)
    if existing.get("API_AUTH_KEY") and not spec.rotate_key:
        return existing["API_AUTH_KEY"], True
    return secrets.token_hex(32), False


def _sandbox_ctl(ctl: str, *args: str, timeout_s: float) -> tuple[int, dict[str, Any]]:
    """One e2b-sandbox-ctl call -> (exit code, strict-JSON stdout document).

    The CLI's contract is exactly one JSON document per call (sandbox-ctl
    README); a broken contract on a zero exit is reported as a synthetic
    non-zero failure (125), never as success, and a hung CLI is bounded by the
    subprocess timeout and reported as UNKNOWN (124) — a false success is the
    one answer provisioning may not give.
    """
    try:
        completed = subprocess.run(
            [ctl, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 124, {}
    except OSError as exc:  # CLI not installed
        raise ProvisionError(f"cannot exec {ctl}: {exc}") from exc
    try:
        doc = json.loads(completed.stdout or "")
    except ValueError:
        doc = None
    if completed.returncode == 0 and not isinstance(doc, dict):
        return 125, {}
    return completed.returncode, doc if isinstance(doc, dict) else {}


def _ctl_message(code: int, doc: dict[str, Any], op: str) -> str:
    """Failure detail: prefer the CLI's JSON error envelope over stderr."""
    error = doc.get("error") if isinstance(doc.get("error"), dict) else None
    message = str((error or {}).get("message", "")).strip()
    return f"e2b-sandbox-ctl {op} failed (exit {code}): {message or 'no error detail'}"


def _claim_sandbox(ctl: str, spec: SandboxSpec) -> tuple[str, float | None]:
    """``create --template <pool>`` -> (sandbox_id, claim_s)."""
    code, doc = _sandbox_ctl(
        ctl, "create", "--template", spec.template, timeout_s=spec.create_timeout_s
    )
    if code != 0:
        raise ProvisionError(_ctl_message(code, doc, "create"))
    sandbox_id = doc.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id.strip():
        raise ProvisionError(f"e2b-sandbox-ctl create returned no sandbox_id: {doc!r}")
    claim = doc.get("claim_s")
    return sandbox_id.strip(), float(claim) if isinstance(claim, (int, float)) else None


def _wait_for_route(ctl: str, spec: SandboxSpec, sandbox_id: str) -> dict[str, Any]:
    """``route-wait`` until the gateway route serves the sandbox."""
    code, doc = _sandbox_ctl(
        ctl,
        "route-wait",
        sandbox_id,
        "--budget",
        str(spec.route_wait_budget_s),
        timeout_s=spec.route_wait_budget_s + 30,
    )
    if code != 0 or doc.get("ready") is not True:
        raise ProvisionError(_ctl_message(code, doc, "route-wait"))
    return doc


def _kill_note(ctl: str, sandbox_id: str) -> str | None:
    """Best-effort ``kill``: None on success, the failure detail otherwise.

    Never raises: every call site sits in an error path and a failed cleanup
    must not mask the original failure (it is recorded in the message instead).
    """
    try:
        code, doc = _sandbox_ctl(ctl, "kill", sandbox_id, timeout_s=_KILL_TIMEOUT_S)
    except ProvisionError as exc:
        return str(exc)
    return None if code == 0 else _ctl_message(code, doc, "kill")


def _write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
