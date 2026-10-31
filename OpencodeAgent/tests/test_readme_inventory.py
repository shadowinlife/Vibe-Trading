"""Offline inventory assertions for the OpencodeAgent README (todo 15).

The README is the human entry point for the harness, and its skill count,
skill list and command list drift whenever a directory is added. These
tests pin the prose to the filesystem and pin the research report's plan
link, with no container and no model calls.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
SKILLS_DIR = ROOT / "skills"
COMMANDS_DIR = ROOT / "config" / "command"
REPORT_PATH = (
    ROOT.parent
    / "mymain-wiki"
    / "agent-ux"
    / "2026-10-27-novice-onboarding-guidance.md"
)

COMMAND_NAMES = ("backtest", "checkup", "review", "screen")
PLAN_LINE = (
    "> 已立项：`.omo/plans/novice-onboarding-gprime.md` + ACS 姊妹计划（2026-10-27）。"
)
CHANGES_HEADING = "## 13. 立项后变更（2026-10-28）"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _skill_dirs() -> list[str]:
    return sorted(
        path.name
        for path in SKILLS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith((".", "_"))
    )


def test_skill_count_bullet_matches_the_filesystem():
    readme = _read(README_PATH)
    count = len(_skill_dirs())
    match = re.search(r"\*\*(\d+) OpenCode skills\*\*", readme)
    assert match, "README is missing the '**N OpenCode skills**' bullet"
    assert int(match.group(1)) == count, (
        f"README claims {match.group(1)} skills; skills/ has {count}"
    )


def test_tree_header_matches_the_filesystem():
    readme = _read(README_PATH)
    match = re.search(r"# OpenCode skills \((\d+)\)", readme)
    assert match, "README tree is missing the '# OpenCode skills (N)' comment"
    assert int(match.group(1)) == len(_skill_dirs())


def test_every_skill_directory_is_named():
    readme = _read(README_PATH)
    missing = [name for name in _skill_dirs() if name not in readme]
    assert not missing, f"skills missing from README: {missing}"


def test_intake_skill_is_documented():
    assert "intake" in _read(README_PATH)


def test_readme_agents_md_line_count_matches_the_file():
    match = re.search(r"Full AGENTS\.md\*\* \((\d+) lines", _read(README_PATH))
    assert match, "README is missing the 'Full AGENTS.md (N lines' claim"
    actual = len((ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines())
    assert int(match.group(1)) == actual, (
        f"README claims {match.group(1)} AGENTS.md lines; file has {actual}"
    )


def test_four_slash_commands_exist_and_are_documented():
    readme = _read(README_PATH)
    assert COMMANDS_DIR.is_dir(), COMMANDS_DIR
    on_disk = tuple(sorted(path.stem for path in COMMANDS_DIR.glob("*.md")))
    assert on_disk == COMMAND_NAMES, f"config/command has: {on_disk}"
    for name in COMMAND_NAMES:
        assert f"/{name}" in readme, f"README does not mention /{name}"
        assert f"{name}.md" in readme, f"README does not list {name}.md"


def test_commands_are_discovered_from_the_workspace_path():
    assert "/workspace/.opencode/command/" in _read(README_PATH)


def test_report_links_the_plan_directly_under_the_frontmatter():
    lines = _read(REPORT_PATH).splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    assert lines[closing + 1] == PLAN_LINE, (
        "the plan line must be the first line after the frontmatter block"
    )


def test_report_appends_the_post_plan_changes_section():
    assert CHANGES_HEADING in _read(REPORT_PATH)
