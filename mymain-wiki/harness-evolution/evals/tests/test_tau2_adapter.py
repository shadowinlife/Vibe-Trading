"""Tests for the tau2 adapter, run CLI and opencode session bridge gate.

Everything runs offline: no docker, no network, no tau2 import (the real
tau2 API surface is exercised through a fake package injected into
``sys.modules``). Bridge failures are injected via the documented seams
(``BridgeConfig.probe_fn``, ``OpenCodeBridge._make_dialect``,
``run.build_bridge``).
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest

from src.evals.harness_bench import opencode_bridge, opencode_check, parity, report, run
from src.evals.harness_bench.adapter import HarnessAdapter, MockAdapter
from src.evals.harness_bench.adapters.tau2_adapter import (
    Tau2Adapter,
    pass_hat_k,
)

PKG_DIR = Path(report.__file__).resolve().parent


def _argv(benchmark="tau2", tasks="1", harness="mock", extra=None):
    argv = ["--benchmark", benchmark, "--tasks", tasks, "--harness", harness]
    argv.extend(extra or [])
    return argv


def _run_mock_adapter(tasks: int = 2, seeds=(1, 2, 3)) -> dict:
    adapter = Tau2Adapter("mock")
    adapter.setup({"harness": "mock", "benchmark": "tau2"})
    for spec in adapter.prepare_tasks(tasks):
        for seed in seeds:
            adapter.run_task({**spec, "seed": seed})
    adapter.teardown()
    return adapter.report()


class FakeDialect:
    def __init__(self, poll_ok: bool = False, detail: str = "budget exhausted"):
        self.poll_ok = poll_ok
        self.detail = detail
        self.sent: list[str] = []

    def create_session(self):
        return "sess-fake", "POST /api/session -> 201"

    def send_prompt(self, session_id: str, text: str):
        self.sent.append(text)
        return True, "POST /api/session/../prompt -> 200"

    def poll_until(self, session_id: str, markers: list, need_tool: bool):
        return self.poll_ok, [], self.detail

    def list_messages(self, session_id: str):
        return [], "0 messages"


def _bridge_with_fake_dialect(dialect: FakeDialect) -> opencode_bridge.OpenCodeBridge:
    bridge = opencode_bridge.OpenCodeBridge(
        opencode_bridge.BridgeConfig(endpoint="http://fake-bridge")
    )
    bridge._url = "http://fake-bridge"
    bridge._password = "pw"
    bridge._container = {"origin": "test", "as_found": True}
    bridge._make_dialect = lambda timeouts: dialect  # type: ignore[method-assign]
    return bridge


# --------------------------------------------------------------------------- #
# Protocol + deterministic mock path
# --------------------------------------------------------------------------- #


def test_adapter_satisfies_protocol() -> None:
    for harness in ("mock", "opencode", "pydantic"):
        adapter = Tau2Adapter(harness, bridge=object() if harness != "mock" else None)
        assert isinstance(adapter, HarnessAdapter)


def test_mock_report_schema_valid_with_pass1_cost_provenance() -> None:
    report_dict = _run_mock_adapter()
    report.validate_report(report_dict)
    metrics = {row["metric"] for row in report_dict["metrics"]}
    assert "pass^1" in metrics
    assert report_dict["total_cost"]["currency"] == "USD"
    provenance = report_dict["provenance"]
    assert provenance["parity_spec_sha256"] == report.parity_spec_sha256()
    assert len(provenance["git_commit"]) >= 7
    assert provenance["seeds"]["tau2"] == [1, 2, 3]


def test_pass_hat_k_matches_tau2_formula() -> None:
    assert pass_hat_k(5, 2, 1) == pytest.approx(2 / 5)
    assert pass_hat_k(5, 2, 2) == pytest.approx(1 / 10)
    assert pass_hat_k(4, 0, 1) == 0.0
    with pytest.raises(ValueError):
        pass_hat_k(2, 1, 3)


# --------------------------------------------------------------------------- #
# Registry + CLI contract (malformed input included)
# --------------------------------------------------------------------------- #


def test_registry_resolves_tau2_and_mock() -> None:
    # superset: todos 5-8 each add their own registry entries
    assert {"tau2", "mock"} <= set(run.BENCHMARK_REGISTRY)
    assert isinstance(run.BENCHMARK_REGISTRY["mock"]("mock"), MockAdapter)
    adapter = run.BENCHMARK_REGISTRY["tau2"]("mock")
    assert isinstance(adapter, Tau2Adapter)


def test_cli_unknown_benchmark_exits_2(tmp_path, capsys) -> None:
    code = run.main(["--benchmark", "nope", "--tasks", "1", "--harness", "mock"])
    assert code == run.EXIT_USAGE
    assert "unknown benchmark" in capsys.readouterr().err


def test_cli_bad_tasks_exits_2(capsys) -> None:
    code = run.main(["--benchmark", "tau2", "--tasks", "0", "--harness", "mock"])
    assert code == run.EXIT_USAGE
    assert "--tasks" in capsys.readouterr().err


def test_cli_mock_smoke_writes_valid_report(tmp_path) -> None:
    out = tmp_path / "smoke.json"
    code = run.main(_argv(extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    assert any(row["metric"] == "pass^1" for row in report_dict["metrics"])


def test_cli_pydantic_placeholder_skip_marker(tmp_path) -> None:
    out = tmp_path / "pydantic.json"
    code = run.main(_argv(harness="pydantic", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "poc-not-wired" in reasons


def test_budget_cap_aborts_with_skip_marker(tmp_path, monkeypatch) -> None:
    spec = json.loads((PKG_DIR / "parity_spec.json").read_text(encoding="utf-8"))
    spec["benchmarks"]["tau2"]["cost_cap_usd_per_run"] = 0.0
    monkeypatch.setattr(run.parity, "load_spec", lambda *a, **k: spec)
    out = tmp_path / "budget.json"
    code = run.main(_argv(tasks="2", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "cost_cap_exceeded" in reasons


# --------------------------------------------------------------------------- #
# Bridge preflight gate (the todo-4 gate) + HIL package
# --------------------------------------------------------------------------- #


class FakeBridge:
    def __init__(self, ok: bool):
        self._ok = ok
        self.image_facts = {"image": "opencode-serve:fake", "model_config_models": []}
        self.torn_down = 0

    def preflight(self):
        error = (
            None
            if self._ok
            else opencode_bridge.BridgeError(
                "auth_401", "turn2_tool_call: HTTP 401 missing_api_key"
            )
        )
        return opencode_bridge.ProbeReport(
            ok=self._ok,
            phases={
                "health": {"ok": True, "detail": "GET /health -> 200"},
                "turn2_tool_call": {
                    "ok": self._ok,
                    "detail": "ok" if self._ok else "HTTP 401 missing_api_key",
                },
            },
            session_id="sess-1",
            error=error,
            raw={"ok": self._ok},
        )

    def hil_facts(self):
        return {"service": {"url": "http://fake"}, "image_facts": self.image_facts}

    def run_task(self, prompt, *, timeout_s, seed, require_tool=False):
        return opencode_bridge.BridgeTrajectory(
            task_prompt=prompt, seed=seed, final_result="done", duration_s=0.5
        )

    def teardown(self):
        self.torn_down += 1
        return {"torn_down": True}


def test_gate_fail_writes_hil_package_and_awaiting_hil_state(
    tmp_path, monkeypatch
) -> None:
    hil_path = tmp_path / "bridge_preflight_hil_package.json"
    out = tmp_path / "opencode.json"
    monkeypatch.setattr(run, "HIL_PACKAGE_PATH", hil_path)
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge(ok=False))
    code = run.main(_argv(harness="opencode", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    package = json.loads(hil_path.read_text(encoding="utf-8"))
    for key in (
        "probe_transcript",
        "container_image_facts",
        "evidence_401",
        "remediation_options",
        "recommendation",
    ):
        assert key in package
    assert package["preflight_outcome"] == "FAIL"
    assert package["evidence_401"]["observed"] is True
    option_ids = {o["id"] for o in package["remediation_options"]}
    assert option_ids == {"fix-bridge", "degrade"}
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    # misleading-success guard: a failed preflight must never yield rows
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "awaiting-hil-decision" in reasons
    assert "bridge_preflight_failed" in reasons


def test_gate_pass_runs_opencode_smoke(tmp_path, monkeypatch) -> None:
    _install_fake_tau2(monkeypatch)
    out = tmp_path / "opencode_pass.json"
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge(ok=True))
    code = run.main(
        _argv(harness="opencode", extra=["--seed", "7", "--report-out", str(out)])
    )
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["provenance"]["harness_id"] == "opencode_omo_baseline"
    assert any(row["metric"] == "pass^1" for row in report_dict["metrics"])
    assert report_dict["skip_markers"] == []


# --------------------------------------------------------------------------- #
# Bridge behaviour: bounded timeout, interruption, structured errors
# --------------------------------------------------------------------------- #


def test_bridge_timeout_is_structured_and_bounded() -> None:
    bridge = _bridge_with_fake_dialect(FakeDialect(poll_ok=False))
    started = time.monotonic()
    trajectory = bridge.run_task("do the thing", timeout_s=1, seed=0)
    assert time.monotonic() - started < 5.0
    assert trajectory.ok is False
    assert trajectory.error is not None
    assert trajectory.error.kind == "timeout"


def test_bridge_no_tool_call_when_required() -> None:
    bridge = _bridge_with_fake_dialect(
        FakeDialect(poll_ok=True, detail="verified: 1 messages")
    )
    trajectory = bridge.run_task("x", timeout_s=1, seed=0, require_tool=True)
    assert trajectory.error is not None
    assert trajectory.error.kind == "no_tool_call"


def test_bridge_interrupted_preflight_tears_down(monkeypatch) -> None:
    started = {"n": 0}
    torn = {"n": 0}

    def fake_start(env):
        started["n"] += 1
        return {
            "ok": True,
            "container": "hb-fake",
            "url": "http://127.0.0.1:9",
            "password": "pw",
            "image": "opencode-serve:fake",
            "started_at": "2026-08-23T00:00:00+00:00",
        }

    def fake_teardown(name):
        torn["n"] += 1
        return {"container": name, "docker_rm_exit_code": 0}

    monkeypatch.setattr(opencode_check, "start_ephemeral_container", fake_start)
    monkeypatch.setattr(opencode_check, "teardown_container", fake_teardown)

    def exploding_probe(url, password, timeouts):
        raise RuntimeError("simulated kill after turn 1")

    bridge = opencode_bridge.OpenCodeBridge(
        opencode_bridge.BridgeConfig(probe_fn=exploding_probe)
    )
    bridge.collect_image_facts = lambda: {"image": "opencode-serve:fake"}  # type: ignore
    probe = bridge.preflight()
    assert probe.ok is False
    assert probe.error is not None
    assert probe.error.kind == "interrupted"
    assert started["n"] == 1
    assert torn["n"] == 1  # cancel/resume: resources torn down, no leak


def test_hil_package_assembly_shape() -> None:
    bridge = FakeBridge(ok=False)
    probe = bridge.preflight()
    package = run.assemble_hil_package(bridge, probe, "fix-bridge: test")
    assert package["gate"] == "bridge_preflight"
    assert package["probe_transcript"]["error"]["kind"] == "auth_401"
    assert package["recommendation"].startswith("fix-bridge")


# --------------------------------------------------------------------------- #
# Real tau2 API surface behind an injected fake package
# --------------------------------------------------------------------------- #


class _FakeTau2Task:
    def __init__(self, task_id: str):
        self.id = task_id

    def model_dump(self):
        return {"id": self.id, "user_scenario": {"instruction": "fake"}}


def _install_fake_tau2(monkeypatch) -> None:
    fake_registry = types.SimpleNamespace(
        get_tasks_loader=lambda domain: (
            lambda split: [_FakeTau2Task(f"{domain}-{i}") for i in range(3)]
        )
    )
    fake_tau2 = types.ModuleType("tau2")
    fake_tau2_registry = types.ModuleType("tau2.registry")
    fake_tau2_registry.registry = fake_registry
    fake_tau2.registry = fake_tau2_registry
    monkeypatch.setitem(sys.modules, "tau2", fake_tau2)
    monkeypatch.setitem(sys.modules, "tau2.registry", fake_tau2_registry)


def test_prepare_tasks_uses_tau2_registry_loader(monkeypatch) -> None:
    _install_fake_tau2(monkeypatch)
    adapter = Tau2Adapter("opencode", bridge=FakeBridge(ok=True))
    adapter.setup({"harness": "opencode", "benchmark": "tau2"})
    specs = adapter.prepare_tasks(3)
    assert [s["domain"] for s in specs] == ["retail", "airline", "retail"]
    assert specs[0]["task_id"] == "tau2-retail-retail-0"
    assert all("_tau2_task" in s for s in specs)


def test_tau2_data_unavailable_degrades_to_skip_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tau2", None)  # force ImportError path
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge(ok=True))
    out = tmp_path / "nodata.json"
    code = run.main(_argv(harness="opencode", extra=["--report-out", str(out)]))
    # tau2 not importable here -> data-unavailable skip marker, never a crash
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "tau2-data-unavailable" in reasons


def test_parity_spec_self_validation_holds() -> None:
    spec = parity.load_spec()
    parity.assert_runtime_config(spec, spec)
