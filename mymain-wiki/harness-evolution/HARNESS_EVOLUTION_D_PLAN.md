# HARNESS_EVOLUTION · D 批领域子代理 — 工作计划 + 测试计划（预注册）

> 状态：**已执行完毕（2026-08-28）** —— 判据于首轮 judge 调用前冻结；
> v1 施测触发预注册修订轮（§5.7 规则 4，唯一一轮，已披露），v2 结果为
> 裁决依据。裁决文档：`mymain-wiki/harness-evolution/evals/tool_selection/artifacts/d_batch_verdict.md`。
> **结论：有条件通过——路由层可靠（R1 99.1% / R2 3.57% / R3 85%），
> W1/R4 功效不足未证非劣（亦未证有害）；L2 暴露两个生产前置条件
> （编排侧政策 + 全命名空间 deny）。D4 铺开与主循环收敛暂缓。**
> 依赖证据：ROADMAP §9.5（C 批回滚教训）、AUDIT §8.1（子代理草案）、
> B 批裁决（`b_batch_verdict.md`）、方法学缺口清单（`llm_judge_design.md`）。
> 本文结构与 C_PLAN 对齐：判据先于实验冻结，禁止事后改阈值。

---

## 1. 范围与上下文

### 1.1 问题

生产部署 = **opencode + vibe-trading MCP**（mymain 分支，`OpencodeAgent/`）。
B 批后 MCP 面 keyless 59 工具 + 90 技能（AGENT 面 90 工具），每规划轮披露税
~11.2k 描述 token（本次实测，§6 T 组）。PAPERS §F：工具选择准确率在 25-30 个
可见工具后退化。C 批已证明"藏起来按需召回"路线失败（端到端 −11.5pp ~ −33.6pp），
根因是**"何时该搜索"决策不可靠**（§9.3）。

D 批是与之并行的另一条路线（ROADMAP §9.5 教训 2）：**领域子代理**——每个子代理
固定小白名单（~11-13 工具），天然落在舒适区内，**无需模型做搜索决策**。主代理
只需做"是否委派 + 委派给谁"这一更粗粒度的决策。

### 1.2 本次范围（D1 + D2 试点，不含 D3/D4）

| 项 | 内容 |
|---|---|
| **D1 quant-agent** | D06+D07 域；工具白名单 11 + 技能 12（v1，覆盖审计修订后） |
| **D2 web-docs-agent** | D19 域；工具白名单 3 + 技能 2；边界仲裁规则最明确的最小闭环 |
| 宿主形态 | opencode 自定义 subagent（`agent.<name>` 配置：description = 路由信号，prompt = 行为层，permission = 白名单强制） |
| 评测 | 复用 E1 语料 + LLM-judge 基建（run_llm_judge.py 已支持 --post-corpus/--queries-file/--tag） |

**试点为纯增量设计**：主循环保留全部 59+90 面（与生产现状一致），子代理作为
可委派目标加入。D4 式"主循环收敛为编排层"的收益不在本批（依赖 D1/D2 验证结论）。

### 1.3 白名单覆盖审计（已执行，确定性）

`d_batch/build_d_corpora.py` 对 queries.yaml 全量 158 条做了"期望命中 ∈ 白名单"
审计：

| 子代理 | 面 | 描述 token | 覆盖 | 发现 |
|---|---|---|---|---|
| 全表面（B 后） | 59T+90S | 11,238 | — | — |
| quant-agent v0（§8.1 草案） | 11T+9S | — | **17/20** | **3 死角**：D06-007(alpha-zoo 技能)、D07-006(pine-script)、D07-007(vnpy-export) |
| **quant-agent v1（修订）** | 11T+12S | 1,366（−88%） | 20/20 | 补 3 技能，修订记录入定义文件 |
| web-docs-agent | 3T+2S | 243（−98%） | 6/6 | 无死角 |

**修订规则（预注册）**：覆盖审计发现的死角允许**一轮**白名单修订，修订逐条
记录于定义文件的 revision log。本轮已执行（v0→v1）。LLM-judge 阶段起白名单
冻结。

