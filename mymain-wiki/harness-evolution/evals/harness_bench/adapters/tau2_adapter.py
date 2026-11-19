"""tau2-bench HarnessAdapter (retail + airline, pass^k metric).

Implements ``HarnessAdapter`` for the tau2 benchmark under all three harness
modes:

* ``mock``     -- deterministic MockAdapter-style run (CRC32-derived, no tau2
                  import) so the CLI and report pipeline stay testable
                  offline.
* ``opencode`` -- each tau2 task is driven through ``OpenCodeBridge.run_task``
                  and scored with tau2's own evaluator; pass^k is computed
                  over the parity seeds.
* ``pydantic`` -- documented NotImplemented placeholder (todo 14 wires it);
                  ``run.py`` converts the raise into skip marker
                  ``poc-not-wired``.

tau2 API relied on (installed package ``tau2`` v1.0.1 in ``.venv-eval``):
``tau2.registry.registry.get_tasks_loader`` (tau2/registry.py),
``tau2.metrics.agent_metrics.pass_hat_k`` / ``is_successful``
(tau2/metrics/agent_metrics.py), ``tau2.evaluator.evaluator.
evaluate_simulation`` + ``EvaluationType`` (tau2/evaluator/evaluator.py),
``tau2.data_model.simulation.SimulationRun`` / ``TerminationReason``,
``tau2.data_model.message.AssistantMessage.text`` / ``UserMessage.text``.
Task data (retail/airline tasks.json+db.json) is an external download
(``TAU2_DATA_DIR``); loading failures degrade to a skip marker, never crash.
"""

from __future__ import annotations

import json
import math
import zlib
from typing import Any

from src.evals.harness_bench.adapter import TaskResult
from src.evals.harness_bench.report import build_report

TAU2_DOMAINS = ("retail", "airline")
POC_NOT_WIRED = (
    "poc-not-wired: the pydantic-ai harness wiring for tau2 lands in todo 14"
)


class Tau2DataUnavailable(RuntimeError):
    """tau2 task data missing (TAU2_DATA_DIR) or tau2 not importable."""


def pass_hat_k(num_trials: int, success_count: int, k: int) -> float:
    """tau2's pass^k (arXiv:2406.12045): C(success,k) / C(trials,k)."""
    if num_trials < k:
        raise ValueError(f"num_trials {num_trials} < k {k}")
    if success_count < k:
        return 0.0
    return math.comb(success_count, k) / math.comb(num_trials, k)


def is_successful(reward: float) -> bool:
    """tau2 success rule (agent_metrics.is_successful): reward ~= 1.0."""
    return (1 - 1e-6) <= reward <= (1 + 1e-6)


def _unit_hash(*parts: Any) -> float:
    digest = zlib.crc32(":".join(str(p) for p in parts).encode("utf-8"))
    return (digest % 1000) / 1000.0


def _load_tau2_tasks(domain: str, limit: int) -> list[Any]:
    try:
        from tau2.registry import registry
    except ImportError as exc:  # pragma: no cover - depends on venv
        raise Tau2DataUnavailable(f"tau2 not importable: {exc}") from exc
    try:
        loader = registry.get_tasks_loader(domain)
        tasks = loader("base")
    except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
        raise Tau2DataUnavailable(
            f"tau2 {domain} task data unavailable (set TAU2_DATA_DIR): {exc}"
        ) from exc
    return list(tasks)[:limit]


