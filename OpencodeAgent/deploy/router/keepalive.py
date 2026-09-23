"""TTL keepalive one-shot for sandbox (e2b) tenants (plan T18, gate D11 ⓑ).

Why this exists: platform TTL release is real (T10 — a ``timeout=1800``
sandbox was released mid-experiment at expiry), and ``start``-time renewal is
only-if-longer from the *wake* side, so a session that stays active without
ever going through a wake never pushes ``end_at`` forward. Only an active
ladder of ``set-timeout`` calls keeps a running sandbox alive (runbook 7.4:
"活跃会话 keep-alive 用 set_timeout 阶梯续期").

Shape: a one-shot CLI pass (``python -m router.cli keepalive``), mirroring the
existing ``reclaim`` one-shot — cron/systemd friendly, and ordered against
reclaim by the external scheduler (running tenants get refreshed, idle ones
get paused by reclaim, paused ones are retained indefinitely so they need no
refresh at all). No background loop is embedded in the app: the router's
lifecycle passes have always run externally (DEPLOYMENT history), and the
loop cadence belongs to the operator, not the proxy.

Per-tenant isolation: one dead sandbox (probe failure, TTL call failure) is
RECORDED in the pass report and cannot block the other tenants' keepalive.
The process exit code is 1 when any tenant needs operator attention, so a
cron monitor can alert without parsing stdout.

Dependency surface unchanged (decision D7): the state probe reuses the T16
:class:`~router.backend.E2bSandboxBackend` as-is, and the ``set-timeout``
call itself is a subprocess to the same ``e2b-sandbox-ctl`` binary — the
Protocol stays four methods because keepalive is NOT a data-path operation
(the router must never need it while serving), so it is deliberately absent
from :class:`ContainerBackend` and backend.py is not edited.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .backend import BackendUnavailable, E2bSandboxBackend
from .registry import BACKEND_E2B, Tenant

#: Default sandbox TTL ladder step (CLI create/start default is 1800 — the
#: keepalive step must match it so the ratchet is "back to the standard
#: deadline", never a surprise extension).
DEFAULT_TTL_S = 1800

#: One control-plane API call, same budget class as T16's state probe.
#: Never run without a timeout (STATE.md lesson 7).
SET_TIMEOUT_BUDGET_S = 30.0

Outcome = Literal[
    "refreshed",  # running → set-timeout exit 0 (contract honoured)
    "skipped-paused",  # paused == stopped-equivalent: retention is indefinite
    "absent",  # control plane has no such sandbox → reprovision (T17)
    "skip-backend",  # not an e2b tenant (mixed-fleet safe)
    "probe-failed",  # state probe said backend unavailable
    "set-timeout-failed",  # CLI refused/failed/broke its JSON contract
]

#: Outcomes a cron monitor must treat as actionable (non-zero exit).
ATTENTION_OUTCOMES = frozenset({"absent", "probe-failed", "set-timeout-failed"})


@dataclass(frozen=True, slots=True)
class KeepaliveDecision:
    """One tenant's keepalive verdict with the evidence behind it."""

    tenant_id: str
    backend: str
    state: str | None
    outcome: Outcome
    detail: str


def _resolve_ctl(ctl_bin: str | None) -> str:
    """Explicit argument > ``VT_ROUTER_E2B_CTL_BIN`` > the T16 stable-venv path.

    ``E2bSandboxBackend._DEFAULT_CTL`` is read, not re-stated, so the probe
    and the set-timeout call can never drift to two different binaries.
    """
    raw = ctl_bin or os.environ.get("VT_ROUTER_E2B_CTL_BIN", "").strip()
    return os.path.expanduser(raw or E2bSandboxBackend._DEFAULT_CTL)


