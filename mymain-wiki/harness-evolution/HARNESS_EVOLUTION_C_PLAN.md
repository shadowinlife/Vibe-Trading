# HARNESS_EVOLUTION · C 批路由层 — 工作计划 + 测试计划（预注册）

> ⛔ **终局状态（2026-08-28）：已试·失败·已回滚。** 确定性检索达标（recall@7=0.937、
> 披露税 −79%），但 LLM-judge 端到端两臂对比的预注册门槛 **R1 失败**——"默认分层 +
> 按需召回"使路由准确率显著下降（4 种模拟配置池化 Δ=−0.115 ~ −0.336，非劣界 −5pp）。
> 按 §5.4 裁决树 + 用户决定，C1-C3 全部回滚，机制代码不保留。结果与根因见
> ROADMAP §9 与 `mymain-wiki/harness-evolution/evals/tool_selection/artifacts/c_batch_verdict.md`。
> 本文以下内容为**历史记录**（计划 + 预注册判据），其中"放行/回滚"的预注册规则
> 已被触发并执行（回滚分支）。

> 维护者：shadowinlife + opencode ｜ 2026-08-27 ｜ 状态：**已回滚（R1 失败）**
> 本文定位：ROADMAP §0.2 约定的 C 批分拆产物——工作计划（TASK 卡）+ 调研裁决 +
> 测试计划（预注册判据）。判据先于实验冻结，禁止事后改阈值。
> 上游约束：按 ROADMAP §8.4 最终处置，本批**留本地**（暂缓上游贡献），不产生上游 PR。

---

## 1. 范围与上下文

**问题**（ROADMAP §1 / PAPERS §F）：披露税存在于每一个把全量工具描述注入 LLM
上下文的表面。B 批（2026-08-27 落地）把 MCP keyless 面从 74 裁到 59（−5,100
tok/轮），并证明了路由非劣。但 59 仍远超 25-30 退化区间（PAPERS §F），AGENT 面
~90 更甚。C 批建**路由层**：元工具召回 + 披露层级，把默认决策面压进舒适区。

**C 批三个 PLAN**（ROADMAP §3 工作流 C）：

| PLAN | 内容 | 依赖（已核实） |
|---|---|---|
| C1 | `search_tools` 元工具：自然语言意图 → 候选短名单；索引语料 = AUDIT §7.2 触发关键词列 | A 批（描述基线已冻结）✅、B1 门控机制 ✅ |
| C2 | 披露层级：12 常驻 + 元工具 + gated 通过项为默认面；其余 ~45 工具经 C1 召回；技能维持一行目录 | C1、B 批 ✅ |
| C3 | 5 条路由元规则编译进路由器 system 层 + MCP 客户端指南 | C2 |

**受益形态**：AGENT+MCP（C1/C2 双面）；C3 的 AGENT 面为 system prompt，MCP 面为
客户端指南文档。

---

## 2. 调研裁决（2026-08-27，论文 + 开源实现双源核实）

> 两路并行调研：① PAPERS §F 三篇锚点论文的全文机制核实（arXiv HTML 抓取 +
> 逐节提取）；② 开源工具路由实现普查（OpenBB MCP / FastMCP visibility /
> mcp-toolgate / OmniMCP / ToolLLM retriever / AnyTool / COLT / ToolRet +
> Anthropic/OpenAI 官方工具搜索文档）。完整证据链存于本会话调研记录。

### 2.1 论文机制核实结果（对 PAPERS §F 的校正）

| 锚点 | 核实结论 |
|---|---|
| [2605.24660] BoR/短名单 | **存在，机制属实**：BFCL(370 工具) 上 BM25 打分 + 自适应停止，平均呈现 7.4±2.5 个工具达 90.3% 覆盖（vs 固定 50 的 90.8%）。⚠️ **校正**：内部索引引用的"93.1% vs 87.1%"是**呈现条件下的选择准确率**；端到端反而是固定 5 略胜（73.3 vs 71.7），因固定 5 呈现正解更频繁（84.2% vs 76.9%）。自适应深度的价值在 token 效率而非端到端准确率 |
| [2604.21816] Tool Attention | **存在，机制属实**：两阶段懒加载——Phase 1 全量摘要池（每条 ≤60 token，常驻、可缓存，84% 缓存命中）+ Phase 2 仅对门控 top-k 注入完整 JSON schema；**幻觉门**确定性拒绝未提升工具的调用，是激进门控安全的前提。配置 θ=0.28、k=10；消融：去掉懒加载 −10.3pp（**模型需要完整 schema 才能正确填参**）、TF-IDF 替代嵌入 −8.1pp、24% 失败源于糟糕描述（重写摘要修复 6/7）、48% 失败源于歧义查询、17% 多跳（中间结果后重新检索可缓解）。N≈50 断裂点出自 §7.4 的 token 衰减模型（~400 tok/工具、70% 利用率断裂），是**模型推导值非实测** |
| [2508.01780] LiveMCPBench | **存在**：检索错误 = 失败的 **50.00%**（精确值）；k=1→64.21%、k=5→78.95%、k=10 平台期（检索器饱和）；任务平均需要 2.82 个工具、~3 次重新检索——**召回入口必须支持迭代再搜** |

