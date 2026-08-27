"""Offline tests for the A5/A6 quantitative helpers.

Pins the load-bearing behaviour of ``a5_token_accounting`` (the skill
double-exposure token-tax measure) and ``a6_coverage_assert`` (the
internal/external name-drift portability measure). Both are deterministic and
offline; these tests guard the invariants the A5/A6 "real improvement" verdicts
rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from src.evals.tool_selection import a5_token_accounting as a5  # noqa: E402
from src.evals.tool_selection import a6_coverage_assert as a6  # noqa: E402

EXPECTED_SKILL_COUNT = 90
EXPECTED_MCP_TOOL_COUNT = 74
EXPECTED_PRESET_COUNT = 30


# --------------------------------------------------------------------------- #
# A5 token-tax accounting.
# --------------------------------------------------------------------------- #
def test_a5_accounting_measures_all_bundled_skills() -> None:
    accounting = a5.build_accounting()
    assert accounting["skill_count"] == EXPECTED_SKILL_COUNT


def test_a5_disclosure_costs_are_positive_and_bounded() -> None:
    accounting = a5.build_accounting()
    for key in ("host_disclosure", "mcp_disclosure"):
        counts = accounting[key]
        assert counts["chars"] > 0
        assert counts["chars_over_3"] > 0
        if counts["tiktoken_cl100k"] is not None:
            assert counts["tiktoken_cl100k"] > 0
            assert counts["tiktoken_cl100k"] <= counts["chars"]


def test_a5_count_tokens_structure() -> None:
    counts = a5.count_tokens("hello world, 你好")
    assert set(counts) == {"chars", "tiktoken_cl100k", "chars_over_3"}
    assert counts["chars"] == len("hello world, 你好")


def test_a5_report_renders_both_disclosures() -> None:
    report = a5.render_report(a5.build_accounting())
    assert "host `get_descriptions`" in report
    assert "mcp `list_skills` JSON" in report


# --------------------------------------------------------------------------- #
# A6 portability coverage.
# --------------------------------------------------------------------------- #
def test_a6_mcp_surface_matches_pinned_tool_count() -> None:
    assert len(a6.mcp_surface_names()) == EXPECTED_MCP_TOOL_COUNT


def test_a6_drift_pairs_are_the_known_name_mismatches() -> None:
    pairs = a6.drift_pairs()
    assert pairs["pattern"] == "pattern_recognition"
    assert pairs["options_payoff"] == "analyze_options_payoff"
    assert pairs["options_pricing"] == "analyze_options"
    assert pairs["edit_file"] == "write_file"
    for internal, counterpart in pairs.items():
        assert internal != counterpart


def test_a6_classify_covers_every_class() -> None:
    mcp = {"get_market_data"}
    drift = {"pattern": "pattern_recognition"}
    internal = {"bash", "pattern"}
    assert a6.classify("get_market_data", mcp, drift, internal) == "mcp"
    assert a6.classify("pattern", mcp, drift, internal) == "drift"
    assert a6.classify("bash", mcp, drift, internal) == "internal-only"
    assert a6.classify("nope", mcp, drift, internal) == "unknown"


def test_a6_scan_presets_covers_all_bundled_presets() -> None:
    result = a6.scan_presets()
    assert result["preset_count"] == EXPECTED_PRESET_COUNT
    assert set(result["counts"]) == {"mcp", "drift", "internal-only", "unknown"}
    assert result["counts"]["drift"] > 0, "baseline must surface the drift risk"


def test_a6_scan_skill_docs_counts_only_unannotated_tool_refs() -> None:
    # The refined scan counts only UNANNOTATED backticked drift-name tool
    # references. Every bundled skill already annotates its drift-name refs
    # with the MCP counterpart (e.g. "use `write_file`（内部名 `edit_file`）"),
    # and bare "pattern" prose such as "candlestick pattern" is not a tool
    # reference — so the accurate unannotated count is zero.
    result = a6.scan_skill_docs()
    assert result["total_internal_refs"] == 0
    assert result["per_skill"] == {}


def test_a6_report_renders_both_sections() -> None:
    report = a6.render_report(a6.build_report_data())
    assert "Preset tool-reference resolvability" in report
    assert "Skill-doc internal-name references" in report
