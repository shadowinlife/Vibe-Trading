"""Table-driven contract tests for the strategy spec validator.

Valid specs must produce no gaps; invalid specs must produce the exact gap
codes, at the exact fields, in the documented order. The allowlist is checked
against the live tool registry so a renamed tool breaks the suite instead of a
strategy run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.strategy_library.spec import (
    ALLOWED_STRATEGY_TOOLS,
    FORBIDDEN_TOOL_PREFIXES,
    MIN_WORKFLOW_STEPS,
    OUTPUT_CONTRACT_FORMATS,
    SPEC_KINDS,
    Gap,
    OutputContract,
    SpecInput,
    SpecStep,
    StrategySpec,
    is_allowed_tool,
    validate_spec,
)
from src.tools import build_registry

FROZEN_TOOL_NAMES = frozenset(
    {
        "search_symbol",
        "get_market_data",
        "technical_indicators",
        "get_fundamentals",
        "get_financial_statements",
        "get_stock_news",
        "get_fund_flow",
        "get_sector_info",
        "get_northbound_flow",
        "get_margin_trading",
        "get_research_reports",
        "get_sec_filings",
        "web_search",
        "read_url",
        "backtest",
        "paper_trade_step",
        "quantlib_call",
        "factor_analysis",
        "alpha_zoo",
        "list_strategies",
        "get_strategy_evidence",
    }
)


def step(index: int, tool: str = "get_market_data") -> dict[str, Any]:
    return {
        "id": f"s{index}",
        "action": f"action {index}",
        "tool": tool,
        "output": f"output {index}",
    }


def workflow(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "northbound-flow-monitor",
        "kind": "workflow",
        "thesis": "Northbound flow leads large-cap demand.",
        "universe": "csi300",
        "inputs": [
            {
                "name": "flow",
                "source": "get_northbound_flow",
                "required": True,
                "missing_policy": "abort",
            }
        ],
        "preconditions": ["the trading calendar has a session today"],
        "steps": [step(1), step(2), step(3)],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "net_flow"],
            "destination": "briefing",
        },
        "failure_handling": "stop and report the failing step",
        "schedule_hint": "weekdays 09:00 Asia/Shanghai",
        "evidence_refs": ["docs/strategies/northbound.md"],
    }
    spec.update(overrides)
    return spec


def code_spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "momentum-code",
        "kind": "code",
        "thesis": "Twelve-month momentum persists.",
        "universe": "csi300",
        "output_contract": {"format": "json", "required_fields": ["sharpe"]},
    }
    spec.update(overrides)
    return spec


VALID_CASES: list[tuple[str, Any]] = [
    ("workflow-four-steps", workflow(steps=[step(1), step(2), step(3), step(4)])),
    ("workflow-minimum-three-steps", workflow()),
    (
        "workflow-edge-tools",
        workflow(steps=[step(1, "backtest"), step(2, "quantlib_call"), step(3, "alpha_zoo")]),
    ),
    (
        "workflow-all-allowlisted-tools",
        workflow(
            steps=[
                step(index, tool)
                for index, tool in enumerate(sorted(FROZEN_TOOL_NAMES))
            ]
        ),
    ),
    (
        "workflow-optional-input",
        workflow(
            inputs=[
                {
                    "name": "news",
                    "source": "get_stock_news",
                    "required": False,
                    "missing_policy": "skip the sentiment step",
                }
            ]
        ),
    ),
    ("workflow-unknown-keys-ignored", {**workflow(), "author": "ops", "notes": "draft"}),
    (
        "workflow-dataclass-instance",
        StrategySpec(
            name="built-directly",
            kind="workflow",
            thesis="Direct construction matches the mapping shape.",
            universe="csi300",
            inputs=[SpecInput(name="prices", source="get_market_data")],
            preconditions=["market data bridge is reachable"],
            steps=[
                SpecStep(id="a", action="load", tool="get_market_data", output="bars"),
                SpecStep(id="b", action="compute", tool="technical_indicators", output="signals"),
                SpecStep(id="c", action="verify", tool="backtest", output="metrics"),
            ],
            output_contract=OutputContract(format="csv", required_fields=["date", "signal"]),
            failure_handling="report the failing step and stop",
        ),
    ),
    ("code-minimal", code_spec()),
    (
        "code-no-declarative-contract",
        code_spec(output_contract=None, preconditions=[], failure_handling=""),
    ),
    (
        "code-full-contract",
        code_spec(
            inputs=[{"name": "run_dir", "source": "backtest", "missing_policy": "fail"}],
            output_contract={
                "format": "json",
                "required_fields": ["sharpe", "max_drawdown"],
                "destination": "strategy detail",
            },
        ),
    ),
]

INVALID_CASES: list[tuple[str, Any, list[tuple[str, str]]]] = [
    ("missing-spec-none", None, [("missing_spec", "spec")]),
    (
        "empty-mapping",
        {},
        [
            ("bad_kind", "kind"),
            ("empty_name", "name"),
        ],
    ),
    ("blank-name", workflow(name="   "), [("empty_name", "name")]),
    ("bad-kind", workflow(kind="pipeline"), [("bad_kind", "kind")]),
    (
        "workflow-two-steps",
        workflow(steps=[step(1), step(2)]),
        [("too_few_steps", "steps")],
    ),
    ("workflow-zero-steps", workflow(steps=[]), [("too_few_steps", "steps")]),
    (
        "empty-preconditions",
        workflow(preconditions=[]),
        [("missing_preconditions", "preconditions")],
    ),
    (
        "blank-failure-handling",
        workflow(failure_handling="   "),
        [("missing_failure_handling", "failure_handling")],
    ),
    (
        "absent-output-contract",
        workflow(output_contract=None),
        [("missing_output_contract", "output_contract")],
    ),
    (
        "unsupported-output-format",
        workflow(
            output_contract={"format": "pdf", "required_fields": ["sharpe"]}
        ),
        [("missing_output_contract", "output_contract")],
    ),
    (
        "empty-required-fields",
        workflow(
            output_contract={"format": "json", "required_fields": []}
        ),
        [("missing_output_contract", "output_contract")],
    ),
    (
        "input-without-source",
        workflow(inputs=[{"name": "flow", "source": ""}]),
        [("missing_input_source", "inputs[0].source")],
    ),
    (
        "second-input-without-source",
        workflow(
            inputs=[
                {"name": "flow", "source": "get_northbound_flow"},
                {"name": "news", "source": "  "},
            ]
        ),
        [("missing_input_source", "inputs[1].source")],
    ),
    (
        "broker-tool-in-step",
        workflow(steps=[step(1), step(2, "trading_place_order"), step(3)]),
        [("tool_not_allowed", "steps[1].tool")],
    ),
    (
        "write-file-tool-in-step",
        workflow(steps=[step(1, "write_file"), step(2), step(3)]),
        [("tool_not_allowed", "steps[0].tool")],
    ),
    (
        "shell-tool-in-step",
        workflow(steps=[step(1, "bash"), step(2), step(3)]),
        [("tool_not_allowed", "steps[0].tool")],
    ),
    (
        "swarm-tool-in-step",
        workflow(steps=[step(1, "run_swarm"), step(2), step(3)]),
        [("tool_not_allowed", "steps[0].tool")],
    ),
    (
        "unknown-instrument-tool-in-step",
        workflow(steps=[step(1, "resolve_instrument"), step(2), step(3)]),
        [("tool_not_allowed", "steps[0].tool")],
    ),
    (
        "connector-prefix-tool-in-step",
        workflow(steps=[step(1, "connector_place_order"), step(2), step(3)]),
        [("tool_not_allowed", "steps[0].tool")],
    ),
    (
        "contract-and-trading-step",
        workflow(steps=[step(1), step(2), step(3, "trading_place_order")], output_contract=None),
        [
            ("missing_output_contract", "output_contract"),
            ("tool_not_allowed", "steps[2].tool"),
        ],
    ),
    (
        "every-gap-at-once",
        workflow(
            name="",
            inputs=[{"name": "flow", "source": ""}],
            preconditions=[],
            steps=[step(1, "trading_place_order"), step(2, "resolve_instrument")],
            output_contract=None,
            failure_handling="",
        ),
        [
            ("missing_failure_handling", "failure_handling"),
            ("missing_input_source", "inputs[0].source"),
            ("empty_name", "name"),
            ("missing_output_contract", "output_contract"),
            ("missing_preconditions", "preconditions"),
            ("too_few_steps", "steps"),
            ("tool_not_allowed", "steps[0].tool"),
            ("tool_not_allowed", "steps[1].tool"),
        ],
    ),
]


@pytest.fixture(scope="module")
def registered_tool_names() -> frozenset[str]:
    """Names the live registry exposes; built once for the whole module."""
    return frozenset(build_registry().tool_names)


@pytest.mark.parametrize(
    "spec_input",
    [case[1] for case in VALID_CASES],
    ids=[case[0] for case in VALID_CASES],
)
def test_valid_specs_produce_no_gaps(spec_input: Any) -> None:
    assert validate_spec(spec_input) == []


@pytest.mark.parametrize(
    "spec_input,expected",
    [case[1:] for case in INVALID_CASES],
    ids=[case[0] for case in INVALID_CASES],
)
def test_invalid_specs_report_exact_gaps_in_order(
    spec_input: Any,
    expected: list[tuple[str, str]],
) -> None:
    gaps = validate_spec(spec_input)

    assert [(gap.code, gap.field) for gap in gaps] == expected
    assert all(gap.message and gap.fix_hint for gap in gaps)


def test_case_table_has_at_least_twenty_cases() -> None:
    assert len(VALID_CASES) >= 8
    assert len(INVALID_CASES) >= 12
    assert len(VALID_CASES) + len(INVALID_CASES) >= 20


def test_code_spec_skips_workflow_only_contract_checks() -> None:
    """A code strategy is gated by its backtest run, not by a plan contract."""
    spec = code_spec(
        output_contract=None,
        preconditions=[],
        failure_handling="",
        steps=[step(1, "trading_place_order"), step(2, "write_file")],
    )

    assert validate_spec(spec) == []


def test_workflow_contract_and_tool_gaps_survive_the_scope_change() -> None:
    spec = workflow(
        steps=[step(1), step(2), step(3, "trading_place_order")],
        output_contract=None,
    )

    assert [(gap.code, gap.field) for gap in validate_spec(spec)] == [
        ("missing_output_contract", "output_contract"),
        ("tool_not_allowed", "steps[2].tool"),
    ]


def test_workflow_missing_only_the_contract_reports_that_gap() -> None:
    gaps = validate_spec(workflow(output_contract=None))

    assert [(gap.code, gap.field) for gap in gaps] == [
        ("missing_output_contract", "output_contract")
    ]


def test_gap_order_is_stable_and_sorted() -> None:
    spec = workflow(
        name="",
        preconditions=[],
        steps=[step(1, "bash"), step(2)],
        output_contract=None,
        failure_handling="",
    )

    first = validate_spec(spec)
    second = validate_spec(spec)

    assert first == second
    assert [(gap.field, gap.code) for gap in first] == sorted(
        (gap.field, gap.code) for gap in first
    )


def test_allowlist_is_exactly_the_frozen_names() -> None:
    assert isinstance(ALLOWED_STRATEGY_TOOLS, frozenset)
    assert len(ALLOWED_STRATEGY_TOOLS) == 21
    assert ALLOWED_STRATEGY_TOOLS == FROZEN_TOOL_NAMES


def test_allowlist_names_are_registered_tools(
    registered_tool_names: frozenset[str],
) -> None:
    assert set(ALLOWED_STRATEGY_TOOLS) <= set(registered_tool_names)


def test_allowlist_excludes_forbidden_tools() -> None:
    assert FORBIDDEN_TOOL_PREFIXES == ("trading_", "etoro_", "connector_")
    for name in ALLOWED_STRATEGY_TOOLS:
        assert not name.startswith(FORBIDDEN_TOOL_PREFIXES)
    assert {
        "write_file",
        "bash",
        "run_swarm",
        "resolve_instrument",
    }.isdisjoint(ALLOWED_STRATEGY_TOOLS)


def test_constants_expose_the_documented_domains() -> None:
    assert SPEC_KINDS == frozenset({"code", "workflow"})
    assert OUTPUT_CONTRACT_FORMATS == frozenset({"markdown_table", "json", "csv"})
    assert MIN_WORKFLOW_STEPS == 3


@pytest.mark.parametrize(
    "name",
    [
        "trading_place_order",
        "trading_account",
        "etoro_place_order",
        "connector_write",
        "write_file",
        "bash",
        "run_swarm",
        "resolve_instrument",
        "backtest ",
        "",
        None,
        42,
    ],
)
def test_is_allowed_tool_rejects_everything_outside_the_allowlist(name: object) -> None:
    assert not is_allowed_tool(name)


@pytest.mark.parametrize("name", sorted(FROZEN_TOOL_NAMES))
def test_is_allowed_tool_accepts_every_allowlisted_name(name: str) -> None:
    assert is_allowed_tool(name)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [],
        "workflow",
        42,
        {"inputs": "oops"},
        {"steps": [None, "not a step"]},
        {"output_contract": "json"},
        {"name": None, "kind": None},
    ],
)
def test_validate_spec_never_raises_on_malformed_input(value: Any) -> None:
    gaps = validate_spec(value)

    assert all(isinstance(gap, Gap) for gap in gaps)


def test_from_dict_to_dict_round_trip_is_lossless() -> None:
    original = StrategySpec.from_dict(workflow())

    assert StrategySpec.from_dict(original.to_dict()) == original
    assert json.loads(json.dumps(original.to_dict())) == original.to_dict()


def test_mapping_and_instance_validation_agree() -> None:
    as_mapping = workflow(steps=[step(1), step(2)])
    as_instance = StrategySpec.from_dict(as_mapping)

    assert validate_spec(as_mapping) == validate_spec(as_instance)
