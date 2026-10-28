"""Recompute the bounded Wave 1 probe and render ``results/wave1_frozen10_report.md``."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CORPUS_PATH = BASE_DIR / "corpus40_v2.jsonl"
CORRECTED_LABEL_IDS = ("c19", "c22")
PROBE_PATH = BASE_DIR / "results" / "wave1_probe.jsonl"
LANE_PATHS = (BASE_DIR / "results" / "wave1_probe_a.jsonl", BASE_DIR / "results" / "wave1_probe_b.jsonl")
TEXT_DIR = BASE_DIR / "results" / "wave1_texts_probe"
BASELINE_PATH = BASE_DIR / "results" / "baseline.jsonl"
BASELINE_META_PATH = BASE_DIR / "results" / "baseline_meta.json"
TWO_TURN_PATH = BASE_DIR / "results" / "two_turn_probe.md"
REPORT_PATH = BASE_DIR / "results" / "wave1_frozen10_report.md"
EVENTS = ("route_class", "intake_loaded", "question_called", "params_compiled")
FROZEN = tuple(f"c{number:02d}" for number in range(1, 11))
PROBE_IDS = FROZEN + ("c27",)
CLOSING_IDS = ("c01", "c02", "c03", "c04", "c07", "c27")
CLOSING_THRESHOLD = 0.90
BUDGET_MARGIN = 0.10
MIN_PAIRED_IDS = 8
MIN_ARTIFACT_DENOMINATOR = 5
IMAGE = "opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1"
BLOCK_BEGIN = "<!-- wave1-probe:begin -->"
BLOCK_END = "<!-- wave1-probe:end -->"
TWO_TURN_RE = re.compile(r"<!-- two-turn:json\s*(\{.*?\})\s*-->", re.DOTALL)

CLEANUP_RECEIPT = """\
Both compose projects (`wave1probe-a`, `wave1probe-b`) were brought down with
`docker compose -p <project> down --remove-orphans`; `rm -rf "$TMPDIR/wave1probe"`
removed the lane volumes and raw transcripts; `docker ps` returned no rows. No
image was rebuilt, tagged, or pushed."""


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wave1 = _load("wave1_stats", BASE_DIR / "analyze_wave1.py")
checks = _load("probe_checks", BASE_DIR / "checks.py")
runner = _load("probe_runner", BASE_DIR / "run.py")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_corrected_labels() -> None:
    """The probe ran after the c19/c22 correction; v2 is the corpus that holds it."""
    items = {item["id"]: item for item in load_jsonl(CORPUS_PATH)}
    if len(items) != 40:
        raise SystemExit(f"{CORPUS_PATH}: expected 40 items, found {len(items)}")
    relabelled = {corpus_id for corpus_id, item in items.items() if item.get("labels_version") == "v2"}
    if relabelled != set(CORRECTED_LABEL_IDS):
        raise SystemExit(f"{CORPUS_PATH}: labels_version=v2 records are {sorted(relabelled)}")
    for corpus_id in CORRECTED_LABEL_IDS:
        if items[corpus_id]["expect"] != "clarify_private":
            raise SystemExit(f"{CORPUS_PATH}: {corpus_id} must be clarify_private")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def ci(block: dict[str, Any]) -> str:
    return wave1.ci(block)


def verify_lanes(merged: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[str]]:
    lane_ids: dict[str, list[str]] = {}
    for path in LANE_PATHS:
        lane_events = wave1.group_events(path)
        lane_ids[path.name] = sorted(lane_events)
        for corpus_id, records in lane_events.items():
            if sorted(records) != sorted(merged.get(corpus_id, {})):
                raise SystemExit(f"{path}: {corpus_id} disagrees with the merged probe")
    overlap = sorted(set(lane_ids[LANE_PATHS[0].name]) & set(lane_ids[LANE_PATHS[1].name]))
    if overlap:
        raise SystemExit(f"lane shards overlap: {overlap}")
    if sorted(set(lane_ids[LANE_PATHS[0].name]) | set(lane_ids[LANE_PATHS[1].name])) != sorted(PROBE_IDS):
        raise SystemExit("lane shards do not cover the probe ids")
    return lane_ids


def _closing_note(complete: bool, artifact: bool) -> str:
    if complete:
        return ""
    if not artifact:
        return "no artifact (600 s cap, recovered session truncated mid-tool-loop); counted as a miss because the request ended without an artifact"
    return "emitted an artifact; contract-compliance miss (rewrote `数据截止 T-1` as a concrete date), not a routing miss"


def closing_block(probe: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for corpus_id in CLOSING_IDS:
        text_path = TEXT_DIR / f"{corpus_id}.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        result = checks.find_closing_block(text)
        artifact = runner._is_artifact(text)
        rows.append({
            "id": corpus_id,
            "route": probe[corpus_id]["route_observed"],
            "complete": result["complete"],
            "missing": result["missing"],
            "text_chars": len(text),
            "artifact": artifact,
            "note": _closing_note(result["complete"], artifact),
        })
    k = sum(1 for row in rows if row["complete"])
    n = len(rows)
    if n < MIN_ARTIFACT_DENOMINATOR:
        verdict = "inconclusive"
    elif k / n >= CLOSING_THRESHOLD:
        verdict = "pass"
    else:
        verdict = "fail"
    artifacts = [row["id"] for row in rows if row["artifact"]]
    artifact_k = sum(1 for row in rows if row["complete"] and row["id"] in artifacts)
    return {
        "ids": list(CLOSING_IDS), "rows": rows, "threshold": CLOSING_THRESHOLD,
        "wilson95": wave1.wilson(k, n), "verdict": verdict,
        "artifact_only": {"ids": artifacts, "k": artifact_k, "n": len(artifacts)},
    }


def budget(probe: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = json.loads(BASELINE_META_PATH.read_text(encoding="utf-8"))
    baseline_items = meta["per_item"]
    ids = [corpus_id for corpus_id in FROZEN if corpus_id in baseline_items]
    complete = [corpus_id for corpus_id in ids if probe[corpus_id]["tokens"] is not None and probe[corpus_id]["wall_s"] is not None]
    if len(complete) < len(ids):
        missing = [corpus_id for corpus_id in ids if corpus_id not in complete]
        return {"ids": ids, "n": len(complete), "verdict": "inconclusive", "reason": "missing token/wall values", "missing_ids": missing, "rows": []}
    base_tokens = sum(baseline_items[corpus_id]["tokens"] for corpus_id in ids) / len(ids)
    probe_tokens = sum(probe[corpus_id]["tokens"] for corpus_id in complete) / len(complete)
    base_wall = sum(baseline_items[corpus_id]["wall_s"] for corpus_id in ids) / len(ids)
    probe_wall = sum(probe[corpus_id]["wall_s"] for corpus_id in complete) / len(complete)
    token_change = (probe_tokens - base_tokens) / base_tokens
    wall_change = (probe_wall - base_wall) / base_wall
    rows = [
        {"metric": "tokens", "baseline_mean": round(base_tokens, 3), "probe_mean": round(probe_tokens, 3), "pct_change": round(token_change, 6)},
        {"metric": "wall_s", "baseline_mean": round(base_wall, 3), "probe_mean": round(probe_wall, 3), "pct_change": round(wall_change, 6)},
    ]
    verdict = "pass" if token_change <= BUDGET_MARGIN and wall_change <= BUDGET_MARGIN else "fail"
    return {"ids": ids, "n": len(complete), "margin": BUDGET_MARGIN, "rows": rows, "verdict": verdict}


def two_turn() -> dict[str, Any]:
    text = TWO_TURN_PATH.read_text(encoding="utf-8")
    match = TWO_TURN_RE.search(text)
    if not match:
        raise SystemExit(f"{TWO_TURN_PATH}: missing two-turn machine block")
    return json.loads(match.group(1))


def observation(records: dict[str, dict[str, Any]], event: str) -> tuple[Any, Any]:
    record = records.get(event)
    if record is None:
        return None, None
    return record.get("observed"), record.get("expected")


def agrees(groups: dict[str, dict[str, dict[str, Any]]], corpus_id: str, event: str) -> bool:
    observed, expected = observation(groups.get(corpus_id, {}), event)
    return observed is not None and observed == expected


def agreement_summary(groups: dict[str, dict[str, dict[str, Any]]], corpus_ids: list[str]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for event in EVENTS:
        k = sum(1 for corpus_id in corpus_ids if agrees(groups, corpus_id, event))
        summary[event] = {"successes": k, **wave1.wilson(k, len(corpus_ids))}
    return summary


def paired_agreement(
    baseline_groups: dict[str, dict[str, dict[str, Any]]],
    probe_groups: dict[str, dict[str, dict[str, Any]]],
    corpus_ids: list[str],
) -> dict[str, Any]:
    """Agreement = `observed == expected` on every layer, symmetrically for both arms."""
    layers: dict[str, dict[str, Any]] = {}
    for event in EVENTS:
        base_only: list[str] = []
        probe_only: list[str] = []
        base_true = probe_true = base_agree = probe_agree = 0
        for corpus_id in corpus_ids:
            base_observed, _ = observation(baseline_groups.get(corpus_id, {}), event)
            probe_observed, _ = observation(probe_groups.get(corpus_id, {}), event)
            base_hit = agrees(baseline_groups, corpus_id, event)
            probe_hit = agrees(probe_groups, corpus_id, event)
            base_true += int(base_observed is True)
            probe_true += int(probe_observed is True)
            base_agree += int(base_hit)
            probe_agree += int(probe_hit)
            if base_hit and not probe_hit:
                base_only.append(corpus_id)
            if probe_hit and not base_hit:
                probe_only.append(corpus_id)
        layers[event] = {
            # `route_class` is nominal, so an observed-true count does not exist for it.
            "baseline_observed_true": None if event == "route_class" else base_true,
            "probe_observed_true": None if event == "route_class" else probe_true,
            "baseline_agreement": base_agree,
            "probe_agreement": probe_agree,
            "base_only": base_only,
            "wave_only": probe_only,
            "discordant": len(base_only) + len(probe_only),
            "mcnemar_exact_p": wave1.mcnemar_exact(len(base_only), len(probe_only)),
        }
    return {"ids": corpus_ids, "layers": layers}


def behavioural_gate(agreement: dict[str, Any], n_complete: int) -> dict[str, Any]:
    if n_complete < MIN_PAIRED_IDS:
        return {"verdict": "inconclusive", "n": n_complete, "reason": f"only {n_complete}/{len(PROBE_IDS)} probe items carry all four layers (< {MIN_PAIRED_IDS})"}
    dropped = [event for event in EVENTS if agreement["layers"][event]["probe_agreement"] < agreement["layers"][event]["baseline_agreement"]]
    summary = ", ".join(
        f"{event} {agreement['layers'][event]['probe_agreement']}/10 vs {agreement['layers'][event]['baseline_agreement']}/10"
        for event in EVENTS
    )
    if dropped:
        return {"verdict": "fail", "n": n_complete, "reason": "paired agreement below baseline on " + ", ".join(dropped) + ": " + summary}
    return {"verdict": "pass", "n": n_complete, "reason": "no layer's paired agreement is below baseline: " + summary}


def build_metrics() -> dict[str, Any]:
    verify_corrected_labels()
    groups = wave1.group_events(PROBE_PATH)
    verify_lanes(groups)
    baseline_groups = wave1.group_events(BASELINE_PATH)
    probe = wave1.build_per_items(groups, list(PROBE_IDS))
    baseline = wave1.build_per_items(baseline_groups, list(FROZEN))
    paired = wave1.paired_comparison(baseline, probe, list(FROZEN))
    agreement = paired_agreement(baseline_groups, groups, list(FROZEN))
    n_complete = sum(1 for item in probe.values() if item["event_complete"])
    closing = closing_block(probe)
    spend = budget(probe)
    twoturn = two_turn()
    gates = {
        "todo6_behaviour": behavioural_gate(agreement, n_complete),
        "todo8_closing_block": {
            "verdict": closing["verdict"],
            "n": closing["wilson95"]["n"],
            "reason": f"{closing['wilson95']['k']}/{closing['wilson95']['n']} complete ({pct(closing['wilson95']['rate'])}), threshold {pct(CLOSING_THRESHOLD)}; denominator = frozen execute-contract ids (c01-c04, c07) + c27",
        },
        "todo9_no_regression": behavioural_gate(agreement, n_complete),
    }
    return {
        "image": IMAGE,
        "probe_ids": list(PROBE_IDS),
        "n_items": len(PROBE_IDS),
        "n_event_complete": n_complete,
        "n_records": sum(1 for line in PROBE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()),
        "probe_sha256": sha256_file(PROBE_PATH),
        "texts_sha256": hashlib.sha256(b"".join(
            (TEXT_DIR / f"{corpus_id}.txt").read_bytes() for corpus_id in PROBE_IDS if (TEXT_DIR / f"{corpus_id}.txt").is_file()
        )).hexdigest(),
        "text_files": sum(1 for corpus_id in PROBE_IDS if (TEXT_DIR / f"{corpus_id}.txt").is_file()),
        "recovered": sum(
            1 for corpus_id in PROBE_IDS
            if (TEXT_DIR / f"{corpus_id}.json").is_file()
            and json.loads((TEXT_DIR / f"{corpus_id}.json").read_text(encoding="utf-8")).get("recovered") is True
        ),
        "timeout_recovered": [
            corpus_id for corpus_id in PROBE_IDS
            if any(
                record.get("evidence", "").startswith(runner.RECOVERY_PREFIX + "timeout_recovered;")
                for record in groups.get(corpus_id, {}).values()
            )
        ],
        "layers_probe11": {event: wave1.layer_summary(probe, list(PROBE_IDS))[event] for event in EVENTS},
        "agreement_probe11": agreement_summary(groups, list(PROBE_IDS)),
        "paired": paired,
        "paired_agreement": agreement,
        "closing_block": closing,
        "budget": spend,
        "two_turn": twoturn,
        "gates": gates,
    }


def render_report(m: dict[str, Any]) -> str:
    agreement = m["paired_agreement"]["layers"]
    closing = m["closing_block"]
    spend = m["budget"]
    two = m["two_turn"]
    lines = [
        "# Wave 1 bounded behavioural probe — frozen ten + c27",
        "",
        "Generated by `tests/evals/intake/analyze_probe.py --write`; `--check` regenerates and fails on drift.",
        f"Image: `{m['image']}`. This is an **11-item bounded probe, not the 40-item gate**.",
        "",
        "## 0. Label correction found by the run",
        "",
        "`c19` (`看看我的账户组合近三年表现怎么样`, 组合体检) and `c22`"
        " (`帮我复盘一下近三个月的交易记录，看看盈亏`, 交易复盘) were labelled"
        " `execute_with_defaults` although both require user-private holdings / 交割单"
        " that no tool can obtain. They are now `clarify_private` in both"
        " `gen_corpus40.py` and `corpus40.jsonl`; regeneration keeps `head -10` byte-identical"
        " to `corpus.jsonl` and the prompts unchanged. **This is a label correction, not a"
        " threshold change.**",
        "",
        "## 1. Protocol",
        "",
        f"- Items: {', '.join(f'`{corpus_id}`' for corpus_id in m['probe_ids'])}.",
        "- Lanes: `wave1probe-a` = c01-c06 (host port 4097), `wave1probe-b` = c07-c10 + c27 (port 4099);",
        "  each lane 3 G / 1.2 CPU, private project, container, port and volume directories.",
        "- Readiness: authenticated `GET /config?directory=/workspace` returned 200 (47 s / 10 s).",
        "- Per-item timeout 600 s; a timed-out CLI is followed by `recover_after_timeout`",
        "  (poll the session until idle, 120 s budget), so a killed CLI does not lose a running session.",
        f"- Records: {m['n_records']} in `results/wave1_probe.jsonl` ({m['n_event_complete']}/{m['n_items']} items carry all four layers);",
        f"  texts: {m['text_files']}/{m['n_items']} in `results/wave1_texts_probe/`.",
        f"- Session-API transcripts: {m['recovered']}/{m['n_items']}; timeout recovery poll used for: "
        + (", ".join(f"`{corpus_id}`" for corpus_id in m["timeout_recovered"]) or "none") + ".",
        "",
        "## 2. Paired comparison on the frozen ten",
        "",
        "Criterion: the gate metric is **agreement** = `observed == expected` on every layer,"
        " computed identically for both arms from the committed JSONL. The observed-true column"
        " is kept beside it so the previous revision's numbers stay auditable.",
        "observed-true is not a gate metric on this corpus: `params_compiled` is expected FALSE"
        " for clarify and direct-answer items, so scoring `observed is True` rewards"
        " over-execution — the baseline's 9/10 there is its defect, not its quality.",
        "",
        "| layer | baseline observed-true | probe observed-true | baseline agreement | probe agreement | delta (agreement) | baseline-only | probe-only | discordant | exact McNemar p |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for event in EVENTS:
        row = agreement[event]
        delta = f"{(row['probe_agreement'] - row['baseline_agreement']) * 10:+.0f} pp"
        observed_true = [
            "—" if row[key] is None else str(row[key])
            for key in ("baseline_observed_true", "probe_observed_true")
        ]
        lines.append(
            f"| `{event}` | {observed_true[0]} | {observed_true[1]} | "
            f"{row['baseline_agreement']} | {row['probe_agreement']} | {delta} | "
            f"{', '.join(row['base_only']) or '—'} | {', '.join(row['wave_only']) or '—'} | "
            f"{row['discordant']} | {row['mcnemar_exact_p']:.4f} |"
        )
    lines += [
        "",
        "`route_class` is nominal (`observed` is a class name, not a boolean), so it has no"
        " observed-true count; its 6 → 7 was already an agreement count in the previous revision.",
        "",
        "Agreement on all 11 items (descriptive, same definition as the gate): "
        + "; ".join(
            f"`{event}` {m['agreement_probe11'][event]['successes']}/11 ({pct(m['agreement_probe11'][event]['rate'])})"
            for event in EVENTS
        ) + ". Observed-true on all 11 items (descriptive, not gated): "
        + "; ".join(
            f"`{event}` {m['layers_probe11'][event]['successes']}/11 ({pct(m['layers_probe11'][event]['rate'])})"
            for event in EVENTS
        ) + ".",
        "",
        "## 3. Closing block",
        "",
        "Denominator: the frozen execute-contract items `c01,c02,c03,c04,c07` plus `c27`"
        " (6 ids); a second denominator counts only the ids that emitted an artifact."
        " Each row is checked with `checks.find_closing_block` on the stored answer text.",
        "",
        "| id | observed route | complete | missing lines | chars | note |",
        "|---|---|---|---|---|---|",
    ]
    for row in closing["rows"]:
        lines.append(
            f"| `{row['id']}` | {row['route'] or '—'} | {'yes' if row['complete'] else 'no'} | "
            f"{'、'.join(row['missing']) or '—'} | {row['text_chars']} | {row['note'] or '—'} |"
        )
    artifact = closing["artifact_only"]
    lines += [
        "",
        f"Contract denominator: **{closing['wilson95']['k']}/{closing['wilson95']['n']} = {pct(closing['wilson95']['rate'])}** "
        f"(Wilson 95% {ci(closing['wilson95'])}). "
        f"Emitted-artifact denominator (`{', '.join(artifact['ids'])}`): "
        f"**{artifact['k']}/{artifact['n']} = {pct(artifact['k'] / artifact['n'])}**. "
        f"Both are below the {pct(CLOSING_THRESHOLD)} threshold → **`{closing['verdict']}`**.",
        "",
        "`c02` produced **no artifact** — the 600 s cap fired and the recovered session is"
        " truncated mid-tool-loop — and it counts as a miss because the contract says an"
        " artifact-bearing request must end with the block, and this request ended without an"
        " artifact.",
        "`c27` **emitted an artifact** but rewrote `数据截止 T-1` into a concrete date"
        " (`数据截止 2026-10-28`), so it is a **contract-compliance miss**, not a routing miss;"
        " all four canonical lines are missing.",
        "",
        "## 4. Two-turn clarification probe",
        "",
        f"- Session `{two['session_id']}`, continued with `opencode run --session <id> ... \"1A 2B 3C\"`"
        f" (exit 0, {two['turn2_wall_s']} s).",
        f"- Turn 1: {two['turn1_questions']} questions, {two['turn1_options_per_question']} options each,"
        f" escape hatch {'present' if two['turn1_escape_hatch'] else 'absent'}; `question` tool calls = 0"
        " (headless handler denies the tool; text options are used).",
        f"- Turn 2: artifact **{'yes' if two['artifact_path'] else 'no'}** at `{two['artifact_path']}`"
        f" ({two['artifact_bytes']} bytes, {two['artifact_lines']} lines); closing block complete ="
        f" **{str(two['closing_complete']).lower()}**"
        + (f" (missing: {'、'.join(two['missing'])})" if two["missing"] else "") + ".",
        f"- User turns used: **{two['user_turns']}** (requirement: 2). Raw tool names, turn 2: "
        + ", ".join(f"`{name}`" for name in two["turn2_tools"]) + ".",
        f"- Continuation supported on this harness: **{str(two['continuation_supported']).lower()}**.",
        "",
        "## 5. Token and wall-clock budget",
        "",
        "| metric | baseline mean (n=10) | probe mean (n=10) | change | threshold |",
        "|---|---|---|---|---|",
    ]
    for row in spend.get("rows", []):
        lines.append(f"| {row['metric']} | {row['baseline_mean']} | {row['probe_mean']} | {row['pct_change'] * 100:+.1f}% | ≤ +10% |")
    lines += ["", f"Budget verdict: **`{spend['verdict']}`**."]
    if spend.get("missing_ids"):
        lines.append("Missing token/wall values: " + ", ".join(f"`{corpus_id}`" for corpus_id in spend["missing_ids"]) + ".")
    lines += [
        "",
        "## 6. Gate verdicts",
        "",
        "| todo | verdict | n | exact criterion | reason |",
        "|---|---|---|---|---|",
        f"| `todo6_behaviour` | `{m['gates']['todo6_behaviour']['verdict']}` | {m['gates']['todo6_behaviour']['n']} | "
        f"no layer's paired point estimate below baseline on the 10 frozen ids; `inconclusive` if <{MIN_PAIRED_IDS} ids have all four layers | "
        f"{m['gates']['todo6_behaviour']['reason']} |",
        f"| `todo8_closing_block` | `{m['gates']['todo8_closing_block']['verdict']}` | {m['gates']['todo8_closing_block']['n']} | "
        f"complete/denominator ≥ {pct(CLOSING_THRESHOLD)} with denominator ≥ {MIN_ARTIFACT_DENOMINATOR}; `inconclusive` below that denominator | "
        f"{m['gates']['todo8_closing_block']['reason']} |",
        f"| `todo9_no_regression` | `{m['gates']['todo9_no_regression']['verdict']}` | {m['gates']['todo9_no_regression']['n']} | "
        f"same paired criterion as `todo6_behaviour` | {m['gates']['todo9_no_regression']['reason']} |",
        "",
        "## 7. Limitations",
        "",
        "This is an 11-item bounded probe, not the 40-item gate: the paired table uses the"
        " frozen ten that also anchor the baseline, and `c27` is added only for the closing"
        " and timeout-recovery evidence. McNemar p-values at n=10 have low power and are"
        " reported for audit, not as significance claims. Route defects visible in this probe"
        " (c06 executed instead of clarifying; c09 executed a prediction instead of answering;"
        " c10/c27 misrouted by the refusal-first classifier) are recorded, not repaired here.",
        "",
        "## 8. Cleanup receipt",
        "",
        CLEANUP_RECEIPT,
        "",
        "## 9. Reproduction",
        "",
        "```bash",
        "cd OpencodeAgent",
        "conda run -n legonanobot python -m pytest tests/ -q",
        "conda run -n legonanobot python -m pytest tests/evals/intake -q",
        "conda run -n legonanobot python tests/evals/intake/analyze_probe.py --check",
        "wc -l tests/evals/intake/results/wave1_probe.jsonl",
        "```",
        "",
        BLOCK_BEGIN,
        "```json",
        json.dumps(m, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        BLOCK_END,
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate results/wave1_frozen10_report.md")
    parser.add_argument("--check", action="store_true", help="recompute and fail on drift")
    args = parser.parse_args(argv)
    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    for path in (PROBE_PATH, TEXT_DIR, BASELINE_PATH, BASELINE_META_PATH, TWO_TURN_PATH):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    metrics = build_metrics()
    rendered = render_report(metrics)
    if args.write:
        REPORT_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {REPORT_PATH}")
        return 0
    if args.check:
        if not REPORT_PATH.is_file():
            print(f"--check: missing {REPORT_PATH}", file=sys.stderr)
            return 1
        if REPORT_PATH.read_text(encoding="utf-8") != rendered:
            print("--check: report drift detected; rerun analyze_probe.py --write", file=sys.stderr)
            return 1
        print(f"--check OK ({REPORT_PATH})")
        return 0
    print(json.dumps(metrics["gates"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