**补充召回阈值证据**（设定 D1 阈值的依据）：

| 来源 | 规模 | 召回实测 |
|---|---|---|
| 2607.15593（生产网关，混合检索） | 3,616 工具 | Recall@15 = 98.2%（纯密集 83.1%、纯 BM25 67.7%）；k=15 为操作点，再增大 k 召回仅 +0.6pp 而选择准确率下降 |
| 2511.01854（Tool-to-Agent） | 527 工具 | SOTA Recall@5 = 0.80-0.87（agent 级） |
| 2510.17843（GRETEL） | ToolBench | Recall@10 基线 0.841 |
| ToolRet（ACL 2025） | 43k 工具 | 通用嵌入模型工具检索 nDCG@10 仅 33.83——**现成嵌入很弱，除非微调或增强** |

### 2.2 开源实现普查结论

| 系统 | 检索方法 | 短名单 | 激活机制 | 召回载荷 |
|---|---|---|---|---|
| OpenBB MCP server | 类目浏览（无向量） | 全类目列表 | **per-session** `ctx.enable_components` → 仅通知该 session 的 `tools/list_changed` | 名称+短描述（schema 压缩、用时取） |
| mcp-toolgate | 纯词法分层打分（100/60/40/10） | 默认 5、上限 20 | 无激活，invoke_tool 代发 | 名称+一行描述 + `total_matched`/`shown` |
| OmniMCP | 嵌入 + LLM 查询扩写 + 索引化 utterances | 默认 10 | 代发；明确反对动态加载（"破坏缓存"） | 极简 + "下一步"引导文本 |
| ToolLLM retriever | 微调 Sentence-BERT（对比学习） | top-5（10× 过召回再过滤） | 静态候选池 | API 标识+文档 |
| Anthropic tool search | 托管检索 + `defer_loading` | 模型驱动 | **追加式** schema 注入（保缓存）；502 工具实测成本持平、准确率不变 | 加载即完整 schema |
| OpenAI tool_search | 托管/客户端执行两式 | 模型驱动 | `defer_loading`；<20 函数/回合、<10/命名空间、~100 为分布内 | 加载即完整 schema |

### 2.3 设计裁决（逐条对照调研证据）

1. **检索方法 = 确定性词法 + 人工策展的双语触发词**（不引入嵌入模型）。
   依据：① 74 工具规模下纯词法分层打分有生产先例（toolgate ~140 工具）；
   ② 现成嵌入在工具检索上很弱（ToolRet nDCG@10=33.8），微调/扩写才有效，
   而本仓库纪律是零新依赖、离线可复现；③ 触发词策展 = OmniMCP"索引化
   utterances"技术的人工版（AUDIT §7.2 现成 + 先验补全）；④ 词法失效场景
   （ToolRet：查询与文档词汇不重叠）由策展触发词直接覆盖。**风险预注册**：
   若 D1 召回不达标，按 §5 决策树走一轮语料修订，而非引入嵌入。
2. **激活机制 = fastmcp 3.2.4 会话级可见性**（`ctx.enable_components`，OpenBB
   同款，已核实本仓库依赖版本原生支持）：召回的工具以完整 schema 出现在该
   session 的 `tools/list`，客户端原生调用、参数校验不降级；通知只发给该
   session，HTTP 多客户端下也隔离。AGENT 面等价物 = 运行中注册表变更
   （已核实 `AgentLoop` 每迭代重读 `registry.get_definitions()`）+ 会话级
   激活存储。**不采用** invoke_tool 代发（丢失 schema 校验、改变调用契约）。
3. **两阶段披露**（Tool Attention 验证）：默认面 = 12 常驻（完整 schema，
   等价 Phase 1 摘要池但仅 12 条）+ 元工具；召回 = 完整 schema 激活（Phase 2）。
   召回结果本身只返回**名称+一行摘要+调用提示**（slim hit，toolgate/OpenBB/
   OmniMCP 一致），不返回 schema——schema 由激活自动提供。
