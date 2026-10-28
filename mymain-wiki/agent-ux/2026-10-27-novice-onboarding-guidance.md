---
title: 小白用户的意图引导：方案空间、证据与代价
description: 面向"首次上手、问不出好问题、没有量化背景"的用户，评估 opencode 侧可用的引导机制（AGENTS.md / Skill / Subagent / question 工具 / 插件 hook / UI），结合本分支已跑过的 harness 实验与外部产品/论文证据，给出分阶段方案与代价。
type: research
status: active
created: 2026-10-27
updated: 2026-10-27
tags: [onboarding, intake, clarification, agent-ux, opencode, harness, product]
related: [../features/f7-opencode-agent.md, ../harness-evolution/README.md, ../../OpencodeAgent/AGENTS.md]
---
> 已立项：`.omo/plans/novice-onboarding-gprime.md` + ACS 姊妹计划（2026-10-27）。

# 小白用户的意图引导：方案空间、证据与代价

> 结论先行：**不要做"专用 intake 子代理"。** 本分支已经测试过 12 领域子代理层并整体回退；更硬的事实是，opencode 1.19.3 的 subagent 默认**没有 `question` 权限**，结构上就不能向用户提问。
>
> 当前性价比最高的组合是：**把 89 个工具折叠成 5–8 个"具名产物"（用户看得见）+ 用原生 `question` 工具做结构化消歧（模型问得出）+ 用按需 `intake` Skill 承载访谈剧本（流程稳定）+ 用能力卡/示例 chips 解决"不知道你能干什么"（UI 层）**。AGENTS.md 只做路由，不再堆规则。

---

## 0. 适用范围与证据等级

本文回答三个问题：

1. 用户问"今天买什么"时，怎样把模糊意图收敛成可执行参数；
2. 用户不知道 89 个 VT 工具 / 91 个金融 Skill / 30 个 swarm 预设存在时，怎样让他发现"原来还能这样"；
3. 用户不懂因子、量化、超参调优时，怎样不靠术语也能得到有用结果。

证据标记如下，正文用标记区分：

| 标记 | 含义 |
|---|---|
| **[实测]** | 本机 opencode 1.19.3 隔离配置下实际运行得到 |
| **[仓库实验]** | 本分支 `mymain-wiki/harness-evolution/` 的预注册 A/B/C/D 结果 |
| **[官方文档]** | opencode / Claude Code / 各产品官方文档或源码 |
| **[论文]** | arXiv / ACL / CHI 等一手论文 |
| **[产品文档]** | 产品官网、帮助中心、README |
| **[估算]** | 工程量估算，不是任何来源的公开数据 |

---

## 1. 问题不是"模型不够聪明"，是三个缺口叠加

| 缺口 | 用户表现 | 现有机制 | 为什么不够 |
|---|---|---|---|
| **意图缺口** | "今天买什么？"没有市场、期限、风险、仓位 | AGENTS.md 四类分流 + Least-to-Most 漏斗 + 1 轮 ≤3 问 | 触发依赖模型自己判断"这是开放型"；提问只能是自然语言文本，选项/后果无法强制；"绝不问第二轮"与外部实践冲突（见 §5.3） |
| **发现缺口** | 不知道有 swarm、有因子库、有 Shadow Account | 能力索引表 + `list_skills` / `list_swarm_presets` | 索引是**工具名**不是**产物名**；`load_skill(name)` 要求先知道名字；用户侧没有任何目录 |
| **概念缺口** | 不懂"因子""IC/IR""超参调优" | 91 个金融 Skill 的正文 | Skill 正文是写给会的人看的；没有"这个产物能回答什么问题"的一层翻译 |

规模数据（**[仓库实验]**）：

- MCP 面合计 **194 个可路由能力单元（74 + 90 + 30）**（`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_CAPABILITY_AUDIT.md:30`）。
- 工具选择准确率在 **25–30 个可见工具后退化、~100 个崩塌**；74 个 MCP 工具单独已逼近崩塌阈值，叠加 90 条技能描述后是 **164 条竞争描述**（同文件 `:32`）。
- 常驻上下文成本：`OpencodeAgent/AGENTS.md` 389 行 / 32,366 字节，约 **9.2k tokens**（本次实测估算）；`research-scenarios` 已把场景步骤移出常驻，正文约 **3.6k tokens**（本次估算），按需加载。

**结论**：继续往 AGENTS.md 加规则，是在一个已经 9.2k tokens 的常驻文件上继续加税，而且解决不了"用户看不见"的问题。

---

## 2. 本分支已经试过什么：四批实验的硬结论

来源：`mymain-wiki/harness-evolution/README.md:24-35`、`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:87-142`、`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:358-367`、`:400-401`。

| 批次 | 假设 | 关键数字 | 裁决 |
|---|---|---|---|
| **A 描述治理** | 改工具/技能描述措辞能改善路由 | 池化 McNemar `p=0.885`；基线 `0.88–0.94` 已到顶；A7 全集 `+3.48pp p=0.027`、靶点 `p=0.629`；A8 显著回归 `p=0.012` | 路由中性；A7/A8 回滚，标记 **DO-NOT-RE-TEST**（`mymain-wiki/harness-evolution/README.md:84`） |
| **B 暴露面工程** | 裁掉不可用工具不损路由 | MCP keyless `74→59`，`−5,100 tok/轮`（`−17.9%`）；C1 `Δ=+1.05pp`，CI `[−2.03,+3.75]`；幻觉调用 0 | **成立**；暂缓上游 |
| **C 路由层** | 懒加载砍披露税且路由不降 | 检索本身达标：`recall@7=0.937`、披露税 `−79%`；但端到端 4 配置全部更差，`Δ −11.5pp ~ −33.6pp` | **全部回滚，思路标记失败** |
| **D 领域子代理** | 固定小白名单落在舒适区，无需搜索决策 | `R1 99.1%`、`R2 3.57%`、`R3 85%`；D2-1 `quant +6.67pp`、`webdocs +33.44pp`；决策读入 `−86%`、延迟中位 `−33%` | 当时生产落地 12 子代理，**2026-09-27 整体回退** |

### 2.1 D 批为什么不能当作 intake 的证据

- `R1` 是**路由召回**，不是"问出了更好的问题"；`R2` 是误委派率（`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:94`）。
- 同文件 `:122` 明确："**代理内选择未见改进（天花板效应）**"；价值主张是 token 节省 + 上下文隔离 + 扩容安全边际。
- `W1/R4` 未证得非劣：`Δ=−3.75/−5.0pp`，CI 跨 0，功效不足，**未证有害**（同文件 `:94`）。
- token/延迟收益：`10,974 → 1,548 tokens`（`−86%`）、`13.8s → 9.3s`（`−33%`）、`p90 46s → 29s`；但"轻任务净亏 5–10 秒，重任务（8+ 轮工具调用）净赚 40–60 秒"（`:108-111`）。

### 2.2 同一条路线第二次失败：specialist-arch iter3

- 终局：**"2 PASS / 3 FAIL，修复未达标，放弃 specialist 方案"**（`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:358`）。
- `G1'` 委派率 `0.7917 FAIL`（<0.90）；`G2'` token `8.84M ≤ baseline PASS`；`G3'` rubric `Δ −0.0625 FAIL`（<−0.05）；`G4'` 控制纪律 PASS；`G6'` 去重超时 `23>19 FAIL`（目标 ≤8）。
- 决定性对照：baseline 超时 `0/102`，specialist `23/102 (22.5%)`；状态矩阵 **单向 ok→timeout 23、timeout→ok 0**；LLM 调用 `442→1156`（**2.6x**）；双 ok 对 wall 中位 `25s→85s`（**3.4x**）；token `21.6M→15.4M`（仅 **1.4x** 省）；**非 hard 任务 0/25 更快**（`:360-361`）。
- 教训：**"机制可移植，经济性不可移植"**；后来者必须把 child 模型速度与派发并行度当承重项（`:363`）。

### 2.3 2026-09-27 的最终裁决

> "12 领域子代理层与 nano-search-mcp **经验证不够有效已回退**"（`mymain-wiki/branch/MYMAIN_DIVERGENCE.md:18`）。
>
> "opencode 侧 D 批生产 regime（并行+快模型）虽曾达标，但**综合验证结论为不够有效**，回退到主循环直连的简单形态。"（`:400`）

保留项：**OMO 内建子代理（explore/librarian/build/oracle）不属回退范围**（`:400`）。

nano-search 回退：整目录删除（含 193 测试）；通用检索由 `web_search + read_url` 承接；**已知缺口 R2**：年报/公告全文、监管处罚、机构调研、gov.cn 行业政策（`:401`、`:194`）。注意：**没有量化 A/B 指标**，只有"经验证不够有效"和"193 测试删除"。

### 2.4 三条可直接复用的实验教训

1. **描述措辞不是杠杆**（A 批）——不要靠改 prompt 文案解决路由。
2. **"裁掉没用的"无损，"藏起来让模型找"有损**（B 批 vs C 批）——C 批的 tiered 配置里，最贴近设计意图的 config 4 从 `0.962` 掉到 `0.755`（`Δ −0.206`，search rate `12–19%`，FAIL）。诊断句："when skills are visible the judge settles for a visible candidate and under-searches"；"when skills are hidden it over-searches but still mis-selects"（`mymain-wiki/harness-evolution/evals/tool_selection/artifacts/c_batch_verdict.md:88-89`）。
3. **子代理路线成立但非银弹**（D 批）——委派需要编排侧显式政策激活，且收益是 token/隔离，不是判断质量。

