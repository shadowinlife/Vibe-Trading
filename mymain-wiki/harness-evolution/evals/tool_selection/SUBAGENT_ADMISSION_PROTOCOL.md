# 子代理准入协议（Subagent Admission Protocol）

> 版本：v1.0（2026-08-30）｜ 来源：D 批试点（D1/D2）+ D4 三轮铺开评审的固化
> 地位：**新增子代理或新工具域进入生产前的强制门禁**。绕过本协议上线的子代理
> 视为未评审。证据基线：`artifacts/d_batch_verdict.md`、`artifacts/d2/track_a_verdict.md`、
> `artifacts/d2/d4_round1_verdict.md`、`artifacts/d2/d4_final_verdict.md`。

---

## 0. 何时适用

- 新增领域子代理（新白名单 + 新 description + 新 prompt）；
- 既有子代理的 description / 白名单**实质性变更**（路由语义改变）；
- 不适用：纯 prompt 措辞打磨（走普通改动 + before/after 抽查）；工具实现变更
  （走单测 + 回归）。

## 1. 流水线（六步，顺序不可跳）

### Step 1 — 定义评审（人工，无 LLM）
产出候选定义 YAML（参照 `d4_batch/subagent_*.yaml`）：
`name / description / tools[] / twin_pairs[]`。三条硬规则：
1. **白名单完整**：覆盖该域全部高频动作；凡缺工具必走 NEED_INPUT 逃逸，
   不允许"凑活调用相邻工具"。
2. **twin_pairs 显式列出**：与相邻域的仲裁边界（谁做什么、谁不做什么）。
3. **诚实拒绝是合法产出**：orchestrator 在 D4 被拒绝（编排是主循环的本职）；
   trading-connector 被安全挂起（trading_* 全局 deny）。准入不是默认结局。

### Step 2 — 语料构建
每候选 ≥30 条 query→期望命中对，混入负向触发（应路由去别处的 query）与边界项。
参照 `d4_batch/queries_d4_routing_all_v2.yaml`（353 条）。
语料纪律（D4 三轮换来的教训，全部为硬规则）：
- **动词必须可区分**：两条域用同一动词描述不同对象时，语料必须带区分性语境
  （D19 教训：裸动词导致标签歧义）；
- **验证器是筛子不是标注器**：`d4_corpus_validate.py` 只报可疑项，人工裁决后
  入 REVIEWED_OK 登记；不允许为消警报而改标签；
- **与生产路由契约冲突的标签，以契约为准**（冻结规则，D2_PLAN §8）；冲突项
  记入争议登记（见 §3），不许静默删除。

### Step 3 — 门禁评测（三判据，全部过才准入）
判官面板冻结：**qwen3.8-max + kimi-k3，temperature=0**（`judge_config_a5a8.yaml`）。
模板 hash 钉死（routing `24809ade` / selection v1 `b0e0fb11` / v2 policy-block
`acc5eac5`）；换模板 = 新评测，不可与旧分比较。

| 判据 | 阈值 | 含义 |
|---|---|---|
| R1 域内召回 | 每候选 ≥ 0.85 | 域内 query 被路由到该候选的比例 |
| R2 误委派率 | ≤ 5%（含边界） | 域外 query 误入该候选的比例 |
| R3 边界仲裁 | ≥ 85% | twin_pairs 边界项的仲裁正确率 |

工具链：`run_llm_judge.py`（采集）→ `d4_verdict.py`（判据对照）→
`d2_power_analysis.py`（功效，样本不足时不得下结论）。
评分口径：kimi 裸名应答 = strict 判 miss（保守，宁可低估）。

### Step 4 — 修订纪律（防过拟合的核心）
- 每轮 FAIL 后**只允许改 description**；语料、判据、模板一律不许动；
- **修订先于采集冻结**：写好修订版定义 → 提交 → 才跑新一轮采集；
  "先看结果再调描述" = 过拟合，结果作废；
- 每轮如实成文（含 FAIL），参照 `artifacts/d2/d4_round1_verdict.md` 的写法；
- 三轮不收敛 → 该候选退回定义评审，不许无限迭代。

### Step 5 — 生产同步（mymain）
1. prompt 文件入 `OpencodeAgent/config/prompts/<snake_name>.md`，结构 =
   角色 → 工具契约 → 孪生仲裁 → OUT_OF_SCOPE/SUGGESTED → 输出契约 → 诚实条款；
2. `subagents.json` 追加条目（`{file:./prompts/}` 相对引用 + v3 描述 + 白名单）；
3. `OpencodeAgent/AGENTS.md` 路由政策扩写（行数预算 450）；
4. **白名单保真扫描**：prompt 内反引号工具名必须 ⊆ subagents.json 白名单
   （参数名/字段名误报人工甄别）；
5. `OpencodeAgent` 测试全绿 + 渲染验证（子代理节数 = prompts 数）。

### Step 6 — 冒烟（真实 opencode 环境）
渲染配置 + 宿主 MCP 路径补丁后，至少跑：一个回归场景（老域仍正确委派）+
每个新高风险域一个委派场景。观察项（非阻塞）如实记入 Track B 遥测窗。

## 2. 已知方法学缺口（下轮评测前必须重读）

`artifacts/llm_judge_design.md` "Known methodology gaps"：靶点集功效、非劣性定义、
主口径预指定、判官重测噪声地板。B 批起已闭合（`b_batch_stats.py` /
`retest_noise.py` / `--probe-tag`），复用工具链时**不要绕过这四项**。

## 3. 语料登记（Corpus Registry）

| 语料 | 路径 | 规模 | 用途 | 状态 |
|---|---|---|---|---|
| 基础评测集 | `queries.yaml` | 158 | A/B/C 批通用路由评测 | 冻结 |
| 表面快照 | `corpus_b_{baseline,post}.yaml` | 74T+90S / 59T+90S | B 批前后工具面快照（非 query 语料） | 冻结 |
| D 批试点 | `d_batch/queries_d_{quant,webdocs}.yaml` + `queries_d_routing.yaml` | 40+16+198 | D1/D2 域内 + 路由召回 | 冻结 |
| D2 Track A | `d_batch/queries_d2_{quant,webdocs}.yaml` | 80+144（孪生富集） | 孪生仲裁门禁 | 冻结（裁决 track_a_verdict.md） |
| D4 铺开 | `d4_batch/queries_d4_routing_all_v2.yaml` | 353 | 9 候选准入（R1/R2/R3） | 冻结（裁决 d4_final_verdict.md） |
| 争议登记 | 各 verdict 文档附录 | 3 项 | BND-003 / D4R-B-069 / D05-010 | 留痕，不消除 |

新增语料必须在此登记（路径/规模/用途/裁决指针），未登记语料的评测结果不予采信。
