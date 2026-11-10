from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from src.strategy_runs import control_contract as cc

Payload = dict[str, cc.JsonValue]
CONFIG_SHA, SOURCE_SHA, INPUT_DIGEST = "a" * 64, "b" * 64, "e" * 64
RUN_DIR = "/home/opencode/.vibe-trading/runs/mcp-long-job-2-3"
START_ADAPTER = TypeAdapter(cc.ExecutionStartRequest)


def _digest_inputs() -> dict[str, str]:
    return {
        "config_sha256": CONFIG_SHA,
        "source_sha256": SOURCE_SHA,
        "data_source": "clickhouse",
        "data_as_of": "2026-11-08",
        "snapshot_basis": "local-csv-v1",
    }


def _backtest_payload() -> Payload:
    return {
        "dispatch_key": "user-1.job-2.attempt-3",
        "kind": "backtest",
        "run_id": "mcp-long-job-2-3",
        "run_dir": RUN_DIR,
        "input_digest": INPUT_DIGEST,
        "config_sha256": CONFIG_SHA,
        "source_sha256": SOURCE_SHA,
        "build_fingerprint": "c" * 64,
        "prompt": "run the frozen backtest",
        "contract_version": cc.CONTRACT_VERSION,
    }


def _factor_spec() -> Payload:
    return {
        "registry_digest": "d" * 64,
        "candidate_ids": ["alpha_001", "alpha_002"],
        "symbols": ["000001.SZ", "600000.SH"],
        "start_date": "2025-01-01",
        "split_date": "2025-06-01",
        "end_date": "2025-08-01",
        "source": "alpha_zoo",
        "as_of": "2025-08-01",
        "horizon_days": 5,
        "n_groups": 5,
    }


def _factor_payload() -> Payload:
    return {
        "dispatch_key": "user-1.factor-2.attempt-1",
        "kind": "factor",
        "spec": _factor_spec(),
        "contract_version": cc.CONTRACT_VERSION,
    }


def _stop_payload() -> Payload:
    return {
        "dispatch_key": "user-1.job-2.attempt-3",
        "remote_id": "mcp-long-job-2-3",
        "stop_key": "stop-user-1-job-2-attempt-3",
        "deadline_at": "2026-11-09T12:30:00Z",
        "contract_version": cc.CONTRACT_VERSION,
    }


def _rejects(model: type[BaseModel], payload: Payload) -> None:
    with pytest.raises(ValidationError):
        model(**payload)


VERSIONED_CASES = (
    (cc.BacktestStartRequest, _backtest_payload()),
    (cc.FactorStartRequest, _factor_payload()),
    (cc.StopRequest, _stop_payload()),
)
EXTRA_CASES = ((cc.FactorPrototypeSpec, _factor_spec()), *VERSIONED_CASES)


def test_vocabulary_is_closed_and_versioned() -> None:
    # Given / When / Then: consumers can enumerate only the frozen wire values.
    assert cc.CONTRACT_VERSION == "mcp-execution-control-v1"
    assert cc.FIND_STATES == ("not_dispatched", "dispatched", "absent")
    assert cc.RUN_STATES == ("running", "completed", "failed", "cancelled", "unknown")
    assert cc.STOP_STATES == ("requested", "confirmed", "unknown")
    assert cc.STOP_EVIDENCE == (
        "process_exit",
        "terminal_record",
        "process_absent",
        "ttl_expired",
        "explicit_absence",
    )
    assert set(cc.ERROR_KINDS) >= {
        "timeout",
        "runner_error",
        "strategy_not_found",
        "version_not_found",
        "input_digest_mismatch",
    }
    assert cc.DATA_DIGEST_STATUSES == ("verified", "unavailable")


def test_request_fields_are_exact_and_required() -> None:
    # Given exact schemas, when a required field is omitted, then parsing fails.
    for model, payload in EXTRA_CASES:
        assert set(model.model_fields) == set(payload)
        assert all(field.is_required() for field in model.model_fields.values())
    spec = _factor_spec()
    spec.pop("n_groups")
    _rejects(cc.FactorPrototypeSpec, spec)


def test_canonical_json_is_key_stable_compact_and_ascii() -> None:
    # Given two documents whose insertion order differs.
    left = {"z": "中文", "a": 1, "nested": {"b": True, "a": None}}
    right = {"nested": {"a": None, "b": True}, "a": 1, "z": "中文"}

    # When canonicalized, then exact bytes match the independent representation.
    expected = b'{"a":1,"nested":{"a":null,"b":true},"z":"\\u4e2d\\u6587"}'
    assert cc.canonical_json_bytes(left) == cc.canonical_json_bytes(right) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    # Given a non-finite JSON number, when encoded, then validation fails.
    with pytest.raises(cc.ContractValidationError, match="payload"):
        cc.canonical_json_bytes({"value": value})


