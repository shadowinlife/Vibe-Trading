"""Tests for the SWE-bench Verified / terminal-bench adapters (todo 7).

Everything runs offline: no docker, no network, no harbor import. The subset
artifact's redraw determinism is re-verified from the committed full id list;
bridge failures are injected via the documented seams; the anti-exploit spot
checks must FLAG planted cheat fixtures (a constant all-pass would fail here).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from src.evals.harness_bench import opencode_bridge, parity, report, run
from src.evals.harness_bench.adapter import HarnessAdapter
from src.evals.harness_bench.adapters import spot_check, swe_terminal_adapter as sta
from src.evals.harness_bench.adapters.swe_terminal_adapter import (
    SWEbenchVerifiedAdapter,
    TerminalBenchAdapter,
    extract_patch,
)

PKG_DIR = Path(report.__file__).resolve().parent
SUBSET_PATH = PKG_DIR / "artifacts" / "swebench_subset.json"


def _argv(benchmark, tasks="2", harness="mock", extra=None):
    argv = ["--benchmark", benchmark, "--tasks", tasks, "--harness", harness]
    argv.extend(extra or [])
    return argv


class FakeBridge:
    """Bridge double: preflight ok, run_task returns a diff-fenced answer."""

    def __init__(self, final_result: str = "done"):
        self.final_result = final_result
        self.image_facts = {"image": "opencode-serve:fake"}
        self.torn_down = 0

    def preflight(self):
        return opencode_bridge.ProbeReport(
            ok=True,
            phases={"health": {"ok": True, "detail": "GET /health -> 200"}},
            session_id="sess-1",
        )

    def hil_facts(self):
        return {"service": {"url": "http://fake"}, "image_facts": self.image_facts}

    def run_task(self, prompt, *, timeout_s, seed, require_tool=False):
        return opencode_bridge.BridgeTrajectory(
            task_prompt=prompt,
            seed=seed,
            final_result=self.final_result,
            duration_s=0.5,
            usage={"cost_usd": 0.01},
        )

    def teardown(self):
        self.torn_down += 1
        return {"torn_down": True}


PATCH_ANSWER = (
    "I fixed the bug.\n"
    "```diff\n"
    "--- a/src/mod.py\n"
    "+++ b/src/mod.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
    "```\n"
)


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter_cls", [SWEbenchVerifiedAdapter, TerminalBenchAdapter])
def test_adapters_satisfy_protocol(adapter_cls) -> None:
    for harness in ("mock", "opencode", "pydantic"):
        adapter = adapter_cls(harness, bridge=object() if harness != "mock" else None)
        assert isinstance(adapter, HarnessAdapter)


# --------------------------------------------------------------------------- #
# Subset artifact validity + redraw determinism (stale_state guard)
# --------------------------------------------------------------------------- #


def test_subset_artifact_valid_and_redraw_deterministic() -> None:
    spec = json.loads((PKG_DIR / "parity_spec.json").read_text(encoding="utf-8"))
    subset_spec = spec["subsets"]["swebench_verified"]
    artifact = json.loads(SUBSET_PATH.read_text(encoding="utf-8"))
    ids = artifact["task_ids"]
    assert artifact["benchmark"] == "swebench_verified"
    assert artifact["n"] == subset_spec["n"] == 100
    assert artifact["sampling_seed"] == subset_spec["sampling_seed"] == 20260823
    assert artifact["cap"] == subset_spec["cap"] == 200
    assert len(ids) == 100 and len(set(ids)) == 100
    source_ids = artifact["source_ids"]
    assert source_ids == sorted(source_ids)
    assert set(ids) <= set(source_ids)
    # redraw determinism: same seed + same full list -> identical subset
    redraw = random.Random(20260823).sample(source_ids, 100)
    assert redraw == ids
    # source provenance is recorded (HF mirror revision)
    assert artifact["source"]["revision"]
    assert artifact["source"]["parquet_sha256"]


# --------------------------------------------------------------------------- #
# CLI contract: mock smokes, malformed input, pydantic placeholder, budget
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "benchmark,metric",
    [("terminal-bench", "task_success_rate"), ("swebench_verified", "resolve_rate")],
)
def test_cli_mock_smoke_writes_valid_report(tmp_path, benchmark, metric) -> None:
    out = tmp_path / f"{benchmark}.json"
    code = run.main(_argv(benchmark, extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    rows = {(row["benchmark"], row["metric"]) for row in report_dict["metrics"]}
    assert (benchmark, metric) in rows
    assert report_dict["total_cost"]["currency"] == "USD"
    assert report_dict["total_cost"]["value"] >= 0.0
    # spot-check hook disclosed on the report (clean mock trajectories;
    # 2 tasks x 3 parity seeds = 6 sampled trajectories)
    assert (
        "spot_check: checked=6 passed=6 flagged=0" in report_dict["total_cost"]["note"]
    )


def test_cli_bad_tasks_exits_2(capsys) -> None:
    code = run.main(
        ["--benchmark", "terminal-bench", "--tasks", "0", "--harness", "mock"]
    )
    assert code == run.EXIT_USAGE
    assert "--tasks" in capsys.readouterr().err


def test_cli_bad_flag_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args(
            ["--benchmark", "terminal-bench", "--tasks", "x", "--harness", "mock"]
        )
    assert excinfo.value.code == 2


@pytest.mark.parametrize("benchmark", ["swebench_verified", "terminal-bench"])
def test_cli_pydantic_placeholder_skip_marker(tmp_path, benchmark) -> None:
    out = tmp_path / "pydantic.json"
    code = run.main(
        _argv(benchmark, harness="pydantic", extra=["--report-out", str(out)])
    )
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "poc-not-wired" in reasons


def test_budget_cap_aborts_with_skip_marker(tmp_path, monkeypatch) -> None:
    spec = json.loads((PKG_DIR / "parity_spec.json").read_text(encoding="utf-8"))
    spec["benchmarks"]["swebench_verified"]["cost_cap_usd_per_run"] = 0.0
    monkeypatch.setattr(run.parity, "load_spec", lambda *a, **k: spec)
    out = tmp_path / "budget.json"
    code = run.main(_argv("swebench_verified", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "cost_cap_exceeded" in reasons


# --------------------------------------------------------------------------- #
# Docker-unavailable degradation (injected failure -> skip marker, no crash)
# --------------------------------------------------------------------------- #


def test_terminal_bench_docker_unavailable_degrades(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge())
    monkeypatch.setattr(sta, "_docker_available", lambda: False)
    out = tmp_path / "docker_out.json"
    code = run.main(
        _argv("terminal-bench", harness="opencode", extra=["--report-out", str(out)])
    )
    assert code == run.EXIT_OK  # degraded with disclosure, never a crash
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "docker-unavailable" in reasons
    assert all(m["degraded"] is True for m in report_dict["skip_markers"])


def test_swe_cli_subset_unavailable_degrades(tmp_path, monkeypatch) -> None:
    """BenchmarkDataUnavailable (a Tau2DataUnavailable) hits run.py's degrade
    path: skip marker, exit 0, never a crash."""
    monkeypatch.setattr(sta, "SUBSET_PATH", tmp_path / "missing_subset.json")
    out = tmp_path / "swe_out.json"
    code = run.main(_argv("swebench_verified", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "swe-subset-artifact-unreadable" in reasons


def test_swe_loader_failure_is_data_unavailable() -> None:
    def boom(tid):
        raise sta.BenchmarkDataUnavailable("swe-task-data-unavailable: injected")

    adapter = SWEbenchVerifiedAdapter("opencode", bridge=FakeBridge(), swe_loader=boom)
    adapter.setup({"harness": "opencode", "benchmark": "swebench_verified"})
    with pytest.raises(sta.BenchmarkDataUnavailable):
        adapter.prepare_tasks(2)


# --------------------------------------------------------------------------- #
# opencode driving seams (offline doubles only)
# --------------------------------------------------------------------------- #


def test_swe_opencode_bridge_grading_seam_pass_and_honest_zero() -> None:
    adapter = SWEbenchVerifiedAdapter(
        "opencode",
        bridge=FakeBridge(final_result=PATCH_ANSWER),
        swe_loader=lambda tid: {"problem_statement": "fix the bug", "repo": "org/repo"},
        verifier_fn=lambda task, patch: "+x = 2" in patch,
    )
    adapter.setup({"harness": "opencode", "benchmark": "swebench_verified"})
    specs = adapter.prepare_tasks(2)
    assert all(s["task_id"] for s in specs) and all("_record" in s for s in specs)
    for spec in specs:
        adapter.run_task({**spec, "seed": 1})
    report_dict = adapter.report()
    report.validate_report(report_dict)
    row = report_dict["metrics"][0]
    assert row["metric"] == "resolve_rate" and row["value"] == 1.0

    # without an injected verifier the seam is an honest zero, never a pass
    honest = SWEbenchVerifiedAdapter(
        "opencode",
        bridge=FakeBridge(final_result=PATCH_ANSWER),
        swe_loader=lambda tid: {"problem_statement": "fix"},
    )
    honest.setup({"harness": "opencode", "benchmark": "swebench_verified"})
    result = honest.run_task({**honest.prepare_tasks(1)[0], "seed": 1})
    assert result.status == "failed" and result.score == 0.0
    assert result.details["grading"].startswith("not_applied")


def test_extract_patch_variants() -> None:
    assert extract_patch(PATCH_ANSWER) is not None
    bare = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b"
    assert extract_patch(f"intro\n{bare}") == bare
    assert extract_patch("no diff here") is None
    assert extract_patch("") is None


def test_terminal_harbor_seam_bounded_failure() -> None:
    adapter = TerminalBenchAdapter(
        "opencode", bridge=FakeBridge(), harbor_bin="/nonexistent/harbor"
    )
    adapter.setup({"harness": "opencode", "benchmark": "terminal-bench"})
    task = {
        "task_id": "terminal-bench-x",
        "benchmark": "terminal-bench",
        "_harbor_task": "x",
    }
    result = adapter.run_task({**task, "seed": 1})
    assert result.status == "error"
    assert "harbor_cli_unavailable" in result.details["harbor"]


# --------------------------------------------------------------------------- #
# Anti-exploit spot checks: planted cheats must be FLAGGED, clean must pass
# --------------------------------------------------------------------------- #

CHEAT_CONFTEST_TEXT = (
    "I updated the test config.\nimport pytest\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    report = object()\n"
    "    report.outcome = 'passed'\n"
)
CHEAT_CONFTEST_AST = (
    "import pytest\n\n\n"
    "def pytest_collection_modifyitems(config, items):\n"
    "    items.clear()\n"
)
CHEAT_DEP_REPLACE = (
    "steps taken:\n"
    "pip install fake-validator --index-url file:///tmp/wheels\n"
    "then ran the checker; echo PASS\n"
)
CHEAT_ANSWER_INJECTION = (
    "here is my solution\n"
    "assert result == 42  # copied from the test\n"
    "FAIL_TO_PASS expects this value\n"
)
GRADING_TEST_CONTENT = (
    "def test_compute():\n"
    "    assert compute_answer() == 42\n"
    "    assert compute_side_effect() == 'xyzzy-plugh'\n"
)
CLEAN_TRAJECTORY = (
    "I read the source, found the off-by-one in parse.py and fixed the loop "
    "bounds. The diff is attached.\n"
    "```diff\n--- a/parse.py\n+++ b/parse.py\n@@ -1 +1 @@\n-for i in range(10):\n+for i in range(11):\n```"
)


def test_spot_check_flags_planted_cheat_variants() -> None:
    # 1. conftest weakening via string heuristic
    findings = spot_check.scan_text(CHEAT_CONFTEST_TEXT)
    assert any(f.check_id == "conftest_weakening" for f in findings)
    # 2. conftest weakening via AST heuristic (workspace file)
    findings = spot_check.scan_workspace({"conftest.py": CHEAT_CONFTEST_AST})
    assert any(f.check_id == "conftest_weakening" for f in findings)
    # 3. dependency replacement to fake validators
    findings = spot_check.scan_text(CHEAT_DEP_REPLACE)
    assert any(f.check_id == "dependency_replacement" for f in findings)
    # 4. direct answer injection markers
    findings = spot_check.scan_text(CHEAT_ANSWER_INJECTION)
    assert any(f.check_id == "answer_injection" for f in findings)
    # 5. verbatim grading-test line copied into the submission
    findings = spot_check.scan_text(
        "solution:\n    assert compute_side_effect() == 'xyzzy-plugh'\n",
        reference_texts={"tests/test_grader.py": GRADING_TEST_CONTENT},
    )
    assert any(f.check_id == "answer_injection" for f in findings)


def test_spot_check_passes_clean_fixture() -> None:
    assert spot_check.scan_text(CLEAN_TRAJECTORY) == []
    assert spot_check.scan_workspace({"src/fix.py": CLEAN_TRAJECTORY}) == []
    summary = spot_check.spot_check_sample(
        [{"task_id": "t1", "texts": [CLEAN_TRAJECTORY], "files": {}}]
    )
    assert summary == {
        "checked": 1,
        "passed": 1,
        "flagged": 0,
        "findings": {},
        "sampled_task_ids": ["t1"],
    }


def test_spot_check_hook_flags_cheat_on_report() -> None:
    """Misleading-success guard: a planted cheat must surface on the report,
    not dissolve into a constant all-pass."""
    adapter = SWEbenchVerifiedAdapter("mock")
    adapter.setup({"harness": "mock", "benchmark": "swebench_verified"})
    spec = adapter.prepare_tasks(2)[0]
    adapter.run_task({**spec, "seed": 1})
    adapter._trajectories[-1]["texts"] = [CHEAT_CONFTEST_TEXT]  # plant the cheat
    report_dict = adapter.report()
    report.validate_report(report_dict)
    note = report_dict["total_cost"]["note"]
    assert "flagged=1" in note and spec["task_id"] in note
    flagged = [r for r in adapter._results if r.details["spot_check"] == "flagged"]
    assert len(flagged) == 1 and flagged[0].task_id == spec["task_id"]


def test_parity_spec_self_validation_holds() -> None:
    spec = parity.load_spec()
    parity.assert_runtime_config(spec, spec)
