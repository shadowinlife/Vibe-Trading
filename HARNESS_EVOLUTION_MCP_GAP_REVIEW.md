# MCP 面缺口收口评估（F2 + F4 + trading-connector 安全设计）

> 日期：2026-08-30 ｜ 状态：**全部裁决通过并已执行**（DEC-3/4/5/6，2026-08-30 用户拍板；执行证据：F2+F4=eebf48af，DEC-5=mymain b5a7265b + artifacts/d2/d4tc_verdict.md，DEC-6 状态降级已落 ROADMAP）
> 输入：ROADMAP F2/F4（K25/Q19 残留）+ D4 安全挂起的 trading-connector 候选
> 性质：三者为同一决策族——"内部工具/能力在 MCP 面（含 opencode 子代理）的可达性边界画在哪"

---

## 1. 事实基线（全部经代码核实）

| 工具 | 性质 | 副作用 | 门面 | 引用面 |
|---|---|---|---|---|
| `financial_rigor` | 精确十进制算术 + 估值核验（three_scenario / verify_valuation / Benford 等） | 无（纯计算，gate=none，无凭据） | 仅 agent 面 | `value_investing_committee`（4 个 worker 白名单 + 终检 worker）、`fundamental_research_team`、bottleneck-hunter / thesis-tracker / deep-company-series 等技能 |
| `report_audit` | 研究报告数字点的发布前审计（extract → verdict） | 无（纯计算） | 仅 agent 面 | `value_investing_committee` 终检 worker（报告质量门） |
| `sdm_register` | 因子/策略注册入策略库 | **写**（~/.vibe-trading 策略库） | 仅 agent 面 | strategy-dev-manager 技能生命周期第一步 |
| `sdm_status` | 查询**或更新**库中状态 | 读+写（含 four-eyes 治理校验 `is_four_eyes_violation`） | 仅 agent 面 | 同上 |
| `sdm_decay_scan` | 衰减监控扫描 | 读 + 可能写（生命周期状态迁移） | 仅 agent 面 | 同上 + 定时任务场景 |
| `trading_*` ×17 | 连接器交易族（B2 后条件暴露） | 读族无副作用；**写族（place/cancel）从不在 MCP 面**（agent+CLI，mandate 门控） | MCP 面仅读族（B2 门控后）；opencode 部署中全族全局 deny | D4 候选 trading-connector 因此被安全挂起 |

**关键已存在事实**：策略库的**读侧**已在 MCP 面——strategy-discovery 三件套
（`list_strategies` / `query_strategies` / `get_strategy_evidence`，mcp_server.py:1215+）
就是 SDM 库的只读门面。F4 的"读"问题已解，未解的只有"写"。

## 2. F2 评估：financial_rigor / report_audit

### 两案成本收益

| | 暴露案（MCP 只读注册） | 不暴露案（维持 §8.1 替代方案） |
|---|---|---|
| 收益 | ① 2 个 preset（value_investing_committee / fundamental_research_team）的 rigor/audit 行为可移植到 MCP 面子代理；② D4 已准入的 valuation-agent 可在 MCP 面做精确算术核验（现只能靠 quantlib_call + prompt 自律）；③ K25/Q19 缺口清零 | 零改动；披露税不增 |
| 成本 | 披露税 +2 工具（B 批实测 ~340 token/工具/轮 → ~700 token）；README 计数锚点 + 6 份 README 同步（B 批全局验收规则）；描述需过路由 sanity（准入协议 lite） | preset 行为在 MCP 面永久不可移植；valuation 类子代理的数字核验能力缺一层 |
| 风险 | 与 `quantlib_call` 同级（纯计算只读工具，已有先例） | 无新增风险 |

### 建议：**暴露**（两工具天然只读，连 read-only 包装都不需要）
执行清单（裁决后）：mcp_server.py 注册 → `test_readme_counts.py` 锚点 + 6 份 README
工具计数同步 → 描述按准入协议 Step 1 规则过一遍 → keyless 环境实测 tools/list。

## 3. F4 评估：sdm_*

### 三案对照

