"""Contract tests for the four strategy-library agent tools.

Each tool is exercised through ``execute(**kwargs)`` — the same entry point the
registry and the MCP wrappers use — with the library facade patched at its
module boundary. Nothing touches a store, a runtime root or a network.

The regression that matters most is the schedule one: the tool must relay the
control-plane cadence arguments to ``src.agent_schedules.tools`` and return
that action's envelope verbatim, instead of writing the retired local job
store or inventing a confirmation step.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from src.tools import build_registry
from src.tools.strategy_library_tool import (
    EvaluateStrategyTool,
    ListStrategyLibraryTool,
    PromoteStrategyTool,
    ScheduleStrategyTool,
)

TOOL_CLASSES = (
    PromoteStrategyTool,
    EvaluateStrategyTool,
    ListStrategyLibraryTool,
    ScheduleStrategyTool,
)
EXPECTED_NAMES = {
    "promote_strategy",
    "evaluate_strategy",
    "list_strategy_library",
    "schedule_strategy",
}
WRITE_TOOL_NAMES = {"promote_strategy", "evaluate_strategy", "schedule_strategy"}


def _json(text: str) -> dict:
    payload = json.loads(text)
    assert isinstance(payload, dict), f"envelope must be an object, got {type(payload)}"
    assert isinstance(payload.get("ok"), bool), f"envelope has no boolean ok: {payload}"
    return payload


def _artifact(**overrides):
    values = {
        "id": "stg-001",
        "name": "Momentum tilt",
        "universe": "US",
        "kind": "code",
        "readiness": "ready",
        "artifact_version": "v1-abcdef12",
        "demo": False,
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-02T00:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# Registration and the read-only flags
# ---------------------------------------------------------------------------


def test_registry_contains_all_four_strategy_library_tools() -> None:
    registered = set(build_registry().tool_names)

    missing = EXPECTED_NAMES - registered
    assert not missing, f"strategy-library tools absent from the registry: {sorted(missing)}"


@pytest.mark.parametrize("tool_cls", TOOL_CLASSES, ids=lambda cls: cls.name)
def test_tool_names_and_descriptions_are_self_describing(tool_cls) -> None:
    assert tool_cls.name in EXPECTED_NAMES
    assert tool_cls.description.strip()
    assert tool_cls.parameters["type"] == "object"


@pytest.mark.parametrize("tool_cls", TOOL_CLASSES, ids=lambda cls: cls.name)
def test_readonly_flag_matches_the_mutation_surface(tool_cls) -> None:
    expected = tool_cls.name not in WRITE_TOOL_NAMES
    assert tool_cls.is_readonly is expected, (
        f"{tool_cls.name}: is_readonly={tool_cls.is_readonly!r}, expected {expected!r}. "
        "The MCP read-only mirror refuses a False class; the three writers must "
        "declare False so they can only be exposed through an explicit write path."
    )


# ---------------------------------------------------------------------------
# list_strategy_library
# ---------------------------------------------------------------------------


def test_list_strategy_library_returns_a_paginated_envelope(monkeypatch) -> None:
    rows = [_artifact(id=f"stg-{index}") for index in range(3)]
    calls: list[dict] = []

    def fake_list(**kwargs):
        calls.append(kwargs)
        return rows[kwargs["offset"] :] if kwargs["offset"] else rows

    monkeypatch.setattr("src.strategy_library.store.list_strategies", fake_list)

    payload = _json(ListStrategyLibraryTool().execute(limit=1, offset=1))

    assert payload["ok"] is True
    data = payload["data"]
    assert data["total"] == 3
    assert data["limit"] == 1
    assert data["offset"] == 1
    assert [item["strategy_id"] for item in data["items"]] == ["stg-1"]
    assert set(data["items"][0]) >= {
        "strategy_id",
        "name",
        "universe",
        "kind",
        "readiness",
        "version",
        "demo",
    }
    assert calls and calls[0]["q"] is None and calls[0]["kind"] is None


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_list_strategy_library_rejects_an_out_of_range_limit(monkeypatch, limit: int) -> None:
    monkeypatch.setattr(
        "src.strategy_library.store.list_strategies",
        lambda **_: pytest.fail("store must not be queried with an invalid limit"),
    )

    payload = _json(ListStrategyLibraryTool().execute(limit=limit))

    assert payload["ok"] is False
    assert payload["code"] == "invalid_argument"


def test_list_strategy_library_hides_store_internals(monkeypatch) -> None:
    def explode(**_kwargs):
        raise RuntimeError("/srv/secret/path is unreadable")

    monkeypatch.setattr("src.strategy_library.store.list_strategies", explode)

    payload = _json(ListStrategyLibraryTool().execute())

    assert payload["ok"] is False
    assert "secret" not in payload["error"]
    assert payload["error"] == "list_strategy_library failed internally; see server logs"


# ---------------------------------------------------------------------------
# promote_strategy
# ---------------------------------------------------------------------------


def test_promote_strategy_returns_the_registered_summary(monkeypatch) -> None:
    from src.strategy_library.promote import PromotionResult

    monkeypatch.setattr(
        "src.strategy_library.promote.promote",
        lambda spec, **kwargs: PromotionResult(
            "stg-001",
            "ready",
            [],
            {"passed": True, "reasons": []},
            _artifact(),
            kwargs["session_id"],
        ),
    )

    payload = _json(
        PromoteStrategyTool(default_session_id="sess-9").execute(
            spec={"name": "Momentum tilt", "kind": "code"}, run_dir="/runs/x"
        )
    )

    assert payload["ok"] is True
    data = payload["data"]
    assert data["strategy_id"] == "stg-001"
    assert data["readiness"] == "ready"
    assert data["session_id"] == "sess-9"
    assert data["strategy"]["kind"] == "code"
    assert data["gaps"] == []


def test_promote_strategy_invalid_spec_returns_a_gap_envelope() -> None:
    payload = _json(PromoteStrategyTool().execute(spec={}))

    assert payload["ok"] is False
    assert payload["code"] not in {"", "None"}
    assert payload["gaps"], "an invalid spec must report its deterministic gaps"
    assert "Traceback" not in payload["error"]


def test_promote_strategy_rejects_a_non_object_spec() -> None:
    payload = _json(PromoteStrategyTool().execute(spec="not a spec"))

    assert payload["ok"] is False
    assert payload["code"] == "invalid_spec"


def test_promote_strategy_wraps_an_unexpected_failure(monkeypatch) -> None:
    def explode(_spec, **_kwargs):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("src.strategy_library.promote.promote", explode)

    payload = _json(PromoteStrategyTool().execute(spec={"name": "x", "kind": "workflow"}))

    assert payload["ok"] is False
    assert payload["error"] == "store unavailable"
    assert payload["code"] == "RuntimeError"
    assert "Traceback" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# evaluate_strategy
# ---------------------------------------------------------------------------


def test_evaluate_strategy_dispatches_code_to_the_deterministic_gate(monkeypatch) -> None:
    from src.strategy_library.gate_code import Verdict

    monkeypatch.setattr(
        "src.strategy_library.store.get",
        lambda strategy_id: _artifact(id=strategy_id, readiness="needs_work"),
    )
    monkeypatch.setattr(
        "src.strategy_library.gate_code.evaluate_code",
        lambda strategy_id: Verdict(
            passed=False, reasons=["no_state"], details={"strategy_id": strategy_id}
        ),
    )

    payload = _json(EvaluateStrategyTool().execute(strategy_id="stg-001"))

    assert payload["ok"] is True
    data = payload["data"]
    assert data["kind"] == "code"
    assert data["passed"] is False
    assert data["reasons"] == ["no_state"]
    assert data["readiness"] == "needs_work"
    assert data["verdict"]["passed"] is False


def test_evaluate_strategy_dispatches_workflow_to_the_rubric_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.strategy_library.store.get",
        lambda strategy_id: _artifact(id=strategy_id, kind="workflow", readiness="needs_work"),
    )
    monkeypatch.setattr(
        "src.strategy_library.review.evaluate_workflow",
        lambda strategy_id: {
            "verdict": "fail",
            "gaps": [{"code": "missing_inputs", "message": "no input source"}],
            "must_pass": [],
        },
    )

    payload = _json(EvaluateStrategyTool().execute(strategy_id="stg-001"))

    assert payload["ok"] is True
    data = payload["data"]
    assert data["kind"] == "workflow"
    assert data["passed"] is False
    assert data["reasons"] == ["missing_inputs"]
    assert data["verdict"]["verdict"] == "fail"


def test_evaluate_strategy_unknown_id_returns_not_found(monkeypatch) -> None:
    monkeypatch.setattr("src.strategy_library.store.get", lambda strategy_id: None)

    payload = _json(EvaluateStrategyTool().execute(strategy_id="ghost"))

    assert payload["ok"] is False
    assert payload["code"] == "not_found"


def test_evaluate_strategy_wraps_a_gate_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.strategy_library.store.get", lambda strategy_id: _artifact(id=strategy_id)
    )

    def explode(_strategy_id):
        raise ValueError("strategy has no version/spec hash; re-promote it first")

    monkeypatch.setattr("src.strategy_library.gate_code.evaluate_code", explode)

    payload = _json(EvaluateStrategyTool().execute(strategy_id="stg-001"))

    assert payload["ok"] is False
    assert payload["code"] == "ValueError"
    assert "re-promote" in payload["error"]


# ---------------------------------------------------------------------------
# schedule_strategy
# ---------------------------------------------------------------------------


def test_schedule_strategy_parameters_describe_the_control_plane_contract() -> None:
    parameters = ScheduleStrategyTool.parameters

    assert parameters["required"] == ["strategy_id", "kind"]
    properties = parameters["properties"]
    assert set(properties) == {
        "strategy_id",
        "kind",
        "strategy_version",
        "name",
        "cron_spec",
        "interval_s",
        "time_of_day",
        "tz",
    }
    assert "schedule" not in properties, "the retired local cadence argument must be gone"
    assert "timezone" not in properties, "the control plane takes tz, not timezone"
    assert "run_timeout_s" not in properties, "run timeouts belong to the retired bridge"


def test_schedule_strategy_forwards_the_control_plane_arguments(monkeypatch) -> None:
    calls: list[dict] = []
    envelope = {
        "ok": True,
        "schedule_id": "ss_0001",
        "next_run_at": "2026-10-27T01:00:00Z",
        "pinned_version": "v1-abcdef12",
    }

    def fake_schedule(**kwargs):
        calls.append(kwargs)
        return envelope

    monkeypatch.setattr("src.agent_schedules.tools.schedule_strategy", fake_schedule)

    payload = _json(
        ScheduleStrategyTool().execute(
            strategy_id="stg-001",
            kind="interval",
            interval_s=3600,
            name="Hourly run",
            tz="Asia/Shanghai",
        )
    )

    assert payload == envelope
    assert calls == [
        {
            "strategy_id": "stg-001",
            "kind": "interval",
            "strategy_version": None,
            "name": "Hourly run",
            "cron_spec": None,
            "interval_s": 3600,
            "time_of_day": None,
            "tz": "Asia/Shanghai",
        }
    ]


def test_schedule_strategy_without_the_control_plane_module_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "src.agent_schedules.tools", None)

    payload = _json(
        ScheduleStrategyTool().execute(
            strategy_id="stg-001", kind="cron", cron_spec="0 9 * * *"
        )
    )

    assert payload["ok"] is False
    assert payload["code"] == "scheduling_unavailable"
    assert "scheduling" in payload["error"]


def test_schedule_strategy_requires_strategy_id_and_kind() -> None:
    tool = ScheduleStrategyTool()

    assert _json(tool.execute())["code"] == "invalid_argument"
    assert _json(tool.execute(strategy_id="stg-001"))["code"] == "invalid_argument"
    assert _json(tool.execute(kind="cron"))["code"] == "invalid_argument"
