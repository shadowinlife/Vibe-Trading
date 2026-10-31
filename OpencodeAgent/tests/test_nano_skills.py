"""Contract tests for the script-backed ashare skills.

These run offline: they assert CLI shape, argument rejection and the shared
helper behaviour. Live network probes are opt-in via ``NANO_SKILLS_LIVE=1``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
LIB = SKILLS / "_lib"

SKILL_NAMES = [
    "ashare-filings",
    "ashare-events",
    "ashare-risk",
    "ashare-ir",
    "ashare-research",
    "ashare-policy",
]


def _scripts() -> list[Path]:
    found = []
    for name in SKILL_NAMES:
        found.extend(sorted((SKILLS / name / "scripts").glob("*.py")))
    return found


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"nanofetch_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_every_skill_ships_skill_md_with_triggers() -> None:
    for name in SKILL_NAMES:
        skill_md = SKILLS / name / "SKILL.md"
        assert skill_md.is_file(), f"{name} is missing SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{name}/SKILL.md has no frontmatter"
        assert f"name: {name}" in text
        assert "Use when:" in text, f"{name}/SKILL.md has no trigger line"
        assert "argument-hint:" in text


def test_every_skill_has_at_least_one_script() -> None:
    for name in SKILL_NAMES:
        scripts = list((SKILLS / name / "scripts").glob("*.py"))
        assert scripts, f"{name} has no script"


def test_scripts_do_not_import_mcp() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _scripts()
        if "import mcp" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"scripts must stay MCP-free: {offenders}"


def test_scripts_reject_invalid_code_with_exit_2() -> None:
    for script in _scripts():
        if "_text" in script.stem or script.stem == "industry_policies":
            continue
        result = _run(script, "--code", "12")
        assert result.returncode == 2, f"{script.stem} accepted an invalid code"
        assert result.stderr.strip(), f"{script.stem} rejected silently"


def test_text_scripts_reject_foreign_host_with_exit_2() -> None:
    for script in _scripts():
        if not script.stem.endswith("_text"):
            continue
        result = _run(script, "--url", "https://example.com/x")
        assert result.returncode == 2, f"{script.stem} accepted a foreign host"


def test_scripts_helper_discovers_shared_lib() -> None:
    for script in _scripts():
        result = _run(script, "--help")
        assert result.returncode == 0, f"{script.stem} --help failed: {result.stderr[-400:]}"


def test_clip_and_grep_helpers() -> None:
    envelope = _load_module(LIB / "nanofetch" / "envelope.py")
    text_mod = _load_module(LIB / "nanofetch" / "text.py")

    clipped, truncated = envelope.clip("x" * 100, 10)
    assert (len(clipped), truncated) == (10, True)
    assert envelope.clip("short", 10) == ("short", False)

    body = "前言" * 50 + "毛利率为 32.1%" + "后记" * 50
    hits = text_mod.grep_excerpts(body, "毛利率", context=30)
    assert len(hits) == 1
    assert "32.1%" in hits[0]["excerpt"]


def test_grep_windows_merge_and_stay_bounded() -> None:
    text_mod = _load_module(LIB / "nanofetch" / "text.py")
    body = "储能" * 400
    hits = text_mod.grep_excerpts(body, "储能", context=50)
    sizes = [len(hit["excerpt"]) for hit in hits]
    assert len(hits) < 400, "adjacent matches were not merged"
    assert max(sizes) <= 200, f"excerpt escaped the context*4 bound: {max(sizes)}"


def test_assert_host_allow_list() -> None:
    http = _load_module(LIB / "nanofetch" / "http.py")
    allowed = ["vip.stock.finance.sina.com.cn"]
    assert http.assert_host("https://vip.stock.finance.sina.com.cn/x", allowed)
    with pytest.raises(http.HostNotAllowed):
        http.assert_host("https://evil.example/x", allowed)
    with pytest.raises(http.HostNotAllowed):
        http.assert_host("file:///etc/passwd", allowed)


def test_html_to_text_strips_scripts_and_styles() -> None:
    text_mod = _load_module(LIB / "nanofetch" / "text.py")
    html = "<html><style>a{}</style><script>x()</script><p>营收 100 亿</p></html>"
    out = text_mod.html_to_text(html)
    assert "营收 100 亿" in out
    assert "x()" not in out
    assert "a{}" not in out


@pytest.mark.skipif(os.environ.get("NANO_SKILLS_LIVE") != "1", reason="live network probe")
def test_live_company_report_returns_json() -> None:
    script = SKILLS / "ashare-filings" / "scripts" / "company_report.py"
    result = _run(script, "--code", "300750", "--year", "2024", "--grep", "储能", "--max-chars", "2000")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["available"] is True
    assert payload["excerpts"]