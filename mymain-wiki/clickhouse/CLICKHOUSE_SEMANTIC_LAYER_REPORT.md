---
title: ClickHouse 数据访问与语义层架构决策报告（R1，中文）
description: R1 决策报告中文版——分层混合+语义下沉数据库的结论与理由。先要中文结论时读这份。触发词：ClickHouse 决策、语义层、R1、llm_role、ch_query。
type: research
status: active
created: 2026-08-12
updated: 2026-08-12
tags: [clickhouse, semantic-layer, decision]
related: [CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md, CLICKHOUSE_ITERATION_PLAN.md]
---

# ClickHouse 数据访问与语义层架构决策报告（R1）

> 作者：shadowinlife（架构决策）｜ 日期：2026-08-12 ｜ 状态：决策建议
> 对应 `../branch/MYMAIN_DIVERGENCE.md` §4.6 待办研究任务 R1（ClickHouse 语义层深度研究）
> 证据基础：本地 F5 代码取证 + 2026-08-11 会话调研（ses_01133972）+ 2026-08-12 四路并行外部调研（官方仓库源码、ClickHouse 官方博客、学术文献、开源项目、行业标准）

---

## 0. 决策摘要（TL;DR）

**问题**：如何让 OpenCode + Vibe-Trading MCP 从 ClickHouse 取数，同时保有一个**稳定的语义层**（单位、口径、列含义不随访问路径丢失）。

**决策**：**不二选一，采用「分层混合 + 语义下沉数据库」架构**——

1. **不引入官方 mcp-clickhouse 作为主数据接口**（三个量化场景硬伤：UInt64 JSON 精度损坏 #111 仍 open、无结果集上限、readonly 可被用户 profile 击穿 #131 曾删生产表）。
2. **保留并强化 F5 领域工具层作为主接口**（这是我们自己的 ClickStack 式语义工具，hdx-evals 官方基准证明语义工具比裸 SQL 准确率高 7–20pp）。
3. **语义下沉数据库作为地基**：DDL 入仓库 + `COMMENT COLUMN` 结构化注释约定 + 专用只读 DB 用户 + 资源限额——让任何直连路径（含未来任何 CH MCP）不再丢失全部语义。
4. **新增分层探索工具作为灵活性逃生舱**：`ch_list_tables → ch_describe_table → ch_query`（受约束 SQL），自建而非引入官方 server。
5. **指标字典（AGENTS.md/skill 形态）作为 L4 上下文**：pe_ttm 口径、volume=手/amount=元 约定、close vs close_hfq 选择规则、gold queries。
6. **暂不引入 dbt SL / Cube 等独立语义层服务**（当前只有一个消费者，不满足语义层的价值判据；dbt SL 消费端 API 为付费 Cloud）。

**一句话**：语义必须从「代码携带的隐性知识」变成「数据库承载 + 代码消费 + 文档注入」的三层显性资产；领域工具是主通道，受约束 SQL 是逃生舱，裸 SQL MCP 永远不是主接口。

---

## 1. 背景与问题定义

### 1.1 现状（本地取证，2026-08-12 核实）

mymain 分支 F5 的 ClickHouse 访问栈：

| 层 | 文件 | 语义承载方式 |
|---|---|---|
| 连接器 | `agent/src/clickhouse_connector.py` | 裸 `query(sql)` + 8 个领域方法；`get_daily_bars` 精选 11 列，其余方法多为 `SELECT *` |
| Loader | `agent/backtest/loaders/clickhouse.py` | `SELECT * FROM stk_factor_pro`（**199 列全取**）+ 当日网络源联邦；仅做 `vol→volume` 改名，无单位元数据 |
| 资金流工具 | `agent/src/tools/clickhouse_fallbacks.py` | **单位换算硬编码在 Python**：万元→元 `×10⁴`（fund_flow）、北向 `×100`（northbound）；列名映射硬编码（`rzye→financing_balance` 等） |
| Envelope | `agent/src/market_data.py` | `_provenance` 存在但**当前无单位字段**（上游 PR #1065 待合入） |

**关键事实**：仓库中**无任何 CH DDL**（无 `CREATE TABLE`/`COMMENT COLUMN`），数据库层零语义；CH 实例列注释情况未直查（实例暂不可达）。

### 1.2 两个候选方案的已知缺陷（前期调研，2026-08-11）

| 方案 | 缺陷 |
|---|---|
| **A. 部署 ClickHouse MCP**（官方 mcp-clickhouse 直连） | 语义与数据物理分离——绕过工具链即丢失全部语义（单位/口径/列含义）；`SELECT *` 泄漏 ~199 列无标注；LLM 写 `SELECT close` 跨除权日算收益即错（上游 v0.1.13 曾修同类 47pp 偏差问题） |
| **B. 本地分支改造**（F5 领域工具链） | 语义是「隐性的、代码携带的」——只有穿过工具链的请求才受保护；个人部署独有不回流；`get_market_data` 的 `SELECT *` 直通路径仍是语义盲区；单位换算散落硬编码、与文档层语义（工具描述/skill）存在漂移风险 |

