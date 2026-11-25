---
name: html-report
description: |
  手工导出带 ECharts 的独立 HTML 文件（非产品报告路径），经 RM STS 直传交付可点击报告链接。Use when: 用户明确要求“生成一个独立 HTML 文件”、离线分享、非产品报告导出。
  支持 6 种专用模板：Vibe-Trading 回测、Alpha158 Walk-Forward、七看八问、基本面分析、缠论分析、信号报告。
argument-hint: "报告类型 + 股票代码"
user-invocable: true
triggers:
  - html report
  - HTML 报告
  - 生成 HTML
  - html-report
---

# HTML Report Skill

Use this skill to generate beautiful, interactive HTML reports with ECharts charts and deliver them to the user as a clickable report URL through the Resource Manager (RM).

## When to Use

Only when the user explicitly asks for a standalone HTML file. Product reports — including every backtest report — are rendered by the control plane from structured run data; do not generate HTML for them.

## Design Quality Workflow

1. **Before designing**: load the `frontend-design` skill for design direction (typography, color, layout, anti-slop). Sibling skill: `/home/opencode/.config/opencode/skills/frontend-design`.
2. **Build** with the ECharts renderer and templates below — the chart engine stays `scripts/reports/html_renderer.py`.
3. **After building**: run the `web-design-guidelines` skill as a QA pass over the generated HTML (accessibility, contrast, responsive behavior) and fix findings before delivery. Sibling skill: `/home/opencode/.config/opencode/skills/web-design-guidelines`.

### Finance Color Semantics

- CN A-share reports: **red = up, green = down**. Do not assume the US green=up convention; apply this to KPI changes, candlesticks, and P&L cells.
- Keep the dual light/dark theme and the CJK-first font stack in every report.

## Available Templates

| Template | Renderer Function | Best For |
|----------|-------------------|----------|
| Vibe-Trading 回测 | `render_vibe_backtest_html()` | 12 KPI cards + equity curve + drawdown + trades table |
| Alpha158 Walk-Forward | `render_alpha158_backtest_html()` | Multi-strategy comparison + best strategy highlight |
| 七看八问 | `render_seven_look_html()` | Radar chart + financial trends + peer comparison |
| 基本面分析 | `render_fundamental_html()` | KPI cards + financial trends + business mix pie |
| 缠论分析 | `render_chanlun_html()` | K-line candlestick + fractals/strokes/centers + buy/sell signals |
| 信号报告 | `render_signal_html()` | Signal summary + price chart with markers |
| Markdown fallback | `render_from_markdown()` | Convert existing .md reports to styled HTML |

## Design Constraints (Hard Rules)

Every generated HTML must follow these constraints:

### Typography
- **CJK-first font stack**: `"Noto Sans SC", "Source Han Sans SC", "PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
- **Monospace**: `"JetBrains Mono", "Fira Code", "SF Mono", monospace`
- **8px baseline grid**: All spacing, line-height, and font-size must be multiples of 8px

### Colors
- **No pure black/white**: Use `#fafaf9` (light bg) and `#1c1917` (dark bg)
- **Contrast ratio ≥ 4.5** (WCAG AA) for all text
- **Semantic colors**: success `#16a34a`, warning `#d97706`, danger `#dc2626`, accent `#2563eb`
- **Finance semantics**: CN A-share red=up / green=down (see above)

### Layout
- **Single-file HTML**: All CSS and JS inline — **including ECharts** (the renderer inlines the vendored build from `scripts/reports/assets/echarts.min.js`). **No CDN or any external fetch**: reports render under `CSP: sandbox allow-scripts` on an opaque origin where external requests are blocked.
- **Chart containers must have fixed height**: `<div style="position:relative;height:280px">` (KPI sparklines ~40px, main charts ~280px)
- **Responsive**: Works from 375px mobile to 1440px desktop
- **Dual theme**: Light/dark via `prefers-color-scheme` media query; theme scripts must tolerate `localStorage` throwing (opaque-origin sandbox)

### Anti-Slop Rules
- Must use real data — no lorem ipsum or placeholder content
- No decorative blobs, waves, or geometric patterns
- No emoji as primary visual elements
- No purple/blue gradient backgrounds as default
- Rounded corners (`--radius: 8px`) + soft shadows

## Workflow

1. Determine the report type from the analysis context.
2. Load `frontend-design` for design direction; prepare the data structure matching the renderer function's parameters.
3. Call the appropriate renderer from `scripts/reports/html_renderer.py`:
   ```python
   import sys; sys.path.insert(0, "/home/opencode/.config/opencode/skills/html-report")
   from scripts.reports.html_renderer import render_vibe_backtest_html
   html = render_vibe_backtest_html(ts_code="588000.SH", name="科创50ETF", ...)
   ```
4. Save the HTML to `/home/opencode/.vibe-trading/reports/<stock_code>/<date>_<strategy>.html`.
5. Run the `web-design-guidelines` QA pass; fix findings before delivery.
6. Deliver through the RM (STS direct upload to private OSS — contract in `docs/report-delivery.md`):
   ```bash
   python /home/opencode/.config/opencode/skills/html-report/scripts/reports/upload_report.py <html_path>
   ```
   The script reads `RM_AGENT_API_URL` and the per-box agent token at `/home/opencode/.config/rm/agent-token`, requests a short-lived upload credential, PUTs the HTML to OSS via the internal endpoint, and confirms. Requires `oss2` (preinstalled in the image; see `requirements.txt`). Use `--dry-run` for a zero-network self-containment check.
7. Return the printed `report_url` to the user as a clickable markdown link, e.g. `[查看报告](<report_url>)`. Record the `report_id` in `analysis/_index.json` under `rm_report_id`. The deliverable is the link — never a bare filesystem path, a `docker cp` instruction, or a fabricated localhost/nginx URL.
8. If the upload fails, report the credential-free error honestly and do not fabricate a URL.

## Fallback: Markdown to HTML

For existing Markdown reports that don't have structured data:
```python
from scripts.reports.html_renderer import render_from_markdown
html = render_from_markdown("analysis/588000/backtests/2026-06-04_dual_ma_momentum.md")
```

## Storage Contract

- Manual exports live under `/home/opencode/.vibe-trading/reports/<stock_code>/`.
- File extension: `.html`.
- Product backtest reports are not stored here; the control plane renders them from `run_dir`.
- **Delivery**: STS direct upload to the RM private-OSS bucket (`docs/report-delivery.md`). The authoritative object lives at `reports/{user_id}/{report_id}.html` in OSS; the sandbox keeps no served copy.
- **Receipt**: `upload_report.py` prints the `report_id` plus a clickable `report_url` (`{public_base}/reports/{report_id}`; default base `https://www.jetwind.net/sandbox`, override with `--public-base`).
- **Self-contained**: the uploaded HTML must inline ECharts + CSS and use system CJK fonts — no external fetches (opaque-origin CSP). `upload_report.py` rejects non-self-contained HTML before requesting a credential.

## Rules

- Never generate HTML for a backtest or for a “可视化回测报告” request; return the `run_id` instead.
- Never use external CSS frameworks (Bootstrap, Tailwind CDN).
- Never use CDN fonts (system font stack is sufficient).
- Never reference any external URL in the HTML (no `<script src>`, `<link href>`, or CSS `url(http…)`); ECharts is inlined by the renderer.
- Never generate multi-file output (everything in one `.html` file).
- Chart containers must always have explicit height to prevent ECharts resize loops.
- Never log or persist the agent token or STS credentials; `upload_report.py` handles them in memory only.
- Never deliver a bare filesystem path or `docker cp` instruction as the report; the deliverable is the clickable `report_url`.