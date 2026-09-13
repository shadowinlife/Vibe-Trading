"""Idle-reclaim tests: opencode-router's engine truth check + fail-closed posture.

Proxy-side activity is NOT sufficient (SSE/WS traffic never hits the access
log), so the engine is asked (``GET /session?limit=1&roots=true`` ->
``time.updated``) before anything is reclaimed, and an absent truth fails
CLOSED (keep). The stop itself runs through the fence, so reclaim cannot race
an in-flight request.

No docker, no network: the container backend is a fake.
"""

from __future__ import annotations

import asyncio
import json

from router.backend import BackendUnavailable
from router.fence import TenantFence
from router.reclaim import (
    SESSION_PROBE_PATH,
    ActivityLedger,
    ReclaimContext,
    engine_last_updated_ms,
    evaluate_tenant,
    reclaim_idle,
    reclaim_tenant,
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


# --- engine truth check -----------------------------------------------------


def test_engine_probe_reads_time_updated_from_the_documented_shape() -> None:
    backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 5_000))

    updated = asyncio.run(engine_last_updated_ms(backend, TENANT))

    assert updated == NOW_MS - 5_000
    assert backend.engine_calls == [("vt-t11-a", SESSION_PROBE_PATH)]


def test_engine_probe_accepts_a_wrapped_sessions_list() -> None:
    backend = FakeBackend(
        engine_body=json.dumps({"sessions": [{"time": {"updated": 42}}]})
    )

    assert asyncio.run(engine_last_updated_ms(backend, TENANT)) == 42


def test_engine_probe_returns_none_for_an_empty_session_list() -> None:
    assert (
        asyncio.run(engine_last_updated_ms(FakeBackend(engine_body="[]"), TENANT))
        is None
    )


def test_engine_probe_returns_none_for_an_unparsable_body() -> None:
    assert (
        asyncio.run(
            engine_last_updated_ms(FakeBackend(engine_body="<html>nope</html>"), TENANT)
        )
        is None
    )


def test_engine_probe_returns_none_when_the_backend_is_unavailable() -> None:
    backend = FakeBackend(engine_error="docker is gone")

    assert asyncio.run(engine_last_updated_ms(backend, TENANT)) is None


# --- reclaim decisions ------------------------------------------------------


def make_context(
    backend: FakeBackend,
    *,
    ledger: ActivityLedger | None = None,
    fence: TenantFence | None = None,
    ttl_ms: int = TTL_MS,
    now_ms: int = NOW_MS,
    drain_timeout_s: float = 30.0,
) -> ReclaimContext:
    """A reclaim context frozen at *now_ms* so idleness is deterministic."""
    return ReclaimContext(
        backend=backend,
        fence=fence or TenantFence(),
        ledger=ledger or ActivityLedger(),
        idle_ttl_ms=ttl_ms,
        drain_timeout_s=drain_timeout_s,
        clock=lambda: now_ms / 1000,
    )


def test_fresh_engine_activity_keeps_the_container_even_when_the_proxy_saw_nothing() -> (
    None
):
    # The whole point of the truth check: an SSE-only tenant never touches the
    # proxy ledger, yet the engine reports activity inside the TTL.
    backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 1_000))

    decision = asyncio.run(evaluate_tenant(TENANT, make_context(backend)))

    assert decision.reclaim is False
    assert decision.truth.source == "engine"
    assert decision.idle_ms == 1_000


def test_stale_engine_and_stale_proxy_reclaim() -> None:
    backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 120_000))
    context = make_context(backend, ledger=ledger_with(NOW_MS - 90_000))

    decision = asyncio.run(evaluate_tenant(TENANT, context))

    assert decision.reclaim is True
    # The NEWEST truth wins: the proxy saw activity 90s ago, the engine 120s ago.
    assert decision.idle_ms == 90_000
    assert decision.truth.source == "engine"


def test_silent_engine_falls_back_to_the_proxy_ledger() -> None:
    backend = FakeBackend(engine_body=None)
    context = make_context(backend, ledger=ledger_with(NOW_MS - 10))

    decision = asyncio.run(evaluate_tenant(TENANT, context))

    assert decision.reclaim is False
    assert decision.truth.source == "proxy"