**核心矛盾**：A 有灵活性无正确性保证，B 有正确性保证无灵活性且语义不随数据走。

---

## 2. 方案 A 评估：ClickHouse MCP（裸 SQL 直连）

### 2.1 官方 mcp-clickhouse 现状（源码取证：HEAD `423ca2e`，v0.4.1，2026-07-17）

- 工具集：`run_query`（原 `run_select_query`）/ `list_databases` / `list_tables`（分页 + `include_detailed_columns`）/ 可选 chDB。
- 定位：官方明确是「LLM↔ClickHouse 的最小执行层」——**不做 text-to-SQL 优化、不做语义层**。
- 安全模型：默认 `readonly=1`（**query-level setting**）+ DROP/TRUNCATE 正则检测 + HTTP 传输强制认证。

### 2.2 量化场景的三个硬伤（决策依据）

| # | 硬伤 | 证据 |
|---|---|---|
| 1 | **UInt64 值在 JSON 响应中损坏**——`total_mv`/`amount`/成交量等大数值正是 UInt64，精度损坏直接污染量化数据 | [Issue #111](https://github.com/ClickHouse/mcp-clickhouse/issues/111)，**仍 open** |
| 2 | **只读承诺可被击穿**：query-level `readonly=1` 在用户 profile 有全权限时不生效；真实事故中 Claude 子 agent **删除了生产表** | [Issue #131](https://github.com/ClickHouse/mcp-clickhouse/issues/131)（2026-02，closed）；官方修复方向=文档化「必须建专用只读用户」 |
| 3 | **无结果集大小上限**——199 列宽表 + 无 LIMIT 自觉时 context 爆炸；官方 changelog 自己承认此问题（#55 token 编码优化、#75 chDB prompt 重构避免 context-too-large、#92 list_tables 分页） | CHANGELOG 0.1.8 / 0.1.12 / 0.1.13 |

其他已知问题：长查询阻塞事件循环（#128，0.4.0 修复）、fastmcp 依赖链 CVE（#188）、启动慢（#160）。

### 2.3 官方自己的基准：裸 SQL 全面劣于语义工具

**hdx-evals**（ClickHouse 官方 evals 框架，位于 `hyperdxio/hyperdx/packages/hdx-eval`；[基准博客](https://clickhouse.com/blog/benchmarking-the-clickstack-mcp-server-with-hdx-evals) 2026-07-28）：同一 Claude Opus 4.6 agent、确定性合成数据、盲评——

| 场景 | ClickStack MCP（领域工具） | mcp-clickhouse（裸 SQL） | Δ |
|---|---|---|---|
| error-root-cause | 93% | 73% | **+20pp** |
| noisy-signals | 64% | 45% | +19pp |
| latency-spike | 60% | 43% | +17pp |
| segmented-regression | 75% | 60% | +15pp |
| service-health-check | 61% | 54% | +7pp |

配套效率指标（[ClickStack MCP 公告](https://clickhouse.com/blog/announcing-managed-clickstack-mcp-server)，2026-06-26）：工具调用 **−25%**、运行间一致性 **2.5×**、评估分数 **+20%**。细分数据显示裸 SQL 的劣势主要来自**试错成本**（工具错误惩罚 7pp、多 26% 调用），不是推理能力本身。
> ⚠️ 适用性告诫：这是可观测性域基准，具体数字不能外推到 A 股因子查询；但它证明的机制（语义工具 > 裸 SQL）是通用的，且裸 SQL 基线正是我们在评估的 mcp-clickhouse。

### 2.4 方案 A 的适用场景（不是完全否定）

- **人工数据调试 / ad-hoc 探索**：开发者已知语义、一次性查询、结果由人审核——这是裸 SQL MCP 的合理定位（DWAINE 模式：ClickHouse 内部 agent 解决 70% 数仓问题，但背后是 curated marts + llm_role 全套生产配置）。
- **前提条件**（官方[生产指南](https://clickhouse.com/blog/how-to-set-up-clickhouse-for-agentic-analytics)，2026-02-23）：只暴露 curated 层、专用只读用户（`GRANT SELECT`）、role 级资源限额（`max_execution_time=30` / `max_memory_usage=2GB` / `max_rows_to_read` / `max_threads=4`）、独立只读 service。
- **结论**：方案 A 不删除，降级为「人工探索通道」，且必须先完成 §5 的 L0 地基。

---

## 3. 方案 B 评估：当前分支 F5 领域工具链

### 3.1 优势（为什么它是对的方向）

1. **单位/口径正确性有代码保证**：`clickhouse_fallbacks.py` 的换算逻辑虽硬编码，但**确定性地**把 CH 原生单位（万元、北向原始口径）转成工具契约单位；LLM 永远看不到原始列。
2. **与上游 #1062 修复方向一致**：CH `stk_factor_pro.vol` 为 tushare 口径（手），与归一化方向（手）一致；CH 可达时 volume 单位行为被锁定为确定性。
3. **符合业界收敛模式**：ClickStack 领域工具、Databricks Genie 的 Trusted Assets / SQL Functions（LLM 不可见/不可改内部 SQL 的确定性封装）、market-terminal（31 个只读研究型工具，明确文档化与 Vibe-Trading 集成）——**严肃的生产用法都在 MCP 之上自建语义层**（前期调研中 equity-data-agent 案例甚至直接规定「LLM 禁止编造数字」）。
4. **失败模式安全**：dbt 官方基准（[Semantic Layer vs Text-to-SQL](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)，2026-04-07）的核心发现——**语义层失败时报错，text-to-SQL 失败时返回看似合理的错误数字**。金融场景静默错误是最昂贵的错误。

### 3.2 劣势（必须修复的缺口）

| # | 缺口 | 后果 |
|---|---|---|
| 1 | **语义与数据物理分离**：语义固化在 Python 调用链，数据库层零语义 | 任何绕过工具链的直连（人工 SQL、未来任何 MCP、数据调试）立即丢失全部语义；语义无法被第二消费者复用 |
| 2 | **`SELECT *` 泄漏路径**：`get_market_data` 直通 199 列（pe_ttm/pb/total_mv…）无标注；loader 本身也是 `SELECT *` | 契约漂移——CH 加列即静默改变工具输出；LLM 拿到未标注列自行猜测口径 |
| 3 | **单位换算散落硬编码**：`×10⁴`/`×100` 在 fallbacks 代码里，工具描述/skill 文档另说一套 | 两处漂移风险；新增表/列必须改代码；无法被测试门禁锚定 |
| 4 | **灵活性受限**：固定意图之外的 ad-hoc 查询（如「查某股某日 turnover_rate 分位数」）无通道 | 用户被迫绕开工具链直连 → 回到方案 A 的无保护状态 |
| 5 | **不回流社区**：个人部署独有 | 语义层投入无法上游化，长期维护成本自担 |

### 3.3 方案 B 的适用场景

- **高频固定意图查询**（OHLCV、资金流、融资融券、龙虎榜、北向）——主通道，继续强化。
- **回测数据管线**——loader 层 CH 优先 + 网络联邦的架构保持不变。
- **不适用**：ad-hoc 探索、跨表自由分析、人工数据审计——这些需求真实存在，是方案 A 诱惑的根源，必须给出受保护的替代通道（见 §5 L2/L3）。

---

## 4. 业界的答案：同类问题如何解决

### 4.1 共识：分层混合（Layered Hybrid）

业界对「LLM 访问分析型数据库」的答案已高度收敛，**不是 text-to-SQL vs 领域工具的二选一**：

> **确定性语义层打底 + 分层探索工具做渐进发现 + 受约束只读 SQL 做逃生舱 + 业务词汇表/gold queries 注入上下文。**

三条关键证据链：

1. **裸 text-to-SQL 在企业级宽表上准确率崩塌**：Spider 1.0 上 86–91% 的模型，在真实企业 schema（平均 812 列）上只剩 10–21%（[Spider 2.0，ICLR 2025 Oral](https://arxiv.org/abs/2411.07763)）；私有企业数仓基准 BEAVER 上 SOTA agentic 框架仅 10.8%（[arXiv:2409.02038](https://arxiv.org/html/2409.02038v3)）。错误分布：数据分析错误 35.5%、**schema linking 错误 27.6%（其中列链接 16.6%）**——199 列宽表正处在这个问题的核心。
2. **语义层内准确率接近 100% 且失败安全**：dbt 基准中 SL 覆盖范围内问题 98–100%，text-to-SQL 64.5%。
3. **一份 4KB 语义文档的代价收益**：Cube 配对基准（**数据就在 ClickHouse 上**，[arXiv:2604.25149](https://arxiv.org/abs/2604.25149)）——仅给 LLM 增加一份含 measures 定义 + 约定 + **消歧规则**的语义 markdown，三个前沿模型准确率 **+17~23pp**，且模型间差异消失。

### 4.2 开源项目与厂商实践的收敛形态

| 项目/实践 | 做法 | 对我们的启示 |
|---|---|---|
| **[Altinity MCP](https://github.com/Altinity/altinity-mcp)**（ClickHouse 头部服务商，Go，Apache-2.0） | ① 真·SELECT-only 解析守卫；② 服务端结果上限（默认 500 行/50KB，双层强制 + 截断提示）；③ **参数化视图自动生成类型化 MCP 工具，视图 COMMENT=工具描述，列 COMMENT=参数描述**（经 `system.columns`）；④ memory 系统存 recipes/坑点 | **「语义下沉数据库」路线最完整的工程实证**——COMMENT 是 LLM 可消费的一等语义；我们的 L2/L3 护栏可直接照抄 |
| **[mcp-clickhouse 官方](https://github.com/ClickHouse/mcp-clickhouse)** | 3 工具 + 只读 + 分页 schema 发现；[PR #146](https://github.com/ClickHouse/mcp-clickhouse/pull/146) 新增参数化查询（理由：字符串插值易错且绕过类型校验） | 官方设计本身即「分层探索 + 受约束 SQL」混合；但执行层缺陷（§2.2）要求我们自建而非引入 |
| **[OpenBB MCP](https://github.com/OpenBB-finance/OpenBB/tree/develop/openbb_platform/extensions/mcp_server)**（40k★ 金融数据平台官方） | **动态工具激活**：初始只暴露 discovery 工具，agent 按需激活类别，防 token 膨胀 | 我们 70+ MCP 工具规模问题的直接参考实现 |
| **[WrenAI](https://github.com/Canner/WrenAI)**（20k★，Apache-2.0，**有 ClickHouse connector**） | MDL 语义层（Git 可版本化 YAML）+ 已验证 query 对记忆 + dry-plan 预校验 + MCP | L4 元数据的形态参考：YAML in Git 而非 GUI |
| **[Vanna](https://github.com/vanna-ai/vanna)**（23.8k★，MIT，支持 ClickHouse） | RAG 训练 DDL + 文档 + question-SQL 对 | 反向参照：社区讨论显示 60 表/500 列规模下纯 RAG 仍然吃力 |
| **[market-terminal](https://github.com/jalilsedna/market-terminal)** | 31 个只读研究工具 + `decision_brief` 一揽子简报；「Research flows out; orders never flow back」 | 量化领域工具封装的同类实例，文档化了与 Vibe-Trading 的集成 |
| **Databricks [Genie](https://docs.databricks.com/aws/en/genie/best-practices)** | 上下文优先级：SQL expressions > example SQL > 纯文本；30 表硬上限；benchmark 驱动（黄金问题集 54%→100%）；Trusted Assets/SQL Functions 确定性封装 | L4 注入优先级与「benchmark-driven」方法论来源 |
| **Snowflake Cortex Analyst** | verified ("gold") queries 库 → 反推优化语义模型 | gold queries 是公认的高杠杆手段 |
| **Uber QueryGPT**（[官方博客](https://www.uber.com/us/en/blog/query-gpt/)，2024-09） | Workspaces（策划的表+SQL 样本）→ Intent → Table（人工确认）→ Column Prune → 生成；即便如此仍有表/列幻觉 | 生产级 text-to-SQL 必须多阶段裁剪 + 人工兜底；进一步支持「不让 LLM 自由写 SQL」 |
| **Anthropic 工具设计原则**（[Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) 2025-09、[MCP 生产化](https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp) 2026-04） | 按意图分组工具而非 1:1 包装 API；大能力面=薄工具面+代码编排（Cloudflare 2 工具≈2500 端点）；Claude Code 仅 ~20 工具 | 工具数量治理原则；MCP 官方 [Client Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices.md) 定义三层渐进发现：Catalog→Inspect→Execute |

### 4.3 语义层的两条路线与融合趋势

| | 路线 A：语义下沉数据库 | 路线 B：语义独立成层 |
|---|---|---|
| 载体 | 列 COMMENT、视图、DDL 入仓库 | YAML/代码模型 + 独立运行时（dbt SL / Cube / LookML） |
| 优势 | 零额外组件、语义与数据同地、SQL 原生可达、**任何客户端（含 LLM）可消费** | 跨源、动态指标编译、统一治理与缓存 |
| 风险 | 表达力弱（无 join 图/指标组合逻辑） | YAML 腐化（[a16z 2026](https://a16z.com/your-data-agents-need-context/)：「离职员工去年更新的 YAML」）、多一跳运行时、**API 消费排斥 SQL 原生用户**（[Benn Stancil](https://benn.substack.com/p/metrics-layer) 对 Minerva 的批评：分析师最终会在仓库里用 SQL 重写指标，口径再次分裂） |

**2026 年的融合趋势**：独立语义层把自己包装成 LLM 的 context layer（MCP 成标准接口）；数据库侧语义（COMMENT）被证明可直接作为 MCP 工具元数据（Altinity MCP）；ClickHouse 官方 text-to-SQL（client 25.7+）与 ClickHouse Assistant 都把 COMMENT/AGENTS.md 当一等语义输入。

**语义层价值判据**（Stancil + a16z 共同指向）：**价值在于消费者的数量与多样性，而非架构时髦程度**。当前我们只有一个消费者（Vibe-Trading agent 自身）→ 路线 A（下沉数据库）+ 代码内语义（现有工具链）是性价比最优解；路线 B 留作「出现第二个非 SQL 消费方」时的升级项。

---

## 5. 推荐方案：分层混合 + 语义下沉（L0–L4 架构）

```
L0  语义地基（数据库侧，新增，先决条件）
    ① DDL 入仓库：ashare 库全部表的 CREATE TABLE（git 版本化，单一事实来源）
    ② COMMENT COLUMN 结构化注释约定（199 列全覆盖）：
       "成交量，单位=手；口径=tushare daily.vol；adjust=raw"
       建议机器可解析前缀：unit= / adjust= / caliber= / source= / ambiguous_with=
    ③ 专用只读用户 llm_role（GRANT SELECT ON ashare.* ONLY —— #131 教训：
       只读必须在 DB 用户层强制，不能靠 query-level setting）
       + 资源限额：max_execution_time=30 / max_memory_usage=2G / max_rows_to_read / max_threads=4
    证据：ClickHouse 官方生产指南；Altinity MCP 证明 COMMENT 可被 LLM 消费；
          官方幻觉对策原文「给 LLM 最大且最准确的 context——用 COMMENT 语法」

L1  领域指标工具（现有 F5，主接口，强化）
    get_market_data / get_fund_flow / get_margin_trading / ...
    修复三件事：
    ① SELECT * 泄漏路径显式工具化：新增 get_valuation（pe_ttm/pb/total_mv 固定模板
       + tushare daily_basic 兜底），或至少在 envelope 附列语义标注
    ② _provenance 附单位元数据（对齐上游 PR #1065/#1067 的 volume_unit 方向，
       扩展为 unit/adjust/caliber 三元组）
    ③ 单位换算从硬编码改为元数据驱动：换算规则（×10⁴/×100）声明在 L0 的 COMMENT
       或随 DDL 入仓库的 YAML 中，fallbacks 代码读取元数据执行——消除双处漂移
    证据：hdx-evals +7~20pp；dbt 基准 SL 覆盖内 ~100% 且失败显式

L2  分层探索工具（新增，3 个工具，灵活性通道）
    ch_list_tables（名称+一行描述）→ ch_describe_table（单表列描述/单位/样例值/
    分区键，数据来自 system.columns.comment）→ ch_query
    证据：MCP 官方三层渐进发现模式；AutoLink（AAAI 2026）证明迭代探索在 3000+ 列
          仍保持 ~90% recall 且 token 消耗最低；mcp-clickhouse 官方即此设计

L3  受约束 SQL 逃生舱（L2 的 query 层，自建而非引入官方 server）
    护栏（照 Altinity + 官方生产指南收敛实践）：
    ① 用 L0 的 llm_role 连接（DB 层只读，不依赖应用层）
    ② SELECT-only 解析守卫（sqlglot AST 校验，白名单表引用）
    ③ 参数化查询（{name:Type} 占位，不字符串插值 —— mcp-clickhouse PR #146 同款理由）
    ④ 结果上限（默认 500 行/50KB，截断时显式告知 + 收窄建议）
    ⑤ 强制 LIMIT 注入 + 30s 超时
    证据：Spider 2.0/BEAVER 证明无护栏裸 SQL 在企业宽表上 10-20%；
          #111 UInt64 损坏问题要求自建序列化层（用我们自己的 connector 而非官方 server）

L4  语义元数据注入（指标字典，AGENTS.md/skill 形态）
    ① 指标字典：pe_ttm=TTM 口径、volume 规范单位=手（#1062 决策）、amount 单位、
       close vs close_hfq 选择规则（回测用 hfq/展示用 qfq，见 §6.2）、
       北向/融资融券列含义（rzye=融资余额 等）
    ② gold queries：10-20 个已验证的问题-SQL 对（含单位陷阱、复权陷阱）
    ③ 注入方式：skill 文件（现有 data-routing/tushare skill 模式）+ 工具 description
    证据：Snowflake verified queries / Genie example SQL / Vanna question-SQL 对；
          Spider 2.0 涉及业务文档的题目正确率仅 11.5%（反面教训）；
          ClickHouse Assistant AGENTS.md 模式（官方）
```

### 5.1 为什么不是其他选项

| 否决项 | 理由 |
|---|---|
| 官方 mcp-clickhouse 作为主接口 | §2.2 三硬伤；且 hdx-evals 证明裸 SQL 劣于语义工具 |
| 纯 F5 不加灵活性通道 | ad-hoc 需求真实存在，无保护通道时用户会绕开工具链直连（回到无保护状态）；这也是方案 A 诱惑的根源 |
| dbt Semantic Layer | 消费端 API（GraphQL/JDBC）为付费 dbt Cloud 专有；MetricFlow 引擎虽开源但需自建服务层；单消费者场景收益不抵运维成本；API 消费排斥 SQL 原生用户 |
| Cube.dev | 模式优秀（MCP + certified queries + Semantic SQL）但多一个常驻运行时；其 AI 叙事文章自述立场偏向；作为「第二消费者出现后」的升级参考保留 |
| 参数化视图→工具（Altinity 模式）全自动 | 好方向但非第一步——我们当前表结构无视图层，先做 L0-L4，视图自动化留作 Phase 3 |

---

## 6. 四个专项问题

### 6.1 扩展性

| 扩展维度 | 本方案的行为 |
|---|---|
| **新增表**（如分钟表、行业表） | DDL + COMMENT 入仓库（L0 约定自动覆盖）→ 按需加 L1 领域工具或仅靠 L2/L3 探索；无需改架构 |
| **新增指标** | 指标字典 + gold query 追加（L4）；高频后升级为 L1 工具 |
| **新增消费者**（Web UI 直查、第二个 agent、人工 BI） | 走 L2/L3 只读网关，语义随 COMMENT 走——**这正是语义下沉相对代码携带的核心红利** |
| **新市场入 CH**（HK/US） | 同一套 L0 约定复用；单位约定按市场声明（对齐 #1065 的 per-market volume_units 模式） |
| **社区回流** | L0（DDL+COMMENT 约定）+ L2/L3（通用 CH 探索工具）是**纯增量、与个人 CH 实例解耦**的 patch，比 F5 整体更可上游化（可配置开关 + 无实例依赖的测试）；F5 数据源本身维持「个人部署独有不回流」 |
| **向独立语义层演进** | L4 的 YAML 化（WrenAI MDL 模式）是通往 Cube/dbt SL 的平滑台阶，不锁死 |

### 6.2 金融「单位」处理

**业界现状**（调研确认）：不存在规范行情成交量单位的 ISO 标准；标准范式是 **ISO 20022 的「值 + 显式单位属性」**（Amount 必带 Currency、Quantity 带 Unit）与 **FIBO 语义本体**。各单位标注实践：

| 数据源 | 做法 | 证据 |
|---|---|---|
| CRSP/WRDS | 文档显式 Units 小节：月表 VOL "**reported in units of 100**"（百股！）、SHROUT=千股 | [WRDS 变量表](https://wrds-www.wharton.upenn.edu/demo/crsp/form/) |
| Tushare | API 文档内联："vol：成交量（手）、amount：成交额（千元）" | [tushare daily 文档](https://tushare.pro/document/2?doc_id=27) |
| AkShare | 输出参数描述列系统性标注 "注意单位: 手/元"——**「单位入字段描述」的最直接开源范例** | [akshare 文档](https://akshare.akfamily.xyz/data/stock/stock.html) |
| yfinance | 无标注，但内置 `_fix_unit_mixups()`（修复 100× 错误）与 `_standardise_currency()`（GBp→GBP ×0.01）——**单位混乱普遍性的工程铁证** | [history.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py) |

**本方案的单位处理**：
1. **列级单位元数据结构**（ISO 20022 范式）：`unit`（shares/lots/CNY/thousand_CNY/ratio）、`scale_factor`（到规范单位的乘数，lot→share ×100）、`adjust`（raw/qfq/hfq）、`adjust_base`（基准日）、`caliber`（口径）、`source`。
2. **三个挂载点叠加**：COMMENT COLUMN 结构化字符串（DB 原生、LLM 可见）+ 随 DDL 入仓库的 YAML 契约（可测试、可版本化，参照 [ODCS v3.1.0](https://github.com/bitol-io/open-data-contract-standard)——其 `customProperties` 官方示例即含 `clickhouseType`，Data Contract CLI 原生支持 ClickHouse 做可执行校验）+ `_provenance` envelope（运行时随数据返回，对齐 #1065）。
3. **规范单位延续 #1062 决策**（手）；CH `stk_factor_pro.vol` 为 tushare 口径（手），与归一化方向一致，无需改动。
4. **复权价规则**（综合 CRSP/qlib/Tushare 权威来源）：
   - 回测/收益率 → **后复权或因子法**（历史值冻结，无前视偏差；qlib issue #410 明确以此为由）；
   - 展示/与当前价对齐 → 前复权；
   - 存储层 → 存原始价 + 因子（CRSP/qlib 模式）最优；**现存的 close/close_hfq 并存可接受，但每列 COMMENT 必须注明 adjust 类型 + 因子来源**（Tushare 官方明示各源复权因子算法不同）；
   - 成交量复权方向与价格相反（价除、量乘：CRSP 公式 `A(t)=P(t)*C(t)`、qlib `volume/factor`）。

### 6.3 宽表性能保证

分两个层面：

**数据库侧（ClickHouse 本身）**：宽表是列存引擎的原生优势场景，199 列不构成查询性能问题；真正风险是**全列 SELECT 的 I/O 与结果传输**——L3 护栏（强制 LIMIT + 结果上限）+ L1 工具按需选列即可控制。loader 层当前的 `SELECT *` 在纯历史查询下可保留（缓存收益），但 L1 修复时应评估改为按需选列。

**上下文侧（LLM context，真正的瓶颈）**：
- 199 列的 `create_table_query` 约 8–15K tokens——官方 mcp-clickhouse 靠分页 + `include_detailed_columns=false` 缓解（#55/#75/#92 的演进史就是 context 爆炸的对抗史）；
- 本方案用 **L2 分层探索**解决：`ch_list_tables` 只给名称+一行描述，`ch_describe_table` 按需展开单表——AutoLink（AAAI 2026）实证：一次性注入全 schema 的方法在 3000+ 列 recall 跌破 40%，迭代探索保持 ~90% 且 token 消耗最低；
- 反方证据需平衡呈现：《The Death of Schema Linking?》（[arXiv:2408.07702](https://arxiv.org/html/2408.07702v2)）发现前沿模型在 schema 放得进 context 时给全量优于裁剪——199 列放得进，但**每次调用重复消耗 + 列越多注意力越稀释**（TriSQL，Nature Sci Rep 2026：移除 schema 相关性排序后 EM 从 76.4%→50.3%）。结论：全量 schema 作为**可选**模式保留，默认走分层探索。

### 6.4 宽表相似列的正确性保证

**问题确认**（学术证据）：
- EACL 2026 Findings 明确承认："schemas with **highly similar column names** may still lead to selection errors"；
- KaSLA（2025）：漏掉一个必要列 → SQL 必然错误；
- 真实事故案例（Datus）：`gl_amount` vs `gl_operating_amount`——列名几乎相同，只有业务规则能区分；
- 我们的实例：`close` vs `close_hfq`（跨除权日选错即错，上游 v0.1.13 修过 47pp 偏差的同类问题）、`pe` vs `pe_ttm`、`vol` vs `amount`。

**本方案的四重保证**：
1. **COMMENT 消歧标注**：每列注释含口径 + 显式 `ambiguous_with=` 声明（CData 三层模型：`synonyms` / `ambiguous_with` / `grain` / `excludes`——直接可抄的元数据结构）；BIRD 基准证明列描述等外部知识是弥合 LLM 与真实库差距的关键（无外部知识 ChatGPT 40% vs 人类 93%）。
2. **L1 领域工具直接消除选列机会**：高频意图（取复权价、取估值）由工具固化列选择——LLM 不选列就不会选错列（Genie Trusted Assets 同款逻辑）。
3. **指标字典写明选择规则**：如「计算收益率必须用 close_hfq（后复权）；展示当前价格用 close；pe_ttm 是滚动市盈率，pe 字段不存在于本表」。
4. **gold queries 回归**：相似列陷阱纳入黄金问题集，任何 L2/L3 改动必须过回归。

**量化证据**：Cube 配对基准（ClickHouse 上）仅加 4KB 含消歧规则的语义文档 → **+17~23pp**，模型间差异消失。

---

## 7. 其它架构关注点

1. **语义漂移治理（最高优先级债务）**：a16z 2026 描述的「离职员工的 YAML」失败模式同样适用于 COMMENT——必须 DDL/COMMENT 入 git + **CI 门禁**（仿 env-var AST gate：校验 `stk_factor_pro` 每列在仓库 DDL 中都有非空 COMMENT，可经 `system.columns` 对账）。没有门禁的语义层会在 6 个月内腐化。
2. **单一事实来源**：当前单位换算存在双处（fallbacks 代码 ×10⁴/×100 + 工具描述/skill 文档）——L1 修复③（元数据驱动换算是消除漂移的关键），否则 L0 建成后反而出现第三处。
3. **安全边界**：CH 实例当前 VPC 内网 + 无 TLS + 密码在 `.env`——L2/L3 网关若仅 agent 自用可接受；一旦暴露给更广表面（远端 MCP、人工客户端），必须加认证（参照官方 Remote MCP 的 OAuth 模式与 `VIBE_TRADING_MCP_ALLOWED_HOSTS` 的 DNS-rebinding 防护经验）。
4. **可观测性**：L2/L3 的每条 agent 生成查询应落审计日志（Altinity audit log 模式）——既是调试素材，也是 gold queries 的挖掘来源（Genie 的 benchmark-driven 循环依赖查询日志）。
5. **测试基线**：golden question set（10–20 题，含单位陷阱/复权陷阱/相似列陷阱）作为回归基线；与 #1067 的跨源一致性测试互补——前者测语义传达，后者测数值一致。
6. **MCP 工具数量治理**：现有 70+ 工具 + 新增 ch_* 工具，应关注 Anthropic 的警告（工具定义占上下文 >1–5% 即需渐进发现）；OpenBB 式类别激活或命名空间化是预案，非本期必做。
7. **上游 #1062 依赖**：L1 修复②（_provenance 单位元数据）与 PR #1065/#1067 强相关——若上游合入，直接在其上扩展；若长期未合入，mymain 先行实现并保持可 rebase 形态。
8. **不过度设计**：当前单消费者，抵制引入 dbt SL/Cube/独立元数据服务（OpenMetadata/DataHub）的冲动——它们的价值判据是消费者多样性（Stancil），我们尚未到达。架构预留接口（YAML 契约即台阶），不提前付费。

---

## 8. 落地路线图

| 阶段 | 内容 | 工作量 | 风险 |
|---|---|---|---|
| **Phase 0**（地基，先行） | ① 从 CH 实例导出全库 DDL 入仓库（`SHOW CREATE TABLE` + 补 COMMENT）；② 199 列 COMMENT 结构化注释（单位/口径/消歧）；③ 建 llm_role 只读用户 + 资源限额 | 1–2 天 | 零（纯增量，不改运行路径） |
| **Phase 1**（主通道强化，与 #1065/#1067 协同） | ① `_provenance` 单位元数据；② `SELECT *` 泄漏路径显式工具化（`get_valuation`）；③ 单位换算元数据驱动 | 3–5 天 | 低（有现成测试框架：CH 套件 13/8 基线） |
| **Phase 2**（灵活性通道，按需） | `ch_list_tables` / `ch_describe_table` / `ch_query` 三工具 + L3 全套护栏 + 审计日志 | 3–5 天 | 中（新 MCP 工具需过计数门禁：五份 README + SKILL.md 同步） |
| **Phase 3**（可选演进） | ① golden question 回归基线；② 参数化视图→工具自动化（Altinity 模式）；③ L4 YAML 契约化（ODCS）；④ 语义层独立 PR 回流社区 | 按需 | 低 |

**验收标准**：
- Phase 0 后：`SELECT comment FROM system.columns WHERE table='stk_factor_pro' AND comment=''` 为空；
- Phase 1 后：`get_market_data` 的 envelope 携带 `volume_unit`/单位元数据，`get_valuation` 覆盖 pe_ttm/pb/total_mv 意图；
- Phase 2 后：golden set 通过率 ≥90%，L3 无法执行任何非 SELECT、超 500 行结果被截断并显式告知。

---

## 9. 主要证据索引

**ClickHouse MCP 生态**：[mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse)（v0.4.1，#111/#128/#131/#141/#188）· [hdx-evals](https://github.com/hyperdxio/hyperdx/tree/main/packages/hdx-eval) + [基准博客](https://clickhouse.com/blog/benchmarking-the-clickstack-mcp-server-with-hdx-evals) · [agentic analytics 生产指南](https://clickhouse.com/blog/how-to-set-up-clickhouse-for-agentic-analytics) · [Altinity MCP](https://github.com/Altinity/altinity-mcp) · [Agent Skills](https://github.com/ClickHouse/agent-skills)

**语义层**：[dbt SL vs Text-to-SQL 基准](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026) · [Cube semantic layer for AI agents](https://cube.dev/articles/semantic-layer-for-ai-agents-2026) + [配对基准](https://arxiv.org/abs/2604.25149) · [a16z context layer](https://a16z.com/your-data-agents-need-context/) · [Stancil metrics layer](https://benn.substack.com/p/metrics-layer) · [ClickHouse Assistant 语义层](https://clickhouse.com/docs/use-cases/AI_ML/AIChat/semantic-layer)

**text-to-SQL 证据**：[Spider 2.0](https://arxiv.org/abs/2411.07763)（ICLR 2025 Oral）· [BEAVER](https://arxiv.org/html/2409.02038v3) · [Uber QueryGPT](https://www.uber.com/us/en/blog/query-gpt/) · [AutoLink](https://arxiv.org/html/2511.17190v1)（AAAI 2026）· [Death of Schema Linking](https://arxiv.org/html/2408.07702v2) · [TriSQL](https://www.nature.com/articles/s41598-026-39128-9)

**工具设计**：[Anthropic writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) · [MCP 生产化](https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp) · [MCP Client Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices.md) · [OpenBB MCP](https://github.com/OpenBB-finance/OpenBB/tree/develop/openbb_platform/extensions/mcp_server) · [Genie 最佳实践](https://docs.databricks.com/aws/en/genie/best-practices)

**单位与复权**：[WRDS/CRSP 变量表](https://wrds-www.wharton.upenn.edu/demo/crsp/form/) · [CRSP 计算方法](https://www.crsp.org/wp-content/uploads/guides/CRSP_Calculations_and_Index_Methodologies.pdf) · [Tushare daily](https://tushare.pro/document/2?doc_id=27) / [复权因子](https://tushare.pro/document/2?doc_id=146) · [AkShare](https://akshare.akfamily.xyz/data/stock/stock.html) · [yfinance history.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py) · [qlib issue #410](https://github.com/microsoft/qlib/issues/410) · [ODCS v3.1.0](https://github.com/bitol-io/open-data-contract-standard) · [ISO 20022 数据字典](https://www.iso20022.org/understanding-data-dictionary)

**本地证据**：`../branch/MYMAIN_DIVERGENCE.md` §2.4（#1062）/§4.6（R1）· 会话 ses_01133972（2026-08-11 调研）· F5 代码（`clickhouse_connector.py` / `loaders/clickhouse.py` / `tools/clickhouse_fallbacks.py`）

> **证据强度声明**：Spider 2.0/BEAVER/AutoLink/TriSQL 为同行评审论文；hdx-evals 为官方基准但属可观测性域（机制可外推、数字不可）；dbt/Cube 基准为厂商自测但方法开源可复现；Bloomberg 字段单位无公开规范（置信度中）；本方案所有量化收益数字均标注原始来源域，外推到 A 股场景前应以 Phase 3 的 golden set 实测校准。
