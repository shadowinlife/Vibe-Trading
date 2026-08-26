"""Integrity and determinism guards for the tool-selection eval suite.

The suite under test (``src/evals/tool_selection``) is the quantitative gate
for description improvements: a frozen corpus snapshot of the pre-change
tool/skill descriptions is scored against a versioned query set, and later
description edits must prove their delta against that baseline. Two
properties make the gate meaningful, and this file pins both:

* **Asset integrity.** Every query must point at a capability that actually
  exists in the frozen corpus (a typo in ``expected.name`` would silently
  score an impossible target), every domain D01-D19 of the AUDIT taxonomy
  needs at least five entries, and every arbitration scenario named in the
  AUDIT §7.2 routing table must be exercised. The §7.2 id list is hardcoded
  below from a direct reading of the table — it is the contract the query
  set was built against.

* **Determinism.** Scoring reads only ``corpus_snapshot.yaml`` and
  ``queries.yaml`` and must produce byte-identical output on repeated runs;
  otherwise the baseline could not attribute score movement to description
  edits. Two in-process evaluations are compared here, and the CLI-level
  guarantee (two processes, identical stdout) is checked by diffing two
  interpreter runs.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from src.evals.tool_selection import run_eval  # noqa: E402

DOMAINS = tuple(f"D{i:02d}" for i in range(1, 20))

ENTRY_ID = re.compile(r"^D\d{2}-\d{3}$")
ARBITRATION_REF = re.compile(r"^(K|G|Q)\d+$")

# Arbitration ids literally referenced in the AUDIT §7.2 routing table
# (HARNESS_EVOLUTION_CAPABILITY_AUDIT.md, lines ~584-622). The query set
# must exercise every one of them.
AUDIT_7_2_REFS = (
    "K3", "K5", "K6", "K7", "K9", "K10", "K12", "K15", "K17", "K23", "K24", "K25",
    "G1", "G2", "G3", "G4", "G7", "G9",
    "Q1", "Q2", "Q4", "Q5", "Q6", "Q7", "Q8", "Q10", "Q12", "Q15", "Q17",
    "Q18", "Q19",
)

# Corpus sizes the frozen snapshot was captured with (74 MCP tools / 90
# bundled skills, pre-change descriptions). Rebuilding the snapshot is a
# deliberate baseline reset — update these pins in the same edit.
EXPECTED_TOOL_COUNT = 74
EXPECTED_SKILL_COUNT = 90

MIN_ENTRIES = 100
MIN_ENTRIES_PER_DOMAIN = 5


@functools.lru_cache(maxsize=1)
def _assets() -> tuple[dict, tuple]:
    """Load corpus and queries once for the whole test session.

    Returns:
        The parsed corpus snapshot and the entry tuple.
    """
    corpus, queries = run_eval.load_assets()
    return corpus, tuple(queries)


@functools.lru_cache(maxsize=1)
def _corpus_names() -> dict[str, set[str]]:
    """Return capability names in the corpus, keyed by kind.

    Returns:
        Mapping of ``tool``/``skill`` to the set of names in the snapshot.
    """
    corpus, _ = _assets()
    return {
        "tool": {row["name"] for row in corpus["tools"]},
        "skill": {row["name"] for row in corpus["skills"]},
    }


def _entry_ids() -> list[str]:
    """Return every entry id, for parametrized tests.

    Returns:
        Entry ids in file order.
    """
    _, queries = _assets()
    return [entry["id"] for entry in queries]


def _entry(entry_id: str) -> dict:
    """Return one entry by id.

    Args:
        entry_id: The entry's ``id`` field.

    Returns:
        The entry dict.
    """
    _, queries = _assets()
    return next(entry for entry in queries if entry["id"] == entry_id)


def test_query_set_has_at_least_100_entries() -> None:
    """The eval set is only a gate if it is wide enough to absorb churn."""
    _, queries = _assets()
    assert len(queries) >= MIN_ENTRIES, (
        f"queries.yaml ships {len(queries)} entries; the gate needs {MIN_ENTRIES}"
    )


def test_entry_ids_are_unique_and_well_formed() -> None:
    """Ids are the report's primary key; collisions or drift break tracing."""
    _, queries = _assets()
    ids = [entry["id"] for entry in queries]
    assert len(ids) == len(set(ids)), "duplicate entry ids exist"
    malformed = [i for i in ids if not ENTRY_ID.match(i)]
    assert not malformed, f"malformed entry ids: {malformed}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_domain_has_at_least_five_entries(domain: str) -> None:
    """Each AUDIT §1 domain must carry enough entries to show a delta."""
    _, queries = _assets()
    count = sum(1 for entry in queries if entry["domain"] == domain)
    assert count >= MIN_ENTRIES_PER_DOMAIN, (
        f"{domain} has {count} entries; needs {MIN_ENTRIES_PER_DOMAIN}"
    )