---

## 2. 设计裁决

### 2.1 子代理定义的生产形态

mymain 的 `render_config.py` 已证明机制：把治理 manifest 编译为 opencode
`agent.<name>.permission` 条目（`vibe-trading_*: deny` + 白名单逐个 allow）。
D 批子代理定义 = 同一 manifest 机制的扩展：

- **description 字段 = 路由信号**。opencode 主代理经 task 工具看到各子代理的
  description 决定委派——这对应 C 批失败点的移植面，是本批复测的核心风险。
- **prompt 字段 = 行为层**（角色、边界、交回规则、输出契约）。提示词工程
  调研输入见 §3。
- **permission = 白名单硬强制**（对齐 AUDIT §8.1"swarm preset tools: 硬白名单"
  语义，R5 已证实运行时交集强制）。

### 2.2 两级评测协议

C 批的失败教训是"单看检索质量、漏看端到端"。D 批把决策链拆成两级分别测量，
再合成端到端：

- **Level-W（代理内选择）**：判官只看子代理面（11T+12S / 3T+2S），对域内
  query 选 top-1。对照 = 同 query 在全表面（59T+90S）的准确率（B 批语料
  `corpus_b_post.yaml` 即全表面快照，两臂同模板同判官）。
- **Level-R（路由委派）**：判官看到主循环视角 = 全表面 + 2 张子代理卡片
  （name + description），决策 = {direct, delegate:quant-agent,
  delegate:web-docs-agent}。全量语料 + 扩展语料 + 边界集。
- **E2E 合成**：目标域最终工具正确率 = P(路由正确) × P(代理内选对)，与
  全表面直选基线做非劣对比（C 批 R1 的诚实对应物）。

### 2.3 判官与有效性控制（继承 B/C 批方法学修复）

- 判官面板：qwen3.8-max（主）+ kimi-k3（敏感），DashScope 同 key，temp 0；
  与 B/C 批冻结配置一致（judge_config.yaml 的 2 模型变体）。
- **主口径预指定 = strict top-1**，lenient 仅作敏感性（修复缺口③）。
- 非劣判定 = 边界 δ + 确切 95% CI 下界（修复缺口②，不用"无显著回归"）。
- 每模板每判官跑确定性探针测重测噪声地板（修复缺口④；routing 模板是新模板，
  必须重测）。
- 语料扩量解决功效不足（修复缺口①），MDE 在 §5 如实申报。
- 预算纪律不变：budget 预检 + golden trace + resume 不重复扣费。

---

## 3. 提示词工程调研（2026-08-28 双源调研完成）

> 目标：为两个试点子代理的 description（路由信号）与 prompt（行为层）
> 提供证据支持的设计原则。调研 = librarian（论文+OSS）+ explore（仓内先验）
> 两路并行，原始简报存档于会话；本节为蒸馏结论。

### 3.1 论文证据（对路由/委派设计的可执行主张）