def _ttl_seconds(ttl_s: int | None) -> int:
    """Explicit argument > ``VT_ROUTER_E2B_TTL_S`` > :data:`DEFAULT_TTL_S`.

    The env read is deliberately localised here, exactly as T16 localised
    ``VT_ROUTER_E2B_CTL_BIN`` inside backend.py: config.py stays the boundary
    for every other knob and is not widened. A malformed or non-positive
    value fails fast — an operator typo must not silently disable the ladder.
    """
    if ttl_s is not None:
        value = int(ttl_s)
    else:
        raw = os.environ.get("VT_ROUTER_E2B_TTL_S", "").strip()
        value = int(raw) if raw else DEFAULT_TTL_S
    if value <= 0:
        raise ValueError(f"VT_ROUTER_E2B_TTL_S must be > 0, got {value}")
    return value


async def _set_timeout(ctl: str, sandbox_id: str, ttl_s: int) -> str:
    """One ``e2b-sandbox-ctl set-timeout`` call; returns '' or the failure detail."""
    args = ("set-timeout", sandbox_id, str(ttl_s))
    try:
        process = await asyncio.create_subprocess_exec(
            ctl,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return f"cannot exec {ctl}: {exc}"
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=SET_TIMEOUT_BUDGET_S
        )
    except TimeoutError:
        process.kill()
        return f"set-timeout timed out after {SET_TIMEOUT_BUDGET_S}s"
    code = process.returncode or 0
    out = stdout.decode("utf-8", "replace")
    if code != 0:
        return _failure_detail(out, stderr)
    try:
        doc = json.loads(out)
    except ValueError:
        return f"set-timeout broke its JSON contract: {out.strip()[:200]!r}"
    if not (isinstance(doc, dict) and doc.get("timeout_set") is True):
        return f"set-timeout returned an unexpected document: {out.strip()[:200]!r}"
    return ""


def _failure_detail(out: str, stderr: bytes) -> str:
    """Prefer the CLI's JSON error envelope, like backend.py's _describe."""
    try:
        doc = json.loads(out)
        return str(doc["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return (stderr.decode("utf-8", "replace") or out).strip()[:200]


async def keepalive_pass(
    tenants: Iterable[Tenant],
    *,
    ctl_bin: str | None = None,
    ttl_s: int | None = None,
) -> list[KeepaliveDecision]:
    """Refresh the TTL of every *running* e2b tenant; never touch the others.

    Paused tenants are skipped by policy, not by luck: their retention is
    indefinite (reserve-paused default forever), and the CLI's set-timeout is
    a pure control-plane call — skipping anyway keeps the pass honest about
    which deadlines it actually moved.
    """
    ctl = _resolve_ctl(ctl_bin)
    ttl = _ttl_seconds(ttl_s)
    probe = E2bSandboxBackend(ctl_bin=ctl)
    decisions: list[KeepaliveDecision] = []
    for tenant in tenants:
        decisions.append(await _keepalive_one(probe, ctl, ttl, tenant))
    return decisions


async def _keepalive_one(
    probe: E2bSandboxBackend, ctl: str, ttl: int, tenant: Tenant
) -> KeepaliveDecision:
    if tenant.backend != BACKEND_E2B:
        return KeepaliveDecision(
            tenant.tenant_id,
            tenant.backend,
            None,
            "skip-backend",
            "keepalive targets e2b tenants only",
        )
    try:
        state = await probe.inspect_state(tenant.container)
    except BackendUnavailable as exc:
        return KeepaliveDecision(
            tenant.tenant_id, tenant.backend, None, "probe-failed", str(exc)
        )
    if state is None:
        return KeepaliveDecision(
            tenant.tenant_id,
            tenant.backend,
            None,
            "absent",
            "control plane has no such sandbox; reprovision (T17)",
        )
    if state != "running":
        # T16 maps e2b "paused" onto "exited" (the stopped-equivalent).
        return KeepaliveDecision(
            tenant.tenant_id,
            tenant.backend,
            state,
            "skipped-paused",
            "paused retention is indefinite; no TTL to refresh",
        )
    failure = await _set_timeout(ctl, tenant.container, ttl)
    if failure:
        return KeepaliveDecision(
            tenant.tenant_id, tenant.backend, state, "set-timeout-failed", failure
        )
    return KeepaliveDecision(
        tenant.tenant_id,
        tenant.backend,
        state,
        "refreshed",
        f"end_at ratcheted to now+{ttl}s (only-if-longer ladder)",
    )
