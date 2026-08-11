"""SWE-bench Verified + terminal-bench 2.0 HarnessAdapters (todo 7).

Modes (mirrors ``tau2_adapter``):

* ``mock``     -- deterministic CRC32-derived results, no Docker/network, so
                  CLI acceptance stays offline. SWE task ids come from the
                  committed subset artifact; terminal-bench uses synthetic ids.
* ``opencode`` -- SWE tasks are driven per-task through
                  ``OpenCodeBridge.run_task`` (repo scaffold + problem
                  statement as prompt; the patch is extracted from the final
                  result). terminal-bench tasks run through harbor inside
                  Docker with the opencode harness as the agent; a bridge
                  session inside the task container is the fallback seam.
* ``pydantic`` -- NotImplemented placeholder; ``run.py`` converts the raise
                  into the ``poc-not-wired`` skip marker.

harbor 0.6.1 entry points used (installed in ``.venv-eval``, cited from the
package; see ``TerminalBenchAdapter._run_harbor_task`` for the wiring):
``harbor.cli.main:app`` (typer CLI behind the ``harbor``/``hb`` console
scripts, ``harbor job start -c <config>``), ``harbor/models/job/config.py:
JobConfig``, ``harbor/models/trial/config.py:AgentConfig(name="opencode")``
-> ``harbor/agents/installed/opencode.py:OpenCode`` registered in
``harbor/agents/factory.py:AgentFactory`` under ``harbor/models/agent/
name.py:AgentName.OPENCODE`` (native opencode agent plugin: the harness runs
INSIDE the task container, no custom adapter needed), and
``harbor/verifier/verifier.py:Verifier`` (the built-in task checks that grade
a trial; ``harbor/job.py:Job`` writes ``<jobs_dir>/<job>/result.json``).

Official validators retained: SWE grading uses the dataset's
PASS_TO_PASS/FAIL_TO_PASS semantics — resolved iff, after applying the
agent's patch to the repo scaffold, every FAIL_TO_PASS test passes AND every
PASS_TO_PASS test still passes. Real validation runs only in real batches
(todo 8) behind ``SWEbenchVerifiedAdapter.apply_official_verifier``; until
then the seam returns an honest not-applied zero, never a fake pass.
terminal-bench grading stays on harbor's built-in task checks. Anti-exploit
spot checks (``spot_check.py``) run as a post-run hook over a trajectory
sample; pass/flag counts are disclosed via ``total_cost.note`` (the schema's
only free-form disclosure slot) plus per-task ``details["spot_check"]``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from src.evals.harness_bench.adapter import TaskResult
from src.evals.harness_bench.adapters.swe_terminal_support import (
    REPO_ROOT,
    SUBSET_PATH,
    SWE_DATA_FILE,
    SWE_DATASET,
    BenchmarkDataUnavailable,
    SweTerminalBase,
    _collect_key,
    _docker_available,
    extract_patch,
)


class SWEbenchVerifiedAdapter(SweTerminalBase):
    """HarnessAdapter for SWE-bench Verified; see module docstring."""

    benchmark_id = "swebench_verified"
    metric_name = "resolve_rate"

    def __init__(
        self,
        harness: str,
        bridge: Any = None,
        verifier_fn: Callable[[dict[str, Any], str], bool] | None = None,
        swe_loader: Callable[[str], dict[str, Any]] | None = None,
    ):
        super().__init__(harness, bridge)
        #: Official-validation seam: todo 8 injects the real verifier, tests
        #: inject a fake. Signature: (task_spec, patch) -> resolved bool.
        self._verifier_fn = verifier_fn
        #: Test seam replacing the HF dataset download (id -> record dict).
        self._swe_loader = swe_loader

    def prepare_tasks(self, n: int) -> list[dict[str, Any]]:
        ids = self._subset_ids()[: max(0, int(n))]
        if self.harness == "opencode":
            records = self._load_records(ids)
            return [
                {
                    "task_id": tid,
                    "benchmark": self.benchmark_id,
                    "_record": records[tid],
                }
                for tid in ids
            ]
        return [
            {
                "task_id": tid,
                "benchmark": self.benchmark_id,
                "problem": f"synthetic problem statement for {tid} (mock)",
            }
            for tid in ids
        ]

    def apply_official_verifier(
        self, task: dict[str, Any], patch: str | None
    ) -> tuple[bool, str]:
        """Official SWE grading seam (PASS_TO_PASS/FAIL_TO_PASS semantics):
        resolved iff, after applying ``patch`` to the task repo scaffold,
        every FAIL_TO_PASS test passes AND every PASS_TO_PASS test still
        passes. Real validation runs only in real batches (todo 8); until
        then this returns an honest not-applied zero, never a fake pass."""
        if patch is None:
            return False, "no_patch_extracted"
        if self._verifier_fn is not None:
            try:
                return bool(self._verifier_fn(task, patch)), "official_verifier_applied"
            except Exception as exc:  # noqa: BLE001 - honest zero, never a crash
                return False, f"verifier_error: {str(exc)[:200]}"
        return False, (
            "not_applied: official PASS_TO_PASS/FAIL_TO_PASS validation runs "
            "only in real batches (todo 8) behind this seam"
        )

    def _subset_ids(self) -> list[str]:
        try:
            spec = json.loads(SUBSET_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkDataUnavailable(
                f"swe-subset-artifact-unreadable: {exc}"
            ) from exc
        ids = [str(tid) for tid in spec.get("task_ids", [])]
        if not ids:
            raise BenchmarkDataUnavailable("swe-subset-artifact-empty: no task_ids")
        return ids

    def _load_records(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if self._swe_loader is not None:
            return {tid: self._swe_loader(tid) for tid in ids}
        try:
            import pandas as pd
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(SWE_DATASET, SWE_DATA_FILE, repo_type="dataset")
            frame = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001 - any fetch failure degrades
            raise BenchmarkDataUnavailable(
                "swe-task-data-unavailable: HF download failed (real batches set "
                "HF_ENDPOINT=https://hf-mirror.com when huggingface.co is "
                f"unreachable): {str(exc)[:200]}"
            ) from exc
        wanted = set(ids)
        records = {
            str(row["instance_id"]): dict(row)
            for _, row in frame.iterrows()
            if str(row.get("instance_id", "")) in wanted
        }
        missing = [tid for tid in ids if tid not in records]
        if missing:
            raise BenchmarkDataUnavailable(
                f"swe-task-data-unavailable: {len(missing)} subset ids missing "
                f"from the dataset revision, e.g. {missing[0]}"
            )
        return records

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        seed = int(task.get("seed", 0))
        timeout_s = int(self._config.get("task_timeout_s", 900))
        trajectory = self.bridge.run_task(
            self._task_prompt(task), timeout_s=timeout_s, seed=seed
        )
        if trajectory.error is not None:
            return TaskResult(
                task_id=str(task["task_id"]),
                benchmark=self.benchmark_id,
                status="error",
                cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
                duration_seconds=trajectory.duration_s,
                seed=seed,
                details={"bridge_error": trajectory.error.as_dict()},
            )
        patch = extract_patch(trajectory.final_result or "")
        resolved, note = self.apply_official_verifier(task, patch)
        self._record_trajectory(task["task_id"], [trajectory.final_result or ""])
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=self.benchmark_id,
            status="passed" if resolved else "failed",
            score=1.0 if resolved else 0.0,
            cost_usd=float(trajectory.usage.get("cost_usd", 0.0)),
            duration_seconds=trajectory.duration_s,
            seed=seed,
            details={
                "grading": note,
                "patch_chars": len(patch or ""),
                "tool_calls": trajectory.tool_calls[:10],
            },
        )

    def _task_prompt(self, task: dict[str, Any]) -> str:
        record = task.get("_record") or {}
        problem = str(record.get("problem_statement") or task.get("problem") or "")
        repo = str(record.get("repo") or "the task repository")
        return (
            f"You are solving SWE-bench Verified task {task['task_id']} in {repo}. "
            "A scaffold of the repository is available in the workspace; make the "
            "minimal code change that fixes the problem below. Do not modify tests "
            "or grading infrastructure. When done, output your complete patch as a "
            f"single unified diff inside a ```diff fence.\nProblem statement:\n{problem[:6000]}"
        )


class TerminalBenchAdapter(SweTerminalBase):
    """HarnessAdapter for terminal-bench 2.0 (Harbor); see module docstring."""

    benchmark_id = "terminal-bench"
    metric_name = "task_success_rate"

    def __init__(
        self,
        harness: str,
        bridge: Any = None,
        docker_probe: Callable[[], bool] | None = None,
        harbor_bin: str | None = None,
    ):
        super().__init__(harness, bridge)
        self._docker_probe = docker_probe or _docker_available
        self._harbor_bin = (
            harbor_bin
            or os.environ.get("HARNESS_BENCH_HARBOR_BIN")
            or str(REPO_ROOT / ".venv-eval" / "bin" / "harbor")
        )

    def prepare_tasks(self, n: int) -> list[dict[str, Any]]:
        if self.harness == "opencode":
            if not self._docker_probe():
                raise BenchmarkDataUnavailable(
                    "docker-unavailable: bounded `docker info` probe failed; "
                    "terminal-bench tasks run in Docker via harbor, so the "
                    "batch cannot start"
                )
            return [
                {
                    "task_id": f"terminal-bench-{name}",
                    "benchmark": self.benchmark_id,
                    "_harbor_task": name,
                }
                for name in self._discover_tasks(int(n))
            ]
        return [
            {
                "task_id": f"terminal-bench-synthetic-{index}",
                "benchmark": self.benchmark_id,
                "index": index,
            }
            for index in range(max(0, int(n)))
        ]

    def _discover_tasks(self, n: int) -> list[str]:
        """terminal-bench 2.0 task discovery seam (harbor registry CLI,
        ``harbor.cli.tasks`` / ``harbor task list``). The terminal-bench 2.0
        dataset download and task-name filtering land in todo 8's real batch;
        until then this seam degrades honestly instead of guessing names."""
        raise BenchmarkDataUnavailable(
            "terminal-bench-task-registry-not-wired: docker gate passed, but "
            "the terminal-bench 2.0 task download + harbor job wiring land in "
            "todo 8 (todo 7 is mock acceptance only)"
        )

    def _run_opencode(self, task: dict[str, Any]) -> TaskResult:
        seed = int(task.get("seed", 0))
        timeout_s = int(self._config.get("task_timeout_s", 900))
        reward, note = self._run_harbor_task(task, timeout_s, seed)
        self._record_trajectory(task["task_id"], [note])
        if reward is None:
            return TaskResult(
                task_id=str(task["task_id"]),
                benchmark=self.benchmark_id,
                status="error",
                seed=seed,
                details={"harbor": note},
            )
        resolved = float(reward) >= 1.0
        return TaskResult(
            task_id=str(task["task_id"]),
            benchmark=self.benchmark_id,
            status="passed" if resolved else "failed",
            score=round(float(reward), 6),
            seed=seed,
            details={"harbor": note, "reward": float(reward)},
        )

    def _run_harbor_task(
        self, task: dict[str, Any], timeout_s: int, seed: int
    ) -> tuple[float | None, str]:
        """harbor wiring seam (module docstring cites the entry points).

        Builds a ``JobConfig``-shaped config with the native opencode agent
        plugin, runs ``harbor job start -c <config>`` bounded by
        ``timeout_s``, and reads the verifier reward from harbor's
        ``result.json``. Real batches land in todo 8.
        """
        job_name = f"hb-{task['task_id']}-{seed}"
        with tempfile.TemporaryDirectory(prefix="harness-bench-harbor-") as workdir:
            config = {
                "job_name": job_name,
                "jobs_dir": workdir,
                "n_attempts": 1,
                "quiet": True,
                "agents": [{"name": "opencode"}],
                "tasks": [{"name": str(task.get("_harbor_task", ""))}],
            }
            config_path = Path(workdir) / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            try:
                proc = subprocess.run(
                    [self._harbor_bin, "job", "start", "-c", str(config_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                return None, f"harbor_job_timeout_after_{timeout_s}s"
            except OSError as exc:
                return None, f"harbor_cli_unavailable: {exc}"
            if proc.returncode != 0:
                return None, f"harbor_job_failed: {proc.stderr[:300]}"
            return self._read_harbor_reward(Path(workdir) / job_name / "result.json")

    def _read_harbor_reward(self, result_path: Path) -> tuple[float | None, str]:
        try:
            doc = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"harbor_result_unreadable: {str(exc)[:200]}"
        rewards = [value for value in _collect_key(doc, "reward") if value is not None]
        if not rewards:
            return None, "harbor_result_missing_reward"
        try:
            return float(rewards[0]), "harbor_verifier_reward"
        except (TypeError, ValueError):
            return None, f"harbor_reward_unparsable: {str(rewards[0])[:100]}"

    def _bridge_session_fallback(
        self, task: dict[str, Any], timeout_s: int, seed: int
    ) -> Any:
        """Secondary seam: drive the task through ``OpenCodeBridge.run_task``
        inside the task container if harbor's agent plugin were unavailable.
        harbor 0.6.1 natively supports opencode (``AgentName.OPENCODE``), so
        the harbor path is primary; documented for plan parity."""
        prompt = (
            f"You are working inside the terminal-bench task container for task "
            f"{task['task_id']}. Complete the task using shell commands in the "
            "workspace; do not modify verification scripts or grading files."
        )
        return self.bridge.run_task(prompt, timeout_s=timeout_s, seed=seed)
