"""Tests for the BacktestBench adapter, fetch integrity and no-lookahead hook.

Everything runs offline: the fetch-script tests use ``file://`` URLs in a
fixture manifest, the bridge is a fake, and the repo Runner is replaced by a
stub where its wiring shape is asserted. Real-data pool tests skip cleanly
when the checksummed cache is absent (run ``scripts/fetch_backtestbench.py``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.evals.harness_bench import opencode_bridge, report, run
from src.evals.harness_bench.adapter import HarnessAdapter
from src.evals.harness_bench.adapters import backtestbench_tasks as tasks
from src.evals.harness_bench.adapters.backtestbench_adapter import (
    BacktestBenchAdapter,
    BacktestBenchDataUnavailable,
)
from src.evals.harness_bench.scripts import fetch_backtestbench as fetch

PKG_DIR = Path(report.__file__).resolve().parent
REAL_CACHE = PKG_DIR.parents[3] / ".venv-eval" / "data" / "backtestbench"


def _argv(tasks_n="2", harness="mock", extra=None):
    argv = ["--benchmark", "backtestbench", "--tasks", tasks_n, "--harness", harness]
    argv.extend(extra or [])
    return argv


# --------------------------------------------------------------------------- #
# Protocol conformance + deterministic mock path
# --------------------------------------------------------------------------- #


def test_adapter_satisfies_protocol() -> None:
    for harness in ("mock", "opencode", "pydantic"):
        adapter = BacktestBenchAdapter(
            harness, bridge=object() if harness != "mock" else None
        )
        assert isinstance(adapter, HarnessAdapter)


def test_mock_run_records_real_no_lookahead_checks() -> None:
    adapter = BacktestBenchAdapter("mock")
    adapter.setup({"harness": "mock", "benchmark": "backtestbench"})
    for spec in adapter.prepare_tasks(3):
        for seed in (1, 2):
            result = adapter.run_task({**spec, "seed": seed})
            check = result.details["no_lookahead_check"]
            assert check["passed"] is True
            assert check["detail"].startswith("ok:")
    report_dict = adapter.report()
    report.validate_report(report_dict)
    rows = {row["metric"]: row["value"] for row in report_dict["metrics"]}
    assert rows["no_lookahead_pass_rate"] == 1.0
    assert 0.0 <= rows["task_success_rate"] <= 1.0
    assert "subset_rule=backtestbench-v1" in report_dict["total_cost"]["note"]


# --------------------------------------------------------------------------- #
# No-lookahead hook: compliant pass + planted violations detected
# --------------------------------------------------------------------------- #


def test_lookahead_check_passes_on_compliant_task() -> None:
    config, engine_source = tasks.render_task_artifacts(tasks.synthetic_qa("t1"))
    ok, detail = tasks.check_no_lookahead(config, engine_source)
    assert ok, detail
    assert config["decision_start_date"] > config["start_date"]


def test_lookahead_check_detects_planted_violations() -> None:
    config, engine_source = tasks.render_task_artifacts(tasks.synthetic_qa("t1"))
    planted_shift = engine_source.replace(
        "closes.iloc[i - 1]", "closes.shift(-1).iloc[i]"
    )
    ok, detail = tasks.check_no_lookahead(config, planted_shift)
    assert ok is False
    assert "negative_shift" in detail

    ok, detail = tasks.check_no_lookahead(
        config, engine_source.replace(tasks.T1_GUARD_MARKER, "_disabled_guard")
    )
    assert ok is False and "missing_runtime_hook" in detail

    no_warmup = dict(config, decision_start_date=config["start_date"])
    ok, detail = tasks.check_no_lookahead(no_warmup, engine_source)
    assert ok is False and "no_warmup" in detail

    inverted = dict(
        config, start_date=config["end_date"], end_date=config["start_date"]
    )
    ok, detail = tasks.check_no_lookahead(inverted, engine_source)
    assert ok is False and "config_dates_invalid" in detail


def test_runtime_guard_raises_on_consumed_future_bar() -> None:
    _, engine_source = tasks.render_task_artifacts(tasks.synthetic_qa("t1"))
    namespace: dict = {}
    exec(compile(engine_source, "<materialized>", "exec"), namespace)  # noqa: S102
    guard = namespace[tasks.T1_GUARD_MARKER]
    guard(5, [1, 2, 3])  # compliant: all consumed bars < decision date
    with pytest.raises(RuntimeError, match="lookahead violation"):
        guard(5, [1, 5])  # planted: decision at t consumed bar t


def test_materialize_run_dir_writes_repo_format(tmp_path) -> None:
    qa = tasks.synthetic_qa("t1")
    config = tasks.materialize_run_dir(qa, tmp_path)
    assert (tmp_path / "config.json").exists()
    engine_path = tmp_path / "code" / "signal_engine.py"
    assert engine_path.exists()
    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk == config
    ok, detail = tasks.check_no_lookahead(
        on_disk, engine_path.read_text(encoding="utf-8")
    )
    assert ok, detail


def test_execute_run_dir_routes_through_repo_runner(monkeypatch, tmp_path) -> None:
    import types

    seen = {}

    class FakeRunner:
        def __init__(self, timeout=300):
            seen["timeout"] = timeout

        def execute(self, entry_script, run_dir, *, cwd=None, cli_args=None):
            seen["entry_script"] = Path(entry_script)
            seen["cli_args"] = cli_args
            return types.SimpleNamespace(
                success=True, exit_code=0, stdout="{}", stderr="", artifacts={}
            )

    fake_module = types.ModuleType("src.core.runner")
    fake_module.Runner = FakeRunner
    monkeypatch.setitem(__import__("sys").modules, "src.core.runner", fake_module)
    outcome = tasks.execute_run_dir(tmp_path, timeout_s=42)
    assert outcome["success"] is True
    assert seen["timeout"] == 42
    assert seen["entry_script"] == tasks.AGENT_ROOT / "backtest" / "runner.py"
    assert seen["cli_args"] == [str(tmp_path)]


# --------------------------------------------------------------------------- #
# Subset sampler determinism
# --------------------------------------------------------------------------- #


def test_subset_sampler_is_deterministic_and_order_independent() -> None:
    pool = [f"uuid-{i:03d}" for i in range(50)]
    first = tasks.sample_subset(pool, 10, 7)
    again = tasks.sample_subset(list(reversed(pool)), 10, 7)
    assert first == again
    assert len(first) == len(set(first)) == 10
    assert tasks.sample_subset(pool, 10, 8) != first
    assert tasks.sample_subset(pool, 1000, 7) == sorted(set(pool))
    assert tasks.sample_subset([], 5, 7) == []


# --------------------------------------------------------------------------- #
# Fetch script: checksum verification refuses corrupted/truncated/wrong files
# --------------------------------------------------------------------------- #


def _fixture_manifest(tmp_path: Path, payload: bytes, *, sha256=None, size=None):
    source = tmp_path / "source.json"
    source.write_bytes(payload)
    entry = {
        "path": "datasets/fixture.json",
        "cache_path": "raw/fixture.json",
        "url": source.as_uri(),
        "size_bytes": size if size is not None else len(payload),
        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
        "group": "qa",
    }
    manifest = {
        "files": [entry],
        "fetch_policy": {
            "connect_timeout_s": 5,
            "per_file_timeout_s": 30,
            "retries": 1,
            "retry_backoff_s": [0],
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, entry


def test_fetch_verifies_clean_download_and_is_idempotent(tmp_path) -> None:
    manifest_path, entry = _fixture_manifest(tmp_path, b'{"qa": [1, 2, 3]}')
    cache = tmp_path / "cache"
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 0
    )
    dest = cache / entry["cache_path"]
    assert dest.exists() and dest.read_bytes() == b'{"qa": [1, 2, 3]}'
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 0
    )


def test_fetch_refuses_wrong_checksum_file(tmp_path) -> None:
    payload = b'{"qa": [1, 2, 3]}'
    wrong = "0" * 64
    manifest_path, entry = _fixture_manifest(tmp_path, payload, sha256=wrong)
    cache = tmp_path / "cache"
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 1
    )
    assert not (cache / entry["cache_path"]).exists()


def test_fetch_refuses_truncated_file(tmp_path) -> None:
    payload = b'{"qa": [1, 2, 3]}'
    manifest_path, entry = _fixture_manifest(tmp_path, payload, size=len(payload) + 10)
    cache = tmp_path / "cache"
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 1
    )
    assert not (cache / entry["cache_path"]).exists()


def test_fetch_refuses_corrupted_cached_file(tmp_path, capsys) -> None:
    payload = b'{"qa": [1, 2, 3]}'
    manifest_path, entry = _fixture_manifest(tmp_path, payload)
    cache = tmp_path / "cache"
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 0
    )
    dest = cache / entry["cache_path"]
    corrupted = bytearray(dest.read_bytes())
    corrupted[0] ^= 0xFF  # flip one byte
    dest.write_bytes(bytes(corrupted))
    assert (
        fetch.main(["--manifest", str(manifest_path), "--cache-dir", str(cache)]) == 1
    )
    err = capsys.readouterr().err
    assert "cached_file_corrupt" in err and "sha256_mismatch" in err


def test_fetch_refuses_unchecksummed_file(tmp_path) -> None:
    manifest_path, entry = _fixture_manifest(tmp_path, b"x", sha256=None)
    entry["sha256"] = None
    manifest_path.write_text(
        json.dumps({"files": [entry], "fetch_policy": {"retries": 1}}),
        encoding="utf-8",
    )
    with pytest.raises(fetch.FetchIntegrityError) as excinfo:
        fetch.download_verified(entry, tmp_path / "out.json", {"retries": 1})
    assert excinfo.value.reason == "sha256_unknown"


def test_missing_data_degrades_to_skip_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VT_BTB_DATA_DIR", str(tmp_path))  # empty dir
    adapter = BacktestBenchAdapter("opencode", bridge=object())
    adapter.setup({"harness": "opencode", "benchmark": "backtestbench"})
    assert adapter.prepare_tasks(3) == []
    markers = [m["reason"] for m in adapter._skip_markers]
    assert any("backtestbench-data-unavailable" in m for m in markers)
    with pytest.raises(BacktestBenchDataUnavailable):
        adapter._load_qa_pool()


# --------------------------------------------------------------------------- #
# CLI contract via run.main
# --------------------------------------------------------------------------- #


def test_cli_mock_smoke_report_carries_no_lookahead_marker(tmp_path) -> None:
    out = tmp_path / "smoke.json"
    code = run.main(_argv(tasks_n="5", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    rows = {row["metric"]: row["value"] for row in report_dict["metrics"]}
    assert rows["no_lookahead_pass_rate"] == 1.0
    assert "task_success_rate" in rows
    assert report_dict["total_cost"]["value"] >= 0.0
    assert report_dict["total_cost"]["currency"] == "USD"
    assert report_dict["provenance"]["seeds"]["backtestbench"] == [1, 2, 3, 4, 5]


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
    spec["benchmarks"]["backtestbench"]["cost_cap_usd_per_run"] = 0.0
    monkeypatch.setattr(run.parity, "load_spec", lambda *a, **k: spec)
    out = tmp_path / "budget.json"
    code = run.main(_argv(extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "cost_cap_exceeded" in reasons


class FakeBridge:
    def __init__(self, ok: bool = True):
        self._ok = ok
        self.image_facts = {"image": "opencode-serve:fake", "model_config_models": []}

    def preflight(self):
        return opencode_bridge.ProbeReport(
            ok=self._ok,
            phases={"health": {"ok": self._ok, "detail": "GET /health -> 200"}},
            session_id="sess-1",
            error=None,
            raw={"ok": self._ok},
        )

    def hil_facts(self):
        return {"service": {"url": "http://fake"}, "image_facts": self.image_facts}

    def run_task(self, prompt, *, timeout_s, seed, require_tool=False):
        return opencode_bridge.BridgeTrajectory(
            task_prompt=prompt, seed=seed, final_result="52.0833", duration_s=0.2
        )

    def teardown(self):
        return {"torn_down": True}


def test_opencode_without_data_degrades_via_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VT_BTB_DATA_DIR", str(tmp_path))  # empty dir
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge(ok=True))
    out = tmp_path / "nodata.json"
    code = run.main(_argv(harness="opencode", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "backtestbench-data-unavailable" in reasons


def test_real_pool_subset_deterministic_when_cache_present() -> None:
    test_json = REAL_CACHE / "raw" / "test.json"
    if not test_json.exists():
        pytest.skip("checksummed BacktestBench cache absent (fetch script not run)")
    adapter = BacktestBenchAdapter("opencode", bridge=object(), data_dir=REAL_CACHE)
    adapter.setup({"harness": "opencode", "benchmark": "backtestbench"})
    pool = adapter._load_qa_pool()
    assert len(pool) > 1000
    uuids = [q["uuid"] for q in pool]
    assert tasks.sample_subset(uuids, 5, adapter._sampling_seed()) == (
        tasks.sample_subset(list(reversed(uuids)), 5, adapter._sampling_seed())
    )