| 来源 | 主张 | 对 D 批的含义 |
|---|---|---|
| **DecisionBench**（arXiv 2605.19099） | 路由保真@1 跨条件仅 7.5%-29.5%；**投递通道（预载 description vs 按需拉取）主导描述内容本身**；误委派代价 = 完美委派天花板高出现测 15-31pp；端到端质量指标掩盖路由错误 | opencode 把子代理 description 预载进 task 工具描述——通道已是强通道，措辞是残余杠杆；**评测必须直接测路由正确率，不能用任务完成质量代理**（本计划 R 组即此） |
| **EARS**（arXiv 2606.18668） | 子代理对越界/欠规约请求"过度回答并幻觉"；结构化弃权协议（暴露可操作失败态给编排者）使通过率 68.5%→78.9% | 子代理 prompt 必须有**结构化交回协议**（`OUT_OF_SCOPE: <原因>; SUGGESTED: <去向>`），不是简单拒绝 |
| **MAST**（arXiv 2503.136**57**，纠正：调研任务书误写 2503.13656，该号为量子物理论文） | 14 种失败模式三类：规格问题（角色/任务规格模糊）、代理间错位（**隐瞒部分/负面结果** FM-2.4）、验证缺失（提前终止/浅验证） | prompt 必须：角色范围一句话、**强制上报负面/部分结果**、给出**具体验证方法**（命令/条件，不是"请验证"） |
| **Anthropic multi-agent 工程post**（2025-06） | 委派时任务包须含 objective/output format/tool guidance/boundaries（否则重复劳动/留缺口）；工具描述重写 → 任务完成时间 −40%；子代理产物写文件系统、返回引用（避免 telephone 损耗）；编排者要有工作量伸缩规则 | 委派层（生产主代理侧）规则与 description 设计同步；子代理输出契约 = 文件路径 + 摘要，不贴大对象 |
| **Provenance Paradox**（arXiv 2603.08852/2603.18043） | 按自我声称质量路由**差于随机** | description 禁止自我吹捧词（"expert/best"），只写触发条件 |
| Claude Code 官方文档 + Anthropic blog（2026-04） | description 字段定义 = "**when** to delegate"（不是"是什么"）；"use proactively"措辞被 harness 显式尊重；description 是常驻 token（>15k 告警），细节放懒加载 prompt | description 短、以触发条件为主干；细节入 prompt |

### 3.2 OSS 实现约定（一手源码/文档核实）

**opencode（生产宿主，schema 经 https://opencode.ai/config.json 核实）**：
- `agent.<name>` 字段：`description`（路由信号）、`mode: subagent`、`prompt`
  （内联或 `{file:...}`）、`permission`、`steps`、`hidden`、`model`（缺省继承主代理）。
- **permission glob 语义**：key 支持任意工具名通配（schema `additionalProperties`
  确认 MCP 名合法）；**最后匹配规则胜出**——`"vibe-trading_*": "deny"` 在前、
  `"vibe-trading_backtest": "allow"` 在后 → 单工具放行。生产 render_config.py
  已用纯 deny 先例；通配+例外序列为本批新增用法，L2 冒烟负责实证。
- **路由面**：主代理经 task 工具描述看到子代理 description；`permission.task`
  deny 可从 task 描述中整体摘除子代理（硬路由开关）；`subagent_depth` 默认 1
  （子代理不能再派生子代理——结构性防递归）。
- task.txt（上游一手）：harness 已嘱咐调用方"给详细任务描述 + 明确返回什么 +
  告诉它如何验证自己的工作"——子代理 prompt 在被调侧镜像这三条。

**oh-my-openagent（omo，生产同款插件）**：代理元数据把 `keyTrigger` / `useWhen` /
**`avoidWhen`**（反触发）作为一等字段喂给编排者；description 写成"MUST BE USED
when …"式路由规则 + 用户真实问句形态触发（"Answers 'Where is X?'"）。

**仓内 swarm preset（30 个现成角色）**：prompt 解剖 = 一句话人设 → `## Task` →
`{upstream_context}` → 编号式 `## Required outputs` 契约 → 工具路由提示 →
**反捏造规则**（可复用原句："a drawdown probability stated without a simulation
behind it is a guess and must not be given"、"do not re-derive equity from memory"、
"report the section as unavailable rather than producing an illustrative number"）。
`tools:` 白名单在运行时为注册表级硬过滤（build_swarm_registry）。

### 3.3 蒸馏清单（D2 prompt 定稿的验收检查表）

**description（路由信号，≤ ~120 词，常驻 token）**：① 能力 + 触发条件（非职位名）；
② 用户真实措辞形态的域关键词（中英双语：backtest/回测、IC/IR/因子、PDF/网页/文档）；
③ 邻域反触发（quant-agent: NOT web 阅读/基本面/交易连接；web-docs-agent: NOT 本地
源码/行情数据）；④ 主动委派提示（"Delegate … to this agent"句式，opencode/Claude Code
均显式尊重）；⑤ 无质量吹嘘词。

