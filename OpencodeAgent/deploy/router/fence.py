"""Per-tenant admission fence (openwork ``engine-directory-fence`` pattern).

The race this closes: an operator deletes/stops a tenant while a message is in
flight. Without a fence the reply lands on a torn-down stack (or the container
stops mid-turn and the client sees a silent death). openwork serializes
*instance disposal against prompt admission* with a per-directory promise
chain; this is the same shape with an ``asyncio.Condition``:

* :meth:`TenantFence.enter` admits a request, or refuses once disposal started;
* :meth:`TenantFence.dispose` marks the tenant closing, waits for in-flight
  requests to drain, then runs the disposal action and marks it disposed.

Admission is a separate object (:class:`Admission`) rather than a context
manager because an SSE response outlives its handler: the proxy transfers
ownership of the admission to the response body iterator, which releases it
when the stream ends (or the client disconnects).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class TenantDisposed(RuntimeError):
    """The tenant is closing or gone; no new request may be admitted."""

    def __init__(self, tenant_id: str) -> None:
        super().__init__(f"tenant {tenant_id!r} is disposed")
        self.tenant_id = tenant_id


@dataclass(frozen=True, slots=True)
class Disposal(Generic[T]):
    """Outcome of one disposal: the action's result plus drain accounting."""

    result: T
    drained: bool
    waited_s: float
    inflight_at_start: int


class Admission:
    """One in-flight request's claim on a tenant. Release exactly once."""

    __slots__ = ("_fence", "_released", "_tenant_id")

    def __init__(self, fence: "TenantFence", tenant_id: str) -> None:
        self._fence = fence
        self._tenant_id = tenant_id
        self._released = False

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    async def release(self) -> None:
        """Drop the claim and wake a disposal waiting for the drain."""
        if self._released:
            return
        self._released = True
        await self._fence._release(self._tenant_id)


class TenantFence:
    """Serialize disposal against admission, per tenant."""

    __slots__ = ("_closing", "_condition", "_disposed", "_inflight")

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._inflight: dict[str, int] = {}
        self._closing: set[str] = set()
        self._disposed: set[str] = set()

    def inflight(self, tenant_id: str) -> int:
        """Requests currently admitted for *tenant_id*."""
        return self._inflight.get(tenant_id, 0)

    def is_disposed(self, tenant_id: str) -> bool:
        """Whether disposal already completed for *tenant_id*."""
        return tenant_id in self._disposed

    async def enter(self, tenant_id: str) -> Admission:
        """Admit one request, or refuse when the tenant is closing/disposed.

        Raises:
            TenantDisposed: Disposal has been requested for this tenant.
        """
        async with self._condition:
            if tenant_id in self._closing or tenant_id in self._disposed:
                raise TenantDisposed(tenant_id)
            self._inflight[tenant_id] = self.inflight(tenant_id) + 1
            return Admission(self, tenant_id)

    async def _release(self, tenant_id: str) -> None:
        async with self._condition:
            remaining = self.inflight(tenant_id) - 1
            if remaining > 0:
                self._inflight[tenant_id] = remaining
            else:
                self._inflight.pop(tenant_id, None)
            self._condition.notify_all()

    async def dispose(
        self,
        tenant_id: str,
        action: Callable[[], Awaitable[T]],
        *,
        drain_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> Disposal[T]:
        """Drain in-flight requests, run *action*, then mark the tenant gone.

        New admissions are refused from the moment disposal is requested. The
        drain wait is bounded: an operator-initiated disposal must not be
        wedged forever by one stuck stream, so on timeout the action still runs
        and ``Disposal.drained`` reports ``False`` (the caller logs/records it).
        """
        started = clock()
        async with self._condition:
            self._closing.add(tenant_id)
            inflight_at_start = self.inflight(tenant_id)
            drained = True
            try:
                # Condition.wait re-acquires the lock when asyncio.timeout
                # cancels it, so the timeout path leaves the fence consistent.
                async with asyncio.timeout(drain_timeout_s):
                    while self.inflight(tenant_id) > 0:
                        await self._condition.wait()
            except TimeoutError:
                drained = False
        try:
            result = await action()
        finally:
            async with self._condition:
                self._closing.discard(tenant_id)
                self._disposed.add(tenant_id)
                self._inflight.pop(tenant_id, None)
        return Disposal(
            result=result,
            drained=drained,
            waited_s=clock() - started,
            inflight_at_start=inflight_at_start,
        )

    def reset(self, tenant_id: str) -> None:
        """Clear disposal state so a re-provisioned tenant can be admitted."""
        self._closing.discard(tenant_id)
        self._disposed.discard(tenant_id)
        self._inflight.pop(tenant_id, None)
