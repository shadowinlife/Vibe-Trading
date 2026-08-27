# HARNESS_EVOLUTION · A5-A8 量化测试计划

> 状态：草案（待执行）｜ 日期：2026-08-26
> 上游依据：`HARNESS_EVOLUTION_ROADMAP.md` §7.5（SOTA 天花板裁决）、
> `HARNESS_EVOLUTION_P0_PLAN.md` §10（E2 终局结论）。
> 本文档回答一个问题：**A5-A8 的改动，是否能带来"真实的提升"——用可证伪的量化口径，而不是直觉。**

---

## 0. 摘要（TL;DR）

- **判官面板**：只用两档头部 SOTA 开源模型 —— `qwen3.8-max`（主）+ `kimi-k3`（敏感性）。
  同一 DashScope key，无弱模型、不考虑成本。
- **核心结论预告**：E2 已证明"描述措辞 → 路由准确率"在 SOTA + 全表面呈现下**路由中性**
  （基线天花板 top-1 0.84-0.89）。因此 A7/A8（同为描述改动）大概率也是中性。
  本计划的价值不是"证明它们有效"，而是**提供一个有足够统计功效、能区分"真无效"与"测不出来"的裁决仪器**。
- **四项测试**：
  - **T-A7 / T-A8**（路由维度）：扩充靶点语料 → 前后对比 → 池化 McNemar + **基线失败回收率**。
  - **T-A5**（代币税，非路由）：token 核算（确定性）+ 路由非劣性（LLM）。
  - **T-A6**（移植覆盖，非路由）：映射覆盖率 / preset 可解析数 / 内部名引用归零（确定性断言）。
- **预注册判据**：先写死"什么算真实提升"，再跑实验——杜绝事后合理化。

---

## 1. 目标与范围

**目标**：对 A5-A8 逐项给出可证伪的量化裁决——该改动是否带来真实提升、提升多少、有无回归。

**范围划分**（来自 §7.5 的可量化性分析）：

| 项 | 维度 | 测量口径 | 需要 LLM? |
|---|---|---|---|
| A7 | 路由 | 靶点 query 组前后 top-1 对比 | ✅ 2 模型 |
| A8 | 路由 | 同 A7 | ✅ 2 模型 |
| A5 | 代币税（非路由） | 关一侧双暴露的 token 降幅 + 路由非劣 | ✅（仅非劣部分） |
| A6 | 移植覆盖（非路由） | 映射覆盖率 / preset 可解析 / 内部名引用=0 | ❌（静态断言为主） |

**明确不在范围**：
- 弱模型复测——目标环境永远是 SOTA 开源模型（用户确认），E2 结果直接适用。
- A1-A4 的重新验证——已裁决划掉（路由中性）。

---

## 2. 目标环境与判官面板

**环境假设**：需要应对的环境永远是**头部 SOTA 开源模型**（比 GPT/Claude 弱一点、但最强的开源模型）。
无弱模型场景，故无需弱模型复测。

**判官面板**（本计划专用，从 E2 的 4 家族收窄为 2）：

| 模型 | 角色 | provider | temp | max_response_tokens | 依据 |
|---|---|---|---|---|---|
| `qwen3.8-max` | primary | dashscope | 0.0 | 500 | E2 主判官，cap 80 探测 7/8 一致 |
| `kimi-k3` | sensitivity | dashscope | 0.0 | 1000 | E2 敏感性，~200 token 完成，1000 留余量 |

> **cap 纪律**：以上 cap 为 E2 经验探测值，**不得下调**（reasoning 模型在低 cap 会截断为空内容）。
> 详见 `judge_config.yaml` 的 DOCUMENTED DEVIATIONS。

**预算**：用户批准为**无限**。cap 只用于约束最坏延迟与快速失败，不是成本控制。

**确定性**：`temperature=0.0`。正式跑 flip 判定前，先跑 `--probe-only`
（8 query × 3 repeat）确认两模型 `first` 选择的一致率 ≥ 既定阈值；一致率不足则先排查，再谈 flip。

---

## 3. 效度威胁与设计应对（本计划的"为什么"）

> 这是"能否反映真实提升"的核心。E2 已经踩过的坑，这里逐项设防。

