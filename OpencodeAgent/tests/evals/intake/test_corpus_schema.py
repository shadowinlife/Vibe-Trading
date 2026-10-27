"""Contract tests for the frozen intake eval corpus and event schema.

WHY: Wave 0 needs a baseline that can disagree with the target design. That
only works if the corpus, the expectation table and the event schema are
frozen and mechanically checked; a corpus line that silently loses its
``expect`` label would make every downstream measurement meaningless.
Everything here is offline: no network, no docker, stdlib + pytest only.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

INTAKE_DIR = Path(__file__).resolve().parent
CORPUS_PATH = INTAKE_DIR / "corpus.jsonl"
EXPECTED_PATH = INTAKE_DIR / "expected.json"
SCHEMA_PATH = INTAKE_DIR / "events.schema.json"
RUN_PY = INTAKE_DIR / "run.py"

EXPECT_VALUES = {"direct_answer", "execute_with_defaults", "clarify_private", "refuse"}
PRODUCT_VALUES = {
    "个股体检",
    "选股清单",
    "策略回测",
    "组合体检",
    "事件/宏观影响",
    "交易复盘",
    "定时监控",
    "报告",
    "none",
}
REQUIRED_FIELDS = {
    "id",
    "prompt",
    "expect",
    "negative",
    "open_ended",
    "expected_product",
    "rationale",
}
EVENT_NAMES = ("route_class", "intake_loaded", "question_called", "params_compiled")
PRIVATE_MARKERS = {"成本价", "仓位"}


def _load_run_module():
    spec = importlib.util.spec_from_file_location("intake_eval_run", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load_run_module()


@pytest.fixture(scope="module")
def corpus():
    return run.load_corpus(CORPUS_PATH)


@pytest.fixture(scope="module")
def expected():
    return run.load_expected(EXPECTED_PATH)


def test_corpus_has_ten_unique_complete_items(corpus):
    assert len(corpus) == 10
    ids = [item["id"] for item in corpus]
    assert len(set(ids)) == 10
    assert run.validate_corpus(corpus) == []
    for item in corpus:
        assert REQUIRED_FIELDS <= set(item), item["id"]
        for field in REQUIRED_FIELDS:
            if isinstance(item[field], str):
                assert item[field].strip(), item["id"]
        assert item["expect"] in EXPECT_VALUES, item["id"]
        assert item["expected_product"] in PRODUCT_VALUES, item["id"]
        assert isinstance(item["negative"], bool), item["id"]
        assert isinstance(item["open_ended"], bool), item["id"]
        assert len(item["rationale"]) <= 80, item["id"]


def test_negative_examples_are_direct_answers(corpus):
    negatives = [item for item in corpus if item["negative"]]
    assert len(negatives) >= 2
    assert {item["id"] for item in negatives} >= {"c08", "c09"}
    prompts = {item["prompt"] for item in negatives}
    assert {"什么是因子", "明天能涨吗"} <= prompts
    for item in negatives:
        assert item["expect"] == "direct_answer", item["id"]
        assert item["expected_product"] == "none", item["id"]


def test_expected_json_matches_corpus(corpus, expected):
    assert set(expected) == {item["id"] for item in corpus}
    assert run.validate_corpus(corpus, expected) == []
    for item in corpus:
        detail = expected[item["id"]]
        assert detail["expect"] == item["expect"], item["id"]
        assert detail["expected_product"] == item["expected_product"], item["id"]
        assert detail["negative"] is item["negative"], item["id"]
        assert detail["max_user_turns"] == 2, item["id"]
        assert detail["required_markers"], item["id"]
    assert expected["c08"]["required_markers"] == ["因子"]
    assert expected["c09"]["required_markers"] == ["不预测", "免责声明"]
    for corpus_id in ("c05", "c06"):
        assert PRIVATE_MARKERS <= set(expected[corpus_id]["required_markers"])


def test_events_schema_is_self_describing_json_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    defs = schema["$defs"]
    assert "base_record" in defs
    for required in ("run_id", "corpus_id", "event", "ts", "observed", "expected", "evidence"):
        assert required in defs["base_record"]["required"]
    for name in EVENT_NAMES:
        assert name in defs, name
        judge_rule = defs[name].get("judge_rule")
        assert isinstance(judge_rule, str) and judge_rule.strip(), name
        assert defs[name]["allOf"][0]["$ref"] == "#/$defs/base_record", name
    refs = {entry["$ref"].rsplit("/", 1)[-1] for entry in schema["oneOf"]}
    assert set(EVENT_NAMES) <= refs
    assert "run_error" in refs


def test_event_validator_accepts_valid_and_rejects_incomplete_records():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    record = {
        "run_id": "run-1",
        "corpus_id": "c01",
        "event": "route_class",
        "ts": "2026-10-28T00:00:00+00:00",
        "observed": "execute_with_defaults",
        "expected": "execute_with_defaults",
        "evidence": "tools=ch_query",
        "user_turns": 1,
        "tokens": None,
    }
    assert run.validate_event(record, schema) == []
    missing_evidence = dict(record)
    del missing_evidence["evidence"]
    assert run.validate_event(missing_evidence, schema)
    bad_label = dict(record, observed="make_me_a_sandwich")
    assert run.validate_event(bad_label, schema)


def test_failure_probe_missing_expect_fails_validation(tmp_path):
    lines = CORPUS_PATH.read_text(encoding="utf-8").splitlines()
    probe_item = json.loads(lines[0])
    del probe_item["expect"]
    probe_path = tmp_path / "corpus.jsonl"
    probe_path.write_text(
        "\n".join([json.dumps(probe_item, ensure_ascii=False), *lines[1:]]) + "\n",
        encoding="utf-8",
    )
    probe_items = run.load_corpus(probe_path)
    assert len(probe_items) == 10
    errors = run.validate_corpus(probe_items)
    assert errors, "deleting `expect` must make corpus validation fail"
    assert any("required field 'expect'" in error for error in errors)
    assert run.validate_corpus(run.load_corpus(CORPUS_PATH)) == []


def test_dry_run_prints_ten_lines_offline():
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--dry-run"],
        cwd=str(INTAKE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 10
    assert lines[0].startswith("c01 | execute_with_defaults | 个股体检 | false |")
    assert lines[-1].startswith("c10 | refuse | none | false |")
