# D2-C0: Deterministic Audit of the 30 Swarm Presets

Scope: all 30 bundled presets in `agent/src/swarm/presets/*.yaml` (30/30 files,
119 agent specs). Read-only audit; no preset or runtime code modified; no swarm
or backtest executed. Findings derive from the preset YAMLs plus the swarm
runtime source cited inline.

---

## 1. Enforcement verdict (highest priority)

**Verdict: partially enforced.** The per-agent `tools:` field is a **hard
allowlist at runtime** (enforced), while the per-agent `skills:` field is
**prompt-text filtering only** (advisory). Shell-family tools sit behind a
second, entry-point-level gate that the preset cannot open by itself.

### 1a. `tools:` — ENFORCED (allowlist intersection + fail-closed dispatch)

Evidence chain, load → build → expose → dispatch:

1. `agent/src/swarm/presets.py:312-322` — `build_run_from_preset` copies the
   YAML `tools:` list verbatim into `SwarmAgentSpec.tools` (no expansion, no
   defaults injected).
2. `agent/src/swarm/models.py:174-199` — `SwarmAgentSpec`; the docstring
   (line 183) documents `tools` as "Whitelist of allowed tool names".
3. `agent/src/swarm/runtime.py:1137-1145` — the runtime passes the unmodified
   `agent_spec` into `run_worker(...)` (also `runtime.py:520` threading
   `include_shell_tools` through layer execution).
4. `agent/src/swarm/worker.py:538-542` — `run_worker` builds the worker's
   registry with `build_swarm_registry(agent_spec.tools, ...)`.
5. `agent/src/tools/__init__.py:297-338` — `build_swarm_registry` builds the
   full local+MCP pool, then `_filter_registry`
   (`agent/src/tools/__init__.py:372-391`) **intersects** it with the
   whitelist: only names present in `agent_spec.tools` are registered. A
   requested-but-unavailable name is dropped with a log warning
   (`__init__.py:385-390`) — the worker still runs, minus that tool.
6. `agent/src/swarm/worker.py:687` — the LLM only ever sees
   `registry.get_definitions()`, i.e. the filtered set.
7. `agent/src/agent/tools.py:97-131` — `ToolRegistry.execute` dispatches by
   `self._tools.get(name)`; a name outside the filtered registry returns a
   fail-closed `{"status": "error", "error": "Tool '<name>' not found"}`
   envelope. A model that hallucinates a non-whitelisted tool cannot execute
   it.

Caveats on the enforcement: (i) the drop of an unavailable whitelisted tool is
**advisory** (log warning only, worker proceeds) — the YAML author is not told
their grant is dead; (ii) there is **no deny-list anywhere**: enforcement is
allowlist-only, so the guarantee is "nothing unlisted runs", not "listed
dangerous things are blocked"; (iii) OMO-harness builtins named in the brief
(`websearch`/`context7`/`grep_app`/`lsp`) do not exist in this registry at
all — a preset naming one is silently dropped per (i). If a host harness
composes its own tool surface around this runtime, nothing on the repo side
guards it.

### 1b. `skills:` — ADVISORY ONLY (prompt filtering; bypassable via `load_skill`)

1. `agent/src/swarm/worker.py:546` + `worker.py:113-129` —
   `_filter_skill_descriptions` uses `agent_spec.skills` only to decide which
   skill *descriptions* are rendered into the system prompt. It grants or
   revokes no capability.
2. `agent/src/tools/load_skill_tool.py:213-216` — `LoadSkillTool.execute`
   loads **any** skill by name via `self._loader.get_content(name)`; there is
   no check against the calling agent's `skills:` list.
3. 117 of 119 agents (28/30 presets) hold the `load_skill` tool (see §3), so
   for them the `skills:` list is cosmetic: any of the 90 skills in
   `agent/src/skills/` is one tool call away. The only two agents without
   `load_skill` are `risk_committee/aggregator` and
   `technical_analysis_panel/signal_aggregator`.

The model file documents `skills` as "List of allowed skill names"
(`models.py:184`) — the documentation claims an allowlist that the runtime
does not enforce.

