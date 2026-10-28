"""Contract tests for the frozen intake eval corpus and event schema.

WHY: Wave 0 needs a baseline that can disagree with the target design. That
only works if the corpus, the expectation table and the event schema are
frozen and mechanically checked; a corpus line that silently loses its
``expect`` label would make every downstream measurement meaningless.
Everything here is offline: no network, no docker, stdlib + pytest only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

INTAKE_DIR = Path(__file__).resolve().parent
CORPUS_PATH = INTAKE_DIR / "corpus.jsonl"
CORPUS40_PATH = INTAKE_DIR / "corpus40.jsonl"
CORPUS40_V2_PATH = INTAKE_DIR / "corpus40_v2.jsonl"
EXPECTED_PATH = INTAKE_DIR / "expected.json"
SCHEMA_PATH = INTAKE_DIR / "events.schema.json"
RUN_PY = INTAKE_DIR / "run.py"

# Frozen Wave 0/Wave 1 evidence: `git show 32f5e595~1:OpencodeAgent/tests/evals/intake/corpus40.jsonl`.
# 32f5e595 corrected c19/c22, so this pre-correction hash pins the corpus the
# published reports were generated from.
CORPUS40_V1_SHA256 = "69bb6f256d5e2afa7a521015b40d22101e47ceec7f84a4acfacac4cdbd410630"

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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_run_module():
    return _load_module("intake_eval_run", RUN_PY)


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


def test_intake_loaded_expectation_follows_routing_class(corpus):
    expected_true = {"c02", "c03", "c05", "c06", "c07"}
    stream = {"calls": [], "text": "", "tokens": None}
    for item in corpus:
        events = run.build_events("run-1", item, stream, wall_s=0.0)
        intake = next(event for event in events if event["event"] == "intake_loaded")
        assert intake["expected"] == (item["id"] in expected_true), item["id"]


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


@pytest.mark.parametrize(
    "line",
    [
        '{"type":"step_start","sessionID":"ses_x"}',
        '{"type":"step_start","session_id":"ses_x"}',
        '{"type":"step_start","session.id":"ses_x"}',
        '{"session":{"id":"ses_x"}}',
    ],
)
def test_stream_session_id_accepts_all_spellings(line):
    assert run.stream_session_id(line) == "ses_x"


def test_recovery_merges_session_messages_without_network():
    stdout = '{"type":"step_start","sessionID":"ses_fake"}\n'
    assert run.parse_stream(stdout)["calls"] == []
    assert run.parse_stream(stdout)["text"] == ""
    payload = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "用户消息不应进入路由"}]},
        {
            "info": {"role": "assistant", "tokens": {"total": 7}},
            "parts": [
                {"type": "text", "text": "因子是横截面上可计算的收益解释变量。"},
                {"type": "tool", "tool": "vibe-trading_get_market_data", "state": {"input": {"code": "600519"}}},
            ],
        },
    ]

    def fake_fetch(url, username, password):
        if url.endswith("/doc"):
            return {"paths": {"/session/{sessionID}/message": {}}}
        assert url.endswith("/session/ses_fake/message"), url
        return payload

    session_id = run.stream_session_id(stdout)
    assert session_id == "ses_fake"
    recovered = run.recover_stream("http://fake:4096", session_id, "opencode", "pw", fetch=fake_fetch)
    assert "因子" in recovered["text"]
    assert "用户消息" not in recovered["text"]
    assert recovered["calls"] == [{"name": "vibe-trading_get_market_data", "args": {"code": "600519"}}]
    assert recovered["tokens"] == 7
    item = next(item for item in run.load_corpus() if item["id"] == "c08")
    events = run.build_events("run-1", item, recovered, wall_s=9.5, prefix=run.RECOVERY_PREFIX)
    assert all(event["evidence"].startswith(run.RECOVERY_PREFIX) for event in events)
    assert events[0]["observed"] == "execute_with_defaults"


def test_parser_supports_only_and_disable_recovery():
    args = run.build_parser().parse_args(["--only", "c08", "--only", "c09", "--disable-recovery"])
    assert args.only == ["c08", "c09"]
    assert args.disable_recovery is True


def test_custom_corpus_accepts_40_and_rejects_short(tmp_path):
    items = run.load_corpus(CORPUS40_PATH)
    assert len(items) == 40
    assert {item["id"] for item in items} == {f"c{number:02d}" for number in range(1, 41)}
    assert run.validate_corpus(items) == []
    assert all(item["source"] == "extended_generated" for item in items[10:])
    assert all(item["generator"] == "corpus40 v1 seed=20261027" for item in items[10:])
    short_path = tmp_path / "short.jsonl"
    short_path.write_text("\n".join(CORPUS40_PATH.read_text(encoding="utf-8").splitlines()[:9]) + "\n", encoding="utf-8")
    errors = run.validate_corpus(run.load_corpus(short_path))
    assert any("at least 10 items" in error for error in errors), errors


def test_corpus40_v1_is_frozen_before_the_label_correction():
    assert hashlib.sha256(CORPUS40_PATH.read_bytes()).hexdigest() == CORPUS40_V1_SHA256
    items = run.load_corpus(CORPUS40_PATH)
    assert len(items) == 40
    assert len({item["id"] for item in items}) == 40
    labels = {item["id"]: item["expect"] for item in items}
    assert labels["c19"] == labels["c22"] == "execute_with_defaults"


def test_corpus40_v2_relabels_only_c19_and_c22():
    v1 = {item["id"]: item for item in run.load_corpus(CORPUS40_PATH)}
    v2_items = run.load_corpus(CORPUS40_V2_PATH)
    v2 = {item["id"]: item for item in v2_items}
    assert len(v2_items) == 40
    assert len(v2) == 40
    assert set(v1) == set(v2) == {f"c{number:02d}" for number in range(1, 41)}
    assert [item["id"] for item in v2_items] == [item["id"] for item in run.load_corpus(CORPUS40_PATH)]
    for corpus_id in v1:
        assert v2[corpus_id]["prompt"] == v1[corpus_id]["prompt"], corpus_id
    assert {corpus_id for corpus_id in v1 if v1[corpus_id] != v2[corpus_id]} == {"c19", "c22"}
    for corpus_id in ("c19", "c22"):
        assert v1[corpus_id]["expect"] == "execute_with_defaults"
        assert v2[corpus_id]["expect"] == "clarify_private"
        assert v2[corpus_id]["labels_version"] == "v2"
        assert "私有槽位" in v2[corpus_id]["rationale"]
    assert run.validate_corpus(v2_items) == []


def test_generator_reproduces_both_label_versions():
    generator = _load_module("intake_gen_corpus40", INTAKE_DIR / "gen_corpus40.py")
    assert generator.render_corpus("v1").encode("utf-8") == CORPUS40_PATH.read_bytes()
    assert generator.render_corpus("v2").encode("utf-8") == CORPUS40_V2_PATH.read_bytes()


def test_frozen_rules_apply_only_to_default_path(tmp_path):
    lines = CORPUS40_PATH.read_text(encoding="utf-8").splitlines()[:11]
    eleven_path = tmp_path / "eleven.jsonl"
    eleven_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    items = run.load_corpus(eleven_path)
    assert run.validate_corpus(items) == []
    assert any("exactly 10 items" in error for error in run.validate_corpus(items, frozen=True))
    assert run.validate_corpus(run.load_corpus(CORPUS_PATH), frozen=True) == []


def test_dry_run_honours_custom_corpus():
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--dry-run", "--corpus", str(CORPUS40_PATH)],
        cwd=str(INTAKE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 40
    assert lines[0].startswith("c01 | execute_with_defaults | 个股体检 | false |")
    assert lines[-1].startswith("c40 | refuse | none | false |")


def test_frozen_rubric_matches_exporter():
    analyze = _load_module("intake_analyze_wave0", INTAKE_DIR / "analyze_wave0.py")
    exporter = _load_module("intake_first_message_exporter", INTAKE_DIR / "export_first_messages.py")
    assert analyze.LABEL_KEYWORDS == exporter.LABEL_KEYWORDS
    assert tuple(analyze.PRODUCT_LABELS) == tuple(exporter.PRODUCT_LABELS)


def test_private_product_items_without_supplied_data_are_clarify_private():
    supplied = re.compile(r"\d{6}|成本|交割单|持仓|CSV|Excel|文件", re.IGNORECASE)
    for item in run.load_corpus(CORPUS40_V2_PATH):
        if item["expected_product"] in {"组合体检", "交易复盘"} and not supplied.search(item["prompt"]):
            assert item["expect"] == "clarify_private", item["id"]
