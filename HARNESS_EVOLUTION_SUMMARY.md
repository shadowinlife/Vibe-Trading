# HARNESS 演进工程 · 总结（Summary）

> 周期：2026-08-21 ~ 2026-08-28 ｜ 状态：**收官快照（D 批已于同日落地生产，见 §5）**
> 本文是 `HARNESS_EVOLUTION_*` 文档族的总收口：问题 → 调研 → 方案 → 实测 → 裁决。
> 各方案的过程证据见文末引用清单；本文数字均可在对应裁决文档中复核。

---

## 1. 要解决什么问题

生产部署形态 = **opencode + vibe-trading MCP**（mymain 分支托管）。核心矛盾是
**披露税（disclosure tax）**：每个规划轮都要把全部工具/技能描述注入模型上下文——

| 暴露面 | 规模 | 每轮披露税 |
|---|---|---|
| VT 内置 agent（CLI/Web/API） | ~106 工具（无 key） | ~74k token |
| MCP 客户端（opencode 等） | 74 工具 + 90 技能 | ~52k token |
| swarm worker | ~10-15 工具/角色（硬白名单） | 天然受控 |

论文证据（PAPERS §F）：工具选择准确率在 **25-30 个可见工具后退化、~100 个崩塌**。
审计（CAPABILITY_AUDIT v2）在此之上定位了具体病灶：撞名（sentiment 工具×技能、
sec-edgar 词序差）、门控不对称（agent 侧注册时门控 vs MCP 侧恒注册调用时才失败）、
技能双暴露、内部名×MCP 名漂移、同族工具无仲裁规则。

**目标**：在 SOTA 开源模型（qwen3.8-max / kimi-k3 这一档）+ 生产形态下，
把每次决策的工具面收敛到舒适区，同时**路由准确率不降**、能力可达性无损。

---

## 2. 调研了哪些材料

### 2.1 论文（PAPERS.md，9 大类索引）

| 类别 | 关键内容 |
|---|---|
| A. LLM 交易 Agent 框架 | 现有框架的证据缺陷 |
| B. 评测基准批判（2026） | 静态基准失真，驱动我们自建评测 |
| C/E. 通用 harness 与上下文工程 | 可迁移设计原则 |
| D/F. MCP 工具面经济学 + 工具扩展律 | 25-30 退化 / ~100 崩塌 / 懒加载 −95% token / 平均 7 工具舒适区 |
| G/H/I. 记忆、多代理编排、金融量化增量 | 子代理与编排的设计输入 |

C 批前对三篇锚点论文做了**全文机制核实**（非只看摘要）：BoR 短名单
（arXiv 2605.24660，7.4 工具达 90% 覆盖）、Tool Attention 两阶段懒加载
（2604.21816，幻觉门 + schema 按需注入）、LiveMCPBench（2508.01780，50%
失败源于检索错误）。召回阈值设定另有四源（2607.15593 / 2511.01854 /
2510.17843 / ToolRet）。

D 批前做了委派/路由专题调研：DecisionBench（2605.19099——**路由保真必须
直接测量，且投递通道主导描述内容**）、EARS（2606.18668——结构化交回协议
+10.4pp）、MAST（2503.13657——14 种多代理失败模式）、CADMAS-CTX、
Provenance Paradox（自夸式描述路由差于随机）等。

### 2.2 开源实现普查（源码级）

- **C 批**（工具召回/懒加载）：OpenBB MCP server、mcp-toolgate、OmniMCP、
  ToolLLM retriever、AnyTool/COLT/ToolRet、Anthropic tool search、
  OpenAI tool_search——结论：生产系统普遍放弃动态加载（破坏缓存），
  召回载荷趋简。
- **D 批**（子代理机制）：opencode 配置 schema + task.txt 一手源码
  （description 即路由信号、permission 后匹配胜出、subagent_depth=1 防递归）、
  oh-my-openagent 代理元数据（keyTrigger / useWhen / avoidWhen）、
  Claude Code 子代理官方文档（description = "when to delegate"）、
  OpenAI Swarm/Agents SDK 编排指引。
- **架构层**（RESEARCH.md）：32 个候选框架源码级调研 → 裁决保留
  `opencode + omo + MCP` 布局。

### 2.3 工程实践与仓内先验

