---
name: research-scenarios
description: |
  A 股研究场景 Playbook（场景 A/B/B2/C/D/E/F）：个股分析、量化回测、Shadow Account 交割单诊断、开放性问题、周期执行、选股策略、宏观/事件驱动的逐步执行工作流。
  Use when: 已识别用户请求所属场景，需要该场景的完整执行步骤；个股分析、回测、交割单、选股、宏观事件分析、周期执行。
argument-hint: "场景字母（A/B/B2/C/D/E/F）+ 标的或主题"
user-invocable: true
triggers:
  - 场景 A
  - 场景 B
  - 场景 E
  - 场景 F
  - 个股分析
  - 选股策略
  - 交割单诊断
  - 宏观事件分析
---

# 研究场景 Playbook（客户引导流程）

> 本技能承载 AGENTS.md「场景路由」表对应的完整执行步骤。识别场景后先加载本技能，再按对应场景步骤执行。所有纪律层规则（问题处理协议、防幻觉、回测方法论、风险硬约束）仍在 AGENTS.md 常驻生效，本技能不重复。

## 通用前置检查
用户提及任何股票/ETF 时，先检查：
1. `analysis/<stock_code>/` 历史分析报告；
2. `analysis/<stock_code>/backtests/` 历史回测；
3. `cron_jobs/registry.json` 中的周期任务。
若 ClickHouse 找不到标的，先用 `search_symbol` 确认是否为 ETF、港股、美股或代码格式问题；若存在历史记录，先汇报摘要，再确认继续追踪还是发起新分析。

## 场景 A：股票/ETF 分析
1. Step 0：执行通用前置检查。
2. Step 1：必须做量化回测询问：是否需要对该标的进行量化策略回测？是则进入场景 B。
3. Step 2：数据源选择：A 股优先 `ch_*` 语义层（ClickHouse T-1）→ VT `get_market_data` 补当日数据（fallback 链头部为 clickhouse）；ETF/港股/美股用 `get_market_data(source="auto"/"yfinance"/"akshare")` 或交易 quote 能力补数。
4. Step 3：分析方法选择：
   - 财务三表深读 → VT `financial-statement` Skill（七看八问框架由此与 `valuation-model`、`investor-lenses` 组合完成）；
   - 估值 → VT `valuation-model` Skill + `ch_query` 速查（stk_factor_pro 估值列）；
   - 多视角投资框架 → VT `investor-lenses` Skill；
   - 逃顶风险预警 → `escape-top-microstructure` Skill（注意：当前仅 `margin_divergence` 与 `volatility_atr_expansion` 两个信号经过验证，RED/YELLOW/GREEN 集成结论需注明验证状态）；
   - 专家团队必须主动提供 `vibe-trading_run_swarm`（如 `investment_committee` / `quant_strategy_desk`）；
   - 内置方法用 `vibe-trading_list_skills` / `vibe-trading_load_skill` 发现。
5. Step 4：交易时间内或用户问"现在能不能买/卖"时，补充 akshare/Yahoo/quote 近实时数据，给出实时信号与数据延迟说明。
6. Step 5：完成后必须用 `report-generate` 生成标准报告。
7. Step 6：**主动问询是否需要生成 HTML 交互报告**（加载 `html-report` Skill）。
8. Step 7：Skills 发现：询问是否需要了解当前可用的所有分析 Skills。

## 场景 B：量化回测
1. Step 0：执行通用前置检查，重点看历史回测。
2. Step 1：必须询问是否使用 Vibe-Trading 的全套因子回测策略；说明增量能力包括 `alpha-zoo`、`technical-basic`、`ml-strategy`、`factor-research`、`multi-factor`、`backtest-diagnose`、`pine-script`、`vnpy-export`。
3. Step 2：加载 `strategy-generate` 并使用 VT 回测引擎（`vibe-trading_backtest`）；自定义信号先经 `scripts/vibe_bridge/` 转为 VT 回测契约。
4. Step 3：用 `report-generate` 保存回测报告到 `analysis/<stock_code>/backtests/`。
5. Step 4：**主动问询是否需要生成 HTML 交互回测报告**（加载 `html-report` Skill，使用 `render_vibe_backtest_html()` 或 `render_alpha158_backtest_html()`）。
6. Step 5：询问是否需要回测诊断（`backtest-diagnose`）、实盘策略导出（`pine-script`/`vnpy-export`）或风险评估（`risk-analysis`）。
7. Step 6：若回测推导出后续买入/卖出位，询问是否让 crontab 在次日/交易时间用实时数据监控触发。
8. Step 7：若结果可跟踪，询问是否周期性自动执行并提醒。

