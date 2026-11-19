"""Contract tests for the workflow rubric evaluator.

Every case drives :func:`evaluate_workflow` with an injected fake provider, so
the suite never reaches a network endpoint and can assert the exact request it
would have sent.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from src.strategy_library import review
from src.strategy_library.review import MUST_PASS_IDS, evaluate_workflow
from src.strategy_library.store import StaleVerdictError, create_draft, update_draft
from src.strategy_store.models import Artifact, ArtifactType, ValidationStatus
from src.strategy_store.sqlite_store import SqliteStrategyStore

_VERDICT_KEYS = {
    "verdict",
    "must_pass",
    "gaps",
    "summary",
    "rubric_version",
    "model",
    "created_at",
    "raw_digest",
    "spec_sha256",
    "artifact_version",
}


def _make_store(tmp_path: Path) -> SqliteStrategyStore:
    return SqliteStrategyStore(tmp_path / "strategy_store.db")


def _workflow_spec(name: str = "Momentum Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength after a breakout confirmed by volume.",
        "universe": "cn_a",
        "inputs": [
            {
                "name": "prices",
                "source": "get_market_data",
                "required": True,
                "missing_policy": "stop",
            }
        ],
        "preconditions": ["the market is open"],
        "steps": [
            {
                "id": "s1",
                "action": "load prices",
                "tool": "get_market_data",
                "output": "price table",
            },
            {
                "id": "s2",
                "action": "score momentum",
                "tool": "technical_indicators",
                "output": "momentum score",
            },
            {
                "id": "s3",
                "action": "summarise",
                "tool": "web_search",
                "output": "markdown table",
            },
        ],
        "output_contract": {
            "format": "markdown_table",
            "required_fields": ["symbol", "score"],
            "destination": "chat",
        },
        "failure_handling": "Report the failing step and stop.",
        "schedule_hint": "weekdays 09:00",
        "evidence_refs": [],
    }


def _code_spec() -> dict:
    return {
        "name": "Momentum Code",
        "kind": "code",
        "thesis": "Cross-sectional momentum on liquid names.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _rows(all_true: bool = True) -> list[dict]:
    return [
        {"id": criterion, "pass": all_true, "why": f"{criterion} holds"}
        for criterion in MUST_PASS_IDS
    ]


def _response(
    verdict: str = "pass",
    *,
    rows: list[dict] | None = None,
    gaps: list[Any] | None = None,
    summary: str = "the plan is concrete and runnable",
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "must_pass": _rows() if rows is None else rows,
            "gaps": gaps or [],
            "summary": summary,
        }
    )


class _FakeLLM:
    """Record every request and return one fixed response."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append(messages)
        return self.response


class _SlowLLM:
    """Block past the configured budget, then answer."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        time.sleep(self.delay_s)
        return _response("pass")


class _BoomLLM:
    """Fail the way a misconfigured or unreachable provider does."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        raise RuntimeError("relay unavailable")


class _DriftLLM:
    """Edit the strategy from inside the provider call, on another thread.

    The write must succeed while the evaluator is "thinking"; if the review
    held a connection or a write transaction, SQLite would refuse or block it.
    """

    def __init__(
        self,
        store: SqliteStrategyStore,
        strategy_id: str,
        spec: dict,
        runtime_root: Path,
    ) -> None:
        self.store = store
        self.strategy_id = strategy_id
        self.spec = spec
        self.runtime_root = runtime_root
        self.calls = 0
        self.edited: Artifact | None = None
        self.write_error: BaseException | None = None
        self.write_finished = False

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1

        def _writer() -> None:
            try:
                self.edited = update_draft(
                    self.strategy_id,
                    self.spec,
                    store=self.store,
                    runtime_root=self.runtime_root,
                )
            except BaseException as exc:  # relayed through the assertions below
                self.write_error = exc

        thread = threading.Thread(target=_writer, daemon=True)
        thread.start()
        thread.join(10.0)
        self.write_finished = not thread.is_alive()
        return _response("pass")


def _draft(tmp_path: Path, store: SqliteStrategyStore) -> Artifact:
    return create_draft(_workflow_spec(), store=store, runtime_root=tmp_path)


# ---------------------------------------------------------------------------
# pass
# ---------------------------------------------------------------------------


