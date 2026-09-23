"""Idle auto-pause on the e2b path (plan T18, gate D11 ⓑ) — zero-edit proof.

The reclaim recipe (idle → engine truth check → stop) already speaks to
sandbox tenants through the T16 :class:`E2bSandboxBackend` contract; these
tests run the REAL reclaim.py/fence.py against the real e2b backend with the
CLI subprocess mocked, and pin that no consumer module was edited to get
there. ``stop`` on that backend IS ``beta_pause`` (D11 pause-only).
"""

from __future__ import annotations

import asyncio

from router.backend import E2bSandboxBackend
from router.fence import TenantDisposed, TenantFence
from router.reclaim import (
    SESSION_PROBE_PATH,
    ActivityLedger,
    ReclaimContext,
    evaluate_tenant,
    reclaim_tenant,
)
from router_harness import (
    SANDBOX_A,
    SANDBOX_CTL,
    cli_fail,
    cli_ok,
    commands_of,
    sandbox_tenant,
    sessions_ms,
    spawn_cli,
    state_paused,
    state_running,
)

NOW_S = 1_700_000_000.0
NOW_MS = int(NOW_S * 1000)
IDLE_TTL_MS = 60_000


def reclaim_context(fence: TenantFence | None = None) -> ReclaimContext:
    return ReclaimContext(
        backend=E2bSandboxBackend(ctl_bin=SANDBOX_CTL),
        fence=fence or TenantFence(),
        ledger=ActivityLedger(),
        idle_ttl_ms=IDLE_TTL_MS,
        drain_timeout_s=5.0,
        clock=lambda: NOW_S,
    )


def test_idle_engine_silence_fails_closed_and_never_pauses(monkeypatch) -> None:
    """The gate-ⓑ safety property on the e2b path: an engine that does not
    answer is SILENCE, not idleness — the tenant is KEPT and the CLI stop
    (= beta_pause) is never called."""
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{
            "engine-get": {
                SANDBOX_A: cli_fail(5, "operation_failed", "envd command error")
            }
        },
    )
    tenant = sandbox_tenant("acme", SANDBOX_A)

    decision = asyncio.run(evaluate_tenant(tenant, reclaim_context()))

    assert decision.reclaim is False
    assert decision.truth.source == "unavailable"
    assert "fail closed" in decision.reason
    assert commands_of(recorder, "stop") == []


def test_idle_with_stale_engine_truth_pauses_via_cli_stop(monkeypatch) -> None:
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{
            "engine-get": {SANDBOX_A: sessions_ms(NOW_MS - 120_000)},
            "stop": {SANDBOX_A: cli_ok({"paused": True, "already_paused": False})},
        },
    )
    tenant = sandbox_tenant("acme", SANDBOX_A)

    decision = asyncio.run(reclaim_tenant(tenant, reclaim_context()))

    assert decision.reclaim is True
    assert decision.drained is True
    assert recorder.calls == [
        (SANDBOX_CTL, "state", SANDBOX_A),
        (SANDBOX_CTL, "engine-get", SANDBOX_A, SESSION_PROBE_PATH),
        (SANDBOX_CTL, "stop", SANDBOX_A),
    ]


def test_fresh_engine_activity_keeps_a_running_sandbox(monkeypatch) -> None:
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{"engine-get": {SANDBOX_A: sessions_ms(NOW_MS - 1_000)}},
    )
    tenant = sandbox_tenant("acme", SANDBOX_A)

    decision = asyncio.run(evaluate_tenant(tenant, reclaim_context()))

    assert decision.reclaim is False
    assert decision.truth.source == "engine"
    assert decision.idle_ms == 1_000
    assert commands_of(recorder, "stop") == []


def test_paused_sandbox_has_nothing_to_reclaim(monkeypatch) -> None:
    spawn_cli(monkeypatch, state={SANDBOX_A: state_paused()})
    tenant = sandbox_tenant("acme", SANDBOX_A)

    decision = asyncio.run(evaluate_tenant(tenant, reclaim_context()))

    assert decision.reclaim is False
    assert "exited" in decision.reason


def test_fence_serializes_a_sandbox_pause_behind_inflight_requests(
    monkeypatch,
) -> None:
    """Sandbox disposal never races an in-flight request: the fence is keyed
    by tenant id and the disposal action is whatever the backend's stop does —
    for e2b that is beta_pause. T17 proved the shape consumer-agnostic; this
    pins it as a regression on the sandbox path."""
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{
            "engine-get": {SANDBOX_A: sessions_ms(NOW_MS - 120_000)},
            "stop": {SANDBOX_A: cli_ok({"paused": True})},
        },
    )
    tenant = sandbox_tenant("acme", SANDBOX_A)
    fence = TenantFence()

    async def scenario() -> tuple[list[str], bool, bool]:
        admission = await fence.enter("acme")
        task = asyncio.create_task(reclaim_tenant(tenant, reclaim_context(fence)))
        await asyncio.sleep(0.01)
        order = ["early" if commands_of(recorder, "stop") else "held"]
        refused = False
        try:
            await fence.enter("acme")
        except TenantDisposed:
            refused = True
        await admission.release()
        decision = await task
        order.extend("stopped" for _ in commands_of(recorder, "stop"))
        return order, decision.drained, refused

    order, drained, refused = asyncio.run(scenario())

    assert order == ["held", "stopped"]
    assert drained is True
    assert refused is True
