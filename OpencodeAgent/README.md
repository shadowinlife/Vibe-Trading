# OpencodeAgent — opencode-serve harness

Docker build project for `opencode-serve` — OpenCode Web Server with Vibe-Trading AI, configured for Chinese A-share quantitative research with ClickHouse data warehouse.

> Since 2026-08-17 this harness is managed inside the Vibe-Trading `mymain`
> branch as `OpencodeAgent/` (personal-deployment capability, not upstreamed).
> The original standalone repo `shadowinlife/vibetrading-opencode-instruct`
> is archived; this directory is the source of truth.

## Overview

This project packages the OpenCode Web Server research environment into a reproducible Docker image. It extends the base `opencode-serve` image with:

- **OpenCode CLI 1.18.5** + OMO (oh-my-openagent) plugin
- **nano-search-mcp** — local MCP server for Chinese financial data (新浪财经, 百炼 WebSearch)
- **5 OpenCode skills**: data-warehouse (ClickHouse heavy queries), html-report (ECharts), periodic-execution (cron), escape-top-microstructure (top-detection signals), research-scenarios (scenario playbooks A–F, loaded on demand)
- **12 domain subagents**: `quant-agent` + `web-docs-agent` (production pilots) plus 10 admitted via the D4 admission eval (market-data / fundamentals-text / derivatives / risk-portfolio / valuation / macro-sector / altdata / funds-fi / user-analytics, and read-only trading-connector under the DEC-5 Tier-0/Tier-1 split — order placement never enters any subagent) — the orchestrator delegates via the AGENTS.md routing policy; each subagent sees only its whitelist (every other MCP namespace is permission-denied)
- **Full AGENTS.md** with behavior instructions for 6 scenarios (A through F), a 4-class question-handling protocol and anti-hallucination discipline
- **Quantitative scripts**: market microstructure, multi-layer screening, realtime quote adapter (backtest / Chanlun / agent memory now provided by Vibe-Trading built-ins)
- **Cron job infrastructure** with CLI management and DingTalk/email notification

## Quick Start

### Prerequisites

- Docker 20.10+
- DashScope API key (for LLM inference)
- ClickHouse instance (for data warehouse)

### Build

```bash
# From the Vibe-Trading repo root (mymain branch):
cd OpencodeAgent
./build.sh            # vendors ../ (the Vibe-Trading tree) and builds the image
```

### Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

### Run

```bash
docker compose up -d
# Access at http://localhost:4096
```

## Directory Structure

```
OpencodeAgent/
├── Dockerfile                  # Multi-stage build (13 steps)
├── Dockerfile.amd64            # AMD64 variant with full Python 3.12 venv
├── build.sh                    # Build script
├── docker-compose.yml          # Docker Compose deployment
├── entrypoint.sh               # Container entrypoint (Jinja2 config render + ClickHouse probe)
├── .env.example                # Environment variable template
├── AGENTS.md                   # Agent behavior instructions
│
├── config/                     # OpenCode configuration
│   ├── opencode.json.tmpl      # Jinja2 template (rendered at runtime with ClickHouse creds)
│   ├── render_config.py        # Config renderer: template + manifests → opencode.json (+ prompts colocated)
│   ├── oh-my-openagent.json    # Agent/category model assignments (uniform qwen3.8-max)
│   ├── tui.json                # TUI plugin configuration
│   ├── package.json            # OpenCode plugin dependencies
│   ├── vibe-trading-tools.json # Tool governance manifest (disabled VT tools → permission denies)
│   ├── subagents.json          # Domain subagent manifest (quant-agent / web-docs-agent whitelists)
│   └── prompts/                # Subagent system prompts (materialized next to the rendered config)
│
├── nano-search-mcp/            # Local MCP server for Chinese financial search
│   ├── pyproject.toml
│   ├── src/nano_search_mcp/    # 12 MCP tools (search, reports, announcements, etc.)
│   └── tests/
│
├── skills/                     # OpenCode skills (5)
│   ├── data-warehouse/         # ClickHouse query interface (query_warehouse, list_tables)
│   ├── html-report/            # Interactive HTML reports with ECharts (7+1 templates)
│   ├── periodic-execution/     # Cron job management (manage.py, notifier)
│   ├── escape-top-microstructure/ # A-share top-detection signals
│   └── research-scenarios/     # Scenario playbooks A–F (on-demand, offloaded from AGENTS.md)
│
├── tests/                      # Config assembly tests (render / governance / budget guards)
│
└── workspace/                  # Runtime files
    ├── pyproject.toml
    ├── scripts/                # Core computation engines
    │   ├── microstructure/     # Market microstructure (escape top, concentration, margin, flow)
    │   ├── screening/          # 3-layer stock screening (fundamental, narrative, flow)
    │   ├── realtime/           # Quote adapter + signal scanner
    │   └── vibe_bridge/        # Vibe-Trading adapter (22 signal builders)
    └── cron_jobs/              # Periodic task management
        ├── manage.py           # CLI management tool
        ├── notifier.py         # DingTalk/email notification
        ├── trigger.sh          # Cron invocation entry point
        ├── registry.json       # Task registry (example)
        └── watchlist.json      # Watchlist configuration (example)
```

## Skills Reference

