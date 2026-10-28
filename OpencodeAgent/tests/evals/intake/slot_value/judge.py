#!/usr/bin/env python3
"""Recompute the slot decision-value verdict from matrix, spec and transcripts.

``judge_spec.json`` is the pre-registered rule. This module is its only
executable form: it canonicalises tool-call arguments, derives a run
signature, adjudicates every cell and slot, and (with ``--write``) emits
``verdict.md`` and the per-slot aggregates in ``results.json``.

``--check`` recomputes everything and refuses any drift, including a
judge_spec.json digest that no longer matches the digest recorded before the
first run. Stdlib only, by design.
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

REPEATS = 3
CELLS_PER_SLOT = 4
DEFAULT_THRESHOLD = 4
PRIVATE_SLOTS = ("成本价", "仓位规模")
ENUMISH = re.compile(r"^[A-Za-z0-9_.:+/-]{1,32}$")
STATUSES = ("ok", "run_error", "incomplete")


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
    reps: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        args = call.get("args")
        name = call.get("name")
        if isinstance(name, str) and isinstance(args, dict) and args:
            reps.append(f"{name}::{_json(canonical_args(args))}")
    return sorted(reps)


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
            if status == "ok":
                computed = signature_of(record.get("calls") or [])
                if record.get("signature") != computed:
                    raise SystemExit(f"{path}: stored signature is not reproducible")
                if record.get("signature_hash") != signature_hash(computed):
                    raise SystemExit(f"{path}: stored signature_hash mismatch")
            elif record.get("signature") is not None:
                raise SystemExit(f"{path}: {status} repeat must not carry a signature")
            entries.append(record)
        runs[cell["id"]] = entries
    return runs


def _sig(record: dict[str, Any] | None) -> list[str] | None:
    if not record or record.get("status") != "ok":
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
                        "wall_s": None,
                        "tokens": None,
                        "recovered": False,
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
                    "wall_s",
                    "tokens",
                    "recovered",
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
    statuses = [record["status"] for cell in matrix["cells"] for record in runs[cell["id"]] if record]
    walls = [
        record["wall_s"]
        for cell in matrix["cells"]
        for record in runs[cell["id"]]
        if record and record["status"] == "ok" and isinstance(record.get("wall_s"), (int, float))
    ]
    return {
        "total": len(matrix["cells"]) * REPEATS,
        "ok": statuses.count("ok"),
        "run_error": statuses.count("run_error"),
        "incomplete": statuses.count("incomplete"),
        "timeouts": sum(
            1
            for cell in matrix["cells"]
            for record in runs[cell["id"]]
            if record and "timeout after" in (record.get("error") or "")
        ),
        "recovered": sum(
            1 for cell in matrix["cells"] for record in runs[cell["id"]] if record and record.get("recovered")
        ),
        "mean_wall_s": round(statistics.fmean(walls), 3) if walls else None,
        "median_wall_s": round(statistics.median(walls), 3) if walls else None,
    }


def check_meta(results: dict[str, Any], matrix: dict[str, Any], stats: dict[str, Any]) -> None:
    meta = results.get("meta") or {}
    if meta.get("judge_spec_sha256") != file_sha256(SPEC_PATH):
        raise SystemExit(
            "judge_spec.json digest differs from the digest recorded before the run: spec changed, run is void"
        )
    if meta.get("matrix_sha256") != file_sha256(MATRIX_PATH):
        raise SystemExit("matrix.json digest differs from the digest recorded before the run")
    if meta.get("repeats_per_cell") != REPEATS:
        raise SystemExit("results.json repeats_per_cell does not match judge_spec.json")
    expected = {
        "runs_total": stats["total"],
        "runs_ok": stats["ok"],
        "runs_run_error": stats["run_error"],
        "runs_incomplete": stats["incomplete"],
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
    fired = adjudication["kill_criterion"]["fired"]
    lines = [
        "# Slot decision-value verdict",
        "",
        "Pre-registered scripted experiment. The decision rule lives in `judge_spec.json`",
        f"(sha256 `{spec_digest}`); every number below is emitted by `judge.py --write` from",
        "`matrix.json` + `transcripts/`, and `judge.py --check` recomputes all of it with zero",
        "tolerance. No human or LLM judgement enters a verdict.",
        "",
        "## Execution",
        "",
        "| item | value |",
        "|------|-------|",
        f"| cells | {len(matrix['cells'])} |",
        f"| repeats per cell | {REPEATS} |",
        f"| runs recorded | {stats['total']} |",
        f"| runs ok | {stats['ok']} |",
        f"| runs run_error | {stats['run_error']} |",
        f"| runs incomplete | {stats['incomplete']} |",
        f"| runs that hit the per-run timeout | {stats['timeouts']} |",
        f"| recovered via session API | {stats['recovered']} |",
        f"| mean wall per ok run (s) | {_fmt(stats['mean_wall_s'])} |",
        f"| median wall per ok run (s) | {_fmt(stats['median_wall_s'])} |",
        f"| image | {_fmt(meta.get('image'))} |",
        f"| container | {_fmt(meta.get('container'))} |",
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
            "| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |",
            "|-------|------|-----------------|--------|---------------|---------------|--------|",
        ]
        for cell in slot["cells"]:
            lines.append(
                f"| {cell['value']} | {cell['cell']} | {_fmt(cell['repeat_statuses'])} | "
                f"{cell['usable_repeats']} | {cell['distinct_signatures']} | "
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
        "",
    ]
    if all(adjudication["slots"][key]["verdict"] == "INCONCLUSIVE" for key in matrix["slot_order"]):
        lines += [
            "## Diagnosis",
            "",
            f"{stats['ok']} of {stats['total']} runs produced a signature; {stats['timeouts']} hit the",
            f"pre-registered {meta.get('per_run_timeout_s')}s per-run budget. Under the pre-registered",
            "majority rule a cell needs two usable repeats that agree; no cell reached that bar.",
            "Every slot is therefore INCONCLUSIVE: the experiment does not show that any slot value",
            "changes the tool-call signature, and it is not evidence for defaulting any slot. The",
            "limiting factor is the per-run time budget, not an observed absence of effect.",
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
        f"| compose project down | {cleanup.get('compose_down')} |",
        f"| lane temp dir removed | {cleanup.get('lane_dir_removed')} |",
        f"| intake-slot container absent | {cleanup.get('container_absent')} |",
        f"| checked at (UTC) | {_fmt(cleanup.get('checked_at'))} |",
        f"| `docker ps -a` intake-slot rows | {_fmt(cleanup.get('container_rows'))} |",
        "",
        "## Caveats",
        "",
        "- Empty signature (`[]`) means the run produced no tool call with non-empty arguments;",
        "  it is a usable signature under the pre-registered rule, not a failed run.",
        "- The container has no ClickHouse credentials, so `ch_*` tools fail fast. This is an",
        "  environment limitation recorded once; it is not counted as a model failure.",
        "- A `run_error` is retained, never replaced by an extra repeat. A cell with fewer than",
        "  two usable repeats is INCONCLUSIVE.",
        "- Runs share one persistent `/workspace`; a run that finds artifacts written by an earlier",
        "  run can complete by validating them. That is measured behavior, not a controlled variable.",
        f"- Runs executed with {meta.get('workers')} concurrent workers in one shared `/workspace`.",
        "",
        "<!-- judge:begin -->",
        "```json",
        json.dumps(
            {
                "spec_sha256": spec_digest,
                "matrix_sha256": file_sha256(MATRIX_PATH),
                "runs": stats,
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
                f"[verdict] {adjudication['slots'][key]['name']}: " f"{adjudication['slots'][key]['verdict']}",
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
        f"{stats['ok']} ok / {stats['run_error']} run_error / {stats['incomplete']} incomplete",
        file=sys.stderr,
    )
    for key in matrix["slot_order"]:
        print(f"[check] {adjudication['slots'][key]['name']}: {adjudication['slots'][key]['verdict']}", file=sys.stderr)
    print(f"[check] kill criterion fired: {adjudication['kill_criterion']['fired']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