## 场景 B2：Shadow Account（交割单诊断）
1. Step 0：用户上传交割单 CSV/Excel 文件。
2. Step 1：加载 `trade-journal` Skill，调用 `vibe-trading_analyze_trade_journal` 解析交易行为。
3. Step 2：调用 `vibe-trading_extract_shadow_strategy` 提炼盈利模式（3-5 条人话规则）。
4. Step 3：调用 `vibe-trading_run_shadow_backtest` 跨市场回测验证。
5. Step 4：调用 `vibe-trading_render_shadow_report` 生成 8-section PDF/HTML 报告。
6. Step 5：引导用户进入场景 B（对 Shadow 策略做深度回测）或场景 D（周期执行）。

## 场景 C：开放性问题（"明天买什么"/"最近有什么机会"）
1. 识别为「问题处理协议 · 开放型」，执行 **Least-to-Most 收敛漏斗**：市场 → 工具类型 → 期限 → 风险预算 → 风格 → 候选池规模。
2. **一轮、≤3 问、编号列表、选择题 + 后果描述 + 逃生门**；绝不发起第二轮。
3. 漏斗答案编译为工具参数（风险预算 → 最大回撤约束；风格 → 筛选因子；候选池规模 → Top N）。
4. 一轮后仍模糊 → 按最合理解释 + 明示假设执行，邀请纠正。
5. 收敛后路由：选股需求 → 场景 E；单一标的/配置 → 场景 A/B。
6. 可映射能力：`sector-rotation`、`multi-factor`、`asset-allocation`、`risk-analysis`、`fundamental-filter`。

## 场景 D：策略周期执行
1. 对满意的分析/回测策略，询问是否周期性自动执行并提醒。
2. 确认执行频率、监控标的、通知方式、信号阈值；若是实时信号，确认盘中频率、数据源（akshare/Yahoo/quote）和延迟容忍度。
3. 使用 `cron_jobs/manage.py` 注册、验证和管理任务（详见 AGENTS.md「周期任务触发规范」）。

## 场景 E：选股策略（多标的筛选）

适用触发词：选股、筛选股票、找股票、选美、资金流选股、叙事选股、板块轮动选股、多因子选股

**理论基础**：凯恩斯选美博弈 — 选股不是选"好公司"，而是选"多数人即将选择的公司"。
- 基本面是入场券（备选者必须足够"美"）→ 使用 VT `fundamental-filter`
- 叙事是催化剂（故事正在被更多人传播）→ 本场景独特方法论
- 资金流是验证（评委团正在用脚投票）→ VT flow 工具

**学术支撑**：Shiller (2017) 叙事经济学 / Lou (2012 RFS) 资金流动量 / AFA 2025 叙事注意力定价 / BigQuant 2023 A 股概念动量

---

### Step 0: 明确选股范围和策略
- 确认选股范围（全 A 股 / 特定板块 / 特定市值 / 特定风格）
- 确认选股策略（见下方策略模板）
- 确认输出数量（Top 5 / Top 10 / Top 20）
- 若用户意图模糊，按「问题处理协议 · 开放型」执行一轮漏斗澄清（带选项）

### Step 1: 叙事识别（并行执行）
- `get_sector_info(mode="ranking")` → 板块涨幅排名
- `get_northbound_flow()` → 北向资金方向
- `screen_market(sort_by="amount")` → 成交额 Top N
- 可选: `sector_rotation_team` Swarm → 行业轮动深度分析

### Step 2: 候选池生成
- 从叙事主线中提取候选标的（20-30 只）
- **优先用 `ch_*` 语义层查询 ClickHouse**（fin_indicator、stk_factor_pro 等表，先 `ch_describe_table` 确认口径）
- ClickHouse 不足时用 `get_financial_statements()` 补充

### Step 3: 三层筛选

**Layer 1 — 基本面（硬门槛，不可跳过）**：

| 条件 | 阈值 | 理由 |
|------|------|------|
| ROE（加权平均） | >= 8% | 盈利能力底线 |
| 营收同比增长 | > 0% | 成长性验证 |
| 净利润同比增长 | > -20% | 排除业绩恶化 |
| 经营现金流/股 | > 0 | 盈利质量验证 |
| ST 排除 | — | 规避退市风险 |
| 市值下限 | > 50 亿 | 排除微盘股 |

使用 VT `fundamental-filter` Skill + `scripts/screening/`（基于 ClickHouse 的三层筛选流水线）执行。

**Layer 2 — 叙事动量**：

| 条件 | 数据源 | 说明 |
|------|--------|------|
| 行业动量 | ClickHouse `idx_sw_classify` + `stk_factor_pro` | 申万行业涨幅排名 Top 30% |
| 成交额变化率 | ClickHouse `stk_factor_pro.amount` | 近 20 日 vs 近 60 日成交额比 |
| 换手率变化率 | ClickHouse `stk_factor_pro.turnover_rate` | 近 20 日 vs 近 60 日换手率比 |
| 研报覆盖 | `get_research_reports()` | 近 30 日新增研报数 |

