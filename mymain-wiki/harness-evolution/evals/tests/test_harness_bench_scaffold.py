"""Tests for the harness_bench scaffold: adapter protocol, report schema,
canonical tool manifest and soak rig.

The manifest tests spawn the REAL MCP server subprocess (bounded, smoke-test
pattern) and assert the manifest matches the live ``tools/list`` three ways:
emitted count == entries in the JSON == server registration count.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

from src.evals.harness_bench import manifest, report, soak
from src.evals.harness_bench import soak_executors
from src.evals.harness_bench.adapter import HarnessAdapter, MockAdapter

PKG_DIR = Path(report.__file__).resolve().parent
REPORT_SCHEMA_PATH = PKG_DIR / "report_schema.json"


# --------------------------------------------------------------------------- #
# Protocol contract
# --------------------------------------------------------------------------- #


def _run_mock_adapter(skip: dict | None = None) -> dict:
    adapter = MockAdapter()
    adapter.setup({"harness_id": "mock", "skip_benchmarks": skip or {}})
    for benchmark in ("tau2", "financebench"):
        for seed in (1, 2, 3):
            for task_index in range(4):
                adapter.run_task(
                    {
                        "task_id": f"{benchmark}-t{task_index}",
                        "benchmark": benchmark,
                        "seed": seed,
                    }
                )
    adapter.teardown()
    return adapter.report()


def test_mock_adapter_satisfies_protocol() -> None:
    adapter = MockAdapter()
    assert isinstance(adapter, HarnessAdapter)
    for method in ("setup", "run_task", "teardown", "report"):
        assert callable(getattr(adapter, method))


def test_mock_adapter_report_validates() -> None:
    report_dict = _run_mock_adapter()
    report.validate_report(report_dict)
    import jsonschema

    jsonschema.validate(report_dict, report.load_schema())
    assert {row["benchmark"] for row in report_dict["metrics"]} == {
        "tau2",
        "financebench",
    }
    assert report_dict["provenance"]["seeds"]["tau2"] == [1, 2, 3]
    assert report_dict["total_cost"]["value"] > 0


def test_mock_adapter_is_deterministic() -> None:
    first, second = _run_mock_adapter(), _run_mock_adapter()
    for key in ("metrics", "total_cost", "skip_markers"):
        assert first[key] == second[key], key


def test_mock_adapter_skip_markers() -> None:
    report_dict = _run_mock_adapter(skip={"swebench_verified": "docker unavailable"})
    marker = report_dict["skip_markers"][0]
    assert marker["benchmark"] == "swebench_verified"
    assert marker["degraded"] is True
    assert marker["decision"] == "excluded_from_adjudication"


# --------------------------------------------------------------------------- #
# Report schema validation
# --------------------------------------------------------------------------- #


@pytest.fixture()
def valid_report() -> dict:
    return _run_mock_adapter()


def test_report_schema_file_is_valid_schema() -> None:
    import jsonschema

    schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)


def test_valid_report_passes(valid_report: dict) -> None:
    report.validate_report(valid_report)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda r: r.pop("provenance"), "provenance"),
        (lambda r: r["metrics"][0].__setitem__("value", "high"), "metrics[0].value"),
        (lambda r: r["total_cost"].__setitem__("value", -1), "total_cost.value"),
        (
            lambda r: r["skip_markers"].append(
                {"benchmark": "x", "reason": "y", "degraded": True, "decision": "bogus"}
            ),
            "skip_markers[0].decision",
        ),
        (
            lambda r: r["provenance"].__setitem__("parity_spec_sha256", "nothex"),
            "provenance.parity_spec_sha256",
        ),
    ],
    ids=[
        "missing-provenance",
        "bad-metric-value",
        "negative-cost",
        "bad-decision",
        "bad-spec-hash",
    ],
)
def test_malformed_report_names_field(valid_report: dict, mutate, field: str) -> None:
    mutate(valid_report)
    with pytest.raises(report.ReportValidationError) as excinfo:
        report.validate_report(valid_report)
    assert field in str(excinfo.value)


def test_semantic_violations_name_fields(valid_report: dict) -> None:
    dup = copy.deepcopy(valid_report)
    dup["metrics"].append(dict(dup["metrics"][0]))
    with pytest.raises(report.ReportValidationError) as excinfo:
        report.validate_report(dup)
    assert "metrics[2]" in str(excinfo.value)

    overlap = copy.deepcopy(valid_report)
    overlap["skip_markers"].append(
        {
            "benchmark": "tau2",
            "reason": "test overlap",
            "degraded": True,
            "decision": "excluded_from_adjudication",
        }
    )
    with pytest.raises(report.ReportValidationError) as excinfo:
        report.validate_report(overlap)
    assert "skip_markers" in str(excinfo.value)

    cost_row = copy.deepcopy(valid_report)
    cost_row["metrics"].append(
        {"benchmark": "tau2", "metric": "cost_usd", "value": 1.0}
    )
    with pytest.raises(report.ReportValidationError) as excinfo:
        report.validate_report(cost_row)
    assert "metrics[2].metric" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Canonical tool manifest (spawns the real MCP server)
# --------------------------------------------------------------------------- #


def _test_env_pin() -> dict:
    pin = manifest.make_env_pin()
    pin["credential_env_presence"] = {
        var: False for var in manifest.ALL_PINNED_CREDENTIAL_VARS
    }
    return pin


@pytest.fixture(scope="module")
def live_capture() -> tuple[dict, list[dict]]:
    pin = _test_env_pin()
    return pin, manifest.capture_tools(pin)


def test_manifest_three_way_count_and_names(live_capture) -> None:
    pin, raw = live_capture
    built = manifest.build_manifest(raw, pin)
    assert built["tool_count"] == len(built["tools"]) == len(raw)
    assert {t["name"] for t in built["tools"]} == {t["name"] for t in raw}
    assert len({t["name"] for t in raw}) == len(raw)


def test_manifest_callability_and_digests(live_capture) -> None:
    pin, raw = live_capture
    built = manifest.build_manifest(raw, pin)
    for entry in built["tools"]:
        assert entry["callability"] in manifest.CALLABILITY_VALUES, entry["name"]
        assert len(entry["schema_sha256"]) == 64, entry["name"]
    by_name = {t["name"]: t for t in built["tools"]}
    for name in ("trading_account", "trading_quote"):
        assert by_name[name]["callability"] == manifest.CALLABILITY_GOVERNANCE_DISABLED
    for name in ("get_macro_series", "iwencai_search", "qveris_execute", "ch_query"):
        assert by_name[name]["callability"] == manifest.CALLABILITY_CREDENTIAL_GATED
    assert by_name["analyze_options"]["callability"] == manifest.CALLABILITY_NORMAL


def test_committed_manifest_matches_live(live_capture) -> None:
    _, raw = live_capture
    committed = json.loads(manifest.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert committed["tool_count"] == len(raw)
    assert {t["name"] for t in committed["tools"]} == {t["name"] for t in raw}
    presence = committed["env_pin"]["credential_env_presence"]
    assert presence and all(isinstance(value, bool) for value in presence.values())


@pytest.mark.integration
def test_manifest_digests_stable_across_two_captures(live_capture) -> None:
    pin, first_raw = live_capture
    second_raw = manifest.capture_tools(pin)
    first = {t["name"]: manifest.schema_digest(t.get("inputSchema")) for t in first_raw}
    second = {
        t["name"]: manifest.schema_digest(t.get("inputSchema")) for t in second_raw
    }
    assert first == second


@pytest.mark.integration
def test_manifest_emit_then_check_and_tamper(tmp_path: Path, live_capture) -> None:
    pin, raw = live_capture
    target = tmp_path / "manifest.json"
    emitted = manifest.emit(target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert (
        emitted["tool_count"]
        == len(raw)
        == loaded["tool_count"]
        == len(loaded["tools"])
    )
    assert manifest.check(target) == 0

    loaded["tools"] = [t for t in loaded["tools"] if t["name"] != "analyze_options"]
    loaded["tools"][0]["schema_sha256"] = "0" * 64
    loaded["tool_count"] = len(loaded["tools"])
    target.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
    assert manifest.check(target) == 1


# --------------------------------------------------------------------------- #
# Soak rig
# --------------------------------------------------------------------------- #


def test_soak_sampling_loop_wellformed_at_seconds_scale() -> None:
    artifact = soak.run_soak(
        label="unit",
        sampler=soak.ProcessRssSampler(os.getpid()),
        executor=soak_executors.MockWorkloadExecutor(per_iteration_sleep=0.001),
        duration_hours=0.0004,
        sample_interval_seconds=0.15,
    )
    soak.validate_soak_artifact(artifact)
    series = artifact["rss_timeseries"]
    assert len(series) >= 3
    assert all(p["rss_mb"] > 0 for p in series)
    assert [p["t_seconds"] for p in series] == sorted(p["t_seconds"] for p in series)
    assert artifact["boundary"] == "process"
    assert artifact["workload_sha256"] == soak.workload_sha256()
    assert artifact["iterations_completed"] > 0
    assert artifact["growth_mb_per_hour"] is not None


def test_soak_workload_hash_is_stable() -> None:
    assert soak.workload_sha256() == soak.workload_sha256(soak.DEFAULT_WORKLOAD)
    changed = copy.deepcopy(soak.DEFAULT_WORKLOAD)
    changed["steps"][0]["kind"] = "something_else"
    assert soak.workload_sha256(changed) != soak.workload_sha256()


def _assert_error_dict(error: dict) -> None:
    assert isinstance(error, dict)
    for key in ("kind", "target", "boundary", "message"):
        assert key in error


def _assert_structured_error(sample: soak.Sample) -> None:
    assert sample.ok is False
    assert sample.rss_mb is None
    _assert_error_dict(sample.error)


def test_soak_nonexistent_process_returns_structured_error() -> None:
    sampler = soak.ProcessRssSampler(pid=2**22 + 12345)
    _assert_structured_error(sampler.sample(0.0))
    artifact = soak.run_soak(
        label="deadpid",
        sampler=sampler,
        executor=soak_executors.MockWorkloadExecutor(per_iteration_sleep=0.001),
        duration_hours=0.0002,
        sample_interval_seconds=0.1,
    )
    soak.validate_soak_artifact(artifact)
    assert artifact["rss_timeseries"] == []
    assert artifact["sample_errors"]
    _assert_error_dict(artifact["sample_errors"][0]["error"])
    assert artifact["growth_mb_per_hour"] is None


def test_soak_nonexistent_container_returns_structured_error() -> None:
    sampler = soak.ContainerRssSampler(container="harness-bench-no-such-container")
    sample = sampler.sample(0.0)
    _assert_structured_error(sample)
    assert sample.error["boundary"] == "container"


def test_soak_docker_stats_parser_fixtures() -> None:
    cases = [
        ("12.34MiB / 16GiB", 12.34),
        ("1.5GiB / 8GiB", 1536.0),
        ("512KiB / 2GiB", 0.5),
        ("256MB / 1GB", 256.0),
        ("123456B / 2GiB", 123456 / (1024 * 1024)),
    ]
    for text, expected in cases:
        assert soak.parse_mem_usage(text) == pytest.approx(expected), text
    for bad in ("", "garbage", "12.3 XB / 1 GiB"):
        with pytest.raises(ValueError):
            soak.parse_mem_usage(bad)


def test_soak_artifact_validator_names_fields() -> None:
    good = soak.run_soak(
        label="validator",
        sampler=soak.ProcessRssSampler(os.getpid()),
        executor=soak_executors.MockWorkloadExecutor(per_iteration_sleep=0.001),
        duration_hours=0.0001,
        sample_interval_seconds=0.05,
    )
    soak.validate_soak_artifact(good)
    missing = copy.deepcopy(good)
    missing.pop("workload_sha256")
    with pytest.raises(soak.SoakArtifactError) as excinfo:
        soak.validate_soak_artifact(missing)
    assert "workload_sha256" in str(excinfo.value)
    bad_point = copy.deepcopy(good)
    bad_point["rss_timeseries"][0]["rss_mb"] = "lots"
    with pytest.raises(soak.SoakArtifactError) as excinfo:
        soak.validate_soak_artifact(bad_point)
    assert "rss_timeseries[0].rss_mb" in str(excinfo.value)
    bad_boundary = copy.deepcopy(good)
    bad_boundary["boundary"] = "cgroup"
    with pytest.raises(soak.SoakArtifactError) as excinfo:
        soak.validate_soak_artifact(bad_boundary)
    assert "boundary" in str(excinfo.value)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_soak_container_sampler_missing_container_kind() -> None:
    sample = soak.ContainerRssSampler(
        container="harness-bench-no-such-container"
    ).sample(0.0)
    assert sample.error is not None
    assert sample.error["kind"] in (
        "target_not_found",
        "docker_error",
        "docker_timeout",
    )