---

## 3. 外部证据：别人怎么把"不知道问什么"变成"能开始"

### 3.1 消费级 fintech：问卷不是重点，**具名产物**才是

| 产品 | 问几题 | 输出 | 关键机制 |
|---|---|---|---|
| **Betterment** | **0 道风险题** | 每个 goal 一个配置 | 风险由 goal + horizon 推导；无目标时用年龄 + 默认 65 岁生日；偏离推荐打标签 `"too aggressive"` / `"very conservative"`，并给一年期最好/最差情景 |
| **Schwab** | **7 题**（2 期限 + 5 风险） | 5 个方案（A–E） | Time Horizon Score × Risk Tolerance Score 查表；Q7 直接给 5 个方案的**年均 / 最好一年 / 最差一年**数值 |
| **Vanguard** | **11 题** | **9 档**股/债/短储配置 | 每题下方折叠 **"Why am I being asked this question?"** 与 **"What does my answer mean?"**，后者给方向性后果 |
| **Wealthfront** | **10 题**（4 客观 + 6 主观）⚠️ 2012 官方博客口径 | **100 个桶**（0.0–10.0，步长 0.1） | 回答一致性惩罚；两个分数取更保守者加权；强制每年重测 |

Schwab Q7 的后果表（**[产品文档]**，可直接做 UI 参考）：

| Plan | 年均 | 最好一年 | 最差一年 |
|---|---|---|---|
| A | 2.6% | 10.8% | −5.1% |
| B | 4.1% | 19.2% | −10.6% |
| C | 5.6% | 27.6% | −16.4% |
| D | 6.1% | 36.0% | −21.7% |
| E | 7.2% | 42.5% | −25.8% |

> 注：Robinhood 只证实了 `Basic options strategies (Level 2)` / `Advanced options strategies (Level 3)` 两个具名档位；其问卷题数、题目、是否产出具名 profile **UNVERIFIED**（无公开一手文档）。

**三个可迁移的观察**：

1. Schwab / Wealthfront / Vanguard 三家有可核实的问卷；Robinhood 只证实了档位命名；Betterment 主动取消问卷。但**没有一家把"评分后果"写在每个选项旁边**——后果只在题目层解释（Vanguard）或产物层预测（Betterment / Schwab Q7）。
2. 全部输出**具名档位**（9 档 / 100 桶 / 5 方案 / risk level），用户能复述、能分享、能下次直接说"我还是 7.3"。
3. Betterment 证明"不问"也可以是正确答案：**用默认值 + 后果预览 + 偏离标签**替代问卷。

### 3.2 专业终端：先给一张能用的屏，再让用户改

- **TradingView**：入口是 `Apply a popular screen`（预置统计），不是空表单；`Open screen` 可加载保存的模板或"popular preset screens tailored to related asset groups"；`Recently used` 只留 5 个。自定义是 `Add a filter → Add a filtering condition → Edit filter → Reset/Remove`。
- **Koyfin**：5 步建屏（命名 → 交易区域 → universe → criteria → create），100K+ 标的、5,900+ 筛选条件，但**不平铺**，按 criterion 展开。
- **Bloomberg**：终端本质是 function code / mnemonics；NL→BQL 只在其自家论文中被列为用例，**当前终端对普通用户是否开放此入口 UNVERIFIED**。
- **Finviz**：UNVERIFIED（站点 403），不作为设计依据。

### 3.3 策略构建器：**受限 DSL + 可编辑产物**

- **Composer**：`Trade with AI` 三扇门——`Browse top performers`（2000+ 社区策略）、`Find new opportunities`、`Compose your own`。底层是受限积木 DSL：`Assets / Weights / Conditionals / Filters / Groups`，有 guardrails 阻止非法组合；"Every AI-curated strategy is fully editable."；产品立场是 **"AI's Robin, you're still Batman."**
- **QuantConnect**：Learning Center 课程 → lesson → task；"you need to complete **30% of the Bootcamp lessons to post in the community forum**"——用社区权限做课程关卡。
- **TradingAgents**：固定角色流水线（Analyst → Researcher bull/bear → Trader → Risk → PM），CLI 只问 4 类（ticker、date、provider、research depth），**上一次答案作为默认值**。
- **ai-hedge-fund v2**：从 agent 名单进化成**基金参数面**（strategies / capital / cadence / tickers）；mandate 不绑定 tickers，回测时再选 universe。
- **FinRobot**：**动词即菜单**——`finrobot research AAPL` / `dcf MSFT` / `comps NVDA --peers ...` / `ic-memo TSLA` / `ask AAPL "..."`；DCF 表标为 code-computed，叙述才是 LLM 写的。
- **FinGPT**：4 字段表单（ticker、预测起点、回看周数、是否加最新财务），无追问。

**小结**：调研的 5 个开源项目里 **4 个完全不做意图澄清**，输入就是 ticker / 4 字段表单；只有 **OpenBB** 做了能力发现层（`/` + Skills + Prompt Library）。这个领域没有现成答案可抄，但有三个可组合零件：**FinRobot 的动词菜单、OpenBB 的 `/`+Skills、ai-hedge-fund 的 fund 参数面**。

### 3.4 多工具产品：把"开放式提问"换成"有限选项 + 一次一个"

- **Claude Code**：`/` 菜单列出 built-in commands 与 skills（每项带 description）；Skill 正文"loads only when it's used"；`AskUserQuestion` 是多选题，`options` 为 **2–4 个**、每个 `label + description`，`header` **≤12 字符**，有 `Other` 自由文本与 notes 字段；`/doctor` 反向审计 "unused skills, MCP servers, and plugins compared with their context cost"。
- **OpenBB**：`/` 即出 "the full list of available Skills and MCP tools"，也可 `/skill:skill-name` 强制指定；默认自带 `/openbb-html-report`；Copilot "automatically tries to identify a relevant skill"；Prompt Library 保存"复杂 prompt + 绑定 widget"。
- **ChatGPT GPT Store**：分类 + 排行榜 + 每周精选；用户看到的是"一个专家"，不是工具清单。
- **Salesforce Agentforce**：Atlas 用 **subagent classification** 把输入映射到一个 intent/job-to-be-done，再在 ReAct 循环里"ask clarifying questions or confirmations"；原文说这是为了 "scale to thousands of actions"。
- **Rasa Collect**：一个 slot + 一个提问（`utter_ask_{slot_name}`）+ 可选按钮（title + payload）+ 校验响应；`Prevent digressions`、`Ask before filling`、`Persist slot value`；slot 映射支持 `from LLM` / `from text` / `from entity` / `from intent` / `from custom action`。
- **Dialogflow / Amazon Lex**：同一机制（intent → 必填参数 → 逐轮 slot elicitation），但一手文档本轮被反爬拦截，**UNVERIFIED**；用 Rasa 作为可核实替代。

**厂商一手指导（与上面机制互相印证）**

- **OpenAI《A Practical Guide to Building Agents》**：把"信息不完整时怎么办"写成显式动作——"a step might instruct the agent to ask the user for their order number"，并要求在 edge cases 里写明 "how to proceed when a user provides incomplete information"；同时给出失败升级规则："fails to understand customer intent after multiple attempts, escalate to human intervention"。关于工具数量，原文是："The issue isn't solely the number of tools, but their similarity or overlap. Some implementations successfully manage more than 15 well-defined, distinct tools while others struggle with fewer than 10 overlapping tools."（2026-01-15）
- **Anthropic Agent Skills**：启动时把每个已安装 skill 的 **name + description** 预加载进 system prompt，正文只在需要时加载——"At startup, the agent pre-loads the name and description of every installed skill into its system prompt. This metadata is the first level of progressive disclosure, letting the agent know what skills are available without loading their full contents."；"Progressive disclosure is the core design principle."（2025-10-16）

### 3.5 设计规范：两层是硬上限

- **NN/g（2006，仍是基准）**："designs that go beyond **2 disclosure levels** typically have low usability because users often get lost when moving between the levels." 若真有 3+ 层，"consider simplifying your design"；实在不能简化，"chunk your advanced features into groups that make sense"。
- **Google PAIR**：`Onboard in stages`；"**Only introduce new features when needed**"；把"onboarding screen three of 10"列为反面案例；给一个低风险、可撤销的首个动作。可直接套用的文案模板：
  > "This is *{ your product or feature }*, and it'll help you by *{ core benefits }*. Right now, it's not able to *{ primary limitations of AI }*. Over time, it'll change to become more relevant to you. You can help it get better by *{ user actions to teach the system }*."
- **Apple HIG**："For open-ended features like a search bar or generation prompt, consider offering **curated suggestions** that make it easy to get started."；"offer **diverse, predefined example inputs** that hint at what's possible"；必须有非 AI 回退。
- **Microsoft HAX**：**G1** "Make clear what the system can do."；**G2** "Make clear how well the system can do what it can do."；**G10** "Scope services when in doubt."（不确定时做消歧或优雅降级）；**G16** "Convey the consequences of user actions."

