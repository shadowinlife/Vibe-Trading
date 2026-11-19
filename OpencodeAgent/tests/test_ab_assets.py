"""Offline contract tests for the ask-first vs artifact-first A/B apparatus.

These tests never start a container and never call a model. They prove that the
corpus is generated (not hand-written), the decision rule is pre-registered with
``n_min = 30``, the control snapshot digest is wired into the spec, the report
carries the verdict and the two human-review dimensions, and ``--check`` passes.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB_DIR = ROOT / "tests" / "evals" / "intake" / "ab"
CORPUS = AB_DIR / "corpus_ab.jsonl"
SPEC = AB_DIR / "judge_spec.json"
REPORT = AB_DIR / "ab_report.md"
SNAPSHOT = (
    ROOT / "tests" / "evals" / "intake" / "fixtures" / "ask_first_protocol_snapshot.md"
)


def load_corpus() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AB_DIR / name), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_corpus_has_at_least_30_open_items_and_three_sources() -> None:
    items = load_corpus()
    assert len(items) >= 30
    assert {item["source"] for item in items} == {
        "frozen",
        "extended_generated",
        "real_label_guided",
    }
    assert [item["id"] for item in items] == [
        f"a{index:02d}" for index in range(1, len(items) + 1)
    ]
    assert all(item["open_ended"] is True for item in items)
    assert all(item["expected_artifact"] not in (None, "", "none") for item in items)
    assert all(item["expected_product"] == item["expected_artifact"] for item in items)
    assert (
        len({item["prompt"] for item in items}) >= 30
    ), "duplicate prompts would pad the sample"


def test_corpus_regeneration_matches() -> None:
    result = run_script("gen_corpus_ab.py", "--check")
    assert result.returncode == 0, result.stderr
    assert "regeneration matches" in result.stdout


def test_judge_spec_is_preregistered() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    for key in (
        "schema_version",
        "spec_id",
        "arms",
        "corpus",
        "success",
        "failure",
        "manual_param_rate",
        "primary_test",
        "n_min",
        "verdict_rule",
        "verdicts",
        "required_n",
        "human_review",
        "rollback_if_ask_first",
    ):
        assert key in spec, f"judge_spec.json is missing {key!r}"
    assert spec["n_min"] == 30
    assert "INCONCLUSIVE" in spec["verdict_rule"]
    assert spec["primary_test"]["name"].lower().startswith("exact mcnemar")
    assert "first usable artifact" in spec["success"]["definition"]
    assert "parameters retrievable" in spec["success"]["definition"]
    assert "manual parameter completion" in spec["failure"]["definition"]
    assert spec["manual_param_rate"]["reported_separately"] is True
    assert spec["arms"]["control"]["image"] == "opencode-serve:v3.10.0-mymain"
    assert (
        spec["arms"]["experiment"]["image"]
        == "opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1"
    )
    dimensions = {entry["dimension"]: entry for entry in spec["human_review"]}
    assert set(dimensions) == {"参数改动是否合理", "first usable artifact 是否真可用"}
    assert all(
        entry["flag"] == "human-review-needed" and entry["scored"] is False
        for entry in dimensions.values()
    )


def test_control_snapshot_digest_is_wired_into_the_spec() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    digest = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    assert digest == spec["arms"]["control"]["protocol_snapshot_sha256"]
    blocks = spec["arms"]["control"]["protocol_blocks"]
    assert set(blocks) == {"agents-protocol", "research-scenarios-scenario-c"}
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", block["sha256"]) for block in blocks.values()
    )


def test_report_contains_verdict_pilot_counts_and_human_review() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "`INCONCLUSIVE`" in text
    assert "No direction is claimed" in text
    assert "human-review-needed" in text
    assert "参数改动是否合理" in text
    assert "first usable artifact 是否真可用" in text
    assert "run_ab.py" in text
    assert "n_min = 30" in text
    assert "Required N" in text
    assert "7/7" in text and "6/7" in text and "1/7" in text
    assert "two_turn_probe.md" in text


def test_ab_analysis_check_passes() -> None:
    result = run_script("ab_analysis.py", "--check")
    assert result.returncode == 0, result.stderr
    assert "up to date" in result.stdout
    assert "verdict=INCONCLUSIVE" in result.stdout


def test_run_ab_dry_run_is_offline_and_lists_every_item() -> None:
    result = run_script("run_ab.py", "--dry-run")
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == len(load_corpus())
