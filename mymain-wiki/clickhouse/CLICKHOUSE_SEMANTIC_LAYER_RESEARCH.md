---
title: ClickHouse Semantic Layer Research Record（英文调研原文）
description: R1 研究的正式调研结论——语义层方案对比、出处链接、架构图、数据流、场景决策示例。触发词：semantic layer、dbt、Cube、mcp-clickhouse、views、COMMENT。
type: research
status: active
created: 2026-08-12
updated: 2026-08-12
tags: [clickhouse, semantic-layer, research]
related: [CLICKHOUSE_SEMANTIC_LAYER_REPORT.md, CLICKHOUSE_ITERATION_PLAN.md]
---

# ClickHouse Semantic Layer Research Record

> **Status**: Research conclusions (R1 complete) · **Date**: 2026-08-12 · **Author**: shadowinlife
> **Companion documents**: [`CLICKHOUSE_SEMANTIC_LAYER_REPORT.md`](CLICKHOUSE_SEMANTIC_LAYER_REPORT.md) (decision report, zh) · [`../branch/MYMAIN_DIVERGENCE.md`](../branch/MYMAIN_DIVERGENCE.md) §2.4 (#1062) / §4.6 (R1)
> **Evidence base**: local F5 code forensics · 2026-08-11 prior investigation (opencode session `ses_01133972`) · 2026-08-12 four-track parallel external research (official repo source reads, ClickHouse official blogs, peer-reviewed papers, open-source project sources, industry standards). Every viewpoint below carries its source link for re-verification.

---

## 1. Executive Summary & Decision

**Problem.** How to let OpenCode + Vibe-Trading MCP fetch data from ClickHouse while keeping a **stable semantic layer** — units, calibers, and column meanings that do not get lost when the access path changes.

**Decision: layered hybrid with database-side semantics as the foundation.**

1. **Do NOT adopt the official `mcp-clickhouse` as the primary data interface** — three quant-specific hard defects (§3.2).
2. **Keep and harden the F5 domain-tool layer as the primary interface** — it is our ClickStack-equivalent semantic tooling; ClickHouse's own benchmark shows semantic tools beat raw SQL by 7–20pp (§3.3).
3. **Sink semantics into the database as the foundation (L0)** — DDL in repo + structured `COMMENT COLUMN` convention + dedicated read-only DB user + resource quotas, so that *any* direct path (human SQL, future MCP) no longer loses all semantics (§6).
4. **Add hierarchical exploration tools as the flexibility escape hatch (L2/L3)** — `ch_list_tables → ch_describe_table → ch_query` with constrained SQL; self-built rather than importing the official server (§6).
5. **Metrics dictionary as L4 context** — pe_ttm caliber, volume=lot / amount=CNY conventions, close-vs-close_hfq selection rules, gold queries.
6. **Do NOT adopt dbt SL / Cube standalone semantic-layer services for now** — a single consumer does not meet the value criterion for an independent semantic layer; dbt SL consumption APIs are paid dbt Cloud (§5.2).

**One-liner.** Semantics must move from *implicit, code-carried knowledge* to a three-tier explicit asset: **database-carried (COMMENT/DDL) + code-consumed (domain tools) + doc-injected (metrics dictionary)**. Domain tools are the main channel; constrained SQL is the escape hatch; raw-SQL MCP is never the primary interface.

---

## 2. Problem Statement & Local Evidence

### 2.1 The two candidate options and their known flaws

| Option | Flaw (evidence in §2.2–§2.3) |
|---|---|
| **A. Deploy a ClickHouse MCP** (raw SQL direct access) | Semantics physically separated from data — bypassing the tool chain loses all semantics; `SELECT *` leaks ~199 unannotated columns; LLM writing `SELECT close` across an ex-dividend date silently computes wrong returns |
| **B. Local branch modification** (F5 domain-tool chain) | Semantics are *implicit and code-carried* — only requests passing through the tool chain are protected; unit conversions hardcoded in Python; `SELECT *` leak path unannotated; personal-deployment-only, not upstreamable |

### 2.2 Current F5 stack (code forensics, verified 2026-08-12)

| Layer | File | How semantics are carried |
|---|---|---|
| Connector | `agent/src/clickhouse_connector.py` | raw `query(sql)` + 8 domain methods; `get_daily_bars` curates 11 fields, most others `SELECT *` |
| Loader | `agent/backtest/loaders/clickhouse.py` | `SELECT * FROM stk_factor_pro` (**all 199 columns**) + same-day network federation; only `vol→volume` rename, **no unit metadata** |
| Flow tools | `agent/src/tools/clickhouse_fallbacks.py` | **unit conversions hardcoded in Python**: 10k-CNY→CNY `×10⁴` (fund flow), northbound `×100`; column mapping hardcoded (`rzye→financing_balance`, …) |
| Envelope | `agent/src/market_data.py` | `_provenance` exists but **carries no unit field today** (upstream PR #1065 pending) |

**Key fact:** the repo contains **no ClickHouse DDL at all** (no `CREATE TABLE` / `COMMENT COLUMN` anywhere) — the database layer has zero semantics.

### 2.3 Prior investigation conclusions (2026-08-11, session `ses_01133972`)

1. Official `mcp-clickhouse` positions itself as the *minimal execution layer between LLM and ClickHouse* — explicitly **no text-to-SQL optimization, no semantic layer**.
2. mymain's CH semantics are **implicit and code-carried** — pinned to the Python call path; only requests traversing the tool chain enjoy protection.
3. `get_market_data`'s `SELECT *` pass-through (~199 columns incl. `pe_ttm`/`pb`/`total_mv`) is a **semantic blind spot** — contract drift on any CH schema change.
4. MCP-path empirical test: code-level semantics held end-to-end, but **document-level semantics (units/calibers/field meanings) were completely lost** on the MCP path; CH `amount` (thousand-CNY) vs tool-layer conversion → 100×/1000× magnitude-error risk.
5. Upstream `main` is in a worse shape (no CH, not even unit labeling) — tracked as [HKUDS/Vibe-Trading#1062](https://github.com/HKUDS/Vibe-Trading/issues/1062); mymain CH `stk_factor_pro.vol` is tushare-caliber (lot), consistent with the #1062 normalization direction (lot).

---

## 3. Option A Findings: ClickHouse MCP (raw SQL direct access)

### 3.1 What the official server actually is

Source reads of [`ClickHouse/mcp-clickhouse`](https://github.com/ClickHouse/mcp-clickhouse) at HEAD `423ca2e` (v0.4.1, released 2026-07-17; 220k+ PyPI downloads per [official blog](https://clickhouse.com/blog/agentic-analytics-ask-ai-agent-and-remote-mcp-server-beta-launch)):

- Tools: `run_query` (renamed from `run_select_query`), `list_databases`, `list_tables` (paginated, `include_detailed_columns` flag), optional `run_chdb_select_query`.
- Security model: default `readonly=1` as a **query-level setting**, regex-based DROP/TRUNCATE detection, mandatory auth on HTTP/SSE transports, 30s default query timeout.
- The CHANGELOG is itself an evidence trail of context-explosion problems: [PR #55](https://github.com/ClickHouse/mcp-clickhouse/pull/55) "token-efficient result encoding" (0.1.8), [PR #75](https://github.com/ClickHouse/mcp-clickhouse/pull/75) "refactored chDB prompt to **avoid context-too-large errors**" (0.1.12), [PR #92](https://github.com/ClickHouse/mcp-clickhouse/pull/92) `list_tables` pagination (0.1.13), [PR #146](https://github.com/ClickHouse/mcp-clickhouse/pull/146) parameterized queries because "string interpolation is error-prone and bypasses type checking" (2026-03).

### 3.2 Three quant-specific hard defects (decision evidence)

| # | Defect | Evidence |
|---|---|---|
| 1 | **UInt64 values corrupted in JSON responses** — `total_mv` / `amount` / volume are exactly the large UInt64 columns; precision corruption directly pollutes quant data | [Issue #111](https://github.com/ClickHouse/mcp-clickhouse/issues/111), **still open** |
| 2 | **Read-only promise can be bypassed** — query-level `readonly=1` is not enforced when the DB user profile has full privileges; in a real incident a Claude sub-agent **dropped a production table** | [Issue #131](https://github.com/ClickHouse/mcp-clickhouse/issues/131) (2026-02, closed); official fix direction = dedicated read-only DB user |
| 3 | **No result-size cap** — a 199-column wide table without a disciplined LIMIT explodes the LLM context; the official changelog patched this three times (§3.1) | CHANGELOG 0.1.8 / 0.1.12 / 0.1.13 |

Other known issues: long queries blocking the event loop ([#128](https://github.com/ClickHouse/mcp-clickhouse/issues/128), fixed 0.4.0), fastmcp dependency CVEs ([#188](https://github.com/ClickHouse/mcp-clickhouse/issues/188)), >10s startup ([#160](https://github.com/ClickHouse/mcp-clickhouse/issues/160)).

### 3.3 ClickHouse's own benchmark: semantic tools beat raw SQL

**hdx-evals** — ClickHouse's official eval framework, source at [`hyperdxio/hyperdx/packages/hdx-eval`](https://github.com/hyperdxio/hyperdx/tree/main/packages/hdx-eval); methodology: deterministic synthetic telemetry (seeded PRNG), planted anomalies + distractors, real Claude Code agents, blinded judging (0.4×programmatic + 0.6×LLM-judge − tool-error penalty). Results (Claude Opus 4.6, 10 runs each), from the [official benchmark blog](https://clickhouse.com/blog/benchmarking-the-clickstack-mcp-server-with-hdx-evals) (2026-07-28):

| Scenario | ClickStack MCP (domain tools) | mcp-clickhouse (raw SQL) | Δ |
|---|---|---|---|
| error-root-cause | 93% | 73% | **+20pp** |
| noisy-signals | 64% | 45% | +19pp |
| latency-spike | 60% | 43% | +17pp |
| segmented-regression | 75% | 60% | +15pp |
| service-health-check | 61% | 54% | +7pp |

Efficiency numbers from the [ClickStack MCP announcement](https://clickhouse.com/blog/announcing-managed-clickstack-mcp-server) (2026-06-26): **25% fewer tool calls, 2.5× consistency, ~20% higher eval scores**. The raw-SQL baseline was `mcp-clickhouse==0.3.0` — exactly the server we evaluated.

> **Applicability caveat:** this is an observability-domain benchmark (OTel logs/traces). The *mechanism* (semantic tools > raw SQL) generalizes; the specific numbers do not transfer to A-share factor queries. Note also that in `metric-saturation` the raw-SQL programmatic score was actually higher (100% vs 96%) but lost 7pp to tool-error penalties with 26% more calls — raw SQL's disadvantage is primarily **trial-and-error cost**, not reasoning ability.

### 3.4 Official production guidance (what Option A would require)

From [How to set up ClickHouse for agentic analytics](https://clickhouse.com/blog/how-to-set-up-clickhouse-for-agentic-analytics) (2026-02-23):

> "**Expose only curated data marts to AI.** The model should see stable, canonical definitions. **Do not expose raw tables** and competing metric logic."

Recommended production config: dedicated `llm_role` with `readonly=1`, `max_execution_time=30`, `max_memory_usage=2GB`, `max_rows_to_read=100M`, `max_bytes_to_read=5GB`, `max_threads=4`; separate read-only service; raw→staging→marts medallion. Internal agent DWAINE: 250 users, 200 messages/day, resolves 70% of questions ([Building a data platform for agents](https://clickhouse.com/blog/building-a-data-platform-for-agents), 2026-03-27). The official anti-hallucination advice (2026-07): "provide LLMs with maximally accurate context — **use the COMMENT syntax**" ([Ask AI & Remote MCP beta](https://clickhouse.com/blog/agentic-analytics-ask-ai-agent-and-remote-mcp-server-beta-launch)).

### 3.5 Option A verdict & applicable scenarios

**Verdict:** demote from "candidate primary interface" to **human exploration channel**, conditional on completing the L0 foundation (§6).

**Applicable scenarios:** human data debugging; ad-hoc queries by developers who already know the semantics; one-off exploration with human-reviewed results.
**Not applicable:** production agent pipelines, any unit-sensitive computation, any unattended path — per §3.2 defects and §3.3 accuracy evidence.

---

## 4. Option B Findings: F5 Domain-Tool Chain (current branch)

### 4.1 Strengths (why it is the right direction)

1. **Deterministic unit/caliber correctness**: conversions are hardcoded in `clickhouse_fallbacks.py`, but they are *deterministic* — the LLM never sees raw columns. This matches the industry's converged pattern: ClickStack domain tools (§3.3), Databricks Genie **Trusted Assets / SQL Functions** — deterministic encapsulation the LLM can call but neither see nor modify ([Genie tuning docs](https://docs.databricks.com/aws/en/genie-agents/tune-quality)), and [market-terminal](https://github.com/jalilsedna/market-terminal) (31 research-only tools, documents its Vibe-Trading integration).
2. **Failure mode is safe**: the core finding of dbt's official benchmark ([Semantic Layer vs Text-to-SQL](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026), 2026-04-07, code at [dbt-labs/dbt-llm-sl-bench](https://github.com/dbt-labs/dbt-llm-sl-bench)): within-scope questions scored **98–100%** on the semantic layer vs 64.5% for text-to-SQL, and — crucially — *"a semantic-layer failure is an error; a text-to-SQL failure is a plausible wrong number."* Silent errors are the most expensive failure in finance.
3. **Aligned with upstream #1062**: CH-at-chain-head locks volume unit to "lot" (tushare caliber), consistent with the normalization direction ([#1062](https://github.com/HKUDS/Vibe-Trading/issues/1062), [PR #1065](https://github.com/HKUDS/Vibe-Trading/pull/1065), [PR #1067](https://github.com/HKUDS/Vibe-Trading/pull/1067)).

### 4.2 Weaknesses (the gaps to fix)

| # | Gap | Consequence |
|---|---|---|
| 1 | **Semantics physically separated from data** — pinned to the Python call path; database layer has zero semantics | Any bypass (human SQL, any future MCP, data debugging) loses all semantics; semantics cannot be reused by a second consumer |
| 2 | **`SELECT *` leak path** — `get_market_data` passes through all ~199 columns unannotated; the loader itself is `SELECT *` | Contract drift: a new CH column silently changes tool output; LLM receives unannotated columns and guesses calibers |
| 3 | **Hardcoded, scattered unit conversions** — `×10⁴`/`×100` in code, tool descriptions/skills say it separately | Two-place drift risk; new tables/columns require code changes; nothing a test gate can anchor |
| 4 | **Limited flexibility** — no channel for ad-hoc intents (e.g., "turnover_rate percentile for stock X on date Y") | Users are pushed to bypass the tool chain — back to unprotected Option A |
| 5 | **Not upstreamable** — personal deployment only | Semantic-layer investment cannot return to the community; maintenance cost borne alone |

### 4.3 Option B verdict & applicable scenarios

**Verdict:** keep as the **primary interface**, fix the five gaps (§6 L1).
**Applicable scenarios:** high-frequency fixed-intent queries (OHLCV, fund flow, margin, dragon-tiger, northbound); backtest data pipeline; any unit-sensitive analysis.
**Not applicable:** ad-hoc exploration, cross-table free analysis, human data audit — these needs are real and are the root cause of the Option A temptation; they must get a protected alternative channel (§6 L2/L3).

---

## 5. Industry Findings: How Similar Problems Are Solved

### 5.1 The consensus: layered hybrid

The industry answer to "LLM agents accessing analytical databases" has converged — **not** a binary choice between text-to-SQL and domain tools:

> **Deterministic semantic layer as the base + hierarchical exploration tools for progressive discovery + constrained read-only SQL as the escape hatch + business glossary / verified (gold) queries injected as context.**

Three evidence chains:

**① Raw text-to-SQL collapses on enterprise wide tables.**

| Benchmark | Numbers | Source |
|---|---|---|
| Spider 1.0 (simple schemas, ~54 cols/DB) | GPT-4o 86.6%, SOTA ~91% | [Spider 2.0 paper](https://arxiv.org/abs/2411.07763), ICLR 2025 Oral |
| **Spider 2.0** (real enterprise warehouses, **avg 812 columns/DB**) | **o1-preview code agent 21.3%; GPT-4o 10.1%** | same; [official site](https://spider2-sql.github.io/) |
| **BEAVER** (first private-enterprise DW benchmark, 9128 real query logs) | **SOTA agentic framework 10.8%**; 30.1% even with oracle subtask hints | [arXiv:2409.02038](https://arxiv.org/html/2409.02038v3) |
| BIRD (academic, 37 domains) | top test EX ~82%, human 92.96% | [bird-bench.github.io](https://bird-bench.github.io/) (accessed 2026-08) |

Spider 2.0's error taxonomy (300 manually analyzed cases): flawed data analysis 35.5%; **wrong schema linking 27.6% (column linking alone 16.6%)** — a 199-column table sits exactly in the kill zone. Benchmark reliability caveat: CIDR 2026 found annotation errors in 52.8% of BIRD Mini-Dev and 66.1% of Spider 2.0-Snow ([paper](https://vldb.org/cidrdb/papers/2026/p5-jin.pdf); [arXiv:2601.08778](https://arxiv.org/html/2601.08778v3)).

Production corroboration: **Uber QueryGPT** (1.2M queries/month; [official blog](https://www.uber.com/us/en/blog/query-gpt/) 2024-09-18; [ZenML case study](https://www.zenml.io/llmops-database/natural-language-to-sql-query-generation-at-scale)) converged on Workspaces → Intent Agent → Table Agent (**human confirmation**) → Column-Prune Agent → generation, and *still* hallucinates non-existent tables/columns. Production failure-mode surveys: [tianpan.co I](https://tianpan.co/blog/2026-04-10-text-to-sql-failure-modes-production) (the most dangerous failures are **silent** — query succeeds, schema correct, number wrong) and [tianpan.co II](https://tianpan.co/blog/2026-04-20-text-to-sql-production-schema-boundary-failures) (full-schema dump ≈ 8400 tokens for a 16-token SQL; FK-graph injection cuts context 83% at 92% recall). Note: tianpan.co is a single personal engineering blog — directional reference only.

**② Inside a semantic layer, accuracy approaches 100% and failures are safe.** dbt benchmark (§4.1): 98–100% in-scope vs 64.5% text-to-SQL; explicit-error vs plausible-wrong-number failure asymmetry.

**③ A 4KB semantic document buys +17–23pp.** Cube's paired benchmark — **data stored in ClickHouse**, three frontier models, adding only a 4KB semantic-layer markdown (measures, conventions, **disambiguation rules**): accuracy **+17–23pp** and cross-model variance disappears ([blog](https://cube.dev/blog/why-semantic-layers-make-llm-analytics-reliable-a-paired-benchmark-across-three-frontier-models); [arXiv:2604.25149](https://arxiv.org/abs/2604.25149); [open benchmark repo](https://github.com/cubedevinc/semantic-layer-benchmark)).

### 5.2 Semantic-layer patterns: two routes, converging

| | Route A: sink semantics into the database | Route B: standalone semantic layer |
|---|---|---|
| Carriers | column COMMENTs, views, DDL in repo | YAML/code models + runtime service (dbt SL, Cube, LookML) |
| Strengths | zero extra components; semantics co-located with data; SQL-native; **any client incl. LLMs can consume** | cross-source; dynamic metric compilation; unified governance/caching |
| Risks | limited expressiveness (no join graph / metric composition) | YAML rot; extra runtime hop; **API consumption excludes SQL-native users** |

Key sources per route:

- **Route A (the one we choose):**
  - **Altinity MCP** ([repo](https://github.com/Altinity/altinity-mcp), Go, Apache-2.0; [tools doc](https://github.com/Altinity/altinity-mcp/blob/main/docs/tools.md)) — the most complete engineering proof of database-side semantics: parameterized **views auto-generate typed MCP tools**; view `COMMENT` = tool description; column `COMMENT` (via `system.columns`) = parameter description; SELECT-only parser guard; server-enforced result caps (default 500 rows / 50KB, two-tier) with truncation guidance; memory system for recipes/pitfalls. See also [Altinity AI overview](https://altinity.com/ai-with-clickhouse/), [API endpoints pattern](https://altinity.com/blog/use-altinity-cloud-to-simplify-your-app-with-clickhouse-endpoints), MV series ([part 1](https://altinity.com/blog/clickhouse-materialized-views-illuminated-part-1), [part 2](https://altinity.com/blog/clickhouse-materialized-views-illuminated-part-2)), and the third-party endorsement "a well-designed view is a controlled integration contract… conceptually no different from a REST GET endpoint" ([martinelli.ch](https://martinelli.ch/a-view-is-not-a-table/)).
  - **ClickHouse native semantics**: `COMMENT COLUMN` ([docs](https://clickhouse.com/docs/en/sql-reference/statements/alter/column#comment-column)), `CREATE VIEW` with COMMENT/DEFINER ([docs](https://clickhouse.com/docs/en/sql-reference/statements/create/view)), `system.columns.comment` as machine-readable catalog ([docs](https://clickhouse.com/docs/en/operations/system-tables/columns)); clickhouse-client 25.7+ text-to-SQL feeds CREATE TABLE **including COMMENTs** to the LLM ([docs](https://clickhouse.com/docs/guides/use-cases/ai-ml/ai-powered-sql-generation)); ClickHouse Assistant's `AGENTS.md`-as-system-prompt pattern ([semantic-layer docs](https://clickhouse.com/docs/use-cases/AI_ML/AIChat/semantic-layer)); [ClickHouse Agent Skills](https://github.com/ClickHouse/agent-skills) (28 best-practice rules).
- **Route B (deferred):**
  - **dbt Semantic Layer / MetricFlow**: semantic models (entities/dimensions/measures) + metrics YAML ([semantic models](https://docs.getdbt.com/docs/build/semantic-models); [architecture](https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl)); MetricFlow engine is Apache-2.0 but **the consumption APIs (GraphQL/JDBC) are paid dbt Cloud** (same architecture doc); LLM integration pattern = UDF calling the GraphQL API ([dbt blog](https://www.getdbt.com/blog/semantic-layer-llm)).
  - **Cube**: semantic layer as API — data modeling / access control / caching / APIs ([intro](https://docs.cube.dev/docs/introduction)); official MCP server ([docs](https://github.com/cube-js/cube/blob/master/docs/content/product/apis-integrations/mcp-server.mdx)); Discover→Select→Execute-under-governance agent flow ([Cube AI agents article](https://cube.dev/articles/semantic-layer-for-ai-agents-2026), 2026-06 — vendor content, pattern description credible, product claims discounted); certified queries ([docs](https://docs.cube.dev/admin/ai/certified-queries)).
  - **LookML**: views→models→explores, query-time SQL generation, descriptions→Data Dictionary ([What is LookML](https://cloud.google.com/looker/docs/what-is-lookml)); non-additive metrics argument for dynamic metrics over materialized wide tables ([Google blog](https://cloud.google.com/blog/products/data-analytics/why-use-both-lookml-and-elt-tools-in-your-data-analytics-stack)).
- **The value criterion** (why we defer Route B): Benn Stancil — the metrics layer's value is consumer diversity; API-only layers get rewritten in SQL by analysts, splitting calibers again ([metrics layer](https://benn.substack.com/p/metrics-layer); [Minerva critique](https://benn.substack.com/p/minerva-metrics-layer); [BI by another name](https://benn.substack.com/p/bi-by-another-name); [the context layer](https://benn.substack.com/p/the-context-layer)). a16z — the YAML-rot failure mode ("the person who updated it left last year") and the agent-era need for a self-updating **context layer** ([Emerging Architectures](https://a16z.com/emerging-architectures-for-modern-data-infrastructure/), 2020; [Your Data Agents Need Context](https://a16z.com/your-data-agents-need-context/), 2026-03).
- **Open-source hybrids with ClickHouse connectors**: [WrenAI](https://github.com/Canner/WrenAI) (20k★, Apache-2.0: Git-versionable MDL semantic layer + validated-query memory + dry-plan validation + MCP; [architecture](https://docs.getwren.ai/oss/reference/architecture)) and [Vanna](https://github.com/vanna-ai/vanna) (23.8k★, MIT: RAG over DDL/docs/question-SQL pairs; [training docs](https://try.vanna.ai/docs/train/); community discussions show pure RAG struggles at 60-table/500-column scale).

### 5.3 Tool-design guidance (Anthropic / MCP official)

There is no dedicated "server tool-design best practices" page on modelcontextprotocol.io — only [Client Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices.md) and [Security Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices.md). The de-facto official guidance is Anthropic's engineering series:

| Document | Date | Relevant guidance |
|---|---|---|
| [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) | 2024-12 | ACI concept; tools are prompt engineering; poka-yoke parameter design |
| [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 2025-09 | "more tools ≠ better"; don't 1:1-wrap API endpoints; group around intent; namespacing; token-efficient responses |
| [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 2025-09 | bloated tool sets are a top failure mode; just-in-time retrieval over preloading |
| [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | 2025-11 | Tool Search (−85%+ tool-definition tokens); Programmatic Tool Calling (−37% tokens) |
| [Seeing like an agent](https://claude.com/blog/seeing-like-an-agent) | 2026-04-10 | Claude Code keeps ~20 tools; high bar for adding tools; progressive disclosure |
| [Building agents that reach production systems with MCP](https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp) | 2026-04-22 | "Group tools around intent, not endpoints"; large surface → code orchestration (Cloudflare: 2 tools ≈ 2500 endpoints, ~1K tokens); Skills + MCP pairing |
| [MCP Client Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices.md) | 2026-07 | official three-tier progressive discovery: **Catalog → Inspect → Execute** — the official version of list→describe→query |

Related reference points: [OpenBB MCP server](https://github.com/OpenBB-finance/OpenBB/tree/develop/openbb_platform/extensions/mcp_server) — **dynamic category activation** to keep the initial tool list small (direct reference for our 70+ MCP tools; [PR #7094](https://github.com/OpenBB-finance/OpenBB/pull/7094)); Anthropic's own data-analysis route is code-execution sandboxes + skills+MCP pairing, not raw text-to-SQL ([analysis tool](https://claude.com/blog/analysis-tool) 2024-10; [code execution tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool); [data analyst cookbook](https://platform.claude.com/cookbook/managed-agents-data-analyst-agent) 2026-04; [Data plugin](https://claude.com/plugins/data)); vendor-side context-injection priority — Databricks Genie: SQL expressions > example SQL > text instructions, 30-table cap, benchmark-driven iteration 54%→100% ([best practices](https://docs.databricks.com/aws/en/genie/best-practices); [production case](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way) 2026-02-06); Snowflake Cortex Analyst: verified "gold" queries → semantic-model optimization ([semantic views best practices](https://docs.snowflake.com/en/user-guide/views-semantic/best-practices); [optimization](https://docs.snowflake.com/en/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization)).

### 5.4 Wide tables (100+ columns): failure modes and standard mitigations

Failure evidence: Spider 2.0 column-linking errors 16.6% at avg 755+ columns ([paper](https://arxiv.org/abs/2411.07763)); TriSQL — accuracy drops as table/column counts grow, removing schema-relevance sorting collapses EM 76.4%→50.3% ([Nature Sci Rep 2026](https://www.nature.com/articles/s41598-026-39128-9)); column retrieval collapses on abbreviated identifiers — BM25 recall@10 = 5.5 on LiveSQLBench Large ([arXiv:2607.13311](https://arxiv.org/pdf/2607.13311)); ACM survey: "many failures stem from inability to correctly identify column/table names" ([DOI 10.1145/3737873](https://dl.acm.org/doi/10.1145/3737873)). Counter-evidence to weigh: frontier models with the full schema in context can self-filter ([The Death of Schema Linking?](https://arxiv.org/html/2408.07702v2)) — but 199 columns ≈ 8–15K tokens *per call*, and attention dilution grows with column count (TriSQL).

Standard mitigations (evidence-ranked):
1. **Hierarchical exploration** — [AutoLink](https://arxiv.org/html/2511.17190v1) (AAAI 2026): agentic iterative schema exploration keeps ~90% recall at 3000+ columns where other methods drop below 40%, at the lowest token cost; MCP official three-tier pattern (§5.3); mcp-clickhouse itself is this design.
2. **Schema linking research** — [LinkAlign](https://aclanthology.org/2025.emnlp-main.51/) (EMNLP 2025, AmbiDB ambiguity dataset), [RASL](https://www.amazon.science/publications/rasl-retrieval-augmented-schema-linking-for-massive-database-text-to-sql) (Amazon), [ExSL](https://arxiv.org/html/2501.17174) (IBM: "recall matters more than precision — a missed column is always wrong"), [SchemaGraphSQL](https://aclanthology.org/2026.findings-eacl.134.pdf) (EACL 2026), [CHESS](https://arxiv.org/abs/2405.16755), [RSL-SQL](https://arxiv.org/html/2411.00073v2), [KaSLA](https://arxiv.org/html/2502.12911v2), [EDBT 2026 analysis](https://openproceedings.org/2026/conf/edbt/paper-24.pdf) (schema enrichment beats minimal column sets).
3. **Metadata enrichment** — DDL + 2–3 sample rows + column descriptions + glossary + FK graph (tianpan.co II; Databricks value sampling).
4. **Result-side guardrails** — sqlglot AST validation, EXPLAIN dry-run, row/cost caps, generate-execute-critique (WrenAI dry-plan is a complete open-source implementation).

### 5.5 Financial units & adjusted prices (domain-specific findings)

**Standards:** no ISO standard prescribes market-data volume units. The authoritative paradigm is **ISO 20022's "value + explicit unit attribute"**: Amount must carry a Currency attribute (ISO 4217 minor units) ([currency & amount](https://www.iso20022payments.com/miscellaneous/currency-and-amount/); [message definitions](https://www.iso20022.org/iso-20022-message-definitions)); the Quantity datatype carries a Unit attribute ([data dictionary](https://www.iso20022.org/understanding-data-dictionary); Master Rules addendum, medium confidence: [Scribd copy](https://www.scribd.com/document/930950619/ISO20022-MasterRules-Addendum-20141222)). FDC3 explicitly does not model units but mandates `CURRENCY_ISOCODE` on valuations ([Valuation](https://fdc3.finos.org/docs/context/ref/Valuation); [Instrument](https://fdc3.finos.org/docs/context/ref/Instrument); [context spec](https://fdc3.finos.org/docs/context/spec)). FIBO provides finance-concept ontology ([EDM Council](https://edmcouncil.org/frameworks/industry-models/fibo/); [GitHub](https://github.com/edmcouncil/fibo)).

**How major sources declare volume units:**

| Source | Practice | Evidence |
|---|---|---|
| CRSP/WRDS | explicit "Units" sections: monthly VOL "**reported in units of 100**" (hundreds of shares!), SHROUT in thousands | [WRDS variable table](https://wrds-www.wharton.upenn.edu/demo/crsp/form/) |
| Bloomberg | field units live inside the terminal (FLDS); public docs thin — PX_VOLUME anchors only (confidence: medium) | [Galaxy Crypto Index methodology](https://assets.bbhub.io/professional/sites/10/Bloomberg-Galaxy-Crypto-Index-Methodolgy-December-2020.pdf) |
| Tushare | inline API docs: "vol: 成交量（手）", "amount: 成交额（千元）" | [daily docs](https://tushare.pro/document/2?doc_id=27) |
| AkShare | systematic "注意单位: 手/元" in output-parameter descriptions — the most direct open-source "unit-in-description" exemplar | [stock docs](https://akshare.akfamily.xyz/data/stock/stock.html); [source docs](https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock.md) |
| yfinance | no labeling, but built-in repair: `_fix_unit_mixups()` (100× price mixups) and `_standardise_currency()` (GBp→GBP ×0.01, ZAc, ILA) — engineering proof that unit confusion is endemic | [history.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py) |

**Adjusted prices — the converged rules** (synthesized from CRSP/qlib/Tushare/zipline/yfinance):

| Framework | Practice | Evidence |
|---|---|---|
| CRSP (academic standard) | store raw + factors; adjust at use time: price `A(t)=P(t)/C(t)`, volume `A(t)=P(t)*C(t)` (**opposite direction**) | [Calculations & Index Methodologies](https://www.crsp.org/wp-content/uploads/guides/CRSP_Calculations_and_Index_Methodologies.pdf); [factor guide](https://leiq.bus.umich.edu/docs/crsp_factor_adjustment.pdf); [data dictionary](https://www.crsp.org/wp-content/uploads/2023/10/crsp_us_stock.pdf) |
| qlib | `factor` column; normalize first-day price to 1 **to avoid look-ahead bias**; volume divided by factor | [data docs](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst); [collector.py](https://github.com/microsoft/qlib/blob/main/scripts/data_collector/yahoo/collector.py); [issue #410](https://github.com/microsoft/qlib/issues/410) |
| zipline | raw prices + separate adjustment store (`SQLiteAdjustmentReader`, split-adjusted asof-date) | [API reference](https://zipline.ml4trading.io/api-reference.html) |
| yfinance | `auto_adjust=True` default adjusts OHLC, **volume unadjusted**; semantics debated in [issue #687](https://github.com/ranaroussi/yfinance/issues/687) | [utils.py](https://github.com/ranaroussi/yfinance/blob/6e52c83d9affc7a8a6a209f83d7482c64b24d67b/yfinance/utils.py) |
| Tushare | factor = cumulative backward-adjust factor; qfq = price×factor/latest factor (**dynamic in end_date**); hfq = price×factor; "different sources use different dividend/split/tax logic, so factors differ across sources" | [adj docs](https://tushare.pro/document/2?doc_id=146); [factor API](https://tushare.pro/document/2?doc_id=28); [source-divergence note](https://developer.aliyun.com/article/1747480) |

Selection rules: **backtesting/returns → backward-adjusted or factor method** (history frozen, no look-ahead; qlib #410 rationale); **display/current-price alignment → forward-adjusted**; **storage → raw + factor preferred**; if materializing multiple adjusted columns (our `close`/`close_hfq`), each column's metadata must state adjust type + base date + factor source.

**Data contracts & column metadata (the unit-metadata carriers):** ODCS v3.1.0 (Linux Foundation) — no first-class `unit` field; units go in `description`/`customProperties`/`authoritativeDefinitions`; `customProperties` official example includes `clickhouseType`; Data Contract CLI natively supports ClickHouse for executable checks ([repo](https://github.com/bitol-io/open-data-contract-standard); [schema](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/schema.md); [datacontract.com ODCS page](https://docs.datacontract.com/open-data-contract-standard); [schema checks](https://docs.datacontract.com/schema); predecessor spec with unit-in-description example: [datacontract-specification](https://github.com/datacontract/datacontract-specification)). Andrew Jones: a data contract = schema + semantics + policies + SLOs ([what makes contracts more than schema](https://andrew-jones.com/daily/2024-05-31-what-makes-data-contracts-more-than-a-schema/); [enforce standardisation](https://andrew-jones.com/daily/2024-05-20-enforce-standardisation-with-data-contracts/); [contracts enable data products](https://andrew-jones.com/daily/2024-02-29-data-contracts-enable-data-products/); [101](https://andrew-jones.com/data-contracts-101/)). Catalogs: OpenMetadata column schema ([spec](https://openmetadatastandards.org/data-assets/databases/column/); [repo — ODCS-compatible, ships MCP server](https://github.com/open-metadata/OpenMetadata)); DataHub `SchemaField` with `semanticFieldAnnotation` dimension/measure classification ([PDL](https://github.com/datahub-project/datahub/blob/master/metadata-models/src/main/pegasus/com/linkedin/schema/SchemaField.pdl); [entity docs](https://docs.datahub.com/docs/generated/metamodel/entities/schemafield)); Amundsen ([databuilder](https://github.com/amundsen-io/amundsen/blob/main/databuilder/README.md)); dbt column `description`/`meta` + **`persist_docs` writing descriptions to database COMMENTs** ([columns](https://docs.getdbt.com/reference/resource-properties/columns); [meta](https://docs.getdbt.com/reference/resource-configs/meta); [persist_docs](https://docs.getdbt.com/reference/resource-configs/persist_docs)).

### 5.6 Column disambiguation (similar column names)

Problem confirmation: EACL 2026 findings — "schemas with **highly similar column names** may still lead to selection errors" ([paper](https://aclanthology.org/2026.findings-eacl.236.pdf)); KaSLA — missing a single required column guarantees wrong SQL ([arXiv:2502.12911](https://arxiv.org/html/2502.12911v2)); real incident: `gl_amount` vs `gl_operating_amount`, distinguishable only by business rules ([Datus](https://datus.ai/blog/what-is-schema-linking/)); ambiguity-specific dataset: LinkAlign AmbiDB (§5.4); BIRD — external knowledge (incl. column descriptions) is essential: without it ChatGPT 40.08% vs human 92.96% ([BIRD paper](https://arxiv.org/abs/2305.03111)).

Proven solutions: Cube paired benchmark +17–23pp from a 4KB disambiguation-rules document (§5.1③); CData's synonym-map metadata model — `synonyms` / `ambiguous_with` / `grain` / `excludes` ([CData insights](https://cdatainsights.com/blogs/semantic-layer-synonyms-cutting-time-to-answer)); DataHub `semanticFieldAnnotation` dimension/measure grouping (§5.5); Dremio's wiki/synonyms-as-agent-interface ([blog](https://www.dremio.com/blog/data-meaning-why-the-semantic-layer-is-the-brain-of-agentic-analytics/)).

---

## 6. Recommended Architecture (L0–L4)

### 6.1 Target architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CONSUMERS                                                                    │
│  OpenCode agent (Vibe-Trading MCP, 70+ tools)   ·   Human (CLI / DBeaver)    │
└──────────────┬───────────────────────────────────────────────┬───────────────┘
               │ MCP tool calls                                │ direct SQL
               ▼                                               ▼
┌──────────────────────────────────────────────┐   ┌───────────────────────────┐
│ L1  DOMAIN TOOLS  (existing F5, hardened)    │   │ L2  HIERARCHICAL          │
│  get_market_data · get_fund_flow ·           │   │     EXPLORATION (new)     │
│  get_margin_trading · get_dragon_tiger ·     │   │  ch_list_tables           │
│  get_northbound_flow · get_valuation (new)   │   │  ch_describe_table        │
│  units/calibers fixed in code path;          │   │     (COMMENTs + samples   │
│  _provenance carries unit metadata           │   │      from system.columns) │
│  (unit / adjust / caliber)                   │   │  ch_query ──► L3 guards   │
└──────────────┬───────────────────────────────┘   └─────────────┬─────────────┘
               │ parameterized SQL (fixed templates)             │ validated SQL
               ▼                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ L0  SEMANTIC FOUNDATION (ClickHouse, database-side) — the prerequisite       │
│   • DDL in repo: git-versioned CREATE TABLE, single source of truth          │
│   • COMMENT COLUMN structured convention on all 199 columns:                 │
│       "unit=lot; adjust=raw; caliber=tushare daily.vol; ambiguous_with=…"    │
│   • llm_role: GRANT SELECT ON ashare.* ONLY (Issue #131 lesson:              │
│     read-only MUST be enforced at DB-user level, not query-level setting)    │
│     + max_execution_time=30 / max_memory_usage=2G / max_rows_to_read / …     │
│   • system.columns.comment = the machine-readable catalog                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ L4  METRICS DICTIONARY (skills / AGENTS.md form) — injected into L1 tool     │
│   descriptions and L2/L3 context: pe_ttm TTM caliber · volume canonical      │
│   unit = lot (#1062 decision) · amount units · close vs close_hfq selection  │
│   rules · margin/northbound column glossary · 10–20 verified gold queries    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Layer specifications

| Layer | What | Guardrails / evidence |
|---|---|---|
| **L0** | DDL in repo; structured COMMENT convention (`unit=` / `adjust=` / `caliber=` / `source=` / `ambiguous_with=`); `llm_role` read-only user + resource quotas | Official production guide (§3.4); Altinity proves COMMENTs are LLM-consumable (§5.2); #131 lesson (§3.2) |
| **L1** | Existing F5 domain tools, three fixes: ① explicit-tool the `SELECT *` leak path (`get_valuation` for pe_ttm/pb/total_mv with tushare daily_basic fallback); ② `_provenance` unit metadata (align #1065/#1067, extend to unit/adjust/caliber); ③ unit conversions become metadata-driven instead of hardcoded | hdx-evals +7–20pp (§3.3); dbt in-scope ~100% + explicit failures (§4.1) |
| **L2** | Three new tools: `ch_list_tables` (name + one-line description) → `ch_describe_table` (single-table columns/types/COMMENTs/samples/partition key) → `ch_query` | MCP official three-tier discovery (§5.3); AutoLink ~90% recall at 3000+ columns (§5.4) |
| **L3** | Constrained SQL inside `ch_query`: connect as `llm_role`; sqlglot SELECT-only AST guard with table whitelist; parameterized queries (no string interpolation — [mcp-clickhouse PR #146](https://github.com/ClickHouse/mcp-clickhouse/pull/146) rationale); result cap default 500 rows / 50KB with explicit truncation notice; forced LIMIT injection; 30s timeout | Altinity two-tier caps (§5.2); Spider 2.0/BEAVER unguarded-SQL accuracy (§5.1①); #111 UInt64 corruption ⇒ self-built serialization, not the official server (§3.2) |
| **L4** | Metrics dictionary + gold queries in skill/AGENTS.md form | Snowflake verified queries / Genie example SQL (§5.3); Spider 2.0 business-doc questions 11.5% correct (§5.1①); ClickHouse Assistant AGENTS.md pattern (§5.2) |

### 6.3 Data-flow walkthroughs (where semantics apply — and where they used to be lost)

**Flow A — today's protected path (L1, unchanged by this plan):**

```
agent: get_market_data(["000001.SZ"], 2023-01-01..2025-12-31)
  → loader registry: a_share chain, clickhouse at head
  → loaders/clickhouse.py: SELECT * FROM stk_factor_pro WHERE ts_code=… (cached)
      └─ federates today's bar from the network chain when end_date ≥ today
  → _normalize_ch_frame: vol→volume, numeric coercion
  → envelope + _provenance {source: clickhouse, volume_unit: lot (after L1 fix ②)}
Semantics: units/calibers enforced by code path. LLM never sees raw columns.
```

**Flow B — the leak the plan closes (L1 fix ①):**

```
BEFORE:  get_market_data fields=[pe_ttm,pb,total_mv] → SELECT * pass-through,
         ~199 unannotated columns reach the LLM → caliber guessing, contract drift
AFTER:   get_valuation(symbol, date_range) → fixed template SELECT pe_ttm, pb,
         total_mv + COMMENTs attached + tushare daily_basic fallback
```

**Flow C — new flexibility path (L2→L3), replacing unprotected direct SQL:**

```
agent: "turnover_rate percentile of 000001.SZ over 2025"
  → ch_list_tables                     (names + one-liners only, small context)
  → ch_describe_table(stk_factor_pro)  (COMMENTs incl. unit=/caliber= + 2–3 sample rows)
  → ch_query(SELECT …)                 (L3: AST guard → parameterize → LIMIT inject
                                        → llm_role executes → ≤500 rows / 50KB cap)
Semantics: travel WITH the data via COMMENT; guardrails bound cost & blast radius.
```

**Flow D — human exploration (Option A, now conditioned on L0):**

```
analyst (DBeaver/CLI, connected as llm_role) → SELECT …
  • read-only enforced by GRANT, resource limits by role settings
  • COMMENTs visible via DESCRIBE / system.columns → units self-documenting
```

### 6.4 Rejected alternatives (with reasons)

| Rejected | Reason |
|---|---|
| Official mcp-clickhouse as primary interface | §3.2 defects (#111 UInt64 corruption open; #131 read-only bypass; no result caps); hdx-evals shows raw SQL inferior (§3.3) |
| Pure F5 with no flexibility channel | ad-hoc needs are real; without a protected channel users bypass the tool chain (back to unprotected state) — §4.2 gap 4 |
| dbt Semantic Layer | consumption APIs (GraphQL/JDBC) are paid dbt Cloud; single-consumer scenario fails the value criterion; API consumption excludes SQL-native users (§5.2) |
| Cube.dev runtime | excellent patterns (kept as reference) but adds an always-on service for one consumer; vendor AI content carries stated bias (§5.2) |
| Full Altinity-style view→tool automation now | right direction, wrong first step — no view layer exists yet; revisit in Phase 3 (§8) |

---

## 7. Scenario Guide: Which Solution for Which Scenario

| # | Scenario | Chosen path | Why this / why not the alternatives |
|---|---|---|---|
| S1 | Backtest pipeline needs OHLCV for 500 A-shares, 2020–2025 | **L1** (CH loader, `SELECT *` cached) | Highest-throughput fixed intent; units locked to lot at chain head; raw MCP would add per-call schema overhead and zero protection |
| S2 | Agent asks for fund-flow / margin / northbound | **L1** domain tools | Conversions (×10⁴, ×100) deterministic; envelopes stable; after L1 fix ③ conversions read from L0 metadata |
| S3 | Agent asks "PE/PB/market-cap of 600519.SH last quarter" | **L1 `get_valuation`** (new) | Closes the `SELECT *` leak; fixed template + caliber annotation + tushare fallback |
| S4 | Agent asks an ad-hoc question with no dedicated tool ("turnover percentile", "correlation of two columns") | **L2 → L3** exploration + constrained SQL | Flexibility with guardrails; COMMENTs supply semantics; raw mcp-clickhouse would supply neither caps nor units |
| S5 | Human data audit: "is 600519.SH volume on 2026-07-31 correct?" | **Direct SQL as `llm_role`** (or L3) | COMMENT states unit=lot; cross-check amount/volume≈price (the #1062 empirical method); before L0 this audit had no in-DB semantics at all |
| S6 | A second consumer appears (Web UI direct query, another agent) | **L2/L3 gateway** | Semantics travel with COMMENTs — the core dividend of sinking semantics into the DB; code-carried semantics (Option B as-is) cannot serve a second consumer |
| S7 | Counter-example: raw mcp-clickhouse as backtest data source | ❌ rejected | LLM faces 199 un-COMMENTed columns (today zero), may `SELECT close` across ex-dividend dates (wrong returns — upstream v0.1.13 fixed a 47pp instance of this class); UInt64 corruption risk on total_mv/amount (#111); no result caps |
| S8 | Counter-example: deploy dbt SL / Cube now | ❌ deferred | One consumer; paid consumption APIs (dbt SL); extra runtime (Cube); YAML-rot risk without a maintenance loop (a16z). Revisit when a second non-SQL consumer exists |

**Decision rule of thumb.** Fixed intent + unit-sensitive → L1. Unknown intent + human-in-loop or guardrailed → L2/L3. Human expert exploration → direct SQL under `llm_role`. Never: unguarded raw SQL with semantics living only in Python.

---

## 8. Roadmap & Acceptance Criteria

| Phase | Content | Effort | Risk |
|---|---|---|---|
| **Phase 0** (foundation, first) | export full DDL from the CH instance into the repo; structured COMMENTs on all 199 columns; create `llm_role` + resource quotas | 1–2 days | none (pure additive, no runtime path changes) |
| **Phase 1** (primary-channel hardening, coordinate with #1065/#1067) | `_provenance` unit metadata; `get_valuation` explicit tool; metadata-driven conversions | 3–5 days | low (existing CH test suite baseline 13 passed / 8 skipped) |
| **Phase 2** (flexibility channel, on demand) | `ch_list_tables` / `ch_describe_table` / `ch_query` + full L3 guardrails + audit log | 3–5 days | medium (new MCP tools ⇒ README/SKILL.md count gates: five READMEs + agent/SKILL.md) |
| **Phase 3** (optional evolution) | golden-question regression baseline; parameterized-view→tool automation (Altinity pattern); ODCS YAML contracts; upstream the semantic layer as an independent PR | on demand | low |

Acceptance criteria: after Phase 0 — `SELECT comment FROM system.columns WHERE table='stk_factor_pro' AND comment=''` returns empty; after Phase 1 — `get_market_data` envelopes carry unit metadata and `get_valuation` covers the PE/PB/MV intent; after Phase 2 — golden-set pass rate ≥90%, L3 refuses every non-SELECT, results above 500 rows truncated with explicit notice.

---

## 9. Additional Architectural Concerns

1. **Semantic-drift governance (top-priority debt).** The a16z "stale YAML from the person who left" failure applies to COMMENTs too: DDL/COMMENT must live in git behind a **CI gate** (mirroring `tools/ci_env_var_gate.py`: assert every `stk_factor_pro` column has a non-empty COMMENT in the repo DDL, reconcilable against `system.columns`). Ungated semantics rot within months.
2. **Single source of truth.** Unit conversions currently live in two places (fallbacks code `×10⁴`/`×100` + tool descriptions/skills). Building L0 without L1 fix ③ creates a third. Metadata-driven conversion is the keystone fix.
3. **Security boundary.** The CH instance is VPC-internal, no TLS, password in `.env` — acceptable while only the agent uses it; if L2/L3 is ever exposed to a wider surface, add authentication (official Remote MCP OAuth model; our own `VIBE_TRADING_MCP_ALLOWED_HOSTS` DNS-rebinding experience).
4. **Observability.** Log every agent-generated L2/L3 query (Altinity audit-log pattern) — debugging material and the mining source for gold queries (Genie's benchmark-driven loop depends on query logs).
5. **Testing baseline.** A golden-question set (10–20 items incl. unit traps, adjustment traps, similar-column traps) as the regression baseline; complementary to #1067's cross-source consistency tests (those test numeric agreement; the golden set tests semantic communication).
6. **MCP tool-count governance.** 70+ existing tools plus new `ch_*` tools — watch Anthropic's threshold (tool definitions >1–5% of context ⇒ progressive discovery); OpenBB-style category activation or namespacing is the contingency, not this phase's work.
7. **Upstream dependency.** L1 fix ② couples to PR #1065/#1067 — extend them if merged; if stalled, implement on mymain in rebase-friendly form.
8. **Resist over-engineering.** Single consumer today: no dbt SL / Cube / OpenMetadata / DataHub. Their value criterion is consumer diversity (Stancil). Keep the interface open (YAML contracts are the staircase), don't pay in advance.

---

## 10. Evidence-Strength Statement

- Peer-reviewed / primary: Spider 2.0 (ICLR 2025), BEAVER, AutoLink (AAAI 2026), TriSQL (Nature Sci Rep), BIRD (NeurIPS 2023), LinkAlign (EMNLP 2025), EACL/EDBT 2026 papers; mcp-clickhouse & altinity-mcp source reads; ClickHouse official docs & WRDS/CRSP/Tushare/AkShare primary docs; ISO 20022 / ODCS / FDC3 specifications.
- Official but domain-bounded: hdx-evals numbers are observability-domain — mechanism generalizes, specific numbers do not transfer to A-share queries; recalibrate via the Phase 3 golden set.
- Vendor-run but reproducible: dbt SL benchmark (open-sourced), Cube paired benchmark (open-sourced).
- Single-source / directional only: tianpan.co percentages; Bloomberg field units (no public spec, confidence medium); DWAINE resolution rates (official self-report).
- Local empirical: 2026-08-11 MCP-path test (session `ses_01133972`), #1062 audit (volume 手 vs 股, 100× verified on 600519.SH 2026-07-31), F5 code forensics (2026-08-12).