**综合**：没有任何被调研的产品让新手"描述自己想干什么"然后从零生成；它们先给**一个已经能用的产物**（preset screen / Discover symphony / 分类 GPT / 一条 CLI 动词），再允许改。**收敛的单位不是工具，是产物。**

### 3.6 opencode 原语对照（决定"用 AGENTS.md 还是 subagent"）

| 原语 | 强制力 | 空闲 token 成本 | 能向用户提问？ | 关键限制 |
|---|---|---|---|---|
| **AGENTS.md** | 软（模型可读可不读） | 全文 × 每一步（无截断、无上限） | ❌ | 不支持 `@file` 引用；全局 + 项目是拼接；同名文件在祖先目录的命中会全部收进来；子目录 AGENTS.md 只在读该目录文件时懒加载 |
| **Skill** | 描述常驻 = 软路由；正文按需 | 仅 name + description + location；正文 0 | ❌（只能"指示模型去调用 question"） | `load_skill(name)` 要求先知道名字；`allowed-tools` **不支持**；没有数量上限/裁剪 |
| **自定义命令** | 用户触发的确定性注入 | 0（不在 prompt 里） | ❌ | 展开为**一条**用户消息；`subtask` 仍是单次投递；不能强制多轮 |
| **Subagent** | 硬隔离（模型/工具/权限） | 只有 description 进入父级 `task` 工具 | **默认❌**（1.19.3 实测 `general` 为 deny、`question:false`） | 只能把 `<task_result>` 回传父级；`subagent_depth` 默认 1，嵌套禁用 |
| **插件 hook** | 硬（可改参数/抛错/改工具定义） | 0（注入时另计） | ❌（无提问 API） | 与 OMO 共用 hook 链、串行执行、顺序敏感；`permission.ask` 只是审批 |
| **permission ask** | 硬门控 | 0 | ◐ 只是 once/always/reject 审批，不是选择题 | 不能替代 `question` |
| **`question` 工具** | 中高（结构化、答案可消费） | 0 | ✅ | 默认 deny，primary 已 allow；自定义 agent 需显式 allow；`header` ≤30 字符；`serve` 下需确认 `flags.client` / `OPENCODE_ENABLE_QUESTION_TOOL` |

**三条直接推论**：

1. **"基于 AGENTS.md 实现"只适合做路由，不适合做流程。** 它是软提示、常驻、无强制力；把访谈剧本写进去只会增加 9.2k tokens 的税。
2. **"基于 subagent 实现"在当前架构上不成立。** 默认不能提问，且已两次失败（D 批回退 + iter3 硬失败）。
3. **`question` 工具是唯一能"结构化地问"的原语。** 它必须由 primary agent 调用；UI 只需渲染事件与回传答案。

---

## 4. 方案空间：7 个选项

> 说明：`question` 工具是 opencode 1.19.3 的原生能力，本轮之前的设计默认"提问=自然语言文本"，这是最大的认知差。它改变了选项 D 的可行性。

### 选项 A：继续加强 AGENTS.md 规则（现状加强）

**机制**：在现有 389 行 AGENTS.md 里增加更多分流规则、示例、话术。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 意图（部分） |
| 强制力 | **软**。模型可以读、可以不读；没有硬门控 |
| 用户可见性 | 无 |
| 代价 | 0.5–2 人日；每加 1k tokens 常驻成本 × 每轮 |
| 主要风险 | 规则膨胀、指令冲突、注意力稀释；A 批已证明"描述措辞不是杠杆" |
| 证据 | 现有四类分流 + 1 轮 ≤3 问**已存在**（`mymain-wiki/features/f7-opencode-agent.md:18`；`OpencodeAgent/skills/research-scenarios/SKILL.md:64-70`），但**没有任何 efficacy 实验** |
| 结论 | **不作为主方案**；只保留一个 ≤40 行的"产物路由器"，其余移入按需 Skill |

### 选项 B：显式路由的 `intake` Skill（按需加载）

**机制**：新建 `OpencodeAgent/skills/intake/SKILL.md`；AGENTS.md 只写一句"开放型/待澄清型请求 → 先加载 `intake`"；Skill 正文承载 6 个槽位、3 个问题、选项与后果、逃生门、参数编译规则。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 意图 + 概念（通过选项文案翻译术语） |
| 强制力 | 中。加载是显式的，但"是否加载"仍由模型判断 |
| 用户可见性 | 间接（问题文本） |
| 代价 | 3–7 人日（写 Skill + AGENTS.md 路由 + 10–20 条脚本对话测试）；常驻成本≈0（只有 description） |
| 主要风险 | **C 批的失败模式**：不要让模型"自己决定要不要 search 隐藏的 skill"；必须显式路由（像 `research-scenarios` 的"识别场景后先加载"） |
| 证据 | `research-scenarios` 已是成功先例（202 行，按需加载）；"显式路由 intake skill"本身**尚未被这批评测验证**，只能标为"未验证的设计" |
| 结论 | **推荐作为 Phase 1 的核心**，但必须显式路由 + 有脚本化验收 |

### 选项 C：专用 intake subagent

**机制**：定义一个 `intake` subagent，让它访谈用户、输出结构化 brief，再交主循环执行。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 意图（理论上） |
| 强制力 | 高（隔离上下文/工具） |
| 用户可见性 | **结构性受限**：subagent 只能把 `<task_result>` 回传父级，不能直接对用户说话 |
| 能力 | **[实测]** opencode 1.19.3：`general` subagent `question` 权限 deny、`tools.question=false`；`subagent_depth` 默认 1，嵌套被禁；child 默认 deny `task` / `todowrite` |
| 代价 | 10–20 人日 + 评测；若改权限允许提问，还要处理"问题落在 child session、用户在主 session"的体验断裂 |
| 主要风险 | 已测试两次：D 批路由指标达标但 2026-09-27 整体回退；built-in loop 移植 2 PASS/3 FAIL、23/102 超时、2.6x LLM 调用、3.4x wall |
| 结论 | **不推荐**。除非重新定义 regime（并行+快模型）、intake 指标，并通过 `SUBAGENT_ADMISSION_PROTOCOL.md` 准入 |

### 选项 D：原生 `question` 工具（结构化消歧）

**机制**：主 agent 在需要澄清时调用 `question`，一次最多 3 个问题；每个问题有 `header`（≤30 字符）、`options[{label, description}]`、`multiple`，并默认允许自由文本。UI 侧渲染选项卡片，回传用户选择。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 意图（主）+ 概念（选项描述可翻译术语） |
| 强制力 | 中高：问题结构化、答案可被工具消费；但"是否提问"仍由模型决定 |
| 用户可见性 | 高（选项卡片 + 自由文本） |
| 代价 | 3–8 人日（AGENTS.md 规则 + 事件处理 + 端到端测试）；若用自带 Web UI 可能 1–3 人日 |
| 主要风险 | `question` 权限默认 deny（primary 已 allow，自定义 agent 需显式 allow）；`opencode serve` 下需确认 `flags.client` / `OPENCODE_ENABLE_QUESTION_TOOL`；自定义前端必须处理 `question.v2.asked` 与 reply/reject 路由 |
| 证据 | opencode 1.19.3 源码与本地实测；Claude Code 的 AskUserQuestion 是同一形状（2–4 选项、label+description、Other）；Claude Code 官方最佳实践直接建议"interview me" |
| 结论 | **推荐作为 Phase 1 的交互底座**，但必须配"问什么/问几个"的政策（§5.3） |

### 选项 E：插件 hook 门控（policy plugin）

**机制**：写一个 opencode 插件，在 `chat.message` 改写/注入、`tool.execute.before` 阻断、`permission.ask` 改判、`experimental.chat.system.transform` 注入提示。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 合规/风险门控（不是意图） |
| 强制力 | **硬**（可抛错阻断、可改参数、可改工具定义） |
| 用户可见性 | 低（除非配合 permission ask） |
| 代价 | 5–15 人日；hook 与 OMO 已在用的 11 个 hook 同链、串行执行，顺序敏感 |
| 主要风险 | 不能创建 `question`（插件无提问 API）；`permission.ask` 只是审批（once/always/reject），不是选择题；与 OMO 的 `chat.message` / `tool.execute.before` 可能冲突 |
| 证据 | opencode 插件 hook 列表；OMO 已注册 `chat.params`、`chat.headers`、`command.execute.before`、`chat.message`、`experimental.chat.messages.transform`、`experimental.chat.system.transform`、`tool.definition`、`tool.execute.before`、`tool.execute.after` 等 |
| 结论 | **不用于 intake**；用于"实盘/外部调用/敏感数据"的硬门控 |

### 选项 F：UI/产品层（产物菜单 + 能力卡 + 示例 chips）

**机制**：首屏给 5–8 个具名产物卡片；每张卡写"能回答什么 / 需要你提供什么 / 产出什么 / 不能做什么 / 典型耗时 / 数据截止"；配 3 条示例 prompt；会话内提供"换个产物"入口。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 发现 + 概念（最强） |
| 强制力 | 无（纯引导），但**用户可见** |
| 用户可见性 | 最高 |
| 代价 | 10–30 人日（前端 + 内容 + 状态）；内容运营是持续成本 |
| 主要风险 | 违反 NN/g 的 2 层上限（产物→字段→高级设置 = 3 层）；需要克制 |
| 证据 | Betterment/Schwab/Vanguard/Wealthfront 全部输出具名产物；TradingView/Composer/OpenBB 全部先给可用产物；HAX G1/G2；Apple 策划示例；PAIR 分阶段 onboarding |
| 结论 | **推荐作为 Phase 1 的发现层**，先做"轻量版"（欢迎页 + 3 条示例 + 8 张能力卡），不做完整 wizard |

