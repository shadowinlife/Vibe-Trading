"""Offline tests for the B-batch verdict statistics — no LLM calls.

Pins the four methodology-gap closures of the LLM-judge evaluation
(``artifacts/llm_judge_design.md``, 2026-08-27 review) on synthetic paired
traces:

* exact Clopper-Pearson non-inferiority CI on the paired difference;
* strict-only primary verdict (lenient can never flip it);
* expected-absent exclusion with a descriptive behavior probe;
* the noise-band interpretation rule;
* test-retest probe agreement (``retest_noise``) and ``--probe-tag``
  administration separation in ``run_llm_judge``.

Every scenario is deterministic and offline: traces are seeded into tmp
directories through the same append helpers the real runner uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from src.evals.tool_selection import b_batch_report as report  # noqa: E402
from src.evals.tool_selection import b_batch_stats as bstats  # noqa: E402
from src.evals.tool_selection import llm_judge_protocol as protocol  # noqa: E402
from src.evals.tool_selection import retest_noise as retest  # noqa: E402
from src.evals.tool_selection import run_llm_judge as judge  # noqa: E402

MARGIN = 0.05


# --------------------------------------------------------------------------- #
# Synthetic trace seeding.
# --------------------------------------------------------------------------- #
def _rec(qid, top1, *, first="tool:target", expected="tool:target", lenient=None, top3=None):
    rec = {
        "query_id": qid,
        "parsed": {"first": first, "second": None, "third": None},
        "expected_id": expected,
        "top1_hit": top1,
        "top3_hit": top1 if top3 is None else top3,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "api_calls": 1,
    }
    if lenient is not None:
        rec["top1_hit_lenient"] = lenient
        rec["top3_hit_lenient"] = lenient
    return rec


def _seed(tmp_path, model, surface, records, tag=None):
    path = judge.trace_path_for(tmp_path, model, surface, tag)
    judge.append_line(
        path,
        {
            "header": True,
            "prompt_template_sha256": protocol.prompt_template_sha256(),
            "model": model,
            "surface": surface,
            "corpus_captured_at": "t",
            "config_pins": {},
        },
    )
    for rec in records:
        judge.append_line(path, rec)


def _seed_scenario(tmp_path, model, n, improved, regressed, *, tag=None, with_lenient=True):
    """Seed paired traces: `improved` flips up, `regressed` flips down."""
    base, post = [], []
    lenient = with_lenient
    for i in range(n):
        qid = f"Q-{i:03d}"
        if i < improved:  # baseline miss -> post hit
            base.append(_rec(qid, False, first="tool:wrong", lenient=False if lenient else None))
            post.append(_rec(qid, True, lenient=True if lenient else None))
        elif i < improved + regressed:  # baseline hit -> post miss
            base.append(_rec(qid, True, lenient=True if lenient else None))
            post.append(_rec(qid, False, first="tool:wrong", lenient=False if lenient else None))
        else:  # concordant hit
            base.append(_rec(qid, True, lenient=True if lenient else None))
            post.append(_rec(qid, True, lenient=True if lenient else None))
    _seed(tmp_path, model, "baseline", base, tag)
    _seed(tmp_path, model, "post", post, tag)


def _result(
    tmp_path,
    *,
    models=("m1",),
    margin=MARGIN,
    noise_band=None,
    absent=frozenset(),
    tag=None,
):
    return bstats.build_result(models, tag, tmp_path, absent, margin, noise_band)


# --------------------------------------------------------------------------- #
# Exact CI construction (Clopper-Pearson on the discordant direction).
# --------------------------------------------------------------------------- #
def test_exact_delta_ci_construction() -> None:
    """CP interval on q = b/(b+c), transformed by Δ = d(2q − 1)/n."""
    assert bstats.exact_delta_ci(0, 0, 10) == (0.0, 0.0)
    assert bstats.exact_delta_ci(1, 1, 0) is None
    # Anchor: CP(5, 10) = [0.18709, 0.81291]; d=10, n=20 -> symmetric Δ CI.
    lo, hi = bstats.exact_delta_ci(5, 5, 20)
    assert lo == pytest.approx(-0.31291, abs=1e-4)
    assert hi == pytest.approx(+0.31291, abs=1e-4)
    # One-sided discordance pins the lower bound at -d/n.
    lo, hi = bstats.exact_delta_ci(0, 12, 400)
    assert lo == pytest.approx(-0.03)
    assert hi == pytest.approx(-0.01412, abs=1e-4)


# --------------------------------------------------------------------------- #
# The seven pre-registered verdict scenarios.
# --------------------------------------------------------------------------- #
def test_clear_improvement_passes(tmp_path: Path) -> None:
    _seed_scenario(tmp_path, "m1", n=200, improved=25, regressed=2)
    result = _result(tmp_path)
    c1 = result["criteria"]["C1"]
    assert c1["stats"]["delta"] == pytest.approx(0.115)
    assert c1["stats"]["delta_ci"][0] == pytest.approx(0.06942, abs=1e-4)
    assert c1["pass"] is True
    assert result["verdict"] == bstats.VERDICT_PASS


def test_clear_regression_fails(tmp_path: Path) -> None:
    _seed_scenario(tmp_path, "m1", n=200, improved=2, regressed=30)
    result = _result(tmp_path)
    c1 = result["criteria"]["C1"]
    assert c1["stats"]["delta"] == pytest.approx(-0.14)
    assert c1["stats"]["delta_ci"][0] == pytest.approx(-0.15755, abs=1e-4)
    assert c1["pass"] is False
    assert result["verdict"] == bstats.VERDICT_FAIL


def test_non_inferior_within_margin_passes_despite_significant_p(
    tmp_path: Path,
) -> None:
    """Δ=-3pp with exact CI [-3.0, -1.4]pp stays inside δ=5pp: PASS — even
    though the (report-only) McNemar p is highly significant."""
    _seed_scenario(tmp_path, "m1", n=400, improved=0, regressed=12)
    result = _result(tmp_path)
    c1 = result["criteria"]["C1"]
    assert c1["stats"]["mcnemar_p"] == pytest.approx(0.000488, abs=1e-6)
    assert c1["stats"]["mcnemar_p"] < 0.05
    assert c1["pass"] is True, "the p-value must never drive the verdict"
    assert result["verdict"] == bstats.VERDICT_PASS


def test_ci_straddling_margin_fails_despite_nonsignificant_p(tmp_path: Path) -> None:
    """Δ=-2pp looks benign and p=0.45 reads 'non-significant', but the
    exact CI lower bound -5.57pp crosses -δ: non-inferiority FAILS."""
    _seed_scenario(tmp_path, "m1", n=200, improved=6, regressed=10)
    result = _result(tmp_path)
    c1 = result["criteria"]["C1"]
    assert c1["stats"]["delta"] == pytest.approx(-0.02)
    assert c1["stats"]["mcnemar_p"] == pytest.approx(0.4545, abs=1e-3)
    assert c1["stats"]["delta_ci"][0] == pytest.approx(-0.05568, abs=1e-4)
    assert c1["pass"] is False
    assert result["verdict"] == bstats.VERDICT_FAIL


def test_absent_ids_excluded_and_probed(tmp_path: Path) -> None:
    # 18 concordant efficacy pairs + 2 absent queries whose post picks can
    # never be the removed expected capability (it is off the surface).
    base = [_rec(f"Q-{i:03d}", True) for i in range(18)]
    post = [_rec(f"Q-{i:03d}", True) for i in range(18)]
    base.append(_rec("A-001", False, first="tool:other", expected="tool:removed_a"))
    post.append(_rec("A-001", False, first="tool:alt1", expected="tool:removed_a"))
    base.append(_rec("A-003", True, expected="tool:removed_b"))
    post.append(_rec("A-003", False, first="tool:alt2", expected="tool:removed_b"))
    _seed(tmp_path, "m1", "baseline", base)
    _seed(tmp_path, "m1", "post", post)
    result = _result(tmp_path, absent=frozenset({"A-001", "A-003"}))
    c1 = result["criteria"]["C1"]
    assert c1["stats"]["n_pairs"] == 18
    assert c1["stats"]["n_improved"] == 0 and c1["stats"]["n_regressed"] == 0
    probe = result["criteria"]["C5"]["probe"]
    assert probe["n_queries"] == 2
    assert probe["query_ids"] == ["m1:A-001", "m1:A-003"]
    assert probe["removed_capability_pick_events"] == 0
    assert probe["post_first_distribution"] == {"tool:alt1": 1, "tool:alt2": 1}
    assert result["config"]["absent_ids_not_found"] == []


def test_removed_capability_pick_event_flags_c5(tmp_path: Path) -> None:
    """If a post top-3 pick ever equals the removed expected id, C5 fails."""
    base = [_rec("Q-001", False, first="tool:other", expected="tool:removed_a")]
    post = [_rec("Q-001", False, first="tool:removed_a", expected="tool:removed_a", top3=True)]
    _seed(tmp_path, "m1", "baseline", base)
    _seed(tmp_path, "m1", "post", post)
    result = _result(tmp_path, absent=frozenset({"Q-001"}))
    c5 = result["criteria"]["C5"]
    assert c5["probe"]["removed_capability_pick_events"] == 1
    assert c5["pass"] is False


def test_absent_ids_not_found_are_reported(tmp_path: Path) -> None:
    _seed_scenario(tmp_path, "m1", n=10, improved=1, regressed=0)
    result = _result(tmp_path, absent=frozenset({"NOPE-1"}))
    assert result["config"]["absent_ids_not_found"] == ["NOPE-1"]
    assert result["primary_efficacy"]["n_pairs_pooled"] == 10


def test_noise_band_override(tmp_path: Path) -> None:
    """Pooled |Δ| within the band reads as no effect — a third state."""
    _seed_scenario(tmp_path, "m1", n=50, improved=0, regressed=0)
    plain = _result(tmp_path)
    assert plain["verdict"] == bstats.VERDICT_PASS
    banded = _result(tmp_path, noise_band=0.05)
    assert banded["verdict"] == bstats.VERDICT_NOISE
    assert banded["criteria"]["C2"]["within_band"] is True
    # A delta above the band stays interpretable.
    _seed_scenario(tmp_path, "m2", n=50, improved=10, regressed=0)
    loud = _result(tmp_path, models=("m2",), noise_band=0.05)
    assert loud["criteria"]["C2"]["within_band"] is False
    assert loud["verdict"] == bstats.VERDICT_PASS


def test_lenient_cannot_flip_strict(tmp_path: Path) -> None:
    """Strict regresses hard; lenient (forgiving bare-name flips) soars.
    The verdict must stay FAIL — enforced structurally."""
    base, post = [], []
    for i in range(100):
        qid = f"Q-{i:03d}"
        if i < 20:  # strict regression, lenient 'recovery' (bare-name pick)
            base.append(_rec(qid, True, lenient=True))
            post.append(_rec(qid, False, first="target", lenient=True))
        else:
            base.append(_rec(qid, True, lenient=True))
            post.append(_rec(qid, True, lenient=True))
    _seed(tmp_path, "m1", "baseline", base)
    _seed(tmp_path, "m1", "post", post)
    result = _result(tmp_path)
    c1, c4 = result["criteria"]["C1"], result["criteria"]["C4"]
    assert c1["stats"]["delta"] == pytest.approx(-0.20)
    assert c1["pass"] is False
    assert c4["pooled"]["delta"] == pytest.approx(0.0), "lenient sees no change"
    assert c4["flips_c1"] is False
    assert result["verdict"] == bstats.VERDICT_FAIL


# --------------------------------------------------------------------------- #
# Caliber availability, pooling, artifacts.
# --------------------------------------------------------------------------- #
def test_lenient_suppressed_when_field_missing(tmp_path: Path) -> None:
    """Old strict-only traces: lenient view suppressed, never all-miss."""
    _seed_scenario(tmp_path, "m1", n=100, improved=2, regressed=1, with_lenient=False)
    result = _result(tmp_path)
    c4 = result["criteria"]["C4"]
    assert c4["available"] is False
    assert c4["pooled"] is None
    assert "Suppressed" in report.render_verdict_report(result)
    assert result["verdict"] == bstats.VERDICT_PASS  # strict still decides


def test_two_models_pool_and_per_model_criteria(tmp_path: Path) -> None:
    _seed_scenario(tmp_path, "m1", n=100, improved=10, regressed=0)
    _seed_scenario(tmp_path, "m2", n=100, improved=0, regressed=2)
    result = _result(tmp_path, models=("m1", "m2"))
    assert result["primary_efficacy"]["n_pairs_pooled"] == 200
    assert result["criteria"]["C1"]["stats"]["delta"] == pytest.approx(0.04)
    c3 = result["criteria"]["C3"]["per_model"]
    assert c3["m1"]["pass"] is True and c3["m2"]["pass"] is True
    assert c3["m2"]["stats"]["delta"] == pytest.approx(-0.02)


def test_verdict_artifacts_written_and_name_criteria(tmp_path: Path) -> None:
    _seed_scenario(tmp_path, "qwen3.8-max", n=60, improved=4, regressed=1)
    _seed_scenario(tmp_path, "kimi-k3", n=60, improved=2, regressed=1)
    code = bstats.main(
        [
            "--artifacts-dir",
            str(tmp_path),
            "--margin",
            "0.05",
            "--noise-band",
            "0.02",
            "--absent-ids",
            "Q-000,Q-999",
            "--output-prefix",
            "b_batch_verdict",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "b_batch_verdict.json").read_text())
    assert set(payload["criteria"]) == {"C1", "C2", "C3", "C4", "C5"}
    assert payload["config"]["absent_ids_not_found"] == ["Q-999"]
    md = (tmp_path / "b_batch_verdict.md").read_text()
    for criterion in ("C1", "C2", "C3", "C4", "C5"):
        assert criterion in md
    assert payload["verdict"] in md
    assert "report only" in md  # C3/C4 carry no gate authority


def test_cli_rejects_bad_margin_and_noise_band(tmp_path: Path) -> None:
    assert bstats.main(["--artifacts-dir", str(tmp_path), "--margin", "0"]) == 2
    assert bstats.main(["--artifacts-dir", str(tmp_path), "--noise-band", "1.5"]) == 2


def test_missing_traces_fail_closed(tmp_path: Path) -> None:
    result = _result(tmp_path)
    assert result["criteria"]["C1"]["has_data"] is False
    assert result["criteria"]["C1"]["pass"] is False
    assert result["verdict"] == bstats.VERDICT_FAIL


# --------------------------------------------------------------------------- #
# Test-retest probe agreement + --probe-tag administration separation.
# --------------------------------------------------------------------------- #
def _probe_line(qid, repeat, first):
    return {
        "query_id": qid,
        "repeat": repeat,
        "parsed": (None if first is None else {"first": first, "second": None, "third": None}),
    }


def test_retest_noise_agreement(tmp_path: Path) -> None:
    lines_a = [
        _probe_line("Q1", 0, "tool:a"),
        _probe_line("Q1", 1, "tool:a"),
        _probe_line("Q2", 0, "tool:a"),
        _probe_line("Q2", 1, "tool:b"),
        _probe_line("Q3", 0, "tool:c"),
        _probe_line("Q3", 1, "tool:c"),
        _probe_line("Q4", 0, None),
        _probe_line("Q4", 1, "tool:d"),
    ]
    lines_b = [
        _probe_line("Q1", 0, "tool:a"),
        _probe_line("Q1", 1, "tool:b"),
        _probe_line("Q2", 0, "tool:b"),
        _probe_line("Q2", 1, "tool:b"),
        _probe_line("Q3", 0, "tool:c"),
        _probe_line("Q3", 1, "tool:d"),
        _probe_line("Q4", 0, "tool:d"),
        _probe_line("Q4", 1, "tool:d"),
    ]
    result = retest.compare_probes(lines_a, lines_b)
    # Representatives (mode, ties -> earliest repeat):
    #   A: Q1=a Q2=a Q3=c Q4=None ; B: Q1=a Q2=b Q3=c Q4=d
    assert result["n_queries"] == 4
    assert result["rho"] == pytest.approx(0.5)
    assert result["per_query"]["Q2"]["agree"] is False
    assert result["per_query"]["Q4"] == {
        "first_a": None,
        "first_b": "tool:d",
        "agree": False,
    }
    assert retest.compare_probes(lines_a, [])["rho"] is None


def test_retest_noise_probe_path_naming() -> None:
    base = Path("/tmp/artifacts")
    assert retest.probe_path_for(base, "m", "post").name == ("llm_judge_probe_m_post.jsonl")
    assert retest.probe_path_for(base, "m", "post", "run1").name == ("llm_judge_probe_m_post_run1.jsonl")


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [type("C", (), {"message": _FakeMessage(content)})()]
        self.usage = type("U", (), {"prompt_tokens": 50, "completion_tokens": 5})()


class _FakeCompletions:
    def __init__(self, client: "_FakeClient") -> None:
        self._client = client

    def create(self, *, model, messages, temperature, max_tokens):
        self._client.n_calls += 1
        return self._client._replies.pop(0)


class _FakeClient:
    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.n_calls = 0
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self)})()


def test_probe_tag_separates_administrations(tmp_path: Path) -> None:
    """Two --probe-tag administrations land in distinct files; the second
    run's resume logic sees an empty file and records every call again."""
    corpus, entries = judge.load_corpus("post"), judge.load_queries()
    model_cfg = next(m for m in judge.load_config(judge.CONFIG_PATH)["models"] if m["role"] == "primary")
    probe_cfg = {"sample_queries": 2, "repeats": 2}
    reply = _FakeResponse('{"first": "tool:a", "second": null, "third": null}')
    for tag in ("run1", "run2"):
        client = _FakeClient([reply] * 4)
        code = judge.run_probe(
            model_cfg=model_cfg,
            surface="post",
            caps=judge.BudgetCaps(max_tokens=25_000_000, max_calls=700),
            probe_cfg=probe_cfg,
            corpus=corpus,
            entries=entries,
            env={"DASHSCOPE_API_KEY": "test-only"},
            artifacts_dir=tmp_path,
            tag=tag,
            client_factory=lambda cfg, env, c=client: c,
        )
        assert code == 0
        assert client.n_calls == 4, f"administration {tag} must record all calls"
    path_a = tmp_path / f"llm_judge_probe_{model_cfg['id']}_post_run1.jsonl"
    path_b = tmp_path / f"llm_judge_probe_{model_cfg['id']}_post_run2.jsonl"
    assert path_a.exists() and path_b.exists()
    _, lines_a = judge.load_trace(path_a)
    _, lines_b = judge.load_trace(path_b)
    assert len(lines_a) == 4 and len(lines_b) == 4
    comparison = retest.compare_probes(lines_a, lines_b)
    assert comparison["rho"] == pytest.approx(1.0)


def test_probe_tag_cli_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_probe(**kwargs):
        captured.update(kwargs)
        return 0

    env_path = tmp_path / ".env"
    env_path.write_text("DASHSCOPE_API_KEY=test-only\n", encoding="utf-8")
    monkeypatch.setattr(judge, "run_probe", fake_run_probe)
    monkeypatch.setattr(judge, "ENV_PATH", env_path)
    monkeypatch.setattr(judge, "ARTIFACTS_DIR", tmp_path)

    assert (
        judge.main(
            [
                "--probe-only",
                "--model",
                "qwen3.8-max",
                "--tag",
                "main",
                "--probe-tag",
                "run1",
            ]
        )
        == 0
    )
    assert captured["tag"] == "run1", "--probe-tag wins for the probe artifact"
    assert judge.main(["--probe-only", "--model", "qwen3.8-max", "--tag", "main"]) == 0
    assert captured["tag"] == "main", "legacy --tag behavior is preserved"