def test_no_truth_at_all_fails_closed_and_keeps_the_container() -> None:
    decision = asyncio.run(evaluate_tenant(TENANT, make_context(FakeBackend())))

    assert decision.reclaim is False
    assert decision.truth.source == "unavailable"
    assert "fail closed" in decision.reason


def test_a_stopped_container_has_nothing_to_reclaim() -> None:
    backend = FakeBackend(
        state="exited", engine_body=sessions_payload(NOW_MS - 999_999)
    )

    decision = asyncio.run(evaluate_tenant(TENANT, make_context(backend)))

    assert decision.reclaim is False
    assert "exited" in decision.reason


def test_an_unreachable_backend_keeps_the_container() -> None:
    backend = FakeBackend(inspect_error="no docker here")

    decision = asyncio.run(evaluate_tenant(TENANT, make_context(backend)))

    assert decision.reclaim is False
    assert "backend unavailable" in decision.reason


def test_reclaim_stops_the_container_and_records_the_drain() -> None:
    backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 120_000))

    decision = asyncio.run(reclaim_tenant(TENANT, make_context(backend)))

    assert decision.reclaim is True
    assert decision.drained is True
    assert backend.stopped == ["vt-t11-a"]


def test_reclaim_waits_for_an_inflight_request_before_stopping() -> None:
    async def scenario() -> tuple[list[str], bool]:
        backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 120_000))
        fence = TenantFence()
        context = make_context(backend, fence=fence, drain_timeout_s=5.0)
        admission = await fence.enter("a")
        task = asyncio.create_task(reclaim_tenant(TENANT, context))
        await asyncio.sleep(0.01)
        order = ["stopped_early" if backend.stopped else "not_stopped_yet"]
        await admission.release()
        decision = await task
        order.extend(backend.stopped)
        return order, decision.drained

    order, drained = asyncio.run(scenario())

    assert order == ["not_stopped_yet", "vt-t11-a"]
    assert drained is True


def test_reclaim_pass_over_multiple_tenants_reports_each_verdict() -> None:
    class PerTenantBackend(FakeBackend):
        async def engine_get(self, container: str, path: str) -> str | None:
            self.engine_calls.append((container, path))
            if container == "c-busy":
                return sessions_payload(NOW_MS - 1_000)
            return sessions_payload(NOW_MS - 120_000)

    async def scenario() -> tuple[list[tuple[str, bool]], list[str]]:
        idle = Tenant("idle", "idle.local", "http://127.0.0.1:1", "c-idle")
        busy = Tenant("busy", "busy.local", "http://127.0.0.1:2", "c-busy")
        backend = PerTenantBackend(engine_body=None)
        decisions = await reclaim_idle([idle, busy], make_context(backend))
        return [
            (decision.tenant_id, decision.reclaim) for decision in decisions
        ], backend.stopped

    verdicts, stopped = asyncio.run(scenario())

    assert verdicts == [("idle", True), ("busy", False)]
    assert stopped == ["c-idle"]


def test_a_reclaimed_tenant_can_be_admitted_again_for_the_next_wake() -> None:
    async def scenario() -> tuple[list[str], bool]:
        backend = FakeBackend(engine_body=sessions_payload(NOW_MS - 120_000))
        fence = TenantFence()
        # reclaim_idle is the pass the admin surface runs: it clears the
        # disposal mark afterwards, because a reclaimed tenant is stopped, not
        # deleted — the next inbound request must be admitted so it can wake it.
        decisions = await reclaim_idle([TENANT], make_context(backend, fence=fence))
        admission = await fence.enter("a")
        await admission.release()
        return backend.stopped, decisions[0].reclaim

    stopped, reclaimed = asyncio.run(scenario())

    assert stopped == ["vt-t11-a"]
    assert reclaimed is True


def test_ledger_touch_and_snapshot_use_wall_clock_milliseconds() -> None:
    ledger = ActivityLedger(clock=lambda: 1_700_000_000.5)

    ledger.touch("a")

    assert ledger.last_ms("a") == 1_700_000_000_500
    assert ledger.snapshot() == {"a": 1_700_000_000_500}
    assert ledger.last_ms("b") is None