### 选项 G：组合（推荐）

**机制**：F 的产物层（用户看见）+ D 的结构化提问（模型问得出）+ B 的 intake Skill（流程稳定）+ A 的极简路由（只有一段）。

| 维度 | 评估 |
|---|---|
| 解决哪个缺口 | 三个都覆盖 |
| 代价 | 20–40 人日，分 2–3 期 |
| 风险 | 需要指标与灰度；产物定义需要产品判断 |
| 结论 | **推荐路线** |

### 4.1 对比矩阵

| 选项 | 意图 | 发现 | 概念 | 强制力 | 用户可见 | 首次见效 | 代价（人日） | 主要风险 | 证据强度 |
|---|---|---|---|---|---|---|---|---|---|
| A AGENTS.md 加规则 | ◐ | ✗ | ✗ | 软 | ✗ | 1 天 | 0.5–2 | 规则膨胀、无测量 | 中（已存在但未测） |
| B 显式路由 intake Skill | ● | ◐ | ● | 中 | ◐ | 1 周 | 3–7 | 隐藏 search 失败模式 | 中（有先例，未验证 intake） |
| C intake subagent | ◐ | ✗ | ◐ | 高 | ✗ | 2–4 周 | 10–20 | 已两次失败；默认不能提问 | 强（负面） |
| D question 工具 | ● | ✗ | ● | 中高 | ● | 3–7 天 | 3–8 | 权限/部署/前端适配 | 强（原生+实测） |
| E 插件 hook | ✗ | ✗ | ✗ | 硬 | ✗ | 1–2 周 | 5–15 | 与 OMO 冲突；无提问 API | 中（文档） |
| F UI 产物层 | ◐ | ● | ● | 无 | ● | 1–2 周 | 10–30 | 2 层上限、内容运营 | 强（产品证据） |
| **G 组合** | **●** | **●** | **●** | **中高** | **●** | **2 周** | **20–40** | 需要指标/灰度 | **中（组合未验证；2026-10-27 复盘改判，见 §12）** |

图例：● 强 / ◐ 部分 / ✗ 无。

### 4.2 代价拆解（人日，**[估算]**）

| 选项 | 工程 | 内容运营 | 评测 | 常驻 token | 持续维护 |
|---|---|---|---|---|---|
| A AGENTS.md | 0.5–2 | 0 | 2–3 | **+1–4k** | 高（规则漂移/冲突） |
| B intake Skill | 3–7 | 1–2 | 3–5 | ≈0 | 中 |
| C intake subagent | 10–20 | 2–3 | 5–10 | 0 | 高（模型/权限变更） |
| D question 工具 | 3–8 | 0 | 2–3 | 0 | 低 |
| E 插件 hook | 5–15 | 0 | 3–5 | 0 | 中（hook 兼容） |
| F UI 产物层 | 10–30 | **持续** | 3–5 | 0 | 高（内容运营） |
| G 组合 | 20–40 | 持续 | 8–15 | ≈0 | 中高 |

> "工程"不含 UI 设计稿；"内容运营"指产物卡、示例、模板库的持续更新；"评测"指脚本对话 + 真实用户对照。

---

## 5. 关键设计决策（有争议的三处）

### 5.1 "1 轮 ≤3 问"要不要改？

现状：AGENTS.md 硬性规定"每个意图最多 1 轮澄清；单轮 ≤3 问；绝不发起第二轮"（`mymain-wiki/features/f7-opencode-agent.md:18`；`OpencodeAgent/skills/research-scenarios/SKILL.md:64-70`）。

外部证据：

- **[论文] 对话长度研究**（Huang et al., CHI EA 2024, arXiv:2404.17025；24 名参与者，0/3/5/7 个澄清问题）：
  - 高可对话性问题：满意度 `3.64`（无提问）/ `3.44`（3 问）/ `3.57`（5 问）/ `4.33`（7 问），7 问 vs 基线 `p=0.041`；
  - "问题数量是否合适"：3 问 `3.11`、5 问 `2.71`、7 问 `3.75`；N=7 相对 N=5 显著偏向"太多"（`p=0.027`）；
  - **低可对话性问题**：满意度 `4.36`（无提问）→ `4.13` / `4.20` / `4.17`（3/5/7 问）——**提问反而降低满意度**；
  - 预实验：`N≥10` 超过 1.5 小时并导致疲劳。
- **[官方文档] Claude Code 最佳实践**：明确建议 "Ask Claude to interview you instead... It keeps asking until the answers add up to a spec."——**多轮访谈**被官方推荐。
- **[论文] CLAM**（Kuhn, Gal & Farquhar, 2023）：在 Ambiguous TriviaQA 上，澄清带来约 **+20pp** 调整后准确率；澄清问题本身正确率 `84/99/95%`（三个数据集）。
- **[论文] Tree of Clarifications**（EMNLP 2023）：不打扰用户、自己生成多种解释，D-F1 `24.2→33.7`（+9.5）、DR `29.5→36.6`（+7.1），超过全监督基线 +7.3——说明"假设并覆盖"是"提问"之外的可行替代。
- **[论文] Knowing but Not Showing**（arXiv:2605.25284, 2026）：模型能识别 60–80% 的歧义，但回答率 >95%，只有 80–95% 是"只回答"；澄清率 ≤~5%；歧义 vs 无歧义准确率差 10–15pp；给上下文后无歧义 54%→67%、歧义 46%→55%，但上下文会**抑制提问**。

**建议**：把硬规则改为**分层预算**：

1. 默认 1 轮、≤3 问（保持现状，避免问卷疲劳）；
2. 仅当用户对某个槽位明确回答"不知道/不确定"时，允许追加 **1 轮**，只问该槽位；
3. 第二轮仍不明确 → 用默认值 + 明示假设 + 执行，并邀请纠正（现有规则）；
4. 问题必须是"答案会改变工具参数"的；答案不改变执行的，不问，直接默认。

这样既保留"不啰嗦"的纪律，又给"真的不知道"的用户一条路。**不要无条件放开多轮**——低可对话性用户会被提问伤害。

### 5.2 问几个、问什么？

建议 6 个槽位，**只挑 3 个区分度最高的问**：

| 槽位 | 选项示例 | 编译到 |
|---|---|---|
| 市场 | A股 / 港股 / 美股 / 加密 / 不确定 | 数据源、交易规则、币种 |
| 期限 | 1 周内 / 1–3 月 / 6–12 月 / 1 年+ | 回测窗口、因子窗口 |
| 风险预算 | 最大回撤 5% / 10% / 20% / 30%+ | 仓位上限、止损、组合约束 |
| 当前持仓 | 空仓 / 持有被套 / 有底仓 | 诊断 vs 新建仓 |
| 风格 | 价值 / 成长 / 动量 / 红利 / 事件 | 因子集、筛选条件 |
| 产出 | 结论 / 候选清单 / 回测 / 报告 | 场景路由 |

规则：

- **能用工具解决的绝不问**：标的模糊 → `search_symbol`；估值口径 → `ch_describe_table`；行业归属 → `get_sector_info`；现价/回撤 → `get_market_data`。
- 每个问题带**一句后果**（不是一段小作文），例如"稳健型：最大回撤约束 ≤15%，预期年化相应降低"。
- 每个问题必须有逃生门："以上都不是 / 我自己说"。
- 用户选"不确定"时，用默认值并展示"换成另一个选项会怎样"。

### 5.3 "问"还是"假设"？

证据指向**按槽位区分**，不是全局二选一：

| 槽位类型 | 策略 | 理由 |
|---|---|---|
| 答案会改变工具/参数（市场、期限、风险） | **问**（结构化选项） | CLAM +20pp；Claude Code 官方建议访谈 |
| 用户私有且无法推断（成本价、仓位规模） | **问**（≤1 轮） | 现有槽位澄清规则 |
| 有安全默认值（复权方式、回测区间） | **假设 + 明示** | Tree of Clarifications 证明"覆盖假设"可行；避免无意义提问 |
| 低可对话性/事实查询 | **不问** | CHI 研究：提问降低满意度 |
| 高风险动作（实盘、外部写入） | **permission ask**（审批，不是选择题） | HAX G10 + 现有硬约束 |

### 5.4 论文给出的 practice（可直接落到规则里）

