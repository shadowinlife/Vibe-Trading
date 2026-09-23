"""Keepalive one-shot tests (plan T18, gate D11 ⓑ) — CLI subprocess mocked.

The TTL ladder for RUNNING e2b tenants: refresh/paused-skip/absent-report/
per-tenant isolation/env knobs, plus the ``router.cli keepalive`` surface a
cron monitor consumes. The fakes live in ``router_harness``; the reclaim-side
lifecycle half is in ``test_router_sandbox_reclaim.py``.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from router import cli, keepalive
from router.backend import E2bSandboxBackend
from router.keepalive import keepalive_pass
from router.registry import write_registry
from router_harness import (
    DOCKER_TENANT,
    SANDBOX_A,
    SANDBOX_B,
    SANDBOX_C,
    SANDBOX_CTL,
    FakeProc,
    cli_fail,
    cli_ok,
    commands_of,
    sandbox_tenant,
    spawn_cli,
    state_paused,
    state_running,
)


def run_pass(monkeypatch, tenants, **scripts):
    monkeypatch.delenv("VT_ROUTER_E2B_TTL_S", raising=False)
    recorder = spawn_cli(monkeypatch, **scripts)
    decisions = asyncio.run(keepalive_pass(tenants, ctl_bin=SANDBOX_CTL))
    return recorder, decisions


# --- the pass ----------------------------------------------------------------


def test_running_tenant_is_refreshed_with_the_default_ttl(monkeypatch) -> None:
    recorder, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: cli_ok({"timeout_set": True, "seconds": 1800})}},
    )
    assert decisions[0].outcome == "refreshed"
    assert decisions[0].state == "running"
    assert recorder.calls == [
        (SANDBOX_CTL, "state", SANDBOX_A),
        (SANDBOX_CTL, "set-timeout", SANDBOX_A, "1800"),
    ]


def test_ttl_env_moves_the_ladder_step(monkeypatch) -> None:
    monkeypatch.setenv("VT_ROUTER_E2B_TTL_S", "900")
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: cli_ok({"timeout_set": True, "seconds": 900})}},
    )
    asyncio.run(
        keepalive_pass([sandbox_tenant("acme", SANDBOX_A)], ctl_bin=SANDBOX_CTL)
    )
    assert commands_of(recorder, "set-timeout")[-1] == (
        SANDBOX_CTL,
        "set-timeout",
        SANDBOX_A,
        "900",
    )


def test_explicit_ttl_argument_beats_the_env(monkeypatch) -> None:
    monkeypatch.setenv("VT_ROUTER_E2B_TTL_S", "600")
    recorder = spawn_cli(
        monkeypatch,
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: cli_ok({"timeout_set": True, "seconds": 7200})}},
    )
    asyncio.run(
        keepalive_pass(
            [sandbox_tenant("acme", SANDBOX_A)], ctl_bin=SANDBOX_CTL, ttl_s=7200
        )
    )
    assert commands_of(recorder, "set-timeout")[-1] == (
        SANDBOX_CTL,
        "set-timeout",
        SANDBOX_A,
        "7200",
    )


@pytest.mark.parametrize("bad", ["0", "-5", "abc"])
def test_malformed_or_nonpositive_ttl_fails_fast(monkeypatch, bad) -> None:
    """A typo in the TTL knob must not silently disable the ladder."""
    monkeypatch.setenv("VT_ROUTER_E2B_TTL_S", bad)
    with pytest.raises(ValueError):
        asyncio.run(
            keepalive_pass([sandbox_tenant("acme", SANDBOX_A)], ctl_bin=SANDBOX_CTL)
        )


def test_paused_tenant_is_skipped_and_never_touched(monkeypatch) -> None:
    """Paused retention is indefinite (reserve-paused default forever), so a
    set-timeout there is a wasted call at best — and the skip also proves
    keepalive never rides the connect path that WOULD resume it."""
    recorder, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: state_paused()},
    )
    assert decisions[0].outcome == "skipped-paused"
    assert decisions[0].state == "exited"
    assert recorder.calls == [(SANDBOX_CTL, "state", SANDBOX_A)]


def test_absent_tenant_is_reported_for_reprovision(monkeypatch) -> None:
    _, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: cli_fail(4, "sandbox_absent", "no such sandbox")},
    )
    assert decisions[0].outcome == "absent"


def test_docker_tenants_are_never_probed_by_keepalive(monkeypatch) -> None:
    recorder, decisions = run_pass(monkeypatch, [DOCKER_TENANT])
    assert decisions[0].outcome == "skip-backend"
    assert recorder.calls == []


def test_probe_backend_unavailable_is_recorded(monkeypatch) -> None:
    _, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: cli_fail(3, "backend_unavailable", "gateway dead")},
    )
    assert decisions[0].outcome == "probe-failed"
    assert "gateway dead" in decisions[0].detail


def test_one_dead_sandbox_does_not_block_the_others(monkeypatch) -> None:
    """The per-tenant isolation requirement: a failing keepalive is RECORDED
    for that tenant only; the pass continues over the rest of the fleet."""
    recorder, decisions = run_pass(
        monkeypatch,
        [
            sandbox_tenant("a", SANDBOX_A),
            sandbox_tenant("b", SANDBOX_B),
            sandbox_tenant("c", SANDBOX_C),
        ],
        state={sid: state_running() for sid in (SANDBOX_A, SANDBOX_B, SANDBOX_C)},
        **{
            "set-timeout": {
                SANDBOX_A: cli_fail(5, "operation_failed", "sandbox a is dying"),
                SANDBOX_B: cli_ok({"timeout_set": True, "seconds": 1800}),
                SANDBOX_C: cli_ok({"timeout_set": True, "seconds": 1800}),
            }
        },
    )
    outcomes = {d.tenant_id: d.outcome for d in decisions}
    assert outcomes == {"a": "set-timeout-failed", "b": "refreshed", "c": "refreshed"}
    assert len(commands_of(recorder, "set-timeout")) == 3


def test_broken_json_contract_on_zero_exit_is_a_failure(monkeypatch) -> None:
    _, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: FakeProc(0, "not json at all")}},
    )
    assert decisions[0].outcome == "set-timeout-failed"
    assert "contract" in decisions[0].detail


def test_set_timeout_budget_is_bounded_and_recorded(monkeypatch) -> None:
    proc = FakeProc(0, "", hang=1.0)
    monkeypatch.setattr(keepalive, "SET_TIMEOUT_BUDGET_S", 0.01)
    _, decisions = run_pass(
        monkeypatch,
        [sandbox_tenant("acme", SANDBOX_A)],
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: proc}},
    )
    assert decisions[0].outcome == "set-timeout-failed"
    assert "timed out" in decisions[0].detail
    assert proc.killed is True


def test_ctl_resolution_matches_the_t16_backend(monkeypatch) -> None:
    """Probe and ladder must never drift to two different binaries."""
    monkeypatch.delenv("VT_ROUTER_E2B_CTL_BIN", raising=False)
    assert keepalive._resolve_ctl(None) == os.path.expanduser(
        E2bSandboxBackend._DEFAULT_CTL
    )
    assert keepalive._resolve_ctl(SANDBOX_CTL) == SANDBOX_CTL
    monkeypatch.setenv("VT_ROUTER_E2B_CTL_BIN", "~/x/bin/e2b-sandbox-ctl")
    resolved = keepalive._resolve_ctl(None)
    assert resolved == os.path.expanduser("~/x/bin/e2b-sandbox-ctl")
    assert not resolved.startswith("~")


# --- CLI wiring (the cron-facing surface) -------------------------------------


def write_mixed_registry(tmp_path):
    reg = tmp_path / "tenant_registry.json"
    write_registry(
        reg,
        {
            "dock": {
                "tenant_id": "dock",
                "public_host": "dock.local",
                "upstream": "http://127.0.0.1:28081",
                "container": "vt-tenant-dock",
                "volume": "",
                "token_sha256": "0" * 64,
            },
            "acme": {
                "tenant_id": "acme",
                "public_host": "acme.sandbox.local",
                "upstream": f"http://localhost:8888/kruise/{SANDBOX_A}/4096",
                "container": SANDBOX_A,
                "volume": "",
                "backend": "e2b",
                "sandbox_id": SANDBOX_A,
                "token_sha256": "1" * 64,
            },
        },
    )
    return reg


def run_cli(monkeypatch, tmp_path, **scripts):
    monkeypatch.setenv("VT_ROUTER_REGISTRY", str(write_mixed_registry(tmp_path)))
    monkeypatch.delenv("VT_ROUTER_E2B_TTL_S", raising=False)
    monkeypatch.setenv("VT_ROUTER_E2B_CTL_BIN", SANDBOX_CTL)
    recorder = spawn_cli(monkeypatch, **scripts)
    return cli.main(["keepalive"]), recorder


def test_cli_keepalive_exits_zero_when_everything_is_healthy(
    monkeypatch, tmp_path, capsys
) -> None:
    code, recorder = run_cli(
        monkeypatch,
        tmp_path,
        state={SANDBOX_A: state_running()},
        **{"set-timeout": {SANDBOX_A: cli_ok({"timeout_set": True, "seconds": 1800})}},
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    outcomes = {row["tenant_id"]: row["outcome"] for row in printed}
    assert outcomes == {"dock": "skip-backend", "acme": "refreshed"}
    assert (SANDBOX_CTL, "set-timeout", SANDBOX_A, "1800") in recorder.calls


def test_cli_keepalive_exit_one_signals_attention(
    monkeypatch, tmp_path, capsys
) -> None:
    code, _ = run_cli(
        monkeypatch,
        tmp_path,
        state={SANDBOX_A: cli_fail(4, "sandbox_absent", "released at TTL expiry")},
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert printed[0]["outcome"] == "absent"


def test_cli_keepalive_registry_error_is_exit_one(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("VT_ROUTER_REGISTRY", str(tmp_path / "missing.json"))
    assert cli.main(["keepalive"]) == 1
    assert "registry error" in capsys.readouterr().out