| 案 | 内容 | 评 |
|---|---|---|
| ① MCP 暴露 | 读侧优先注册 | **否**：读侧与 strategy-discovery 三件套功能重复（双入口 = 新的路由混淆源，正是 A 批划掉的病）；写侧（register/status 更新）经 MCP 暴露 = 策略库写信任升级，而策略库直接喂 strategy-discovery 的推荐——写入面应留在本地运行时 |
| ② 文档降级 | 技能声明"仅内置 agent 可用" | 部分采纳（写侧） |
| ③ 技能改写 | strategy-dev-manager 流程改为不依赖不可达工具 | 部分采纳（读侧）：技能文档中"查库"步骤一律改写为 strategy-discovery 三件套（MCP 名），注册/状态更新/衰减扫描三步声明"仅 agent 面" |

### 建议：**②+③ 混合**——不新增 MCP 注册；改写 strategy-dev-manager 技能文档，
读侧指向三件套、写侧声明 agent-only。验收：按技能走查无死路（F4 原验收口径）。

## 4. trading-connector 安全门设计提案

D4 挂起原因复述：不是能力问题，是**写族不可逆**——place/cancel 一出错就是真钱。

### 分层设计（提交裁决）

| 层 | 工具 | 子代理可见性提案 |
|---|---|---|
| Tier-0 只读 | trading_connections / select / check / account / positions / orders / quote / history | ✅ 可入 trading-connector-agent 白名单（前提：B2 门控已保证无连接器时整族不可见） |
| Tier-1 写 | trading_place_order / trading_cancel_order | ❌ **永不进任何子代理**；主循环 + 用户显式确认双门（现有 mandate/kill-switch 体系之上再加一层编排侧硬规则） |

### opencode 落地形态（若裁决通过）
- `vibe-trading-tools.json`：trading_* 从全局 deny 改为"deny 写族 + 允许读族"；
- `subagents.json`：trading-connector-agent 白名单 = Tier-0 八件套；
- prompt 写死："你没有下单工具。任何下单请求一律 NEED_INPUT 交回主循环"；
- 准入流程：走《子代理准入协议》全流程（R1/R2/R3 + 语料），不得跳步。

### 前置条件
用户侧至少配置一个连接器（B2 门控：无配置时整族天然不可见，子代理无存在意义）。
**若用户当前无连接器配置，本项直接裁决为"暂缓"**——设计与门禁已备，等有配置时启用。

## 5. D3 范围评估（swarm 白名单映射工程化）——建议：**暂缓，降级为按需**

- **现状**：映射表已在 AUDIT §8.1 成文（A6 完成）；preset 白名单用内部名，
  在 VT 内置 swarm 运行时工作正常（`build_swarm_registry` 按本地注册表解析）。
- **缺失的只是运行时映射层**（内部名→MCP 名的代码化转换）。
- **关键判断：当前无消费者**。D4 后的生产子代理花名册活在 opencode 配置里
  （直接用 MCP 名写白名单）；30 个 preset 在 VT 内部运行不需要映射。
  唯一消费场景 = "把 preset 白名单整体移植到外部 harness"——该需求目前不存在。
- **工作量**（若未来要做）：映射表 YAML 化（§8.1 已有数据，半天）+
  `presets.py` 解析层接入（~100 LOC + 测试）。不建议现在写没有消费者的代码。
- **降级处置**：D3 从 P1 降为"按需触发"，触发条件 = 出现第一个要把 preset
  白名单移植到 MCP 面子代理的实际需求。

## 6. 待裁决清单

| # | 决策 | 建议 | 阻塞 |
|---|---|---|---|
| DEC-3 | F2：financial_rigor + report_audit 注册 MCP 只读 | **通过** | 执行清单见 §2 |
| DEC-4 | F4：sdm_* 不暴露，技能文档改写（读→三件套，写→agent-only） | **通过** | 技能文档改写 |
| DEC-5 | trading-connector：Tier-0/Tier-1 分层 + 无连接器则暂缓 | **通过设计，按前置条件决定启用** | 用户连接器配置状态 |
| DEC-6 | D3 降级为按需触发 | **通过** | 无（文档状态更新） |
