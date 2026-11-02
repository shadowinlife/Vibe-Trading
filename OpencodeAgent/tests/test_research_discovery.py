"""Offline discovery contract tests using actual MCP tools/list schemas."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "research-discovery"
EVAL = ROOT / "tests" / "evals" / "research_discovery"


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bv = _module("discovery_brief_validator", ROOT / "brief_validation.py")
capture = _module("discovery_capture", ROOT / "scripts" / "capture_discovery_tools.py")
checks = _module("discovery_checks", EVAL / "checks.py")


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return asyncio.run(
        capture.capture(
            ROOT.parent / "agent" / "mcp_server.py",
            ROOT / "config" / "vibe-trading-tools.json",
            SKILL / "recipes.json",
        )
    )


@pytest.fixture
def brief() -> dict:
    return {
        "intake_version": "2.0",
        "revision": 1,
        "original_request": "有什么内置策略？先看目录。",
        "objective": "Inspect available strategies and their evidence",
        "mode": "progressive",
        "status": "ready",
        "constraints": {
            "market": {"value": None, "source": "unknown", "reason": "not specified"}
        },
        "decisions": [],
        "routes": ["strategy_catalog"],
        "selected_route": "strategy_catalog",
        "acceptance_criteria": ["Separate catalogue entries from adequate evidence"],
        "out_of_scope": ["backtest", "live trading"],
        "assumptions": [],
        "approval": {"message_id": "synthetic-user-message", "revision": 1},
        "tool_plan": [
            {
                "id": "catalog",
                "tool": "list_strategies",
                "args": {"limit": 5},
                "depends_on": [],
            }
        ],
        "files": [],
    }


def _errors(brief: dict, snapshot: dict) -> list[str]:
    return bv.validate(
        brief,
        tool_schemas={tool["name"]: tool["inputSchema"] for tool in snapshot["tools"]},
        disabled_tools=set(snapshot["disabled_tools"]),
    )


def test_actual_mcp_schema_and_skill_contract(brief: dict, snapshot: dict) -> None:
    assert _errors(brief, snapshot) == []
    schemas = {tool["name"]: tool["inputSchema"] for tool in snapshot["tools"]}
    assert set(schemas["backtest"]["properties"]) == {"run_dir"}
    assert "trading_positions" in snapshot["disabled_tools"]
    header = (SKILL / "SKILL.md").read_text().split("---")[1]
    metadata = yaml.safe_load(header)
    assert metadata["name"] == SKILL.name
    assert "显式" in metadata["description"]
    assert metadata["user-invocable"] is True
    recipes = json.loads((SKILL / "recipes.json").read_text())
    assert recipes["ashare_top_risk"]["markets"] == ["a"]
    for recipe in recipes.values():
        assert set(recipe["tools"]) <= schemas.keys()
        for skill in recipe["skills"]:
            assert (
                ROOT.parent / "agent" / "src" / "skills" / skill / "SKILL.md"
            ).is_file()


def test_v2_never_falls_back_to_summary_only_snapshot(brief: dict) -> None:
    assert "requires tools/list" in bv.validate(brief)[0]


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda b: b["constraints"]["market"].update(value="a"), "expected null"),
        (lambda b: b["approval"].update(revision=2), "stale revision"),
        (lambda b: b.update(selected_route="not_offered"), "offered routes"),
        (lambda b: b["routes"].append("invented_route"), "unknown route"),
        (lambda b: b.update(status="waiting_user"), "requires ready"),
        (lambda b: b["tool_plan"][0]["args"].update(unknown=1), "unexpected key"),
        (lambda b: b["tool_plan"][0]["args"].update(limit=True), "expected"),
        (lambda b: b["tool_plan"][0].update(tool="trading_positions"), "disabled"),
        (
            lambda b: b["tool_plan"][0].update(
                tool="backtest", args={"run_dir": "synthetic-run", "config": {}}
            ),
            "unexpected key",
        ),
        (
            lambda b: b["constraints"].update(
                risk={"value": float("nan"), "source": "user", "reason": "bad"}
            ),
            "finite JSON",
        ),
        (
            lambda b: b["constraints"].update(
                sample_start={"value": "2026-02-30", "source": "user", "reason": "bad"}
            ),
            "ISO date",
        ),
    ],
)
def test_rejects_invalid_briefs(
    brief: dict, snapshot: dict, mutation, expected: str
) -> None:
    mutation(brief)
    assert any(expected in error for error in _errors(brief, snapshot))


def test_dependencies_and_unknown_answers(brief: dict, snapshot: dict) -> None:
    answer = {"value": None, "source": "unknown", "reason": "not answered"}
    brief["decisions"] = [
        {"id": "goal", "question": "Which goal?", "depends_on": [], "answer": answer},
        {
            "id": "window",
            "question": "Which sample?",
            "depends_on": ["goal"],
            "answer": {"value": "three years", "source": "user", "reason": "answered"},
        },
    ]
    assert any("prerequisite" in error for error in _errors(brief, snapshot))
    brief["decisions"][0]["depends_on"] = ["window"]
    assert any("precede" in error for error in _errors(brief, snapshot))


def test_backtest_config_is_not_a_tool_argument(brief: dict, snapshot: dict) -> None:
    brief.update(routes=["strategy_test"], selected_route="strategy_test")
    brief["constraints"]["market"] = {"value": "us", "source": "user", "reason": "AAPL"}
    brief["tool_plan"] = [
        {
            "id": "test",
            "tool": "backtest",
            "args": {"run_dir": "synthetic-run"},
            "depends_on": [],
        }
    ]
    assert any("config.json" in error for error in _errors(brief, snapshot))
    brief["files"] = [
        {
            "path": "synthetic-run/config.json",
            "kind": "config",
            "content": {
                "codes": ["AAPL.US"],
                "start_date": "2020-01-01",
                "end_date": "2025-12-31",
            },
        }
    ]
    assert _errors(brief, snapshot) == []
    brief["files"][0]["path"] = "other-run/config.json"
    assert any("config.json" in error for error in _errors(brief, snapshot))


def test_market_and_unknown_schema_fail_closed(brief: dict, snapshot: dict) -> None:
    brief["constraints"]["market"] = {
        "value": "crypto",
        "source": "user",
        "reason": "selected",
    }
    brief.update(
        routes=["ashare_top_risk"], selected_route="ashare_top_risk", tool_plan=[]
    )
    assert any("does not support" in error for error in _errors(brief, snapshot))
    brief.update(routes=["opportunity_screen"], selected_route="opportunity_screen")
    brief["tool_plan"] = [
        {
            "id": "scan",
            "tool": "screen_market",
            "args": {"market": "crypto"},
            "depends_on": [],
        }
    ]
    assert any("no crypto" in error for error in _errors(brief, snapshot))
    brief["tool_plan"][0]["args"]["market"] = "us"
    schema = next(
        tool["inputSchema"]
        for tool in snapshot["tools"]
        if tool["name"] == "screen_market"
    )
    changed = copy.deepcopy(snapshot)
    next(tool for tool in changed["tools"] if tool["name"] == "screen_market")[
        "inputSchema"
    ] = dict(schema, oneOf=[])
    assert any("unsupported schema" in error for error in _errors(brief, changed))


def test_mcp_schemas_do_not_advertise_config_as_backtest_arg(snapshot: dict) -> None:
    schemas = {tool["name"]: tool["inputSchema"] for tool in snapshot["tools"]}
    for tool, schema in schemas.items():
        assert bv._unsupported_schema(bv._expand(schema, schema), tool) == []


def test_market_scope_matches_execution(brief: dict, snapshot: dict) -> None:
    brief.update(
        routes=["opportunity_screen"], selected_route="opportunity_screen", tool_plan=[]
    )
    assert any("known market" in error for error in _errors(brief, snapshot))
    brief["constraints"]["market"] = {
        "value": "crypto",
        "source": "user",
        "reason": "selected",
    }
    brief["tool_plan"] = [
        {
            "id": "scan",
            "tool": "screen_market",
            "args": {"market": "us"},
            "depends_on": [],
        }
    ]
    assert any("selected market" in error for error in _errors(brief, snapshot))
    brief["constraints"]["market"]["value"] = "us"
    assert _errors(brief, snapshot) == []
    brief["constraints"]["market"]["value"] = {"invented": "market"}
    assert _errors(brief, snapshot)


def test_cross_market_allocation_is_an_explicit_scope(
    brief: dict, snapshot: dict
) -> None:
    brief.update(
        routes=["allocation_review"], selected_route="allocation_review", tool_plan=[]
    )
    brief["constraints"]["market"] = {
        "value": "cross",
        "source": "user",
        "reason": "compare A-shares, US stocks and crypto",
    }
    assert _errors(brief, snapshot) == []
    brief.update(routes=["technical_structure"], selected_route="technical_structure")
    assert any("does not support" in error for error in _errors(brief, snapshot))


def test_cancelled_brief_clears_the_plan_and_approval(
    brief: dict, snapshot: dict
) -> None:
    brief.update(status="cancelled", tool_plan=[])
    assert _errors(brief, snapshot)
    brief["approval"] = None
    assert _errors(brief, snapshot) == []
    brief["tool_plan"] = [
        {"id": "catalog", "tool": "list_strategies", "args": {}, "depends_on": []}
    ]
    assert _errors(brief, snapshot)


def test_default_decision_requires_a_declared_assumption(
    brief: dict, snapshot: dict
) -> None:
    brief["decisions"] = [
        {
            "id": "example",
            "question": "Which example?",
            "depends_on": [],
            "answer": {
                "value": "SMA",
                "source": "default",
                "reason": "teaching example",
            },
        }
    ]
    assert any(
        "defaults must be declared" in error for error in _errors(brief, snapshot)
    )
    brief["assumptions"] = ["SMA is only an example, not the user's preference"]
    assert _errors(brief, snapshot) == []


def test_duplicate_calls_and_dependencies_are_rejected(
    brief: dict, snapshot: dict
) -> None:
    brief["tool_plan"].append(copy.deepcopy(brief["tool_plan"][0]))
    assert any("duplicate id" in error for error in _errors(brief, snapshot))
    brief["tool_plan"][1].update(id="next", depends_on=["catalog", "catalog"])
    assert any("duplicate items" in error for error in _errors(brief, snapshot))


def test_multiturn_artifact_checks_detect_proxies(tmp_path: Path) -> None:
    cases = json.loads((EVAL / "corpus.json").read_text())
    assert len({case["id"] for case in cases}) == len(cases) == 12
    assert max(len(case["turns"]) for case in cases) > 3
    case = next(case for case in cases if case["id"] == "nvda_pivot")
    payload = {"event": "synthetic earnings-risk evidence"}
    artifact = {
        "target": case["target"],
        "conclusion": "Synthetic risk explanation",
        "limitations": ["No position sizing"],
        "source_call_ids": ["fixture-call"],
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(artifact))
    record = {
        "user_turns": case["turns"],
        "target": case["target"],
        "route": case["route"],
        "unknown": case["unknown"],
        "tool_results": [
            {
                "call_id": "fixture-call",
                "payload": payload,
                "data_as_of": "synthetic-as-of",
                "sha256": hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
                ).hexdigest(),
            }
        ],
        "artifact": {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    assert checks.check_record(record, case, tmp_path) == []
    bad = copy.deepcopy(record)
    bad.pop("artifact")
    assert any(
        "actual artifact" in error for error in checks.check_record(bad, case, tmp_path)
    )
    bad = copy.deepcopy(record)
    bad["target"] = cases[0]["target"]
    assert any(
        "revised target" in error for error in checks.check_record(bad, case, tmp_path)
    )
    bad = copy.deepcopy(record)
    bad["tool_results"] = []
    assert any(
        "fabricates citations" in error
        for error in checks.check_record(bad, case, tmp_path)
    )
    bad = copy.deepcopy(record)
    bad["tool_results"][0]["payload"] = {}
    assert any(
        "lacks actual evidence" in error
        for error in checks.check_record(bad, case, tmp_path)
    )
    bad = copy.deepcopy(record)
    bad["artifact"]["path"] = "../outside.json"
    assert any("outside" in error for error in checks.check_record(bad, case, tmp_path))
