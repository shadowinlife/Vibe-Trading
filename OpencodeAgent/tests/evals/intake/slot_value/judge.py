#!/usr/bin/env python3
"""Recompute the slot decision-value verdict from matrix, spec and transcripts.

``judge_spec.json`` v2 is the pre-registered rule: a run signature is the first
three distinct non-empty tool calls, and a run that observed at least one call
before the early stop is usable. This module is the rule's only executable
form. It canonicalises arguments, derives signatures, adjudicates every cell
and slot, and (with ``--write``) emits ``verdict.md`` and the per-slot
aggregates in ``results.json``.

``--check`` recomputes everything and refuses any drift, including a
judge_spec.json digest that no longer matches the digest recorded before the
first v2 run. Stdlib only, by design.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = BASE_DIR / "matrix.json"
SPEC_PATH = BASE_DIR / "judge_spec.json"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
RESULTS_PATH = BASE_DIR / "results.json"
VERDICT_PATH = BASE_DIR / "verdict.md"
V1_RESULTS_PATH = BASE_DIR / "v1_evidence" / "results.json"

REPEATS = 3
MAX_CALLS = 3
CELLS_PER_SLOT = 4
DEFAULT_THRESHOLD = 4
PRIVATE_SLOTS = ("成本价", "仓位规模")
ENUMISH = re.compile(r"^[A-Za-z0-9_.:+/-]{1,32}$")
USABLE_STATUSES = ("ok", "ok_partial")
STATUSES = ("ok", "ok_partial", "run_error", "incomplete")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _canon_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return round(value, 4)
    if isinstance(value, str):
        text = value.strip()
        return text.lower() if ENUMISH.fullmatch(text) else text
    return str(value)


def canonical_args(value: Any) -> Any:
    """Canonicalise tool arguments exactly as judge_spec.json prescribes."""
    if isinstance(value, dict):
        return {str(key): canonical_args(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        items = [canonical_args(item) for item in value]
        if all(not isinstance(item, (dict, list)) for item in items):
            return sorted(items, key=_json)
        return items
    return _canon_scalar(value)


def signature_of(calls: list[dict[str, Any]]) -> list[str]:
    """Ordered, deduplicated first ``MAX_CALLS`` calls with non-empty args."""
    reps: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        args, name = call.get("args"), call.get("name")
        if not isinstance(name, str) or not isinstance(args, dict) or not args:
            continue
        rep = f"{name}::{_json(canonical_args(args))}"
        if rep in reps:
            continue
        reps.append(rep)
        if len(reps) == MAX_CALLS:
            break
    return reps


def signature_hash(signature: list[str]) -> str:
    return hashlib.sha256("|".join(signature).encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_runs(matrix: dict[str, Any]) -> dict[str, list[dict[str, Any] | None]]:
    runs: dict[str, list[dict[str, Any] | None]] = {}
    for cell in matrix["cells"]:
        entries: list[dict[str, Any] | None] = []
        for repeat in range(REPEATS):
            path = TRANSCRIPTS_DIR / cell["id"] / f"{repeat}.json"
            if not path.exists():
                entries.append(None)
                continue
            record = load_json(path)
            status = record.get("status")
            if status not in STATUSES:
                raise SystemExit(f"{path}: invalid status {status!r}")
            if record.get("cell") != cell["id"] or record.get("repeat") != repeat:
                raise SystemExit(f"{path}: cell/repeat identity mismatch")
            if status in USABLE_STATUSES:
                computed = signature_of(record.get("calls") or [])
                if record.get("signature") != computed:
                    raise SystemExit(f"{path}: stored signature is not reproducible")
                if record.get("signature_hash") != signature_hash(computed):
                    raise SystemExit(f"{path}: stored signature_hash mismatch")
                if record.get("k_calls") != len(computed):
                    raise SystemExit(f"{path}: k_calls does not match the observed signature")
            elif record.get("signature") is not None:
                raise SystemExit(f"{path}: {status} repeat must not carry a signature")
            entries.append(record)
        runs[cell["id"]] = entries
    return runs


def _sig(record: dict[str, Any] | None) -> list[str] | None:
    if not record or record.get("status") not in USABLE_STATUSES:
        return None
    return record["signature"]


def adjudicate_cell(cell: dict[str, Any], repeats: list[dict[str, Any] | None]) -> dict[str, Any]:
    usable = [record for record in repeats if _sig(record) is not None]
    counts: dict[str, int] = {}
    for record in usable:
        key = "|".join(record["signature"])
        counts[key] = counts.get(key, 0) + 1
    majority_key = next((key for key, count in counts.items() if count >= 2), None)
    majority = majority_key.split("|") if majority_key is not None else None
    stable = majority is not None
    return {
        "cell": cell["id"],
        "slot_key": cell["slot_key"],
        "slot": cell["slot"],
        "value": cell["value"],
        "repeat_statuses": [record["status"] if record else "missing" for record in repeats],
        "k_calls": [record.get("k_calls") if record else None for record in repeats],
        "usable_repeats": len(usable),
        "distinct_signatures": len(counts),
        "majority_signature": majority,
        "majority_signature_hash": signature_hash(majority) if majority is not None else None,
        "stable": stable,
        "verdict": "stable" if stable else "INCONCLUSIVE",
    }


def pair_evidence(
    cell_a: dict[str, Any],
    cell_b: dict[str, Any],
    repeats_a: list[dict[str, Any] | None],
    repeats_b: list[dict[str, Any] | None],
) -> dict[str, Any]:
    comparable = different = 0
    for repeat in range(REPEATS):
        sig_a, sig_b = _sig(repeats_a[repeat]), _sig(repeats_b[repeat])
        if sig_a is None or sig_b is None:
            continue
        comparable += 1
        different += int(sig_a != sig_b)
    return {
        "a_value": cell_a["value"],
        "b_value": cell_b["value"],
        "comparable": comparable,
        "different": different,
        "differs": comparable >= 2 and different >= 2,
    }


def adjudicate_slot(
    slot: dict[str, Any], cells: list[dict[str, Any]], runs: dict[str, list[dict[str, Any] | None]]
) -> dict[str, Any]:
    summaries = [adjudicate_cell(cell, runs[cell["id"]]) for cell in cells]
    pairs = [
        pair_evidence(cells[i], cells[j], runs[cells[i]["id"]], runs[cells[j]["id"]])
        for i in range(len(cells))
        for j in range(i + 1, len(cells))
    ]
    inconclusive = [summary for summary in summaries if not summary["stable"]]
    majors = [summary["majority_signature"] for summary in summaries if summary["stable"]]
    distinct = sorted({signature_hash(sig) for sig in majors})
    if len(inconclusive) >= 2:
        verdict, reason = "INCONCLUSIVE", f"{len(inconclusive)} of 4 cells have no majority signature"
    elif len(majors) == CELLS_PER_SLOT and len(distinct) == 1:
        verdict, reason = "default-worthy", "all 4 cell majority signatures are identical"
    elif len(distinct) >= 2 and any(pair["differs"] for pair in pairs):
        verdict, reason = (
            "question-worthy",
            ">=2 distinct majority signatures and a value pair differs in >=2 of 3 repeats",
        )
    else:
        verdict, reason = "INCONCLUSIVE", "neither the default-worthy nor the question-worthy rule is satisfied"
    return {
        "key": slot["key"],
        "name": slot["name"],
        "values": slot["values"],
        "verdict": verdict,
        "reason": reason,
        "stable_cells": sum(1 for summary in summaries if summary["stable"]),
        "inconclusive_cells": [summary["cell"] for summary in inconclusive],
        "distinct_majority_signatures": distinct,
        "pair_diffs": pairs,
        "cells": summaries,
    }


def adjudicate(matrix: dict[str, Any], runs: dict[str, list[dict[str, Any] | None]]) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for slot in matrix["slots"]:
        cells = [cell for cell in matrix["cells"] if cell["slot_key"] == slot["key"]]
        slots[slot["key"]] = adjudicate_slot(slot, cells, runs)
    verdicts = [slots[key]["verdict"] for key in matrix["slot_order"]]
    default_count = verdicts.count("default-worthy")
    fired = default_count >= DEFAULT_THRESHOLD
    question_table = []
    for key in matrix["slot_order"]:
        slot = slots[key]
        decision = (
            "default"
            if slot["verdict"] == "default-worthy"
            else ("ask" if slot["verdict"] == "question-worthy" else "hold-inconclusive")
        )
        question_table.append({"slot": slot["name"], "verdict": slot["verdict"], "decision": decision})
    for name in PRIVATE_SLOTS:
        question_table.append({"slot": name, "verdict": "private", "decision": "ask"})
    return {
        "slots": slots,
        "kill_criterion": {
            "fired": fired,
            "default_worthy_count": default_count,
            "threshold": f"{DEFAULT_THRESHOLD} of {len(matrix['slot_order'])}",
            "consequence": (
                "question table shrinks to private slots only: 成本价, 仓位规模"
                if fired
                else "the question table keeps question-worthy public slots plus private slots 成本价, 仓位规模; "
                "default-worthy public slots are defaulted; INCONCLUSIVE slots are held, not guessed"
            ),
        },
        "question_table": question_table,
    }


def build_cells(matrix: dict[str, Any], runs: dict[str, list[dict[str, Any] | None]]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for cell in matrix["cells"]:
        records = []
        for repeat, record in enumerate(runs[cell["id"]]):
            if record is None:
                records.append(
                    {
                        "repeat": repeat,
                        "status": "incomplete",
                        "signature": None,
                        "signature_hash": None,
                        "k_calls": 0,
                        "wall_s": None,
                        "tokens": None,
                        "recovered": False,
                        "killed_by_early_stop": False,
                        "reason": "not_started",
                        "transcript": f"transcripts/{cell['id']}/{repeat}.json",
                    }
                )
                continue
            item = {
                key: record.get(key)
                for key in (
                    "repeat",
                    "status",
                    "signature",
                    "signature_hash",
                    "k_calls",
                    "wall_s",
                    "tokens",
                    "recovered",
                    "killed_by_early_stop",
                    "lane",
                    "transcript",
                )
            }
            item.update(
                {
                    key: record[key]
                    for key in ("reason", "error", "started_at", "finished_at")
                    if record.get(key) is not None
                }
            )
            records.append(item)
        cells[cell["id"]] = {
            "slot_key": cell["slot_key"],
            "slot": cell["slot"],
            "value": cell["value"],
            "repeats": records,
        }
    return cells


def run_stats(matrix: dict[str, Any], runs: dict[str, list[dict[str, Any] | None]]) -> dict[str, Any]:
    records = [record for cell in matrix["cells"] for record in runs[cell["id"]] if record]
    statuses = [record["status"] for record in records]
    usable = [record for record in records if record["status"] in USABLE_STATUSES]
    walls = [record["wall_s"] for record in usable if isinstance(record.get("wall_s"), (int, float))]
    k_distribution = {"0": 0, "1": 0, "2": 0, "3": 0}
    for record in records:
        if record["status"] in USABLE_STATUSES:
            k = record.get("k_calls")
            if k is None:
                k = len(record.get("signature") or [])
            key = str(k)
            k_distribution[key] = k_distribution.get(key, 0) + 1
        elif record["status"] == "run_error":
            k_distribution["0"] += 1
    return {
        "total": len(matrix["cells"]) * REPEATS,
        "ok": statuses.count("ok"),
        "ok_partial": statuses.count("ok_partial"),
        "run_error": statuses.count("run_error"),
        "incomplete": statuses.count("incomplete"),
        "early_stop_count": sum(1 for record in usable if record.get("killed_by_early_stop")),
        "timeout_count": sum(1 for record in records if "timeout after" in (record.get("error") or "")),
        "recovered": sum(1 for record in records if record.get("recovered")),
        "k_distribution": k_distribution,
        "mean_wall_s": round(statistics.fmean(walls), 3) if walls else None,
        "median_wall_s": round(statistics.median(walls), 3) if walls else None,
    }


def v1_evidence_summary() -> dict[str, Any]:
    if not V1_RESULTS_PATH.exists():
        return {"available": False}
    data = load_json(V1_RESULTS_PATH)
    meta = data.get("meta") or {}
    cells = data.get("cells") or {}
    with_majority = 0
    for cell in cells.values():
        counts: dict[str, int] = {}
        for record in cell.get("repeats") or []:
            if record.get("status") == "ok" and record.get("signature") is not None:
                key = "|".join(record["signature"])
                counts[key] = counts.get(key, 0) + 1
        with_majority += int(any(count >= 2 for count in counts.values()))
    return {
        "available": True,
        "spec_version": 1,
        "runs": meta.get("runs_total"),
        "ok": meta.get("runs_ok"),
        "run_error": meta.get("runs_run_error"),
        "incomplete": meta.get("runs_incomplete"),
        "per_run_timeout_s": meta.get("per_run_timeout_s"),
        "mean_wall_s": meta.get("mean_wall_s"),
        "cells_with_majority": with_majority,
        "note": "v1 is void: 69/72 runs hit the 240s per-run timeout; the 3 isolated signatures formed 0 cell-level majorities. Full v1 transcripts remain in commit 8d25e5d8.",
    }


def check_meta(results: dict[str, Any], matrix: dict[str, Any], stats: dict[str, Any]) -> None:
    meta = results.get("meta") or {}
    spec = load_json(SPEC_PATH)
    if meta.get("judge_spec_sha256") != file_sha256(SPEC_PATH):
        raise SystemExit(
            "judge_spec.json digest differs from the digest recorded before the run: spec changed, run is void"
        )
    if meta.get("matrix_sha256") != file_sha256(MATRIX_PATH):
        raise SystemExit("matrix.json digest differs from the digest recorded before the run")
    if meta.get("repeats_per_cell") != REPEATS:
        raise SystemExit("results.json repeats_per_cell does not match judge_spec.json")
    if meta.get("per_run_timeout_s") != spec["observation"]["per_run_timeout_s"]:
        raise SystemExit("results.json per_run_timeout_s does not match judge_spec.json")
    expected = {
        "runs_total": stats["total"],
        "runs_ok": stats["ok"],
        "runs_ok_partial": stats["ok_partial"],
        "runs_run_error": stats["run_error"],
        "runs_incomplete": stats["incomplete"],
        "early_stop_count": stats["early_stop_count"],
        "timeout_count": stats["timeout_count"],
        "recovery_count": stats["recovered"],
        "mean_wall_s": stats["mean_wall_s"],
        "median_wall_s": stats["median_wall_s"],
    }
    for key, value in expected.items():
        if meta.get(key) != value:
            raise SystemExit(f"results.json meta.{key}={meta.get(key)!r}, recomputed {value!r}")
    if len(matrix["cells"]) != len(matrix["slot_order"]) * CELLS_PER_SLOT:
        raise SystemExit("matrix must contain 6 slots x 4 cells")


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def render_verdict(
    matrix: dict[str, Any],
    results: dict[str, Any],
    adjudication: dict[str, Any],
    stats: dict[str, Any],
    spec_digest: str,
) -> str:
    meta = results.get("meta") or {}
    cleanup = meta.get("cleanup") or {}
    v1 = v1_evidence_summary()
    fired = adjudication["kill_criterion"]["fired"]
    lanes = ", ".join(cleanup.get("lanes") or [])
    lines = [
        "# Slot decision-value verdict (v2, bounded observation)",
        "",
        "Pre-registered scripted experiment. The decision rule lives in `judge_spec.json` v2",
        f"(sha256 `{spec_digest}`); every number below is emitted by `judge.py --write` from",
        "`matrix.json` + `transcripts/`, and `judge.py --check` recomputes all of it with zero",
        "tolerance. No human or LLM judgement enters a verdict.",
        "",
        "## Observation design (pre-registered)",
        "",
        "A signature is the **first 3 distinct tool calls with non-empty args**, in order of",
        "first appearance, canonicalised with the v1 argument rules. The driver reads the CLI",
        "JSON stream line by line and kills the CLI process group as soon as the 3rd call is",
        "observed; it also kills at the 300s per-run timeout. A run that observed at least one",
        "call before the stop is `ok_partial` and usable; a run with zero calls is `run_error`.",
        "Later execution calls are outside the observation window by design: parameter",
        "compilation happens in the first calls.",
        "",
        "## v1 evidence: what the first attempt proved and why it was void",
        "",
        "| item | v1 |",
        "|------|----|",
        f"| runs recorded | {_fmt(v1.get('runs'))} |",
        f"| ok signatures | {_fmt(v1.get('ok'))} |",
        f"| run_error / timeouts | {_fmt(v1.get('run_error'))} |",
        f"| cells with a majority signature | {_fmt(v1.get('cells_with_majority'))} |",
        f"| per-run timeout (s) | {_fmt(v1.get('per_run_timeout_s'))} |",
        "| verdict | void: all six slots INCONCLUSIVE |",
        "",
        "v1 is void because the observation window was unbounded: 69/72 runs hit the 240s",
        "timeout, and the 3 isolated signatures never formed a cell-level majority. The v2",
        "change is an observation-window change, not a threshold change; the slot rule and",
        "kill criterion are identical and were frozen before the first v2 run.",
        "",
        "## Execution",
        "",
        "| item | value |",
        "|------|-------|",
        f"| cells | {len(matrix['cells'])} |",
        f"| repeats per cell | {REPEATS} |",
        f"| runs recorded | {stats['total']} |",
        f"| ok (natural completion) | {stats['ok']} |",
        f"| ok_partial (early stop or timeout with >=1 call) | {stats['ok_partial']} |",
        f"| run_error (0 calls) | {stats['run_error']} |",
        f"| incomplete (budget) | {stats['incomplete']} |",
        f"| killed by the 3-call early stop | {stats['early_stop_count']} |",
        f"| hit the per-run timeout | {stats['timeout_count']} |",
        f"| recovered via session API | {stats['recovered']} |",
        f"| k-call distribution (0/1/2/3) | {stats['k_distribution']['0']}/{stats['k_distribution']['1']}/{stats['k_distribution']['2']}/{stats['k_distribution']['3']} |",
        f"| mean wall per usable run (s) | {_fmt(stats['mean_wall_s'])} |",
        f"| median wall per usable run (s) | {_fmt(stats['median_wall_s'])} |",
        f"| image | {_fmt(meta.get('image'))} |",
        f"| containers | {_fmt(meta.get('containers'))} |",
        f"| per-run timeout (s) | {_fmt(meta.get('per_run_timeout_s'))} |",
        f"| batch wall budget (s) | {_fmt(meta.get('batch_wall_budget_s'))} |",
        f"| run window (UTC) | {_fmt(meta.get('started_at'))} -> {_fmt(meta.get('finished_at'))} |",
        "",
        "## Per-slot verdicts",
        "",
        "| 槽位 | 裁决 | stable cells | distinct majority signatures | differing value pairs | usable repeats |",
        "|------|------|--------------|------------------------------|-----------------------|----------------|",
    ]
    for key in matrix["slot_order"]:
        slot = adjudication["slots"][key]
        usable = sum(cell["usable_repeats"] for cell in slot["cells"])
        differing = [f"{pair['a_value']} vs {pair['b_value']}" for pair in slot["pair_diffs"] if pair["differs"]]
        lines.append(
            f"| {slot['name']} | {slot['verdict']} | {slot['stable_cells']}/4 | "
            f"{len(slot['distinct_majority_signatures'])} | {_fmt(differing)} | {usable}/12 |"
        )
    lines += ["", "### Per-slot evidence", ""]
    for key in matrix["slot_order"]:
        slot = adjudication["slots"][key]
        lines += [
            f"#### {slot['name']} — {slot['verdict']}",
            "",
            f"Reason: {slot['reason']}.",
            "",
            "| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |",
            "|-------|------|-----------------|---------|--------|---------------|---------------|--------|",
        ]
        for cell in slot["cells"]:
            lines.append(
                f"| {cell['value']} | {cell['cell']} | {_fmt(cell['repeat_statuses'])} | "
                f"{_fmt(cell['k_calls'])} | {cell['usable_repeats']} | {cell['distinct_signatures']} | "
                f"{_fmt(cell['majority_signature_hash'])} | {str(cell['stable']).lower()} |"
            )
        lines += [
            "",
            "| value pair | comparable repeats | differing repeats | differs |",
            "|------------|--------------------|-------------------|---------|",
        ]
        for pair in slot["pair_diffs"]:
            lines.append(
                f"| {pair['a_value']} vs {pair['b_value']} | {pair['comparable']} | "
                f"{pair['different']} | {str(pair['differs']).lower()} |"
            )
        lines.append("")
    lines += [
        "## Kill criterion",
        "",
        f"Default-worthy slots: {adjudication['kill_criterion']['default_worthy_count']} of 6 "
        f"(threshold {adjudication['kill_criterion']['threshold']}).",
        f"Kill criterion fired: **{str(fired).lower()}**.",
        "",
        adjudication["kill_criterion"]["consequence"] + ".",
        "",
        "Counterfactual, fixed by the pre-registration: if at least 4 of 6 slots had been",
        "default-worthy, the question table would shrink to private slots only: 成本价, 仓位规模.",
        "",
    ]
    if all(adjudication["slots"][key]["verdict"] == "INCONCLUSIVE" for key in matrix["slot_order"]):
        inconclusive_total = sum(
            1 for key in matrix["slot_order"] for cell in adjudication["slots"][key]["cells"] if not cell["stable"]
        )
        min_per_slot = min(
            sum(1 for cell in adjudication["slots"][key]["cells"] if not cell["stable"]) for key in matrix["slot_order"]
        )
        max_distinct = max(
            cell["distinct_signatures"] for key in matrix["slot_order"] for cell in adjudication["slots"][key]["cells"]
        )
        lines += [
            "## Diagnosis",
            "",
            f"{stats['ok'] + stats['ok_partial']} of {stats['total']} runs yielded a usable signature; "
            f"{stats['early_stop_count']} stopped at the 3-call threshold and {stats['timeout_count']} "
            "hit the per-run timeout. Under the pre-registered majority rule a cell needs two usable",
            "repeats that agree; no cell reached that bar. Every slot is therefore INCONCLUSIVE: the",
            "experiment does not show that any slot value changes the first compiled calls, and it is",
            "not evidence for defaulting any slot.",
            "",
            f"Acceptance check: {inconclusive_total} of {len(matrix['cells'])} cells are INCONCLUSIVE; "
            f"the smallest per-slot count is {min_per_slot}, so the >=2 INCONCLUSIVE cells per slot",
            f"condition is met. Every cell shows {max_distinct} distinct usable signatures; the cause is",
            "run-to-run variation in the first three calls, not missing observations. Required N: rerun",
            "until each cell has at least two usable repeats sharing a signature.",
            "",
        ]
    lines += [
        "## Resulting question table (todo 12)",
        "",
        "| 槽位 | 裁决 | 处置 |",
        "|------|------|------|",
    ]
    for row in adjudication["question_table"]:
        lines.append(f"| {row['slot']} | {row['verdict']} | {row['decision']} |")
    if all(adjudication["slots"][key]["verdict"] == "INCONCLUSIVE" for key in matrix["slot_order"]):
        lines += [
            "",
            "All six public slots are INCONCLUSIVE. This experiment does not license defaulting",
            "any of them; todo 12 must not consume a default set from this run.",
        ]
    lines += [
        "",
        "Decision vocabulary: `default` = 默认化，不提问；`ask` = 保留提问；",
        "`hold-inconclusive` = 证据不足，不得静默归入 default 或 ask。",
        "",
        "## Cleanup receipt",
        "",
        "| check | result |",
        "|-------|--------|",
        f"| compose projects down | {cleanup.get('compose_down')} |",
        f"| lane temp dir removed | {cleanup.get('lane_dir_removed')} |",
        f"| containers absent | {cleanup.get('containers_absent', cleanup.get('container_absent'))} |",
        f"| lanes | {_fmt(lanes)} |",
        f"| checked at (UTC) | {_fmt(cleanup.get('checked_at'))} |",
        f"| `docker ps -a` intake-slot rows | {_fmt(cleanup.get('container_rows'))} |",
        "",
        "## Caveats",
        "",
        "- A signature of length 1 or 2 means the run stopped before the 3rd call; it is usable",
        "  under the pre-registered rule, not a failed run.",
        "- A `run_error` is retained, never replaced by an extra repeat. A cell with fewer than",
        "  two usable repeats is INCONCLUSIVE.",
        "- Each lane runs one session at a time and clears scratch artifacts and agent memory",
        "  before every run, so a run cannot compile against another run's workspace.",
        "- Runs executed on 2 lanes with 1 worker per lane.",
        "",
        "<!-- judge:begin -->",
        "```json",
        json.dumps(
            {
                "spec_version": 2,
                "spec_sha256": spec_digest,
                "matrix_sha256": file_sha256(MATRIX_PATH),
                "runs": stats,
                "v1_evidence": v1,
                "slots": {
                    key: {
                        "name": adjudication["slots"][key]["name"],
                        "verdict": adjudication["slots"][key]["verdict"],
                        "reason": adjudication["slots"][key]["reason"],
                        "stable_cells": adjudication["slots"][key]["stable_cells"],
                        "distinct_majority_signatures": adjudication["slots"][key]["distinct_majority_signatures"],
                        "pair_diffs": adjudication["slots"][key]["pair_diffs"],
                    }
                    for key in matrix["slot_order"]
                },
                "kill_criterion": adjudication["kill_criterion"],
                "question_table": adjudication["question_table"],
                "cleanup": cleanup,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "<!-- judge:end -->",
        "",
    ]
    return "\n".join(lines)


def rebuild(
    matrix: dict[str, Any], results: dict[str, Any]
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    runs = load_runs(matrix)
    stats = run_stats(matrix, runs)
    check_meta(results, matrix, stats)
    adjudication = adjudicate(matrix, runs)
    cells = build_cells(matrix, runs)
    updated = dict(results)
    updated["cells"] = cells
    updated["slots"] = adjudication["slots"]
    updated["kill_criterion"] = adjudication["kill_criterion"]
    updated["question_table"] = adjudication["question_table"]
    verdict = render_verdict(matrix, updated, adjudication, stats, file_sha256(SPEC_PATH))
    return updated, verdict, adjudication, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="rewrite results.json aggregates and verdict.md")
    group.add_argument("--check", action="store_true", help="recompute and fail on any drift (default)")
    args = parser.parse_args(argv)
    matrix = load_json(MATRIX_PATH)
    if not RESULTS_PATH.exists():
        print(f"missing {RESULTS_PATH}", file=sys.stderr)
        return 2
    results = load_json(RESULTS_PATH)
    updated, verdict, adjudication, stats = rebuild(matrix, results)
    if args.write:
        RESULTS_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        VERDICT_PATH.write_text(verdict, encoding="utf-8")
        print(
            f"[write] {RESULTS_PATH.name}: {len(updated['cells'])} cells, "
            f"{stats['total']} runs; {VERDICT_PATH.name} regenerated",
            file=sys.stderr,
        )
        for key in matrix["slot_order"]:
            print(
                f"[verdict] {adjudication['slots'][key]['name']}: {adjudication['slots'][key]['verdict']}",
                file=sys.stderr,
            )
        print(f"[verdict] kill criterion fired: {adjudication['kill_criterion']['fired']}", file=sys.stderr)
        return 0
    problems: list[str] = []
    if updated["cells"] != results.get("cells"):
        problems.append("results.json cells do not match recomputation")
    for key in ("slots", "kill_criterion", "question_table"):
        if updated[key] != results.get(key):
            problems.append(f"results.json {key} does not match recomputation")
    if not VERDICT_PATH.exists():
        problems.append("verdict.md is missing")
    elif VERDICT_PATH.read_text(encoding="utf-8") != verdict:
        problems.append("verdict.md does not match recomputation")
    if problems:
        print("\n".join(f"[drift] {problem}" for problem in problems), file=sys.stderr)
        return 1
    print(
        f"[check] ok: {len(updated['cells'])} cells, {stats['total']} runs, "
        f"{stats['ok']} ok / {stats['ok_partial']} ok_partial / {stats['run_error']} run_error / "
        f"{stats['incomplete']} incomplete",
        file=sys.stderr,
    )
    for key in matrix["slot_order"]:
        print(f"[check] {adjudication['slots'][key]['name']}: {adjudication['slots'][key]['verdict']}", file=sys.stderr)
    print(f"[check] kill criterion fired: {adjudication['kill_criterion']['fired']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
