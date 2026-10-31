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

INTAKE_MAX_LINES = 160


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


PRODUCT_INDEX_HEADING = "# 产物索引"
CAPABILITY_INDEX_HEADING = "# 能力索引"
CAPABILITY_TABLE_HEADER = "| 能力 | Skill/工具 | 触发词 |"
APPENDIX_HEADING = "## 附录：能力索引（按需查阅）"
PRODUCT_COLUMNS = ("产物", "场景/Skill", "用户说法", "输入", "产出", "不能做")
PRODUCT_ROWS = (
    "个股体检",
    "选股清单",
    "策略回测",
    "组合体检",
    "事件/宏观影响",
    "交易复盘",
    "定时监控",
    "报告",
)
FALLBACK_LINE = "未命中产物 → 加载 research-scenarios 附录查能力索引。"
FALLBACK_ROUTING_LINE = (
    "菜单外请求（未命中以上 8 个产物）→ 先按分流表判定类型："
    "明确可执行直接路由；开放型/待澄清型加载 intake；宏观型走场景 F；"
    "仍无匹配则说明能力边界并给出可回答的替代菜单。"
)

# Report §12.2 P1-1: 6 of the 8 products map 1:1 onto a research-scenarios
# playbook; 组合体检 (A + risk-analysis) and 报告 (html-report) are the two
# named exceptions. The mapping is asserted cell-by-cell so a reorder or a
# silent re-routing cannot pass as cosmetic.
PRODUCT_SCENARIOS = (
    ("个股体检", "场景 A"),
    ("选股清单", "场景 E"),
    ("策略回测", "场景 B"),
    ("组合体检", "组合（场景 A + risk-analysis）"),
    ("事件/宏观影响", "场景 F"),
    ("交易复盘", "场景 B2"),
    ("定时监控", "场景 D"),
    ("报告", "html-report"),
)

SCENARIO_TARGETS = ("场景 A", "场景 B", "场景 B2", "场景 D", "场景 E", "场景 F")

HTML_REPORT_SKILL_PATH = SKILLS_DIR / "html-report" / "SKILL.md"
# ``risk-analysis`` is a VT built-in skill, not an OpencodeAgent skill dir: the
# container reaches it through ``vibe-trading_load_skill`` from the installed
# Vibe-Trading tree, which this repo carries at ``agent/src/skills/``.
VT_SKILLS_DIR = OPENCODE_AGENT_DIR.parent / "agent" / "src" / "skills"

# Pre-migration count, measured before the move: AGENTS.md:191-220 held
# heading + header + separator + 27 data rows. Frozen because the source
# block no longer exists to recount; the appendix must match within ±2.
CAPABILITY_INDEX_DATA_ROWS = 27


def _table_rows(text: str, heading_prefix: str) -> list[list[str]]:
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(heading_prefix)),
        None,
    )
    if start is None:
        raise AssertionError(f"heading not found: {heading_prefix}")
    rows: list[list[str]] = []
    in_table = False
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            in_table = True
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            rows.append(cells)
        elif in_table or line.startswith("# "):
            break
    return rows


