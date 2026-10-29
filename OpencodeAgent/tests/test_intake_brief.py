"""Offline contract tests for the intake brief (todo 12).

These pin the schema, exercise the dependency-free validator, and — the
anti-drift check — parse the compile table out of ``skills/intake/SKILL.md``
and assert every tool name exists verbatim in the runtime ``tools/list``
snapshot, not the three-entry deny-list in ``config/vibe-trading-tools.json``.
The three happy cases are the schema's own ``examples``. No container, no
model calls.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "intake" / "SKILL.md"
SCHEMA_PATH = ROOT / "tests" / "evals" / "intake" / "brief.schema.json"
FIXTURE_PATH = ROOT / "tests" / "evals" / "intake" / "fixtures" / "tool_list.json"
COMPILE_HEADING = "## 选项 → 工具参数编译表"
DEFAULTS_HEADING = "## 槽位默认值表"

_spec = importlib.util.spec_from_file_location("brief_validation", ROOT / "brief_validation.py")
assert _spec is not None and _spec.loader is not None
bv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bv)

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
TOOL_NAMES = {entry["name"] for entry in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))}
TOOL_SPECS = SCHEMA["$defs"]["tool_specs"]
PUBLIC_SLOTS = ("market", "horizon", "risk", "style", "universe", "position", "output")
EXAMPLES = {example["request_class"]: example for example in SCHEMA["examples"]}
DEFAULTS_ONLY = EXAMPLES["execute_with_defaults"]


class TestHappyBriefs:
    def test_schema_ships_the_three_examples(self):
        assert set(EXAMPLES) == {"execute_with_defaults", "clarify_private", "direct_answer"}

    def test_defaults_only_brief_is_valid(self):
        assert bv.validate(DEFAULTS_ONLY) == []

    def test_clarify_then_execute_brief_is_valid(self):
        assert bv.validate(EXAMPLES["clarify_private"]) == []

    def test_direct_answer_with_empty_tool_plan_is_valid(self):
        assert EXAMPLES["direct_answer"]["tool_plan"] == []
        assert bv.validate(EXAMPLES["direct_answer"]) == []


class TestMalformedBriefs:
    def test_default_slot_without_assumptions_is_rejected(self):
        brief = copy.deepcopy(DEFAULTS_ONLY)
        brief["assumptions"] = []
        assert any("assumptions" in error for error in bv.validate(brief))

    def test_tool_absent_from_snapshot_is_rejected(self):
        brief = copy.deepcopy(DEFAULTS_ONLY)
        brief["tool_plan"][0]["tool"] = "vibe-trading_backtest"
        errors = bv.validate(brief)
        assert any("tool_plan[0].tool" in e and "vibe-trading_backtest" in e for e in errors), errors
        shadowed = bv.validate(DEFAULTS_ONLY, tool_names=TOOL_NAMES - {"screen_market"})
        assert any("tools/list snapshot" in error for error in shadowed), shadowed


_PIPE_RE = re.compile(r"(?<!\\)\|")


def _split_row(line: str) -> list[str]:
    """Split a markdown row on unescaped pipes; ``\\|`` is literal cell text."""
    text = line.strip()
    cells = _PIPE_RE.split(text)
    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|"):
        cells = cells[:-1]
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _skill_rows(heading: str) -> list[dict[str, str]]:
    lines = SKILL_PATH.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    header: list[str] = []
    rows: list[dict[str, str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("# "):
            break
        if not line.lstrip().startswith("|"):
            if header:
                break
            continue
        cells = _split_row(line)
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if not header:
            header = cells
        else:
            rows.append(dict(zip(header, cells)))
    assert header, f"no table under {heading}"
    return rows


class TestCompileTableAntiDrift:
    def test_every_compile_table_tool_exists_in_the_snapshot(self):
        # The row parser splits on unescaped pipes only (``\|`` stays in-cell).
        assert _split_row("| a | b | c | d \\| e |") == ["a", "b", "c", "d | e"]
        rows = _skill_rows(COMPILE_HEADING)
        assert rows, "compile table not found"
        assert {row["工具名"] for row in rows} <= TOOL_NAMES

    def test_every_declared_parameter_and_value_is_inside_its_contract(self):
        problems = []
        for row in _skill_rows(COMPILE_HEADING):
            spec = bv.resolve_arg_spec(TOOL_SPECS[row["工具名"]]["args"], row["参数"])
            if spec is None:
                problems.append((row["工具名"], row["参数"], "undeclared"))
            elif spec.get("enum") and row["值"] not in spec["enum"]:
                problems.append((row["工具名"], row["参数"], row["值"]))
        assert not problems, problems

    def test_schema_tool_enum_is_exactly_the_compile_table(self):
        enum = set(SCHEMA["$defs"]["tool_call"]["properties"]["tool"]["enum"])
        assert enum == {row["工具名"] for row in _skill_rows(COMPILE_HEADING)}
        assert enum <= TOOL_NAMES


class TestSchemaContract:
    def test_contract_shape_requests_and_slot_keys_are_pinned(self):
        assert SCHEMA["additionalProperties"] is False
        assert set(SCHEMA["required"]) == {
            "intake_version",
            "request_class",
            "slots",
            "tool_plan",
            "assumptions",
            "closing_block",
        }
        assert SCHEMA["$defs"]["slot"]["additionalProperties"] is False
        assert SCHEMA["$defs"]["tool_call"]["additionalProperties"] is False
        assert set(SCHEMA["properties"]["request_class"]["enum"]) == set(EXAMPLES) | {"refuse"}
        assert set(SCHEMA["properties"]["slots"]["propertyNames"]["enum"]) == set(PUBLIC_SLOTS) | {
            "cost_basis",
            "position_size",
        }

    def test_defaulted_slot_rule_lives_in_the_schema(self):
        then = SCHEMA["allOf"][0]["then"]["properties"]["assumptions"]
        assert then["minItems"] == 1


class TestDefaultTableRuling:
    def test_only_private_slots_are_asked(self):
        rows = _skill_rows(DEFAULTS_HEADING)
        asked = {row["槽位"] for row in rows if "提问（私有" in row["何时提问"]}
        assert asked == {"成本价", "仓位规模"}
        assert all("不提问" in row["何时提问"] for row in rows if row["槽位"] not in asked)

    def test_public_defaults_and_inconclusive_ruling_are_stated(self):
        by_slot = {row["槽位"]: row["默认值"] for row in _skill_rows(DEFAULTS_HEADING)}
        assert by_slot["市场"] == "A股"
        assert by_slot["期限"] == "1-3 个月"
        assert "10%" in by_slot["风险"]
        assert by_slot["风格"] == "均衡"
        assert by_slot["持仓"] == "空仓"
        assert by_slot["产出"] == "结论 + 清单"
        body = SKILL_PATH.read_text(encoding="utf-8")
        assert "INCONCLUSIVE" in body and "无证据支持提问" in body