### 1c. Shell tools — double-gated (preset grant ∧ entry-point opt-in)

`bash` / `background_run` / `cancel_background` are registered only when the
entry point enables them:

- `agent/src/tools/__init__.py:31` (`_SHELL_TOOL_NAMES`) and
  `__init__.py:156-158` — `build_registry` skips shell tools unless
  `include_shell_tools=True`, regardless of what the preset asks for.
- CLI interactive swarm: enabled unconditionally —
  `agent/cli/_legacy.py:2402` and `_legacy.py:2454`
  (`include_shell_tools=True`).
- MCP server: default off — `agent/mcp_server.py:105`
  (`_include_shell_tools = False`), resolved at
  `mcp_server.py:115-135` (`_resolve_include_shell_tools`: CLI flag or env
  only; docstring cites GHSA-6wjh-cc6v-xfrx / GHSA-m768-22r9-h4x7), consumed
  at `mcp_server.py:1665-1666`.
- HTTP API: `agent/src/api/swarm_routes.py:101` →
  `agent/src/api/security.py:561-563` (`_shell_tools_enabled_for_request` →
  env only) → `agent/src/config/env_schema.py:296`
  (`VIBE_TRADING_ENABLE_SHELL_TOOLS`, default `False`).

Consequence: a preset that grants `bash` gets it on the interactive CLI and
silently loses it on API/MCP surfaces (dropped at registry build per 1a(i)).
The same YAML therefore describes two different capability sets depending on
entry point — an honesty gap the preset layer does not disclose.

### 1d. Adjacent enforcement facts worth knowing for C1

- `worker.py:300` (`has_code_tools`) and `worker.py:220` — the prompt text
  branches on the *content* of `agent_spec.tools`, confirming the whitelist is
  the operative contract the prompt itself is built around.
- MCP tools enter only as operator-configured `mcp_<server>_<tool>` names:
  `_prune_agent_config_for_swarm_tools` (`__init__.py:341-369`) prunes the
  agent config to servers whose prefix the whitelist explicitly names. An
  external MCP client driving `run_swarm` cannot inject server URLs
  (`__init__.py:313-316`).

---

## 2. Twin-pair audit (tool names colliding with skill names)

Method: normalized names (`_`→`-`, lowercase), then (a) exact match between a
granted tool and a known skill in `agent/src/skills/`, (b) stem match after
stripping `-analysis`/`-strategy`/`-research`/`-fetch`, (c) reverse gap — a
granted skill whose same-named tool exists in the registry (113 tools
inventoried from `agent/src/tools/*`) but is NOT granted to that agent.
Purely functional companion pairs (skill is the tool's manual, e.g.
`get_market_data`↔`yfinance`, `backtest`↔`strategy-generate`,
`read_url`↔`web-reader`, `factor_analysis`↔`factor-research`) are by design
and listed only as notes.

**Headline: no agent holds both members of an exact twin pair simultaneously
in its YAML. But because `skills:` is advisory (§1b), the exact twin
`options_payoff` (tool) ↔ `options-payoff` (skill) is materializable at
runtime by the two agents holding the tool plus `load_skill`.**