class TestProductIndex:
    def test_capability_index_moved_out(self):
        body = _read(AGENTS_MD_PATH)
        assert PRODUCT_INDEX_HEADING in body
        assert CAPABILITY_INDEX_HEADING not in body
        assert CAPABILITY_TABLE_HEADER not in body

    def test_product_table_has_eight_rows_and_six_columns(self):
        rows = _table_rows(_read(AGENTS_MD_PATH), PRODUCT_INDEX_HEADING)
        assert rows, "product index table not found"
        assert tuple(rows[0]) == PRODUCT_COLUMNS
        data_rows = rows[1:]
        assert len(data_rows) == 8, data_rows
        names = [row[0].strip("*") for row in data_rows]
        assert names == list(PRODUCT_ROWS)
        for name in PRODUCT_ROWS:
            assert names.count(name) == 1, name

    def test_every_product_row_keeps_all_six_columns(self):
        data_rows = _table_rows(_read(AGENTS_MD_PATH), PRODUCT_INDEX_HEADING)[1:]
        assert len(data_rows) == 8
        for row in data_rows:
            assert len(row) == 6, row

    def test_each_product_maps_to_its_scenario_or_skill(self):
        data_rows = _table_rows(_read(AGENTS_MD_PATH), PRODUCT_INDEX_HEADING)[1:]
        actual = [(row[0].strip("*"), row[1]) for row in data_rows]
        assert actual == list(PRODUCT_SCENARIOS)

    def test_scenario_targets_exist_in_the_scenarios_skill(self):
        text = _read(SCENARIOS_SKILL_PATH)
        for target in SCENARIO_TARGETS:
            heading = f"## {target}："
            assert heading in text, f"{target} has no playbook: {heading}"

    def test_risk_analysis_resolves_to_the_vt_skill_tree(self):
        appendix = _read(SCENARIOS_SKILL_PATH)
        assert "`risk-analysis` (VT)" in appendix, (
            "组合体检 maps to risk-analysis, which must stay labelled as a VT "
            "skill in the capability appendix"
        )
        assert (VT_SKILLS_DIR / "risk-analysis" / "SKILL.md").is_file(), (
            f"risk-analysis is not an OpencodeAgent skill dir; expected the VT "
            f"tree at {VT_SKILLS_DIR / 'risk-analysis' / 'SKILL.md'}"
        )

    def test_html_report_target_exists_locally(self):
        assert HTML_REPORT_SKILL_PATH.is_file(), HTML_REPORT_SKILL_PATH

    def test_fallback_line_follows_the_table(self):
        lines = _read(AGENTS_MD_PATH).splitlines()
        assert FALLBACK_LINE in lines
        fallback_index = lines.index(FALLBACK_LINE)
        assert fallback_index > 0
        assert lines[fallback_index - 1].startswith("|"), (
            "the fallback line must immediately follow the product table"
        )

    def test_off_menu_routing_follows_the_fallback_line(self):
        lines = _read(AGENTS_MD_PATH).splitlines()
        assert FALLBACK_ROUTING_LINE in lines
        assert lines.index(FALLBACK_ROUTING_LINE) == lines.index(FALLBACK_LINE) + 1
        assert "菜单外请求" in FALLBACK_ROUTING_LINE

    def test_appendix_carries_the_migrated_table(self):
        text = _read(SCENARIOS_SKILL_PATH)
        assert APPENDIX_HEADING in text
        rows = _table_rows(text, APPENDIX_HEADING)
        assert rows, "capability appendix table not found"
        assert tuple(rows[0]) == ("能力", "Skill/工具", "触发词")
        data_rows = rows[1:]
        assert abs(len(data_rows) - CAPABILITY_INDEX_DATA_ROWS) <= 2, (
            f"appendix has {len(data_rows)} capability rows; pre-migration "
            f"AGENTS.md had {CAPABILITY_INDEX_DATA_ROWS} (±2)"
        )
        assert "vibe-trading_read_run_artifact" in text
        assert "91 个" in text
        assert "memory-lifecycle" in text
        assert "vibe-trading_read_run_artifact" not in _read(AGENTS_MD_PATH)


# The six external-evidence skills replaced the retired nano-search-mcp server.
# Both directions are pinned: the directories must exist, and AGENTS.md must
# route to them — a stale "known gap" paragraph silently disables the tools.
ASHARE_SKILLS = (
    "ashare-filings",
    "ashare-events",
    "ashare-risk",
    "ashare-ir",
    "ashare-research",
    "ashare-policy",
)


def test_ashare_skill_dirs_exist():
    for name in ASHARE_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").is_file(), f"missing {name}/SKILL.md"
        assert list((SKILLS_DIR / name / "scripts").glob("*.py")), f"{name} has no script"


def test_agents_md_routes_to_every_ashare_skill():
    text = _read(AGENTS_MD_PATH)
    missing = [name for name in ASHARE_SKILLS if name not in text]
    assert not missing, f"AGENTS.md never routes to: {missing}"


def test_agents_md_no_longer_claims_the_gap():
    text = _read(AGENTS_MD_PATH)
    assert "nano-search-mcp" not in text
    assert "已知缺口" not in text
