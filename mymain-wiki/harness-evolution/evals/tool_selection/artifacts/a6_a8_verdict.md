# A6-A8 量化测试裁决（2026-08-26）

> 判官面板：**qwen3.8-max（主）+ kimi-k3（敏感性）**，同 DashScope key，temp 0.0。
> 目标环境 = 头部 SOTA 开源模型（用户约束），无弱模型、不考虑成本。
> 方法：前后配对（每 query 自身对照），exact McNemar + Wilson 95% CI +
> 基线失败回收率，对照 `../../../HARNESS_EVOLUTION_A5_A8_TEST_PLAN.md` §6.1 预注册判据。
> 顺序隔离：A7 基线=pre-A7，A8 基线=post-A7。全集基线复用上一阶段的 post traces。

---

## 预注册判据回顾（§6.1）

"真实提升"须同时满足：
1. 靶点池化 McNemar **p < 0.05**
2. 靶点池化 **Δtop-1 ≥ +3pp**
3. 基线失败**回收率 ≥ 30%**
4. 全集 top-1 **非劣**（不显著回归）

决策树：四条全过 → 真实提升；仅 1-2 条过且回收集中靶点 → 弱/局部效应；**全集显著回归 → 否决**。

---

## T-A7 · 路由维度（6 项，④入口序已在基线，实施 ①②③⑤⑥）

改动 11 工具 + 2 技能描述（correlation-analysis 删越界、screen_market 补边界、
web_search 多引擎+read_url 衔接、technical 层级、研报/期权/策略发现族内互指）。

| 集 | 口径 | baseline→post | Δ | 进/退 | McNemar p |
|---|---|---|---|---|---|
| 靶点60 | strict 池化 | 0.8333→0.8583 | +2.50pp | 10/7 | 0.629 ❌ |
| 靶点60 | lenient 池化 | 0.8500→0.8833 | +3.33pp | 9/5 | 0.424 ❌ |
| 全集158 | strict 池化 | 0.9019→0.9367 | **+3.48pp** | 16/5 | **0.0266 ✅** |

- 回收率：靶点 strict **50%**（20 失败回收 10）✅
- 判据：1❌ 2❌ 3✅ 4✅

**裁决：弱/局部效应（仅 2/4 判据通过；最终划掉，见综合结论的裁决演进）。**
全集显著改善（+3.48pp, p=0.027）且无净回归；但最敏感的靶点困难集仅 +2.5pp 不显著。
靶点 flip 证实改动按预期生效（correlation→pair-trading、screen→iwencai_search、
technical 层级、研报族互指），同时被少量回归抵消。
> 注：全集基线复用 E2 post traces（无 lenient 字段），全集改善含少量裸名→前缀的格式
> flip，真实路由改善可能略小于 +3.48pp；但非劣（判据4）不受影响。

---

## T-A8 · 路由维度（7 项）

改动 8 工具 + 9 技能描述（cashflow 触发词、sector 模式、prediction 定位、volatility
策略定位、6 个 TA 流派场景句、shadow 流水线步骤号、akshare/mootdx 删互称备份）。

| 集 | 口径 | baseline→post | Δ | 进/退 | McNemar p |
|---|---|---|---|---|---|
| 靶点70 | strict 池化 | 0.9286→0.9571 | +2.86pp | 6/2 | 0.289 ❌ |
| 靶点70 | lenient 池化 | 0.9357→0.9643 | +2.86pp | 5/1 | 0.219 ❌ |
| 全集158 | strict 池化 | 0.9367→0.9114 | **−2.53pp** | 6/14 | 0.115 |
| 全集158 | lenient 池化 | 0.9557→0.9272 | **−2.85pp** | 1/10 | **0.0117 ❌回归** |
| 全集158 | strict qwen | 0.9430→0.8987 | **−4.43pp** | 1/8 | **0.039 ❌回归** |

- 回收率：靶点 strict **60%**（10 失败回收 6）✅
- 判据：1❌ 2❌ 3✅ 4❌（全集显著回归）