| Practice | 证据 | 落到哪 |
|---|---|---|
| **只问"答案会改变决策"的槽位** | Rao & Daumé 2018 用 Expected Value of Perfect Information 排序问题，在 20 Questions 上优于所有基线（`p<0.05` bootstrap；指标是 P@k/MAP，不是端到端准确率）；Horvitz 1999 的 mixed-initiative 原则：问与猜是收益/成本权衡 | `intake` Skill 的槽位表 + "答案不改变执行就不问" |
| **不要靠模型自觉提问** | `Knowing but Not Showing`（2026）：模型能识别 60–80% 歧义，但澄清率 ≤~5%；上下文会抑制提问 | 显式路由 + 硬性"开放型必须先加载 intake" |
| **提问有边际递减；7 问开始偏向"太多"** | CHI EA 2024（24 人）：高可对话性问题上 7 问的满意度最高（`4.33` vs 基线 `3.64`，`p=0.041`），但"是否太多"评分也显著偏向 too many（`3.75` vs 5 问 `2.71`，`p=0.027`）；低可对话性问题提问反而降低满意度（`4.36→4.13/4.20/4.17`） | 默认 3 问；"不知道"才追加 1 轮 |
| **可枚举的歧义可以"假设并覆盖"，不必问** | Tree of Clarifications（EMNLP 2023）：不打扰用户，D-F1 `24.2→33.7`（+9.5），超过全监督基线 +7.3 | 对"市场/风格"这类可枚举槽位，可先生成 2–3 个解释并分别给结论，再让用户选 |
| **结构化多选 > 开放文本** | Claude Code `AskUserQuestion`：2–4 个选项、每个 label+description、`Other` 自由文本；opencode `question` 同形状（header ≤30、options label+description） | `question` 工具；每个 option 写后果 |
| **谄媚是主要失败模式** | Sharma et al. 2023：Claude 1.3 在 **98%** 的问题上错误认错；用户暗示错误答案可让 LLaMA 2 准确率降 **27%**；偏好模型 **95%** 偏好谄媚回答 | 不让模型猜偏好；选项即记录；关键参数写进 brief |
| **澄清会放大注入** | ASPI（arXiv:2605.17324）：澄清寻求会放大 prompt injection | 答案只填槽，不直接触发工具；保持 allowlist |

---

## 6. 推荐路线（分三期）

### Phase 0（1 周，低成本，先验证）

1. **产物菜单（轻量版）**：欢迎页/首条消息给 5–8 张卡，每张卡 6 行（能回答什么 / 需要你提供什么 / 产出什么 / 不能做什么 / 典型耗时 / 数据截止）。
2. **3 条示例 prompt**：`"帮我体检一下 600519"`、`"筛出 ROE>15%、回撤<20% 的 10 只票"`、`"用近 5 年数据回测一个红利动量策略"`。
3. **`question` 工具接入**：主 agent 允许提问；一次 ≤3 问；每题带 `description`（后果）；保留自由文本。
4. **AGENTS.md 只改一段**：把"能力索引"换成"产物索引"（8 行），加一句"开放型请求先加载 `intake`"。
5. **埋点**：首次有用问题时间、澄清完成率、工具调用成功率、会话放弃率、tokens/会话。

**验收**：10 条脚本对话（"今天买什么" / "帮我看看这个票" / "什么是因子" / "我想做量化" 等）中，≥7 条在 2 轮内产出可执行参数。

### Phase 1（2–3 周，核心）

1. **`intake` Skill**：6 槽位、3 问、选项+后果、逃生门、参数编译表；显式路由，不做 hidden search。
2. **产物路由**：8 个产物各有一张能力卡 + 一个 `research-scenarios` 场景映射；每个产物写"不能做什么"。
3. **自定义命令**（可选，1–2 天）：`/体检`、`/选股`、`/回测`、`/复盘`，本质是单条消息模板，用于发现而非流程引擎。
4. **评估**：20 条脚本对话 + 5 名真实用户；对照 Phase 0 基线。

### Phase 2（可选，2–4 周）

1. **自定义前端**：渲染 `question` 事件为选项卡片；显示"为什么问 / 这个答案意味着什么"折叠；产物卡片可点击。
2. **模板库**：保存的筛选条件/策略/报告模板（TradingView + OpenBB Prompt Library 模式）。
3. **硬门控**：插件 hook 只用于实盘/外部写入/敏感数据，不用于 intake。

### 明确不做

- ❌ 新建 intake subagent（已两次失败；默认不能提问）。
- ❌ 把 91 个 skill 或 30 个 swarm 平铺成菜单（25–30 就退化，~100 崩塌）。
- ❌ 让模型在隐藏 skill 目录里 search（C 批 config 4：0.962→0.755）。
- ❌ 继续往 AGENTS.md 堆规则（9.2k tokens 已很重；A 批证明措辞不是杠杆）。
- ❌ 在选项旁写长后果（产品证据显示后果应放在产物层或每题折叠解释）。

---

## 7. 具体设计草案

### 7.1 八个具名产物

| 产物 | 用户说法 | 输入（3–6） | 输出 | 不能做 |
|---|---|---|---|---|
| **个股体检** | "帮我看看 600519" | 标的、期限、风险 | 基本面+估值+资金+技术+结论 | 不预测具体价格 |
| **选股清单** | "最近买什么" | 市场、风格、风险、数量 | 10–20 只候选 + 理由 + 风险 | 不构成投资建议 |
| **策略回测** | "帮我回测一个策略" | 市场、因子/信号、区间、成本 | 净值、Sharpe、回撤、归因、run card | 不做实盘 |
| **组合体检** | "我的持仓怎么样" | 持仓、成本、风险 | 暴露、集中度、回撤、再平衡建议 | 不读取未授权账户 |
| **事件/宏观影响** | "俄乌战争影响" | 事件、市场、窗口 | 历史 CAR、行业暴露、情景 | 不预测地缘政治 |
| **交易复盘** | "分析我的交割单" | 文件、市场 | 行为诊断、shadow account、改进 | 不保证收益 |
| **定时监控** | "每天帮我盯" | 标的、条件、频率、通知 | 定时任务 + 通知 | 不自动下单 |
| **报告** | "生成报告" | 已有分析、模板 | HTML/PDF | 不编造数据 |

产物与 `research-scenarios` 场景的映射**只有 6/8 成立**（个股体检→A、选股清单→E、策略回测→B、事件影响→F、交易复盘→B2、定时监控→D）；**"组合体检"无对应场景，"报告"是输出格式不是场景**（对应 VT `report-generate` skill）——此断言在 2026-10-27 复盘中被证伪，修正记录见 §12。swarm 是"策略回测/组合体检"背后的**实现**，不作为第 2 层菜单。

### 7.2 `intake` Skill 的契约

```yaml
---
name: intake
description: 把模糊的投资/研究请求收敛成可执行参数。用于开放型（"今天买什么"）与待澄清型（"我的 ETF 怎么解套"）请求；一次最多 3 个选择题，每题带后果，问完即执行。
---
```

正文必须包含：

1. **触发条件**：无明确标的/期限/风险，或用户说"不知道""随便""你看着办"。
2. **槽位表**（§5.2）+ 每个槽位的默认值。
3. **提问规则**：≤3 问、选择题、每个选项一句后果、逃生门、不重复问第二轮（除非用户说"不知道"）。
4. **参数编译表**：选项 → 工具参数（不是把选项文本塞进 prompt）。
5. **执行承诺**：问完立即执行，不再确认；执行后邀请纠正。
6. **失败处理**：用户拒答 → 用默认值 + 明示假设 + 执行。

### 7.3 `question` 调用示例

```json
{
  "questions": [
    {
      "header": "市场",
      "question": "你要交易哪个市场？不同市场的交易规则和可用数据不同。",
      "options": [
        {"label": "A股", "description": "T+1、涨跌停、100 股整手；数据以 ClickHouse T-1 + 当日网络源为主"},
        {"label": "港股", "description": "T+0、无涨跌停、每手股数不同；用 yfinance/akshare"},
        {"label": "美股", "description": "T+0、盘前盘后；用 yfinance/akshare"},
        {"label": "加密", "description": "7×24、高波动；用 okx/ccxt"}
      ],
      "multiple": false
    },
    {
      "header": "期限",
      "question": "这笔钱大概多久不用？它决定回测窗口和因子选择。",
      "options": [
        {"label": "1 周内", "description": "偏事件/短线；回测至少 6 个月，避免过拟合"},
        {"label": "1–3 个月", "description": "波段；回测至少 2 年"},
        {"label": "6–12 个月", "description": "中期；回测至少 3 年"},
        {"label": "1 年以上", "description": "长期；回测至少 5 年"}
      ],
      "multiple": false
    },
    {
      "header": "风险",
      "question": "最坏情况下你能接受多大回撤？它决定仓位上限和止损。",
      "options": [
        {"label": "5%", "description": "接近货币/短债替代；权益仓位 ≤20%"},
        {"label": "10%", "description": "稳健；单票 ≤10%，行业 ≤30%"},
        {"label": "20%", "description": "平衡；单票 ≤20%，行业 ≤40%"},
        {"label": "30%+", "description": "进取；仍需止损与回撤暂停纪律"}
      ],
      "multiple": false
    }
  ]
}
```

注意：

- `header` ≤30 字符（opencode 限制；Claude Code 是 ≤12，别混用）。
- 每个 `description` 是**后果**，不是同义词解释。
- `multiple: false`；风格题可用 `true`。
- 自由文本由 `custom` 默认开启兜底；仍建议显式加"以上都不是"选项，降低用户找不到出口的概率。

### 7.4 能力卡模板

```markdown
### 策略回测
- 能回答：这个策略在过去 N 年赚不赚钱？回撤多大？换参数会不会崩？
- 需要你给：市场、信号/因子、区间、成本假设
- 产出：净值曲线、Sharpe、最大回撤、换手、归因、run card
- 不能做：不保证未来收益；不做实盘；数据截止 T-1（当日盘中需另接实时源）
- 典型耗时：2–5 分钟
```

---

## 8. 失败模式与对策

