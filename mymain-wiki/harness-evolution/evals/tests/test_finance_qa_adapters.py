"""Tests for the FinanceBench-150 and FinEval adapters (todo 5).

Offline only: the mock harness runs synthetic tasks through the REAL
scorers; the opencode path uses a fake bridge plus injected fake dataset
loaders (seams: ``run.build_bridge``, ``finance_qa_adapter.load_*``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.evals.harness_bench import opencode_bridge, report, run
from src.evals.harness_bench.adapter import HarnessAdapter
from src.evals.harness_bench.adapters import finance_qa_adapter as fqa
from src.evals.harness_bench.adapters import finance_qa_scoring as fqs

PKG_DIR = Path(report.__file__).resolve().parent
SUBSET_PATH = PKG_DIR / "artifacts" / "fineval_subset.json"


def _argv(benchmark, tasks="5", harness="mock", extra=None):
    argv = ["--benchmark", benchmark, "--tasks", tasks, "--harness", harness]
    argv.extend(extra or [])
    return argv


class FakeBridge:
    """ok-preflight bridge whose answers are derived from the prompt."""

    def __init__(self, answer_map: dict[str, str] | None = None, error=None):
        self.answer_map = answer_map or {}
        self.error = error
        self.prompts: list[str] = []
        self.torn_down = 0

    def preflight(self):
        return opencode_bridge.ProbeReport(
            ok=True,
            phases={"health": {"ok": True, "detail": "GET /health -> 200"}},
            session_id="sess-fake",
        )

    def run_task(self, prompt, *, timeout_s, seed, require_tool=False):
        self.prompts.append(prompt)
        if self.error is not None:
            return opencode_bridge.BridgeTrajectory(
                task_prompt=prompt, seed=seed, error=self.error, duration_s=0.1
            )
        answer = next(
            (value for key, value in self.answer_map.items() if key in prompt),
            "FINAL ANSWER: no idea",
        )
        return opencode_bridge.BridgeTrajectory(
            task_prompt=prompt,
            seed=seed,
            final_result=answer,
            duration_s=0.2,
            usage={"cost_usd": 0.01},
            tool_calls=["get_financial_statements"],
        )

    def teardown(self):
        self.torn_down += 1
        return {"torn_down": True}


# --- Protocol conformance ---


@pytest.mark.parametrize("cls", [fqa.FinanceBenchAdapter, fqa.FinEvalAdapter])
def test_adapters_satisfy_protocol(cls) -> None:
    for harness in ("mock", "opencode", "pydantic"):
        adapter = cls(harness, bridge=object() if harness != "mock" else None)
        assert isinstance(adapter, HarnessAdapter)
        for method in ("setup", "run_task", "teardown", "report"):
            assert callable(getattr(adapter, method))


def test_unknown_harness_rejected() -> None:
    with pytest.raises(ValueError):
        fqa.FinanceBenchAdapter("bogus")


# --- Scorer unit tests: normalization, numeric tolerance, messy MCQ extraction ---


@pytest.mark.parametrize(
    ("prediction", "gold", "expected"),
    [
        ("$1577.00", "$1577.00", 1.0),  # exact
        ("  1,577  ", "$1577.00", 1.0),  # whitespace + digit-grouping commas
        ("$1,577.5", "$1577.00", 1.0),  # 0.03% off -> within 1% tolerance
        ("1577", "$1577.00", 1.0),  # bare number, numeric path
        ("1500", "$1577.00", 0.0),  # 4.9% off -> outside tolerance
        ("YES, because CAPEX ratio fell.", "Yes", 1.0),  # case-insensitive yes
        ("no", "No, the company is managing its CAPEX efficiently", 1.0),
        ("Yes", "No, the company is managing its CAPEX", 0.0),  # wrong verdict
        ("", "$1577.00", 0.0),  # empty prediction
        ("   ", "Data Center", 0.0),  # whitespace-only prediction
        ("some unrelated prose", "Data Center", 0.0),  # non-match
    ],
)
def test_grade_financebench_protocol(prediction, gold, expected) -> None:
    assert fqs.grade_financebench(prediction, gold) == expected


@pytest.mark.parametrize(
    ("messy", "expected"),
    [
        ("B", "B"),
        ("答案是B", "B"),
        ("The answer is C.", "C"),
        ("(D)", "D"),
        ("【A】", "A"),
        ("经过分析，正确答案是 A。", "A"),
        ("我选择 D", "D"),
        ("选项是c", "C"),
        ("答案：B", "B"),  # full-width colon
        ("无法确定", None),
        ("", None),
    ],
)
def test_extract_mcq_option_messy_outputs(messy, expected) -> None:
    assert fqs.extract_mcq_option(messy) == expected


def test_grade_fineval_matches_ground_truth() -> None:
    assert fqs.grade_fineval("答案是b", "B") == 1.0
    assert fqs.grade_fineval("答案是A", "B") == 0.0
    assert fqs.grade_fineval("不知道", "B") == 0.0


def test_extract_final_answer_marker_and_fallback() -> None:
    assert (
        fqs.extract_final_answer("x\nFINAL ANSWER: $5.00", "FINAL ANSWER:") == "$5.00"
    )
    assert (
        fqs.extract_final_answer("no marker\nlast line", "FINAL ANSWER:") == "last line"
    )
    assert fqs.extract_final_answer("", "FINAL ANSWER:") == ""


# --- Mock path: deterministic, schema-valid, scored by the real scorers ---


def _run_mock_adapter(cls, tasks=5, seeds=(1, 2, 3)) -> dict:
    adapter = cls("mock")
    adapter.setup({"harness": "mock", "benchmark": cls.benchmark_id})
    for spec in adapter.prepare_tasks(tasks):
        for seed in seeds:
            adapter.run_task({**spec, "seed": seed})
    adapter.teardown()
    return adapter.report()


@pytest.mark.parametrize("cls", [fqa.FinanceBenchAdapter, fqa.FinEvalAdapter])
def test_mock_report_schema_valid_with_accuracy_and_cost(cls) -> None:
    report_dict = _run_mock_adapter(cls)
    report.validate_report(report_dict)
    rows = report_dict["metrics"]
    assert [row["metric"] for row in rows] == ["accuracy"]
    assert rows[0]["benchmark"] == cls.benchmark_id
    assert rows[0]["seeds"] == [1, 2, 3]
    assert 0.0 < rows[0]["value"] < 1.0  # mix of pass+fail, not a constant
    assert report_dict["total_cost"]["currency"] == "USD"
    assert report_dict["total_cost"]["value"] > 0


@pytest.mark.parametrize("cls", [fqa.FinanceBenchAdapter, fqa.FinEvalAdapter])
def test_mock_rows_come_from_real_scorer(cls) -> None:
    adapter = cls("mock")
    adapter.setup({"harness": "mock", "benchmark": cls.benchmark_id})
    specs = adapter.prepare_tasks(5)
    results = [
        adapter.run_task({**spec, "seed": seed}) for spec in specs for seed in (1, 2)
    ]
    # misleading-success guard: every row carries the scored prediction+gold,
    # and re-grading the recorded pair reproduces the reported status.
    for result in results:
        prediction, gold = result.details["prediction"], result.details["gold"]
        if cls is fqa.FinanceBenchAdapter:
            score = fqs.grade_financebench(prediction, gold)
        else:
            score = fqs.grade_fineval(prediction, gold)
        assert score == result.score
        assert (result.status == "passed") == (score >= 0.5)
    assert {r.status for r in results} == {"passed", "failed"}


@pytest.mark.parametrize("cls", [fqa.FinanceBenchAdapter, fqa.FinEvalAdapter])
def test_mock_is_deterministic(cls) -> None:
    first, second = _run_mock_adapter(cls), _run_mock_adapter(cls)
    for key in ("metrics", "total_cost", "skip_markers"):
        assert first[key] == second[key], key


# --- Registry + CLI contract (malformed input included) ---


def test_registry_resolves_finance_benchmarks() -> None:
    assert {"financebench", "fineval"} <= set(run.BENCHMARK_REGISTRY)
    assert isinstance(
        run.BENCHMARK_REGISTRY["financebench"]("mock"), fqa.FinanceBenchAdapter
    )
    assert isinstance(run.BENCHMARK_REGISTRY["fineval"]("mock"), fqa.FinEvalAdapter)


@pytest.mark.parametrize("benchmark", ["financebench", "fineval"])
def test_cli_mock_smoke_writes_valid_report(tmp_path, benchmark) -> None:
    out = tmp_path / f"{benchmark}.json"
    code = run.main(_argv(benchmark, extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    assert any(row["metric"] == "accuracy" for row in report_dict["metrics"])
    assert report_dict["total_cost"]["currency"] == "USD"


def test_cli_unknown_benchmark_still_exits_2(capsys) -> None:
    code = run.main(["--benchmark", "nope", "--tasks", "1", "--harness", "mock"])
    assert code == run.EXIT_USAGE
    assert "unknown benchmark" in capsys.readouterr().err


@pytest.mark.parametrize("benchmark", ["financebench", "fineval"])
def test_cli_pydantic_placeholder_skip_marker(tmp_path, benchmark) -> None:
    out = tmp_path / f"{benchmark}_pydantic.json"
    code = run.main(
        _argv(benchmark, harness="pydantic", extra=["--report-out", str(out)])
    )
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "poc-not-wired" in reasons


def test_budget_cap_zero_aborts_with_skip_marker(tmp_path, monkeypatch) -> None:
    spec = json.loads((PKG_DIR / "parity_spec.json").read_text(encoding="utf-8"))
    spec["budgets"]["suite_cost_cap_usd_per_harness"] = 0.0
    monkeypatch.setattr(run.parity, "load_spec", lambda *a, **k: spec)
    out = tmp_path / "budget.json"
    code = run.main(_argv("financebench", extra=["--report-out", str(out)]))
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []
    reasons = " ".join(m["reason"] for m in report_dict["skip_markers"])
    assert "cost_cap_exceeded" in reasons


# --- HF-unreachable degradation (injected at the loader seam) ---


@pytest.mark.parametrize(
    ("benchmark", "loader", "exc"),
    [
        (
            "financebench",
            "load_financebench_records",
            fqs.FinanceQADataUnavailable("mirror down"),
        ),
        ("fineval", "load_fineval_records", OSError("connection refused")),
    ],
)
def test_dataset_unavailable_degrades_to_skip_marker(
    tmp_path, monkeypatch, benchmark, loader, exc
) -> None:
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(fqa, loader, boom)
    monkeypatch.setattr(run, "build_bridge", lambda env: FakeBridge())
    out = tmp_path / f"{benchmark}_nodata.json"
    code = run.main(
        _argv(benchmark, harness="opencode", extra=["--report-out", str(out)])
    )
    assert code == run.EXIT_OK  # degrade-and-disclose, never a crash
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    report.validate_report(report_dict)
    assert report_dict["metrics"] == []
    marker = report_dict["skip_markers"][0]
    assert f"{benchmark}-data-unavailable" in marker["reason"]
    assert "HF_ENDPOINT" in marker["reason"]
    assert marker["decision"] == "excluded_from_adjudication"


# --- opencode wiring: prompt -> bridge -> extraction -> real scorer ---


def test_opencode_wiring_scores_bridge_answers(tmp_path, monkeypatch) -> None:
    records = [
        {
            "id": "financebench_id_00001",
            "question": "What was FY2023 revenue?",
            "answer": "$100.00",
            "company": "FakeCo",
            "doc_name": "FakeCo_2023_10K",
        },
        {
            "id": "financebench_id_00002",
            "question": "Is the firm cyclical?",
            "answer": "Yes, exposure to cycles.",
            "company": "FakeCo",
            "doc_name": "FakeCo_2023_10K",
        },
    ]
    monkeypatch.setattr(fqa, "load_financebench_records", lambda data_dir: records)
    bridge = FakeBridge(
        answer_map={
            "What was FY2023 revenue?": "Reasoning...\nFINAL ANSWER: $100.00",
            "Is the firm cyclical?": "FINAL ANSWER: No, it is not.",
        }
    )
    monkeypatch.setattr(run, "build_bridge", lambda env: bridge)
    out = tmp_path / "opencode.json"
    code = run.main(
        _argv(
            "financebench",
            tasks="2",
            harness="opencode",
            extra=["--seed", "7", "--report-out", str(out)],
        )
    )
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["provenance"]["harness_id"] == "opencode_omo_baseline"
    row = report_dict["metrics"][0]
    assert row["metric"] == "accuracy" and row["value"] == 0.5  # 1 of 2 correct
    assert bridge.torn_down == 1
    assert all("VT MCP" in prompt for prompt in bridge.prompts)


def test_opencode_bridge_error_is_error_row_not_crash(tmp_path, monkeypatch) -> None:
    records = [
        {
            "id": "dev:accounting:0",
            "question": "题",
            "A": "a",
            "B": "b",
            "C": "c",
            "D": "d",
            "answer": "B",
        }
    ]
    monkeypatch.setattr(fqa, "load_fineval_subset_ids", lambda: ["dev:accounting:0"])
    monkeypatch.setattr(
        fqa, "load_fineval_records", lambda data_dir: {"dev:accounting:0": records[0]}
    )
    bridge = FakeBridge(error=opencode_bridge.BridgeError("transport", "boom"))
    monkeypatch.setattr(run, "build_bridge", lambda env: bridge)
    out = tmp_path / "bridge_error.json"
    code = run.main(
        _argv(
            "fineval",
            tasks="1",
            harness="opencode",
            extra=["--seed", "1", "--report-out", str(out)],
        )
    )
    assert code == run.EXIT_OK
    report_dict = json.loads(out.read_text(encoding="utf-8"))
    assert report_dict["metrics"] == []  # error rows carry no accuracy denominator
    assert report_dict["skip_markers"] == []


# --- Subset artifact validity + redraw determinism (stale_state guard) ---


def test_subset_artifact_is_valid() -> None:
    artifact = json.loads(SUBSET_PATH.read_text(encoding="utf-8"))
    ids = artifact["question_ids"]
    assert artifact["n"] == 500 and len(ids) == 500
    assert len(set(ids)) == 500
    assert artifact["sampling_seed"] == 20260823
    assert artifact["source_dataset"] == "SUFE-AIFLM-Lab/FinEval"
    assert "random.Random(20260823)" in artifact["draw_method"]
    pattern = re.compile(r"^(dev|val):[a-z_0-9]+:\d+$")
    assert all(pattern.match(qid) for qid in ids)


def test_subset_draw_is_deterministic() -> None:
    pool = [f"val:subject:{i}" for i in range(1321)]
    first = fqs.draw_subset(pool, 500, 20260823)
    second = fqs.draw_subset(pool, 500, 20260823)
    assert first == second
    assert fqs.draw_subset(pool, 500, 99) != first  # seed actually drives the draw


def test_subset_matches_live_redraw_when_cache_present() -> None:
    try:
        records = fqs.load_fineval_records(fqs.default_data_dir())
    except fqs.FinanceQADataUnavailable:
        pytest.skip("dataset cache absent; run scripts/fetch_finance_qa.py first")
    artifact = json.loads(SUBSET_PATH.read_text(encoding="utf-8"))
    redraw = fqs.draw_subset(list(records), 500, 20260823)
    assert redraw == artifact["question_ids"]