| # | Preset | Exact twin (tool↔skill) | Capability gap (skill granted, twin tool withheld) | Companion-pair notes |
|---|--------|------------------------|----------------------------------------------------|----------------------|
| 1 | commodity_research_team | — | — | read_url↔web-reader (supply_analyst) |
| 2 | convertible_bond_team | — | option_analyst: `options-payoff` w/o `options_payoff` | options_pricing↔options-strategy |
| 3 | credit_research_team | — | — | — |
| 4 | crypto_research_lab | — | crypto_sentiment_analyst: `sentiment-analysis` w/o `sentiment` | get_market_data↔okx-market |
| 5 | crypto_trading_desk | — | — | get_market_data↔okx-market |
| 6 | derivatives_strategy_desk | — | strategy_designer, greeks_manager: `options-payoff` w/o `options_payoff` | options_pricing/get_options_chain↔options-* |
| 7 | earnings_research_desk | — | — | get_options_chain/options_pricing↔options-* (event_options_analyst) |
| 8 | equity_research_team | — | — | get_market_data↔tushare/yfinance/okx-market |
| 9 | etf_allocation_desk | — | — | — |
| 10 | event_driven_task_force | — | impact_analyst: `sentiment-analysis` w/o `sentiment` | — |
| 11 | factor_research_committee | — | — | factor_analysis↔factor-research (intended) |
| 12 | fund_selection_panel | — | — | — |
| 13 | fundamental_research_team | — | — | read_url↔web-reader (quality_analyst) |
| 14 | geopolitical_war_room | — | — | read_url↔web-reader |
| 15 | global_allocation_committee | — | — | get_market_data↔tushare/okx-market/yfinance |
| 16 | global_equities_desk | — | — | get_market_data↔tushare/yfinance/okx-market |
| 17 | investment_committee | risk_officer: `options_payoff` tool granted; same-named skill `options-payoff` exists (not in its `skills:`, but loadable via its `load_skill`) | bull_advocate: `sentiment-analysis` w/o `sentiment` | get_market_data↔yfinance |
| 18 | macro_rates_fx_desk | — | — | — |
| 19 | macro_strategy_forum | — | — | read_url↔web-reader |
| 20 | ml_quant_lab | — | — | factor_analysis↔factor-research (intended) |
| 21 | pairs_research_lab | — | — | — |
| 22 | portfolio_review_board | — | — | — |
| 23 | quant_strategy_desk | — | — | factor_analysis↔factor-research, backtest↔strategy-generate (intended) |
| 24 | risk_committee | tail_risk_analyst: `options_payoff` tool granted; skill `options-payoff` exists (not in its `skills:`, loadable via `load_skill`) | — | technical_indicators↔technical-basic (regime_detector) |
| 25 | sector_rotation_team | — | flow_analyst: `sentiment-analysis` w/o `sentiment` | — |
| 26 | sentiment_intelligence_team | — | news_analyst, social_analyst, flow_analyst: `sentiment-analysis` w/o `sentiment` | read_url↔web-reader |
| 27 | social_alpha_team | — | twitter_analyst, telegram_analyst, reddit_analyst: `sentiment-analysis` w/o `sentiment` | — |
| 28 | statistical_arbitrage_desk | — | — | — |
| 29 | technical_analysis_panel | — | — | pattern↔harmonic/elliott-wave; technical_indicators↔technical-basic |
| 30 | value_investing_committee | — | — | — |

Twin-pair tallies: exact-twin tool grants 2 agents / 2 presets
(`options_payoff`); `options-payoff` skill without its tool 3 slots / 2
presets; `sentiment-analysis` skill without the `sentiment` tool 10 slots / 6
presets. The brief's example pair (`sentiment` vs `sentiment-analysis`) never
collides head-on because no preset grants the `sentiment` tool — it manifests
instead as the capability gap above.

## 3. bash / shell / write grant audit

Every one of the 119 agents holds `bash` + `read_file` + `write_file`; 117/119
also hold `load_skill`. Shell effectiveness is entry-point gated (§1c): live
on interactive CLI, silently dropped on API/MCP without
`VIBE_TRADING_ENABLE_SHELL_TOOLS=1`.

