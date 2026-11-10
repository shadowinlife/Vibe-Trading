"""Live frozen-factor start, replay, result and invalid-input scenarios."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from execution_control_common import (
    CANDIDATE_ID,
    CONTRACT,
    FACTOR_KEYS,
    FACTOR_RESULT_KEYS,
    REGISTRY_DIGEST,
    JsonValue,
    mapping,
    require,
)
from execution_control_harness import Harness
from execution_control_payloads import factor_payload


@dataclass(frozen=True, slots=True)
class FactorEvidence:
    accepted: dict[str, JsonValue]
    outer: dict[str, JsonValue]
    invalid_count: int


def verify_factor(h: Harness) -> FactorEvidence:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "agent/tests/fixtures/factor_prototype/VT00.csv"
    )
    dates = [line.split(",", 1)[0] for line in fixture.read_text().splitlines()[1:]]
    factor_key = "happy.factor"
    factor_body = factor_payload(factor_key, dates)
    accepted = h.expect(
        h.request("A1", "factor-start", "POST", "/factor/prototype", factor_body), 202
    )
    factor_id = str(accepted["remote_id"])
    replay = h.expect(
        h.request("A1", "factor-replay", "POST", "/factor/prototype", factor_body), 202
    )
    require(
        replay["remote_id"] == factor_id and replay["replayed"] is True, "factor replay"
    )
    changed = copy.deepcopy(factor_body)
    mapping(changed["spec"])["symbols"] = ["local:VT00"]
    h.expect(
        h.request("A1", "factor-conflict", "POST", "/factor/prototype", changed),
        409,
        {
            "error": "idempotency_conflict",
            "original_remote_id": factor_id,
            "contract_version": CONTRACT,
        },
    )
    require(
        h.wait_terminal("A8", factor_id)["status"] == "completed",
        "factor did not complete",
    )
    outer = h.expect(
        h.request(
            "A8", "factor-result", "GET", f"/execution-instances/{factor_id}/result"
        ),
        200,
    )
    result = mapping(outer["result"])
    require(
        set(outer) == FACTOR_RESULT_KEYS
        and outer["batch_status"] == "completed"
        and set(result) == FACTOR_KEYS,
        "factor result shape",
    )
    require(
        result["registry_digest"] == REGISTRY_DIGEST
        and result["contract_version"] == CONTRACT,
        "factor provenance",
    )
    require(
        isinstance(result["quality_bounds"], dict)
        and {"selection_window", "oos_window"}
        <= set(mapping(result["quality_bounds"])),
        "quality bounds",
    )
    require(
        set(mapping(result["artifacts"]))
        == {"ic_series", "ic_summary", "group_equity"},
        "factor artifact keys",
    )
    require(bool(result["artifact_paths"]), "factor artifact_paths empty")
    for artifact_path in result["artifact_paths"]:
        relative = str(artifact_path).removeprefix("/vt-state/")
        require((h.state / relative).is_file(), f"missing factor artifact {relative}")
    h.save("factor-result.json", outer)
    factor_files = h.file_state(factor_id)
    h.expect(
        h.request(
            "A7", "factor-replay-no-recompute", "POST", "/factor/prototype", factor_body
        ),
        202,
    )
    require(h.file_state(factor_id) == factor_files, "factor replay rewrote result")

    invalid_cases = (
        ("empty-candidates", "candidate_ids", [], 400),
        ("duplicate-candidates", "candidate_ids", [CANDIDATE_ID, CANDIDATE_ID], 400),
        ("unknown-candidate", "candidate_ids", ["missing_candidate"], 400),
        ("empty-symbols", "symbols", [], 400),
        ("bad-chronology", "split_date", dates[240], 400),
        ("bad-horizon", "forward_return_horizon_days", 253, 400),
        ("bad-groups", "n_groups", 1, 400),
        ("bad-digest", "registry_digest", "0" * 64, 409),
    )
    for label, field, value, expected in invalid_cases:
        body = copy.deepcopy(factor_body)
        body["dispatch_key"] = "factor.invalid"
        mapping(body["spec"])[field] = value
        error = "input_digest_mismatch" if expected == 409 else "invalid_request"
        h.expect(
            h.request("A8", label, "POST", "/factor/prototype", body),
            expected,
            {"error": error, "contract_version": CONTRACT},
        )
        require(h.dispatch("factor.invalid") is None, f"{label} claimed dispatch")
        empty = h.expect(
            h.request(
                "A8",
                f"{label}-no-side-effect",
                "GET",
                "/execution-instances?dispatch_key=factor.invalid",
            ),
            200,
        )
        require(empty["state"] == "not_dispatched", f"{label} side effect")
    extra = copy.deepcopy(factor_body) | {"run_dir": "/tmp/caller-owned"}
    extra["dispatch_key"] = "factor.invalid"
    h.expect(
        h.request("A8", "caller-run-dir", "POST", "/factor/prototype", extra),
        400,
        {"error": "invalid_request", "contract_version": CONTRACT},
    )
    require(h.dispatch("factor.invalid") is None, "caller run_dir claimed dispatch")
    return FactorEvidence(accepted, outer, len(invalid_cases) + 1)