def test_sha256_helpers_return_and_require_lowercase_hex() -> None:
    # Given known bytes and a valid digest, when hashed/validated, then hex is exact.
    assert (
        cc.sha256_hex(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


@pytest.mark.parametrize("value", ["", "a" * 63, "A" * 64, "g" * 64, None, b"abc"])
def test_ensure_sha256_hex_rejects_malformed_values(value: str | bytes | None) -> None:
    # Given malformed digest input, when validated, then the field is identified.
    with pytest.raises(cc.ContractValidationError, match="source_sha256"):
        cc.ensure_sha256_hex(value, "source_sha256")


def test_backtest_input_digest_matches_d3_known_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the six D-3 fields and no ambient checkout state.
    inputs = _digest_inputs()

    # When the digest is recomputed after changing cwd.
    digest = cc.backtest_input_digest(**inputs)
    monkeypatch.chdir(tmp_path)

    # Then it matches the independently calculated value and is cwd-independent.
    assert digest == "cbb26ca21e6ed13bc7359a1288526560bb5a7ac46609db9ce4abf6d77d92f419"
    assert cc.backtest_input_digest(**inputs) == digest


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("config_sha256", "c" * 64),
        ("source_sha256", "d" * 64),
        ("data_source", "local"),
        ("data_as_of", "2026-11-09"),
        ("snapshot_basis", "local-csv-v2"),
    ],
)
def test_d3_digest_changes_per_input(field: str, changed_value: str) -> None:
    # Given one changed D-3 component, when hashed, then the identity changes.
    original = cc.backtest_input_digest(**_digest_inputs())
    changed_inputs = {**_digest_inputs(), field: changed_value}
    assert cc.backtest_input_digest(**changed_inputs) != original


@pytest.mark.parametrize("value", ["", "bad:key", "bad/key", "x" * 201])
def test_backtest_dispatch_key_must_be_bounded_and_url_safe(value: str) -> None:
    # Given an invalid dispatch key, when parsed, then the whole request fails.
    _rejects(cc.BacktestStartRequest, {**_backtest_payload(), "dispatch_key": value})


@pytest.mark.parametrize("field", ["dispatch_key", "remote_id", "stop_key"])
@pytest.mark.parametrize("value", ["", "bad:key", "bad/key"])
def test_stop_tokens_must_be_url_safe(field: str, value: str) -> None:
    # Given an invalid stop identity token, when parsed, then validation fails.
    _rejects(cc.StopRequest, {**_stop_payload(), field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("dispatch_key", "x" * 201), ("remote_id", "x" * 129), ("stop_key", "x" * 129)],
)
def test_stop_tokens_reject_over_length_values(field: str, value: str) -> None:
    # Given an over-length identity token, when parsed, then validation fails.
    _rejects(cc.StopRequest, {**_stop_payload(), field: value})


@pytest.mark.parametrize(
    "field", ["input_digest", "config_sha256", "source_sha256", "build_fingerprint"]
)
@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64])
def test_backtest_digest_format(field: str, value: str) -> None:
    # Given a malformed digest field, when parsed, then validation fails.
    _rejects(cc.BacktestStartRequest, {**_backtest_payload(), field: value})


@pytest.mark.parametrize(("model", "payload"), EXTRA_CASES)
def test_extra_fields_forbidden(model: type[BaseModel], payload: Payload) -> None:
    # Given an undeclared property, when parsed, then extra_forbidden is raised.
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model(**{**payload, "undeclared": "must-not-pass"})


def test_execution_start_rejects_unknown_kind() -> None:
    # Given an unregistered kind, when the discriminated union is parsed, then it fails.
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        START_ADAPTER.validate_python({**_backtest_payload(), "kind": "unknown"})


@pytest.mark.parametrize(("model", "payload"), VERSIONED_CASES)
@pytest.mark.parametrize("version", [None, "mcp-execution-control-v0"])
def test_contract_version_required(
    model: type[BaseModel], payload: Payload, version: str | None
) -> None:
    # Given a missing/wrong version, when parsed, then no execution model is created.
    broken = {**payload, "contract_version": version}
    if version is None:
        broken.pop("contract_version")
    _rejects(model, broken)


