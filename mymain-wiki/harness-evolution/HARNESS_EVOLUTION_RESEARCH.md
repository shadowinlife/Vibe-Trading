# Harness 演进研究 — 是否替换 `opencode + omo + mcp` 布局

> 维护者：shadowinlife ｜ 初版：2026-08-22 ｜ 状态：**定稿**（两轮调研合并完成）
> 配套文档：`HARNESS_EVOLUTION_PAPERS.md`（论文索引，48 篇）· `HARNESS_EVOLUTION_BENCHMARKS.md`（评测基准目录）
> 决策问题：当前 `opencode + oh-my-openagent(OMO) + MCP` 布局是否应替换为自研/第三方 harness 框架？
> **结论速览**（详见 §7）：不从零自研，也不死守 opencode——**迁移到成熟 Python 基座（首选 PydanticAI v2）+ 平移 OMO 可移植资产 + MCP server 原样保留**，同时服务端 P0-P3 增强与基座迁移 PoC 双轨并行。

## 0. 研究背景与动因

**用户提出的三个痛点**（待 §6 逐条验证）：
1. OpenCode + OMO 架构优秀，但 AGENTS.md 的灵活度可能不如垂直量化/财经定制 harness 的提示词灵活；
2. opencode v1 的 event 较混乱，v2 分支当前有严重 bug；通过 opencode-serve 提供事件存在问题；
3. opencode 作为 Node.js 工程，内存管理效率低于其他语言。

**硬性约束**：
- 切换后**不能损失思考能力**——必须保留 OMO 提供的深度推理编排（skills/subagents/categories/team mode）；
- 必须能承接现有 ~77 个 MCP 金融工具面（`agent/mcp_server.py`）与 grounding/治理资产。

**调研方法**：本地代码深读（`agent/src` 核心模块逐文件）+ 多路并行外部调研（OSS 源码级验证、arXiv 检索、harness/MCP 工程实践），关键仓库验证到 commit SHA。第一轮报告 2026-08-21；本轮增量调研 2026-08-22。

---

## 1. 现状盘点：Vibe-Trading agent 层架构（2026-08-21 深读）

本仓库（HKUDS/Vibe-Trading，31.4k★）的 agent 层不是"LangChain 封装"，而是一个相当完整的**领域 harness 内核**：

| 模块 | 实现 | 设计要点 |
|---|---|---|
| **核心循环** | `src/agent/loop.py`（2509 行） | ReAct 循环 + **5 层上下文管理**（microcompact 剪枝旧工具结果 → context_collapse 零成本折叠 → auto_compact LLM 摘要+尾部 20k token 保护 → 模型主动 compact 工具 → 迭代式摘要更新）；只读工具线程并行批处理 |
| **Grounding 闸门** | `src/agent/grounding.py`（2649 行） | **确定性**身份解析状态机（unresolved/conflicting/locked/ambiguous）+ 数字证据账本（摄取所有工具返回的 OHLC + run_dir 内 CSV）；**最终答案机器校验**：价格断言与观测证据矛盾、数字挂到本 session 未处理的标的，草稿被打回重写（有界恢复轮次） |
| **研究目标协议** | `src/goal/` | GoalRecord 带 token/turn/time 预算、12 态生命周期、risk tier（live-trading 目标被正则结构性拒绝）、criteria + evidence ledger（artifact_hash / data_as_of / freshness_status / contradicts_claim_ids） |
| **Swarm 编排** | `src/swarm/`（runtime 768 行 + worker 1185 行） | 30 个 YAML preset，DAG 拓扑分层、层内并行；每 agent 独立 tool/skill 白名单；worker 预取 grounding 市场数据注入 prompt；stale-run reaper + retry；deliverable 校验 |
| **记忆** | `src/memory/` | Markdown + FTS5 索引，Tier-2 分类目录（user/feedback/project/reference），可选生命周期（质量评分/Ebbinghaus 衰减/归档 GC） |
| **技能** | 91 个 bundled skills | 渐进式披露（system prompt 只放一行描述，`load_skill` 取全文），自进化 CRUD（save_skill/patch_skill） |
| **治理** | `src/governance/` | 哈希 manifest（prompt+skills+tool registry+包版本）、哈希链 fsync 审计账本、sink-aware 脱敏 |
| **策略发现** | `src/strategy_discovery/` | **证据门控**：无回测证据即拒绝评估；freshness（fresh/aging/stale）fail-closed；成本可行性筛查（sizing-corrected breakeven） |
| **回测引擎** | `agent/backtest/` | 10 个市场引擎（T+1/涨跌停/funding/lot/tick grid），AST 硬化沙箱执行生成代码，Monte Carlo/Bootstrap/Walk-Forward 验证 |
| **金融数学** | `src/quantlib/` | 286 个受测函数（含 multipletesting、purged CV、Brinson 归因），经单一 read-only `quantlib_call` 暴露 |
| **MCP 表面** | `mcp_server.py`（2977 行） | 77 工具（memory ON 时 82）；shell fail-closed；**下单工具永不上 MCP**；分页/输出上限 |
| **评测** | `src/evals/agent_eval/` | golden trace + prompt hash + scorer + stub LLM；全套约 4700 测试 |

**系统提示词值得单独点名**：6 条输出原则（每个数字必须指向本 session 的工具调用、每个数据带 as-of、工具没返回的不补、分析非建议、足够即停、显式拒绝）——成文的 grounding 契约，与 Alpha Illusion 论文提出的"LLM 只做可审计信息接口"高度同构。

---

## 2. 量化领域 OSS 工程实践调研（源码级验证，2026-08-21）

### 2.1 主对比表

| 项目 | Stars | 编排拓扑 | 记忆/反思 | 回测引擎 | 评测方式 |
|---|---|---|---|---|---|
| **TradingAgents** | 99.1k | LangGraph：分析师→bull/bear 辩论→研究经理→交易员→三方风险辩论→PM | **延迟结果反思**：决策日志 pending→resolved，用实现收益+alpha 反思，按标的回注 | ❌（已移除） | 论文回测；57 个测试含 look-ahead 守卫 |
| **ai-hedge-fund** | 63.0k | 流水线：PIT 数据→分析师→混合→**风险硬钳制**→执行→记录；回测/纸面/实盘同一代码路径 | PromptCache 精确重放；无教训累积 | ✅ 基础（无成本/滑点） | CPCV/PBO 声明未实现 |
| **FinGPT** | 21.1k | 非 agent 框架（LoRA 微调库） | ❌ | ❌ | NLP benchmark |
| **FinRobot** | 7.8k | AutoGen GroupChat；Desktop 版"确定性计算、LLM 叙述" | RAG only | ❌（有确定性估值引擎） | 报告数字溯源 |
| **RD-Agent**（MS） | 14.3k | 假设→实验→CoSTEER 代码→Docker 内跑真 Qlib 回测→反馈进化 | KnowledgeBase（pickle+图+向量） | ✅ 真实（Qlib in Docker） | **全量化反馈**（IC/收益，非 LLM 评审） |
| **Qlib** | 47.8k | 无 LLM 层（README 指向 RD-Agent） | — | ✅ 生产级成本模型 | IC/RankIC |
| **OpenBB** | 72.1k | 数据平台 + **MCP server 扩展**（按会话动态激活工具防 token 膨胀） | ❌ | ❌ | 平台测试 |
| **Lean / freqtrade / nautilus** | 21k/54k/27k | 非 LLM 基线：fill model/滑点/费用/walk-forward/hyperopt | — | ✅ 业界标杆 | 回测指标 |