**prompt（行为层，≤60 行，懒加载）**：① 角色+范围一句话；② 正触发任务型列举；
③ 负边界 + EARS 式结构化交回（`OUT_OF_SCOPE`/`NEED_INPUT` + SUGGESTED）；
④ 工具使用契约（哪个工具干哪件事 + 反捏造规则复用 preset 原句）；
⑤ 输出契约（最终消息自包含：调用方看不到工具输出；大产物写工作区返回路径；
负面/部分结果必须显式上报）；⑥ 验证方法具体化；⑦ 停止/努力预算；⑧ 自包含
（不假设父对话上下文）。

---

## 4. TASK 卡（实现规格）

### TASK-D0 · 白名单定义 + 覆盖审计 ✅（已完成）

- 产物：`d_batch/subagent_quant_agent.yaml`、`d_batch/subagent_web_docs_agent.yaml`、
  `d_batch/build_d_corpora.py`、`d_batch/coverage_report.json`、
  `d_batch/corpus_d_quant.yaml`（11T+12S）、`d_batch/corpus_d_webdocs.yaml`（3T+2S）。
- 验收：覆盖 20/20 + 6/6；白名单名全部存在于 B 后语料（脚本 fail-loud）。

### TASK-D1 · 语料扩量（功效修复）

- 新增 `d_batch/queries_d_expansion.yaml`：D06/D07 +20（→40）、D19 +10（→16）、
  边界集 +10（8 反例宿主直答 + 2 正例委派 web-docs）。
- 构建时与 queries.yaml 的既有条目合并（构建脚本过滤 domain + 追加，单一事实源）。
- 每条：id / query（中英混合自然措辞）/ expected / domain / negatives（仲裁项必填）。
- 验收：新条目期望命中 ∈ 冻结白名单（构建脚本断言）；边界集期望路由标注齐全。

### TASK-D2 · 子代理 prompt 定稿

- 依 §3 调研清单撰写 `d_batch/prompts/quant_agent.md`、`d_batch/prompts/web_docs_agent.md`。
- 验收（检查表）：五要素齐全；description 含域关键词 + 反触发；中英双语触发词覆盖；
  prompt ≤ 60 行（缓存纪律，RESEARCH §4.2 精神——追加式、无花活）。

### TASK-D3 · Level-R 路由协议模块

- 新模块 `d_batch/d_routing_protocol.py`：routing 模板（候选 = 2 子代理卡片 +
  "或直接回答"选项；输出 strict JSON `{route, if_delegate_then_tool?}`——
  预注册简化：路由级只判 route，不判最终工具，E2E 由 W×R 合成而非判官两步）。
- 模板 sha256 钉入 trace 头 + 本文 §5 + 测试（对齐 llm_judge_protocol 三处钉法）。
- 复用 run_llm_judge 的预算/trace/resume 基建（runner 加 `--protocol routing` 开关，
  或独立 `run_d_judge.py` 薄封装——实现时择一，倾向薄封装不动冻结模块）。

### TASK-D4 · LLM-judge 执行

- Level-W：quant 面 + 全面，各 40 query × 2 判官；webdocs 面 + 全面，16 × 2。
- Level-R：全量 158 + 扩展 30（D06/D07 +20、D19 +10）+ 边界 10 = 198 query × 2 判官。
- 探针：routing 模板 8 query × 3 repeat × 2 判官（重测噪声地板）。
- 冒烟先行：--limit 2 验证 trace/schema，再全量。

### TASK-D5 · L2 真实环境验证

- 本机 opencode + omo + 本仓库 vibe-trading MCP（B 批 §8.2 同款环境），临时
  opencode 配置注入 2 个子代理定义。
- 场景（4 个）：S1 量化 query 应委派 quant-agent 且在白名单内完成；
  S2 文档读取 query 委派 web-docs-agent；S3 边界 query（读本地源码文件）
  不委派、走宿主 read；S4 非域 query（基本面研究）不委派给两试点。
