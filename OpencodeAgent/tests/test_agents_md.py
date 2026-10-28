"""Structural contract for the AGENTS.md ↔ intake split.

Todo 6 moved the clarification protocol out of the always-loaded
``AGENTS.md`` and into ``skills/intake/SKILL.md``. Without assertions the
migration can silently regress into the double-protocol state it removed:
two turn-budget rules, a router table that drifts from the skill, or
scenario C and the intake skill pointing at each other.

Everything here is offline and structural — no container, no model calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ``tests/`` is not a package, so pytest's prepend import mode puts this
# directory on ``sys.path`` and the sibling module is importable by name.
from test_config_render import AGENTS_MD_MAX_LINES

OPENCODE_AGENT_DIR = Path(__file__).resolve().parents[1]
AGENTS_MD_PATH = OPENCODE_AGENT_DIR / "AGENTS.md"
SKILLS_DIR = OPENCODE_AGENT_DIR / "skills"
INTAKE_SKILL_PATH = SKILLS_DIR / "intake" / "SKILL.md"
SCENARIOS_SKILL_PATH = SKILLS_DIR / "research-scenarios" / "SKILL.md"

# The protocol budget is the single rule this migration consolidated. Every
# wording that shipped before (or that a careless reword would reintroduce)
# is listed, so a paraphrase still trips the "one rule, one file" assertion.
TURN_BUDGET_MARKERS = (
    "最多 1 轮",
    "≤3 问",
    "绝不重复问第二轮",
    "绝不发起第二轮",
    "追加 1 轮",
    "一次问清",
)

# The three markers that specifically encode the layered budget, asserted to
# be present in the intake skill so the scan above cannot pass vacuously.
CANONICAL_BUDGET_MARKERS = ("最多 1 轮", "≤3 问", "绝不重复问第二轮")

ROUTER_ROWS = (
    "| **明确可执行** |",
    "| **开放型** |",
    "| **待澄清型** |",
    "| **宏观型** |",
)

PROTOCOL_SECTIONS = (
    "## 触发条件",
    "## 分流边界",
    "## 漏斗维度",
    "## 槽位澄清规则",
    "## 分层轮次预算",
    "## 逃生门",
    "## 参数编译规则",
    "## 执行承诺",
    "## 加载机制",
)

INTAKE_MAX_LINES = 180


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section_body(text: str, heading_prefix: str) -> list[str]:
    """Non-empty lines between ``heading_prefix`` and the next heading."""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(heading_prefix)),
        None,
    )
    if start is None:
        raise AssertionError(f"heading not found: {heading_prefix}")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        if line.strip():
            body.append(line)
    return body


def _turn_budget_files() -> dict[str, list[str]]:
    candidates = [AGENTS_MD_PATH, *sorted(SKILLS_DIR.rglob("SKILL.md"))]
    hits: dict[str, list[str]] = {}
    for path in candidates:
        text = _read(path)
        found = [marker for marker in TURN_BUDGET_MARKERS if marker in text]
        if found:
            hits[path.relative_to(OPENCODE_AGENT_DIR).as_posix()] = found
    return hits


class TestLineBudget:
    def test_budget_constant_is_350(self):
        assert AGENTS_MD_MAX_LINES == 350

    def test_agents_md_within_budget(self):
        lines = AGENTS_MD_PATH.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= AGENTS_MD_MAX_LINES, (
            f"AGENTS.md is {len(lines)} lines (budget {AGENTS_MD_MAX_LINES}); "
            "protocol details belong in skills/intake/SKILL.md"
        )

    def test_intake_skill_stays_compact(self):
        lines = INTAKE_SKILL_PATH.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= INTAKE_MAX_LINES, (
            f"intake skill grew to {len(lines)} lines (budget {INTAKE_MAX_LINES})"
        )


class TestSingleTurnBudgetRule:
    def test_rule_lives_in_exactly_one_file(self):
        hits = _turn_budget_files()
        assert set(hits) == {"skills/intake/SKILL.md"}, (
            "the clarification turn budget must exist in exactly one file, "
            f"found: {hits}"
        )

    def test_intake_carries_the_canonical_markers(self):
        body = _read(INTAKE_SKILL_PATH)
        for marker in CANONICAL_BUDGET_MARKERS:
            assert marker in body, marker

    def test_agents_md_does_not_restate_the_budget(self):
        body = _read(AGENTS_MD_PATH)
        leaked = [marker for marker in TURN_BUDGET_MARKERS if marker in body]
        assert not leaked, f"AGENTS.md restates the turn budget via {leaked}"


class TestIntakeSkill:
    def test_frontmatter_is_valid(self):
        text = _read(INTAKE_SKILL_PATH)
        assert text.startswith("---\n")
        parts = text.split("---\n", 2)
        assert len(parts) == 3, "frontmatter must be closed by a second ---"
        frontmatter = parts[1]
        assert "name: intake" in frontmatter
        assert "description:" in frontmatter
        assert "Use when:" in frontmatter
        assert "argument-hint:" in frontmatter
        assert "user-invocable: true" in frontmatter
        assert "triggers:" in frontmatter
        trigger_block = frontmatter.split("triggers:", 1)[1]
        entries = [
            line.strip()[2:]
            for line in trigger_block.splitlines()
            if line.strip().startswith("- ")
        ]
        assert entries, "triggers must be a non-empty YAML list"

    @pytest.mark.parametrize("heading", PROTOCOL_SECTIONS)
    def test_protocol_section_present(self, heading):
        assert heading in _read(INTAKE_SKILL_PATH), heading

    def test_router_table_is_reproduced_verbatim(self):
        body = _read(INTAKE_SKILL_PATH)
        for row in ROUTER_ROWS:
            assert row in body, row

    def test_non_loading_classes_are_named(self):
        body = _read(INTAKE_SKILL_PATH)
        assert "明确可执行 不加载本 skill" in body
        assert "宏观型 不加载本 skill" in body

    def test_second_round_exception_is_documented(self):
        """The layered budget allows one extra round only after an explicit
        "不知道/不确定" — the one intentional semantic change of this move."""
        budget = "\n".join(_section_body(_read(INTAKE_SKILL_PATH), "## 分层轮次预算"))
        assert "不知道" in budget
        assert "不确定" in budget
        assert "只问该槽位" in budget

    def test_loader_boundary_is_declared(self):
        body = _read(INTAKE_SKILL_PATH)
        assert "AGENTS.md 指令 + 文件读取" in body
        assert "vibe-trading_load_skill" in body
        assert "两套系统" in body

    def test_direction_is_declared_one_way(self):
        body = _read(INTAKE_SKILL_PATH)
        assert "## 单向指针" in body
        assert "不回指" in body


class TestScenarioCPointer:
    def test_scenario_c_heading_survives(self):
        body = _read(SCENARIOS_SKILL_PATH)
        assert "## 场景 C：开放性问题" in body

    def test_scenario_c_body_is_one_line_pointer(self):
        body = _section_body(_read(SCENARIOS_SKILL_PATH), "## 场景 C：开放性问题")
        assert len(body) <= 3, body
        joined = "\n".join(body)
        assert "intake" in joined
        assert "第二轮" not in joined
        for marker in TURN_BUDGET_MARKERS:
            assert marker not in joined, marker

    def test_intake_does_not_point_back(self):
        assert "场景 C" not in _read(INTAKE_SKILL_PATH)


class TestAgentsMdRouter:
    def test_four_class_table_survives(self):
        body = _read(AGENTS_MD_PATH)
        for row in ROUTER_ROWS:
            assert row in body, row

    def test_intake_pointer_present(self):
        body = _read(AGENTS_MD_PATH)
        assert "先加载 intake skill（skills/intake/SKILL.md）再执行" in body
        assert "明确可执行 / 宏观型 不加载" in body

    def test_deleted_protocol_headings_stay_deleted(self):
        body = _read(AGENTS_MD_PATH)
        for heading in (
            "## 0. 分流路由器",
            "## 2. 开放型 → Least-to-Most 收敛漏斗",
            "## 3. 待澄清型 → 槽位澄清",
            "## 4. 宏观型 → Step-Back 协议",
            "## 5. 轮次预算（硬性）",
        ):
            assert heading not in body, heading
