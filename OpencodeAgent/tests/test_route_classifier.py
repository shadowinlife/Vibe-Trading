"""Offline regression fixtures for the Wave 1 route classifier.

The committed attempt-1 texts are the evidence for three defects: c10 is a
refusal with a numbered "do it yourself" list, c05 is a clarification whose
options are bold/full-width, and c04 is an artifact with numbered follow-ups.
These tests replay the six pre-registered ids through ``derive_route``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
RUN_PY = BASE_DIR / "tests" / "evals" / "intake" / "run.py"
TEXT_DIR = BASE_DIR / "tests" / "evals" / "intake" / "results" / "wave1_texts"
VT = [{"name": "vibe-trading_get_market_data", "args": {"code": "600519"}}]
CASES = (
    ("c04", "execute_with_defaults", [{"name": "vibe-trading_backtest", "args": {"run_dir": "x"}}]),
    ("c05", "clarify_private", VT),
    ("c10", "refuse", []),
    ("c02", "clarify_private", []),
    ("c03", "clarify_private", VT),
    ("c08", "direct_answer", []),
)


def _route_module():
    spec = importlib.util.spec_from_file_location("wave1_route", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("corpus_id,expected,calls", CASES)
def test_committed_text_has_expected_route(corpus_id, expected, calls):
    path = TEXT_DIR / f"{corpus_id}.txt"
    if not path.is_file():
        pytest.skip(f"attempt-1 text missing: {path}")
    stream = {"calls": calls, "text": path.read_text(encoding="utf-8"), "tokens": None}
    assert _route_module().derive_route(stream)[0] == expected


def test_refusal_wins_over_numbered_menu():
    path = TEXT_DIR / "c10.txt"
    if not path.is_file():
        pytest.skip(f"attempt-1 text missing: {path}")
    module = _route_module()
    text = path.read_text(encoding="utf-8")
    assert len(module._choice_lines(text)) >= 2
    assert module.derive_route({"calls": [], "text": text, "tokens": None})[0] == "refuse"


def test_artifact_guard_keeps_numbered_followups_out_of_clarify():
    path = TEXT_DIR / "c04.txt"
    if not path.is_file():
        pytest.skip(f"attempt-1 text missing: {path}")
    module = _route_module()
    text = path.read_text(encoding="utf-8")
    assert module._is_artifact(text)
    assert len(module._choice_lines(text)) >= 2
    stream = {"calls": [{"name": "vibe-trading_backtest", "args": {"run_dir": "x"}}], "text": text, "tokens": None}
    assert module.derive_route(stream)[0] == "execute_with_defaults"