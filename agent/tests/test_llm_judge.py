"""Offline tests for the E2 LLM-judge runner — no live API calls.

The judge runner (``src.evals.tool_selection.run_llm_judge``) is the
semantic arbiter for description changes the lexical baseline cannot
measure: a pinned LLM picks candidates from the full routing surface and
the choice is scored against ``queries.yaml``. Every property that makes
that comparison trustworthy is testable offline, and this file pins all of
them with an injected fake client (no network, no openai dependency):

* **Frozen protocol.** The prompt builder emits all 164 candidates in
  corpus order under the frozen template whose sha256 is pinned here and
  in the trace header; a trace recorded under another template is refused.
* **Parsing and scoring.** Clean, fenced, partial and garbage replies are
  handled without crashing; top-1/top-3/negative false-recall follow their
  documented (conservative) definitions.
* **Resume and budget.** Completed query ids are skipped on restart, and
  the budget pre-check aborts BEFORE a cap is overshot — the two cost
  controls the full 158-query run relies on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from src.evals.tool_selection import llm_judge_protocol as protocol  # noqa: E402
from src.evals.tool_selection import llm_judge_report as judge_report  # noqa: E402
from src.evals.tool_selection import llm_judge_stats as stats  # noqa: E402
from src.evals.tool_selection import run_llm_judge as judge  # noqa: E402

# Frozen protocol pin: sha256 of SYSTEM_PROMPT + "\n" + USER_TEMPLATE +
# "\n" + CANDIDATE_LINE (see artifacts/llm_judge_design.md). Any change to
# the template is a protocol change and must update this pin deliberately.
FROZEN_TEMPLATE_SHA256 = (
    "b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1"
)

EXPECTED_TOOL_COUNT = 74
EXPECTED_SKILL_COUNT = 90


# --------------------------------------------------------------------------- #
# Fake judge client (offline stand-in for openai.OpenAI).
# --------------------------------------------------------------------------- #
class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(
        self, content: str, prompt_tokens: int = 100, completion_tokens: int = 10
    ) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class FakeJudgeClient:
    """Replays canned replies; raises when the run makes unexpected calls."""

    def __init__(self, replies: list) -> None:
        self.completions = _FakeCompletions(replies)
        self.chat = type("Chat", (), {"completions": self.completions})()


class _FakeCompletions:
    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append({
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        })
        if not self._replies:
            raise AssertionError("unexpected extra judge call")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _factory_for(client):
    """Build an injectable client_factory bound to one fake client."""

    def factory(model_cfg, env):
        return client

    return factory


def _model_cfg() -> dict:
    """Return the pinned primary model config from judge_config.yaml."""
    config = judge.load_config(judge.CONFIG_PATH)
    return next(m for m in config["models"] if m["role"] == "primary")


def _caps(max_tokens: int = 25_000_000, max_calls: int = 700) -> judge.BudgetCaps:
    return judge.BudgetCaps(max_tokens=max_tokens, max_calls=max_calls)


def _assets() -> tuple[dict, list[dict]]:
    return judge.load_corpus("post"), judge.load_queries()


def _reply_for(entry: dict) -> _FakeResponse:
    """A canned judge reply that hits the entry's expected target."""
    expected = f"{entry['expected']['kind']}:{entry['expected']['name']}"
    return _FakeResponse(
        json.dumps({"first": expected, "second": "tool:list_skills",
                    "third": "skill:data-routing"})
    )


# --------------------------------------------------------------------------- #
# Frozen prompt template + candidate block.
# --------------------------------------------------------------------------- #
def test_template_sha256_is_frozen() -> None:
    """The trace header refuses drift against this hash; it must be pinned."""
    assert protocol.prompt_template_sha256() == FROZEN_TEMPLATE_SHA256