| # | 威胁 | E2 中的表现 | 本计划的应对 |
|---|---|---|---|
| T1 | **统计功效不足** | 靶点 query 极薄：A7 全部 6 项仅 13 条、A8 仅 18 条（Q14 甚至 0 条）。13-18 条跑 McNemar 几乎无法检出任何效应 | **扩充靶点语料**：每项改动 ~10 条 → A7≈60、A8≈70；两模型池化后有效 N≈120-140 |
| T2 | **天花板效应** | 基线 top-1 已 0.84-0.89，只有"当前答错"的 query 才可能变对 | **基线失败回收率**（recovery rate）= 被修复的基线失败数 / 基线失败总数；并刻意富集"基线会答错"的困难/对抗样本 |
| T3 | **格式伪影** | scoring 严格全等（`kind:name`），kimi 裸名回复被计 miss，制造假 flip（E2 中 kimi 名义 p=0.049 即此伪影） | **format-tolerant 评分**作为并列宽松口径；预注册"仅格式差异的 flip = 非实质"，不计入改进 |
| T4 | **归因混淆** | A7/A8 若捆绑改，无法区分各自贡献 | **顺序隔离**：先 A7 后 A8，每批前独立冻结基线语料 |
| T5 | **回归被掩盖** | 靶点变好、别处变差，净值可能为零 | **全 158 条回归守卫**：靶点改进必须伴随全集 top-1 非劣（不显著下降） |

---

## 4. 测试设计

### T-A7 · 路由维度（P1，6 项描述修订）

**改动 → 靶点映射**（`arbitration_ref` 已在 `queries.yaml` 标注）：

| 改动 | AUDIT ref | 现有 query 数 | 目标扩充到 |
|---|---|---|---|
| ① correlation-analysis 删越界 | Q5 | 1 | ~10 |
| ② screen_market 补边界 | Q6 | 1 | ~10 |
| ③ web_search 多引擎+read_url 衔接 | Q8 | 1 | ~10 |
| ④ trading_* 入口顺序 | Q10 | 5 | ~10 |
| ⑤ technical 层级声明 | Q17/K2 | 3 | ~10 |
| ⑥ 研报/期权/策略发现族内互指 | Q18 | 2 | ~10 |

**语料构造原则**（针对 T1+T2）：
- 每项改动 ~10 条，其中**至少一半是"基线会答错"的困难样本**（对抗性措辞、易混近邻、越界诱导）。
  只有基线失败的 query 才能展示改进；基线已对的 query 只能回归。
- 保留现有条目，新增条目沿用 `id`/`expected`/`negatives`/`arbitration_ref` schema。
- 新语料单独成文件 `queries_A7_target.yaml`（不污染主 `queries.yaml`），经子集过滤参数喂给 runner。

**前后对比机制**：
1. 冻结当前语料为 `baseline_A7`（`captured_at` 打戳）。
2. 实施 A7 的 6 项描述修订。
3. 重建 `post_A7` 语料。
4. 两模型 × {靶点集, 全 158} × {baseline_A7, post_A7} 全跑。

### T-A8 · 路由维度（P2，7 项描述修订）

**改动 → 靶点映射**：

| 改动 | AUDIT ref | 现有 query 数 | 目标扩充到 |
|---|---|---|---|
| cashflow_performance 触发词 | Q9 | 1 | ~10 |
| get_sector_info 模式说明 | Q14 | **0** | ~10（全新） |
| prediction_market 首句定位 | Q15 | 1 | ~10 |
| volatility 更名评估 | Q16 | 1 | ~10 |
| TA 流派场景句 | G2 | 6 | ~10 |
| shadow 流水线步骤号 | G9 | 7 | ~10 |
| 数据源技能删互称备份 | G1 | 2 | ~10 |

设计与 T-A7 完全相同（语料 `queries_A8_target.yaml`、独立冻结 `baseline_A8`）。
**注意**：Q14 现有 0 条，全部需新写；volatility 更名（Q16）需先附引用点影响面评估再决定是否纳入路由测试。

### T-A5 · 代币税（非路由）

A5 的量化方向是"决策后暴露路径数 = 1"。真实提升 = **关掉一侧双暴露省下的 token**，且**不破坏路由**。

- **指标 1（token 核算，确定性）**：测量 90 技能经 `.opencode/skills/` + MCP `list_skills/load_skill`
  双路径暴露时的披露 token 数，对比单路径。Δ = 代币税节省。需要一个小的披露面捕获 helper。
- **指标 2（路由非劣，LLM）**：选定单路径后，跑全 158（或技能路由探针子集），
  确认 top-1 相对双暴露基线的下降 ≤ 非劣边界 δ（建议 δ=2pp）且 McNemar 不显著回归。