| # | Preset | Agents | bash | write_file | edit_file | load_skill | Notes |
|---|--------|-------|------|------------|-----------|------------|-------|
| 1 | commodity_research_team | 3 | 3 | 3 | 0 | 3 | — |
| 2 | convertible_bond_team | 4 | 4 | 4 | 0 | 4 | — |
| 3 | credit_research_team | 4 | 4 | 4 | 0 | 4 | — |
| 4 | crypto_research_lab | 4 | 4 | 4 | 0 | 4 | — |
| 5 | crypto_trading_desk | 4 | 4 | 4 | 0 | 4 | — |
| 6 | derivatives_strategy_desk | 3 | 3 | 3 | 0 | 3 | — |
| 7 | earnings_research_desk | 4 | 4 | 4 | 0 | 4 | — |
| 8 | equity_research_team | 4 | 4 | 4 | 0 | 4 | — |
| 9 | etf_allocation_desk | 4 | 4 | 4 | 0 | 4 | — |
| 10 | event_driven_task_force | 3 | 3 | 3 | 0 | 3 | — |
| 11 | factor_research_committee | 4 | 4 | 4 | 0 | 4 | — |
| 12 | fund_selection_panel | 3 | 3 | 3 | 0 | 3 | — |
| 13 | fundamental_research_team | 4 | 4 | 4 | 0 | 4 | — |
| 14 | geopolitical_war_room | 4 | 4 | 4 | 0 | 4 | — |
| 15 | global_allocation_committee | 4 | 4 | 4 | 0 | 4 | — |
| 16 | global_equities_desk | 4 | 4 | 4 | 0 | 4 | — |
| 17 | investment_committee | 4 | 4 | 4 | 0 | 4 | — |
| 18 | macro_rates_fx_desk | 4 | 4 | 4 | 0 | 4 | — |
| 19 | macro_strategy_forum | 4 | 4 | 4 | 0 | 4 | — |
| 20 | ml_quant_lab | 3 | 3 | 3 | 1 (data_scientist) | 3 | edit_file justified (iterate on feature code) |
| 21 | pairs_research_lab | 4 | 4 | 4 | 0 | 4 | — |
| 22 | portfolio_review_board | 4 | 4 | 4 | 0 | 4 | — |
| 23 | quant_strategy_desk | 5 | 5 | 5 | 1 (backtester) | 5 | edit_file justified (fix signal_engine) |
| 24 | risk_committee | 4 | 4 | 4 | 0 | 3 | aggregator has no load_skill |
| 25 | sector_rotation_team | 4 | 4 | 4 | 0 | 4 | — |
| 26 | sentiment_intelligence_team | 4 | 4 | 4 | 0 | 4 | — |
| 27 | social_alpha_team | 4 | 4 | 4 | 0 | 4 | — |
| 28 | statistical_arbitrage_desk | 4 | 4 | 4 | 0 | 4 | — |
| 29 | technical_analysis_panel | 6 | 6 | 6 | 0 | 5 | signal_aggregator has no load_skill |
| 30 | value_investing_committee | 5 | 5 | 5 | 0 | 5 | also `web_search` on 4 analysts |

## 4. Over-broad / mismatched grants (judgment per case)

The structural finding: tool hygiene is **flat** — the base kit
(`bash, read_file, write_file, load_skill`) is granted to every role including
pure text-synthesis roles that the worker prompt itself acknowledges "lack
data tools" (`worker.py:319-331`). `write_file` is defensible for synthesis
roles (report contract), `bash` is not. Flags below use OB = over-broad,
UP = under-provisioned (grant too narrow for the named role), CG = capability
gap (from §2).

