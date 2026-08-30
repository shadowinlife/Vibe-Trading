# Harness 演进研究 — 论文索引（可检索资料）

> 维护者：shadowinlife ｜ 初版：2026-08-22 ｜ 第二轮扩充：2026-08-22（E–I 节，25 篇新论文入库）｜ 状态：**持续扩充中**
> 用途：Vibe-Trading agent 层 harness 演进决策的可检索文献库。所有条目均于 2026-08-21/22 在本 session 内检索验证。
> 检索方式：按类别浏览，或按 arXiv ID / 关键词（每条含「要点」与「harness 启示」两栏）全文搜索。

---

## A. LLM 交易 Agent 框架论文 — "证据缺陷"集群

> 集体特征：alpha 声明**全部仅作者自报，零独立复现**；评测窗口短、无交易成本、标的池窄。
> 对自研决策的意义：这些论文的**架构机制**可借鉴，但**性能声明不可作为选型依据**。

| arXiv | 标题 | 核心机制 | 关键警示 | harness 启示 |
|---|---|---|---|---|
| [2412.20138](https://arxiv.org/abs/2412.20138) | TradingAgents | 镜像券商组织架构（分析师→bull/bear 辩论→研究经理→交易员→三方风险辩论→PM）；**结构化文档传状态、NL 只用于有界辩论**（防"电话效应"）；deep/quick 双层模型；延迟结果反思（决策日志 pending→resolved，用实现收益+alpha 反思，按标的回注） | 评测仅 2024 Q1 一个季度、5-6 只巨头科技股；年化 Sharpe 8.21/6.39/5.60 在 3 个月窗口上统计无意义；无交易成本 | **唯一生产级教训累积机制**；多 agent 状态传递必须类型化/结构化，不能靠 NL 转述 |
| [2311.13743](https://arxiv.org/abs/2311.13743) | FinMem | 按信息半衰期分层记忆（浅/中/深，衰减常数 Q=14/90/365 天）+ 即时/延展反思 + 高影响事件晋升深层 | 作者自认训练数据仅 6-12 个月 | 记忆分层衰减参数可直接借用；"高影响事件晋升深层"是记忆写入优先级的好规则 |
| [2407.06567](https://arxiv.org/abs/2407.06567) | FinCon | manager-analyst 层级 + **概念化语言强化**（投资信念由风险事件触发的自我批评更新，选择性传播给需要的节点） | 仅作者报告 | 信念/教训的**选择性传播**（只给需要的节点）比全量广播更省 token、更聚焦 |
| [2402.18485](https://arxiv.org/abs/2402.18485) | FinAgent | 多模态 + **双层反思**（反思决策 vs 反思对市场数据的感知） | 92.27% 收益来自单一未具名数据集 | "反思感知层"与"反思决策层"分离是好的错误定位设计 |
| [2408.06361](https://arxiv.org/abs/2408.06361) | LLM 交易 agent 综述 | 领域自查清单 | 过拟合回测、look-ahead、成本忽视、不可复现 | 选型评审任何交易 agent 论文时的四问清单 |

## B. 评测基准的批判性转向（2026）— 本索引最重要的部分

> 2026 年评测文献集体宣布："端到端 LLM 交易 agent 报告的 alpha 不应被视为部署证据"。
> 对自研决策的意义：**新 harness 的评测方法学比框架本身更值得投资**。

| arXiv | 标题 | 核心发现 | harness 启示 |
|---|---|---|---|
| [2605.16895](https://arxiv.org/abs/2605.16895) | **The Alpha Illusion** | 立场论文：点名 FinCon/FinMem/TradingAgents/FinAgent 的 alpha 声明无效；提出六项有效性检验（时间完整性/真实摩擦/反事实稳健/预测校准/数值执行/多 agent 解耦）；建设性替代：**LLM 作为独立校准、风险、执行模块上游的"可审计信息接口"** | Vibe-Trading grounding 闸门已精确实现该处方——这是演进而非自研的最强论据 |
| [2605.28359](https://arxiv.org/abs/2605.28359) | KTD-Fin | 两大评测缺陷工程：(1) 知识截止污染 → **ticker/日期掩码协议**；(2) 收益≠技能 → **Barra 式归因**。防泄漏评测下 agent 收益"大部分由被动市场与风格暴露解释，几乎没有持续选股 alpha" | 评测必须掩码 + 因子归因，否则测的是模型历史记忆 |
| [2608.09988](https://arxiv.org/abs/2608.09988) | OpenPM | PIT 可审计评测；**分析质量比组合构造模型更重要，换手是主要成本来源**；风险指令应编译为**类型化约束**而非 prompt 文本；标准评测产物 = 污染证书 + 成本敏感曲线 + 约束遵守报告 | "风险指令编译为类型化约束"对 harness 的指令层设计有直接指导意义 |
| [2607.12233](https://arxiv.org/abs/2607.12233) | FinMMEval（Fin-Analyst 实盘评测） | 首批**实盘**评测：短窗口排名靠运气（中期榜与终榜名次反转）；**无记忆 agent 连续数日重复错误调用**；8-K 事件披露是最强信号 | 记忆/反思闭环是实盘表现的区分变量；评测必须 forward-only |
| [2311.11944](https://arxiv.org/abs/2311.11944) | FinanceBench | GPT-4-Turbo+检索在"简单"财务 QA 上 **81% 答错或拒答** | 幻觉是结构性问题——输出验证闸门（grounding）不可省 |
| [2402.12659](https://arxiv.org/abs/2402.12659) | FinBen | LLM 强于信息抽取/文本分析，**弱于推理与预测** | 评测必须按能力分层；别让模型做它不擅长的预测并假装擅长 |

## C. 通用 Harness / Agent 研究 — 可迁移设计原则

| arXiv | 标题 | 已验证结论 | harness 启示 |
|---|---|---|---|
| [2210.03629](https://arxiv.org/abs/2210.03629) | ReAct | 循环内外部 grounding 消除幻觉 | reason→act→observe 是原子单位，任何自研循环的最小内核 |
| [2303.11366](https://arxiv.org/abs/2303.11366) | Reflexion | 91% pass@1 HumanEval | **反思必须锚定外部反馈信号**；金融场景最危险的是"模型给自己打分" |
| [2305.16291](https://arxiv.org/abs/2305.16291) | Voyager | 技能库迁移到新环境 | **可执行+自验证的代码技能优于上下文示例**——技能系统的形态选择依据 |
| [2405.15793](https://arxiv.org/abs/2405.15793) | SWE-agent | 12.5% SWE-bench（当时 SOTA） | **ACI（agent-computer interface）设计是一等变量**；Anthropic 证实"优化工具花的时间比优化 prompt 多" |
| [2310.08560](https://arxiv.org/abs/2310.08560) | MemGPT | 虚拟上下文管理，已产品化为 Letta | 记忆是 OS 资源，agent 显式换页——自研记忆层的参考架构 |
| [2305.14325](https://arxiv.org/abs/2305.14325) + [2311.17371](https://arxiv.org/abs/2311.17371) | 多 agent 辩论 + 独立反驳 | 辩论**不稳健优于** self-consistency，超参敏感 | 辩论不是免费 alpha；只在结构化有界时用，且需对廉价自一致性基线做成本消融 |
| [2409.07429](https://arxiv.org/abs/2409.07429) | Agent Workflow Memory | Mind2Web +24.6% / WebArena +51.1% | 最佳记忆单元是**归纳出的例行程序**而非原始轨迹 |
| [2307.03172](https://arxiv.org/abs/2307.03172) + [2505.06120](https://arxiv.org/abs/2505.06120) | Lost in the Middle + 多轮退化 | 多轮平均**性能下降 39%**，走错不回头 | 长会话需要周期性 re-grounding / 从整合状态重启——压缩策略的设计依据 |
| [2407.01502](https://arxiv.org/abs/2407.01502) | AI Agents That Matter | 只优化准确率 → 过度复杂 + 过拟合基准 | 评测必须**成本+准确率联报** + 真 holdout |
| [2605.25958](https://arxiv.org/abs/2605.25958) | PolyGnosis 2.0 | 金融域 harness 消融：**无约束反思诱发逻辑漂移**、普遍共识偏差 | 反思循环需硬 grounding 与有界轮次——金融域的独立证据 |
| —（网页文献） | Anthropic《Building Effective Agents》 | 工作流 vs agent 五模式 | 从最简单方案开始；工具设计 = prompt 工程 |

## D. MCP / 工具面经济学（工程证据，非论文）

> 自研决策的核心约束：**工具数量与 agent 准确率的关系是悬崖式的**。

| 证据 | 数据 | 出处 |
|---|---|---|
| 披露税 | 每工具中位数 ~700 token 披露成本，每规划轮重复支付 | MCP 客户端实测（本 session 调研） |
| 准确率悬崖 | 工具选择准确率在 **25-30 个可见工具后退化，~100 个崩塌** | 多来源交叉验证（本 session 调研） |
| GitHub Copilot | MCP 工具 40→13 后 SWE-bench **提升 2-5pp** | Copilot MCP 实践报告 |
| Harness 案例 | 130→11 工具后性能提升 | Harness 项目实践 |
| Anthropic code mode | 数据密集链 150k→2k token（**-98.7%**） | Anthropic 官方实测 |
| OpenBB | 按会话动态激活工具防 token 膨胀 | OpenBB Workspace MCP 官方文档 |
| LangAlpha | Programmatic Tool Calling——agent 对 MCP 工具写 Python 而非把数据灌进上下文 | LangAlpha 项目文档 |

> 学术证据补全：本节工程观察的学术锚点见 **F 节**（2025-2026 年 tool scaling / MCP 经济学的实证论文）。

## E. Harness / Scaffold 架构与上下文工程（2025-2026 增量，本轮新增）

> 本节回答"自研 harness 是否值得"的核心问题：**固定模型、只换 harness，质量差异可达两位数百分点**——harness 本身是一等变量，不是模型的附属胶水。

| arXiv | 标题（日期） | 核心发现 | harness 启示 |
|---|---|---|---|
| [2607.13683](https://arxiv.org/abs/2607.13683) | HarnessBank: Semantic Gene-Bank Search with Gated Verification for Agent-Harness Self-Evolution（2026-07-15） | harness 自进化框架：任务 agent + 独立 evolver agent 做迭代失败诊断→harness 生成→门禁验证；"Harness 基因库"保存不同语义坐标的高性能 harness，进化中重组/筛选；门控筛选降低后代评估成本。7 个基准一致提升 5.1%–15.4%；跨模型实验证明增益来自**模型特异的自进化过程，不存在普适最优 harness** | harness 进化的正确形态是"针对当前模型的进化 + 验证门禁"，而非照抄某个"最佳 harness"；Vibe-Trading 的技能自进化（save_skill/patch_skill）应补失败诊断驱动与门控验证环节 |
| [2607.03691](https://arxiv.org/abs/2607.03691) | Don't Blame the Large Language Model: How Agent Harness Evolution Shapes Coding Agent Quality（2026-07-04） | **首个隔离 harness 贡献的纵向受控研究**：固定模型、只变 harness，评估 Qwen Code CLI 35 个连续版本在 50 个分层 SWE-bench Verified 任务上的质量波动，并可追溯到具体 PR/架构组件；同时调研 5 大开源 harness（Codex/Qwen Code/Gemini/OpenCode/OpenHands），发布频率超每天 2 次。从业者习惯把 harness 引起的回归归因给模型 | harness 更新本身需要质量回归监控（固定模型测 harness）；自研必须建立版本级质量基线，否则"是模型还是 harness 变差了"永远是悬案 |
| [2606.13643](https://arxiv.org/abs/2606.13643) | Recursive Agent Harnesses（2026-06-11） | 命名"递归 agent harness"（RAH）模式：父 agent 生成可执行脚本，脚本并行派生带文件系统/代码执行/规划能力的子 harness。GPT-5 底座固定时，Oolong-Synthetic（最长 4M token，13 个上下文长度桶）上把 Codex 基线从 71.75% 提到 81.36%——**增益归因于 harness 而非模型**；换 Sonnet 4.5 达 89.77% | 长上下文任务应靠 harness 级递归/分解而非更大窗口解决；自研 harness 的 swarm 层可把"代码派生子 harness"作为一等模式 |
| [2510.04618](https://arxiv.org/abs/2510.04618) | Agentic Context Engineering（ACE）: Evolving Contexts for Self-Improving Language Models（2025-10-06，ICLR 2026） | 把上下文当作进化 playbook（生成→反思→策展），用结构化增量更新避免"context collapse"（迭代重写侵蚀细节）与"brevity bias"；agent 基准 +10.6%、**金融域 +8.6%**；AppWorld 上用更小开源模型追平顶级生产 agent；无需标注、纯执行反馈自适应 | 自研 harness 的压缩/上下文管理层应保留增量式结构化更新，不做整体重写——这是 5 层上下文管理的直接设计依据 |
| [2507.13334](https://arxiv.org/abs/2507.13334) | A Survey of Context Engineering for Large Language Models（2025-07-17） | 166 页、1400+ 篇文献的上下文工程综述：检索/生成/处理/管理四组件 × RAG/记忆系统/工具集成推理/多智能体四系统形态；指出能力不对称——模型善于理解复杂上下文、弱于生成同等质量的长输出 | 术语与设计空间的地图；架构对比文档用这个 taxonomy 给各 harness 定位，避免自造词 |

## F. 工具扩展律与 MCP 经济学（学术证据，2025-2026 增量，本轮新增）

> 与 D 节工程证据互补：**"25-30 工具退化、~100 崩塌、~700 token/工具披露税"从此有了可引用的学术锚点与量化机制**。

| arXiv | 标题（日期） | 核心发现 | harness 启示 |
|---|---|---|---|
| [2605.24660](https://arxiv.org/abs/2605.24660) | How Many Tools Should an LLM Agent See? A Chance-Corrected Answer（2026-05-23） | 把"可见工具数"作为评估对象，提出机会校正度量 BoR（Bits-over-Random），在 20→3,251 工具的注册表上评估，并把 BoR 转为 RL 奖励学习**逐查询**的短名单深度：BFCL（370 工具）上平均只呈现 7 个工具即接近呈现 50 个的覆盖率（90.3% vs 90.8%）；下游 Claude Sonnet 4.6 工具选择准确率自适应短名单 93.1% vs 固定 5 个 87.1%，中等难度查询差距扩大到 76.8% vs 60.9% | 最优工具数不是全局常数而是逐查询自适应值；自研工具层应做"动态短名单 + 深度搜索兜底"，不是静态全量披露 |
| [2604.21816](https://arxiv.org/abs/2604.21816) | Tool Attention Is All You Need: Dynamic Tool Gating and Lazy Schema Loading（2026-04-23） | 形式化"MCP 税/工具税"：无状态急切 schema 注入在典型 4-6 服务器部署造成每轮 10k–60k token 披露开销；上下文利用率超 ~70% 时推理质量崩塌（全量注入在 **N≈50 工具**处越过断裂点）。中间件层 Tool Attention（意图-schema 重叠打分 + 状态感知门控 + 两阶段懒加载）在 120 工具/6 服务器基准上实测每轮工具 token **-95%（47.3k→2.4k）**、有效上下文利用率 24%→91%。⚠️ 端到端成功率为**投影值非实测** | 协议层效率是硬约束；77 工具的 MCP 面已在 N≈50 断裂点之上——懒加载 + 门控是必选项而非优化项 |
| [2602.14878](https://arxiv.org/abs/2602.14878) | Model Context Protocol (MCP) Tool Descriptions Are Smelly!（2026-02-16） | 856 工具 / 103 MCP 服务器实证：**97.1% 工具描述至少含一个"臭味"，56% 未清楚陈述用途**；补全全部六组件使任务成功率中位数 +5.85pp、部分目标完成 +15.12%，但执行步数 +67.46% 且 **16.67% 情形反而回归**；消融显示紧凑组件组合可保住行为可靠性并省 token（去掉 Examples 组件无显著损失） | 工具描述质量是可度量的 harness 变量；正确做法是逐工具成本收益分析（紧凑描述 + 定向补全），不是一刀切写长描述 |
| [2508.12566](https://arxiv.org/abs/2508.12566) | Help or Hurdle? Rethinking Model Context Protocol-Augmented Large Language Models（MCPGAUGE，2025-08-18） | 首个 MCP 成本收益系统评估：6 个商用 LLM × 30 个 MCP 工具套件 × 160 提示/25 数据集，约 2 万次 API 调用（$6,000+），沿主动性/合规性/有效性/开销四维评估；结论挑战"MCP 集成必然增强"的默认假设——收益高度依赖任务与模型组合 | MCP 工具是否有益是任务相关、模型相关的；自研 harness 增配工具应建立逐工具有效性证据，不假设"多即好" |
| [2508.01780](https://arxiv.org/abs/2508.01780) | LiveMCPBench: Can Agents Navigate an Ocean of MCP Tools?（2025-08-03） | 70 服务器/527 工具上的 95 个真实日常任务：Claude-Sonnet-4 达 78.95%，多数模型仅 30-50%；**检索错误占全部失败的近一半**，主动工具组合与任务成功率相关性最强 | 大工具面的瓶颈在检索而非推理；自研工具层必须支持多工具组合，并把检索质量列为一等监控指标 |

## G. Agent 记忆架构（2025-2026 增量，本轮新增）

> C 节已有 MemGPT（2310.08560）与 Agent Workflow Memory（2409.07429）；本节为 2025+ 增量。

| arXiv | 标题（日期） | 核心发现 | harness 启示 |
|---|---|---|---|
| [2606.29914](https://arxiv.org/abs/2606.29914) | MemDelta: Controlled Baselines and Hidden Confounds in Agent Memory Evaluation（2026-06-29） | LongMemEval-S 上的受控评估（每次只变一个组件）：**仅换 embedding 模型就能移动 ±6.2pp 准确率（p=0.004），足以翻转结论**；agent 自记忆（42%）不如基础 RAG（47%）；Mem0 仅在 6 类问题中的 2 类追平云 RAG（72.7% vs 73.9%）而成本 50 倍；模型家族间排名反转（Gemini 吃全上下文、Sonnet 吃 RAG） | 记忆评测必须固定 embedding、按模型家族分层、报告写入路径成本；自研记忆层先建受控基线，否则无法归因增益来源 |
| [2602.06052](https://arxiv.org/abs/2602.06052) | A Survey of Agent Memory in the Second Half: Towards Self-Evolving and Long-Horizon Agents（2026-01-14，TMLR Survey Certification） | 统一 2025 年数百篇记忆论文：记忆基底（参数化/外部检索存储）× 认知机制（感觉/工作/情景/语义/程序性）× 记忆主体（用户中心/agent 经验）三维框架；核心论点：记忆是 agent 自进化的基底，**记忆管理本身正在成为可训练能力**（RL 上下文策展、决策时经验固化、可移植技能生态） | 自研记忆层的设计空间应按"基底×机制×主体"显式枚举；Vibe-Trading 的 Markdown+FTS5 属外部语义存储，缺口在程序性记忆（技能固化）与可训练写入策略 |
| [2601.07978](https://arxiv.org/abs/2601.07978) | Cost and Accuracy of Long-Term Memory in Distributed Multi-Agent Systems Based on LLMs（2026-01-12，IEEE COMPSAC 2026） | 独立可复现测试床（准确率/延迟/CPU/内存/磁盘/网络）对比 mem0、Graphiti、cognee + RAG/全上下文基线（LoCoMo）：mem0/RAG/全上下文 77-81%，图后端（Graphiti/cognee）仅 55-56%（检索不完整而非推理失败）；**RAG 基线以 mem0 的 1/8.4 TCO 追平上限集群**；压缩精度而非上下文体量决定准确率（全量转发反而输给 mem0） | 记忆层先测成本-准确率帕累托；廉价 RAG 基线是最强竞争者，复杂记忆系统必须证明帕累托改进才值得引入 |
| [2504.19413](https://arxiv.org/abs/2504.19413) | Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory（2025-04-28） | 动态抽取/固化/检索的记忆中枢架构；LOCOMO 上 LLM-as-Judge 相对 OpenAI 记忆方案 +26%，图记忆变体再 +2%；对比全上下文方案 **p95 延迟 -91%、token 成本省 90%+** | 生产级记忆（抽取→固化→检索 + 图增强）的工程模板；其成本/延迟数据可直接作为自研记忆层的对比基线 |
| [2502.12110](https://arxiv.org/abs/2502.12110) | A-MEM: Agentic Memory for LLM Agents（2025-02-17，NeurIPS 2025） | Zettelkasten 式 agentic 记忆：新记忆生成结构化笔记（上下文描述/关键词/标签），系统自动发现与历史记忆的链接，新记忆可触发旧记忆属性更新（记忆进化）；6 个基座模型上一致优于 SOTA 基线 | "记忆网络自组织演化"是区别于 KV 存储的范式；自研记忆层的 Tier-2 分类目录可引入链接关系与演化操作 |

## H. 多 Agent 编排框架（2025-2026 增量，本轮新增）

| arXiv | 标题（日期） | 核心发现 | harness 启示 |
|---|---|---|---|
| [2605.03310](https://arxiv.org/abs/2605.03310) | Coordination as an Architectural Layer for LLM-Based Multi-Agent Systems（2026-05-05） | 引生产环境多 agent 系统失败率 41%–87%，主因是协调缺陷而非基座模型能力；主张协调应是**可配置、与 agent 逻辑和信息访问分离的架构层**；在 100 个知识截止后结算的 Polymarket 市场上（单模型/固定工具/固定输出上限）测 5 种参考协调配置，Murphy-Brier 分解显示不同配置留下可区分的校准/判别签名 | 自研编排层应可配置、可独立消融；"用哪种协调拓扑"是有可测签名的决策而非风格选择；该文同时是编排方法学落地到金融预测市场的样例 |
| [2604.07911](https://arxiv.org/abs/2604.07911) | Dynamic Attentional Context Scoping: Agent-Triggered Focus Sessions for Isolated Per-Agent Steering（DACS，2026-04-09） | 多 agent 编排的**上下文污染**问题：N 个 agent 竞争编排器窗口时互相污染 steering。双模编排器：Registry 模式只保留每 agent ≤200 token 状态摘要，SteeringRequest 触发 Focus 模式注入当事 agent 全量上下文、其余压缩回登记项。200 次试验：steering 准确率 90.0–98.4% vs 扁平上下文基线 21.0–60.0%（p<0.0001），**优势随 N 增大** | 编排器上下文必须"摘要登记 + 按需聚焦"；自研 swarm 编排器要避免 N 个 worker 的状态污染调度上下文——这是 Vibe-Trading swarm runtime 的直接改进项 |
| [2506.12508](https://arxiv.org/abs/2506.12508) | AgentOrchestra: Orchestrating Multi-Agent Intelligence with the Tool-Environment-Agent（TEA）Protocol（2025-06-14） | TEA 协议把工具/环境/agent 建模为**带显式生命周期的一等版本化资源**，支持端到端上下文与版本管理（可追溯/可复现/持续自进化）；层级化中心 planner + 运行时动态能力扩展；GAIA Test set 89.04% | 工具/环境/agent 的版本化与生命周期管理是协议级基建——与 Vibe-Trading 哈希 manifest 治理直接相关，可参考把 manifest 扩展为"资源生命周期"维度 |
| [2503.13657](https://arxiv.org/abs/2503.13657) | Why Do Multi-Agent LLM Systems Fail?（MAST，2025-03-17） | 首个多 agent 失败分类学：7 个主流 MAS 框架的 1600+ 标注轨迹，专家分析 150 条（κ=0.88）得 **14 种失败模式、3 大类**（系统设计缺陷/agent 间错位/任务验证薄弱）；附 LLM-as-Judge 自动标注管线与 MAST-Data 数据集 | 自研编排层应逐条对照 MAST 14 失败模式做检查单；"agent 间错位"与"验证薄弱"正是结构化输出 + deliverable 校验要压制的两大簇 |
| [2411.04468](https://arxiv.org/abs/2411.04468) | Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks（2024-11-07，**锚点**） | Orchestrator 主导的通用多 agent 架构（规划/跟踪/重规划 + web/文件/代码专家 agent）；GAIA/AssistantBench/WebArena 上不改核心能力与协作方式即有竞争力；**模块化设计允许增删 agent 而无需重调 prompt** | "lead-agent 账本 + 重规划"是通用编排的参考原型；其"增删 agent 免重调"应作为自研 swarm 拓扑变更的验收标准 |

## I. 金融/量化 LLM Agent 增量（2025-2026，本轮新增）

> A 节已覆盖 FinCon/FinMem/FinAgent/TradingAgents 等"证据缺陷"集群；本节补 2025-2026 新工作。
> **FinRobot（2405.14767）与 QuantAgent（2402.03755）均为 2024 年论文**，已在现有索引与 OSS 对比表（HARNESS_EVOLUTION_RESEARCH.md §2.1）覆盖，本轮未见其 2025+ 新论文；下表的 QuantAgents（2510.04643）是名称相近的新后续工作。

| arXiv | 标题（日期） | 核心发现 | harness 启示 |
|---|---|---|---|
| [2606.03918](https://arxiv.org/abs/2606.03918) | Hedge-Bench: Benchmarking Agents on Hard, Realistic Tasks Pertaining to Financial Reasoning（2026-06-02） | 102 个对冲基金分析师真实岗位任务 + 显式专家推理轨迹，确定性评分（规避模型评审的噪声与循环性）；**前沿模型与 agent 得分全部低于 16%** | 开放式金融推理与专家工作的差距仍然极大；自研 harness 不应把 agent 定位为"分析师替代"，而是机械执行层 + 专家规则验证层 |
| [2603.22567](https://arxiv.org/abs/2603.22567) | TrustTrade: Human-Inspired Selective Consensus Reduces Decision Uncertainty in LLM Trading Agents（2026-03-23） | 识别 LLM 交易 agent 的"均匀信任"偏差（检索信息默认当事实、异构来源同等权重）；用多 agent 选择性共识（按语义/数值一致性动态加权、折价分歧来源）+ 确定性时间锚 + 测试时反思记忆调风险偏好，在高噪声回测（2024Q1/2026Q1）中把极端风险-收益行为校准回中风险中收益的人类对齐画像 | 来源信任加权应写进 harness 的信息摄取层；"检索到的数据都同等可信"是金融 agent 的默认缺陷 |
| [2603.17692](https://arxiv.org/abs/2603.17692) | Can Blindfolded LLMs Still Trade? An Anonymization-First Framework for Portfolio Optimization（BlindTrade，2026-03-18，ICLR 2026 FinAI Workshop） | 匿名化全部 ticker/公司名以检验记忆化偏差：4 个 LLM agent 输出评分+推理，推理嵌入建 GNN 图，PPO-DSR 策略交易；2025 YTD Sharpe 1.40±0.22（20 seeds），阴性对照验证信号合法性；扩展窗口显示**市场状态依赖**——高波动期有效、趋势牛市 alpha 衰减 | 金融 agent 评测应含匿名化对照（与 KTD-Fin 掩码互为印证）；自研评测管线应内置"蒙眼模式" |
| [2510.04643](https://arxiv.org/abs/2510.04643) | QuantAgents: Towards Multi-agent Financial System via Simulated Trading（2025-10-06，EMNLP 2025） | 把"模拟交易"引入多 agent 金融系统：模拟交易分析师/风控分析师/市场新闻分析师/经理经多轮会议协作，agent 同时获得真实市场表现与模拟交易预测准确性双重反馈。⚠️ 三年累计近 300% 收益为**作者自报、无独立复现**（按 A 节警示处理） | "模拟交易作为前置评估回路"的思路可迁移——自研 harness 可在回测引擎前先跑反事实模拟压缩策略迭代试错成本；收益声明不采信 |
| [2508.00828](https://arxiv.org/abs/2508.00828) | Finance Agent Benchmark: Benchmarking LLMs on Real-world Financial Research Tasks（2025-08） | 537 个专家编写问题（信息检索→复杂财务建模）、9 类金融任务，基于近期 SEC 文件，银行/对冲基金/PE 专家参与分类学设计；agentic harness 配 Google Search + EDGAR 工具；**最佳模型（OpenAI o3）仅 46.8% 准确率，平均每次查询成本 $3.79** | 真实金融研究任务最强模型仍不足半数准确率且单次成本不可忽略；自研评测必须成本+准确率联报（呼应 C 节 AI Agents That Matter） |

**TradingAgents-CN（灰色文献，无 arXiv 论文）**：社区维护的 TradingAgents 中文增强 fork（[github.com/hsliuping/TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN)，Apache-2.0），原生接 A 股数据源（Tushare/AkShare/BaoStock 多级降级链）与国内 LLM（阿里百炼/Qwen/DeepSeek/GLM/MiniMax），增加实时行情兜底、新闻质量过滤、报告导出与 Docker 部署；定位研究/教育、不执行交易。harness 启示：A 股本土化数据栈（多源降级链、国产模型适配）的工程价值大于学术价值；架构直接继承 TradingAgents 的 LangGraph 多角色拓扑，无新编排机制。

---

## 附：复现台账（诚实性声明）

- TradingAgents / FinMem / FinCon / FinRobot / FinAgent / TradingGPT 的 alpha 声明**全部仅作者报告，零独立复现**。
- TradingAgents 论文 Discussion 段与 FinMem 精确结果表在抓取中被截断（架构事实已验证）。
- Claude Code best practices 页面经 code.claude.com 镜像验证。
- 本索引所有条目检索日期：2026-08-21（初版）/ 2026-08-22（第二轮扩充）。
- 第二轮扩充（2026-08-22，E–I 节 25 篇）：全部经 arXiv API 当日检索验证标题/日期/摘要。特别标注：
  - [2604.21816](https://arxiv.org/abs/2604.21816) 的端到端任务成功率/延迟/成本为**作者明示的投影值**（模拟基准实测的只有 token 削减），引用时不得当作实测；
  - [2510.04643](https://arxiv.org/abs/2510.04643) 的 ~300% 收益为作者自报，无独立复现；
  - [2607.13683](https://arxiv.org/abs/2607.13683) 代码"录用后公开"，截至检索日未 released；
  - TradingAgents-CN 无学术论文，按灰色文献处理，事实取自其 GitHub 仓库与官网自述；
  - [2508.00828](https://arxiv.org/abs/2508.00828) 的 arXiv 编号为 2508（2025-08 提交），检索接口返回的日期字段与之矛盾，以编号为准记为 2025-08。