4. **短名单规模 = 默认 7、上限 15**。依据：BoR 平均 7.4（370 工具）、LiveMCPBench
   k=5 即平台期、生产网关 k=15 为操作点、OpenAI <20 规则；7 在本仓库 74 工具
   注册表上 = 9.5% 覆盖深度，深于所有已测设置。支持迭代再搜（LiveMCPBench：
   任务平均 ~3 次检索）。
5. **幻觉门**：MCP 客户端结构性无法调用未列出工具；AGENT 面注册表对"存在但
   未激活"的工具名返回定向错误提示（"用 search_tools 召回"），对应 Tool
   Attention 消融的 −3.2pp 安全项。
6. **负向触发抑制**：语料 `negatives` 列命中即扣分（AUDIT §7.2 现成）+ 分数
   下限（≤0 不返回）+ `total_matched`/`shown` 信封（模型可收窄再搜）。

---

## 3. TASK 卡（实现规格）

### TASK-C1 · search_tools 元工具

**新文件**：
- `agent/src/tools/tool_search_corpus.json` — 索引语料（**已建成**，164 条：
  74 工具 + 90 技能；schema = name/kind/domain/tier/summary/triggers/negatives/
  arbitration；AUDIT §7.2 路由关键子集逐字移植，其余先验补全；构建时未看
  queries.yaml，防评测污染）。
- `agent/src/tools/tool_search_index.py` — 检索引擎：CJK 感知分词（CJK 二元组 +
  Latin 词元，与 run_eval.py 同族）、分层打分（触发词 4.0 / 名称词元 3.0 /
  摘要位置权重 1.0 / 负向触发 −6.0）、分数下限、确定性平局裁决（kind, name）、
  语料懒加载缓存。无依赖、无网络、纯函数。
- `agent/src/tools/tool_search_tool.py` — `SearchToolsTool`（AGENT 面工具类）：
  `search_tools(query, max_results=7)`；注入当前注册表引用 + session_id；
  召回时把被召回工具实例注册进当前注册表（本 run 下一迭代即可调用）并写入
  会话激活存储（后续 run 可见）；幽灵工具防护——只通告 `check_available()`
  通过的工具（strategy_discovery/guard.py 同款 fail-safe）。
- `agent/mcp_server.py` — `@mcp.tool search_tools(query, max_results=7, ctx)`：
  同一检索引擎；召回时 `await ctx.enable_components(names=..., components={"tool"})`
  （会话级激活 + 仅该 session 收 list_changed）；返回 slim-hit JSON。

**输出契约**（两面一致）：
```json
{"status": "ok", "query": "...", "matches": [
   {"name": "...", "kind": "tool|skill", "summary": "...", "score": 12.3,
    "already_visible": false, "hint": "..."}],
 "total_matched": 9, "shown": 7,
 "note": "召回工具已激活，可直接调用；未命中请换措辞再搜。"}
```

### TASK-C2 · 披露层级

- `agent/src/tools/disclosure.py` — 层级配置的单一事实源：
  `ALWAYS_ON_TOOLS`（AUDIT §8.2 输入 2 的 12 个）、`META_TOOL`、gated 家族
  名单、`tier_of(name)` 分类、会话激活存储（进程级、按 session_id、带锁）。
- **MCP 面**：启动时对 on-demand 工具施加全局 `Visibility(False, names=...)`
  变换（fastmcp 3.2.4 原生；`list_tools` 各路径均尊重）。gated 通过项保持可见
  （B1/B2 注册时门控不动）。keyless 默认面 = 12 + search_tools = **13**。
  B3 移出的 2 个运维工具经召回恢复 MCP 可达（ROADMAP B3 欠账）。
- **AGENT 面**：`build_registry(disclosure="full"|"tiered")`——**默认 full**，
  swarm（`build_swarm_registry` 从全量注册表过滤白名单）与既有测试零变化；
  `session/service.py` 在 `VIBE_TRADING_TIERED_TOOLS=1`（默认值由 §5 决策树
  裁定）时传 tiered：仅注册 always-on + 元工具 + gated 通过项 + 本会话已激活项。
- **技能**：维持一行目录（系统提示词注入 90 条摘要现状不变）；`list_skills`/
  `load_skill` 降为 on-demand（经"技能"触发词召回），与 AUDIT §8.2"lazy 层"
  语义一致。

### TASK-C3 · 路由元规则