### 2.2 2025-26 新物种（生态已分化为四类）

1. **Harness 原生平台**：LangAlpha（1.7k，**Programmatic Tool Calling**——agent 对 MCP 工具写 Python 而非把数据灌进上下文）、QuantDinger（10.9k）、Vibe-Trading 本身；
2. **Agent 竞技场**：HKUDS/AI-Trader（21.5k，外部 agent 经 SKILL.md 注册、信号打擂、跟单——⚠️ 无 license 文件）；
3. **实盘循环终端**：nofx（12.7k，Go 运行时**硬风险钳制模型无法覆盖**，9 交易所实盘+公开收益榜）；
4. **记忆/审计中间件**：tradememory-protocol（1.4k，5 层记忆+SHA-256 防篡改审计，已进入维护模式）、TraderHarness、agent-backtest-lab（防污染回测+DSR/PSR/SPA 多重检验+PBO+reward-hacking 检测——星少但正是评测前沿）。

### 2.3 八条已验证的前沿工程实践（GAP 分析的基准线）

1. **结果锚定的反思**（TradingAgents）：决策日志 pending→resolved + 用实现 alpha 延迟反思 + 按标的回注——唯一生产级教训累积机制；
2. **确定性契约**（ai-hedge-fund）：字节级一致的 cycle 记录 + prompt-cache 重放 + 明确的失败契约；
3. **有界的 LLM 权限**（ai-hedge-fund/nofx）：LLM 输出只是有界信号，确定性风险钳制不可协商且审计留痕；
4. **量化门控的研究循环**（RD-Agent）：指标永远来自真实回测，LLM 不做评审；
5. **回测本身的统计验证**（agent-backtest-lab/ai-hedge-fund roadmap）：CPCV/PBO/DSR/PSR——**无人完整交付，是开放的空白**；
6. **Look-ahead 守卫写成测试**（TradingAgents）：`test_news_lookahead.py` 等进 CI；
7. **MCP token 经济学**（OpenBB/LangAlpha）：按会话动态激活工具 + 程序化工具调用；
8. **成本/滑点对 agent 可见**：所有 LLM 框架都没做，非 LLM 基线全做了——Vibe-Trading 的引擎成本建模+成本盈亏平衡门**已经领先全部 LLM 项目**。

---

## 3. GAP 分析：agent 层 vs 前沿研究/工程实践（2026-08-21）

### 3.1 已经领先的（相对 OSS 同类）

| 维度 | Vibe-Trading 现状 | 对照 |
|---|---|---|
| **确定性输出验证** | grounding 闸门机器校验数字断言、打回矛盾草稿 | 精确实现 Alpha Illusion 的"可审计信息接口"处方；同类 OSS 无一具备 |
| **证据门控策略发现** | 无证据拒绝评估、stale fail-closed、成本盈亏平衡 | 全生态唯一；RD-Agent 有量化反馈但无新鲜度门控 |
| **回测引擎真实度** | 10 引擎市场微观结构+成本+验证三件套 | 领先所有 LLM 框架（零成本建模），接近非 LLM 基线 |
| **治理/审计** | 哈希 manifest + 哈希链账本 + 脱敏 | 与 MCP Financial Services IG（Bloomberg+Saxo 牵头）完全同向 |
| **技能渐进披露** | 91 技能一行描述+按需全文 | 符合 Voyager/Claude Code 模式 |

### 3.2 真实 GAP（按严重度排序）

1. **结果锚定的反思闭环缺失**（最大 GAP）。有记忆、有证据账本、有 SDM 衰减监控，但没有"决策记录 pending→resolved → 用实现收益/alpha 反思 → 教训按标的回注"的闭环。当前 `memory/lifecycle.py` 的衰减是**存储侧**的，缺**结果侧**的。
2. **确定性/重放契约缺失**。LLM 响应不可重放——golden trace 评测只能测结构不能测回归。
3. **统计验证未成体系**。quantlib 已有数学（deflated Sharpe/purged CV），但 agent 研究循环产出的策略**没有强制**过 CPCV/PBO/DSR 门。OpenPM 式"污染证书+成本敏感曲线"完全缺失。**全行业空白，谁先做谁定义标准**。
4. **知识截止污染无控制**。回测数据是 PIT 的，但 agent 的**推理**不受掩码约束。
5. **归因未达因子层**。有 Brinson（组合层），缺"agent 决策收益 → 市场/风格/选股"分解。
6. **MCP 工具面 token 经济学**。77 工具全量加载 ≈54k token 披露税，每个规划轮重复支付；工具选择准确率 25-30 个后退化、~100 崩塌。OpenBB 按会话激活、MCP 官方 catalog→inspect→execute 三层发现，都还没有。
7. **程序化工具调用（code mode）未支持**。数据密集链仍走上下文；服务端未提供 outputSchema/类型化 stub。
8. **Swarm 结构化状态不彻底**。`{upstream_context}` 仍是 NL 报告注入 prompt——"电话效应"论证指向类型化磁盘产物。
9. **辩论成本未验证**。30 个 preset 大量使用辩论结构，但没有与廉价自一致性基线的消融对比。
10. **前瞻/实盘评测缺席**（或为刻意选择）。前沿已转向 forward-only 评测。

---

## 4. 架构优劣势矩阵：`opencode + MCP` 及其他 harness+MCP 模式（2026-08-21）

### 4.1 当前模式（薄 MCP server + 通用 harness）的优势——有实证支撑

1. **Harness 商品能力免费获得且自动进化**：压缩、子 agent、后台任务、权限、checkpoint、worktree。mini-swe-agent 证据（100 行 harness 拿 SWE-bench 65%）表明 **harness 复杂度有负半衰期**——自建 harness 的维护负担会随模型变强而贬值；
2. **多 harness 可移植是真实红利**：同一 MCP server 可被 opencode/Claude Code/Codex/Cursor 消费。OpenBB Workspace MCP 官方表述："最快的公司在跑长驻 agent——Claude Code、Codex 这类系统"——通用 harness + 金融 MCP 是被生产验证的模式；
3. **治理下沉到 MCP 层是正确方向**：fail-closed shell、下单工具不上 MCP、审计账本——与 MCP Financial Services IG（2026-06-25 章程：防篡改记录/数据 lineage/attestation/策略执行）完全同向；
4. **技能分发**：SKILL.md + pip + ClawHub 的摩擦远低于分发完整 harness。

### 4.2 劣势/风险——每条都有出处