| 失败模式 | 证据 | 对策 |
|---|---|---|
| **过度提问** | CHI 2024：7 问接近"太多"；低可对话性问题提问降低满意度；N≥10 疲劳 | 默认 3 问；只问"答案会改变参数"的；低可对话性直接执行 |
| **问错问题** | `Knowing but Not Showing`：模型能识别歧义但只 5% 会问；上下文会抑制提问 | 用显式规则 + 槽位表，不依赖模型自觉 |
| **假设不说明** | Tree of Clarifications 证明"覆盖假设"可行，但用户不知道你假设了什么 | 每个默认值都明示；给"换成另一个会怎样" |
| **谄媚式猜测** | Sharma et al. 2023：Claude 1.3 在 98% 的问题上错误认错；用户暗示错误答案可让 LLaMA 2 准确率降 27%；偏好模型 95% 偏好谄媚回答 | 不让模型猜用户偏好；用选项记录选择；关键参数写进 brief |
| **澄清放大注入** | ASPI（arXiv:2605.17324）：澄清寻求会放大 prompt injection | 澄清答案只用于填槽，不直接触发工具；保持工具 allowlist |
| **隐藏 search 失败** | C 批 config 4：0.962→0.755；visible under-search / hidden mis-select | 显式路由；不做 skill search |
| **子代理经济性失败** | iter3：2.6x LLM 调用、3.4x wall、23/102 超时 | 不用 subagent 做 intake |
| **上下文税** | AGENTS.md 9.2k tokens + 91 skill 描述 | 产物卡放 UI；intake Skill 按需；AGENTS.md 只留路由 |

---

## 9. 需要拍板的三件事

1. **是否允许"第二轮澄清"？**
   - 建议：默认 1 轮；用户明确说"不知道"时允许追加 1 轮、只问该槽位。理由：CHI 研究显示 3–5 问可接受、7 问偏多；Claude Code 官方建议多轮访谈；但低可对话性用户会被提问伤害。
2. **是否投入自定义前端？**
   - 建议：Phase 0 先用 opencode 自带 Web UI 验证 `question` 事件是否可渲染；若不可用或体验差，再投入 8–15 人日做选项卡片。不要一开始就做完整 wizard。
3. **swarm 放在哪一层？**
   - 建议：swarm 是"策略回测/组合体检"背后的实现，**不作为用户可选菜单**。用户看到的是产物，不是 30 个团队名。若用户主动问"你们有哪些分析团队"，再由能力卡展开。

---

## 10. 证据附录

### 10.1 本分支实验数字（可直接引用）

| 指标 | 数值 | 来源 |
|---|---|---|
| A 批池化 McNemar | `p=0.885`；基线 `0.88–0.94` | `mymain-wiki/harness-evolution/README.md:28` |
| A7 全集 | `+3.48pp, p=0.027`；靶点 `p=0.629` | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:87-94` |
| A8 | 显著回归 `p=0.012` | 同上 |
| B 批工具裁剪 | `74→59`，`−5,100 tok/轮`（`−17.9%`） | `mymain-wiki/harness-evolution/README.md:29` |
| B 批路由 | `Δ=+1.05pp`，CI `[−2.03,+3.75]`；幻觉 0 | 同上 |
| C 批检索 | `recall@7=0.937`；披露税 `−79%` | `mymain-wiki/harness-evolution/README.md:30` |
| C 批端到端 | 4 配置全部更差，`Δ −11.5pp ~ −33.6pp` | 同上 |
| C 批 config 4 | `0.962→0.755`（`Δ −0.206`），search rate `12–19%` | `mymain-wiki/harness-evolution/evals/tool_selection/artifacts/c_batch_verdict.md:76` |
| D 批 | `R1 99.1%`、`R2 3.57%`、`R3 85%` | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:94` |
| D 批 W1/R4 | `Δ=−3.75/−5.0pp`，CI 跨 0，功效不足 | 同上 |
| D 批 token/延迟 | `10,974→1,548`（`−86%`）；`13.8s→9.3s`（`−33%`）；p90 `46→29s` | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:108-111` |
| D2-1 | `quant +6.67pp`、`webdocs +33.44pp` | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:133-142` |
| D2-2 | L2 五场景 `5/5`，委派正确率 `100%`，对抗零越权 | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:133-142` |
| D4 | `9/9` 候选准入；trading-connector `R1 0.974 / R2 0 / R3 1.000` | `mymain-wiki/harness-evolution/HARNESS_EVOLUTION_SUMMARY.md:133-142` |
| iter3 | 2 PASS/3 FAIL；`G1' 0.7917`、`G3' −0.0625`、`G6' 23>19`；超时 `0/102 vs 23/102`；LLM `2.6x`；wall `3.4x`；`0/25` 非 hard 更快 | `mymain-wiki/branch/MYMAIN_DIVERGENCE.md:358-363` |
| 当前规模 | 89/94 工具、91 skill、30 swarm | `OpencodeAgent/AGENTS.md:4-6` |

### 10.2 opencode 1.19.3 实测

| 项 | 结果 |
|---|---|
| `build` / `plan` | `question` 权限 deny→allow，工具可见 |
| `general` subagent | `question` deny，`question: false`、`todowrite: false` |
| `subagent_depth` | 默认 1（嵌套禁用） |
| `question` schema | `questions[{question, header≤30, options[{label,description}], multiple?}]`；`custom` 默认 true |
| 命令目录 | `{command,commands}/**/*.md` 都支持 |
| 插件 hook | `chat.message`、`chat.params`、`chat.headers`、`permission.ask`、`command.execute.before`、`tool.execute.before`、`tool.execute.after`、`tool.definition`、`shell.env`、`event`、`config`、`tool`、`auth`、`provider`、`experimental.chat.messages.transform`、`experimental.chat.system.transform`、`experimental.provider.small_model`、`experimental.session.compacting`、`experimental.compaction.autocontinue`、`experimental.text.complete`、`dispose` |
| OMO 已占用 | `chat.params`、`chat.headers`、`command.execute.before`、`chat.message`、`experimental.chat.messages.transform`、`experimental.chat.system.transform`、`config`、`event`、`tool.definition`、`tool.execute.before`、`tool.execute.after` |

> 部署注意：`question` 工具注册受 `flags.client ∈ {app, cli, desktop}` 或 `OPENCODE_ENABLE_QUESTION_TOOL` 控制；默认 `client=cli`，`opencode serve` 下通常可用，但上线前应在 :4096 实际发起一次提问验证。自定义 primary agent 需显式 `"permission": {"question": "allow"}`。

### 10.3 外部一手来源

**产品**
- Betterment: https://www.betterment.com/resources/how-betterment-manages-risk
- Schwab: https://hgtoolsweb.schwab.com/pcu/mp/riskProfileQuestionnaire.action
- Vanguard: https://investor.vanguard.com/tools-calculators/investor-questionnaire/questions
- Wealthfront: https://www.wealthfront.com/blog/what-is-risk-tolerance/
- TradingView: https://www.tradingview.com/support/solutions/43000718885-tradingview-screeners-walkthrough/
- Composer: https://www.composer.trade/ai
- QuantConnect: https://www.quantconnect.com/docs/v2/cloud-platform/learning-center
- OpenBB Copilot: https://docs.openbb.co/workspace/analysts/ai-features/copilot-basics
- OpenBB Skills: https://docs.openbb.co/workspace/analysts/ai-features/skills
- Claude Code Skills: https://code.claude.com/docs/en/skills
- Claude Code AskUserQuestion: https://code.claude.com/docs/en/tools-reference
- Rasa Collect: https://rasa.com/docs/studio/build/flow-building/collect/
- Salesforce Atlas: https://www.salesforce.com/agentforce/what-is-a-reasoning-engine/atlas/

**规范**
- NN/g Progressive Disclosure: https://www.nngroup.com/articles/progressive-disclosure/
- Google PAIR Mental Models: https://pair.withgoogle.com/chapter/mental-models/
- Apple HIG Generative AI: https://developer.apple.com/design/human-interface-guidelines/generative-ai
- Microsoft HAX: https://www.microsoft.com/en-us/haxtoolkit/library/

**论文**
- Rao & Daumé III, *Learning to Ask Good Questions*, arXiv:1805.04655
- Kuhn, Gal & Farquhar, *CLAM*, arXiv:2212.07769
- *Tree of Clarifications*, EMNLP 2023, arXiv:2310.14696
- Huang et al., *How Does Conversation Length Impact User's Satisfaction?*, CHI EA 2024, arXiv:2404.17025
- Sharma et al., *Towards Understanding Sycophancy in Language Models*, arXiv:2310.13548
- *Knowing but Not Showing*, arXiv:2605.25284 (2026)
- *Ask or Assume? Uncertainty-Aware Clarification-Seeking in Coding Agents*, EMNLP 2026 Findings
- *ASPI: Seeking Ambiguity Clarification Amplifies Prompt Injection*, arXiv:2605.17324
- Anthropic, *Equipping agents for the real world with Agent Skills* (2025-10-16)

### 10.4 未能核实的断言（不要当论据）

