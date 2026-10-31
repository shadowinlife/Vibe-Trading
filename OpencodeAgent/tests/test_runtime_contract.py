"""Offline guards for the control-plane runtime contract.

The container must keep two roots separate: ``/workspace`` is the opencode
project (AGENTS.md, commands, project config) and ``/home/opencode`` is the
control-plane artifact fence. These tests fail before a rebuild if the
working directory, the backtest run_dir, or the report handoff drifts.
"""

from __future__ import annotations

from pathlib import Path

OPENCODE_AGENT_DIR = Path(__file__).resolve().parents[1]
AGENTS_MD_PATH = OPENCODE_AGENT_DIR / "AGENTS.md"
DOCKERFILE_PATH = OPENCODE_AGENT_DIR / "Dockerfile"
SCENARIOS_SKILL_PATH = OPENCODE_AGENT_DIR / "skills" / "research-scenarios" / "SKILL.md"
HTML_REPORT_SKILL_PATH = OPENCODE_AGENT_DIR / "skills" / "html-report" / "SKILL.md"
HTML_REPORT_DEPLOY_PATH = (
    OPENCODE_AGENT_DIR
    / "skills"
    / "html-report"
    / "scripts"
    / "reports"
    / "deploy_report.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_md_declares_the_control_plane_run_contract() -> None:
    body = _read(AGENTS_MD_PATH)
    assert "回测与报告运行契约（控制面，CRITICAL）" in body
    assert "/home/opencode/.vibe-trading/runs/<run_id>" in body
    assert "vibe-trading_backtest(run_dir=...)" in body
    assert "不要为“可视化回测报告”手写 HTML/PDF" in body
    assert "不要主动生成 HTML 报告" in body
    assert "必须主动问询用户是否需要生成 HTML" not in body
    assert "artifacts/metrics.csv" in body
    assert "artifacts/equity.csv" in body
    assert "artifacts/trades.csv" in body
    assert "artifacts/positions.csv" in body
    assert 'schema_version: "1.0"' in body
    assert "equity_preview" in body
    assert "artifact_paths" in body


def test_scenarios_do_not_route_backtests_to_html() -> None:
    body = _read(SCENARIOS_SKILL_PATH)
    assert "/home/opencode/.vibe-trading/runs/<run_id>" in body
    assert "不另存 Markdown/HTML" in body
    assert "主动问询是否需要生成 HTML" not in body
    assert "HTML 交互回测报告" not in body


def test_html_report_is_manual_export_only() -> None:
    body = _read(HTML_REPORT_SKILL_PATH)
    assert "Only when the user explicitly asks for a standalone HTML file" in body
    assert "/home/opencode/.vibe-trading/reports" in body
    assert "Never generate HTML for a backtest" in body
    assert "Always generate HTML" not in body


def test_html_report_deployer_has_no_scp_or_nginx_surface() -> None:
    body = _read(HTML_REPORT_DEPLOY_PATH)
    assert "scp" not in body
    assert "ssh" not in body
    assert "8088" not in body
    assert "/workspace/reports" not in body


def test_dockerfile_pins_the_project_working_directory() -> None:
    body = _read(DOCKERFILE_PATH)
    assert "\nWORKDIR /workspace\n" in body