1. **披露税每轮重付**：77 工具 × ~700 token ≈ 54k token/规划轮；工具选择准确率 25-30 个工具后退化。**通用 harness 目前都不做渐进式工具发现**（opencode 无 tool search；ToolSearch 只在 Anthropic 自家 Agent SDK）——这个缺口只能服务端自己补；
2. **数据密集链穿过上下文**：回测迭代正是 code mode 要解决的场景，服务端不给 outputSchema/typed stub，客户端的 code-mode 无从发力；
3. **编排状态分裂**：swarm 状态在 MCP server 内、会话状态在 harness 内；harness 的压缩可能丢掉领域关键上下文，harness 的 doom-loop/权限又不懂金融语义——**grounding 闸门这类领域不变量只有服务端能守**；
4. **prompt cache 失效风险**：MCP server 异步初始化/工具数组重排会使缓存前缀失效，77 工具放大该成本；
5. **安全面**：工具投毒/rug pull/跨 server 影子（MCPSecBench：现有防护 <30% 有效）；
6. **延迟**：协议层约 3× 于直接调用——对数据密集循环有影响，对研究型工作可接受。

### 4.3 与其他模式对比

| 模式 | 相对优势 | 相对劣势 |
|---|---|---|
| **Claude Code + MCP** | hooks 确定性拦截、ToolSearch、子 agent 隔离更成熟 | MCP 经济学相同；仍是通用 harness，领域不变量无人守 |
| **Codex CLI + MCP** | 沙箱一等公民，`environment_id` 可把 MCP server 跑进沙箱 | 同上 |
| **OpenBB Workspace MCP** | 最接近的生产对照：证明"通用 harness+带治理的金融 MCP"可行 | 无编排/无记忆/无反思——纯数据层 |
| **集成领域 harness**（TradingAgents/ai-hedge-fund/RD-Agent/nofx） | 循环/记忆/确定性完全自控 | 维护全部 harness 债务；评测声明均仅作者报告；grounding/治理反而落后 Vibe-Trading |
| **pi / CLI-first 极简派** | 上下文经济学最优（225-token README vs 13.7k-token Playwright MCP） | 零可移植性、零治理——与金融合规诉求背道而驰 |

**第一轮结论**：争论点不是"MCP vs 集成"，而是**谁拥有渐进披露、输出压缩、编排状态这三件事**——通用 harness 不会为金融领域自动做它们。

### 4.4 三套 Stack 对比：OpenCode+OMO vs PydanticAI V2 vs Vibe-Trading agent 层（2026-08-22）

> 前提澄清：**三者不在同一层**。Vibe-Trading agent 层是**领域内核**（经 MCP 对外服务），OpenCode+OMO 与 PydanticAI 是**通用 harness 层**（消费 MCP 的客户端/循环）。真正的二选一是后两者；Vibe-Trading agent 层在两种方案下都原样存活。

| 维度 | OpenCode + OMO（现 harness） | PydanticAI V2（候选基座） | Vibe-Trading agent 层（领域内核） |
|---|---|---|---|
| **定位** | 通用编码 harness + OMO 编排智能层 | 通用 agent 基座（typed loop + Capability 原语） | 量化领域 harness 内核，以 MCP 形态服务 |
| **运行时/内存** | ❌ Bun/TS + Effect：系统性监听器泄漏（127MB→4.9GB/20min，#34574；serve 观测 18.2GB，#36739） | ✅ 纯 Python（pydantic-core 为 Rust 编译），无 Node，依赖面小 | ✅ Python，与量化栈（pandas/回测）同进程 |
| **事件系统/可编程性** | ❌ 四代事件并存（Legacy/SessionV1/EventV2→裸 EventEmitter 桥接），payload `any`；serve SSE 反复崩溃（#40812） | ✅ AG-UI + Vercel AI 一等事件流，thinking token 有专门流式事件；OTel 原生 | ⚠️ 无交互事件面（由外层 harness 承担），MCP 请求/响应为唯一界面 |
| **推理模型支持** | ⚠️ 经 opencode 模型层透传；OMO 按 provider 做变体适配 | ✅ **类型化 Thinking 一等消息部件**；全厂商"换字符串"即切（Qwen3-Max 经 OpenAI 兼容端点零摩擦） | ✅ 不绑定模型；quantlib/回测是确定性计算 |
| **MCP 集成** | ⚠️ 客户端全量披露：77 工具 × ~700 token ≈ **54k token 披露税/规划轮**；无渐进发现 | ✅ 原生 MCP capability + **进程内 FastMCP 直连**（无 IPC）；on-demand capability 天然支持懒加载 | ✅ **MCP 服务端本体**：77 工具、shell fail-closed、下单工具永不上 MCP |
| **多智能体编排** | ✅ OMO delegate-task（8 类别）+ team mode（12 工具/文件邮箱/tmux）——编排智能最成熟 | ⚠️ Harness SubAgents + agent-as-tool——原语干净但编排智能需自建 | ✅ swarm：30 个 YAML preset、DAG 分层并行、每 agent tool/skill 白名单、deliverable 校验 |
| **技能机制** | ✅ SKILL.md 原生 + OMO 54+ hooks 注入 | ✅ `defer_loading` capability 与 Agent Skills 同构（markdown、一行目录、按需加载）——**懒加载语义直接解决披露税** | ✅ 91 个领域技能，渐进式披露，自进化 CRUD |
| **记忆/反思** | ⚠️ opencode 会话压缩 + OMO preemptive-compaction（策略好，触发依赖 opencode API） | ⚠️ Harness memory + compaction（新，需压测） | ⚠️ Markdown+FTS5 + Tier-2 + 生命周期衰减——**缺结果锚定反思闭环**（全生态共同 GAP） |
| **领域治理** | ❌ 无——harness 的 doom-loop/权限不懂金融语义 | ❌ 无（需自建——恰由 Vibe-Trading 层经 MCP 补足） | ✅ **独家**：grounding 闸门机器校验数字断言并打回矛盾草稿、哈希 manifest + 哈希链审计、risk tier 结构性拒绝 live-trading、证据门控 + freshness fail-closed |
| **量化资产** | ❌ 无 | ❌ 无 | ✅ **独家**：10 市场回测引擎、286 受测金融数学函数、26 数据源、AST 硬化沙箱 |
| **评测** | ⚠️ 无内建 | ✅ Pydantic Evals 内建 + YAML/JSON agent spec | ✅ golden trace + prompt hash + scorer，~4700 测试；缺前瞻/防污染评测 |
| **垂直定制灵活度** | ⚠️ 靠 AGENTS.md/hooks 提示词工程；深改要碰 opencode 内部 | ✅ Capability/hooks/事件流全为显式 API，循环每步可拦截改写 | ✅ 完全自有代码——但只限领域层 |
| **锁定风险** | ⚠️ OMO 硬钉 `@opencode-ai/plugin@1.18.19`；v2 重写期插件 API 换代；skills/MCP/19 个 Core 包可移植（omo-codex 已实证） | ✅ MIT，模型无关，SKILL.md 标准与生态互通 | ✅ 最低——MCP 协议对外，任何 harness 可消费 |
| **生态势能/维护风险** | ⚠️ 200k★ 但 5,275 open issues、Discussions 禁用、v2 有 13+ 严重 open bug（含 plan-mode 安全失效 #43830）；OMO 作者自己在做多 harness 重构 | ✅ v2.33.0 当日发版、100+ commit/30d、Pydantic 团队维护；风险：harness 包新 | ✅ 自维护，上游社区 31.4k★；风险=自己的工程量 |
| **工程成本** | ✅ 商品能力免费（压缩/子代理/权限/checkpoint/TUI） | ⚠️ 需重写：54 hooks 映射、delegate-task 编排、preemptive compaction 触发（skills/prompts/MCP 全平移） | ⚠️ 全部领域层维护债务（无论选谁都逃不掉） |