| # | Preset | Flagged agents | Judgment |
|---|--------|---------------|----------|
| 1 | commodity_research_team | — | OK — 3 analysts with matching data posture |
| 2 | convertible_bond_team | option_analyst (CG) | Skill demands payoff computation; `options_payoff` tool withheld — will improvise in bash |
| 3 | credit_research_team | rate_analyst, sector_credit_analyst (mild) | Both hold only read_url as a data source for rate/sector-credit claims — thin but defensible |
| 4 | crypto_research_lab | crypto_sentiment_analyst (CG); alpha_synthesizer (OB-bash) | Synthesizer is text-only but holds bash |
| 5 | crypto_trading_desk | — | OK |
| 6 | derivatives_strategy_desk | strategy_designer, greeks_manager (CG) | options-payoff skill without options_payoff tool, ×2 |
| 7 | earnings_research_desk | earnings_strategist (borderline) | `backtest` on the final-call role — defensible (thesis validation), keep |
| 8 | equity_research_team | aggregator (OB-bash) | Report editor needs write_file, not bash |
| 9 | etf_allocation_desk | — | OK |
| 10 | event_driven_task_force | impact_analyst (CG, 8 tools) | Broadest non-synthesis list here; all pieces defensible except sentiment gap |
| 11 | factor_research_committee | backtest_reviewer (UP) | "Backtest Reviewer" holds neither `backtest` nor `factor_analysis` — cannot re-run what it reviews |
| 12 | fund_selection_panel | attribution_analyst (OB, 9 tools) | `get_stock_profile` + `get_fundamentals` are single-stock tools on a fund-attribution role |
| 13 | fundamental_research_team | report_editor (OB-bash) | Editor role with bash |
| 14 | geopolitical_war_room | chief_strategist (OB-bash) | Synthesis role with bash |
| 15 | global_allocation_committee | — | OK |
| 16 | global_equities_desk | — | OK |
| 17 | investment_committee | risk_officer (twin + 8 tools, borderline) | Options payoff/pricing on a CRO role is defensible; the exact-twin with `options-payoff` skill is the real issue |
| 18 | macro_rates_fx_desk | — | OK |
| 19 | macro_strategy_forum | chief_strategist (OB-bash) | Synthesis role with bash |
| 20 | ml_quant_lab | — | OK — edit_file on data_scientist is the one justified mutation grant |
| 21 | pairs_research_lab | microstructure_reviewer (mild UP) | Reviewer holds no data tool; review is upstream-only — acceptable for a review gate |
| 22 | portfolio_review_board | chief_investment_officer (OB-bash); execution_analyst (conditional-availability) | `analyze_trade_journal` reads broker exports from upload roots; a swarm run has no upload → granted tool with no reachable input |
| 23 | quant_strategy_desk | risk_auditor (UP), report_aggregator (OB-bash) | "Risk Auditor" holds no data tool and no `backtest` — audits on trust; aggregator holds bash it never needs |
| 24 | risk_committee | aggregator (OB-bash) | Head of Risk is synthesis-only; no load_skill either (the one place skills: is actually binding) |
| 25 | sector_rotation_team | cycle_analyst (UP) | "Economic Cycle Analyst" holds zero data tools and no read_url — cannot observe any cycle indicator |
| 26 | sentiment_intelligence_team | signal_synthesizer (OB-bash); 3× sentiment CG | The preset's core competency (sentiment scoring) is skill-only everywhere |
| 27 | social_alpha_team | 3× sentiment CG | Same gap; alpha_synthesizer's `factor_analysis` is borderline but skills justify it |
| 28 | statistical_arbitrage_desk | — | OK |
| 29 | technical_analysis_panel | signal_aggregator (OB-bash) | Judge role with bash; no load_skill (skills: binding here too) |
| 30 | value_investing_committee | 4 analysts × 8 tools incl. `web_search` (note) | Broadest fetch tool in the roster on deep-research roles — role-justified, but `web_search` deserves explicit scoping in C1 |

## 5. Summary counts

| Metric | Count |
|--------|-------|
| Presets audited | 30 / 30 |
| Agent specs total | 119 |
| `tools:` enforcement | Enforced (allowlist intersection + fail-closed dispatch) |
| `skills:` enforcement | Advisory (prompt-only; bypass via `load_skill`) |
| Agents holding `bash` | 119 / 119 (100%; entry-point gated — §1c) |
| Agents holding `write_file` | 119 / 119 |
| Agents holding `edit_file` | 2 (ml_quant_lab/data_scientist, quant_strategy_desk/backtester) |
| Agents holding `load_skill` | 117 / 119 (skills: cosmetic for these) |
| Presets with ≥1 shell-grant agent | 30 / 30 |
| Exact twin-pair grants (`options_payoff` tool ↔ `options-payoff` skill) | 2 agents / 2 presets (tool side); 0 agents hold both members in YAML |
| Skill-without-tool gaps | 13 slots / 8 presets (options-payoff ×3, sentiment-analysis ×10) |
| Over-broad flags (OB) | 10 synthesis-role bash grants + fund_selection/attribution_analyst |
| Under-provisioned flags (UP) | 3 (cycle_analyst, risk_auditor, backtest_reviewer) |
| Deny-list mechanism | None anywhere (allowlist-only) |

## 6. Implications for the C1 pilot

Recommended pilot trio (quant_strategy_desk is mandated; two heterogeneous
others chosen to maximize coverage of the finding classes above):

