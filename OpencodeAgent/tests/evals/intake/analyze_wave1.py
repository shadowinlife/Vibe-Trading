"""Recompute both Wave 1 attempts and render ``results/wave1_report.md``.

Attempt 1 is the archived measurement with the old route classifier and the
observed-route closing denominator. Attempt 2 is the fixed instrument: the
classifier order refusal -> clarification -> execution -> direct answer, the
artifact guard, the product-side open-ended branch, and the corpus-expect
closing denominator. Every number in the report is recomputed here; ``--check``
regenerates the file and fails on any drift.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CORPUS_PATH = BASE_DIR / "corpus40.jsonl"
ATTEMPT1_PATH = BASE_DIR / "results" / "wave1_corpus40_attempt1.jsonl"
FIX1_PATH = BASE_DIR / "results" / "wave1_corpus40_fix1.jsonl"
TEXT_DIR_1 = BASE_DIR / "results" / "wave1_texts"
TEXT_DIR_FIX1 = BASE_DIR / "results" / "wave1_texts_fix1"
BASELINE_PATH = BASE_DIR / "results" / "baseline.jsonl"
BASELINE_META_PATH = BASE_DIR / "results" / "baseline_meta.json"
REPORT_PATH = BASE_DIR / "results" / "wave1_report.md"
EVENTS = ("route_class", "intake_loaded", "question_called", "params_compiled")
FROZEN = tuple(f"c{number:02d}" for number in range(1, 11))
Z = 1.959963984540054
WILSON_METHOD = "wilson score interval (95%, z=1.959964)"
CLOSING_THRESHOLD = 0.90
INTAKE_TRIGGER = 0.60
BUDGET_MARGIN = 0.10
BLOCK_BEGIN = "<!-- wave1:begin -->"
BLOCK_END = "<!-- wave1:end -->"

CLEANUP_RECEIPT = """\
Both compose projects (`wave1a`, `wave1b`) and the question-probe project were brought down with
`docker compose ... down --remove-orphans`; `rm -rf "$TMPDIR/wave1"` removed the lane volumes,
text shards and raw transcripts; `docker ps` returned no rows. No image was tagged or pushed."""


def load_checks():
    spec = importlib.util.spec_from_file_location("wave1_checks", BASE_DIR / "checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(k: int, n: int) -> dict[str, Any]:
    block: dict[str, Any] = {"k": k, "n": n, "rate": None, "low": None, "high": None, "method": WILSON_METHOD}
    if n <= 0:
        return block
    p = k / n
    denominator = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denominator
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denominator
    block.update(rate=round(p, 9), low=round(max(0.0, centre - half), 9), high=round(min(1.0, centre + half), 9))
    return block


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def ci(block: dict[str, Any]) -> str:
    return "n/a" if block["low"] is None else f"[{block['low'] * 100:.1f}%, {block['high'] * 100:.1f}%]"


def mcnemar_exact(base_only: int, wave_only: int) -> float:
    pairs = base_only + wave_only
    if pairs == 0:
        return 1.0
    tail = sum(math.comb(pairs, index) for index in range(min(base_only, wave_only) + 1)) / (2 ** pairs)
    return round(min(1.0, 2 * tail), 9)


def group_events(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for record in load_jsonl(path):
        key = (record["corpus_id"], record["event"])
        if key in seen:
            raise SystemExit(f"{path}: duplicate event {key}")
        seen.add(key)
        groups.setdefault(record["corpus_id"], {})[record["event"]] = record
    return groups


def pick(records: dict[str, dict[str, Any]], field: str) -> Any:
    for event in EVENTS + ("run_error",):
        if event in records and records[event].get(field) is not None:
            return records[event][field]
    return None


def item_success(records: dict[str, dict[str, Any]], event: str) -> bool:
    record = records.get(event)
    if record is None:
        return False
    if event == "route_class":
        return record["observed"] == record["expected"]
    return record["observed"] is True


def build_per_items(groups: dict[str, dict[str, dict[str, Any]]], corpus_ids: list[str]) -> dict[str, dict[str, Any]]:
    per: dict[str, dict[str, Any]] = {}
    for corpus_id in corpus_ids:
        records = groups.get(corpus_id, {})
        per[corpus_id] = {
            "event_complete": all(event in records for event in EVENTS),
            "run_error": "run_error" in records and not any(event in records for event in EVENTS),
            "route_observed": records.get("route_class", {}).get("observed"),
            "route_expected": records.get("route_class", {}).get("expected"),
            "success": {event: item_success(records, event) for event in EVENTS},
            "tokens": pick(records, "tokens"),
            "wall_s": pick(records, "wall_s"),
        }
    return per


def layer_summary(per: dict[str, dict[str, Any]], corpus_ids: list[str]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for event in EVENTS:
        k = sum(1 for corpus_id in corpus_ids if per[corpus_id]["success"][event])
        summary[event] = {"successes": k, **wilson(k, len(corpus_ids))}
    return summary


def paired_comparison(baseline: dict[str, dict[str, Any]], attempt: dict[str, dict[str, Any]], corpus_ids: list[str]) -> dict[str, Any]:
    layers: dict[str, dict[str, Any]] = {}
    for event in EVENTS:
        base_only, wave_only = [], []
        base_k = wave_k = 0
        for corpus_id in corpus_ids:
            left = baseline[corpus_id]["success"][event]
            right = attempt[corpus_id]["success"][event]
            base_k += int(left)
            wave_k += int(right)
            if left and not right:
                base_only.append(corpus_id)
            if right and not left:
                wave_only.append(corpus_id)
        layers[event] = {
            "baseline_successes": base_k,
            "wave1_successes": wave_k,
            "base_only": base_only,
            "wave_only": wave_only,
            "discordant": len(base_only) + len(wave_only),
            "mcnemar_exact_p": mcnemar_exact(len(base_only), len(wave_only)),
        }
    return {"ids": corpus_ids, "layers": layers}


def closing_block(text_dir: Path, corpus: dict[str, dict[str, Any]], attempt: dict[str, dict[str, Any]], checks: Any, mode: str) -> dict[str, Any]:
    if mode == "observed_route":
        denominator = [
            corpus_id for corpus_id in corpus
            if corpus[corpus_id]["expected_product"] != "none"
            and attempt[corpus_id]["route_observed"] == "execute_with_defaults"
        ]
    else:
        denominator = [
            corpus_id for corpus_id in corpus
            if corpus[corpus_id]["expected_product"] != "none"
            and corpus[corpus_id]["expect"] == "execute_with_defaults"
        ]
    rows = []
    for corpus_id in denominator:
        text_path = text_dir / f"{corpus_id}.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        result = checks.find_closing_block(text)
        rows.append({
            "id": corpus_id,
            "expected_product": corpus[corpus_id]["expected_product"],
            "route": attempt[corpus_id]["route_observed"],
            "complete": result["complete"],
            "missing": result["missing"],
            "text_chars": len(text),
        })
    k = sum(1 for row in rows if row["complete"])
    n = len(rows)
    verdict = "inconclusive" if n == 0 else ("pass" if k / n >= CLOSING_THRESHOLD else "fail")
    return {"mode": mode, "denominator": denominator, "rows": rows, "threshold": CLOSING_THRESHOLD, "wilson95": wilson(k, n), "verdict": verdict}


def intake_subset(corpus: dict[str, dict[str, Any]], groups: dict[str, dict[str, dict[str, Any]]], measured: list[str]) -> dict[str, Any]:
    ids = [corpus_id for corpus_id in measured if corpus[corpus_id]["open_ended"] or corpus[corpus_id]["expect"] == "clarify_private"]
    observed = [corpus_id for corpus_id in ids if groups.get(corpus_id, {}).get("intake_loaded", {}).get("observed") is True]
    block = wilson(len(observed), len(ids))
    missing = [corpus_id for corpus_id in ids if "intake_loaded" not in groups.get(corpus_id, {})]
    if missing or len(ids) < 14:
        verdict = "inconclusive"
    elif block["rate"] < INTAKE_TRIGGER:
        verdict = "fires"
    else:
        verdict = "does_not_fire"
    all_observed = [corpus_id for corpus_id in measured if groups.get(corpus_id, {}).get("intake_loaded", {}).get("observed") is True]
    return {
        "ids": ids, "observed_ids": observed, "missing_ids": missing, "n": len(ids),
        "observed_k": len(observed), "rate": block["rate"], "wilson95": block,
        "threshold": INTAKE_TRIGGER, "verdict": verdict,
        "all_observed_k": len(all_observed), "all_rate": round(len(all_observed) / len(measured), 9),
        "all_wilson95": wilson(len(all_observed), len(measured)),
    }


def budget(attempt: dict[str, dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    baseline_items = meta["per_item"]
    ids = [corpus_id for corpus_id in FROZEN if corpus_id in baseline_items]
    complete = [corpus_id for corpus_id in ids if attempt[corpus_id]["tokens"] is not None and attempt[corpus_id]["wall_s"] is not None]
    if len(complete) < len(ids):
        missing = [corpus_id for corpus_id in ids if corpus_id not in complete]
        return {"ids": ids, "n": len(complete), "verdict": "inconclusive", "reason": "missing token/wall values", "missing_ids": missing, "rows": []}
    base_tokens = sum(baseline_items[corpus_id]["tokens"] for corpus_id in ids) / len(ids)
    wave_tokens = sum(attempt[corpus_id]["tokens"] for corpus_id in complete) / len(complete)
    base_wall = sum(baseline_items[corpus_id]["wall_s"] for corpus_id in ids) / len(ids)
    wave_wall = sum(attempt[corpus_id]["wall_s"] for corpus_id in complete) / len(complete)
    token_change = (wave_tokens - base_tokens) / base_tokens
    wall_change = (wave_wall - base_wall) / base_wall
    rows = [
        {"metric": "tokens", "baseline_mean": round(base_tokens, 3), "wave1_mean": round(wave_tokens, 3), "pct_change": round(token_change, 6)},
        {"metric": "wall_s", "baseline_mean": round(base_wall, 3), "wave1_mean": round(wave_wall, 3), "pct_change": round(wall_change, 6)},
    ]
    verdict = "pass" if token_change <= BUDGET_MARGIN and wall_change <= BUDGET_MARGIN else "fail"
    return {"ids": ids, "n": len(complete), "margin": BUDGET_MARGIN, "rows": rows, "verdict": verdict}


def core_gate(paired: dict[str, Any], n_complete: int, n_items: int) -> dict[str, Any]:
    if n_complete == 0:
        return {"verdict": "inconclusive", "reason": f"0/{n_items} measured items carry any layer"}
    dropped = [
        event for event in EVENTS
        if paired["layers"][event]["wave1_successes"] < paired["layers"][event]["baseline_successes"]
    ]
    significant = [
        event for event in EVENTS
        if paired["layers"][event]["mcnemar_exact_p"] < 0.05
        and paired["layers"][event]["wave1_successes"] < paired["layers"][event]["baseline_successes"]
    ]
    paired_text = ", ".join(
        f"{event} {paired['layers'][event]['wave1_successes']}/10 vs {paired['layers'][event]['baseline_successes']}/10"
        for event in EVENTS
    )
    if dropped:
        return {"verdict": "fail", "reason": "paired point estimate below baseline: " + paired_text}
    if significant:
        return {"verdict": "fail", "reason": "paired McNemar shows significant worsening: " + paired_text}
    minimum = max(1, math.ceil(0.9 * n_items))
    if n_complete < minimum:
        return {"verdict": "inconclusive", "reason": f"only {n_complete}/{n_items} measured items carry all four layers, but no paired point estimate is below baseline"}
    return {"verdict": "pass", "reason": "paired point estimates are not below baseline and no layer has a significant paired worsening: " + paired_text}


def build_metrics(run_path: Path, text_dir: Path, closing_mode: str, label: str) -> dict[str, Any]:
    corpus = {item["id"]: item for item in load_jsonl(CORPUS_PATH)}
    if len(corpus) != 40:
        raise SystemExit(f"{CORPUS_PATH}: expected 40 items, found {len(corpus)}")
    groups = group_events(run_path)
    baseline_groups = group_events(BASELINE_PATH)
    if label == "attempt2":
        required = {corpus_id for corpus_id, item in corpus.items() if corpus_id in FROZEN or item["expect"] == "execute_with_defaults"}
        measured = sorted(set(groups) | required)
        missing_ids = sorted(required - set(groups))
    else:
        measured = sorted(corpus)
        missing_ids = sorted(set(corpus) - set(groups))
        if missing_ids:
            raise SystemExit(f"{run_path}: missing items {missing_ids}")
    attempt = build_per_items(groups, measured)
    baseline = build_per_items(baseline_groups, list(corpus))
    wave_layers = layer_summary(attempt, measured)
    baseline_layers = layer_summary(baseline, FROZEN)
    paired = paired_comparison(baseline, attempt, list(FROZEN))
    subset = intake_subset(corpus, groups, measured)
    block = closing_block(text_dir, corpus, attempt, load_checks(), closing_mode)
    spend = budget(attempt, json.loads(BASELINE_META_PATH.read_text(encoding="utf-8")))
    n_complete = sum(1 for item in attempt.values() if item["event_complete"])
    run_errors = sorted(corpus_id for corpus_id, item in attempt.items() if item["run_error"])
    core = core_gate(paired, n_complete, len(measured))
    gates = {
        "todo6_behaviour": dict(core),
        "todo8_closing_block": {"verdict": block["verdict"], "reason": f"{block['wilson95']['k']}/{block['wilson95']['n']} complete ({pct(block['wilson95']['rate'])}), threshold {pct(CLOSING_THRESHOLD)}, denominator={closing_mode}"},
        "todo9_no_regression": dict(core),
    }
    return {
        "label": label,
        "closing_mode": closing_mode,
        "run_path": run_path.name,
        "run_sha256": sha256_file(run_path),
        "texts_sha256": hashlib.sha256(b"".join(
            (text_dir / f"{corpus_id}.txt").read_bytes() for corpus_id in sorted(corpus) if (text_dir / f"{corpus_id}.txt").is_file()
        )).hexdigest(),
        "n_items": len(measured),
        "n_event_complete": n_complete,
        "n_run_error": len(run_errors),
        "run_error_ids": run_errors,
        "missing_ids": missing_ids,
        "n_missing": len(missing_ids),
        "n_records": sum(1 for line in run_path.read_text(encoding="utf-8").splitlines() if line.strip()),
        "distinct_run_ids": len({record["run_id"] for bucket in groups.values() for record in bucket.values()}),
        "run_window": {
            "start": min(record["ts"] for bucket in groups.values() for record in bucket.values()),
            "end": max(record["ts"] for bucket in groups.values() for record in bucket.values()),
        },
        "recovered": sum(
            1 for corpus_id in corpus
            if (text_dir / f"{corpus_id}.json").is_file()
            and json.loads((text_dir / f"{corpus_id}.json").read_text(encoding="utf-8")).get("recovered") is True
        ),
        "text_files": sum(1 for corpus_id in corpus if (text_dir / f"{corpus_id}.txt").is_file()),
        "layers_measured": wave_layers,
        "layers_baseline10": baseline_layers,
        "paired": paired,
        "intake": subset,
        "closing_block": block,
        "budget": spend,
        "gates": gates,
    }


def _layer_blocks(metrics: dict[str, Any]) -> list[str]:
    layers = metrics["layers_measured"]
    base = metrics["layers_baseline10"]
    paired = metrics["paired"]["layers"]
    lines = [
        f"| layer | k/{metrics['n_items']} | rate | Wilson 95% | baseline k/10 | baseline rate |",
        "|---|---|---|---|---|---|",
    ]
    for event in EVENTS:
        row = layers[event]
        left = base[event]
        lines.append(f"| `{event}` | {row['successes']}/{metrics['n_items']} | {pct(row['rate'])} | {ci(row)} | {left['successes']}/10 | {pct(left['rate'])} |")
    lines += [
        "",
        "| layer | baseline k/10 | attempt k/10 | delta | baseline-only | attempt-only | p | discordant ids |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for event in EVENTS:
        row = paired[event]
        discordant = ", ".join([*row["base_only"], *row["wave_only"]]) or "none"
        delta = f"{(row['wave1_successes'] - row['baseline_successes']) * 10:+.0f} pp"
        lines.append(f"| `{event}` | {row['baseline_successes']} | {row['wave1_successes']} | {delta} | {len(row['base_only'])} | {len(row['wave_only'])} | {row['mcnemar_exact_p']:.4f} | {discordant} |")
    return lines


def _intake_block(metrics: dict[str, Any]) -> list[str]:
    intake = metrics["intake"]
    return [
        "| item set | observed k/n | rate | Wilson 95% | threshold | verdict |",
        "|---|---|---|---|---|---|",
        f"| open-ended ∪ clarify_private | {intake['observed_k']}/{intake['n']} | {pct(intake['rate'])} | {ci(intake['wilson95'])} | <{pct(INTAKE_TRIGGER)} fires | `{intake['verdict']}` |",
        f"| all measured | {intake['all_observed_k']}/{metrics['n_items']} | {pct(intake['all_rate'])} | {ci(intake['all_wilson95'])} | — | descriptive |",
        "",
        "Subset ids: " + ", ".join(f"`{corpus_id}`" for corpus_id in intake["ids"]) + ".",
        "Observed: " + (", ".join(f"`{corpus_id}`" for corpus_id in intake["observed_ids"]) or "none") + ".",
        "Missing: " + (", ".join(f"`{corpus_id}`" for corpus_id in intake["missing_ids"]) or "none") + ".",
    ]


def _closing_block(metrics: dict[str, Any]) -> list[str]:
    block = metrics["closing_block"]
    lines = [
        f"Denominator (mode `{block['mode']}`): " + (
            "`expected_product != none` **and** observed route `execute_with_defaults`."
            if block["mode"] == "observed_route"
            else "`expected_product != none` **and** corpus `expect == execute_with_defaults`."
        ),
        "",
        "| id | expected product | route | complete | missing lines | chars |",
        "|---|---|---|---|---|---|",
    ]
    for row in block["rows"]:
        missing = "、".join(row["missing"]) or "—"
        lines.append(f"| `{row['id']}` | {row['expected_product']} | {row['route'] or '—'} | {'yes' if row['complete'] else 'no'} | {missing} | {row['text_chars']} |")
    lines += [
        "",
        f"Occurrence: **{block['wilson95']['k']}/{block['wilson95']['n']} = {pct(block['wilson95']['rate'])}** (Wilson 95% {ci(block['wilson95'])}); threshold {pct(CLOSING_THRESHOLD)} → **`{block['verdict']}`**.",
    ]
    return lines


def _budget_block(metrics: dict[str, Any]) -> list[str]:
    lines = [
        "| metric | baseline mean (n=10) | attempt mean (n=10) | change | threshold |",
        "|---|---|---|---|---|",
    ]
    for row in metrics["budget"].get("rows", []):
        lines.append(f"| {row['metric']} | {row['baseline_mean']} | {row['wave1_mean']} | {row['pct_change'] * 100:+.1f}% | ≤ +10% |")
    lines += ["", f"Budget verdict: **`{metrics['budget']['verdict']}`**."]
    if metrics["budget"].get("missing_ids"):
        lines.append("Missing token/wall values: " + ", ".join(f"`{corpus_id}`" for corpus_id in metrics["budget"]["missing_ids"]) + ".")
    return lines


def _gate_block(metrics: dict[str, Any]) -> list[str]:
    lines = ["| gate | verdict | reason |", "|---|---|---|"]
    for name, gate in metrics["gates"].items():
        lines.append(f"| `{name}` | `{gate['verdict']}` | {gate['reason']} |")
    return lines


def render_report(attempt1: dict[str, Any], attempt2: dict[str, Any]) -> str:
    lines = [
        "# Wave 1 behavioural gate — two attempts",
        "",
        "Generated by `tests/evals/intake/analyze_wave1.py --write`. `--check` regenerates and fails on drift.",
        "Attempt 1 is the archived measurement with the old route classifier and the observed-route closing",
        "denominator; attempt 2 is the fixed instrument. Attempt 2's verdicts are the gate.",
        "",
        f"- Attempt 1: `{attempt1['run_path']}` (sha256 `{attempt1['run_sha256'][:16]}…`), {attempt1['n_event_complete']}/{attempt1['n_items']} complete, {attempt1['n_run_error']} run errors.",
        f"- Attempt 2: `{attempt2['run_path']}` (sha256 `{attempt2['run_sha256'][:16]}…`), {attempt2['n_event_complete']}/{attempt2['n_items']} complete, {attempt2['n_run_error']} run errors, {attempt2['n_missing']} not measured; measured subset is the 25 affected items (c01-c10 + all execute_with_defaults).",
        "- Images: attempt 1 `opencode-serve:v3.11.0-onboarding-20261028-6e635820`; attempt 2 `opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1`.",
        "",
        "## 1. Attempt 1 (archived instrument, verdict fail)",
        "",
        f"Records: {attempt1['n_records']}; distinct run ids: {attempt1['distinct_run_ids']}; session-API recovery: {attempt1['recovered']}.",
        "",
        "### Four layers and paired comparison",
        "",
        *_layer_blocks(attempt1),
        "",
        "### intake_loaded",
        "",
        *_intake_block(attempt1),
        "",
        "### Closing block (old denominator, kept for audit)",
        "",
        *_closing_block(attempt1),
        "",
        "### Token and wall-clock budget",
        "",
        *_budget_block(attempt1),
        "",
        "### Gate verdicts (attempt 1)",
        "",
        *_gate_block(attempt1),
        "",
        "## 2. Attempt 2 (fixed instrument — final gate)",
        "",
        f"Records: {attempt2['n_records']}; distinct run ids: {attempt2['distinct_run_ids']}; session-API recovery: {attempt2['recovered']}.",
        "Not measured: " + (", ".join(f"`{corpus_id}`" for corpus_id in attempt2["missing_ids"]) or "none") + ".",
        "Run errors (300 s CLI cap; evidence `timeout after 300.0s`): " + (", ".join(f"`{corpus_id}`" for corpus_id in attempt2["run_error_ids"]) or "none") + ".",
        "",
        "### Four layers and paired comparison (primary evidence)",
        "",
        *_layer_blocks(attempt2),
        "",
        "### intake_loaded",
        "",
        *_intake_block(attempt2),
        "",
        f"Attempt 2's subset is partial; the trigger verdict remains attempt 1's `{attempt1['intake']['verdict']}` "
        f"({attempt1['intake']['observed_k']}/{attempt1['intake']['n']} = {pct(attempt1['intake']['rate'])}).",
        "",
        "### Closing block (fixed denominator)",
        "",
        *_closing_block(attempt2),
        "",
        "### Token and wall-clock budget",
        "",
        *_budget_block(attempt2),
        "",
        "### Gate verdicts (attempt 2 — final)",
        "",
        *_gate_block(attempt2),
        "",
        "## 3. Instrument changes",
        "",
        "- Route order is now refusal → clarification → execution → direct answer. c10 is a refusal with a numbered",
        "  \"do it yourself\" list; checking the choice list first mislabelled it. c05's options are bold/full-width",
        "  and were invisible to the old regex. c04 is a complete report whose numbered \"下一步\" list was read as a",
        "  question; the artifact guard (≥3 table rows or an artifact heading) now suppresses the clarification branch.",
        "- The closing denominator changed from observed route to corpus `expect == execute_with_defaults`, because",
        "  the old denominator punished correct clarifications (c06) and hid real artifacts. Attempt 1's old-denominator",
        "  rows remain above for audit.",
        "- Product-side: the open-ended branch now declares defaults and executes in the same turn (no question round);",
        "  the one-round ≤3-question budget is scoped to 待澄清型 private slots only. Scenarios A/B/B2/E/F now inline",
        "  the four closing lines with an explicit \"do not rewrite the date; always T-1\" instruction.",
        "",
        "## 4. Cleanup receipt",
        "",
        CLEANUP_RECEIPT,
        "",
        "## 5. Reproduction",
        "",
        "```bash",
        "cd OpencodeAgent",
        "conda run -n legonanobot python -m pytest tests/ -q",
        "conda run -n legonanobot python tests/evals/intake/analyze_wave1.py --check",
        "```",
        "",
        BLOCK_BEGIN,
        "```json",
        json.dumps({"attempt1": attempt1, "attempt2": attempt2}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        BLOCK_END,
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate results/wave1_report.md")
    parser.add_argument("--check", action="store_true", help="recompute and fail on drift")
    args = parser.parse_args(argv)
    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    if not ATTEMPT1_PATH.is_file():
        raise SystemExit(f"missing {ATTEMPT1_PATH}")
    if not FIX1_PATH.is_file():
        raise SystemExit(f"missing {FIX1_PATH}")
    attempt1 = build_metrics(ATTEMPT1_PATH, TEXT_DIR_1, "observed_route", "attempt1")
    attempt2 = build_metrics(FIX1_PATH, TEXT_DIR_FIX1, "corpus_expect", "attempt2")
    rendered = render_report(attempt1, attempt2)
    if args.write:
        REPORT_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {REPORT_PATH}")
        return 0
    if args.check:
        if not REPORT_PATH.is_file():
            print(f"--check: missing {REPORT_PATH}", file=sys.stderr)
            return 1
        if REPORT_PATH.read_text(encoding="utf-8") != rendered:
            print("--check: report drift detected; rerun analyze_wave1.py --write", file=sys.stderr)
            return 1
        print(f"--check OK ({REPORT_PATH})")
        return 0
    print(json.dumps({"attempt1": attempt1["gates"], "attempt2": attempt2["gates"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())