- `agent/src/agent/context.py` — system prompt 追加静态路由规则块（可缓存；
  仅当 search_tools 注册时注入，幽灵防护）：① 数据任务遵循 data-routing 规则
  （标准 OHLCV 走 get_market_data）；② 金融研究流程先查 list_swarm_presets；
  ③ 标的解析只走 search_symbol；④ 同族工具按仲裁规则裁决、禁止"都试试"；
  ⑤ 需要的工具不可见时用 search_tools 召回，勿猜测不可见工具名。
- `README.md` MCP 节 — 客户端指南：层级面语义、search_tools 用法、激活行为
  （会话级、客户端需响应 tools/list_changed）、gated 工具条件。

### 锚点与文档同步（B 批全局验收规则继承）

- `test_readme_counts.py` 钉死值随 keyless 面 59→13 更新（测试本身子进程
  keyless 测量，机制不变，仅 README 枚举与计数行同步）；6 份 README 工具枚举
  行 + 条件工具说明行更新。
- 技能数（90）、preset 数（30）等其他锚点不动。

---

## 4. 验证计划（三层，对齐 B 批结构）

- **L1 单元测试**（pytest，CI 门槛）：语料 schema 完整性；检索确定性；已知
  正例/负例；激活流程（MCP 会话级 + AGENT 注册表变更）；层级分类完备性；
  swarm 白名单不受影响；幽灵工具防护。
- **L2 实测**（真实 opencode + 本仓库 MCP，CLI 并发）：S1 keyless 13 工具面 +
  召回流（含 B3 运维工具召回恢复）；S2 技能召回；S3 有配置时 gated 恢复；
  S4 AGENT 面 tiered 开关两侧行为。
- **L3 LLM-judge E2 式对比**（改进阈值法，§5 判据裁决）。

---

## 5. 预注册判据（实验前冻结，禁止事后改阈值）

> 方法学继承 B 批四缺口闭合（strict 主口径、非劣边界+CI 下界、噪声地板、
> 功效意识）。判官面板 = DEC-2 固定的 qwen3.8-max + kimi-k3（temp 0）。

### 5.1 确定性判据（D 组 — C1 质量）

| # | 判据 | 阈值 | 主/辅 | 依据 |
|---|---|---|---|---|
| D1 | E1 全集（158 query）召回率：正解进入 top-7 短名单 | **≥ 0.90** | **主（C1 放行门槛）** | §2.1 召回证据：527 工具 SOTA Recall@5=0.80-0.87；本注册表 74 工具、k/N=9.5%（生产 3,616 工具场景的 23 倍深度）+ 策展触发词 |
| D2 | 短名单规模中位数（默认 max_results） | ≤ 7 | 主 | PAPERS §F 舒适区；ROADMAP C1 验收 |
| D3 | 负向触发越级率：带负向标注的 query 中，负项排名高于正解的比例（正解被召回的子集内） | ≤ 5% | 主 | AUDIT §7.2 负向触发列；ROADMAP"负向触发不召回" |
| D4 | 选择性：recall@3 | ≥ 0.80 | 辅 | 防止召回率纯靠大 k 堆出 |
| D5 | 确定性：同 query 两次运行短名单逐字节一致 | 100% | 主 | 仓库评测纪律（run_eval 同款） |

**语料修订规则（预注册）**：D1 未达 → 允许**一轮**失败案例回填式语料修订
（逐条记录：query/缺失触发词/修订人），修订后重测一次；仍未达 → C1 不放行、
C2 阻塞。禁止迭代拟合（第二轮回填必须走 E3 遥测流程，不在本批）。

### 5.2 表面判据（S 组 — C2 收益，确定性非 LLM）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| S1 | 披露税降幅：keyless MCP 面语料（13 工具）vs 74 基线语料的 wire-format token 数 | **≥ 60%** | **主（收益门槛）** |
| S2 | 能力可达性无损：语料中每个工具均可经某个查询召回（名称查询必中）；gated 工具在有 key 环境正常注册 | 100% | 主 |
| S3 | swarm 不受影响：30 preset 白名单解析不变（build_swarm_registry 全量过滤） | 30/30 | 主 |
| S4 | 默认面工具数（keyless 实测 tools/list） | = 13（12 常驻 + search_tools） | 主 |

> S1 阈值校准教训（B 批 C6）：阈值锚定**实测**每工具常数（B 批实测 ~340
> tok/工具 wire-format），不锚定审计粗估。74→13 ≈ −61 工具 ≈ −20.7k tok/轮，
> 对 74 基线 ~52k = ~62%——阈值取 60% 留测量方法余量。

