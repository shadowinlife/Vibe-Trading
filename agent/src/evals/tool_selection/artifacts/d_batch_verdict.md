# D 批领域子代理试点 — 裁决文档（D1 quant-agent / D2 web-docs-agent）

> 日期：2026-08-28 ｜ 判官：qwen3.8-max + kimi-k3（DashScope，temp 0，judge_config_a5a8.yaml）
> 计划与预注册判据：`HARNESS_EVOLUTION_D_PLAN.md`（§5 冻结于首轮 judge 调用前）
> 证据：`d_routing_trace_*.jsonl`（v1 无 tag / v2 带 _v2）、`d_routing_probe_*_v2r1.jsonl`、
> `llm_judge_trace_*_d-quant-*[-v2].jsonl`、`llm_judge_trace_*_d-webdocs-*.jsonl`、
> `d_batch/coverage_report.json`、L2 实测记录（本文 §5）。

## 1.  TL;DR

**试点目标达成，附两个生产级发现。** 双判官路由口径下：目标域召回 99.1%、
误委派 3.57%、边界仲裁 85%、噪声地板 ≈0（探针一致率 1.0000/1.0000），
全部通过预注册门槛。代理内选择（W 组）与端到端合成（R4）因功效不足
**无法证明非劣**（点估计 −3.8pp/−5.0pp，CI 跨 0，McNemar 不显著）——
诚实结论为"未证有害"，非"证明无损"。L2 真实环境 4/4 场景通过，并暴露
两个 judge 协议测不到的生产事实：① 委派发生需要**编排侧政策**（AGENTS.md
路由规则），仅有子代理 description 不够；② 白名单按命名空间生效，存在
**跨命名空间泄漏**（详见 §5/§6）。

## 2. 确定性判据（C/T 组）

| # | 判据 | 阈值 | 实测 | 裁决 |
|---|---|---|---|---|
| C1 | 域内期望命中 ∈ 白名单 | 100% | 26/26（v1 白名单修订后） | ✅ |
| C2 | 白名单名全部存在于 B 后语料 | 100%（fail-loud） | 通过 | ✅ |
| C3 | 可见工具数 ≤ 15 | quant 11T / webdocs 3T（含技能 23 / 5） | ✅ |
| C4 | 路由语料全量 route 标注 | 100% | 198/198（direct 140 / quant 40 / webdocs 18） | ✅ |
| T1 | 子代理决策面描述 token 降幅 | ≥80% | quant −88%（11,238→1,366）、webdocs −98%（→243） | ✅ |
| T2 | 端到端 token 口径 | 报告项 | 试点为纯增量，主循环披露税不变；收益发生在子代理上下文 | 已声明 |

**覆盖审计实质发现**：AUDIT §8.1 草案白名单使 3/20 量化域 query 成为死角
（D06-007 alpha-zoo 技能、D07-006 pine-script、D07-007 vnpy-export）。
按预注册修订规则执行一轮修订（v0→v1，记录于定义文件 revision log）后
覆盖 20/20 + 6/6。

## 3. Level-R 路由判据（本批放行门槛）

### 3.1 v1 首轮（描述 v1）

| 指标 | qwen | kimi | 池化 |
|---|---|---|---|
| 总正确 | 183/198 | 183/198 | 92.4% |
| quant-agent 召回 | 38/40 | 38/40 | 95.0% |
| web-docs-agent 召回 | 17/18 | 16/18 | 91.7% |
| **R2 误委派** | — | — | **17/280 = 6.07% ❌（>5%）** |

失败分解：误委派集中在 D09 组合风险（VaR/deflated Sharpe → quant-agent）、
D08 衍生品、D05 技术形态——v1 description 把 quantlib 函数名（VaR/CVaR/
Black-Scholes）写进触发词，把邻域 query 吸了过来；欠委派 3 处为
pattern_recognition / 工作区文件读取未在 description 广告。

### 3.2 修订轮（预注册 §5.7 规则 4，唯一一轮）

修订内容（全部记录于定义文件 revision log）：
1. quant-agent description v2：撤下 quantlib 单机数学广告（能力保留在白名单），
   增邻域反触发（存量组合风险/期权永续策略/纯技术形态/事件驱动策略 → 主循环），
   补 K 线形态识别与回测工作区文件的正触发；
2. web-docs-agent description v2：补"工具怎么用"引导类触发；
3. 语料一致性修复（披露）：D06-108 v1 标签与基础语料 D09-001/002 自相矛盾
   （同为"算 VaR"却一东一西；两名判官一致判 direct 暴露了标注错误），
   替换为回测后语境的 quantlib 用例；
