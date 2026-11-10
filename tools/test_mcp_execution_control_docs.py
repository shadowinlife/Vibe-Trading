"""Content and link gate for the published MCP execution-control contract.

The checks read only tracked documentation and examples. They do not start the
entry, open the ledger or read a token. Every JSON request example that an
executor is told to send is validated against the live pydantic contract, and
every digest shown in the examples is recomputed from the same bytes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.factors.prototype_contract import factor_remote_id
from src.strategy_runs.backtest_materialize import (
    BacktestPrepareRequest,
    BacktestPrepareResponse,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    BacktestStartRequest,
    FactorStartRequest,
    StopRequest,
    backtest_input_digest,
    backtest_primitive_prompt,
    canonical_json_bytes,
    sha256_hex,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "OpencodeAgent/docs/MCP-EXECUTION-CONTROL.md"
WIKI_CARD_PATH = REPO_ROOT / "mymain-wiki/features/f14-mcp-execution-control.md"
DOC = DOC_PATH.read_text(encoding="utf-8")

ROUTES = [
    "GET /execution-instances?dispatch_key=",
    "GET /execution-instances/{remote_id}",
    "GET /execution-instances/{remote_id}/result",
    "POST /execution-instances",
    "POST /execution-instances/{remote_id}/stop",
    "POST /backtest/prepare",
    "POST /backtest/execute",
    "POST /factor/prototype",
    "POST /strategy-runs",
    "GET /strategy-runs/{vt_run_id}",
    "POST /strategy-runs/{vt_run_id}/cancel",
]

REFERENCED_PATHS = [
    "agent/strategy_run_server.py",
    "agent/src/api/backtest_prepare_routes.py",
    "agent/src/api/execution_control_routes.py",
    "agent/src/api/factor_prototype_routes.py",
    "agent/src/api/strategy_run_routes.py",
    "agent/src/strategy_runs/backtest_materialize.py",
    "agent/src/strategy_runs/control_contract.py",
    "agent/src/strategy_runs/control_service.py",
    "agent/src/strategy_runs/execution_start_service.py",
    "agent/src/strategy_runs/execution_stop_service.py",
    "agent/src/strategy_runs/native_supervisor.py",
]


def _json_block_after(marker: str) -> dict[str, object]:
    """Return the first fenced JSON object at or after *marker*."""
    start = DOC.index(marker)
    fence = DOC.index("```json", start)
    end = DOC.index("```", fence + len("```json"))
    return json.loads(DOC[fence + len("```json") : end])


@pytest.mark.parametrize("route", ROUTES)
def test_route_is_documented_with_its_status_codes(route: str) -> None:
    assert f"`{route}" in DOC


def test_status_codes_and_fixed_errors_are_documented() -> None:
    for status in ("200", "202", "400", "401", "403", "404", "409", "422", "503"):
        assert f"`{status}`" in DOC
    for error in (
        "invalid_request",
        "invalid_input",
        "not_found",
        "identity_conflict",
        "prepare_required",
        "idempotency_conflict",
        "input_digest_mismatch",
        "storage_unavailable",
        "worker_start_failed",
        "strategy_not_found",
        "version_not_found",
        "unauthorized",
        "forbidden",
    ):
        assert error in DOC


def test_digest_formulas_and_canonical_json_are_documented() -> None:
    assert '"schema": "backtest-input-v1"' in DOC
    for key in (
        "config_sha256",
        "source_sha256",
        "data_source",
        "data_as_of",
        "snapshot_basis",
    ):
        assert key in DOC
    assert "declared_digest" in DOC and "build_fingerprint" in DOC
    assert "excludes RM's `declared_digest` and `build_fingerprint`" in DOC
    assert "backtest-local-data-v1" in DOC
    assert '"schema": "factor-registry-v1"' in DOC
    assert "alpha-zoo-v1+alpha-bench-v1+factor-analysis-v1" in DOC
    assert "sort_keys=True" in DOC
    assert 'separators=(",", ":")' in DOC
    assert "ensure_ascii=True" in DOC
    assert "allow_nan=False" in DOC


def test_neutral_config_and_cost_semantics_are_documented() -> None:
    for field in (
        "strategy_id",
        "strategy_version",
        "market",
        "symbols",
        "start_date",
        "end_date",
        "bar_interval",
        "data_source",
        "data_as_of",
        "snapshot_basis",
        "initial_cash",
        "currency",
        "commission_bps",
        "tax_bps",
        "slippage_bps",
    ):
        assert field in DOC
    assert "start_date < end_date <= data_as_of" in DOC
    assert "duplicate-provenance check is an\n  equality check" in DOC
    assert "no minimum commission, no transfer fee, no default stamp tax" in DOC
    assert "commission = notional * commission_bps / 10000" in DOC
    assert "tax        = notional * tax_bps / 10000" in DOC
    assert "buy_price  = raw_price * (1 + slippage_bps / 10000)" in DOC
    assert "sell_price = raw_price * (1 - slippage_bps / 10000)" in DOC
    assert "fee 2.505" in DOC and "fee 7.485" in DOC


def test_start_and_stop_identity_binding_is_documented() -> None:
    assert "begin_start" in DOC
    assert "The fixed prompt byte for byte" in DOC or "byte for byte" in DOC
    assert (
        "Do not modify, rewrite, or regenerate config.json or code/signal_engine.py."
        in DOC
    )
    for evidence in (
        "`terminal_record`",
        "`process_exit`",
        "`process_absent`",
        "`ttl_expired`",
        "`explicit_absence`",
    ):
        assert evidence in DOC
    assert "A stop is persisted as `requested` before any signal." in DOC
    assert "cannot be upgraded to `confirmed`" in DOC
    assert "cannot be downgraded" in DOC
    assert "There is no bare\n  pid kill" in DOC
    assert "After a process restart" in DOC
    assert "reads `unknown`" in DOC


def test_legacy_projection_and_version_boundary_are_documented() -> None:
    assert "run_key" in DOC
    assert "identity_conflict" in DOC
    assert "There is no second version-query route." in DOC
    assert "0003 precheck boundary" in DOC
    assert "`strategy_not_found`" in DOC and "`version_not_found`" in DOC


def test_result_and_horizon_semantics_are_documented() -> None:
    for field in ("result_status", "result", "progress", "has_more"):
        assert field in DOC
    assert "summary 1.0" in DOC
    assert "run_card.json" in DOC
    assert "quality_bounds" in DOC
    assert "artifact_paths" in DOC
    assert (
        "`artifacts` has exactly the keys `ic_series`, `ic_summary`, `group_equity`."
        in DOC
    )
    assert "positional trading bars" in DOC
    assert "label_end < split_date" in DOC
    assert "factor_date >= split_date" in DOC
    assert "label_end <= end_date" in DOC
    assert "label_end <= as_of" in DOC
    assert "masked to `NaN`" in DOC
    assert "insufficient_data" in DOC


def test_rm_prerequisites_and_delivery_state_are_documented() -> None:
    assert "D-3 versus RM prepare digest" in DOC
    assert "`declared_digest`" in DOC and "`build_fingerprint`" in DOC
    assert "No `backtest.execute` receipt enum" in DOC
    assert "No live adapters" in DOC
    assert "academic_carhart_mom.min_warmup_bars=253" in DOC
    assert "min_warmup_bars=252" in DOC or "uses `252`" in DOC
    assert "85e98816be958990e2cf73c3695ad81e0e6511a779533c469156dff568ef7fa4" in DOC
    assert "freeze the newly" in DOC and "published VT metadata" in DOC
    assert "verified_by` is `pending" in DOC
    assert "verified_at` is `pending" in DOC
    assert "delivered-candidate" in DOC
    assert "Gates G-control, G-backtest and G-factor remain `pending`." in DOC
    assert "Human confirmation is required." in DOC
    assert "Stop requires `deadline_at`" in DOC
    assert "no receipt exists" in DOC


def test_examples_validate_against_live_contracts() -> None:
    prepare = _json_block_after("### 6.1 Request")
    request = BacktestPrepareRequest.model_validate(prepare)
    prepare_response = _json_block_after("### 6.2 Success response")
    stored = BacktestPrepareResponse.model_validate(prepare_response)
    assert stored.contract_version == CONTRACT_VERSION

    config_bytes = canonical_json_bytes(request.config)
    source_bytes = request.source.text.encode("utf-8")
    config_sha256 = sha256_hex(config_bytes)
    source_sha256 = sha256_hex(source_bytes)
    assert config_sha256 == stored.config_sha256
    assert source_sha256 == stored.source_sha256 == request.source.sha256
    digest = backtest_input_digest(
        config_sha256,
        source_sha256,
        request.data_source,
        request.data_as_of,
        request.snapshot_basis,
    )
    assert digest == stored.input_digest

    start_payload = _json_block_after("### 7.1 Request")
    start = BacktestStartRequest.model_validate(start_payload)
    assert start.run_id == Path(stored.run_dir).name
    assert start.run_dir == stored.run_dir
    assert start.input_digest == stored.input_digest
    assert start.config_sha256 == stored.config_sha256
    assert start.source_sha256 == stored.source_sha256
    assert start.prompt == backtest_primitive_prompt(stored.run_dir)
    accepted = _json_block_after("### 7.2 Accepted response")
    assert accepted == {
        "remote_id": start.run_id,
        "dispatch_key": start.dispatch_key,
        "kind": "backtest",
        "status": "running",
        "replayed": False,
        "contract_version": CONTRACT_VERSION,
    }

    factor_payload = _json_block_after("### 8.1 Request")
    factor = FactorStartRequest.model_validate(factor_payload)
    assert factor_remote_id(factor.dispatch_key, factor.spec) == (
        "factor-817d4e829783d7f27df13621bc7e0aa5"
    )

    stop_payload = _json_block_after("### 9.1 Request")
    stop = StopRequest.model_validate(stop_payload)
    assert stop.remote_id == "mcp-long-demo-1"
    assert stop.deadline_at.tzinfo is not None


def test_response_examples_have_exact_field_sets() -> None:
    assert set(_json_block_after("### 3.1 `not_dispatched`")) == {
        "state",
        "dispatch_key",
        "contract_version",
    }
    assert set(_json_block_after("### 3.2 `dispatched`")) == {
        "state",
        "dispatch_key",
        "remote_id",
        "status",
        "run_dir",
        "started_at",
        "finished_at",
        "error_kind",
        "contract_version",
    }
    assert set(_json_block_after("### 3.3 `absent`")) == {
        "state",
        "dispatch_key",
        "remote_id",
        "last_status",
        "absence_evidence",
        "observed_at",
        "contract_version",
    }
    assert set(_json_block_after("## 4. Status")) == {
        "remote_id",
        "dispatch_key",
        "status",
        "started_at",
        "finished_at",
        "error_kind",
        "run_dir",
        "contract_version",
    }
    assert set(_json_block_after("## 5. Result read")) == {
        "remote_id",
        "status",
        "result_status",
        "result",
        "progress",
        "has_more",
        "contract_version",
    }
    assert set(_json_block_after("### 6.2 Success response")) == {
        "run_dir",
        "config_sha256",
        "source_sha256",
        "input_digest",
        "data_source",
        "data_as_of",
        "data_digest",
        "data_digest_status",
        "contract_version",
    }
    assert set(_json_block_after("### 9.2 Response")) == {
        "remote_id",
        "dispatch_key",
        "stop_key",
        "stop_state",
        "evidence_kind",
        "resolved_at",
        "replayed",
        "contract_version",
    }
    factor_result = _json_block_after("### 8.3 Factor result")
    assert set(factor_result) == {
        "remote_id",
        "dispatch_key",
        "registry_digest",
        "tool_schema_version",
        "contract_version",
        "status",
        "completed",
        "total",
        "candidates",
        "quality_bounds",
        "artifacts",
        "artifact_paths",
    }
    assert set(factor_result["artifacts"]) == {
        "ic_series",
        "ic_summary",
        "group_equity",
    }


def test_document_has_no_forbidden_content() -> None:
    lowered = DOC.lower()
    assert not re.search(r"\bscp\b", lowered)
    assert "\u2014" not in DOC and "\u2013" not in DOC
    assert "http://" not in DOC and "https://" not in DOC
    private_range = (
        r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
        r"|\b(?:192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    )
    assert not re.search(private_range, DOC)
    assert not re.search(r"Bearer\s+[A-Za-z0-9._~-]{20,}", DOC)
    assert not re.search(r"(?:sk-|ghp_|AKIA)[A-Za-z0-9]{8,}", DOC)
    for overclaim in (
        "gates verified",
        "gate verified",
        "E2E passed",
        "receipt issued",
        "verified_by=human",
        "verified_at=202",
    ):
        assert overclaim not in DOC
    assert "A10" in DOC and "A11" in DOC
    assert "not a gate receipt" in DOC


def test_referenced_paths_and_wiki_links_exist() -> None:
    for relative in REFERENCED_PATHS:
        assert (REPO_ROOT / relative).is_file(), relative
    card = WIKI_CARD_PATH.read_text(encoding="utf-8")
    assert "MCP-EXECUTION-CONTROL.md" in card
    features_readme = (REPO_ROOT / "mymain-wiki/features/README.md").read_text(
        encoding="utf-8"
    )
    assert "f14-mcp-execution-control.md" in features_readme
    divergence = (REPO_ROOT / "mymain-wiki/branch/MYMAIN_DIVERGENCE.md").read_text(
        encoding="utf-8"
    )
    assert "F14" in divergence
    agent_routes = (REPO_ROOT / "mymain-wiki/AGENTS.md").read_text(encoding="utf-8")
    assert "F14" in agent_routes
    opencode_agents = (REPO_ROOT / "OpencodeAgent/AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "MCP-EXECUTION-CONTROL.md" in opencode_agents