class Tau2Adapter:
    """HarnessAdapter for tau2; see module docstring for the three modes."""

    def __init__(self, harness: str, bridge: Any = None):
        if harness not in ("mock", "opencode", "pydantic"):
            raise ValueError(f"unknown harness {harness!r}")
        self.harness = harness
        self.harness_id = {
            "mock": "mock",
            "opencode": "opencode_omo_baseline",
            "pydantic": "pydantic_ai_poc",
        }[harness]
        self._bridge = bridge
        self._config: dict[str, Any] = {}
        self._results: list[TaskResult] = []
        self._skip_markers: list[dict[str, Any]] = []
        self._setup_done = False

    @property
    def bridge(self) -> Any:
        """The attached OpenCodeBridge or None (run.py's gate checks this)."""
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
            raise RuntimeError("Tau2Adapter.run_task called before setup()")
        if self.harness == "pydantic":
            raise NotImplementedError(POC_NOT_WIRED)
        if self.harness == "mock":
            result = self._run_mock(task)
        else:
            result = self._run_opencode(task)
        self._results.append(result)
        return result

    def teardown(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            try:
                bridge.teardown()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def report(self) -> dict[str, Any]:
        if not self._setup_done:
            raise RuntimeError("Tau2Adapter.report called before setup()")
        metrics = self._pass_k_rows()
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        total_cost = round(sum(r.cost_usd for r in self._results), 6)
        return build_report(
            harness_id=self.harness_id,
            metrics=metrics,
            total_cost_usd=total_cost,
            skip_markers=list(self._skip_markers),
            seeds={"tau2": seeds} if seeds else {},
            git_commit=self._config.get("git_commit"),
            cost_note=self._config.get("cost_note", ""),
        )

    # -- run.py integration ------------------------------------------------ #

    def prepare_tasks(self, n: int) -> list[dict[str, Any]]:
        """Task specs (seed-free); run.py expands them across the seed list."""
        specs: list[dict[str, Any]] = []
        if self.harness == "opencode":
            per_domain = math.ceil(n / len(TAU2_DOMAINS))
            pools = [
                (domain, _load_tau2_tasks(domain, per_domain))
                for domain in TAU2_DOMAINS
            ]
            picked: list[tuple[str, Any]] = []
            for index in range(n):
                domain, pool = pools[index % len(pools)]
                if not pool:
                    continue
                picked.append((domain, pool.pop(0)))
            for index, (domain, tau2_task) in enumerate(picked):
                specs.append(
                    {
                        "task_id": f"tau2-{domain}-{tau2_task.id}",
                        "benchmark": "tau2",
                        "domain": domain,
                        "_tau2_task": tau2_task,
                        "index": index,
                    }
                )
            return specs
        for index in range(n):
            domain = TAU2_DOMAINS[index % len(TAU2_DOMAINS)]
            specs.append(
                {
                    "task_id": f"tau2-{domain}-synthetic-{index}",
                    "benchmark": "tau2",
                    "domain": domain,
                    "index": index,
                }
            )
        return specs

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

    # -- internals --------------------------------------------------------- #

    def _run_mock(self, task: dict[str, Any]) -> TaskResult:
        seed = task.get("seed")
        unit = _unit_hash(task["task_id"], task.get("domain", ""), seed)
        status = "passed" if unit >= 0.3 else "failed"
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark="tau2",
            status=status,
            score=round(unit, 6),
            cost_usd=round(0.001 + unit * 0.01, 6),
            duration_seconds=round(0.1 + unit * 0.5, 6),
            seed=int(seed) if seed is not None else None,
            details={"mock_unit": unit, "domain": task.get("domain")},
        )

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        seed = int(task.get("seed", 0))
        timeout_s = int(self._config.get("task_timeout_s", 900))
        prompt = self._task_prompt(task)
        trajectory = self._bridge.run_task(prompt, timeout_s=timeout_s, seed=seed)
        if trajectory.error is not None:
            return TaskResult(
                task_id=str(task["task_id"]),
                benchmark="tau2",
                status="error",
                score=None,
                cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
                duration_seconds=trajectory.duration_s,
                seed=seed,
                details={"bridge_error": trajectory.error.as_dict()},
            )
        reward, eval_detail = self._score_with_tau2(task, trajectory)
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark="tau2",
            status="passed" if is_successful(reward) else "failed",
            score=round(reward, 6),
            cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
            duration_seconds=trajectory.duration_s,
            seed=seed,
            details={
                "domain": task.get("domain"),
                "tau2_evaluator": eval_detail,
                "tool_calls": trajectory.tool_calls[:10],
            },
        )

    def _task_prompt(self, task: dict[str, Any]) -> str:
        tau2_task = task.get("_tau2_task")
        try:
            payload = json.dumps(tau2_task.model_dump(), default=str)[:6000]
        except (AttributeError, TypeError, ValueError):
            payload = str(tau2_task)[:6000]
        return (
            f"You are a customer-service agent for the '{task.get('domain')}' "
            "domain of the tau2 benchmark. Solve the user's request by following "
            "the domain policy strictly, using the available tools; do not "
            "fabricate data. The user will converse with you turn by turn.\n"
            f"tau2 task object (JSON):\n{payload}"
        )

    def _score_with_tau2(self, task: dict[str, Any], trajectory: Any) -> tuple:
        """Score a bridge trajectory with tau2's evaluator (best effort).

        Bridge trajectories carry no tau2 environment tool calls, so only the
        replayable checks can pass; any construction/evaluation error yields
        reward 0.0 with disclosure instead of a crash.
        """
        try:
            from tau2.data_model.message import AssistantMessage, UserMessage
            from tau2.data_model.simulation import SimulationRun, TerminationReason
            from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation

            messages = [
                UserMessage.text(trajectory.task_prompt[:4000]),
                AssistantMessage.text(trajectory.final_result or ""),
            ]
            simulation = SimulationRun(
                id=f"bridge-{task['task_id']}-{trajectory.seed}",
                task_id=str(task["task_id"]),
                start_time="1970-01-01T00:00:00",
                end_time="1970-01-01T00:00:00",
                duration=trajectory.duration_s,
                termination_reason=TerminationReason.AGENT_STOP,
                messages=messages,
                seed=trajectory.seed,
            )
            reward_info = evaluate_simulation(
                simulation=simulation,
                task=task["_tau2_task"],
                evaluation_type=EvaluationType.ENV,
                solo_mode=False,
                domain=str(task.get("domain")),
                strict_replay=False,
            )
            return float(reward_info.reward), "evaluated"
        except Exception as exc:  # noqa: BLE001 - honest zero, never a crash
            return 0.0, f"not_applied: {str(exc)[:300]}"

    def _pass_k_rows(self) -> list[dict[str, Any]]:
        if self._skip_markers and any(
            m["benchmark"] == "tau2" for m in self._skip_markers
        ):
            return []
        by_task: dict[str, list[TaskResult]] = {}
        for result in self._results:
            if result.status in ("passed", "failed"):
                by_task.setdefault(result.task_id, []).append(result)
        if not by_task:
            return []
        max_k = min(len(rows) for rows in by_task.values())
        rows: list[dict[str, Any]] = []
        seeds = sorted({r.seed for r in self._results if r.seed is not None})
        for k in range(1, max_k + 1):
            values = [
                pass_hat_k(len(rows), sum(r.status == "passed" for r in rows), k)
                for rows in by_task.values()
            ]
            rows.append(
                {
                    "benchmark": "tau2",
                    "metric": f"pass^{k}",
                    "value": round(sum(values) / len(values), 6),
                    "seeds": seeds,
                }
            )
        return rows