**一句话优劣**：

| Stack | 核心优势 | 核心劣势 | 最大风险 |
|---|---|---|---|
| **OpenCode + OMO** | 编排智能最成熟（11 agents/54 hooks/team mode），商品能力免费进化 | 内存泄漏实锤、事件混乱、披露税每轮重付、无领域治理 | v2 重写期 API 换代 + 版本钉死，长驻研究 agent 运维负担 |
| **PydanticAI V2** | 七需求全命中：类型化 thinking + skills 懒加载 + 进程内 MCP + 干净事件流 + 模型自由 | 编排智能需自建，harness 包新 | 生态年轻，深水区自助比例高 |
| **Vibe-Trading agent 层** | 领域治理与量化资产**全生态独家** | 不是完整 harness；10 项 GAP（最大：结果锚定反思闭环） | 与 harness 选型无关——两种方案下的共同常数 |

**层级关系与二选一结论**：

```
┌─────────────────────────────────────────────────┐
│  harness 层（二选一）                              │
│  现状: OpenCode + OMO  ──迁移──▶  候选: PydanticAI V2 │
├─────────────────────────────────────────────────┤
│  MCP 协议面（77 工具 + 治理，可移植红利）            │
├─────────────────────────────────────────────────┤
│  领域内核: Vibe-Trading agent 层（两种方案下都存活）   │
└─────────────────────────────────────────────────┘
```

PydanticAI vs OpenCode+OMO 的真实差距在四处：内存/事件工程质量（✅ vs ❌）、工具面经济学（懒加载原生 vs 54k 披露税）、模型切换自由（类型化 thinking vs 版本钉死）、领域定制 API（显式 capability/hooks vs AGENTS.md 提示词工程）；OpenCode+OMO 的反超项只有**编排智能现货成熟度**——而那部分（agent prompts/category 表/skills）恰是可平移资产。

---

## 5. 替换候选框架深度调研（2026-08-22，32 框架源码级调研）

> 调研基准日 2026-08-22。所有活跃度数据（最新 release、近 30 天 commit、license、stars）来自当日 GitHub API 实时抓取；架构事实来自官方文档与仓库源码。候选中有多个**仓库已迁移/改名/停更**，均已修正为当前真实路径。

### 5.1 2026 年生态重大变动（影响所有决策）

