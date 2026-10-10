"""Tests for the harness_bench preflight checks and the parity spec/validator.

Covers: jsonschema validation of parity_spec.json, seed floors, subset caps,
no-secrets scan, preflight report schema validation against fixtures, the
parity validator round-trip (valid passes / tampered fails naming the drifted
field), the preflight exit-code contract, and the HF degradation path.

No test here needs the network: every network path is poisoned or patched.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import jsonschema
import pytest

from src.evals.harness_bench import opencode_probe, parity, preflight

PKG_DIR = Path(preflight.__file__).resolve().parent
SPEC_PATH = PKG_DIR / "parity_spec.json"
SPEC_SCHEMA_PATH = PKG_DIR / "parity_spec.schema.json"
REPORT_SCHEMA_PATH = PKG_DIR / "preflight_report.schema.json"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(
        r"(?i)(api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}"
    ),
]


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec_schema() -> dict:
    return json.loads(SPEC_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report_schema() -> dict:
    return json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# parity_spec.json: schema, seeds, subsets, secrets
# --------------------------------------------------------------------------- #


def test_parity_spec_validates_against_schema(spec: dict, spec_schema: dict) -> None:
    jsonschema.validate(spec, spec_schema)  # raises on failure


def test_parity_spec_seed_floors(spec: dict) -> None:
    benchmarks = spec["benchmarks"]
    assert benchmarks, "spec must define benchmarks"
    for bench_id, bench in benchmarks.items():
        assert bench["seeds"] >= parity.SEED_FLOOR, f"{bench_id} below seed floor"
        if bench["high_variance"]:
            assert bench["seeds"] >= parity.HIGH_VARIANCE_SEED_FLOOR, (
                f"{bench_id} is high-variance and needs >="
                f"{parity.HIGH_VARIANCE_SEED_FLOOR} seeds"
            )
        assert len(bench["rationale"]) >= 20, f"{bench_id} rationale too thin"


def test_parity_spec_subset_caps(spec: dict) -> None:
    swe = spec["subsets"]["swebench_verified"]
    assert swe["n"] <= 200, "SWE-bench Verified subset exceeds the 200-task cap"
    assert swe["n"] <= swe["cap"]
    assert isinstance(swe["sampling_seed"], int)
    fineval = spec["subsets"]["fineval"]
    assert fineval["n"] >= 1
    assert isinstance(fineval["sampling_seed"], int)


def test_parity_spec_model_mapping(spec: dict) -> None:
    model = spec["model"]
    assert model["harness_model_id"] == "alibaba-cn/qwen3.8-max"
    assert model["endpoint"]["model_name"] == "qwen3.8-max"
    assert model["endpoint"]["mode"] == "openai-compatible"
    assert model["endpoint"]["base_url_default"].startswith("https://")
    # env-var references only, never literal credential material
    assert model["endpoint"]["api_key_env"] == "DASHSCOPE_API_KEY"


def test_parity_spec_contains_no_secrets() -> None:
    for path in (SPEC_PATH, SPEC_SCHEMA_PATH):
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(
                text
            ), f"key-like string matched {pattern.pattern!r} in {path.name}"


def test_spec_consistency_checker_passes_on_real_spec(spec: dict) -> None:
    assert parity.check_spec_consistency(spec) == []


def test_spec_consistency_checker_catches_weak_seeds(spec: dict) -> None:
    tampered = copy.deepcopy(spec)
    tampered["benchmarks"]["tau2"]["seeds"] = 2
    problems = parity.check_spec_consistency(tampered)
    assert any("tau2.seeds" in p for p in problems)


# --------------------------------------------------------------------------- #
# parity validator round-trip
# --------------------------------------------------------------------------- #


def test_valid_runtime_config_passes(spec: dict) -> None:
    config = copy.deepcopy(spec)  # identical config is the happy path
    assert parity.compare_runtime_config(spec, config) == []
    parity.assert_runtime_config(spec, config)  # must not raise


def test_tampered_runtime_config_names_drifted_field(spec: dict) -> None:
    config = copy.deepcopy(spec)
    config["generation"]["temperature"] = 0.2
    drifts = parity.compare_runtime_config(spec, config)
    assert any("generation.temperature" in d for d in drifts)
    with pytest.raises(parity.ParityDriftError) as excinfo:
        parity.assert_runtime_config(spec, config)
    assert "generation.temperature" in str(excinfo.value)


def test_missing_parity_field_is_named(spec: dict) -> None:
    config = copy.deepcopy(spec)
    del config["subsets"]["fineval"]["sampling_seed"]
    drifts = parity.compare_runtime_config(spec, config)
    assert any("subsets.fineval.sampling_seed" in d for d in drifts)
    assert any("missing" in d for d in drifts)


def test_tampered_seed_count_is_named(spec: dict) -> None:
    config = copy.deepcopy(spec)
    config["benchmarks"]["backtestbench"]["seeds"] = 3
    drifts = parity.compare_runtime_config(spec, config)
    assert any("benchmarks.backtestbench.seeds" in d for d in drifts)


def test_parity_cli_validate_spec_ok() -> None:
    assert parity.main(["--validate"]) == 0


def test_parity_cli_valid_config_ok(spec: dict, tmp_path: Path) -> None:
    config_path = tmp_path / "runtime_config.json"
    config_path.write_text(json.dumps(spec), encoding="utf-8")
    assert parity.main(["--validate", str(config_path)]) == 0


def test_parity_cli_tampered_config_fails(spec: dict, tmp_path: Path) -> None:
    config = copy.deepcopy(spec)
    config["model"]["endpoint"]["model_name"] = "qwen-other"
    config_path = tmp_path / "runtime_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    assert parity.main(["--validate", str(config_path)]) == 1


def test_parity_cli_malformed_spec_fails_naming_field(
    spec: dict, tmp_path: Path
) -> None:
    tampered = copy.deepcopy(spec)
    del tampered["generation"]  # missing required field
    tampered_path = tmp_path / "tampered_spec.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        parity.load_spec(tampered_path)
    assert "generation" in str(excinfo.value)
    assert parity.main(["--validate", "--spec", str(tampered_path)]) == 1


def test_parity_cli_wrong_type_spec_fails_naming_field(
    spec: dict, tmp_path: Path
) -> None:
    tampered = copy.deepcopy(spec)
    tampered["generation"]["temperature"] = "hot"  # wrong type
    tampered_path = tmp_path / "tampered_spec.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        parity.load_spec(tampered_path)
    assert "temperature" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# preflight report schema + exit-code contract
# --------------------------------------------------------------------------- #


def _check(status: str, decision: str | None) -> dict:
    return {
        "status": status,
        "measured": {"fixture": True},
        "decision": decision,
        "remediation": ["fixture remediation"] if decision else [],
    }


def _report(checks: dict[str, dict]) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-08-23T00:00:00+00:00",
        "host": {"platform": "fixture", "python": "3.12.13"},
        "timeouts_seconds": {"hf_probe": 15.0},
        "checks": checks,
        "overall": "degraded",
    }


def test_preflight_report_fixture_validates(report_schema: dict) -> None:
    report = _report(
        {
            "docker": _check("ok", None),
            "huggingface": _check(
                "degraded", "set HF_ENDPOINT=https://hf-mirror.com for downloads"
            ),
            "disk": _check("ok", None),
            "opencode_serve": _check("failed", "bridge fix or baseline degradation"),
        }
    )
    jsonschema.validate(report, report_schema)  # raises on failure


def test_preflight_report_bad_status_rejected(report_schema: dict) -> None:
    report = _report(
        {
            "docker": _check("exploded", None),
            "huggingface": _check("ok", None),
            "disk": _check("ok", None),
            "opencode_serve": _check("ok", None),
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, report_schema)


def test_preflight_report_missing_check_rejected(report_schema: dict) -> None:
    report = _report(
        {
            "docker": _check("ok", None),
            "huggingface": _check("ok", None),
            "disk": _check("ok", None),
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, report_schema)


def test_exit_code_zero_when_all_settled() -> None:
    report = _report(
        {
            "docker": _check("ok", None),
            "huggingface": _check("degraded", "use mirror"),
            "disk": _check("ok", None),
            "opencode_serve": _check("failed", "degrade baseline side, disclosed"),
        }
    )
    assert preflight.exit_code_for(report) == 0


def test_exit_code_one_when_failure_has_no_decision() -> None:
    report = _report(
        {
            "docker": _check("failed", None),  # unsettled
            "huggingface": _check("ok", None),
            "disk": _check("ok", None),
            "opencode_serve": _check("ok", None),
        }
    )
    assert preflight.exit_code_for(report) == 1


# --------------------------------------------------------------------------- #
# preflight checks: deterministic paths (no network)
# --------------------------------------------------------------------------- #


def _offline_env(**extra: str) -> dict:
    env = {
        "HARNESS_BENCH_SKIP_OPENCODE": "1",
        "HARNESS_BENCH_POISON_HOSTS": "huggingface.co,hf-mirror.com",
    }
    env.update(extra)
    return env


def test_hf_all_hosts_poisoned_records_skip_decision() -> None:
    check = preflight.check_huggingface(_offline_env())
    assert check["status"] == "failed"
    assert check["decision"]
    assert "HF_ENDPOINT" in " ".join(check["remediation"])
    report = preflight.run_checks(env=_offline_env())
    assert report["checks"]["huggingface"]["status"] == "failed"
    assert preflight.exit_code_for(report) == 0  # decision recorded -> settled


def test_hf_degraded_to_mirror_names_hf_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_status(url: str, timeout: float) -> tuple[int, float]:
        if url.startswith("https://huggingface.co"):
            return 0, 1.0  # unreachable
        return 200, 12.0  # mirror answers

    monkeypatch.setattr(preflight, "_http_status", fake_status)
    check = preflight.check_huggingface({})
    assert check["status"] == "degraded"
    assert "HF_ENDPOINT=https://hf-mirror.com" in check["decision"]
    assert check["measured"]["endpoint_used"] == "https://hf-mirror.com"


def test_injected_failure_forces_nonzero_exit() -> None:
    env = _offline_env(HARNESS_BENCH_INJECT_FAILURE="disk")
    report = preflight.run_checks(env=env)
    assert report["checks"]["disk"]["status"] == "failed"
    assert report["checks"]["disk"]["decision"] is None
    assert report["overall"] == "failed"
    assert preflight.exit_code_for(report) == 1


def test_disk_check_ok_on_real_fs(tmp_path: Path) -> None:
    check = preflight.check_disk({}, tmp_path)
    assert check["status"] in ("ok", "degraded", "failed")
    assert check["measured"]["required_gb"] == preflight.DISK_REQUIRED_GB
    if check["status"] != "ok":
        assert check["decision"]


def test_opencode_probe_part_normalization() -> None:
    legacy_message = {
        "info": {"role": "assistant"},
        "parts": [
            {"type": "text", "text": "SCORE=0.8"},
            {"type": "tool", "tool": "vibe-trading_sentiment"},
        ],
    }
    v2_message = {
        "type": "assistant",
        "content": [
            {"type": "text", "text": "SCORE=0.8"},
            {"type": "tool", "tool": "vibe-trading_sentiment"},
        ],
    }
    for message in (legacy_message, v2_message):
        parts = opencode_probe._iter_parts(message)
        assert len(parts) == 2
        assert any(opencode_probe._part_is_tool(p) for p in parts)
        assert "vibe-trading_sentiment" in opencode_probe._tool_names(parts)
        assert opencode_probe._part_text(parts[0]) == "SCORE=0.8"


def test_opencode_probe_rejects_malformed_message() -> None:
    assert opencode_probe._iter_parts({"unexpected": True}) == []
    assert opencode_probe._iter_parts(None) == []
    assert opencode_probe._iter_parts([{"type": "text", "text": "x"}]) == []
