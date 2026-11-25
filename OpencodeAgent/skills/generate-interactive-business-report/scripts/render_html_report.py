"""Render a standalone HTML report from a validated specification; stdlib only."""
import argparse
import base64
import hashlib
import html
import json
import re
import sys
from pathlib import Path

# Also support loading this script by file path from a notebook.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chart_library import render_chart
from report_specification import compile_fragments
from safe_markup import validate_fragment
from theme import load_theme, render_theme_css


TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")


def hash_source(source):
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii") + "'"


def inline_bodies(document, tag):
    """The exact texts the browser will hash for inline elements."""
    return re.findall(fr"<{tag}>([\s\S]*?)</{tag}>", document)


def render_html_report(specification: dict, root: Path = None, theme_path: Path = None) -> str:
    root = root or Path(__file__).resolve().parent.parent
    shell = (root / "report-shell.html").read_text(encoding="utf-8")
    theme = load_theme(theme_path or root / "theme.json")
    base_css = (root / "report-base.css").read_text(encoding="utf-8").rstrip()
    theme_marker = "/* VALIDATED_THEME_TOKENS */"
    if base_css.count(theme_marker) != 1:
        raise ValueError("Report stylesheet must contain one validated theme token marker")
    themed_css = base_css.replace(
        theme_marker,
        "/* Validated theme tokens. */\n" + render_theme_css(theme),
    )
    resources = {
        "REPORT_BASE_CSS": themed_css,
        "ECHARTS_RUNTIME_JS": (root / "vendor" / "echarts.custom.min.js").read_text(encoding="utf-8"),
        "REPORT_CHARTS_JS": (root / "report-charts.js").read_text(encoding="utf-8"),
        "REPORT_INTERACTIONS_JS": (root / "report-interactions.js").read_text(encoding="utf-8"),
    }
    values = compile_fragments(specification, render_chart)
    ids = set()
    anchors = set()
    for key in ["BRAND_LOGO_HTML", "HERO_HTML", "SECTION_NAV_HTML", "SECTIONS_HTML",
                "CLOSING_HTML", "SUPPORTING_HTML"]:
        values[key] = validate_fragment(values[key], ids, anchors, key)
    dangling = anchors - ids
    if dangling:
        raise ValueError("Generated link targets no element: " + ", ".join("#" + item for item in sorted(dangling)))
    values.update(resources)
    tokens = set(TOKEN.findall(shell))
    expected = set(values) | {"REPORT_CSP"}
    if tokens != expected:
        raise ValueError(f"Shell token mismatch: missing {sorted(expected - tokens)}; unknown {sorted(tokens - expected)}")
    filled = TOKEN.sub(lambda match: match[0] if match[1] == "REPORT_CSP" else values[match[1]], shell)
    # Hash what the browser will actually parse, so shell formatting cannot drift
    # away from the policy. Content cannot contribute a style or script element.
    styles = inline_bodies(filled, "style")
    scripts = inline_bodies(filled, "script")
    if len(styles) != 1 or not scripts:
        raise ValueError("The shell must carry one packaged stylesheet and its packaged scripts")
    css_policy = hash_source(styles[0])
    script_policy = " ".join(hash_source(script) for script in scripts)
    policy = html.escape(
        "default-src 'none'; base-uri 'none'; connect-src 'none'; object-src 'none'; "
        "frame-src 'none'; form-action 'none'; img-src data:; font-src 'none'; "
        f"script-src {script_policy}; script-src-attr 'none'; "
        f"style-src {css_policy}; style-src-elem {css_policy}; style-src-attr 'unsafe-inline'",
        quote=True,
    )
    return filled.replace("{{REPORT_CSP}}", policy, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specification", type=Path, help="Structured JSON report specification")
    parser.add_argument("output", type=Path, help="Output standalone HTML path")
    parser.add_argument("--theme", type=Path,
                        help="Validated JSON theme; defaults to the packaged theme.json")
    parser.add_argument("--check", action="store_true", help="Validate the content and write nothing")
    args = parser.parse_args()
    try:
        specification = json.loads(args.specification.read_text(encoding="utf-8-sig"))
        result = render_html_report(specification, theme_path=args.theme)
    except (ValueError, OSError) as error:
        # Plain stderr rather than parser.error, so a batch of faults stays readable.
        sys.exit("render_html_report: " + str(error))
    if args.check:
        charts = specification.get("charts", {}) if isinstance(specification, dict) else {}
        print(f"OK: {len(result)} bytes, {len(charts)} chart(s); nothing written")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