Anthropic multi-agent research system 工程 post（委派教学四要素、工具描述
重写使任务完成时间 −40%）；仓内 30 个 swarm preset 的 prompt 解剖
（人设→任务→输出契约→反捏造规则的结构沉淀）；CAPABILITY_AUDIT 的
K1-K25 / G1-G10 / Q1-Q19 问题编号体系与 §7.2 路由决策表。

---

## 3. 尝试了哪些方案 & 各自测试结果

> 全部方案共用同一评测纪律：**判据预注册（实验前冻结、禁止事后改阈值）**、
> LLM-judge 双判官（qwen3.8-max + kimi-k3，temp 0）、确定性探针测噪声地板、
> 黄金 trace 归档可复跑。E1 评测集 = 158 条金融域 query（19 域），
> D 批扩至 198 条。

### 3.1 总表

| 方案 | 假设 | 注入阈值（预注册） | 实测 | 裁决 |
|---|---|---|---|---|
| **A1-A4** 描述重写（P0 四项） | 措辞改善路由 | 4 家族 LLM-judge 配对 McNemar | 池化 p=**0.885**，各模型 Δ∈±1.3pp；基线已 0.88-0.94 到顶 | ❌ **划掉**（SOTA 下路由中性） |
| **A5** 双暴露治理决策 | 路由价值 | — | 路由价值到顶 | 🔄 重定位为代币税价值 |
| **A6** 内外名映射表 | 移植覆盖 | 映射表 + 技能文档统一 + strict gate | 未标注引用=0，gate PASS | ✅ **完成**（非路由价值） |
| **A7** P1 描述批量修订 ×6 | 温和改善 | 4 判据（靶点/全集/回收/非劣） | 仅 2/4：全集 +3.48pp p=0.027 属守卫指标，靶点 p=0.629 | ❌ **划掉，已回滚**（弱/局部效应） |
| **A8** P2 描述批量修订 ×7 | 长尾改善 | 同上 | 全集**显著回归**（lenient 池化 p=0.012） | ❌ **否决，已回滚** |
| **B1-B5** 暴露面工程（注册时门控） | 裁掉无用工具面不损路由 | C1 池化非劣 CI 下界 >−5pp；C6 披露税降幅 | C1 **PASS**：Δ=+1.05pp，CI [−2.03,+3.75]；C6 实测 −5,100 tok/轮（**−17.9%**，74→59 工具），幻觉调用 0 起 | ✅ **放行**（经用户按实测数据裁决；暂缓上游） |
| **C1-C3** 路由层（search_tools 元工具 + 披露层级 + 路由元规则） | 懒加载砍掉 79% 披露税且端到端不降 | R1 池化非劣 CI 下界 >−5pp | 检索本身达标（recall@7 **0.937**、披露税 **−79%**），但端到端 4 种配置**全部显著更差**（Δ −11.5pp ~ −33.6pp）——"何时该搜索"决策不可靠 | ❌ **全部回滚**，思路标记失败 |
| **D1/D2** 领域子代理试点（quant-agent / web-docs-agent） | 固定小白名单落在舒适区，无需搜索决策 | R1 路由召回 ≥0.85；R2 误委派 ≤5%；R3 边界 ≥8/10；W1/R4 非劣 CI 下界 >−10pp | **R1 99.1% ✅、R2 3.57% ✅、R3 85% ✅**、噪声地板≈0 ✅；W1/R4 **未证得非劣**（Δ=−3.75/−5.0pp，CI 跨 0，功效不足，未证有害） | ⚠️ **有条件通过**；**已落地生产**（mymain，见 §5），D4 铺开待孪生仲裁遥测 |

### 3.2 基础设施与辅助项（E/F 流）

| 项 | 结果 |
|---|---|
| **E1** 工具选择评测集 | ✅ 158 query × 19 域，版本化资产；D 批扩至 198 |
| **E2** 基线实测方法学 | ✅ 4 家族配对协议 + McNemar/Wilson + 噪声探针；成为 A/B/C/D 全部裁决的度量地基 |
| **E4** 描述变更回归 | 🟡 prompt hash manifest 基建在位（E4 前提），但 A7/A8 回滚后回归对象消失，未单独立项收尾 |
| **F1** 内部工具面完整盘点 | ✅ 以 build_registry() 运行时输出为权威 |
| **B 批对抗评审** | ✅ 5 通道全 PASS（零阻塞）；唯一遗漏（探针测试未暂存）已补 |

### 3.3 速度观察（D 批附带实测）