4. 子代理 prompt 补孪生仲裁句（工具执行 vs 技能教学）。

### 3.3 v2 结果（裁决依据）

| # | 判据 | 阈值 | 实测 | 裁决 |
|---|---|---|---|---|
| R1 | 目标域路由召回（池化） | ≥0.85 | **115/116 = 99.1%**（quant 79/80、webdocs 36/36） | ✅ |
| R2 | 误委派率 | ≤5% | **10/280 = 3.57%** | ✅ |
| R3 | 边界集仲裁 | ≥8/10 | **17/20 = 85%**（BND-003 独立 URL 抓取仍误委派 ×2，另 1 处 INVALID） | ✅ |
| R5 | 分模型一致性 | 报告项 | qwen 192/198、kimi 192/198（各 1/0 invalid） | 一致 |
| R6 | 噪声带 | 探针实测 | 8q×3r×2 判官，路由一致率 **1.0000 / 1.0000** → 带宽≈0，上述效应均可解释 | ✅ |

总路由正确率 96.97%（CI95 [94.8, 98.3]）。残留误委派（10 起）的构成：
D01-008/009（脚本化数据源 query → web-docs/quant 各一）、D05-009/010、
D08-006/007、D09-002/007、D12-008、BND-003×2——均为"策略/文档"语义
邻接的真模糊项，非系统性偏向。

### 3.4 W 组（代理内选择，功效受限如实申报）

| 面 | within | full | Δ | CI95 | McNemar |
|---|---|---|---|---|---|
| quant（v2 语料，池化 80 对） | 74/80 = 92.5% | 77/80 = 96.3% | −3.75pp | [−12.0, +4.1] | b/c=1/4, p=0.375 |
| webdocs（池化 32 对） | 18/32 = 56.3% | 19/32 = 59.4% | −3.1pp | [−25.8, +20.0] | b/c=0/1, p=1.0 |

- **W1（CI 下界 > −10pp）：未证得** —— CI 下界 −12.0pp。预注册 §5.1 已声明
  本组 MDE≈10pp：功效不足以证明非劣，亦未证有害（点估计在噪声带内、
  统计不显著）。**诚实结论：inconclusive，非 safe。**
- 失败分解：全部 miss 为 tool↔skill 孪生互换（alpha_zoo×alpha-zoo、
  backtest×strategy-generate、read_url×web-reader、read_document×doc-reader）。
  webdocs 域在**全表面上同样只有 59%** —— 孪生歧义是基础语料/描述面的
  既有属性，与子代理化无关。
- **协议局限（声明）**：Level-W 判官只看候选描述行，看不到子代理 prompt
  里的孪生仲裁句（v2 已补）；该修法的效果由 L2 承担验证，不在本组。

### 3.5 R4（端到端合成）

| 口径 | 组合 | 全表面直选 | Δ | CI95 |
|---|---|---|---|---|
| 保守合成（预注册公式：委派正确 ∧ 代理内正确） | 73/80 = 91.3% | 77/80 = 96.3% | −5.0pp | [−13.6, +3.1] |
| 增量试点口径（报告项：欠委派回退主循环直选） | 74/80 = 92.5% | 同上 | −3.75pp | — |

预注册门槛（CI 下界 > −10pp）**未证得**。裁决树字面走 R4-FAIL 分支，但
失败成分是 W 组功效不足 + 孪生突显，不是路由不可靠（R1/R2/R3 全过）。
按裁决树规则 5 的精神回查白名单构成：白名单覆盖 100% 无缺陷，问题在
候选描述面的孪生歧义——这不是白名单构成问题，故不按规则 5 回炉白名单。

## 4. 判官噪声与有效性

- 路由模板探针（v2 描述面）：两判官 8 query × 3 repeat 一致率均 1.0000；
- invalid 响应率：v2 路由 1/396（0.25%）；Level-W 0；
- 模板 hash：选择协议 b0e0fb11…（沿用冻结模板）；路由协议 24809ade…
  （钉于 trace 头 / D_PLAN §5 / 本文）；
- 已知混淆：D19 域孪生标注（基础语料既有）压低绝对值，两臂同受影响，
  对比口径不受影响。

## 5. L2 真实环境实测（opencode 1.18.23 + omo + 本仓库 vibe-trading MCP）

配置：`/tmp/d_l2/opencode.json`（项目级），子代理 permission =
`vibe-trading_*: deny` + 白名单逐个 allow（后匹配胜出语义实测成立）；
模型 alibaba-cn/qwen3.8-max（生产同款）。