def test_prompt_builder_emits_all_candidates_in_corpus_order() -> None:
    """The judge must see the full routing surface, tools then skills."""
    corpus, entries = _assets()
    messages = protocol.build_messages(corpus, entries[0]["query"])
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == protocol.SYSTEM_PROMPT

    user = messages[1]["content"]
    assert user.startswith("## Candidates\n")
    assert "\n\n## User request\n" + entries[0]["query"] in user
    block = user.split("\n\n## User request\n")[0].split("\n", 1)[1]
    lines = block.split("\n")
    assert len(lines) == EXPECTED_TOOL_COUNT + EXPECTED_SKILL_COUNT

    for index, row in enumerate(corpus["tools"]):
        assert lines[index].startswith(f"tool:{row['name']} — ")
    for index, row in enumerate(corpus["skills"]):
        assert lines[EXPECTED_TOOL_COUNT + index].startswith(f"skill:{row['name']} — ")

    ids = [line.split(" — ", 1)[0] for line in lines]
    assert len(set(ids)) == len(ids), "candidate ids must be unique"
    # Descriptions are collapsed to one line, so one line == one candidate.
    assert all(" — " in line for line in lines)


def test_config_pins_match_the_spec() -> None:
    """judge_config.yaml is the protocol contract; pin its load-bearing values."""
    config = judge.load_config(judge.CONFIG_PATH)
    by_id = {m["id"]: m for m in config["models"]}
    assert set(by_id) == {
        "qwen3.8-max", "deepseek-v4-flash-0731", "kimi-k3", "glm-5.2",
    }
    assert by_id["qwen3.8-max"]["role"] == "primary"
    for model_id in ("deepseek-v4-flash-0731", "kimi-k3", "glm-5.2"):
        assert by_id[model_id]["role"] == "sensitivity"
    for model in by_id.values():
        # Whole panel is DashScope-hosted under the same key.
        assert model["provider"] == "dashscope"
        assert model["api_key_env"] == "DASHSCOPE_API_KEY"
        assert model["base_url_env"] == "DASHSCOPE_BASE_URL"
        assert model["temperature"] == 0.0
    # Reasoning-model caps are empirical — see DOCUMENTED DEVIATIONS in
    # judge_config.yaml: deepseek-v4-flash-0731 and glm-5.2 spend their
    # max_tokens budget on reasoning before answering (empty content below
    # ~2000), kimi-k3 finishes within ~200 completion tokens but needs
    # headroom for the 164-candidate prompt, qwen3.8-max probed consistent
    # at 80 with 500 headroom.
    assert by_id["qwen3.8-max"]["max_response_tokens"] == 500
    assert by_id["deepseek-v4-flash-0731"]["max_response_tokens"] == 2000
    assert by_id["kimi-k3"]["max_response_tokens"] == 1000
    assert by_id["glm-5.2"]["max_response_tokens"] == 2000
    assert config["budget"]["max_input_tokens_per_model_run"] == 25_000_000
    assert config["budget"]["max_calls_per_model_run"] == 700
    assert config["determinism_probe"] == {"sample_queries": 8, "repeats": 3}
    assert config["prices"]["estimate"] is True
    assert set(config["prices"]["per_million_tokens"]) == set(by_id)


def test_a5a8_config_pins_the_two_model_panel() -> None:
    """The A5-A8 plan restricts the panel to two SOTA open-source models."""
    config = judge.load_config(judge.HERE / "judge_config_a5a8.yaml")
    by_id = {m["id"]: m for m in config["models"]}
    assert set(by_id) == {"qwen3.8-max", "kimi-k3"}
    assert by_id["qwen3.8-max"]["role"] == "primary"
    assert by_id["kimi-k3"]["role"] == "sensitivity"
    for model in by_id.values():
        assert model["provider"] == "dashscope"
        assert model["api_key_env"] == "DASHSCOPE_API_KEY"
        assert model["base_url_env"] == "DASHSCOPE_BASE_URL"
        assert model["temperature"] == 0.0
    assert by_id["qwen3.8-max"]["max_response_tokens"] == 500
    assert by_id["kimi-k3"]["max_response_tokens"] == 1000
    assert config["budget"]["max_input_tokens_per_model_run"] == 25_000_000
    assert config["budget"]["max_calls_per_model_run"] == 700
    assert config["determinism_probe"] == {"sample_queries": 8, "repeats": 3}
    assert config["prices"]["estimate"] is True
    assert set(config["prices"]["per_million_tokens"]) == set(by_id)


# --------------------------------------------------------------------------- #
# Response parsing.
# --------------------------------------------------------------------------- #
def test_parse_clean_json() -> None:
    raw = '{"first": "tool:get_market_data", "second": "skill:tushare", "third": "tool:screen_market"}'
    assert protocol.parse_response(raw) == {
        "first": "tool:get_market_data",
        "second": "skill:tushare",
        "third": "tool:screen_market",
    }