1. **quant_strategy_desk** (mandated) — 5 agents, the fullest pipeline shape
   (screen → factor → backtest → audit → report). Contains the only
   C1-relevant `edit_file` grant (backtester), an under-provisioned
   `risk_auditor`, and a bash-holding `report_aggregator`: exercises the
   write/mutation boundary, the under-provisioning failure mode, and the
   flat-base-kit issue in one preset.
2. **investment_committee** — the flagship debate preset and the only one
   carrying the *exact* twin (`risk_officer`: `options_payoff` tool with the
   `options-payoff` skill one `load_skill` call away) **plus** a sentiment
   gap (`bull_advocate`) **plus** the widest per-agent tool spread (6–8
   tools). Best single preset for twin-pair arbitration measurement.
3. **sentiment_intelligence_team** — the densest capability-gap class (3 of 4
   agents told to do sentiment scoring via the `sentiment-analysis` skill
   while the `sentiment` tool is withheld) on unstructured-data inputs
   (news/social/flow), the polar opposite of quant_strategy_desk's numeric
   pipeline. This is where honest "capability unavailable" disclosure
   contracts will be stress-tested hardest.

C1 design inputs this audit feeds:

- **Enforcement asymmetry is the first thing to fix or to exploit**: `tools:`
  is already a hard allowlist — the C1 pilot can rely on it; `skills:` is not
  — any skill-side hygiene rule needs either a runtime check in
  `LoadSkillTool` or must be treated as prompt-only in the experiment design.
- **bash is universal and entry-point-dependent**: C1 must record which
  surface it runs on; a pilot on the interactive CLI measures a different
  capability set than the same preset over MCP.
- **Dropped-grant silence**: `_filter_registry` warns but runs
  (`__init__.py:385-390`) — the pilot should capture these warnings as
  first-class evidence, since a preset depending on a missing tool currently
  degrades without telling the operator.
- **Deny-coverage**: there is no deny-list to extend; if C1 wants
  S5-leak-class coverage (host-harness namespaces), it must be built — the
  current guarantee is purely "not listed ⇒ not runnable" and applies only to
  this repo's own registry.

---

*Method note: all preset numbers were computed by parsing the 30 YAMLs with
`yaml.safe_load` (same loader as `presets.py:108`) and intersecting against a
113-name tool inventory greped from `agent/src/tools/*.py` and the 90 skill
directories under `agent/src/skills/`. No preset, runtime, or test file was
modified; no swarm or backtest was executed.*

---

## 附录：C1 试点落地与冒烟（2026-08-29，闭环 C0 → C1）

C0 审计 → C1 试点（quant_strategy_desk / investment_committee /
sentiment_intelligence_team，commit `442c8c62`）：

1. **仲裁句移植**：所有持有孪生对的 agent 的 system_prompt 加入
   "Tool arbitration (decide by verb)" 句（factor_analysis×factor-research、
   backtest×strategy-generate、get_market_data×tushare/yfinance、
   read_url×web-reader、options_payoff×options-payoff 精确孪生）。
2. **能力缺口修复**：4 个持 sentiment-analysis 技能而无 sentiment 工具的
   agent 补发 sentiment 工具。
3. **诚实披露契约**：13 个 agent 全部加入"缺能力即显式声明限制"句。
4. **risk_auditor 保持无数据工具**（by design，行内注释记录裁决理由：
   其证据基线是上游回测产物，授予数据工具反而诱使其重算而非审计）。
5. **守门人回归**：`pytest -k "swarm or preset"` 496 passed / 4 skipped
   （含 claim-backing 门）；冒烟前抓出并修复一处子代理编辑引入的
   空白符污染（投资委员会 prompt 行）。

**冒烟证据**（sentiment_intelligence_team 全链路实跑，
run `swarm-20260829-083056-ac7fca88`，16m21s，~750k tokens）：
新增 sentiment 工具被真实调用 **54 次**（修复前为 0——技能在手、工具缺席），
read_url ×273 / load_skill ×56；3 个 agent 的报告中情绪指标落地。
能力缺口修复从配置层穿透到行为层。