| 场景 | 结果 |
|---|---|
| S1c 量化回测任务（写策略+回测茅台双均线） | ✅ 委派 quant-agent，真实回测完成（Sharpe −0.39 / 回撤 −20.43%，子代理自述已读回工件核验） |
| S2 搜索+网页阅读（央行货币政策报告） | ✅ 委派 web-docs-agent，真实抓取央行官网页面并摘要 |
| S3 边界（读本地源码文件） | ✅ 未委派，宿主 read/grep/glob 直接完成 |
| S4 非域（个股基本面） | ✅ 未委派，主循环直接 get_financial_statements |

**L2 关键发现（judge 协议测不到，只有真实环境能暴露）：**

1. **编排侧政策是委派的必要条件**。无 AGENTS.md 路由政策时，主代理
   （omo build）对明确域内的多步量化任务也选择自行完成（S1b 无委派），
   简单查询亦然（S1a）；加入 5 条路由规则后 4/4 场景行为全部符合设计。
   判官协议中 99% 的路由召回是在"强制二选一"下测得的 description 质量，
   生产中的委派倾向需要编排侧显式激活——与 Anthropic 工程 post
   （"teach the orchestrator how to delegate"）和 omo keyTrigger 机制一致。
2. **白名单按命名空间生效，存在跨命名空间泄漏**：S5 对抗性实测中，
   quant-agent 正确地调不到 `vibe-trading_web_search`（白名单生效），
   但改用了另一 MCP 服务器的 `websearch_web_search_exa`（permission 未覆盖
   该命名空间）。生产落地时 render_config 需把 deny glob 扩展到全部
   非白名单 MCP 命名空间（mymain 已有 `search_mcp_*` deny 先例可复用）。

## 6. 裁决树映射与结论

| 判据 | 结果 | 树分支 |
|---|---|---|
| C1-C4 | 全过 | 进入 LLM 阶段 ✅ |
| R1 ✅ / R2 ✅ / R3 ✅ / R6 噪声≈0 | 过 | 放行门槛满足 |
| W1 / R4 | 未证得非劣（功效不足，点估计在噪声带，未证有害） | 规则 5 回查：白名单无缺陷，不回炉 |

**结论**：D1/D2 试点**有条件通过**——
1. 路由层（本批核心风险，C 批失败点的移植面）在描述 v2 + 编排侧政策下可靠；
2. 子代理内决策面达标（C3/T1）且能力无缺口（C1）；
3. 代理内孪生突显是真实的残余风险（W1 未证得非劣），其修法（prompt 仲裁句）
   已入 v2 prompt，但 L2 样本量不足以统计验证——**D4 铺开前的主循环收敛
   （撤下域工具）不应执行**，直到孪生仲裁在更大样本或生产遥测中闭合；
4. 生产落地前置条件（D4 配套）：编排侧路由政策入配置 + render_config 的
   deny glob 扩展为全命名空间。

**处置（已执行，2026-08-28）**：D1/D2 定义文件 + 提示词已落地生产
（mymain `43cf7624` + `6f61a2c5`：`OpencodeAgent/config/subagents.json` +
`prompts/` + render_config 扩展 + 编排侧路由政策入生产 AGENTS.md；
落地冒烟证据见 `d_l2_rendered/`）。落地时两处深化：deny 覆盖扩展到 OMO
插件内建命名空间（websearch/context7/grep_app/lsp）；prompt 引用改为渲染时
colocation（`{file:}` 按配置文件目录解析，探针实证）。D4 铺开仍暂缓，
待孪生仲裁补强证据（生产遥测）。

## 7. 产物索引

- 定义：`d_batch/subagent_quant_agent.yaml`、`subagent_web_docs_agent.yaml`
  （白名单 + 路由 description v2 + revision log）
- 提示词：`d_batch/prompts/quant_agent.md`、`web_docs_agent.md`
- 构建/审计：`d_batch/build_d_corpora.py`（覆盖审计 + 语料生成）、
  `coverage_report.json`
- 协议与执行：`d_batch/d_routing_protocol.py`、`run_d_judge.py`、
  `d_batch_report.py`
- 语料：`queries_d_expansion.yaml`（+40）、`queries_d_{quant,webdocs,
  routing}.yaml`（构建产物）
- 轨迹：`artifacts/d_routing_trace_{qwen3.8-max,kimi-k3}[_v2].jsonl`、
  `d_routing_probe_*_v2r1.jsonl`、`llm_judge_trace_*_d-*[-v2].jsonl`
- L2 配置与轨迹：`/tmp/d_l2/`（opencode.json、AGENTS.md、s1c-s5.jsonl）
  ——⚠️ /tmp 易失，如需归档应复制入 artifacts（本文已记录结论）