**判据**：token 降幅 ≥ 既定阈值 **且** 路由非劣 → A5 真实提升成立。

### T-A6 · 移植覆盖（非路由）

A6 的量化方向是"映射覆盖率 = 100%（对 F1 名单）；未标注内部名引用数 = 0"。全部确定性断言：

- **覆盖率**：内外名称映射表覆盖 F1 盘点的完整内部工具名单（~32 个非 MCP 内部工具）。
- **可解析数**：30 个 swarm preset 的工具白名单，经映射表可解析到 MCP 面的数量（目标 30/30）。
- **引用归零**：`agent/src/skills/` 中引用内部名（`pattern`、`options_payoff` 等 7 组）的位置，
  改为 MCP 名或带映射标注后，未标注内部名引用数 = 0（grep 断言）。
- **可选 LLM 抽查**：两模型对映射后的 MCP 名做有效性确认（非必需，静态断言已足够）。

---

## 5. 所需基建扩展（小而明确）

> 现有 E2 基建在 `fix/trading-tool-routing-hints` 分支的 `agent/src/evals/tool_selection/`。
> 以下为本计划需新增/扩展的项。

| # | 基建 | 说明 | 改动面 |
|---|---|---|---|
| I1 | **子集过滤参数** | `run_llm_judge.py` 现仅 `--limit`（前 N 条），无按 `arbitration_ref`/query 文件过滤。新增 `--queries-file <path>` 或 `--refs Q5,Q6,...` | runner CLI |
| I2 | **corpus 捕获脚本** | 现无独立捕获脚本（语料系一次性生成）。新增 `capture_corpus.py`：从 `mcp_server.mcp.list_tools()` + `SkillsLoader` 快照全表面，打 `captured_at`，产出 `corpus_*_snapshot.yaml` | 新脚本 |
| I3 | **format-tolerant 评分** | 现 `score_response` 严格全等。新增宽松口径：用 `name_kinds` 把裸名归一到 `kind:name` 再比对；与严格口径并列输出 | `llm_judge_protocol.score_response` 或 stats 层 |
| I4 | **token 核算 helper**（A5） | 捕获双暴露/单路径披露面并计 token | 新脚本 |
| I5 | **覆盖断言 helper**（A6） | 映射覆盖率 / preset 可解析 / 内部名 grep 归零 | 新脚本（可复用 F1 的 `inventory_internal_tools.py`） |

> **protocol 完整性**：`prompt_template_sha256()` 只钉住 prompt 模板（SYSTEM_PROMPT + USER_TEMPLATE），
> 不含评分逻辑。故 I3 的 format-tolerant 改动**不改 prompt hash**，不会使既有 trace 失效——
> 但需在 `artifacts/llm_judge_design.md` 记录该评分口径变更。

---

## 6. 指标与预注册判据

> **先写死判据，再跑实验。** 下表是"什么算真实提升"的唯一依据，禁止事后改阈值。

### 6.1 路由类（T-A7 / T-A8）

| 指标 | 定义 | 主/辅 |
|---|---|---|
| 靶点池化 McNemar p | 两模型池化、靶点集、exact McNemar（双侧） | 主 |
| 靶点 Δtop-1 | post − baseline 的 top-1 准确率差 | 主 |
| **基线失败回收率** | 被修复的基线失败数 / 基线失败总数 | 主（对天花板最敏感） |
| improved / regressed 计数 + Wilson 95% CI | 配对 flip 计数 | 辅 |
| 全集 top-1 非劣 | 全 158 上 post 不显著低于 baseline | 守卫 |

**"真实提升"判据（须同时满足）**：
1. 靶点池化 McNemar **p < 0.05**；
2. 靶点 **Δtop-1 ≥ +3pp**；
3. **基线失败回收率 ≥ 30%**；
4. 全集 top-1 **非劣**（不显著回归）。

**裁决树**：
- 四条全过 → **真实提升成立**（值得上游）。
- 仅 1-2 条过、回收率集中于靶点 ref → **弱/局部效应，路由改进证据不足**（与 E2 天花板结论一致，诚实记录）。
- 全集显著回归 → **否决**（无论靶点是否变好）。
- 格式伪影占比高 → 以 format-tolerant 口径复核后再裁。

### 6.2 非路由类（T-A5 / T-A6）

