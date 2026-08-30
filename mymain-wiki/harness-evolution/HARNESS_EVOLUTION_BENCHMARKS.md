# Harness 演进研究 — 评测基准全景目录

> 维护者：shadowinlife ｜ 初版：2026-08-22 ｜ 检索验证日期：2026-08-22
> 用途：为"自建/迁移 harness 替换 opencode + oh-my-openagent + MCP 金融工具"提供能力回归/增益的测量标尺。
> 配套文档：`HARNESS_EVOLUTION_RESEARCH.md`（主调研）· `HARNESS_EVOLUTION_PAPERS.md`（论文索引）

---

## A. 金融 / Quant 侧基准

### A1. 综合金融 LLM 基准（QA 为主）

| 基准 | 测什么 | 格式与规模 | 可得性 | Agentic? | 代表结果 |
|---|---|---|---|---|---|
| **FinBen / FinBenBench**（TheFinAI，NeurIPS 2024）[arXiv:2402.12659](https://arxiv.org/abs/2402.12659)，前身 PIXIU [arXiv:2311.10723](https://arxiv.org/abs/2311.10723) | 金融全能力：信息抽取、文本分析、QA、生成、风控、预测、**决策（含股票交易任务）** | 35-36 数据集 / 23-24 任务 / 7 大类，按 Cattell-Horn-Carroll 认知理论分三档难度 | 开源（评测脚本随 PIXIU 仓库发布；TheFinAI 组织仓库近期有迁移） | 基本 single-shot QA；仅 forecasting/trading 子任务涉及时序决策 | 首发评 15 个 LLM：GPT-4 在量化/抽取/数值推理/股票交易领先，Gemini 在生成/预测领先；两者在复杂抽取与预测上都弱 |
| **FLUE**（Shah et al. 2023，FinBERT 论文）[ACL Anthology](https://aclanthology.org/2022.emnlp-main.148/) | 金融语言理解：情感（FPB、FiQA）、新闻标题分类、NER（贷款协议）、结构边界检测、QA | 5 个任务，数据集均在 HuggingFace | 完全开源 | 否 | FinBERT 系基线；现主要作为"地板级"回归测试 |
| **FinEval**（上海财大）[GitHub](https://github.com/SUFE-AIFLM-Lab/FinEval) | 中文金融知识：金融/经济/会计/证书四学科 MCQ | 26,000+ 题 | 开源数据集 + 评测代码 | 否 | GPT-4 约 68.6%（DISC-FinLLM 报告） |
| **CFLEB**（复旦 BBT）[arXiv:2302.09432](https://arxiv.org/abs/2302.09432)，[GitHub](https://github.com/ssymmetry/BBT-FinCUGE-Applications) | 中文金融 NLU+生成：摘要、关系抽取、情感、事件抽取等 | 6 个数据集 | 开源，DISC-FinLLM 提供一键评测脚本 | 否 | Baichuan-13B+LoRA 平均 40% |
| **CFLUE**（阿里云+苏州大学）[arXiv:2405.10542](https://arxiv.org/abs/2405.10542)，[GitHub](https://github.com/aliyun/cflue) | 中文金融：知识评估 + 应用评估两维 | 38 个子任务 | 开源 | 否 | GPT-4 zero-shot 约 60%+，金融专用模型反而普遍偏低 |
| **CFinBench**（华为+南洋理工）[arXiv:2407.02301](https://arxiv.org/abs/2407.02301) | 中文财经：学科基础/资格认证/从业实践/法律法规 | 99,100 题 | 开源 | 否 | Yi-1.5-34B、GPT-4、Qwen-72B 领先 |
| **FinanceBench**（Patronus AI）[arXiv:2311.11944](https://arxiv.org/abs/2311.11944) | 基于真实 10-K/10-Q 的开卷金融问答（需算 EBITDA、PE 等复合指标） | 10,000 题，公开 150 题（HF: PatronusAI/financebench） | 开源（公开子集） | 否（很适合配 RAG/工具） | GPT-4+检索约 19-30%——金融 RAG 的事实标准测试集 |
| **FinMMEval**（CLEF 2026 Lab）[arXiv:2602.10886](https://arxiv.org/abs/2602.10886) | 三任务：①多语言金融考试 QA（CFA/EFPA/CPA，5 语言）②PolyFiQA 多语言短答 QA ③**金融决策**（BTC+TSLA 每日 long/flat/short） | Task2 决赛 256 题；Task3 序列决策 | 数据集与评测资源公开（CLEF 赛制） | Task3 多步决策，其余 single-shot | Task3 的 DDPG 系统在 TSLA 上 54.96% vs 买入持有 16.45% |
| **FinRAGBench-V** [arXiv:2505.17471](https://arxiv.org/abs/2505.17471) | 多模态金融 RAG + 视觉引用溯源 | 双语检索语料（60,780 中文页 + 51,219 英文页）+ 7 类问题 | 开源 | 否（评 RAG 管线） | 澜舟图表 RAG 69.6%→90.7% |
| **BizFinBench**（同花顺） | 真实金融业务：数值计算、财报解析、行情异动溯源等 9 类任务 | 首批开源 6,781 条中文样本 | 部分开源 | 否 | 复杂跨概念场景准确率显著下降 |
| **FLAME**（人大版 + JPMorgan 版 [arXiv:2506.15846](https://arxiv.org/abs/2506.15846)） | 考试认证（CPA/CFA/FRM 等 14 类）+ 业务场景 | JPMorgan 版：20 领域/23 数据集，含 leaderboard；论文附 FLUE/FLARE/FinBen/CFBenchmark 完整对比表 | JPMorgan 版开源 | 否 | Baichuan4-Finance 93.6%（厂商自报） |

> **关于 BigBench-Finance**：经查证（google/BIG-bench 仓库 + 多篇金融评测综述），**不存在独立的 "BigBench-Finance" 基准**。最接近"金融版 BIG-bench"定位的是 FLAME（JPMorgan）与 FinBen。

### A2. CFA 考试类基准

| 基准 | 格式规模 | 可得性 | 代表结果 |
|---|---|---|---|
| **"Can GPT models be Financial Analysts?"**（Callanan et al.，JPMorgan）[arXiv:2310.08678](https://arxiv.org/abs/2310.08678)，[代码](https://github.com/e-cal/gpt-cfa) | 5 套 L1 + 2 套 L2 官方 mock 卷 | 代码开源 | ChatGPT 全挂；GPT-4 在 FS/CoT 下可过 L1/L2 |
| **"The State of the Art of LLMs on CFA Exams"**（Mahfouz et al.，EMNLP 2024 Industry）[ACL Anthology](https://aclanthology.org/2024.emnlp-industry.80.pdf) | 三级全考，MCQ + L3 Essay，14 模型 | 论文公开 | 闭源旗舰稳过 L1/L2，**无模型过 L3**（essay 瓶颈，GPT-4o essay 仅 46.2） |
| **推理模型复测**（2025-12）[arXiv:2512.08270](https://arxiv.org/abs/2512.08270) | 980 题：3 套 L1 + 2 套 L2 + 3 套 L3 | 论文公开 | **格局已变**：Gemini 3.0 Pro（L1 97.6%）等 6 个推理模型全过三级 → 作为区分度基准已接近饱和 |
| **CFA-Based Benchmark Study** [arXiv:2509.04468](https://arxiv.org/abs/2509.04468) | 1,560 道官方 mock MCQ + 课程 RAG 管线 | 论文公开 | o1/o3-mini 可过三级 MCQ 部分 |

### A3. Quant-Agent / 交易决策类基准（2024-2026 重点）

| 基准 | 测什么 | 格式与规模 | 可得性 | Agentic? | 代表结果 |
|---|---|---|---|---|---|
| **TradingAgents**（Tauric Research）[GitHub](https://github.com/TauricResearch/TradingAgents)（99k★），[arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | 多智能体交易框架 + 回测评估 | 框架自带回测（2024 Q1 美股科技股），需 Finnhub 等 API | 完全开源 | **是** | 论文回测：累计收益 ≥23.21%、夏普 ≥5.60（仅作者报告） |
| **Alpha Arena**（nof1.ai）[官网](https://nof1.ai/)，开源复刻 [etrobot/open-alpha-arena](https://github.com/etrobot/open-alpha-arena)（597★） | **真金白银**实盘交易竞技：统一 harness 每 2-3 分钟喂数值行情 | S1（2025-10）：6 模型 × $10K 加密永续；S1.5：美股 8 模型 × 4 轮 | 官方邀请制（打榜型）；开源复刻可 paper trading | **是** | S1：Qwen3-Max +22.32% 夺冠；GPT-5 -62.66%、Gemini -56.71%。S1.5：32 组仅 6 组盈利。**静态基准分数与实盘 PnL 几乎不相关** |
| **QuantBench**（FITEE 2025）[论文](https://jzus.zju.edu.cn/openiptxt.php?doi=10.1631/FITEE.2500280)，[GitHub](https://github.com/SaizhuoWang/quantbench) | 工业级量化投资全流程：数据→因子挖掘→组合构建→回测 | 全 pipeline 平台 | 开源（研究原型） | 视接入方式 | 揭示持续学习、分布漂移等方向 |
| **QuantEval** [arXiv:2601.08689](http://arxiv.org/abs/2601.08689) | 量化三维：知识 QA + 量化数学推理 + **策略编码（CTA 风格真实回测执行）** | 三维任务集，公开确定性回测配置 | 开源 | 半 agentic | SOTA 模型与人类量化专家在推理和策略编码上差距显著 |
| **BacktestBench**（KDD 2026） | LLM 自动化策略回测：指标计算/标的精选/策略选择/参数确认 | **1.8 万 QA 对**，A 股三大交易所 654 万条真实日线（2020-2025），强制"无未来函数、T-1 信号" | 开源（含多智能体调度与评估源码） | 配套 AutoBacktest 多智能体框架 | 逆向工程构造；直击 look-ahead bias 幻觉 |
| **TraderBench** [arXiv:2603.00285](https://arxiv.org/abs/2603.00285) | 对抗性资本市场中的 agent 鲁棒性：知识检索 + 分析推理 + **期权交易（P&L/Greeks 精度）** + **加密交易（4 级市场操纵扰动）** | ~50 任务，13 模型，基于 A2A + 6 个 MCP 金融数据服务器 | 论文公开 | **是** | Gemini-3-Pro 64.3 居首；8/13 模型在对抗扰动下不自适应；extended thinking 对检索 +26 分但对交易 +0.3 |
| **InvestorBench** [arXiv:2412.18174](https://arxiv.org/abs/2412.18174) | 金融决策：单股票/加密/ETF 交易 | 多源开源数据（Yahoo、SEC EDGAR），13 个 LLM 骨干 | 开源（含环境+记忆型 agent 框架） | **是** | 专有模型股票任务占优；领域微调无决定性优势 |
| **DeepFund**（港科广 DIAL，NeurIPS 2025）[GitHub](https://github.com/HKUSTDial/DeepFund)（293★） | LLM 基金经理：多智能体工作流实时基金管理 | 实时竞技场 + 历史回测两种模式 | 开源 | **是** | AI Agent 2025 最佳开源项目 |
| **AI-Trader**（HKUDS）[arXiv:2512.10971](https://arxiv.org/abs/2512.10971)，[GitHub](https://github.com/HKUDS/AI-Trader)（21.5k★） | 首个全自动、实时、无数据污染的金融交易基准：美股 + A 股 + 加密 | 实盘环境 + 交易 agent 完全解耦；MCP 工具接口 | 开源（回测可本地跑） | **是** | GPT-5 美股仅 +1.56%（跑输 QQQ），A 股普遍亏损；MiniMax-M2 +9.56%/Sortino 4.42 最佳。**通用智能 ≠ 交易能力** |
| **StockBench** [arXiv:2510.02209](https://arxiv.org/abs/2510.02209) | 多股票、多月连续交易决策（防污染：2025-03~07 数据且持续更新） | DJIA 前 20，82 交易日，$100K 起始 | 开源 | **是** | 多数 LLM agent 跑不赢等权买入持有 |
| **AMA（Agent Market Arena）** [arXiv:2510.11695](https://arxiv.org/abs/2510.11695) | 终身、实时、多资产（TSLA/BMRN/ETH/BTC）交易 | 统一协议 + 专家核验信息流 | 开源、持续运行 | **是** | 架构比骨干模型更影响收益 |
| **KTD-Fin** [arXiv:2605.28359](https://arxiv.org/abs/2605.28359) | 记忆控制 + 收益归因：四级数据脱敏 + Barra 风格归因 | CSI300，2024-01~2026-04，Qlib 执行，10 个前沿 LLM vs 18 个 Alpha9 ML 基线 | 开源（全发布） | **是** | 脱敏后 LLM 收益**基本来自市场/风格被动暴露，选股 alpha ≈0 或为负** |
| **FinEvo-Bench** [arXiv:2608.06144](https://arxiv.org/abs/2608.06144)（2026-08，最新） | 自进化 agent 在专业金融工作流中的纵向基准 | 纵向任务设计 | 论文刚发布 | **是** | — |
| **TradeTrap** | 交易 agent 对抗性压力测试（市场智能/策略/组合账本/执行四模块） | 闭环历史回测 + 受控扰动 | 论文公开 | **是** | 单点小扰动可传播为极端集中、失控敞口 |
| **FinChain** [arXiv:2506.02515](https://arxiv.org/abs/2506.02515) | 可验证的金融 CoT 推理（符号化中间步骤检查） | 符号基准 | 开源 | 否（测推理链质量） | FinMMEval 团队出品 |

### A4. FinAgent / FinRobot（是框架，不是独立基准）

- **FinAgent**（多模态交易基础模型）与 **FinMem**（记忆增强交易 agent）：各自论文用单资产回测评估，无标准化公开题库。
- **FinRobot**（AI4Finance）[arXiv:2405.14767](https://arxiv.org/abs/2405.14767)：四层架构金融 agent 平台，评估散见各模块 demo。
- **意义**：这类"框架自带回测"可作为 harness 集成测试（验证工具链没接坏），但**不能**作为横向对比标尺——没有统一题库和防污染设计。

---

## B. 通用 Agent 基准

| 基准 | 测什么 | 格式与规模 | 可得性 | 已发布 harness/模型数字 |
|---|---|---|---|---|
| **SWE-bench Verified** [榜单](https://www.swebench.com/verified.html)，[arXiv:2404.11072](https://arxiv.org/abs/2404.11072) | 真实 GitHub issue → 补丁（12 个 Python 仓库，测试真实执行） | 500 个人工核验 issue | **完全开源**，Docker 化评分；另有 mini-SWE-agent 纯 bash 赛道 | 第三方追踪（2026-04/05）：Augment Code scaffold 72.0%、**OpenHands+CodeAct v3 68.4%**、SWE-agent v1 43.2%、Agentless 34.2%；**同一底座模型换 scaffold 可差 15pp+**——"换 harness 是否掉能力"的直接测量工具 |
| **terminal-bench 2.0/2.1 + Harbor** [GitHub](https://github.com/harbor-framework/harbor)（4.5k★） | 真实终端操作：编译、装依赖、起服务、调试、安全修复 | 2.0 = 89 个双重验证任务；2.1 加持续验证 | **完全开源**；Harbor 官方 runner 内置 Claude Code / OpenHands / Codex CLI 适配 | 各厂商随模型发布：Ornith-1.0-35B 64.2%（TB 2.1）、Qwen3.5-35B 41.4% |
| **GAIA / GAIA2** [HF 榜单](https://huggingface.co/spaces/gaia-benchmark/leaderboard)，[arXiv:2311.12983](https://arxiv.org/abs/2311.12983) | 通用助手：推理+多模态+网页浏览+工具使用 | 466 题三档（公开 165，300 题答案保留）；GAIA2：1000 场景 + Meta ARE 框架 | 公开 split 可本地跑 | 人类 92% vs GPT-4+插件 15%；开源方案 Auto-Deep-Research（HKUDS）曾夺总榜第三、开源第一 |
| **tau-bench / tau²-bench**（Sierra）[GitHub](https://github.com/sierra-research/tau2-bench)（1.8k★），[tau arXiv:2406.12045](https://arxiv.org/abs/2406.12045)、[tau² arXiv:2506.07946](https://arxiv.org/abs/2506.07946) | 客服式工具调用 + 多轮对话 + 政策遵从；tau² 双控（用户也能调工具） | tau²：2,285 程序化生成任务，114 平衡测试集；指标 pass^k（测可靠性） | **完全开源**，pip 安装，仅需 LLM API | Anthropic 官方引用；已成为基座模型 tool-call 能力标配 |
| **AgentBench**（THUDM）[arXiv:2308.03688](https://arxiv.org/abs/2308.03688) | 8 环境：OS/数据库/知识图谱/卡牌/猜谜/家居/网购/网页 | 8 个交互环境 | 开源（Docker 环境较重） | 27 个 LLM 评测；现多被更新基准取代 |
| **OSWorld** [榜单](https://os-world.github.io/)，[arXiv:2404.07972](https://arxiv.org/abs/2404.07972) | 真实虚拟机里的跨软件电脑操作 | 369 任务，30 步上限，像素级验证 | 开源（需 VM 基础设施） | 发布时 12.24%（人类 72.36%）→ 2025-12 Agent S3 72.6% 首超人类 → 2026-07 实在智能 90.2% |
| **DeepResearch Bench**（中科大）[arXiv:2506.11763](https://arxiv.org/abs/2506.11763) | 深度研究 agent 端到端：信息收集→分析→长报告 | 100 个博士级任务（中英各 50），22 领域；RACE + FACT 评估 | **开源** | 钉钉 DeepResearch 48.49、百度千帆登顶（2026-02）、DuMate 58.03（2026-05） |
| **BrowseComp**（OpenAI）[博客](https://openai.com/index/browsecomp/)，[arXiv:2504.12516](https://arxiv.org/abs/2504.12516) | 深度浏览：翻数十上百网站找"难找且相互纠缠"的信息 | 1,266 题，答案加密防爬（simple-evals） | **开源** | 人类训练师限时仅解 29.2%；o1 9.9%、OpenAI Deep Research 51.5% |
| **HLE（Humanity's Last Exam）**（CAIS + Scale AI）[官网](https://lastexam.ai/) | 100+ 学科、硕博级封闭学术题 | 2,500 题，10-14% 多模态 | **开源**（HF）；2026-02 阿里发布 HLE-Verified 修正版 | 首发全部前沿模型 <10% → Gemini 3 Pro 37.5%（带工具 41%）。**测模型不测 harness**，适合"带工具 vs 不带工具"消融 |

**补充**：BFCL v4（函数调用事实标准）、MCPMark（127 个 MCP 专家任务）、SWE-bench Pro（1,865 题企业级）、MAESTRO（多智能体系统可靠性评测，[arXiv:2601.00481](https://arxiv.org/abs/2601.00481)）。

---

## C. 1-2 人团队可行性分级

### ✅ 第一梯队：一周内可跑通，成本 <$100/轮（推荐基线组合）

| 基准 | 成本/工作量 | 说明 |
|---|---|---|
| **tau²-bench**（+ tau-bench） | 仅 LLM API 费（一轮约 $5-30），pip 即装 | 工具调用+政策遵从+多轮可靠性（pass^k），与 MCP 工具链场景最对齐；**强烈建议入选** |
| **SWE-bench Verified** | Docker + API 费（500 题全量约 $50-300）；用 mini-SWE-agent 或 OpenHands 现成 harness | 第三方 harness 数字最丰富；**强烈建议入选** |
| **terminal-bench 2.0** | 89 任务，Docker 本地跑，Harbor 一条命令 | 测终端/环境操作，coding harness 核心能力 |
| **FinEval / CFinBench / CFLUE**（中文金融知识） | 纯 MCQ，几十美元 API 费 | 便宜的知识层回归测试 |
| **CFA mock 卷**（e-cal/gpt-cfa 或 2512.08270 的 980 题协议） | 小题库，<$20 | 英文金融推理快速对照 |
| **FinanceBench 150 公开题** | <$30（配 RAG/工具链） | 直接测"harness+金融工具"端到端问答质量 |

### 🟡 第二梯队：可跑但需搭环境/持续投入（选 1-2 个）

| 基准 | 成本/工作量 | 说明 |
|---|---|---|
| **StockBench / InvestorBench / DeepFund（回测模式）** | 开源 + 免费行情数据，一轮数小时 | 金融决策/P&L 层，防污染设计好；**金融侧最推荐的 agentic 选项** |
| **BacktestBench / QuantEval** | 开源，A 股数据自带 | 直接命中"量化策略代码生成+回测"核心工作流（与 Vibe-Trading 场景同构） |
| **TradingAgents 回测** | 需 Finnhub 等 API key，中等 | 多智能体协作回归测试 |
| **open-alpha-arena**（paper trading） | 免费（ccxt 行情），跑几天 | 低成本体验"实盘式"闭环 |
| **GAIA 公开 165 题** | 需浏览+多模态+代码执行，一轮 $50-150 | 通用助手能力总检 |
| **BrowseComp 子集** | 数据集免费，需长时浏览 agent + 搜索 API | 抽 100-200 题测浏览耐力 |
| **DeepResearch Bench** | 100 个长任务，token 成本高 | 若重视 deep research 场景再上 |

### 🔴 第三梯队：仅打榜 / 基础设施过重（引用公开数字即可）

- **Alpha Arena 官方**（真金白银、邀请制）——用公开结论 + open-alpha-arena 复刻
- **AI-Trader 实盘模式 / AMA**（需 7×24 实时市场接入与持续运维）——可跑历史回放部分
- **OSWorld**（VM 农场，基础设施和 token 都重）
- **AgentBench**（8 套环境 Docker 编排重，部分环境过时）
- **GAIA 私有 300 题 / FinMMEval 决赛题**（仅提交打榜）
- **HLE 全量**：测模型不测脚手架，建议只引用官方数字

---

## D. 推荐评测组合（3-5 个，全部可自跑）

1. **tau²-bench** —— 工具调用/多轮/政策遵从（通用，便宜，有厂商对照数字）
2. **SWE-bench Verified** —— 代码 agent 核心能力（有大量 harness 横向数字，能直接回答"换 harness 掉不掉能力"）
3. **terminal-bench 2.0** —— 终端/环境操作（Harbor 开箱即用）
4. **StockBench 或 BacktestBench** —— 金融决策/量化工作流（防污染、开源、与业务同构）
5. **FinanceBench(150) + FinEval** —— 金融知识/RAG 端到端（便宜的回归地板）

**关键方法论提醒**（本次调研实证）：
- 同一底座模型换 harness，SWE-bench Verified 可差 **15pp+**（SWE-agent 43.2% vs Cline 59.8%，Sonnet 4.5）——harness 对比必须固定底座模型。
- 金融 QA 分数与实盘交易能力**几乎不相关**（Alpha Arena、AI-Trader、StockBench 一致结论）——两侧基准都要有，不能只测 QA。
- 注意基准 exploit 风险：已有研究在 SWE-bench Verified（conftest.py 作弊）、terminal-bench（替换系统依赖骗过 verifier）等 8 个基准上找到可行作弊路径——自跑时保留官方验证器并抽查轨迹。
- 成本+准确率必须联报（AI Agents That Matter 协议；Finance Agent Benchmark 单次查询 $3.79 的前车之鉴）。