@pytest.mark.parametrize("entry_id", _entry_ids())
def test_entry_domain_is_valid(entry_id: str) -> None:
    """Every entry must belong to one of the 19 taxonomy domains."""
    assert _entry(entry_id)["domain"] in DOMAINS


@pytest.mark.parametrize("entry_id", _entry_ids())
def test_expected_target_exists_in_corpus(entry_id: str) -> None:
    """An expected target missing from the snapshot scores an impossible hit."""
    entry = _entry(entry_id)
    expected = entry["expected"]
    assert expected["kind"] in ("tool", "skill"), f"{entry_id}: bad kind"
    assert expected["name"] in _corpus_names()[expected["kind"]], (
        f"{entry_id}: expected {expected['kind']} '{expected['name']}' "
        f"is not in corpus_snapshot.yaml"
    )


@pytest.mark.parametrize(
    "entry_id",
    [i for i in _entry_ids() if _entry(i).get("negatives")],
)
def test_negative_names_exist_in_corpus(entry_id: str) -> None:
    """A negative that is not a real capability measures nothing."""
    entry = _entry(entry_id)
    names = _corpus_names()
    unknown = [
        name for name in entry["negatives"]
        if name not in names["tool"] and name not in names["skill"]
    ]
    assert not unknown, f"{entry_id}: unknown negatives {unknown}"


@pytest.mark.parametrize(
    "entry_id",
    [i for i in _entry_ids() if _entry(i).get("arbitration_ref")],
)
def test_arbitration_refs_are_well_formed(entry_id: str) -> None:
    """Refs point at AUDIT §3/§4/§5 rows — K#/G#/Q# or they are untraceable."""
    ref = _entry(entry_id)["arbitration_ref"]
    assert ARBITRATION_REF.match(ref), f"{entry_id}: bad arbitration_ref {ref!r}"


@pytest.mark.parametrize("ref", AUDIT_7_2_REFS)
def test_audit_7_2_arbitration_ids_are_covered(ref: str) -> None:
    """Every arbitration scenario in the AUDIT §7.2 table must be exercised."""
    _, queries = _assets()
    covered = any(entry.get("arbitration_ref") == ref for entry in queries)
    assert covered, f"AUDIT §7.2 scenario {ref} has no query entry"


@pytest.mark.parametrize(
    "entry_id",
    [i for i in _entry_ids() if _entry(i).get("arbitration_ref")],
)
def test_arbitration_entries_carry_negatives(entry_id: str) -> None:
    """An arbitration scenario without negatives cannot prove its boundary."""
    entry = _entry(entry_id)
    assert entry.get("negatives"), (
        f"{entry_id} encodes {entry['arbitration_ref']} but lists no negatives"
    )


def test_corpus_snapshot_is_internally_consistent() -> None:
    """Stated counts must equal the rows — a truncated snapshot is silent."""
    corpus, _ = _assets()
    assert corpus["tool_count"] == len(corpus["tools"])
    assert corpus["skill_count"] == len(corpus["skills"])
    assert all(row["description"] for row in corpus["tools"]), "empty tool description"
    assert all(row["description"] for row in corpus["skills"]), "empty skill description"


def test_corpus_snapshot_pins_the_baseline_counts() -> None:
    """The snapshot is frozen at the pre-change corpus size.

    Rebuilding the snapshot resets the baseline; doing it deliberately means
    updating this pin in the same edit.
    """
    corpus, _ = _assets()
    assert corpus["tool_count"] == EXPECTED_TOOL_COUNT
    assert corpus["skill_count"] == EXPECTED_SKILL_COUNT


def test_scoring_is_deterministic_in_process() -> None:
    """Two evaluations of the same assets must agree number for number."""
    corpus, queries = _assets()
    results_a, aggregates_a = run_eval.evaluate(corpus, list(queries))
    results_b, aggregates_b = run_eval.evaluate(corpus, list(queries))

    assert aggregates_a == aggregates_b
    assert [r.entry_id for r in results_a] == [r.entry_id for r in results_b]
    assert all(
        a.top1_hit == b.top1_hit and a.top3_hit == b.top3_hit
        and a.winner_name == b.winner_name and a.taxonomy == b.taxonomy
        for a, b in zip(results_a, results_b)
    )
    assert run_eval.render_report(results_a, aggregates_a, corpus) == \
        run_eval.render_report(results_b, aggregates_b, corpus)


def test_two_cli_runs_print_byte_identical_output() -> None:
    """The CI guarantee: two separate processes, byte-identical scores."""
    def run_once() -> str:
        proc = subprocess.run(
            [sys.executable, "-m", "src.evals.tool_selection.run_eval"],
            cwd=AGENT_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        return proc.stdout

    assert run_once() == run_once()