| 测试 | 真实提升判据 |
|---|---|
| T-A5 | token 降幅 ≥ 既定阈值 **且** 路由非劣（δ=2pp 内、McNemar 不回归） |
| T-A6 | 映射覆盖率 = 100%（对 F1 名单）**且** preset 可解析 30/30 **且** 未标注内部名引用 = 0 |

---

## 7. 执行工作流（顺序隔离）

**基线分支决策**：在 `fix/trading-tool-routing-hints` 上执行（E2 基建所在）。
A1-A4 已被证明路由中性（Δ≈0），叠加其上不会实质改变路由基线，故 A7/A8 测得的是**边际贡献**，方法学上成立且最省工。
（若要求"纯上游基线"，可另从 `main` 起分支、仅搬 evals 基建——但注意 `queries.yaml` 的 sec-edgar 改名 reconciliation 需随之回退。）

```
Phase 0  基建：实现 I1-I5（小改 runner + 4 个 helper），跑 39 条离线测试确认不回归
Phase 1  T-A7：
         1a  冻结 baseline_A7（capture_corpus.py，captured_at 打戳）
         1b  构造 queries_A7_target.yaml（~60 条，含困难样本）
         1c  跑 baseline：2 模型 × {靶点, 全158}
         1d  实施 A7 六项描述修订
         1e  重建 post_A7，跑 post：2 模型 × {靶点, 全158}
         1f  stats：per-model + 池化 McNemar + 回收率 + 非劣；对照 §6.1 判据出裁决
Phase 2  T-A8：baseline_A8 = post_A7，重复 1b-1f（用 queries_A8_target.yaml）
Phase 3  T-A5：token 核算（I4）+ 路由非劣（2 模型 × 全158 或技能探针）
Phase 4  T-A6：覆盖断言（I5），可选 LLM 抽查
Phase 5  汇总：写 `artifacts/a5_a8_verdict.md`，回写 ROADMAP §7.5 与 P0_PLAN
```

---

## 8. 产物与报告

| 产物 | 命名 | 内容 |
|---|---|---|
| 黄金 trace | `llm_judge_trace_<model>_<surface>.jsonl` | 逐条判官响应（append-only，sha256 模板钉扎） |
| 准确率/成本报告 | `llm_judge_report_<model>_<surface>.md` | top-1/top-3、invalid、neg_false_recall |
| 统计报告 | `llm_judge_stats_report_a7a8.md` | per-model + 池化 McNemar、回收率、flip list、格式伪影占比 |
| 裁决文档 | `artifacts/a5_a8_verdict.md` | 对照 §6 预注册判据的逐项裁决 |

---

## 9. 成本与资源估算（预算无限，仅供参考）

| 阶段 | 调用数估算 |
|---|---|
| T-A7 / T-A8 各一批 | 2 模型 × 2 表面 × (靶点 ~65 + 全 158) ≈ **~890 calls/批** |
| 两批合计 | ~1,780 calls |
| 确定性 probe | 2 模型 × 8 query × 3 repeat ≈ ~50 calls |
| T-A5 非劣 | 2 模型 × 2 表面 × 158 ≈ ~630 calls |
| **合计** | **~2,500 calls**，约 ~12k prompt token/call → ~30M prompt token |

预算无限，cap 仅约束延迟与快速失败。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 天花板导致"怎么测都中性" | 用**回收率**而非单纯 Δtop-1；富集基线失败样本，让仪器有能力检出真效应 |
| 扩充语料本身引入偏差 | 困难样本须有明确 `arbitration_ref` 与 `negatives`；新条目走与现有条目相同的 schema 审查 |
| 格式伪影再次污染 | format-tolerant 并列口径 + 预注册"仅格式 flip 非实质" |
| A7/A8 归因混淆 | 顺序隔离、每批独立冻结基线 |
| 分支/基线 reconciliation（sec-edgar 改名） | §7 已注明；若从 main 起分支须回退 queries 改名 |
| reasoning 模型低 cap 截断 | 严守 §2 cap 纪律，不下调 |

---

## 附录 A · 与 E2（A1-A4）结论的衔接

E2 终局：A1-A4 描述改动**路由中性**（池化 McNemar p=0.885，基线天花板 0.88-0.94）。
本计划不推翻该结论，而是把同一仪器**升级到能检出靶点效应的功效水平**，用于裁决 A7/A8；
并把 A5/A6 的非路由价值（代币税 / 移植覆盖）纳入各自正确的测量口径。
**真正的路由杠杆仍是 B/C/D 批（减少每次决策呈现的工具数量），而非打磨描述措辞**——本计划的预期结果与该判断一致。