### 1. `data-warehouse` — ClickHouse Heavy Queries

Query interface for the ClickHouse A-share warehouse (`query_warehouse(sql)` / `list_tables()`). Demoted to the heavy-query channel: interactive SQL prefers the VT built-in `ch_*` semantic-layer tools; use this only for results beyond their limits (>500 rows, large aggregations).

### 2. `html-report` — Interactive HTML Reports

Generates interactive HTML reports with ECharts. Templates: backtest, Alpha158, fundamental analysis, Chanlun, signal, screening, markdown conversion.

### 3. `periodic-execution` — Cron Job Management

Manages periodic strategy execution via `cron_jobs/manage.py`. Supports register/pause/resume/remove tasks with DingTalk/email notification.

### 4. `escape-top-microstructure` — A-share Top-Detection Signals

Market-top warning framework over `workspace/scripts/microstructure/`: ~15 signal families, 7-gate validation, RED/YELLOW/GREEN ensemble verdicts via `escape_top_cli --preset strong|balanced|early|extended`. Only `margin_divergence` and `volatility_atr_expansion` are currently VALIDATED; every signal must be reported with its validation classification.

## Scripts Reference

> **Post-migration state**: `scripts/backtest/` (Walk-Forward engine), `scripts/chanlun/`, `scripts/memory/` and `scripts/experiment/` have been REMOVED, replaced by Vibe-Trading built-ins:
>
> - **Backtest** → VT backtest engine (`backtest` tool) + `scripts/vibe_bridge/` for custom signals
> - **Chanlun (缠论)** → VT `chanlun` skill
> - **Agent memory** → VT memory lifecycle (F1–F4: save / recall / reinforce / reflect)

### `scripts/microstructure/` — Market Microstructure

30+ modules: escape top预警, concentration, margin/borrow divergence, flow analysis, breadth, macro indicators, validation. Data layer now reads ClickHouse via the VT connector.

### `scripts/screening/` — Multi-Layer Stock Screening

Three-layer pipeline: fundamental (ROE, growth, OCF), narrative momentum (concept heat, research coverage), capital flow resonance. Data sourced from ClickHouse.

### `scripts/realtime/` — Real-Time Data

Unified quote adapter (tushare/akshare/yfinance) and real-time signal scanner, running alongside VT market-data federation.

### `scripts/vibe_bridge/` — Vibe-Trading Adapter

Adapter between the local scripts and the VT backtest engine. Hosts the 22 signal builders relocated from the former `scripts/backtest/` engine (`SIGNAL_REGISTRY` in `signal_builders/`).

## Cron Jobs

### Management CLI

```bash
python cron_jobs/manage.py list              # List all tasks
python cron_jobs/manage.py add --name "..." --cron "..." --prompt "..."  # Register
python cron_jobs/manage.py pause <task_id>   # Pause
python cron_jobs/manage.py resume <task_id>  # Resume
python cron_jobs/manage.py remove <task_id>  # Remove
```

### Key Rules

1. Every execution must send notification (DingTalk) — no conditional notification
2. Notification must include execution date (YYYY-MM-DD)
3. Use `opencode run --attach <url>` to trigger agent execution (not `curl POST /session`)
4. New tasks auto-schedule a 5-minute test cron; verify with `manage.py verify-test`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | Yes | DashScope API key for LLM inference |
| `OPENCODE_SERVER_PASSWORD` | Yes | Web server password |
| `CLICKHOUSE_HOST` | Yes | ClickHouse host |
| `CLICKHOUSE_PORT` | No | ClickHouse port (default: 8123) |
| `CLICKHOUSE_USER` | No | ClickHouse user (default: default) |
| `CLICKHOUSE_PASSWORD` | No | ClickHouse password |
| `CLICKHOUSE_DATABASE` | No | ClickHouse database (default: ashare) |
| `CLICKHOUSE_LLM_USER` | No | ClickHouse `llm_role` read-only user for the VT `ch_*` semantic-layer tools |
| `CLICKHOUSE_LLM_PASSWORD` | No | Password of the `llm_role` user; the 3 `ch_*` tools are unusable when unset, everything else works |
| `DINGTALK_WEBHOOK` | No | DingTalk robot webhook for notifications |
| `SMTP_HOST` / `SMTP_AUTH_CODE` | No | Email notification |

## Deployment

### docker-compose (Recommended)

```bash
cp .env.example .env   # Edit with your credentials
docker compose up -d
```

### Docker Run

```bash
docker run -d --name opencode-web -p 4096:4096 \
  --env-file .env \
  -v ./volumes/cron-state:/workspace/cron_jobs/state \
  -v ./volumes/cron-logs:/workspace/cron_jobs/logs \
  opencode-serve:latest
```

## Build Variants

| Dockerfile | Use Case |
|-----------|----------|
| `Dockerfile` | Based on `opencode-serve:latest`, upgrades to OpenCode 1.18.5 |
| `Dockerfile.amd64` | Based on `opencode-serve:0.0.6`, installs Node.js 20 + Python 3.12 from apt |

## License

- `skills/html-report/` — from [shadowinlife/vibetrading-html-report](https://github.com/shadowinlife/vibetrading-html-report)
- `nano-search-mcp/` — proprietary
- Other components — see individual file headers