| 断言 | 状态 | 原因 |
|---|---|---|
| "Modeling Ambiguity in Underspecified Instructions" 这篇论文 | ❌ 未找到 | 检索不到该标题；不要引用 |
| "Ask-before-Act" 论文 | ❌ 不存在该标题 | 实际是 *Ask-before-Plan*（EMNLP 2024 Findings, arXiv:2406.12639） |
| CLAM 作者为 Kumar & Talukdar | ❌ 误传 | 实际是 Kuhn, Gal & Farquhar |
| Robinhood 问卷题数/题目/是否产出具名 profile | ❌ | 只有 Level 2 / Level 3 两个具名档位被证实 |
| Finviz 筛选器字段数/预设/Elite 保存 | ❌ | 站点 403 |
| Dialogflow CX / Amazon Lex 槽位引出细节 | ⚠️ | Google 429、AWS 仅返回标题；用 Rasa 替代 |
| Bloomberg 终端当前 NL 查询入口与覆盖面 | ❌ | 只证实 BloombergGPT 论文把 NL→BQL 列为用例 |
| Wealthfront 现行题数（10 题来自 2012 博客） | ⚠️ | 现行支持页只承诺"最少题数" |
| ai-hedge-fund 现行"投资者人格 agent"名单 | ⚠️ | 当前 README 已不列出；v2 改为 fund/mandate |
| QuantConnect AI 助手产品化细节 | ❌ | 未取得一手文档 |

---

## 11. 一页纸总结

**问题**：用户不知道问什么（意图缺口）、不知道你能干什么（发现缺口）、不懂术语（概念缺口）。三者叠加，导致 89 个工具 / 91 个 Skill / 30 个 swarm 实际上不可用。

**已证明**：
- 改描述措辞没用（A 批，p=0.885）。
- 裁掉不可用工具有用（B 批，−17.9% token，路由不降）。
- 让模型自己 search 隐藏 skill 有害（C 批，0.962→0.755）。
- 12 领域子代理曾达标但被整体回退；串行移植 2.6x LLM 调用、3.4x wall（D 批 + iter3）。
- subagent 默认不能向用户提问（1.19.3 实测）。

**推荐**：
1. **产物层**（UI）：5–8 张具名产物卡 + 3 条示例 + 能力边界。
2. **提问层**（原生 `question`）：1 轮 ≤3 问，选项 + 后果 + 逃生门；"不知道"才允许追加 1 轮。
3. **流程层**（按需 Skill）：`intake` 显式路由，不让模型 search。
4. **路由层**（AGENTS.md）：只留 ≤40 行产物路由，不再堆规则。
5. **门控层**（插件 hook）：只用于实盘/外部写入，不用于 intake。

**不做**：intake subagent、91 skill 平铺、隐藏 search、继续膨胀 AGENTS.md。

**第一周就能做**：8 张能力卡 + 3 条示例 + `question` 接入 + 10 条脚本对话验收。成本约 5–8 人日，不依赖前端改造。

---

## 12. 复盘与挑战者审查（2026-10-27）

> 本节是报告完成后同日的三方复查记录：**Momus**（计划严谨性，P0/P1/P2 分级）、**Oracle**（架构对抗，5 条反驳 + 最难辩护点）、**作者事实核查**（2 处硬伤）。三方独立得出结论：**方向正确，但 Option G 按现文不是"最佳可测试路线"——不可按现文开工。** 本节末尾给出修订版路线 G'。

### 12.1 一致裁决

| 审查方 | 裁决 |
|---|---|
| Momus（计划） | **按现文不可执行**：3 个 P0 全在 Phase 0（循环依赖、无物理落点、验收无基线）。引用文件抽查通过，问题不是断链，而是"文件存在但计划用错了"。 |
| Oracle（架构） | **G 可以活，但必须先做 1 天垂直切片 + 2 天槽位价值实验**；否则 20–40 人日中至少 15 人日买不到可归因的行为变化。 |
| 作者（事实核查） | 两处硬伤成立，已就地修正（§7.1 映射断言、§4.1 G 的证据评级）。 |

**未被推翻的部分**：问题定义（三缺口）、四批实验的引用、7 选项空间、外部产品/论文证据、"不做 subagent / 不做平铺 / 不做隐藏 search"的负面清单——三方均认可。

### 12.2 三个 P0（按现文不能开工的原因）

**P0-1 Phase 0 依赖 Phase 1 才创建的东西，且与现有协议冲突。**
Phase 0 第 4 步要求 AGENTS.md 加"开放型请求先加载 `intake`"，但 `intake` skill 在 Phase 1 第 1 步才创建——Phase 0 整周模型会被路由到不存在的 skill。同时 `OpencodeAgent/AGENTS.md:66-135` 已有一套完整的"四类分流 + 1 轮 ≤3 问 + 绝不第二轮"协议，Phase 0 只换能力索引不删它，结果是**两套轮次规则并存**；§6 验收写"2 轮内"，在旧规则下本身就是协议违规。修法二选一：(a) 把现有协议整体迁入 `intake` skill 并从 AGENTS.md 删除，作为 Phase 0 的第一个 commit；(b) intake skill 提到 Phase 0 之前。

**P0-2 "欢迎页卡片 + 不依赖前端改造"在部署面上没有物理落点。**
生产入口是 nginx → `127.0.0.1:4097` 的 **`opencode web`**（`OpencodeAgent/docs/DEPLOY-GUIDE.md`），不是 `serve`，也不是本仓库可控的前端；`opencode web` 没有卡片/欢迎页扩展点。不做前端改造时，卡片只能放 AGENTS.md（用户看不见）、custom command（用户要先输入 `/`）、或模型首条消息（不确定、每会话烧 token）。"删掉'欢迎页'或删掉'不依赖前端改造'，二选一"——Momus 建议改走 `.opencode/command/*.md` 的 `/` 菜单（8 个命令 = 8 张卡，**零前端、用户可见、确定性**）。

**P0-3 验收没有基线，"可执行参数"无定义，脚本里混入了不该产出参数的请求。**
"10 条脚本 ≥7 条 2 轮内产出可执行参数"：无对照基线（报告自己承认现有协议"没有任何 efficacy 实验"，7/10 可能今天就成立）；n=10 的 Wilson 95% 区间约 [0.40, 0.89]，统计上与噪声不可区分——而同一团队 A 批用的是 n=60/70 + exact McNemar，**前后标准不一致**；"什么是因子""我想做量化"按报告自己的 §5.3 应直接回答，不该产出参数——这条验收会奖励错误行为。修法：先加 Phase -1（0.5 人日）用同一组脚本测当前系统拿基线；阈值改相对基线；至少 2 条负例。

### 12.3 Oracle 的五个架构攻击（按严重度）

1. **D 是唯一增量机制，而它在三个关口都未验证**（would sink D 层）：生产 `opencode.json.tmpl` 全文**没有** `permission.question`，`entrypoint.sh`/`docker-compose.yml`/`Dockerfile` 全文**没有** `OPENCODE_ENABLE_QUESTION_TOOL`；"primary 已 allow"只对 built-in `build`/`plan` 的测试态成立，生产跑的是 OMO 自定义 agent（14 个定义）；被测面是 `serve`，生产面是 `web`，而注册枚举 `flags.client ∈ {app, cli, desktop}` **不含 `web`**。若 D 只渲染成纯文本，**G 的意图覆盖等于现状**——AGENTS.md:87-105 已经是 §7.2 想写的那份契约。结案证据：0.5–1 人日在真实 `:4097` 面上发三条探测（工具可见？`question.v2.asked` 事件？回复被消费？）。
2. **提问时机是反的：选中的三问恰好是新手最答不出的三问**（对 D 层 would sink）：报告自己的证据指向反方向——Betterment 0 道风险题；5 个开源项目 4 个不做澄清；CHI 研究里 **3 问组满意度 3.44 是所有提问组里最低的**（不问是 3.64）；"期限"按 §5.3 自己的表是"有安全默认值→假设+明示"；"最大回撤 5%/10%/20%/30%"混淆了"区分度"与"可答性"。真正私有且不可推断的槽位（成本价、仓位）反而没进前三问。
3. **B 不是新机制，它已经在生产里**（would degrade）：`AGENTS.md:231-235` 已写"识别场景后必须先加载 `research-scenarios`"，其场景 C 正文就是同一份漏斗协议。新增 `intake` 会把竞争描述池从 164 变 165，并与场景 C **争夺同一个触发条件**——除非明确"删除场景 C"，否则模型面对两条互指路由。且报告一边用 A 批当普适法则（"措辞不是杠杆"），一边用一句 prompt 文案做 B 的触发器，自相矛盾。结案证据：1 人日、40 条语料测 `intake_load_rate`；<60% 则改用 `chat.message` hook 做确定性触发。
4. **Phase 0 验收测不出成功也归不了因**（would degrade）：跨四层的聚合门（菜单→触发→渲染→编译），失败无法定位是哪层；5 个埋点**零阈值零动作**——那是仪表盘，不是反馈回路。修法：复用归档的 `harness-evolution/evals/tool_selection` 判官与统计脚本，改四层分率 + 40 条真实语料配对对照。
5. **5–8 张卡是产品策展穿了架构外衣**（有效性 worth knowing + 预算 would degrade）：§3.1–3.3 证明的是"具名产物+默认值+后果预览"有效，**不是"这 8 个产物是对的"**——报告没有任何本产品请求分布证据；菜单混了层级（"报告""定时监控"是横切工具）；无 owner、无"菜单错了"的判定条件。结案证据：1 人日，取最近 N 条真实首条消息分类进 8 产物 + "都不是"；**若"都不是">30%，菜单在写代码前就错了**。

