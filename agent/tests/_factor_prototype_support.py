"""Shared fixtures and helpers for frozen factor prototype contract tests.

The orchestrator/worker suite and the HTTP suite both import these helpers so
neither file duplicates the registry, evaluator, or spec builders.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.factors.base import Alpha
from src.factors.prototype import (
    FactorPrototypeRunner,
    PhaseOutcome,
    PrototypeRunRequest,
)
from src.factors.registry_freeze import (
    TOOL_SCHEMA_VERSION,
    freeze_registry,
    rows_from_registry,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    canonical_json_bytes,
    sha256_hex,
)
from src.strategy_runs.control_store import ControlStore

_CANDIDATES = ("academic_done", "academic_skip", "academic_thin")
_SYMBOLS = tuple(f"TEST{index}" for index in range(6))


@pytest.fixture(autouse=True)
def _allowed_run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))


class _Registry:
    def __init__(self, candidate_ids: tuple[str, ...] = _CANDIDATES) -> None:
        self._alphas = {
            candidate_id: Alpha(
                id=candidate_id,
                zoo="academic",
                module_path="tests.fixture",
                meta={
                    "universe": ["equity_us"],
                    "columns_required": ["close"],
                    "min_warmup_bars": 9999 if candidate_id == "academic_skip" else 1,
                },
            )
            for candidate_id in candidate_ids
        }

    def get(self, candidate_id: str) -> Alpha:
        return self._alphas[candidate_id]


class _Evaluator:
    def __init__(self) -> None:
        self.requests = []

    def evaluate(self, request) -> PhaseOutcome:
        self.requests.append(request)
        output = request.output_dir
        output.mkdir(parents=True, exist_ok=True)
        summary = output / "summary.json"
        summary.write_text("{}\n", encoding="utf-8")
        if request.candidate.candidate_id == "academic_thin":
            return PhaseOutcome(
                status="insufficient_data",
                reasons=("no_paired_observations",),
                metrics={},
                artifacts={"summary": str(summary)},
            )

        in_sample = request.phase == "in_sample"
        metrics = {
            "ic_mean": 0.1 if in_sample else 0.04,
            "ic_ir": 1.2 if in_sample else 0.7,
            "group_return_spread": 0.08 if in_sample else 0.03,
            "n_obs": 20 if in_sample else 10,
            "coverage": 0.8 if in_sample else 0.75,
            "missing_ratio": 0.2 if in_sample else 0.25,
            "ic_count": 5,
            "n_groups": request.spec.n_groups,
        }
        artifacts = {"summary": str(summary)}
        for key, name, text in (
            ("factor_csv", "factor.csv", "date,TEST0\n2020-01-01,1\n"),
            ("returns_csv", "returns.csv", "date,TEST0\n2020-01-01,0.1\n"),
            ("ic_series", "ic_series.csv", "date,IC\n2020-01-01,0.1\n"),
            ("group_equity", "group_equity.csv", "date,G1,G2\n2020-01-01,1,1.1\n"),
        ):
            path = output / name
            path.write_text(text, encoding="utf-8")
            artifacts[key] = str(path)
        native_summary = output / "ic_summary.json"
        native_summary.write_text(
            json.dumps({"ic_mean": metrics["ic_mean"], "ic_count": 5}),
            encoding="utf-8",
        )
        artifacts["ic_summary"] = str(native_summary)
        return PhaseOutcome(
            status="ok", reasons=(), metrics=metrics, artifacts=artifacts
        )


def _spec(
    registry: _Registry,
    candidate_ids: tuple[str, ...] = _CANDIDATES,
    *,
    registry_digest: str | None = None,
    symbols: tuple[str, ...] = _SYMBOLS,
) -> FactorPrototypeSpec:
    rows = rows_from_registry(registry, candidate_ids)
    digest = registry_digest or freeze_registry(rows).registry_digest
    return FactorPrototypeSpec(
        registry_digest=digest,
        candidate_ids=candidate_ids,
        symbols=symbols,
        start_date=date(2020, 1, 1),
        split_date=date(2021, 1, 1),
        end_date=date(2021, 6, 30),
        source="alpha_zoo",
        as_of=date(2021, 6, 30),
        forward_return_horizon_days=2,
        n_groups=2,
    )


def _request(spec: FactorPrototypeSpec, run_dir: Path) -> PrototypeRunRequest:
    return PrototypeRunRequest(
        spec=spec,
        run_dir=run_dir,
        remote_id="factor-test-remote",
        dispatch_key="dispatch-factor-test",
    )


def _run_happy(run_dir: Path):
    registry = _Registry()
    evaluator = _Evaluator()
    progress = []
    runner = FactorPrototypeRunner(registry=registry, evaluator=evaluator)
    result = runner.run(_request(_spec(registry), run_dir), on_progress=progress.append)
    return result, evaluator, progress


def _write_spec_document(
    run_dir: Path,
    spec: FactorPrototypeSpec,
    *,
    remote_id: str | None = None,
    dispatch_key: str = "dispatch-factor-test",
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    identity = canonical_json_bytes(
        {
            "dispatch_key": dispatch_key,
            "spec": spec.model_dump(mode="json", by_alias=True),
        }
    )
    remote_id = remote_id or "factor-" + sha256_hex(identity)[:32]
    document = {
        "remote_id": remote_id,
        "dispatch_key": dispatch_key,
        "registry_digest": spec.registry_digest,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "spec": spec.model_dump(mode="json", by_alias=True),
    }
    (run_dir / "spec.json").write_text(json.dumps(document), encoding="utf-8")


def _terminal_row(
    store: ControlStore, run_dir: Path, registry_digest: str | None = None
):
    declaration = {"run_dir": str(run_dir)}
    if registry_digest is not None:
        declaration["start"] = {"spec": {"registry_digest": registry_digest}}
    row = store.claim_dispatch(
        "dispatch-factor-test",
        "factor",
        declaration,
        "factor-test-remote",
        CONTRACT_VERSION,
    ).row
    store.record_worker(row.dispatch_key, 101, 101, {})
    store.mark_dispatch_terminal(
        row.dispatch_key,
        "completed",
        result_path=str(run_dir / "worker_result.json"),
    )
    return row