def test_a_complete_pass_marks_the_strategy_ready(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _FakeLLM(_response("pass"))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "pass"
    assert review._GATE[verdict["verdict"]][1] == "ready"
    assert [row["id"] for row in verdict["must_pass"]] == list(MUST_PASS_IDS)
    assert all(row["pass"] for row in verdict["must_pass"])
    assert len(fake.calls) == 1

    stored = store.get_artifact(draft.id)
    assert stored is not None
    assert stored.readiness == "ready"
    assert stored.validation_status is ValidationStatus.VALIDATED
    assert stored.evaluator_verdict == verdict
    assert set(verdict) == _VERDICT_KEYS

    assert verdict["rubric_version"] == review.RUBRIC_VERSION
    assert verdict["artifact_version"] == draft.artifact_version
    assert verdict["spec_sha256"] == draft.spec_sha256
    assert len(verdict["raw_digest"]) == 64
    assert verdict["created_at"]


def test_the_request_carries_only_the_fixed_rubric_and_the_spec(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _FakeLLM(_response("pass"))
    spec = _workflow_spec()

    evaluate_workflow(draft.id, llm=fake, store=store)

    messages = fake.calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == review.RUBRIC
    assert json.dumps(spec, ensure_ascii=False, sort_keys=True) in messages[1]["content"]

    serialized = json.dumps(messages, ensure_ascii=False)
    assert "session" not in serialized
    assert "history" not in serialized
    assert "assistant" not in [message["role"] for message in messages]


# ---------------------------------------------------------------------------
# fail
# ---------------------------------------------------------------------------


def test_a_rubric_failure_lands_needs_work_and_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    rows = _rows()
    rows[3]["pass"] = False
    rows[3]["why"] = "step 2 names an action but no tool"
    fake = _FakeLLM(
        _response(
            "fail",
            rows=rows,
            gaps=["step 2 names an action but no tool"],
            summary="the plan is not executable as written",
        )
    )

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "fail"
    assert review._GATE[verdict["verdict"]][1] == "needs_work"
    assert verdict["gaps"] == [
        {
            "code": "rubric_gap",
            "field": "",
            "message": "step 2 names an action but no tool",
            "fix_hint": "",
        }
    ]
    stored = store.get_artifact(draft.id)
    assert stored is not None
    assert stored.readiness == "needs_work"
    assert stored.validation_status is ValidationStatus.REJECTED
    assert stored.evaluator_verdict is not None
    assert stored.evaluator_verdict["must_pass"][3]["pass"] is False


def test_a_fail_that_reports_no_gaps_still_yields_actionable_gaps(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    rows = _rows()
    rows[0]["pass"] = False
    rows[0]["why"] = "the thesis has no measurable success condition"
    fake = _FakeLLM(_response("fail", rows=rows, gaps=[]))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "fail"
    assert verdict["gaps"] == [
        {
            "code": "rubric_gap",
            "field": "goal_decidable",
            "message": "the thesis has no measurable success condition",
            "fix_hint": "",
        }
    ]


# ---------------------------------------------------------------------------
# inconclusive: the gate must never pass on an infrastructure failure
# ---------------------------------------------------------------------------


def test_unparseable_output_is_inconclusive_and_never_ready(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _FakeLLM("This looks reasonable to me.")

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "inconclusive"
    assert verdict["summary"].startswith("inconclusive:")
    assert len(verdict["must_pass"]) == len(MUST_PASS_IDS)
    assert all(not row["pass"] for row in verdict["must_pass"])
    _assert_reviewing(store, draft.id)


def test_a_json_object_missing_required_keys_is_inconclusive(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _FakeLLM(json.dumps({"verdict": "pass", "summary": "fine"}))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "inconclusive"
    _assert_reviewing(store, draft.id)


def test_a_timeout_is_inconclusive_and_never_ready(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _SlowLLM(delay_s=0.5)

    verdict = evaluate_workflow(draft.id, llm=fake, store=store, timeout_s=0.05)

    assert fake.calls == 1
    assert verdict["verdict"] == "inconclusive"
    assert verdict["summary"] == "inconclusive: the evaluator call exceeded 0.05s"
    _assert_reviewing(store, draft.id)


def test_a_provider_error_is_inconclusive_and_never_ready(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _BoomLLM()

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert fake.calls == 1
    assert verdict["verdict"] == "inconclusive"
    assert "RuntimeError" in verdict["summary"]
    _assert_reviewing(store, draft.id)


def test_a_pass_that_omits_a_must_pass_row_is_inconclusive(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    fake = _FakeLLM(_response("pass", rows=_rows()[:-1]))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "inconclusive"
    assert [row["id"] for row in verdict["must_pass"]] == list(MUST_PASS_IDS)
    missing = verdict["must_pass"][-1]
    assert missing == {
        "id": MUST_PASS_IDS[-1],
        "pass": False,
        "why": "not reported",
    }
    _assert_reviewing(store, draft.id)


def test_a_pass_with_a_false_row_is_downgraded_to_fail(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    rows = _rows()
    rows[4]["pass"] = False
    rows[4]["why"] = "the step calls a broker write tool"
    fake = _FakeLLM(_response("pass", rows=rows))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert verdict["verdict"] == "fail"
    stored = store.get_artifact(draft.id)
    assert stored is not None
    assert stored.readiness == "needs_work"


def _assert_reviewing(store: SqliteStrategyStore, strategy_id: str) -> None:
    stored = store.get_artifact(strategy_id)
    assert stored is not None
    assert stored.readiness == "reviewing"
    assert stored.validation_status is ValidationStatus.UNVALIDATED
    assert stored.evaluator_verdict is not None
    assert stored.evaluator_verdict["verdict"] == "inconclusive"


# ---------------------------------------------------------------------------
# deterministic precheck
# ---------------------------------------------------------------------------


def test_structural_gaps_fail_without_a_model_call(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    spec = _workflow_spec()
    spec["steps"] = spec["steps"][:2]
    draft = create_draft(spec, store=store, runtime_root=tmp_path)
    fake = _FakeLLM(_response("pass"))

    verdict = evaluate_workflow(draft.id, llm=fake, store=store)

    assert fake.calls == []
    assert verdict["verdict"] == "fail"
    assert verdict["model"] == review.PRE_CHECK_MODEL
    assert any(gap["code"] == "too_few_steps" for gap in verdict["gaps"])
    assert len(verdict["must_pass"]) == len(MUST_PASS_IDS)
    stored = store.get_artifact(draft.id)
    assert stored is not None
    assert stored.readiness == "needs_work"
    assert stored.validation_status is ValidationStatus.REJECTED


def test_a_workflow_without_a_spec_lands_needs_work(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    strategy_id = store.register_artifact(
        Artifact(
            id="",
            type=ArtifactType.STRATEGY,
            name="No Spec Workflow",
            universe="cn_a",
            kind="workflow",
            spec=None,
            spec_sha256="0" * 64,
            artifact_version="v1-00000000",
        )
    )
    fake = _FakeLLM(_response("pass"))

    verdict = evaluate_workflow(strategy_id, llm=fake, store=store)

    assert fake.calls == []
    assert verdict["verdict"] == "fail"
    assert [gap["code"] for gap in verdict["gaps"]] == ["missing_spec"]
    stored = store.get_artifact(strategy_id)
    assert stored is not None
    assert stored.readiness == "needs_work"


# ---------------------------------------------------------------------------
# version drift: CAS, and proof that no transaction was held during the call
# ---------------------------------------------------------------------------


def test_an_edit_during_the_call_makes_the_verdict_stale(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = _draft(tmp_path, store)
    edited_spec = _workflow_spec()
    edited_spec["thesis"] = "Buy strength only above the 200-day average."
    probe = _DriftLLM(store, draft.id, edited_spec, tmp_path)

    with pytest.raises(StaleVerdictError):
        evaluate_workflow(draft.id, llm=probe, store=store)

    assert probe.calls == 1
    assert probe.write_finished, "the concurrent write blocked during the call"
    assert probe.write_error is None
    assert probe.edited is not None
    assert probe.edited.artifact_version != draft.artifact_version

    stored = store.get_artifact(draft.id)
    assert stored is not None
    assert stored.artifact_version == probe.edited.artifact_version
    assert stored.readiness == "needs_work"
    assert stored.evaluator_verdict is None


def test_evaluating_a_code_strategy_is_a_caller_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    draft = create_draft(_code_spec(), store=store, runtime_root=tmp_path)
    fake = _FakeLLM(_response("pass"))

    with pytest.raises(ValueError, match="workflow"):
        evaluate_workflow(draft.id, llm=fake, store=store)

    assert fake.calls == []
