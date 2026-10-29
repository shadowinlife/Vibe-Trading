"""FinanceBench-150 and FinEval HarnessAdapters with deterministic scoring.

Citations: FinanceBench -- Islam et al., "FinanceBench: A Public Benchmark
for Financial Question Answering", arXiv:2311.11944; HF dataset
``PatronusAI/financebench`` (150 open-source cases: question, gold answer,
evidence). FinEval -- Zhang et al., "FinEval: A Chinese Financial Domain
Knowledge Evaluation Benchmark for Large Language Models", arXiv:2308.09975;
HF dataset ``SUFE-AIFLM-Lab/FinEval`` (4,661 Chinese finance MCQs; the test
split's labels are withheld by the authors, so offline grading uses the
answerable dev+val pool and the committed ``artifacts/fineval_subset.json``
-- n=500, sampling_seed=20260823, parity spec ``subsets.fineval``).

Grading protocol (deterministic proxy for the official scoring; the
FinanceBench paper grades open-book answers by human review plus LLM
judging, FinEval's official metric is MCQ accuracy). Scorers live in
``finance_qa_scoring.py``:

* ``grade_financebench``: normalize (casefold, collapse whitespace, drop
  currency symbols and digit-grouping commas, strip trailing periods), then
  (1) exact normalized match; (2) yes/no match when the gold answer leads
  with yes/no -- the prediction must lead the same way; (3) numeric match
  for short gold answers (<= 6 tokens): last number on each side within 1%
  relative tolerance. Anything else scores 0 -- deliberately conservative
  (no false positives) relative to the paper's judging.
* ``grade_fineval``: extract one A/B/C/D option from the model output
  (explicit "答案/answer is" markers, bracketed letters, then the last
  standalone letter) and compare to the ground truth; nothing extractable
  scores 0.

Harness modes (same contract as ``tau2_adapter``): ``mock`` runs
deterministic synthetic tasks THROUGH THE REAL SCORERS (no dataset, no
network); ``opencode`` sends the question through ``OpenCodeBridge`` with a
prompt instructing the baseline to answer via the VT MCP finance tools and
grades the final answer (evidence strings are NOT injected, so retrieval is
part of what the harness under test does); ``pydantic`` raises the
NotImplemented placeholder (todo 14 wires it) -> ``poc-not-wired`` skip
marker via ``run.py``. Dataset loading is lazy (opencode path only); a
missing cache degrades to a skip marker, never a crash. Research-only.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any

from src.evals.harness_bench.adapter import TaskResult
from src.evals.harness_bench.adapters.finance_qa_scoring import (
    FinanceQADataUnavailable,
    default_data_dir,
    extract_final_answer,
    grade_financebench,
    grade_fineval,
    load_financebench_records,
    load_fineval_records,
    load_fineval_subset_ids,
)
from src.evals.harness_bench.report import build_report

POC_NOT_WIRED = "poc-not-wired: pydantic-ai wiring for {benchmark} lands in todo 14"
_HARNESS_IDS = {
    "mock": "mock",
    "opencode": "opencode_omo_baseline",
    "pydantic": "pydantic_ai_poc",
}


def _unit_hash(*parts: Any) -> float:
    digest = zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))
    return (digest % 1000) / 1000.0


class _FinanceQAAdapterBase:
    """Shared HarnessAdapter plumbing; subclasses bind benchmark + grading."""

    benchmark_id: str = ""
    answer_marker: str = ""

    def __init__(self, harness: str, bridge: Any = None, data_dir: Path | None = None):
        if harness not in _HARNESS_IDS:
            raise ValueError(f"unknown harness {harness!r}")
        self.harness = harness
        self.harness_id = _HARNESS_IDS[harness]
        self._bridge = bridge
        self._data_dir = Path(data_dir) if data_dir else default_data_dir()
        self._config: dict[str, Any] = {}
        self._results: list[TaskResult] = []
        self._skip_markers: list[dict[str, Any]] = []
        self._setup_done = False

    @property
    def bridge(self) -> Any:
        return self._bridge

    # -- HarnessAdapter protocol ------------------------------------------- #

    def setup(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self._results = []
        self._skip_markers = []
        self._setup_done = True
        if self.harness == "opencode" and self._bridge is None:
            raise RuntimeError("opencode harness requires a bridge at construction")

    def run_task(self, task: dict[str, Any]) -> TaskResult:
        if not self._setup_done:
            raise RuntimeError(f"{type(self).__name__}.run_task called before setup()")
        if self.harness == "pydantic":
            raise NotImplementedError(POC_NOT_WIRED.format(benchmark=self.benchmark_id))
        runner = self._run_mock if self.harness == "mock" else self._run_opencode
        result = runner(task)
        self._results.append(result)
        return result

    def teardown(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.teardown()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def report(self) -> dict[str, Any]:
        if not self._setup_done:
            raise RuntimeError(f"{type(self).__name__}.report called before setup()")
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        metrics: list[dict[str, Any]] = []
        if not any(m["benchmark"] == self.benchmark_id for m in self._skip_markers):
            attempted = [r for r in self._results if r.status in ("passed", "failed")]
            if attempted:
                passed = sum(1 for r in attempted if r.status == "passed")
                metrics.append(
                    {
                        "benchmark": self.benchmark_id,
                        "metric": "accuracy",
                        "value": round(passed / len(attempted), 6),
                        "seeds": seeds,
                    }
                )
        return build_report(
            harness_id=self.harness_id,
            metrics=metrics,
            total_cost_usd=round(sum(r.cost_usd for r in self._results), 6),
            skip_markers=list(self._skip_markers),
            seeds={self.benchmark_id: seeds} if seeds else {},
            git_commit=self._config.get("git_commit"),
        )

    # -- run.py integration ------------------------------------------------ #

    def add_skip_marker(self, benchmark: str, reason: str, decision: str) -> None:
        self._skip_markers.append(
            {
                "benchmark": benchmark,
                "reason": reason,
                "degraded": True,
                "decision": decision,
            }
        )
        self._results = [r for r in self._results if r.benchmark != benchmark]

    def prepare_tasks(self, n: int) -> list[dict[str, Any]]:
        """Task specs (seed-free); run.py expands them across the seed list."""
        if self.harness == "opencode":
            return self._prepare_opencode_tasks(n)
        return [self._mock_task(index) for index in range(n)]

    def _degrade(self, problem: str) -> list[dict[str, Any]]:
        self.add_skip_marker(
            self.benchmark_id,
            f"{self.benchmark_id}-data-unavailable: {problem}; run "
            "scripts/fetch_finance_qa.py (HF mirror via HF_ENDPOINT)",
            "excluded_from_adjudication",
        )
        return []

    def _result(self, task, status, score, cost, duration, seed, details) -> TaskResult:
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=self.benchmark_id,
            status=status,
            score=score,
            cost_usd=cost,
            duration_seconds=duration,
            seed=int(seed) if seed is not None else None,
            details=details,
        )

    def _run_mock(self, task: dict[str, Any]) -> TaskResult:
        seed = task.get("seed")
        unit = _unit_hash(task["task_id"], seed)
        prediction = self._mock_prediction(task, unit)
        score = self._grade(prediction, task)
        return self._result(
            task,
            "passed" if score >= 0.5 else "failed",
            score,
            round(0.001 + unit * 0.01, 6),
            round(0.1 + unit * 0.5, 6),
            seed,
            {"prediction": prediction[:300], "gold": str(task["gold_answer"])[:300]},
        )

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        seed = int(task.get("seed", 0))
        timeout_s = int(self._config.get("task_timeout_s", 900))
        trajectory = self._bridge.run_task(
            self._prompt(task), timeout_s=timeout_s, seed=seed
        )
        cost = float(trajectory.usage.get("cost_usd", 0.0))
        if trajectory.error is not None:
            return self._result(
                task,
                "error",
                None,
                cost,
                trajectory.duration_s,
                seed,
                {"bridge_error": trajectory.error.as_dict()},
            )
        prediction = extract_final_answer(
            trajectory.final_result or "", self.answer_marker
        )
        score = self._grade(prediction, task)
        return self._result(
            task,
            "passed" if score >= 0.5 else "failed",
            score,
            cost,
            trajectory.duration_s,
            seed,
            {"prediction": prediction[:300], "tool_calls": trajectory.tool_calls[:10]},
        )

    # -- subclass hooks ---------------------------------------------------- #

    def _prepare_opencode_tasks(self, n: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _mock_task(self, index: int) -> dict[str, Any]:
        raise NotImplementedError

    def _mock_prediction(self, task: dict[str, Any], unit: float) -> str:
        raise NotImplementedError

    def _grade(self, prediction: str, task: dict[str, Any]) -> float:
        raise NotImplementedError

    def _prompt(self, task: dict[str, Any]) -> str:
        raise NotImplementedError


# --- Concrete adapters ---


class FinanceBenchAdapter(_FinanceQAAdapterBase):
    """FinanceBench-150 open-book QA; deterministic grading proxy (module doc)."""

    benchmark_id = "financebench"
    answer_marker = "FINAL ANSWER:"

    def _prepare_opencode_tasks(self, n: int) -> list[dict[str, Any]]:
        try:
            records = load_financebench_records(self._data_dir)
        except (
            FinanceQADataUnavailable,
            OSError,
            json.JSONDecodeError,
            KeyError,
        ) as exc:
            return self._degrade(str(exc))
        return [
            {
                "task_id": f"financebench-{record['id']}",
                "benchmark": self.benchmark_id,
                "question": record["question"],
                "gold_answer": record["answer"],
                "company": record["company"],
                "doc_name": record["doc_name"],
            }
            for record in records[: max(1, min(n, len(records)))]
        ]

    def _mock_task(self, index: int) -> dict[str, Any]:
        company = ("AlphaCorp", "BetaInc", "GammaLtd", "DeltaCo", "EpsilonSA")[
            index % 5
        ]
        return {
            "task_id": f"financebench-synthetic-{index}",
            "benchmark": self.benchmark_id,
            "question": f"What was {company}'s FY2023 revenue in USD millions?",
            "gold_answer": f"${1000 + index * 137}.00",
            "company": company,
            "doc_name": f"{company}_2023_10K",
        }

    def _mock_prediction(self, task: dict[str, Any], unit: float) -> str:
        if unit < 0.3:
            return "$9,999.00"  # deterministic wrong figure -> scored failed
        amount = str(task["gold_answer"]).replace("$", "")
        variant = int(unit * 1000) % 3  # messy-but-correct spellings
        if variant == 0:
            return f"  ${amount} "  # whitespace + exact symbol
        if variant == 1:
            return amount  # bare number -> numeric-tolerance path
        return f"The revenue was {amount} USD millions."  # short prose + number

    def _grade(self, prediction: str, task: dict[str, Any]) -> float:
        return grade_financebench(prediction, str(task["gold_answer"]))

    def _prompt(self, task: dict[str, Any]) -> str:
        return (
            "You are completing FinanceBench (arXiv:2311.11944), an open-book "
            "financial QA benchmark. Use the VT MCP finance tools available in "
            "this session (get_financial_statements, get_sec_filings, "
            "get_market_data, get_stock_profile) to retrieve the company's "
            "filing data first, then answer from it. Do not fabricate figures. "
            f"Company: {task.get('company', '')}. Source document: "
            f"{task.get('doc_name', '')}.\nQuestion: {task['question']}\n"
            f"End your reply with exactly: {self.answer_marker} <concise answer>"
        )


class FinEvalAdapter(_FinanceQAAdapterBase):
    """FinEval MCQ accuracy on the committed 500-question subset."""

    benchmark_id = "fineval"
    answer_marker = "答案:"

    def _prepare_opencode_tasks(self, n: int) -> list[dict[str, Any]]:
        try:
            subset_ids = load_fineval_subset_ids()
            records = load_fineval_records(self._data_dir)
        except (
            FinanceQADataUnavailable,
            OSError,
            json.JSONDecodeError,
            KeyError,
        ) as exc:
            return self._degrade(str(exc))
        specs: list[dict[str, Any]] = []
        for question_id in subset_ids:
            if len(specs) >= max(1, n):
                break
            record = records.get(question_id)
            if record is not None:
                specs.append(
                    {
                        "task_id": f"fineval-{question_id}",
                        "benchmark": self.benchmark_id,
                        "question": record["question"],
                        "options": {opt: record[opt] for opt in "ABCD"},
                        "gold_answer": record["answer"],
                    }
                )
        return specs or self._degrade("subset ids did not resolve against the cache")

    def _mock_task(self, index: int) -> dict[str, Any]:
        return {
            "task_id": f"fineval-synthetic-{index}",
            "benchmark": self.benchmark_id,
            "question": f"模拟题{index}: 下列关于金融风险的说法哪项正确?",
            "options": {
                "A": "利率上升必然导致债券价格上升",
                "B": "分散化可以降低非系统性风险",
                "C": "信用风险与市场风险完全无关",
                "D": "流动性风险无法度量",
            },
            "gold_answer": "ABCD"[index % 4],
        }

    def _mock_prediction(self, task: dict[str, Any], unit: float) -> str:
        gold = str(task["gold_answer"])
        if unit < 0.3:
            return f"答案是{'ABCD'.replace(gold, '')[0]}"  # wrong option -> failed
        variant = int(unit * 1000) % 4  # messy-but-correct spellings
        if variant == 0:
            return f"答案是{gold}"
        if variant == 1:
            return f"The answer is {gold}."
        if variant == 2:
            return f"({gold})"
        return f"经过分析，正确答案是 {gold}。"

    def _grade(self, prediction: str, task: dict[str, Any]) -> float:
        return grade_fineval(prediction, str(task["gold_answer"]))

    def _prompt(self, task: dict[str, Any]) -> str:
        options = task.get("options", {})
        lines = [f"{opt}. {options.get(opt, '')}" for opt in "ABCD"]
        return (
            "你正在完成 FinEval 金融知识选择题评测 (arXiv:2308.09975)。请作答以下"
            "单选题；如需要，可使用本会话中的 VT MCP 金融数据工具 (如 "
            "get_financial_statements、get_research_reports) 辅助判断，但不得"
            f"编造事实。\n题目: {task['question']}\n"
            + "\n".join(lines)
            + f"\n最后一行请只输出: {self.answer_marker} X (X 为 A/B/C/D 之一)"
        )