- 记录委派正确性、子代理内工具调用轨迹、是否出现"要白名单外工具"的死角事件。

### TASK-D6 · 裁决文档

- `artifacts/d_batch_verdict.md`：判据对照表 + 黄金 trace 索引 + 失败分解 +
  用户裁决节。同步 ROADMAP §10（新增节）记录结论。

---

## 5. 预注册判据（实验前冻结）

### 5.1 功效申报（MDE 诚实声明，修复缺口①）

- Level-W quant：n=40×2 判官 = 80 配对观测，基线 ~0.90 时不一致对期望 ~14，
  95% CI 半宽 ≈ ±9pp → **MDE ≈ 10pp，δ 取 −10pp**（试点口径，如实标注；
  5pp 证明需 n≈290 配对，超出试点预算）。
- Level-W webdocs：n=16×2 = 32，CI 半宽 ≈ ±13pp → 仅作描述报告，**不设
  非劣门槛**（功效不足，诚实降级）；其域内正确性主要由 L2 实测承担。
- Level-R 路由召回：目标域 query n=(40+16+2 边界正例)×2 判官=116，单比例
  95% CI 半宽 ≈ ±5.2pp（p≈0.9）→ 可支撑 ≥0.85 召回门槛。
- Level-R 误委派：非目标域 n=(198−58)×2=280，可检出 ≥3pp 的误委派率。

### 5.2 确定性判据（C 组 — 结构断言）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| C1 | 白名单覆盖：目标域全部 query 期望命中 ∈ 白名单 | 100%（已达成 26/26） | 主 |
| C2 | 白名单合法性：引用名全部存在于 B 后语料 | 100%（构建脚本 fail-loud） | 主 |
| C3 | 子代理面规模：可见**工具**数落在 PAPERS §F 舒适区（技能为一行懒加载目录，与 C2 披露层级同口径，单列报告） | 工具 ≤ 15（实测 quant 11T、webdocs 3T；含技能合计 quant 23 / webdocs 5） | 主 |
| C4 | 路由语料期望标注：208 条全部有 route 标签 | 100% | 主 |

### 5.3 披露税判据（T 组 — 确定性测量）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| T1 | 子代理内决策面 token 降幅 vs 全表面（描述块口径） | ≥ 80%（实测 quant −88%、webdocs −98%，已达） | 报告项（结构必然） |
| T2 | 委派场景端到端 token：主循环（全面−域工具委派后仍全量在案）+ 子代理面 | 报告实测值；主张 = **收益发生在子代理上下文内决策**，主循环披露税不变（试点增量性质，诚实声明） | 报告项 |

> T 组说明：D1/D2 试点为纯增量，主循环披露税不降；token 收益的严格测量
> 是 D4（主循环收敛）的判据。本批只断言子代理内决策面达标（T1）并报告
> 委派场景的构成（T2）——不夸大收益口径。

### 5.4 Level-W 判据（W 组 — 代理内选择，LLM-judge）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| W1 | quant 面 strict top-1 非劣 vs 全表面（配对，池化 2 判官） | 确切 95% CI 下界 > **−10pp** | 主 |
| W2 | quant 面点估计方向 | 报告；预期 ≥ 0（小面舒适区假设） | 辅 |
| W3 | webdocs 面 strict top-1 | 描述报告（功效不足，§5.1） | 辅 |
| W4 | lenient 敏感性 | 不得结构性翻转 W1 | 辅 |

