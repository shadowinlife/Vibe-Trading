"""Admission-fence tests: openwork's ``engine-directory-fence`` pattern.

Disposal is serialized against prompt admission — the tenant-delete vs
in-flight-message race. No docker, no network: the container backend is a fake.
"""

from __future__ import annotations

import asyncio
import json

from router.backend import BackendUnavailable
from router.fence import TenantDisposed, TenantFence
from router.reclaim import (
    ActivityLedger,
)
from router.registry import Tenant

TENANT = Tenant(
    "a", "a.tenant.local", "http://127.0.0.1:28081", "vt-t11-a", "vt-t11-a-home"
)
NOW_MS = 1_800_000_000_000
TTL_MS = 60_000


class FakeBackend:
    """In-memory stand-in for the docker CLI backend."""

    def __init__(
        self,
        state: str | None = "running",
        engine_body: str | None = None,
        inspect_error: str | None = None,
        engine_error: str | None = None,
    ) -> None:
        self.state = state
        self.engine_body = engine_body
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.engine_calls: list[tuple[str, str]] = []
        self.inspect_error = inspect_error
        self.engine_error = engine_error

    async def inspect_state(self, container: str) -> str | None:
        if self.inspect_error:
            raise BackendUnavailable(self.inspect_error)
        return self.state

    async def start(self, container: str) -> None:
        self.started.append(container)
        self.state = "running"

    async def stop(self, container: str) -> None:
        self.stopped.append(container)
        self.state = "exited"

    async def engine_get(self, container: str, path: str) -> str | None:
        if self.engine_error:
            raise BackendUnavailable(self.engine_error)
        self.engine_calls.append((container, path))
        return self.engine_body


def sessions_payload(updated_ms: int) -> str:
    return json.dumps(
        [{"id": "ses_1", "time": {"created": updated_ms - 1000, "updated": updated_ms}}]
    )


def ledger_with(last_ms: int | None) -> ActivityLedger:
    """A ledger whose single recorded activity is *last_ms* (wall-clock ms)."""
    ledger = ActivityLedger(clock=lambda: (last_ms or 0) / 1000)
    if last_ms is not None:
        ledger.touch(TENANT.tenant_id)
    return ledger


# --- fence ------------------------------------------------------------------


def test_admission_counts_inflight_and_release_is_idempotent() -> None:
    async def scenario() -> tuple[int, int, int]:
        fence = TenantFence()
        admission = await fence.enter("a")
        during = fence.inflight("a")
        await admission.release()
        await admission.release()
        return during, fence.inflight("a"), fence.inflight("b")

    during, after, other = asyncio.run(scenario())

    assert (during, after, other) == (1, 0, 0)


def test_dispose_waits_for_the_inflight_request_then_runs_the_action() -> None:
    async def record(order: list[str], label: str) -> str:
        order.append(label)
        return label

    async def scenario() -> dict[str, object]:
        fence = TenantFence()
        admission = await fence.enter("a")
        order: list[str] = []
        disposal = asyncio.create_task(
            fence.dispose("a", lambda: record(order, "disposed"), drain_timeout_s=5.0)
        )
        await asyncio.sleep(0.01)
        order.append("still_inflight" if not disposal.done() else "disposed_early")
        await admission.release()
        outcome = await disposal
        return {
            "order": order,
            "drained": outcome.drained,
            "inflight_at_start": outcome.inflight_at_start,
        }

    result = asyncio.run(scenario())

    assert result["order"] == ["still_inflight", "disposed"]
    assert result["drained"] is True
    assert result["inflight_at_start"] == 1


def test_admission_is_refused_while_a_disposal_is_in_progress() -> None:
    async def scenario() -> list[str]:
        fence = TenantFence()
        admission = await fence.enter("a")
        task = asyncio.create_task(fence.dispose("a", _noop, drain_timeout_s=5.0))
        await asyncio.sleep(0.01)
        outcomes: list[str] = []
        try:
            await fence.enter("a")
            outcomes.append("admitted")
        except TenantDisposed:
            outcomes.append("refused")
        await admission.release()
        await task
        try:
            await fence.enter("a")
            outcomes.append("admitted_after_dispose")
        except TenantDisposed:
            outcomes.append("refused_after_dispose")
        fence.reset("a")
        await (await fence.enter("a")).release()
        outcomes.append("admitted_after_reset")
        return outcomes

    async def _noop() -> None:
        return None

    assert asyncio.run(scenario()) == [
        "refused",
        "refused_after_dispose",
        "admitted_after_reset",
    ]


def test_dispose_proceeds_and_reports_an_undrained_fence_on_timeout() -> None:
    async def scenario() -> tuple[bool, str, bool]:
        fence = TenantFence()
        await fence.enter("a")  # never released: a stuck stream
        disposal = await fence.dispose("a", _value, drain_timeout_s=0.05)
        return disposal.drained, disposal.result, fence.is_disposed("a")

    async def _value() -> str:
        return "stopped"

    drained, result, disposed = asyncio.run(scenario())

    assert drained is False
    assert result == "stopped"
    assert disposed is True


def test_dispose_runs_the_action_even_when_it_raises() -> None:
    async def scenario() -> bool:
        fence = TenantFence()

        async def explode() -> None:
            raise RuntimeError("boom")

        try:
            await fence.dispose("a", explode, drain_timeout_s=1.0)
        except RuntimeError:
            pass
        return fence.is_disposed("a")

    assert asyncio.run(scenario()) is True