| 变动 | 事实（GitHub API 验证） |
|---|---|
| **AutoGen + Semantic Kernel 合并** | `microsoft/autogen` 已停更（最后 push 2026-04-15，license 改为 CC-BY-4.0 即纯文档态）。官方继任者 [microsoft/agent-framework](https://github.com/microsoft/agent-framework)（Python/.NET/Go，MIT，python-1.15.0 @ 08-21，100+ commit/30d），官方文档明确 "the direct successor" |
| **OpenHands 重构** | 主仓库（84.8k★）转为 TypeScript Agent Canvas 浏览器端；Python harness 重写为 [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)（MIT，v1.43.1 @ 08-21）；旧后端归档为 `OpenHands/legacy` |
| **Goose 捐赠 Linux 基金会** | `block/goose` → [aaif-goose/goose](https://github.com/aaif-goose/goose)，2026-04 并入 Agentic AI Foundation（AAIF） |
| **Strands 改名** | `strands-agents/sdk-python` → [strands-agents/harness-sdk](https://github.com/strands-agents/harness-sdk)，自我定位就是 "Build an agent harness" |
| **terminal-bench → Harbor** | → [harbor-framework/harbor](https://github.com/harbor-framework/harbor)（v0.22.0 @ 08-22） |
| **pi 爆红** | `badlogic/pi-mono` → [earendil-works/pi](https://github.com/earendil-works/pi)，95.2k★（OMO 生态的 oh-my-pi 底座），但 TS/Bun 运行时 |
| **停更/降速名单** | **Qwen-Agent**（最后 push 2026-03-04）、**SWE-agent**（0 commit/30d）、**smolagents**（0 commit/30d）、**OpenManus**（9 commit/30d）、**Letta**（4 commit/30d） |
| **SKILL.md 成为事实标准** | OpenHands、Strands、OpenAI Agents SDK、Claude Agent SDK、Goose、AgentScope、PydanticAI 均支持 markdown skill pack——**现有 OMO skills 在以下所有候选中都可平移** |

### 5.2 候选活跃度总表（2026-08-22 实测，节选）

| 框架 | 语言 | License | Stars | 最新 release | 30天commits |
|---|---|---|---|---|---|
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | Python | MIT | 19.4k | v2.33.0（08-21） | 100+ |
| [Strands harness-sdk](https://github.com/strands-agents/harness-sdk) | Python+TS | Apache-2.0 | 7.0k | python/v1.53.0（08-21） | 100+ |
| [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | Python | MIT | 1.0k | v1.43.1（08-21） | 100 |
| [Goose](https://github.com/aaif-goose/goose) | **Rust** | Apache-2.0 | 53.2k | v1.47.0（08-21） | 100+ |
| [AgentScope](https://github.com/agentscope-ai/agentscope) | Python | Apache-2.0 | 29.2k | v2.0.6（08-07） | 74 |
| [Google ADK](https://github.com/google/adk-python) | Python | Apache-2.0 | 21.2k | v2.7.1（08-17） | 100+ |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | Python | MIT | 28.9k | v0.22.0（08-19） | 100+ |
| [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) | Python（内嵌 Node CLI） | MIT | 8.0k | v0.2.143（08-20） | 70 |
| [MS Agent Framework](https://github.com/microsoft/agent-framework) | Python/.NET/Go | MIT | 13.0k | python-1.15.0（08-21） | 100+ |
| [Agno](https://github.com/agno-agi/agno) | Python | Apache-2.0 | 41.8k | v2.9.0（08-13） | 67 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Python | MIT | 40.2k | sdk==0.4.3（08-19） | 40 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | Python | MIT | 57.5k | 1.15.17（08-20） | 100+ |
| [deer-flow](https://github.com/bytedance/deer-flow) | Python | MIT | 80.5k | v2.0.0（06-25） | 100+ |
| [crush](https://github.com/charmbracelet/crush) | **Go** | NOASSERTION⚠️ | 27.6k | v0.90.0（08-19） | 100+ |
| [pi](https://github.com/earendil-works/pi) | TypeScript | MIT | 95.2k | v0.84.2（08-14） | 100+ |

### 5.3 TOP-5 适配度排名（量化金融垂直 harness 基座）

**🥇 1. PydanticAI v2** —— 七个需求全部原生命中且无短板。需求 (a)：thinking 是类型化的一等消息部件（`capabilities/thinking.py`）并经 AG-UI 事件流透出，模型在 OpenAI/Anthropic/Google/Bedrock/Ollama 间"换个字符串"即切——Claude extended thinking → GPT-5/o-series → Qwen3-Max 不丢深度推理；需求 (d)：`defer_loading` capability 与 Agent Skills（markdown 文件）同构，prompt 只留一行目录条目、模型经 `load_capability` 按需加载——与 OMO skills 懒加载语义完全一致，可近乎 1:1 平移。77 个 MCP 工具可经**进程内 FastMCP 直连**（无 Node、无 IPC）；AG-UI/Vercel AI 事件流 + OTel 给 server/UI 打底；Temporal 级 durable execution 对长时回测是加分项。注意：`pydantic-ai-harness` 包较新，采用前建议对 subagent/compaction 做压测。

**🥈 2. Strands Agents (harness-sdk)** —— 唯一把"给你做 harness"写进产品定位的框架。显式 `event_loop` + hooks 可拦截/改写循环任何一步（正是 opencode 事件系统黑盒的反面），guardrails + steering handlers 对金融合规护栏是现成抽象。双端原生 Agent Skills 插件（`vended_plugins/skills/agent_skills.py`，SKILL.md）；MCP 与 multiagent 内建；双向流式。扣分项：thinking 停留在 provider 透传层无类型化抽象；社区规模五强最小（7k★）。

**🥉 3. OpenHands Software Agent SDK** —— "成品 harness 完整度"最高：Agent Server（REST+WebSocket 事件 API）+ AgentSkills/plugins/marketplace + MCP + 多智能体 + 沙箱工作区，全部 MIT；Agent Canvas 就是这套 API 的现成前端参考实现；经 LiteLLM 模型无关；SWE-bench 77.6 证明循环质量。排第三的原因：仓库仅 1k★、刚完成单体→SDK 迁移，API 稳定性风险最高；设计重心是软件工程 agent，量化域语义要自己做适配层。适合"快速出 demo、边用边换血"。

**4. Goose** —— 内存效率需求（痛点 3）的唯一满分答案：Rust 单二进制，彻底告别 Node 内存问题；MCP 集成深度生态第一（70+ 扩展、MCP Apps 可渲染工具 UI）；skills + subagents + Recipes（YAML 工作流包，可进 CI）；15+ provider；官方支持打自定义 distro（"量化版 goose"）；Linux Foundation 治理。压到第四的原因：**harness 核心定制必须写 Rust**——Python 量化资产（因子库、回测、pandas）只能以 MCP server 外挂，拿不到"循环与量化库同进程"的集成深度。

**5. AgentScope 2.0** —— 独特生态位：点名的 **Qwen3-Max 在其自家框架上是一等公民**，2.0 设计哲学就是"依赖模型推理能力而非硬编排"；MCP Hub + Skill Hub（安装/装备分离、跨 workspace 复用）是五强中唯一的"技能市场级"机制；Agentic Memory + ReMe 长期记忆与本项目 memory 层演进方向高度重合。扣分项：v2 落地仅两个月，事件/流式开放程度不如 PydanticAI 的 AG-UI 透明。**若决定深度绑定 Qwen/DashScope 路线，应升至第 3。**

**第二梯队**（可用但有明确短板）：Google ADK 2.0（图式 Workflow Runtime + Task API 编排最强，但**无原生 skill 机制**）；OpenAI Agents SDK（sandbox agents 带 Skills/Memory/Compaction，但 OpenAI 引力重）；Claude Agent SDK（skill/subagent/hooks 体验最好，**但两个硬伤致命：仅 Claude 模型（需求 a 不成立）+ 本质驱动打包的 Node.js CLI 子进程（痛点 3 原样保留）**）；MS Agent Framework（继任者势能 + Harness Agent，但 Azure 引力且太新）；Agno（平台捆绑重）；LangGraph（适合编排层而非 harness 基座）；CrewAI（高层意见化）；Letta（降速，仅作记忆设计参考）。

**不推荐作为基座**：Mastra/CopilotKit/pi（TS/Node，痛点 3 出局）；crush（终端产品非可嵌入框架）；LiveKit（语音域）；Inspect AI/Harbor（**评测**框架，不是生产运行时——但建议拿它们给新 harness 建回归评测）；AgentZero（个人应用）；OpenManus/SWE-agent/Qwen-Agent/smolagents（停更或维护态；mini-swe-agent 的 ~100 行极简循环可作设计参考）；deer-flow（深度研究子系统参考，非基座）。

### 5.4 三条事实性结论（build-vs-evolve 决策输入）

1. **SKILL.md 已经赢了**：五强全部支持 markdown skill pack。无论选谁，现有 OMO skills 资产都可迁移，不会被锁定在 opencode 上。
2. **MCP 已是所有活跃框架的标配**：77 工具 MCP server 在任何候选中都是即插即用资产；差异只在"进程内直连"（PydanticAI FastMCP、Claude SDK in-process）与"外部连接"之间。
3. **留在 opencode 的真实风险**：面对的不是"要不要换"，而是上游生态正在快速收敛——AutoGen 已死、SWE-agent/Qwen-Agent/smolagents 停更、OpenHands 自己都重写了一遍。建议先用 Inspect AI 或 Harbor 建一套针对自己 harness 的回归评测，让换基座决策有量化依据而不是靠体感。

---

## 6. opencode v1/v2 现状与 OMO 可移植性审计（2026-08-22，源码级）

> 证据基线：opencode 源码 `anomalyco/opencode` dev @ `e00890c6`；OMO 源码 `code-yeongyu/oh-my-openagent` dev @ `13a00bf1`（均 2026-08-22 克隆）。

### 6.1 用户痛点逐条验证

| 痛点 | 判定 | 证据 |
|---|---|---|
| 1. AGENTS.md 灵活度不如垂直定制 harness | **部分成立** | 垂直定制的灵活度在提示词工程层，与 harness 无关——OMO 已证明全部 agent prompt/category 是纯文本可移植资产（§6.3）；任何候选基座都提供等价或更强的指令注入点（PydanticAI capabilities、Strands hooks、Goose recipes） |
| 2. opencode v1 event 混乱 | **成立（代码级证据）** | 事件系统**四代并存**：`LegacyEvent` + `SessionV1`（显式标注 legacy）→ EventV2（Effect PubSub + SQLite 事件溯源）→ `event-v2-bridge.ts` 桥接 → `GlobalBus`（裸 EventEmitter，payload 类型 `any`）→ SSE/插件 hook。同一领域存在 V1/新版双份定义（Permission/PermissionV1、Question/QuestionV1）——迁移未完成的直接证据 |
| 3. opencode v2 有严重 bug | **成立** | v2 以 `opencode2` npm beta 发布；`2.0` 探索分支已停滞（最后提交 2026-04-13），实际重写发生在 dev 分支的 ~30 个新包中，插件 API 本身在换代未稳定（`packages/plugin/src/v2/`）。open 严重缺陷 13+ 个：#43591 TUI segfault（08-20）、**#43830 plan mode 中途重新启用后静默失效、agent 无限制执行变更（安全相关，08-21）**、#43594 并发子代理传输挂起、#44094 compaction 忽略模型配置、#42690 API key 轮换后仍用旧 key、#41828 API 缺口阻塞第三方客户端、#38528 LSP/formatter 未移植等 |
| 4. opencode-serve 提供事件有问题 | **成立** | #36739 上游 SSE 挂起时泄漏内存并卡死（**生产观测 RSS 18.2GB**）；#29204 server 模式每会话无限累加 Effect EventTarget 监听器（启动 ~30 秒即 MaxListenersExceededWarning）；#40812 ≥1.18.12 作为常驻 server 反复崩溃（用户 6 天重启 5 次）；#42299 空闲烧 20-45% CPU；#41066 孤儿进程 100% CPU 空转；#38266 stdio MCP 连接中途静默断开工具永久不可用 |
| 5. Node.js 内存管理效率低 | **部分成立（需精确表述）** | 高复现证据：#34574 AI SDK Effect 运行时 EventTarget 监听器从不清理（RSS 127MB→4.9GB/20 分钟）、#35107/#41026 内存无界增长、#42263 PDF 每轮重复 base64 编码 OOM。但根因是 **opencode 自身的 Bun/Effect 工程缺陷**，不是 Node.js 语言层面——换语言不必然解决、留在 Node 也不必然复现。引用时保留此限定 |

**其他结构性风险**：仓库已从 `sst/opencode` 迁至 `anomalyco/opencode`；5,275 个 open issues；**Discussions 已被禁用**（社区质询只能走 issues）；v1 线仍活跃（v1.18.21 @ 08-21）。

### 6.2 OMO 基本面

`code-yeongyu/oh-my-openagent`（前身 oh-my-opencode），68k★，npm `oh-my-opencode@5.0.0-beta.16`。三个发行版：Ultimate（OpenCode 插件）、Light（Codex CLI 插件 `omo-codex`）、Senpi（独立二进制）。官方规模自述：**11 agents、54+ 生命周期 hooks、5 个内置 MCP**。集成机制：opencode plugin，**硬钉版本** `@opencode-ai/plugin@1.18.19` + `@opencode-ai/sdk@1.18.19`。

**架构关键事实**：OMO 正在执行"多 harness 重构"（ROADMAP.md），已把 **19 个纯 TS Core 包**（`rules-engine`、`agents-md-core`、`boulder-state`、`team-core`、`delegate-core`、`model-core`、`prompts-core`、`skills-loader-core`、`tmux-core`、`omo-config-core` 等）与 harness 适配层（`omo-opencode`/`omo-codex`/`omo-senpi`）分离。**这是"哪些可移植"的最强实证——Core 层已经在 Codex 版上跑通了。**

### 6.3 OMO 组件可移植性清单

| 组件 | 实现机制 | 可移植性 |
|---|---|---|
| **11 个 agents**（sisyphus/oracle/librarian/explore/metis/momus/atlas/prometheus/hephaestus 等） | TS 工厂生成 `AgentConfig`（prompt + model + temperature + 工具白名单） | **部分**：prompt/参数/工具策略纯文本可移植；注册与子代理派生依赖 opencode agent 系统（丢失） |
| **任务类别**（quick/deep/ultrabrain/visual-engineering/artistry/writing 等） | 按 provider 分文件的类别→模型+prompt 追加表 | **部分**：类别表与 prompt 可移植；执行引擎 `delegate-task`（~100 文件：sync-session-creator/poller/lifecycle、background-continuation）深度依赖 opencode 会话 API，需重写 |
| **Skills**（SKILL.md + skills-loader-core + `skill` 工具） | 静态 markdown + 加载器 | **是**：markdown 与 harness 无关；五强候选全部原生支持同格式 |
| **54+ hooks**（todo-continuation、preemptive-compaction、model-fallback、rules-injector、ralph-loop、goal、unstable-agent-babysitter 等） | TS hook 处理器挂接 opencode plugin Hooks 接口 | **部分**：逻辑/意图可移植，但每个 hook 依赖 opencode 特定挂接点（chat.message、tool.execute.before/after、experimental.session.compacting 等）→ 新 harness 需等价扩展点，逐个重接 |
| **Team mode**（12 个 team_* 工具 + 文件邮箱 + tasklist + tmux 布局） | 核心原语已抽到 `team-core`（harness 无关）；会话派生走 opencode | **部分**：原语（文件+tmux）可移植；成员会话派生需重写 |
| **会话压缩增强**（preemptive-compaction + context-injector + todo-preserver + 降级监控） | hooks + SDK 调用 | **部分**：策略逻辑可移植；触发机制依赖 opencode compaction API |
| **内置 MCP**（ast-grep/git-bash/lsp-tools/grep_app/context7/codegraph） | stdio MCP server | **是**：MCP 是协议标准 |
| **配置/规则/遥测**（omo-config-core、rules-engine、agents-md-core、boulder-state、telemetry-core） | 纯 TS 包 | **是** |
| **TUI 特性**（tui-sidebar、task-toast） | opencode TUI 集成 | **否** |

**切换损失面总结**：54 hooks 的挂接重写 + delegate-task 会话编排 + team-mode 会话派生 + preemptive compaction 触发 + TUI。**可带走面总结**：全部 agent prompts、category 表、skills、MCP servers、19 个 Core 包。

---

## 7. 反思问题回答（2026-08-22 定稿）

### 7.1 自研框架是否是一个好的选择？

**结论：从零自研完整 harness ❌ 不推荐；但"在成熟基座上自建垂直层"✅ 现在成立且成本已大幅降低——这与第一轮结论（只演进不自研）相比是实质性修正。**

**支持自研垂直层的新证据（第二轮）**：
1. **harness 是质量变化的主因，不是模型**——[Don't Blame the LLM](https://arxiv.org/abs/2607.03691)（2026-07）首个隔离 harness 贡献的纵向受控研究：固定模型、只变 harness，35 个连续版本的质量波动可追溯到具体 PR；同一底座模型换 scaffold 在 SWE-bench Verified 上差 15pp+。这**部分推翻了第一轮的"harness 复杂度负半衰期"论据**（该论据基于 mini-swe-agent"100 行拿 65%"，而新证据表明模型越强、harness 差异的绝对收益越大）；
2. **不存在普适最优 harness，增益来自模型特异的自进化**——[HarnessBank](https://arxiv.org/abs/2607.13683)（2026-07）：7 个基准提升 5.1-15.4%，但跨模型实验证明最佳 harness 不可移植——垂直域 + 绑定自家模型组合（Qwen3-Max 为主）的定制 harness 有理论正当性；
3. **自研的三座大山已被生态铲平**：① SKILL.md 成为事实标准（五强全支持）→ OMO 91 个 skills 资产不锁定；② MCP 全标配 → 77 工具 server 即插即用；③ 成熟基座（PydanticAI/Strands）已提供循环/事件流/子代理/压缩 → "自研"的实际含义从"造 harness"降级为"造垂直层"（金融 grounding、领域不变量、结果闭环——正是 §3 的 GAP 清单）；
4. **opencode 侧的留存风险在上升**（§6.1）：v2 重写期插件 API 换代、OMO 硬钉 1.18.19、内存泄漏未解决、Discussions 禁用、连 OMO 自己都在做多 harness 重构（作者本人不认为 opencode 是唯一未来）。

**反对从零自研的证据仍然有效**：
1. 从零写循环/压缩/权限/沙箱的工程量巨大，且这些是商品能力——[Recursive Agent Harnesses](https://arxiv.org/abs/2606.13643) 证明 harness 增益来自**结构化分解设计**而非代码量；
2. 集成领域 harness 的前车之鉴（TradingAgents/ai-hedge-fund/RD-Agent/nofx）：维护全部 harness 债务，评测声明均仅作者报告，grounding/治理反而落后 Vibe-Trading。

**因此推荐路径**：**迁移到 Python harness 基座（首选 PydanticAI v2，备选 Strands；深度绑定 Qwen 路线则 AgentScope 2.0 升为备选第一）+ 平移 OMO 可移植资产（skills/prompts/category 表/Core 包）+ MCP server 原样保留**。需要重写的只有：54 hooks → 新基座扩展点的映射、delegate-task 编排、preemptive compaction 触发。而 Vibe-Trading 自己的 `agent/` 层（grounding 闸门/goal 协议/swarm/strategy_discovery/quantlib）本来就是领域 harness 内核，以 MCP 形态服务，**与基座选择无关、不受迁移影响**。

### 7.2 opencode + omo + mcp 是否有进一步空间增强为量化/财经定制 harness？

**结论：有，但空间在 MCP 服务端而非 harness 端，且停留在 opencode 上的风险随时间上升——增强值得做（因为与基座选择无关），但不应把 opencode 当作长期唯一依赖。**

1. **增强空间（与第一轮 P0-P3 路线图一致，全部在 MCP server 端，基座无关）**：渐进披露（77 工具 → 12-15 常驻动词 + `search_tools` 元工具 + 按会话激活——学术锚点：[How Many Tools](https://arxiv.org/abs/2605.24660) 证明平均呈现 7 个工具即接近 50 个的覆盖率、[Tool Attention](https://arxiv.org/abs/2604.21816) 实测懒加载每轮工具 token -95%）；输出压缩（数据类工具 outputSchema + 程序化工具调用）；编排状态收归（swarm 类型化磁盘产物替代 NL 注入——[DACS](https://arxiv.org/abs/2604.07911) 证明"摘要登记+按需聚焦"使 steering 准确率 90-98% vs 扁平基线 21-60%）；结果锚定反思闭环（最大研究 GAP）。这些做在任何 harness 下都受益；
2. **天花板与风险**：通用 harness 永远不会为金融领域自动守领域不变量（grounding 闸门只有服务端能守——这恰是留在薄 MCP 模式的理由），但 opencode 作为**客户端**的特定风险在累积：v2 重写期 API 换代（OMO 已被迫钉版本）、#34574/#36739 级内存泄漏对长驻研究 agent 是实打实的运维负担、5,275 open issues 且 Discussions 禁用；
3. **策略含义**：MCP 可移植红利意味着 opencode 应该被降级为"可互换客户端之一"（与 Claude Code/Codex/Cursor 并列），而不是架构的中心。服务端 P0-P3 增强 + 基座迁移 PoC 并行（§8 双轨）。

---

## 8. 最佳实现路径（第一轮综合 2026-08-21，第二轮校准 2026-08-22）

**总命题（第二轮校准后）**：不做从零自研的独立 harness，也不做被动薄 MCP server，更不死守单一客户端。演进为**领域 harness 内核**：通过 MCP 保持可移植性，把三件通用 harness 不做的事（渐进披露/输出压缩/编排状态）收归服务端，补上决定长期质量的"结果闭环"；同时以双轨推进——**轨道 A**（服务端 P0-P3 增强，与基座选择无关，立即启动）+ **轨道 B**（基座迁移 PoC：PydanticAI v2 为首选基座，按 §5.4 结论先用 Inspect AI/Harbor 思路建回归评测，固定底座模型对比，4-6 周量化数据决定是否切换）。

### 8.0 综合工程路径（最终推荐，2026-08-22 定稿）

**三个不变量**（任何路径下都成立，因此先做）：
1. Vibe-Trading agent 层的领域内核定位不变——grounding/治理/回测/quantlib 经 MCP 服务，与 harness 选型解耦；
2. MCP 可移植红利不变——77 工具 server 在 opencode/PydanticAI/Claude Code 下即插即用；
3. 服务端增强方向不变——渐进披露/输出压缩/编排状态/结果闭环，做在任何 harness 下都受益。

**四阶段路线**：

| 阶段 | 时间 | 内容 | 证据依据 |
|---|---|---|---|
| **阶段 1：评测先行** | 第 1-2 周 | 建回归评测地基：按 `HARNESS_EVOLUTION_BENCHMARKS.md` §D 五件套（tau²-bench + SWE-bench Verified + terminal-bench 2.0 + StockBench/BacktestBench + FinanceBench 150/FinEval），**固定 Qwen3-Max 底座**先测现有 opencode+OMO 基线。这是双轨的共同前置，也是 P1-5 的护城河本体 | [Don't Blame the LLM](https://arxiv.org/abs/2607.03691)：无版本级质量基线则回归无法归因；§5.4 结论 3：让换基座决策有量化依据而非体感 |
| **阶段 2：双轨并行** | 第 2-6 周 | **轨道 A（即时收益，基座无关）**：① 工具面重组——77 工具 → 12-15 常驻动词 + `search_tools` 元工具 + 按会话激活（swarm worker 白名单扩展到 MCP 表面）；② 决策账本反思闭环——DecisionRecord pending→resolved + 实现收益归因 + 按标的回注。**轨道 B（迁移 PoC）**：PydanticAI v2 + 进程内 FastMCP 直连 77 工具 + OMO skills/prompts/category 表 1:1 平移 + 三件套重写（54 hooks 映射 / delegate-task 编排 / preemptive compaction 触发），跑同一评测集 | 工具面：[How Many Tools](https://arxiv.org/abs/2605.24660)（平均呈现 7 工具 ≈ 50 的覆盖率）、[Tool Attention](https://arxiv.org/abs/2604.21816)（懒加载每轮工具 token -95%）；反思闭环：TradingAgents 延迟反思 + FinMMEval"无记忆重复错误" + PolyGnosis 2.0"反思必须硬 grounding"；选型：§5.3 PydanticAI 七需求全命中 |
| **阶段 3：决策门** | 第 6 周 | 用阶段 2 量化数据裁决：PydanticAI 评测 ≥ opencode+OMO 基线且内存/事件稳定性更优 → **完成切换**，opencode 降级为可互换客户端之一；不及预期 → 留 opencode 为主客户端、继续轨道 A，并复评 AgentScope 2.0（若深度绑定 Qwen/DashScope 路线） | [HarnessBank](https://arxiv.org/abs/2607.13683)：不存在普适最优 harness，必须绑定自家模型组合实测——这正是决策门存在的理由 |
| **阶段 4：护城河** | 第 6-12 周 | P1 全行业空白项：统计验证门（deflated Sharpe/purged CV 强制证据行 + CPCV/PBO + OpenPM 三件套：污染证书/成本敏感曲线/约束遵守报告）、确定性重放（(prompt-hash, model) 响应缓存 + golden trace 公开基准）、程序化工具调用（数据类工具 outputSchema + 类型化 stub）、swarm 类型化磁盘产物 | Alpha Illusion/KTD-Fin/OpenPM 2026 批判性转向：谁先把批判标准工程化，谁定义行业标准；Vibe-Trading 手握 grounding 闸门 + 治理账本 + quantlib 数学三张底牌 |

**为什么这条路径最优（综合两轮调研）**：
- **评测先行**把"换不换"从信仰之争变成数据裁决，同时评测基建本身是 P1 护城河的组成部分——没有一步是浪费的；
- **轨道 A 优先于轨道 B 启动**：工具面重组与反思闭环是全生态验证过的最高 ROI 项（一个守住准确率悬崖，一个补最大研究 GAP），且无论阶段 3 裁决如何都保留收益；
- **轨道 B 的风险被结构性压低**：SKILL.md 标准化 + MCP 标配 + OMO 19 个纯 TS Core 包的可移植实证（omo-codex），意味着迁移成本集中在三件套重写，而非资产清零；
- **决策门保留回撤权**：若 PydanticAI 实测不佳，损失仅限 PoC 成本，而阶段 1 评测基线与轨道 A 收益全部保留；
- **阶段 4 是真正的差异化**：前沿评测的批判性转向宣布"报告的 alpha 不是部署证据"，全行业无人完整交付统计验证门——这是 Vibe-Trading 定义标准的时间窗口。

### P0 —— 守住工具面悬崖
（证据：准确率悬崖、Copilot 40→13、Harness 130→11、OpenBB 按会话激活、MCP 官方 client best practices）

1. **工具面重组**：77 工具 → ~12-15 个常驻动词 + 注册表分发；实现 `search_tools` 元工具与 catalog→inspect→execute 三层发现（含 ttlMs/cacheScope 缓存提示与"追加工具数组而非重排"的 prompt-cache 纪律）；
2. **按会话激活**：借鉴 OpenBB `available_categories`→`activate_tools`，让 swarm worker 与外部 client 各取所需。

### P0 —— 补上最大研究 GAP：结果锚定的反思闭环
（证据：TradingAgents 延迟反思、FinMem 半衰期分层、FinCon 选择性传播、FinMMEval"无记忆重复错误"、Reflexion"反思需外部信号"）

3. **决策账本**：goal/evidence ledger 扩展 `DecisionRecord`（pending→resolved）；scheduled research 到期用现有数据工具解析实现收益，经 quantlib 归因后生成 2-4 句教训；按"同标的最近 5 条+跨标的 3 条"回注未来 session。关键纪律：反思的反馈信号**必须是回测/市场实现值**，禁止 LLM 自评（PolyGnosis 2.0 证明无约束反思诱发逻辑漂移）。

### P1 —— 评测护城河
（证据：Alpha Illusion 六检验、KTD-Fin、OpenPM、agent-backtest-lab、AI Agents That Matter）

4. **统计验证门**：把 quantlib 已有的 deflated Sharpe/purged CV 接成 strategy discovery 的强制证据行；新增 CPCV/PBO；每个"毕业"策略产出 OpenPM 式三件套——**污染证书、成本敏感曲线、约束遵守报告**；
5. **确定性重放**：swarm worker 增加 (prompt-hash, model) 键控的响应缓存（ai-hedge-fund PromptCache 模式），agent_eval 的 golden trace 升级为可公开复现的回归基准（成本+准确率联报）；
6. **污染控制模式**：评测运行支持 ticker/日期掩码（KTD-Fin），归因扩展到因子层。

### P1 —— 数据链瘦身
（证据：Anthropic code mode 150k→2k、LangAlpha PTC、opencode code-mode 已存在客户端）

7. **程序化工具调用支持**：数据类工具补 `outputSchema`，提供类型化 stub 供沙箱代码调用；`get_market_data→指标计算→backtest` 链中间结果不进上下文。

### P2 —— 编排深化
（证据：TradingAgents 电话效应、Anthropic 多 agent 文件系统模式、Smit et al. 辩论反驳）

8. **Swarm 类型化产物**：worker 写磁盘 artifact、下游读引用，替代 NL 注入；
9. **辩论消融**：investment_committee 等 preset 对 self-consistency 基线做成本对照。

### P3 —— 前瞻评测布局
（证据：FinMMEval、LLM-Trading-Lab、nofx 榜单）

10. **Forward-only 评测**：与同组织 AI-Trader 竞技场打通（**前提：先修复其 license 缺失**），或自建 paper-forward 评测榜；把 agent_eval 发布为公开基准。

**路径综合逻辑**：P0-1 来自 MCP 经济学实测；P0-3 来自交易 agent 论文唯一的可复现正面机制（TradingAgents 反思）+ 实盘评测的反面证据（FinMMEval）；P1 全部来自 2026 批判性转向（Alpha Illusion/KTD-Fin/OpenPM）——该转向宣布"报告的 alpha 不是部署证据"，而 Vibe-Trading 恰好手握 grounding 闸门+治理账本+quantlib 统计数学这三张底牌，是把批判标准**工程化**的最佳位置。

### 轨道 B：基座迁移 PoC 的评测协议（2026-08-22 新增）

按 `HARNESS_EVOLUTION_BENCHMARKS.md` §D 推荐组合执行，**固定底座模型（Qwen3-Max）**对比现有 opencode+OMO 与候选基座：

1. **通用侧**：tau²-bench（工具调用/多轮可靠性）+ SWE-bench Verified（harness 差异最敏感的标尺，15pp+ 区分度）+ terminal-bench 2.0（Harbor 开箱即用）；
2. **金融侧**：StockBench 或 BacktestBench（与业务同构的量化工作流）+ FinanceBench(150) + FinEval（知识地板）；
3. **方法论纪律**：成本+准确率联报（AI Agents That Matter 协议）；金融 QA 与交易决策两侧都测（Alpha Arena 证明两者不相关）；保留官方验证器并抽查轨迹防 exploit。

---

## 附录：诚实性声明

- 所有外部 alpha 声明均为作者自报，未经复现；
- 第一轮调研中 TradingAgents Discussion 段与 FinMem 精确结果表抓取被截断（架构事实已验证）；
- 第二轮（2026-08-22）：32 框架活跃度数据为当日 GitHub API 实测；opencode/OMO 审计定位到具体 commit（`e00890c6`/`13a00bf1`）；25 篇增量论文全部经 arXiv API 当日验证；[Tool Attention](https://arxiv.org/abs/2604.21816) 端到端收益为作者投影值（模拟基准）、[QuantAgents](https://arxiv.org/abs/2510.04643) ~300% 收益为作者自报，引用时均已显式标注；TradingAgents-CN 无 arXiv 论文，按灰色文献入索引；
- "Node.js 内存管理低效"的判定已修正为"opencode 的 Bun/Effect 运行时存在系统性监听器泄漏"（项目工程缺陷，非语言层面）。