### 5.3 路由判据（R 组 — L3 LLM-judge，C2 放行门槛）

**协议（预注册）**：两臂模拟部署流——
- **臂 A（基线）**：呈现 59 工具语料（`corpus_b_post.yaml`，B 批冻结）+ query
  → 判官选工具（或声明无合适工具）。
- **臂 B（层级）**：阶段 1 呈现 13 默认面 + query → 判官从可见面选**或**声明
  "需要搜索"；声明搜索时，用**原 query**（判官不改写，预注册简化——改写质量
  是部署变量，记为局限）跑确定性 search_tools → 阶段 2 呈现 可见面 ∪ 短名单
  → 判官选。
- 规模：158 query × 2 臂 × 2 判官 = 632 主调用 + 重测探针（8 query×3 repeat×2
  判官 = 48，噪声地板）。缺席集（keyless 下 gated 工具为期望的 query）移出主
  效力集、单独描述（B 批 C5 同款）。

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| R1 | 池化 strict 非劣（臂B − 臂A，主效力集） | 确切 95% CI 下界 > −5pp | **主（放行门槛）** |
| R2 | 噪声带规则：池化 \|Δ\| ≤ 重测带宽 → 记无效应 | — | 主（解释规则） |
| R3 | 分模型 strict | 报告项，无放行权 | 辅 |
| R4 | lenient 敏感性 | 不得反转 R1 | 辅 |
| R5 | 失败分解（报告项）：召回缺失率（正解未入短名单）vs 选择错误率（正解在短名单未选中） | 描述 | 辅 |
| R6 | 召回开销：臂 B 需要搜索的 query 占比 | 描述（预期 40-60%） | 辅 |

### 5.4 裁决树（预注册）

- D1-D3+D5 过 且 S1-S4 过 且 R1 过 → **C 批放行**；`VIBE_TRADING_TIERED_TOOLS`
  默认 1（AGENT 面 tiered 开启）。
- R1 落入噪声带（R2）且 S1 过 → **放行但标注"路由无损不可测"**（收益由 S1
  独立承载，B 批同款裁决路径）。
- R1 失败（CI 下界 ≤ −5pp）→ **否决 C2 默认开启**：MCP 面回退全量暴露，
  search_tools 保留为可选发现工具；按 R5 分解归因（召回缺失→修语料；
  选择错误→修呈现）。
- S1 未达 → 收益不成立，即使 R1 过也不默认开启（B 批 C6 同款纪律）。

---

## 6. 风险登记

| 风险 | 缓解 |
|---|---|
| 客户端不响应 tools/list_changed | search_tools 结果自带 slim-hit（名称+摘要+提示）兜底；L2 实测 opencode 行为；README 指南声明要求 |
| 单 turn 任务（`vibe-trading run`）需要 on-demand 工具时多付一次搜索 | 运行中注册表变更已核实可行（每迭代重读定义）；搜索是 1 次廉价工具调用 vs 全量披露每轮 ~20k tok |
| 语料触发词覆盖不足（词法失效） | D1 门槛 + 预注册的一轮回填修订；触发词策展本身即对标 OmniMCP utterances 技术 |
| HTTP 传输多客户端共享激活 | 会话级 Visibility 天然隔离（规则按 session 存储）；已核实机制 |
| 评测污染（语料拟合 queries.yaml） | 语料 v1 构建全程未看 queries.yaml（本文 §3 声明）；修订轮逐条留痕 |
| 判官噪声淹没小效应 | 噪声带规则预注册（诚实 null 优于过度解读）；探针实测带宽 |

---

## 7. 执行阶段

```
Phase 0  调研 + 语料 + 计划冻结（本文档）            ✅ 完成
Phase 1  实现 C1（检索引擎 + 双面元工具）+ 单测
Phase 2  实现 C2（disclosure.py + MCP Visibility + AGENT tiered）+ 锚点同步
Phase 3  实现 C3（system 规则块 + README 指南）
Phase 4  L1 全绿 → L3 确定性判据（D/S 组）→ 语料修订规则触发则执行
Phase 5  L3 LLM-judge（R 组）→ 裁决树
Phase 6  L2 实测（真实会话）
Phase 7  裁决：c_batch_verdict.md + ROADMAP 回写 + 提交（DCO，本地）
```

**放行门槛**：§5.4 裁决树。批末跑全量 pytest（`pytest --ignore=agent/tests/
e2e_backtest --tb=short -q`，4 个 src/providers/ 既有失败为 HEAD 既有，与 C 批
无关，B 批已确认）。