**裁决：否决（REJECTED）。**
靶点确有真实修复（cashflow 触发词 None→cashflow_performance、sector 定位
search_symbol→get_sector_info），但**全集出现显著净回归**（lenient 池化 p=0.012，
qwen strict p=0.039），且回归**波及无关区域**（D06 策略发现、D16/D17/D19）。
按决策树"全集显著回归 → 否决"。描述加料在此产生了负外部性——给描述追加场景句/
步骤号会扰动模型对无关候选的注意力分布。
**建议：回滚 A8 的 17 处描述改动，保留 A7。**

---

## T-A6 · 移植覆盖（非路由，确定性断言）

| 项 | 结果 |
|---|---|
| 映射表（内部↔MCP） | ✅ 存在（../../../HARNESS_EVOLUTION_TOOL_MAPPING.md，38KB） |
| 技能文档内部名统一 | ✅ 完成（精化后未标注工具引用 = **0**） |
| preset 可解析 | 187 mcp / 10 drift / 33 internal-only / 0 unknown |
| strict gate | ✅ PASS（unknown=0 且技能文档引用=0） |

**裁决：完成（COMPLETE）。**
关键澄清：初版启发式指标**严重误报**——`edit_file`/`options_payoff`/`options_pricing`
引用其实**已全部标注映射**（如 "use `write_file`（内部名 `edit_file`）"，MCP 名为主），
而 `pattern` 引用全是普通英文（"candlestick pattern"）非工具引用。精化指标（只计
"未标注 + 反引号包裹的工具引用"）后，技能文档真实未标注引用 = 0。
10 个 preset drift 引用（options_pricing/options_payoff/edit_file/pattern）是**合法
agent 面内部名**（F1 盘点确认），非 broken；映射表已记录其 MCP 对应，移植缺口已闭合。

---

## 综合结论

| 测试 | 维度 | 测试裁决 | 最终处置（2026-08-27 清理） |
|---|---|---|---|
| T-A7 | 路由 | 弱/局部效应（仅 2/4 判据） | ❌ **划掉·已回滚**（用户裁决无改进） |
| T-A8 | 路由 | **否决**（全集显著回归） | ❌ **划掉·已回滚** |
| T-A6 | 移植 | 完成 | ✅ 映射表+文档统一已达成（验证，非改进） |

> **裁决演进**：初版曾标 T-A7 为"真实但温和改善→保留"。2026-08-27 五路并行
> review 裁定该标签**超出预注册授权**——按 §6.1 决策树，仅 2/4 判据通过应归
> "弱/局部效应、路由改进证据不足"，且全集 +3.48pp 属安全守卫指标非效力指标、
> 可能含格式 flip。用户据此裁决 A7/A8 均**无改进**，描述改动一并回滚
> （A7④ trading_* 入口序为独立 PR #1219，不在回滚范围）。

**对"描述治理"路线的总体印证**：即便在 SOTA 开源模型 + 全表面呈现下，描述措辞改动
的路由收益依然有限且不稳定——A7 仅弱/局部、A8 反而引入显著回归。这与 E2（A1-A4 中性）
一致：**真正的路由杠杆是减少每次决策呈现的工具数量（B/C/D 批），而非打磨描述**。
**A1-A8 描述测试已终局，勿重测**；方法学与 DO-NOT-RE-TEST 声明见 `llm_judge_design.md`。

## 产物
- 靶点/全集统计：`artifacts/a7_target_stats.md`、`a7_full_stats.md`、
  `a8_target_stats.md`、`a8_full_stats.md`
- 语料：`corpus_a7_baseline.yaml`、`corpus_a7_post.yaml`、`corpus_a8_post.yaml`
- 靶点语料：`queries_A7_target.yaml`（60）、`queries_A8_target.yaml`（70）
- 黄金 traces：`artifacts/llm_judge_trace_*_{baseline,post}_{a7,a8}_{target,full}.jsonl`