**最难辩护的是第 2 条，不是第 1 条。** 第 1 条是 bug，1 小时探测可修；第 2 条攻击价值主张：即使 `question` 完美渲染、模型 100% 触发，**ask-first 仍可能是错的形状**。CLAM 的 +20pp 来自答错不可逆的 trivia QA，而这里猜错一个槽位的成本是"再跑 2 分钟"；报告自己引的 Tree of Clarifications（+9.5 D-F1）和 EVPI 原则都指向"先假设、后覆盖"，却被降级为"用户说不知道时才用"。

### 12.4 作者自查的两处硬伤（已就地修正）

1. **§7.1 映射断言证伪**：实测 `research-scenarios` 只有 7 个场景（A/B/B2/C/D/E/F），8 个产物里"组合体检"无对应场景、"报告"是输出格式不是场景。原文"每个产物对应一个场景"已修正。
2. **§3.6 混用了两套 skill 系统**：`OpencodeAgent/skills/`（如 research-scenarios）**没有任何 opencode.json.tmpl 注册**——加载机制是 AGENTS.md 指令 + agent 文件读取；而 §3.6 的 `load_skill(name)` 描述的是 VT 自己的 MCP skill 系统（`agent/src/skills/` 91 个金融 skill）。§7.2 的 intake 契约落在前者（与 research-scenarios 同构，可行），但正文没有说清，会误导执行者去走原生 skill 注册或 VT `load_skill`。

### 12.5 修订版路线 G'（替代 §6，作为可测试路线）

**核心改动：把 ask-first 改成 artifact-first。** 第一份产物先于澄清到达用户；`question` 保留但移到产物之后，只问"私有 + 不可推断 + 改变结论"的槽位（成本价、当前仓位）；市场/期限/风险默认化，用回测引擎算 2×2 后果 diff 卡呈现（直接复刻 Schwab Q7）。发现层从"模型贴卡"改为**确定性 `/` 自定义命令**（用户触发、0 token、§3.6 已证可用）。

| 阶段 | 人日 | 内容 | 出口判据 |
|---|---|---|---|
| **Phase -1（前置验证，先做）** | **~4–6** | ① 0.5d：同一组脚本测当前 build，拿基线；② 1d：真实 `:4097` web 面 `question` 三探测（可见/事件/回传）+ 生产 agent 权限核对；③ 2d：槽位决策价值实验（6 槽 × 4 值 = 24 次跑批，盲评"产物是否不同"），无差别的槽位一律默认化；④ 1d：40 条语料测 `intake_load_rate`；⑤ 1d：最近 N 条真实首条消息分类，验证 8 产物覆盖率 | 任一失败即触发 §12.6 kill criterion |
| **Phase 0（修订）** | ~5–8 | 把 AGENTS.md:66-135 协议**迁入** `intake` skill 并删除原文（单 commit，消除双协议）；产物菜单落地为 `/体检` `/选股` `/回测` `/复盘` 等自定义命令（不是欢迎页）；能力卡由 harness 在结果后**确定性追加**（"我假设了什么 / 换一个会怎样 / 下一步"），不靠模型自觉 | 四层分率埋点 + 40 条真实语料配对对照，相对基线显著改善 |
| **Phase 1（修订）** | ~8–12 | intake skill 契约补全（6 槽位默认值 + 选项→参数编译表 + brief 输出 schema + 解析测试）；产物映射表补全（8 产物 → 场景/skill/工具/边界，含 8 产物之外请求的兜底路由）；ask-first vs artifact-first 配对 A/B（30–40 条真实语料，测首次可用产物时间与完成率） | 预注册阈值（路由正确率、≤3 问、0 协议违规）；非作者评审 |
| **Phase 2（降级为条件触发）** | 仅当 spike 失败 | 自定义前端：明确"fork opencode web"还是"新建 app"，给 artifact/owner；默认不给预算 | — |

### 12.6 Kill criteria（任一命中即停止重估）

- `question` 在 `:4097` 真实面三探测任一失败，且文本回退形态下 A/B 无差异 → D 层撤销，G' 退化为"默认执行 + diff 卡 + `/` 命令"。
- `intake_load_rate` < 60% → 放弃 prompt 路由，改 `chat.message` hook 确定性注入（需评估与 OMO 已占用 hook 的冲突）。
- 槽位价值实验中 ≥4/6 槽位输出无差别 → 只保留私有槽位提问，其余全默认化。
- 首条消息分类中"都不是" > 30% → 8 产物菜单推倒重来，先做请求分布研究。
- Phase 0 配对对照相对基线无显著差异 → 不进入 Phase 1。

### 12.7 对"G 是否仍是最佳可测试路线"的最终回答

**方向正确，原方案不可按现文执行，修订后的 G' 是当前最佳可测试路线。** 理由：四层的方向（产物/提问/流程/路由）与全部内外证据相容，三方审查没有任何一方提出更好的方向；但原 G 把唯一的新机制押在一个未验证的原语上、把提问放在产物之前（与自己的产品证据相反）、验收无基线无法归因。G' 用 ~4–6 人日的前置实验把三个承重假设（D 渲染、B 触发率、槽位价值）全部变成可判假的命题，再决定 20–40 人日的投入——这才是"可测试"的含义。

## 13. 立项后变更（2026-10-28）

本节记录立项后的实测结果与本报告 §12.5 预期的差异。数字取自已提交的证据文件；未完成的测量明确标注，不补猜。

### 13.1 当前构建基线（10 条，镜像 v3.10.0-mymain）

`OpencodeAgent/tests/evals/intake/baseline_report.md`：10 条 event-complete，0 `run_error`。

| 层 | 观测 |
|---|---|
| `route_class` 一致率 | 6/10 |
| `intake_loaded` | 0/10 |
| `question_called` | 0/10 |
| `params_compiled` | 9/10 |
| 分类一致率 | execute_with_defaults 5/5；clarify_private 0/2；direct_answer 0/2；refuse 1/1 |

解读：现有协议已经在产出参数（9/10），但从不提问（`question_called` 0/10）。`intake_loaded=0` 是技能尚不存在的定义性零，不是路由质量测量。

### 13.2 槽位价值实验：六个公共槽位全部 INCONCLUSIVE

`OpencodeAgent/tests/evals/intake/slot_value/verdict.md`（v2，预注册规则）：72/72 次运行产出可用签名（71 次在观察到第 3 个工具调用后提前停止，1 次触达 300s 超时）；24/24 个 cell 各有 3 个互不相同的签名；市场、期限、风险、风格、候选池、持仓六个公共槽位全部 INCONCLUSIVE；kill criterion 未触发（default-worthy slots 0/6，阈值 4/6）。

因此 §12.5 关于「用槽位实验决定问题表」的预期**未达成**：实验既没有证明哪些槽位可默认化，也没有证明哪些必须提问。已实现的契约按产品判断默认化六个公共槽位，只保留 成本价、仓位规模 两个私有槽位提问；这是一项默认选择，不是实验授权。

### 13.3 触发率与菜单门（Wave 0，40 条）

`OpencodeAgent/tests/evals/intake/wave0_verdict.md`：

- `intake_loaded` 0/40 是构造性结果：迁移前的镜像里不存在 `skills/intake/SKILL.md`，事件无法观测。它不能用来判定路由质量，也不能激活条件工作 ①。
- 迁移前澄清代理（open_ended ∪ clarify_private，n=14）：5/14 = 35.7%，Wilson 95% [16.3%, 61.2%]；同一子集的 `question_called` 为 0。条件工作 ①（确定性 `chat.message` 触发）推迟到迁移后的 40 条复跑，门槛为 `intake_loaded` < 60%。
- 菜单覆盖（有产物契约的 26 条）：`都不是` 3/26 = 11.5%，Wilson 上界 29.0%，不超过 35% → **menu gate PASS**。报告中「8 产物菜单可能推倒重来」的担忧未被证据支持。

### 13.4 closing block 的强制方式

`OpencodeAgent/tests/evals/intake/plugin_feasibility.md`：opencode 1.19.3 的 21 个 hook 中，只有 `experimental.text.complete` 接收最终的 assistant 文本，且其 `output.text` 返回值会被写回持久化消息（`static-only` 证据：类型面与调用点支持；运行时插件加载探针失败，未观测到端到端生效）。

因此 closing block 以「契约 + ≥90% 出现率门」交付；硬追加插件登记为 F-wave 跟进项（`F-wave: closing-block hard-append plugin`），不作为本计划的前置条件。40 条语料上的 ≥90% 门仍是独立检查点。

### 13.5 Wave 1 复跑状态

40 条 Wave 1 复跑结果未出：`OpencodeAgent/tests/evals/intake/results/wave1_report.md` 尚未提交。`待 Wave 1 复跑（结果未出）`。

### 13.6 证据路径

- `OpencodeAgent/tests/evals/intake/baseline_report.md`
- `OpencodeAgent/tests/evals/intake/wave0_verdict.md`
- `OpencodeAgent/tests/evals/intake/slot_value/verdict.md`
- `OpencodeAgent/tests/evals/intake/plugin_feasibility.md`
- `OpencodeAgent/tests/evals/intake/results/image_delivery.md`