@pytest.mark.parametrize("value", ["2026-11-09T12:30:00", 0])
def test_stop_rejects_naive_or_non_iso_datetime(value: str | int) -> None:
    # Given a naive/non-ISO datetime, when parsed, then timezone validation fails.
    with pytest.raises(ValidationError, match="timezone"):
        cc.StopRequest(**{**_stop_payload(), "deadline_at": value})


@pytest.mark.parametrize(
    "override",
    [
        {"candidate_ids": []},
        {"candidate_ids": ["alpha_001", "alpha_001"]},
        {"candidate_ids": ["alpha_001", ""]},
        {"candidate_ids": [f"alpha_{index:03d}" for index in range(201)]},
        {"symbols": []},
        {"symbols": ["000001.SZ", "000001.SZ"]},
        {"symbols": ["000001.SZ", ""]},
        {"symbols": [f"{index:06d}.SZ" for index in range(501)]},
    ],
)
def test_factor_collection_limits(override: Payload) -> None:
    # Given invalid candidate/symbol collections, when parsed, then validation fails.
    _rejects(cc.FactorPrototypeSpec, {**_factor_spec(), **override})


@pytest.mark.parametrize("value", [0, 253, True, "5"])
def test_factor_horizon_bounds_are_strict(value: int | bool | str) -> None:
    # Given invalid horizon values, when parsed, then validation fails.
    _rejects(cc.FactorPrototypeSpec, {**_factor_spec(), "horizon_days": value})


@pytest.mark.parametrize("value", [1, 21, True, "5"])
def test_factor_group_bounds_are_strict(value: int | bool | str) -> None:
    # Given invalid group counts, when parsed, then validation fails.
    _rejects(cc.FactorPrototypeSpec, {**_factor_spec(), "n_groups": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "other"), ("registry_digest", "D" * 64), ("registry_digest", "d" * 63)],
)
def test_factor_source_and_registry_digest_are_validated(
    field: str, value: str
) -> None:
    # Given an invalid source/registry digest, when parsed, then validation fails.
    _rejects(cc.FactorPrototypeSpec, {**_factor_spec(), field: value})


@pytest.mark.parametrize(
    "override",
    [
        {"start_date": "2025-06-01"},
        {"start_date": "2025-06-02"},
        {"split_date": "2025-08-02"},
        {"as_of": "2025-07-31"},
    ],
)
def test_factor_chronology_violations_are_rejected(override: Payload) -> None:
    # Given an invalid selection/OOS/as-of chronology, when parsed, then it fails.
    _rejects(cc.FactorPrototypeSpec, {**_factor_spec(), **override})


def test_valid_start_union_and_stop_payloads_are_accepted() -> None:
    # Given valid payloads, when parsed through their public seams.
    backtest = START_ADAPTER.validate_python(_backtest_payload())
    factor = START_ADAPTER.validate_python(_factor_payload())
    cc.StopRequest(**_stop_payload())

    # Then kind, timezone and the bounded factor values are preserved.
    assert isinstance(backtest, cc.BacktestStartRequest)
    assert isinstance(factor, cc.FactorStartRequest)


def test_boundary_values_are_accepted() -> None:
    # Given every documented inclusive boundary, when parsed.
    spec = _factor_spec()
    spec.update(
        candidate_ids=[f"alpha_{index:03d}" for index in range(200)],
        symbols=[f"{index:06d}.SZ" for index in range(500)],
        horizon_days=252,
        n_groups=20,
    )
    factor = cc.FactorPrototypeSpec(**spec)
    stop_payload = _stop_payload()
    stop_payload.update(
        dispatch_key="d" * 200,
        remote_id="r" * 128,
        stop_key="s" * 128,
        deadline_at="2026-11-09T12:30:00+08:00",
    )
    stop = cc.StopRequest(**stop_payload)

    # Then all limits and offset-aware timestamps are retained without coercion.
    assert (len(factor.candidate_ids), len(factor.symbols)) == (200, 500)
    assert (factor.horizon_days, factor.n_groups) == (252, 20)
    assert (len(stop.dispatch_key), len(stop.remote_id), len(stop.stop_key)) == (
        200,
        128,
        128,
    )


def test_factor_accepts_rm_horizon_alias_and_inclusive_oos_boundary() -> None:
    # Given RM's horizon name and split_date == end_date, when parsed.
    spec = _factor_spec()
    spec.pop("horizon_days")
    spec.update(forward_return_horizon_days=1, n_groups=2, split_date=spec["end_date"])
    parsed = cc.FactorPrototypeSpec(**spec)

    # Then the VT attribute and RM serialization retain the declared horizon.
    assert (parsed.horizon_days, parsed.n_groups) == (1, 2)
    assert parsed.model_dump(by_alias=True)["forward_return_horizon_days"] == 1