def test_parse_fenced_json() -> None:
    raw = '```json\n{"first": "tool:a", "second": "tool:b", "third": "tool:c"}\n```'
    assert protocol.parse_response(raw) == {
        "first": "tool:a", "second": "tool:b", "third": "tool:c",
    }


def test_parse_json_embedded_in_prose() -> None:
    raw = 'Sure! {"first": "tool:a", "second": "tool:b", "third": "tool:c"} — hope this helps.'
    assert protocol.parse_response(raw)["first"] == "tool:a"


def test_parse_missing_keys_yields_none_values() -> None:
    assert protocol.parse_response('{"first": "tool:a"}') == {
        "first": "tool:a", "second": None, "third": None,
    }


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "I cannot choose a single tool.", "[1, 2, 3]", '{"broken": '],
)
def test_parse_garbage_returns_none(raw: str) -> None:
    assert protocol.parse_response(raw) is None


# --------------------------------------------------------------------------- #
# Scoring definitions.
# --------------------------------------------------------------------------- #
_NAME_KINDS = {
    "get_market_data": ("tool",),
    "screen_market": ("tool",),
    "tushare": ("skill",),
    "akshare": ("skill",),
    "yfinance": ("skill",),
}
_ENTRY = {
    "id": "T-001",
    "query": "fetch bars",
    "expected": {"kind": "tool", "name": "get_market_data"},
    "domain": "D01",
    "negatives": ["tushare", "akshare"],
}


def test_scoring_top1_hit() -> None:
    parsed = {"first": "tool:get_market_data", "second": "skill:tushare",
              "third": "skill:akshare"}
    scores = protocol.score_response(parsed, _ENTRY, _NAME_KINDS)
    assert scores == {
        "expected_id": "tool:get_market_data",
        "top1_hit": True,
        "top3_hit": True,
        # Conservative: negatives in top-3 do not count while expected is in.
        "neg_false_recall": False,
    }


def test_scoring_top3_hit_only() -> None:
    parsed = {"first": "skill:tushare", "second": "tool:get_market_data",
              "third": None}
    scores = protocol.score_response(parsed, _ENTRY, _NAME_KINDS)
    assert scores["top1_hit"] is False
    assert scores["top3_hit"] is True
    assert scores["neg_false_recall"] is False


def test_scoring_negative_false_recall() -> None:
    parsed = {"first": "skill:tushare", "second": "skill:akshare",
              "third": "skill:yfinance"}
    scores = protocol.score_response(parsed, _ENTRY, _NAME_KINDS)
    assert scores["top1_hit"] is False
    assert scores["top3_hit"] is False
    assert scores["neg_false_recall"] is True


def test_scoring_without_negatives_is_none() -> None:
    entry = {**_ENTRY, "negatives": None}
    scores = protocol.score_response({"first": "x", "second": None, "third": None},
                                  entry, _NAME_KINDS)
    assert scores["neg_false_recall"] is None


def test_scoring_invalid_response_is_a_clean_miss() -> None:
    scores = protocol.score_response(None, _ENTRY, _NAME_KINDS)
    assert scores["top1_hit"] is False
    assert scores["top3_hit"] is False
    assert scores["neg_false_recall"] is False


# --------------------------------------------------------------------------- #
# Format-tolerant (lenient) scoring — forgives the missing 'kind:' prefix only.
# --------------------------------------------------------------------------- #
def test_lenient_scoring_forgives_bare_name_pick() -> None:
    # The kimi-k3 format artifact: right capability, missing 'kind:' prefix.
    parsed = {"first": "get_market_data", "second": None, "third": None}
    lenient = protocol.score_response_lenient(parsed, _ENTRY, _NAME_KINDS)
    assert lenient["top1_hit_lenient"] is True
    # Strict scoring still counts it a miss (the prefix is part of the id).
    strict = protocol.score_response(parsed, _ENTRY, _NAME_KINDS)
    assert strict["top1_hit"] is False


def test_lenient_scoring_does_not_forgive_wrong_kind() -> None:
    # 'skill:get_market_data' carries a prefix, so it is a real wrong-kind
    # pick, not a format artifact — the lenient score must not forgive it.
    parsed = {"first": "skill:get_market_data", "second": None, "third": None}
    lenient = protocol.score_response_lenient(parsed, _ENTRY, _NAME_KINDS)
    assert lenient["top1_hit_lenient"] is False