### 5.5 Level-R 判据（R 组 — 路由委派，LLM-judge，**本批放行门槛**）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| R1 | 目标域路由召回：D06/D07/D19 query 被判官委派给正确子代理的比例（池化 strict） | **≥ 0.85** | **主（放行门槛）** |
| R2 | 误委派率：非目标域 query 被委派给任一子代理的比例 | **≤ 5%** | **主（放行门槛）** |
| R3 | 边界集仲裁正确率（10 条：8 宿主直答 + 2 委派） | ≥ 8/10 | 主 |
| R4 | E2E 合成非劣：目标域 P(路由正确)×P(代理内选对) vs 全表面直选基线 | 95% CI 下界 > −10pp | 主 |
| R5 | 分模型一致性：qwen/kimi 各自的 R1/R2 | 报告项，无放行权 | 辅 |
| R6 | 噪声带规则：R1/R2 的 |\Δ 解释| ≤ 重测带宽时记不可解释 | 探针实测后填带宽 | 主（解释规则） |
| R7 | 失败分解（报告项）：欠委派（域内走 direct）vs 过度委派 vs 代理间错配 | 描述 | 辅 |

### 5.6 L2 判据（真实环境）

| # | 判据 | 阈值 |
|---|---|---|
| L2-1 | S1/S2 委派正确 + 子代理白名单内完成，无幻觉工具调用 | 4 场景全过 |
| L2-2 | S3 边界：宿主读取类 query 不委派 | 通过 |
| L2-3 | 死角事件（子代理请求白名单外工具） | 0 起；发生则记录并回灌白名单评审 |

### 5.7 裁决树（预注册）

1. C 组任一 FAIL → 修定义/语料，不进入 LLM 阶段（结构性问题，无统计含义）。
2. R1 AND R2 AND R3 AND R4 全过 → **试点验证通过**，D4 铺开可议；
3. R1 或 R4 FAIL → 委派决策不可靠（C 批失败模式复现），试点标记失败，
   子代理路线整体降级（同 C 批处置：定义留存、不上生产、教训入 ROADMAP）；
4. R2 FAIL（误委派）但 R1 过 → description 反触发不足，允许**一轮**
   description 修订（修订记录入 §3.2），复测 R 组；复测仍 FAIL → 按 3 处置；
5. W1 FAIL 但 R 组过 → 白名单构成问题（而非路由问题），回 TASK-D0 评审
   白名单构成，不改 description；
6. 噪声带宽内 → 诚实记无效应，不过度解读。

---

## 6. 风险登记

| 风险 | 缓解 |
|---|---|
| C 批失败模式复现（委派=又一个不可靠决策） | R1/R2 即为此设；但子代理决策粒度（3 选项）远粗于 C 批（先搜后选两步），且 description 路由是 opencode 原生机制，非自建检索 |
| 语料扩量引入标注错误 | 构建脚本断言期望命中 ∈ 冻结白名单；边界集双人读规则自查（本次由 §8.2 输入 1 仲裁规则直接派生） |
| routing 模板新引入格式伪影 | 模板三处钉 hash；探针测噪声地板；invalid 响应计 miss 并单列（继承 runner 行为） |
| webdocs W 组功效不足 | §5.1 已降级为描述报告，不设门槛——诚实优于假精确 |
| L2 环境不可复现（本机 opencode 版本漂移） | 记录 opencode --version + 配置快照入 trace；失败不阻断 L3 裁决（L2 为补充证据层） |
| 生产集成面（render_config.py 扩展）与本批验证脱节 | 子代理定义文件即生产可消费格式（manifest 化）；mymain 落地为验证通过后的独立步骤，不在本批 |

---

## 7. 执行阶段

| 阶段 | 内容 | 放行 |
|---|---|---|
| Phase 0 | TASK-D0 白名单 + 覆盖审计 | ✅ 已完成（§1.3） |
| Phase 1 | §3 调研回填 + TASK-D1 语料 + TASK-D2 prompt 定稿 | C 组判据全过 |
| Phase 2 | TASK-D3 协议模块 + 冒烟（--limit 2） | trace/schema 正常 |
| Phase 3 | TASK-D4 全量 LLM-judge + 探针 | W/R 组出分 |
| Phase 4 | TASK-D5 L2 实测 | L2 判据 |
| Phase 5 | TASK-D6 裁决文档 + ROADMAP §10 更新 | 用户裁决 |

**判据冻结点**：本文件 §5 在 Phase 3 首次 LLM-judge 调用前不得再改。
