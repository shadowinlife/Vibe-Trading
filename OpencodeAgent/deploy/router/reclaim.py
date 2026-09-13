"""Idle reclaim with the engine truth check (opencode-router recipe).

Proxy-side activity records are **not sufficient**: WebSocket/SSE traffic does
not hit the access-log path, so a tenant streaming a long turn can look idle to
the router. opencode-router's fix, copied here: before deleting, ASK the engine
— ``GET /session?limit=1&roots=true`` and adopt ``time.updated``.

Two more postures carried over from the prior art:

* **Fail closed** (openwork's reaper): when no activity truth is available at
  all, the container is KEPT, never reclaimed on a guess.
* **Fenced** (openwork's directory-fence): the stop runs through
  :class:`~router.fence.TenantFence`, so reclaim cannot race an in-flight
  request; a drain that exceeds its budget is recorded, not hidden.

Clocks: opencode reports ``time.updated`` in epoch milliseconds, so the proxy
ledger uses wall-clock milliseconds too — the two are directly comparable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .backend import BackendUnavailable, ContainerBackend
from .fence import TenantFence
from .registry import Tenant

TruthSource = Literal["engine", "proxy", "unavailable"]

SESSION_PROBE_PATH = "/session?limit=1&roots=true"


@dataclass(frozen=True, slots=True)
class ActivityTruth:
    """Where the last-activity timestamp came from."""

    source: TruthSource
    updated_ms: int | None


@dataclass(frozen=True, slots=True)
class ReclaimContext:
    """The collaborators one reclaim pass needs.

    Grouped because ``evaluate_tenant`` / ``reclaim_tenant`` / ``reclaim_idle``
    and both entry points (the admin endpoint and the CLI) all need the same
    set; threading six keyword arguments through each of them is noise.
    """

    backend: ContainerBackend
    fence: TenantFence
    ledger: ActivityLedger
    idle_ttl_ms: int
    drain_timeout_s: float = 30.0
    clock: Callable[[], float] = time.time

    def now_ms(self) -> int:
        """Wall-clock milliseconds, comparable with the engine's ``time.updated``."""
        return int(self.clock() * 1000)


@dataclass(frozen=True, slots=True)
class ReclaimDecision:
    """One tenant's reclaim verdict, with the evidence behind it."""

    tenant_id: str
    reclaim: bool
    reason: str
    truth: ActivityTruth
    idle_ms: int | None
    drained: bool | None = None


class ActivityLedger:
    """Proxy-side last-activity record per tenant (wall-clock milliseconds)."""

    __slots__ = ("_clock", "_last_ms")

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._last_ms: dict[str, int] = {}

    def touch(self, tenant_id: str) -> None:
        """Record activity for *tenant_id* (called on every proxied request)."""
        self._last_ms[tenant_id] = int(self._clock() * 1000)

    def last_ms(self, tenant_id: str) -> int | None:
        """Last proxy-observed activity, or ``None`` when never seen."""
        return self._last_ms.get(tenant_id)

    def snapshot(self) -> dict[str, int]:
        """Copy of the ledger (admin surface / evidence dumps)."""
        return dict(self._last_ms)


async def engine_last_updated_ms(
    backend: ContainerBackend, tenant: Tenant
) -> int | None:
    """Read ``time.updated`` of the engine's most recent root session.

    Returns ``None`` when the engine is unreachable or the payload shape is not
    the documented one — the caller then falls back to the proxy ledger and, if
    that is empty too, fails closed.
    """
    try:
        body = await backend.engine_get(tenant.container, SESSION_PROBE_PATH)
    except BackendUnavailable:
        return None
    if not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    sessions = (
        payload
        if isinstance(payload, list)
        else payload.get("sessions") if isinstance(payload, dict) else None
    )
    if not isinstance(sessions, list) or not sessions:
        return None
    first = sessions[0]
    if not isinstance(first, dict):
        return None
    stamp = (
        first.get("time", {}).get("updated")
        if isinstance(first.get("time"), dict)
        else None
    )
    return int(stamp) if isinstance(stamp, (int, float)) else None


async def evaluate_tenant(tenant: Tenant, context: ReclaimContext) -> ReclaimDecision:
    """Decide whether *tenant* may be reclaimed, without touching it."""
    try:
        state = await context.backend.inspect_state(tenant.container)
    except BackendUnavailable as exc:
        return ReclaimDecision(
            tenant.tenant_id,
            False,
            f"backend unavailable: {exc}",
            ActivityTruth("unavailable", None),
            None,
        )
    if state != "running":
        return ReclaimDecision(
            tenant.tenant_id,
            False,
            f"container state is {state!r}, nothing to reclaim",
            ActivityTruth("unavailable", None),
            None,
        )

    engine_ms = await engine_last_updated_ms(context.backend, tenant)
    proxy_ms = context.ledger.last_ms(tenant.tenant_id)
    candidates = [ms for ms in (engine_ms, proxy_ms) if ms is not None]
    if not candidates:
        return ReclaimDecision(
            tenant.tenant_id,
            False,
            "no activity truth (engine silent, no proxy traffic) — fail closed",
            ActivityTruth("unavailable", None),
            None,
        )
    newest = max(candidates)
    truth = ActivityTruth("engine" if engine_ms is not None else "proxy", newest)
    idle_ms = max(0, context.now_ms() - newest)
    if idle_ms < context.idle_ttl_ms:
        return ReclaimDecision(
            tenant.tenant_id,
            False,
            f"idle {idle_ms}ms < ttl {context.idle_ttl_ms}ms",
            truth,
            idle_ms,
        )
    return ReclaimDecision(
        tenant.tenant_id,
        True,
        f"idle {idle_ms}ms >= ttl {context.idle_ttl_ms}ms",
        truth,
        idle_ms,
    )


async def reclaim_tenant(tenant: Tenant, context: ReclaimContext) -> ReclaimDecision:
    """Evaluate and, when idle, stop the container behind the admission fence."""
    decision = await evaluate_tenant(tenant, context)
    if not decision.reclaim:
        return decision
    disposal = await context.fence.dispose(
        tenant.tenant_id,
        lambda: context.backend.stop(tenant.container),
        drain_timeout_s=context.drain_timeout_s,
    )
    reason = decision.reason + (
        "" if disposal.drained else " (drain timed out; stopped anyway)"
    )
    return ReclaimDecision(
        tenant.tenant_id,
        True,
        reason,
        decision.truth,
        decision.idle_ms,
        drained=disposal.drained,
    )


async def reclaim_idle(
    tenants: list[Tenant], context: ReclaimContext
) -> list[ReclaimDecision]:
    """One reclaim pass over *tenants* (the interval policy belongs to T12)."""
    decisions = [await reclaim_tenant(tenant, context) for tenant in tenants]
    for decision in decisions:
        if decision.reclaim:
            # A reclaimed tenant is stopped, not deleted: clearing the disposal
            # mark lets the next inbound request be admitted and wake it.
            context.fence.reset(decision.tenant_id)
    return decisions