def test_lenient_scoring_exact_id_still_hits() -> None:
    parsed = {"first": "tool:get_market_data", "second": None, "third": None}
    lenient = protocol.score_response_lenient(parsed, _ENTRY, _NAME_KINDS)
    assert lenient["top1_hit_lenient"] is True


def test_lenient_scoring_top3_bare_name() -> None:
    parsed = {"first": "tool:other", "second": "get_market_data", "third": None}
    lenient = protocol.score_response_lenient(parsed, _ENTRY, _NAME_KINDS)
    assert lenient["top1_hit_lenient"] is False
    assert lenient["top3_hit_lenient"] is True


def test_lenient_scoring_invalid_is_a_clean_miss() -> None:
    lenient = protocol.score_response_lenient(None, _ENTRY, _NAME_KINDS)
    assert lenient["top1_hit_lenient"] is False
    assert lenient["top3_hit_lenient"] is False


# --------------------------------------------------------------------------- #
# Targeted-subset selection + run tagging (A7/A8 infra).
# --------------------------------------------------------------------------- #
def test_filter_entries_by_refs_keeps_matching_and_order() -> None:
    entries = [
        {"id": "A", "arbitration_ref": "Q5"},
        {"id": "B", "arbitration_ref": "Q6"},
        {"id": "C", "arbitration_ref": "K1"},
        {"id": "D"},
    ]
    kept = judge.filter_entries_by_refs(entries, "Q5,Q6")
    assert [e["id"] for e in kept] == ["A", "B"]
    assert judge.filter_entries_by_refs(entries, "Q5, K6") == [entries[0]]
    assert judge.filter_entries_by_refs(entries, None) == entries
    assert judge.filter_entries_by_refs(entries, "") == entries


def test_trace_path_tag_namespaces_artifacts(tmp_path: Path) -> None:
    untagged = judge.trace_path_for(tmp_path, "m", "post")
    tagged = judge.trace_path_for(tmp_path, "m", "post", "a7target")
    assert untagged.name == "llm_judge_trace_m_post.jsonl"
    assert tagged.name == "llm_judge_trace_m_post_a7target.jsonl"


