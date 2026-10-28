"""Offline contract tests for the closing block (todo 8).

The closing block is the artifact-first mechanism made visible: the user sees
what was assumed, what one alternative would change, what to do next, and the
compliance line. These tests pin the four lines, the disclaimer's T-1 cut-off,
markdown tolerance, and the CLI exit codes. No container, no model calls.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
CHECKS_PATH = TESTS_DIR / "evals" / "intake" / "checks.py"

_spec = importlib.util.spec_from_file_location("intake_checks", CHECKS_PATH)
assert _spec is not None and _spec.loader is not None
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

COMPLETE_BLOCK = """## 结论

贵州茅台当前估值处于近三年中位偏低区间。

**我假设了什么：** A 股、近 3 年窗口、稳健档（最大回撤 ≤15%）、Top 10。
**换一个会怎样：** 改为激进档（回撤 ≤40%）会扩大候选池，但回撤容忍同步上升。
**下一步能做什么：** ① 对 Top 3 做个股体检；② 用 Walk-Forward 复核；③ 登记周度监控。
**仅供研究，不构成投资建议；数据截止 T-1。**
"""

TEXT_ONLY_ANSWER = "因子是可观测的、能解释收益差异的特征，本身不是买卖信号。"


def _without(key: str) -> str:
    """Drop the line carrying ``key`` from the complete block."""
    prefix = checks.CLOSING_BLOCK_LINES[key]
    kept = [line for line in COMPLETE_BLOCK.splitlines() if prefix not in line]
    return "\n".join(kept) + "\n"


class TestCompleteBlock:
    def test_four_lines_pass(self):
        result = checks.assert_closing_block(COMPLETE_BLOCK)
        assert result["complete"] is True
        assert result["missing"] == []
        assert all(result[key] is True for key in checks.CLOSING_BLOCK_LINES)

    def test_constant_declares_the_four_prefixes(self):
        assert checks.CLOSING_BLOCK_LINES == {
            "assumptions": "我假设了什么",
            "alternative": "换一个会怎样",
            "next_steps": "下一步能做什么",
            "disclaimer": "仅供研究",
        }
        assert checks.DISCLAIMER_TOKENS == ("仅供研究", "不构成投资建议", "T-1")


class TestMissingLines:
    @pytest.mark.parametrize("key", list(checks.CLOSING_BLOCK_LINES))
    def test_each_line_is_required(self, key):
        result = checks.assert_closing_block(_without(key), required=False)
        assert result["complete"] is False
        assert result["missing"] == [checks.CLOSING_BLOCK_LABELS[key]]

        with pytest.raises(AssertionError) as excinfo:
            checks.assert_closing_block(_without(key))
        assert checks.CLOSING_BLOCK_LABELS[key] in str(excinfo.value)

    def test_two_missing_lines_are_both_reported(self):
        text = _without("assumptions")
        text = "\n".join(
            line for line in text.splitlines() if "下一步能做什么" not in line
        )
        result = checks.find_closing_block(text + "\n")
        assert result["missing"] == [
            "我假设了什么：",
            "下一步能做什么：",
        ]


class TestDecoration:
    def test_markdown_and_full_width_colon_variants_pass(self):
        text = """
### **我假设了什么**：A 股、近 3 年窗口
- **换一个会怎样**: 改为激进档会扩大候选池
1. `下一步能做什么`: 复核 Top 3
> **仅供研究，不构成投资建议；数据截止 T-1。**
"""
        result = checks.find_closing_block(text)
        assert result["complete"] is True, result

    def test_trailing_punctuation_and_half_width_colon_pass(self):
        text = """
我假设了什么: A 股。
换一个会怎样? 激进档候选更多。
下一步能做什么. 做个股体检。
仅供研究，不构成投资建议，数据截止 T-1。
"""
        assert checks.find_closing_block(text)["complete"] is True


class TestDisclaimerCompliance:
    def test_missing_t1_cut_off_fails(self):
        text = COMPLETE_BLOCK.replace("数据截止 T-1。", "")
        result = checks.find_closing_block(text)
        assert result["disclaimer"] is False
        assert result["complete"] is False
        assert checks.CLOSING_BLOCK_LABELS["disclaimer"] in result["missing"]

    def test_paraphrased_risk_warning_does_not_satisfy_the_line(self):
        text = COMPLETE_BLOCK.replace(
            "仅供研究，不构成投资建议；数据截止 T-1。",
            "仅供研究，不构成投资建议。投资有风险，入市需谨慎。",
        )
        result = checks.find_closing_block(text)
        assert result["disclaimer"] is False
        assert result["complete"] is False

    def test_disclaimer_tokens_may_appear_in_any_order(self):
        text = COMPLETE_BLOCK.replace(
            "仅供研究，不构成投资建议；数据截止 T-1。",
            "数据截止 T-1；不构成投资建议，仅供研究。",
        )
        assert checks.find_closing_block(text)["complete"] is True


class TestTextOnlyAnswers:
    def test_text_only_answer_records_absence_without_raising(self):
        assert checks.find_closing_block(TEXT_ONLY_ANSWER)["complete"] is False
        result = checks.assert_closing_block(TEXT_ONLY_ANSWER, required=False)
        assert result["complete"] is False
        assert len(result["missing"]) == 4

    def test_required_defaults_to_true(self):
        with pytest.raises(AssertionError):
            checks.assert_closing_block(TEXT_ONLY_ANSWER)


class TestCli:
    def test_good_fixture_exits_zero(self, tmp_path):
        fixture = tmp_path / "good.md"
        fixture.write_text(COMPLETE_BLOCK, encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(CHECKS_PATH), str(fixture)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["complete"] is True
        assert payload["missing"] == []

    def test_bad_fixture_exits_nonzero(self, tmp_path):
        fixture = tmp_path / "bad.md"
        fixture.write_text(_without("disclaimer"), encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(CHECKS_PATH), str(fixture)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 1, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["complete"] is False
        assert payload["disclaimer"] is False
        assert payload["missing"] == [checks.CLOSING_BLOCK_LABELS["disclaimer"]]

    def test_stdin_dash_is_accepted(self):
        completed = subprocess.run(
            [sys.executable, str(CHECKS_PATH), "-"],
            input=COMPLETE_BLOCK,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["complete"] is True

    def test_missing_argument_exits_two(self):
        completed = subprocess.run(
            [sys.executable, str(CHECKS_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 2
        assert "usage" in completed.stderr