同一批 40 个量化任务、同模型，子代理小面 vs 全量面：每次决策读入
10,974 → 1,548 token（**−86%**），决策延迟中位 13.8s → 9.3s（**−33%**），
p90 46s → 29s。委派本身增加一次路由决策（5-7s）+ 任务书生成 + 汇总——
**轻任务净亏 5-10 秒，重任务（8+ 轮工具调用）净赚 40-60 秒，卡顿尾延迟改善明显**。

---

## 4. 关键教训（写进路线图的校准项）

1. **描述措辞不是杠杆**（A 批证伪）：SOTA 模型 + 全表面呈现下基线已到顶
   （0.88-0.94），描述级改动路由中性（池化 p=0.885）。
2. **"裁掉没用的"无损，"藏起来让模型找"有损**（B vs C 对照）：门控裁剪不需要
   模型配合，零路由成本；"何时该搜索/何时该委派"这类额外决策必须被显式设计
   （D 批靠编排侧路由政策解决）才有救。
3. **子代理路线成立但非银弹**：路由可靠（99.1% 召回），代理内选择未见改进
   （天花板效应），价值主张 = 确定的 token 节省 + 上下文隔离 + 扩容安全边际；
   残余风险是 tool/skill 孪生歧义在小面上更显眼（修法 = prompt 仲裁句，待
   生产遥测验证）。
4. **评测纪律本身是最大的产出**：预注册判据 + 噪声地板 + 诚实 null，挡住了
   两次"看起来有改进"的假阳性（A7 弱效应、A8 回归），也接住了 C 批的失败。

---

## 5. 最终状态与续作接口

- **生产面**：MCP keyless 74→59 工具（−17.9% 披露税），路由非劣，
  幻觉调用 0——B 批为唯一全量准入的改动（暂缓上游贡献，本地部署有效）。
- **D 批生产落地 ✅（2026-08-28，mymain `43cf7624` + `6f61a2c5`）**：
  §10.5 步骤 1 完成——`OpencodeAgent/config/subagents.json` + `prompts/` +
  render_config 扩展 + 编排侧路由政策入生产 AGENTS.md + 部署文档同步。
  落地冒烟比检查点原要求多修两处：① deny 覆盖扩展至 OMO 插件运行时注入的
  内建命名空间（websearch/context7/grep_app/lsp，模板外不可见，实测复现泄漏
  后闭合）；② prompt 引用改为渲染时 colocation（探针实测 `{file:}` 按配置
  文件目录解析，原容器绝对路径方案会静默失效）。证据：
  `artifacts/d_l2_rendered/`（SMOKE_NOTES.md + 轨迹）。
- **仍开放**：② 孪生仲裁证据补强（生产遥测，未闭合前不执行主循环收敛）；
  ③ D4 铺开评审（复用 `d_batch/` 评测协议，待②闭合）。
- **D3**（swarm 白名单移植映射）不受阻塞，可独立推进。

## 6. 引用清单

| 文档 | 内容 |
|---|---|
| `HARNESS_EVOLUTION_CAPABILITY_AUDIT.md` | 能力审计 v2（K/G/Q 编号、§7.2 路由表、§8.1 子代理草案） |
| `HARNESS_EVOLUTION_RESEARCH.md` | 架构调研（32 框架、opencode+omo 保留裁决） |
| `HARNESS_EVOLUTION_PAPERS.md` | 论文索引（A-I 九类 + 复现台账） |
| `HARNESS_EVOLUTION_BENCHMARKS.md` | 评测基准调研 |
| `HARNESS_EVOLUTION_P0_PLAN.md` | Wave 1 执行与 A 批 E2 终局 |
| `HARNESS_EVOLUTION_B_TEST_PLAN.md` / `HARNESS_EVOLUTION_C_PLAN.md` / `HARNESS_EVOLUTION_D_PLAN.md` | 三批工作计划 + 预注册判据 |
| `agent/src/evals/tool_selection/artifacts/` | a6_a8_verdict / b_batch_verdict / c_batch_verdict / d_batch_verdict + 黄金 traces + `d_l2_rendered/`（生产渲染配置冒烟证据） |
| `agent/src/evals/tool_selection/`（含 `d_batch/`） | E1/E2 评测基建与 D 批资产 |
| mymain 分支 `OpencodeAgent/`（`43cf7624` + `6f61a2c5`） | D 批生产落地：subagents.json、prompts/、render_config 扩展、AGENTS.md 路由政策、部署文档 |