# --------------------------------------------------------------------------- #
# Resume, budget, and run lifecycle (injected fake client — no network).
# --------------------------------------------------------------------------- #
def test_resume_skips_completed_query_ids(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    trace_path = judge.trace_path_for(tmp_path, model_cfg["id"], "post")

    # Pre-seed a finished first entry under the current template.
    judge.append_line(trace_path, {
        "header": True,
        "prompt_template_sha256": protocol.prompt_template_sha256(),
        "model": model_cfg["id"],
        "surface": "post",
        "corpus_captured_at": corpus["captured_at"],
        "config_pins": {},
    })
    done_entry = entries[0]
    judge.append_line(trace_path, {
        "query_id": done_entry["id"], "model": model_cfg["id"], "surface": "post",
        "parsed": {"first": "tool:get_market_data", "second": None, "third": None},
        "top1_hit": True, "top3_hit": True, "neg_false_recall": False,
        "prompt_tokens": 100, "completion_tokens": 10, "api_calls": 1,
    })

    pending = entries[1]
    client = FakeJudgeClient([_reply_for(pending)])
    code = judge.run_surface(
        model_cfg=model_cfg, surface="post", caps=_caps(), corpus=corpus,
        entries=entries, env={"DASHSCOPE_API_KEY": "test-only"},
        artifacts_dir=tmp_path, prices={}, limit=2,
        client_factory=_factory_for(client),
    )
    assert code == 0
    assert len(client.completions.calls) == 1, "completed id must be skipped"
    _, lines = judge.load_trace(trace_path)
    assert [line["query_id"] for line in lines] == [done_entry["id"], pending["id"]]


def test_trace_header_template_drift_is_refused(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    trace_path = judge.trace_path_for(tmp_path, model_cfg["id"], "post")
    judge.append_line(trace_path, {
        "header": True, "prompt_template_sha256": "deadbeef",
        "model": model_cfg["id"], "surface": "post",
    })
    client = FakeJudgeClient([])
    code = judge.run_surface(
        model_cfg=model_cfg, surface="post", caps=_caps(), corpus=corpus,
        entries=entries, env={}, artifacts_dir=tmp_path, prices={}, limit=1,
        client_factory=_factory_for(client),
    )
    assert code == judge.EXIT_CONFIG
    assert client.completions.calls == []


def test_budget_call_cap_aborts_before_overshoot(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    client = FakeJudgeClient([_reply_for(entries[0]), _reply_for(entries[1])])
    code = judge.run_surface(
        model_cfg=model_cfg, surface="post",
        caps=_caps(max_calls=1), corpus=corpus, entries=entries,
        env={"DASHSCOPE_API_KEY": "test-only"}, artifacts_dir=tmp_path,
        prices={}, limit=2, client_factory=_factory_for(client),
    )
    assert code == judge.EXIT_BUDGET
    assert len(client.completions.calls) == 1, "second call must never happen"
    _, lines = judge.load_trace(
        judge.trace_path_for(tmp_path, model_cfg["id"], "post")
    )
    assert len(lines) == 1


def test_budget_token_cap_aborts_before_first_call(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    calls: list = []

    def factory(model_cfg, env):
        calls.append(model_cfg)
        return FakeJudgeClient([])

    code = judge.run_surface(
        model_cfg=model_cfg, surface="post", caps=_caps(max_tokens=5),
        corpus=corpus, entries=entries, env={}, artifacts_dir=tmp_path,
        prices={}, limit=1, client_factory=factory,
    )
    assert code == judge.EXIT_BUDGET
    assert calls == [], "no client may be constructed once the cap is hit"


def test_budget_state_rederived_from_trace(tmp_path: Path) -> None:
    """Resume re-reads spent tokens/calls so a restart cannot double-spend."""
    lines = [
        {"prompt_tokens": 100, "completion_tokens": 20, "api_calls": 1},
        {"prompt_tokens": 150, "completion_tokens": 30, "api_calls": 2},
    ]
    state = judge.derive_spent(lines)
    assert state.tokens_spent == 300
    assert state.calls_made == 3


def test_budget_violation_messages_name_the_cap() -> None:
    state = judge.BudgetState(tokens_spent=0, calls_made=0)
    caps = _caps(max_tokens=100, max_calls=1)
    assert judge.budget_violation(state, 101, caps) is not None
    assert "max_input_tokens_per_model_run" in judge.budget_violation(
        state, 101, caps
    )
    assert "max_calls_per_model_run" in judge.budget_violation(
        judge.BudgetState(tokens_spent=0, calls_made=1), 10, caps
    )
    assert judge.budget_violation(state, 100, caps) is None


def test_invalid_response_is_recorded_not_fatal(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    client = FakeJudgeClient([_FakeResponse("I refuse to answer.")])
    code = judge.run_surface(
        model_cfg=model_cfg, surface="post", caps=_caps(), corpus=corpus,
        entries=entries, env={"DASHSCOPE_API_KEY": "test-only"},
        artifacts_dir=tmp_path, prices={}, limit=1,
        client_factory=_factory_for(client),
    )
    assert code == 0, "one bad response must never crash the run"
    _, lines = judge.load_trace(
        judge.trace_path_for(tmp_path, model_cfg["id"], "post")
    )
    assert lines[0]["parsed"] is None
    assert lines[0]["top1_hit"] is False
    aggregates = judge_report.aggregate_lines(lines, entries)
    assert aggregates["invalid_responses"] == 1
    assert aggregates["top1_hits"] == 0


def test_non_transient_api_error_aborts_without_burning_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "_transient_error_types", lambda: (ValueError,))
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    client = FakeJudgeClient([KeyError("auth exploded")])
    code = judge.run_surface(
        model_cfg=model_cfg, surface="post", caps=_caps(), corpus=corpus,
        entries=entries, env={}, artifacts_dir=tmp_path, prices={}, limit=2,
        client_factory=_factory_for(client),
    )
    assert code == judge.EXIT_ERROR
    _, lines = judge.load_trace(
        judge.trace_path_for(tmp_path, model_cfg["id"], "post")
    )
    assert lines == [], "no query may be consumed by a client-level failure"


def test_single_transient_retry_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge, "_transient_error_types", lambda: (ValueError,))
    client = FakeJudgeClient([
        ValueError("connection reset"),
        _FakeResponse('{"first": "tool:a", "second": null, "third": null}'),
    ])
    outcome = judge.call_judge(client, _model_cfg(), [{"role": "user", "content": "x"}])
    assert outcome["error"] is None
    assert outcome["api_calls"] == 2
    assert outcome["retried"] is True
    assert outcome["raw"].startswith("{")


def test_double_transient_failure_records_error_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(judge, "_transient_error_types", lambda: (ValueError,))
    client = FakeJudgeClient([ValueError("one"), ValueError("two")])
    outcome = judge.call_judge(client, _model_cfg(), [{"role": "user", "content": "x"}])
    assert outcome["error"] is not None
    assert outcome["api_calls"] == 2
    assert outcome["raw"] == ""


# --------------------------------------------------------------------------- #
# .env parsing (manual — python-dotenv is banned in this environment).
# --------------------------------------------------------------------------- #
def test_env_file_manual_parse(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment line\n"
        "\n"
        "DASHSCOPE_API_KEY=sk-abc123\n"
        'QUOTED="hello world"\n'
        "SINGLE='x y'\n"
        "export EXPORTED=yes\n"
        "EMPTY=\n"
        "MALFORMED LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    values = judge.load_env_file(env_path)
    assert values["DASHSCOPE_API_KEY"] == "sk-abc123"
    assert values["QUOTED"] == "hello world"
    assert values["SINGLE"] == "x y"
    assert values["EXPORTED"] == "yes"
    assert values["EMPTY"] == ""
    assert "MALFORMED LINE WITHOUT EQUALS" not in values


def test_env_file_missing_is_empty(tmp_path: Path) -> None:
    assert judge.load_env_file(tmp_path / "nope.env") == {}


def test_missing_key_skips_model_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("UNRELATED=1\n", encoding="utf-8")
    monkeypatch.setattr(judge, "ENV_PATH", env_path)
    code = judge.main(["--surface", "post", "--model", "qwen3.8-max"])
    assert code == 0, "a missing optional key must never fail the run"
    assert "skipped (no DASHSCOPE_API_KEY key)" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Aggregation + determinism probe.
# --------------------------------------------------------------------------- #
def test_aggregate_per_domain_and_tokens() -> None:
    _, entries = _assets()
    first, second = entries[0], entries[1]
    lines = [
        {"query_id": first["id"], "parsed": {"first": "x"}, "top1_hit": True,
         "top3_hit": True, "neg_false_recall": False,
         "prompt_tokens": 100, "completion_tokens": 5, "api_calls": 1},
        {"query_id": second["id"], "parsed": None, "top1_hit": False,
         "top3_hit": False, "neg_false_recall": None,
         "prompt_tokens": 120, "completion_tokens": 0, "api_calls": 2},
    ]
    aggregates = judge_report.aggregate_lines(lines, entries)
    assert aggregates["entries"] == 2
    assert aggregates["top1_hits"] == 1
    assert aggregates["invalid_responses"] == 1
    assert aggregates["prompt_tokens"] == 220
    assert aggregates["api_calls"] == 3
    assert aggregates["per_domain"]["D01"]["entries"] == 2


def test_probe_reports_first_pick_agreement(tmp_path: Path) -> None:
    corpus, entries = _assets()
    model_cfg = _model_cfg()
    probe_cfg = {"sample_queries": 2, "repeats": 2}
    q1, q2 = entries[0], entries[1]
    hit = f'{{"first": "{q1["expected"]["kind"]}:{q1["expected"]["name"]}"}}'
    client = FakeJudgeClient([
        _FakeResponse(hit),                    # q1 repeat 0
        _FakeResponse(hit),                    # q1 repeat 1 (agrees)
        _FakeResponse('{"first": "tool:a"}'),  # q2 repeat 0
        _FakeResponse('{"first": "tool:b"}'),  # q2 repeat 1 (disagrees)
    ])
    code = judge.run_probe(
        model_cfg=model_cfg, surface="post", caps=_caps(), probe_cfg=probe_cfg,
        corpus=corpus, entries=entries, env={"DASHSCOPE_API_KEY": "test-only"},
        artifacts_dir=tmp_path, client_factory=_factory_for(client),
    )
    assert code == 0
    assert len(client.completions.calls) == 4
    probe_path = tmp_path / f"llm_judge_probe_{model_cfg['id']}_post.jsonl"
    _, lines = judge.load_trace(probe_path)
    assert len(lines) == 4
    assert {(line["query_id"], line["repeat"]) for line in lines} == {
        (q1["id"], 0), (q1["id"], 1), (q2["id"], 0), (q2["id"], 1),
    }


def test_cost_estimate_uses_price_table() -> None:
    config = judge.load_config(judge.CONFIG_PATH)
    prices = config["prices"]
    cost = judge_report.estimate_cost("qwen3.8-max", 1_000_000, 1_000_000, prices)
    expected = float(prices["per_million_tokens"]["qwen3.8-max"]["input"]) + float(
        prices["per_million_tokens"]["qwen3.8-max"]["output"]
    )
    assert cost == pytest.approx(expected)
    assert judge_report.estimate_cost("unknown-model", 10, 10, prices) is None


# --------------------------------------------------------------------------- #
# Panel statistics: paired McNemar / Wilson / flips / cross-checks.
# --------------------------------------------------------------------------- #
def test_mcnemar_exact_values() -> None:
    """Exact two-sided binomial on discordant pairs; no discordance -> 1.0."""
    assert stats.mcnemar_p(0, 0) == 1.0
    assert stats.mcnemar_p(6, 0) == pytest.approx(0.03125)
    assert stats.mcnemar_p(3, 3) == 1.0
    assert stats.mcnemar_p(2, 5) == pytest.approx(stats.mcnemar_p(5, 2))


def test_wilson_ci_bounds_and_known_value() -> None:
    lo, hi = stats.wilson_ci(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3)
    assert hi == pytest.approx(0.7634, abs=1e-3)
    assert stats.wilson_ci(0, 10)[0] == 0.0
    assert stats.wilson_ci(10, 10)[1] == pytest.approx(1.0)
    assert stats.wilson_ci(0, 0) is None


def _record(query_id, top1, first="tool:x", top3=None, neg=False, invalid=False):
    return {
        "query_id": query_id,
        "parsed": None if invalid else {"first": first, "second": None, "third": None},
        "top1_hit": top1,
        "top3_hit": top1 if top3 is None else top3,
        "neg_false_recall": neg,
        "prompt_tokens": 100, "completion_tokens": 10, "api_calls": 1,
    }


def test_build_pairs_aligns_and_sorts() -> None:
    baseline_map = {"Q2": _record("Q2", True), "Q1": _record("Q1", False)}
    post_map = {"Q1": _record("Q1", True), "Q3": _record("Q3", True)}
    rows = stats.build_pairs(baseline_map, post_map)
    assert [r.query_id for r in rows] == ["Q1"], "only common ids pair"
    assert rows[0].baseline_top1 is False and rows[0].post_top1 is True


def _pair(query_id, base_top1, post_top1, base_first="tool:b", post_first="tool:p"):
    return stats.PairRow(
        query_id=query_id,
        baseline_top1=base_top1, post_top1=post_top1,
        baseline_top3=base_top1, post_top3=post_top1,
        baseline_first=base_first, post_first=post_first,
    )


def test_paired_block_counts_and_flip_directions() -> None:
    rows = [
        _pair("Q1", True, False, base_first="tool:get_market_data"),
        _pair("Q2", True, True),
        _pair("Q3", False, True, post_first="tool:get_market_data"),
        _pair("Q4", False, False),
    ]
    block = stats.paired_block(rows, "baseline_top1", "post_top1")
    assert block["n_pairs"] == 4
    assert block["baseline_hits"] == 2 and block["post_hits"] == 2
    assert block["n_improved"] == 1 and block["n_regressed"] == 1
    assert block["delta"] == 0.0
    assert block["mcnemar_p"] == 1.0
    assert stats.flip_rows(rows) == [
        {"query_id": "Q1", "direction": "regressed",
         "baseline_first": "tool:get_market_data", "post_first": "tool:p"},
        {"query_id": "Q3", "direction": "improved",
         "baseline_first": "tool:b", "post_first": "tool:get_market_data"},
    ]


def test_rate_delta_invalid_and_neg() -> None:
    baseline_map = {
        "Q1": _record("Q1", True, neg=False),
        "Q2": _record("Q2", False, invalid=True),
    }
    post_map = {
        "Q1": _record("Q1", True, neg=True),
        "Q2": _record("Q2", True, neg=False),
    }
    invalid = stats.rate_delta(baseline_map, post_map, "invalid")
    assert invalid["baseline"] == pytest.approx(0.5)
    assert invalid["post"] == 0.0
    assert invalid["delta"] == pytest.approx(-0.5)
    neg = stats.rate_delta(baseline_map, post_map, "neg_false_recall")
    assert neg["baseline"] == 0.0 and neg["post"] == pytest.approx(0.5)


def test_probe_agreement_from_records(tmp_path: Path) -> None:
    model_id = "qwen3.8-max"
    probe_path = stats.probe_path_for(tmp_path, model_id, "post")
    for query_id, picks in (("Q1", ["tool:a", "tool:a"]), ("Q2", ["tool:a", "tool:b"])):
        for repeat, pick in enumerate(picks):
            judge.append_line(probe_path, {
                "query_id": query_id, "repeat": repeat,
                "parsed": {"first": pick, "second": None, "third": None},
            })
    agreement = stats.probe_agreement(tmp_path, model_id, "post")
    assert agreement == {"agreement": pytest.approx(0.75), "n_queries": 2, "repeats": 2}
    assert stats.probe_agreement(tmp_path, "no-such-model", "post") is None


def test_lexical_agreement_and_disagreement_set() -> None:
    judge_map = {
        "Q1": {"top1_hit": True}, "Q2": {"top1_hit": False}, "Q3": {"top1_hit": True},
    }
    lexical_map = {"Q1": True, "Q2": True, "Q4": False}
    agreement, disagree = stats.lexical_agreement(judge_map, lexical_map)
    assert agreement == pytest.approx(0.5)
    assert disagree == ["Q2"]
    assert stats.lexical_agreement({}, lexical_map) == (None, [])


def test_lexical_top1_covers_both_surfaces() -> None:
    outcomes = stats.lexical_top1()
    assert set(outcomes) == {"baseline", "post"}
    assert len(outcomes["post"]) == 158
    # The three Q2 rename entries (D02-005/006/007) reference the post-rename
    # skill and are not evaluatable on the frozen pre-rename baseline corpus.
    assert len(outcomes["baseline"]) == 155
    assert {"D02-005", "D02-006", "D02-007"} <= set(outcomes["post"])
    assert not {"D02-005", "D02-006", "D02-007"} & set(outcomes["baseline"])


def _seed_panel(tmp_path, model_id, baseline_records, post_records) -> None:
    for surface, records in (("baseline", baseline_records), ("post", post_records)):
        path = judge.trace_path_for(tmp_path, model_id, surface)
        judge.append_line(path, {
            "header": True,
            "prompt_template_sha256": protocol.prompt_template_sha256(),
            "model": model_id, "surface": surface,
            "corpus_captured_at": "t", "config_pins": {},
        })
        for record in records:
            judge.append_line(path, record)


def test_stats_report_end_to_end_and_deterministic(tmp_path: Path) -> None:
    config = judge.load_config(judge.CONFIG_PATH)
    model_id = config["models"][0]["id"]
    baseline_records = [
        _record("D01-001", True, first="tool:get_market_data"),
        _record("D01-002", False, first="skill:yfinance"),
        _record("D01-003", False, first="tool:other"),
    ]
    post_records = [
        _record("D01-001", True, first="tool:get_market_data"),
        _record("D01-002", True, first="tool:get_market_data"),
        _record("D01-003", False, invalid=True),
    ]
    _seed_panel(tmp_path, model_id, baseline_records, post_records)
    report = stats.build_stats_report(tmp_path, config)
    for section in (
        "## Panel and pins",
        "## Per-model paired results (exact McNemar)",
        "## Pooled across models",
        "## Flip lists",
        "## Lexical-vs-semantic agreement",
        "## Determinism audit",
        "## Invalid-response audit",
        "## Cost summary",
    ):
        assert section in report
    assert model_id in report
    assert "D01-002" in report and "improved" in report
    assert report == stats.build_stats_report(tmp_path, config), (
        "the report must be deterministic given the traces"
    )


def test_stats_report_handles_missing_traces(tmp_path: Path) -> None:
    config = judge.load_config(judge.CONFIG_PATH)
    report = stats.build_stats_report(tmp_path, config)
    assert "- pooled pairs: 0" in report
    assert "no probe" in report
