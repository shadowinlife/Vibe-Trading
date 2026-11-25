"""Integration of the vendored adaptive report engine with the delivery gate.

The engine runs through its real CLI: rendered output must pass the upload
boundary's self-contained check, the A-share theme must carry red-up semantics
and CJK fonts, and malformed specifications must fail validation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.reports import upload_report

ENGINE_DIR = Path(__file__).resolve().parents[2] / "generate-interactive-business-report"
RENDERER = ENGINE_DIR / "scripts" / "render_html_report.py"
ASHARE_THEME = ENGINE_DIR / "theme.ashare.json"
RENDER_TIMEOUT_S = 60


def _minimal_spec() -> dict:
    return {
        "meta": {
            "title": "科创板 ETF 动量策略周报",
            "scope": "588000.SH 单标的动量信号",
            "source": "策略回测摘要（测试夹具）",
        },
        "sections": [
            {
                "id": "signal",
                "title": "本周信号",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "MA10 上穿 MA30，动量评分 3.2，触发买入信号。",
                    },
                    {"type": "list", "items": ["收盘价 1.052 元", "RSI(14) = 58.3"]},
                ],
            }
        ],
    }


def _render(spec: dict, out_path: Path, *extra: str) -> subprocess.CompletedProcess:
    spec_path = out_path.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(RENDERER), str(spec_path), str(out_path), *extra],
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT_S,
        check=False,
    )


def test_minimal_spec_render_passes_delivery_self_containment_gate(tmp_path):
    out = tmp_path / "report.html"
    result = _render(_minimal_spec(), out)
    assert result.returncode == 0, result.stderr
    upload_report.assert_self_contained(out.read_text(encoding="utf-8"))


def test_ashare_theme_render_carries_red_up_and_cjk_fonts(tmp_path):
    out = tmp_path / "report.html"
    result = _render(_minimal_spec(), out, "--theme", str(ASHARE_THEME))
    assert result.returncode == 0, result.stderr
    html = out.read_text(encoding="utf-8")
    assert "#e11d48" in html.lower(), "red-up positive color missing"
    assert "Noto Sans SC" in html, "CJK font stack missing"


def test_check_rejects_section_missing_required_blocks(tmp_path):
    spec = _minimal_spec()
    del spec["sections"][0]["blocks"]
    out = tmp_path / "report.html"
    result = _render(spec, out, "--check")
    assert result.returncode != 0, "validation gate accepted a malformed spec"
    assert not out.exists(), "--check must validate without writing output"