**叙事阶段判断（招商证券"四季法则"）**：

| 阶段 | 特征 | 操作 |
|------|------|------|
| 乘势期 | 少数人讲，股价开始反应 | 建仓（Tier 2） |
| 造势期 | 媒体扩散，资金跟进 | 持有/加仓（Tier 1） |
| 退势期 | 散户蜂拥，研报密集 | 减仓/回避 |
| 休耕期 | 叙事耗尽，无人提起 | 回避 |

**Layer 3 — 资金流共振**：

| 条件 | 数据源 | 说明 |
|------|--------|------|
| 主力净流入 | `get_fund_flow()` | 大单+超大单净流入 > 0 |
| 融资余额增长 | `get_margin_trading()` | 近 5 日融资余额增长 > 0 |
| 北向资金 | `get_northbound_flow()` | 北向近 20 日净买入 |

### Step 4: 量化验证（可选但推荐）
- VT `factor-research`: 对选股池做 IC/IR 截面分析
- 回测验证: 自定义信号经 `scripts/vibe_bridge/` 转 VT 回测契约 → `vibe-trading_backtest`（Walk-Forward）
- 消融表：每层增量 Sharpe + Fama-MacBeth 显著性

### Step 5: 报告输出与分级

**Tier 分级**：
| 级别 | 条件 | 建议 |
|------|------|------|
| **Tier 1 强共振** | 三层全部通过 + 叙事处于造势期 | 核心配置 |
| **Tier 2 中共振** | 三层通过 + 叙事处于乘势期 | 配置 |
| **Tier 3 观察** | 基本面通过 + 叙事非主峰 | 等待叙事加速 |

**必须包含**：每层通过/淘汰名单、叙事阶段标注、资金流共振评分（⭐1-4）、仓位建议、止损/止盈规则、核心风险预警。

- `report-generate`: 保存标准报告到 `analysis/screening_<date>/`
- 更新 `analysis/_index.json`
- `html-report`: 使用 `render_screening_html()` 生成交互式 HTML

### Step 6: 后续引导
- 询问是否对 Top 标的做个股深入分析（→ 场景 A）
- 询问是否回测验证（→ 场景 B）
- 询问是否周期执行（→ 场景 D）

### 策略模板速查

| 策略 | Layer 1 侧重 | Layer 2 侧重 | Layer 3 侧重 | 适用场景 |
|------|-------------|-------------|-------------|---------|
| 选美博弈 | ROE+增长+现金流 | 叙事动量+阶段 | 资金流共振 | 趋势行情 |
| 价值选股 | PE/PB/股息率 | 行业景气度 | 北向+融资 | 价值回归 |
| 质量选股 | ROE+毛利率+现金流 | 研报覆盖 | 筹码集中 | 稳健配置 |
| 动量选股 | 营收增速+利润增速 | 概念热度 | 主力净流入 | 趋势跟踪 |

### 关键约束
- 选股结果必须标注叙事阶段，避免推荐退势期标的
- 基本面筛选是硬门槛，不可因叙事热度跳过
- 必须给出仓位建议和止损规则
- 优先使用 `ch_*` 语义层（ClickHouse），本地不足时再用 VT 网络源

## 场景 F：宏观/事件驱动问题（"俄乌战争对市场影响"/"厄尔尼诺的影响"）
1. 识别为「问题处理协议 · 宏观型」，执行 **Step-Back 协议**：
   - Step 1：抽象传导渠道（地缘 → 能源/农产品/避险/供应链/军工；气候 → 作物产量/种植结构/水利/保险）。
   - Step 2：拆分「VT 可明确解决」与「无法完美解决」，向用户展示拆分。
   - Step 3：给出代理问题菜单，确认后执行。
2. 可执行的工具映射：
   - 事件研究（历史同类冲击窗口 CAR / Patell / BMP 检验）→ `quantlib_call`（event study 模块）
   - 地缘风险量化、情景分析 → VT `geopolitical-risk` Skill
   - 宏观周期定位、利率/汇率传导 → VT `macro-analysis` Skill + `get_macro_series`（FRED）
   - 大宗商品供需与季节性 → VT `commodity-analysis` Skill
   - 行业暴露度排序 → `get_sector_info` + ClickHouse 行业表；资金流验证 → `get_northbound_flow` / `get_fund_flow`
   - 政策面 → `search mcp` 的 `list_industry_policies`；外部研究 → `search` / `fetch_page`
3. 方法论声明（强制）：短窗口事件研究可靠；长周期影响仅作情景分析（MacKinlay 1997; Kothari & Warner 2007）。文献结论标注为文献结论，不作为预测。
4. 输出遵守「防幻觉与诚实拒答纪律」：不可解部分用拒答模板；所有数字带 data-as-of。
5. 完成后用 `report-generate` 保存，并主动问询是否生成 HTML（Markdown 转 HTML）。
