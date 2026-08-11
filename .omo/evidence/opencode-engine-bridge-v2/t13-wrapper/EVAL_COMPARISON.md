# T13 tool_selection eval — before/after comparison report

- **日期**: 2026-09-13
- **变更**: `agent/mcp_server.py` 新增 `scheduled_research` wrapper（77→78 工具，keyless 面）
- **harness**: 归档词法评测 `mymain-wiki/harness-evolution/evals/tool_selection/`（run_eval.py，确定性词法打分，无 LLM/无网络）的 worktree 临时复跑副本（`agent/src/evals/tool_selection/`，评测后删除，副本存证于本目录 `run_eval_t13_rerun.py` / `queries_t13_rerun.yaml`）
- **验收标准**（AGENTS.md 约定 + 计划 T13 行）: 全局地板不降 + 定向组提升

## 协议与偏差声明（诚实性）

1. **复跑环境偏差**: 评测基建已归档（wiki INDEX `archived` = 只读证据），原始位置 `agent/src/evals/`（`fix/trading-tool-routing-hints` 分支）在本分支不存在。按归档 README 复跑指引，将 harness 复制到 worktree 同构路径（`AGENT_DIR = HERE.parents[2]` 解析到本 worktree 的 `agent/`），corpus 两次均从**本 worktree 的 live mcp_server** 重建（keyless 面：清掉 FRED/IWENCAI/QVERIS/TW_STOCK 门控 env，`VT_MEMORY_MCP_TOOLS` 关闭）——before = 改动前 HEAD 状态（77 工具），after = 改动后（78 工具）。
2. **queries.yaml 工作副本两处改动**（原归档件零触碰）:
   - `sec-edgar-fetch` → `sec-edgar` 回改 4 处（A2 更名只落在原分支，本分支技能名仍为 `sec-edgar`；D02-005/006/007 对齐当前现实）；
   - 追加 **T13 定向组 8 条**（domain=T13，E1 方法学：新能力=新定向组；含 2 条纯中文盲区样本，不为分数工程掉）。
3. **run_eval.py 打分器容差补丁**（1 处，补丁点有注释）: expected 能力不在当前 corpus 时按 top-1/top-3 miss 诚实计分（原实现 KeyError 崩溃）——基线面上 `scheduled_research` 不存在，定向组基线必然全 miss，这正是"提升只能来自工具面上后命中"的预注册语义。
4. 词法打分器对**纯中文 query × 纯英文描述**是已知盲区（P0 §8.1 A4 归因同款"词法代理盲区"）；语义级裁决属 E2 LLM-judge（已归档，本次未复跑——AGENTS.md 约定锚定的是 run_eval 词法面）。

## 结果总表

| 指标 | BASELINE（77 工具，改动前） | AFTER（78 工具，改动后） | 判定 |
|---|---|---|---|
| 全局 top-1 | 69/166 = **0.4157** | 75/166 = **0.4518** | ✅ +0.0361（全部来自 T13 组） |
| 全局 top-3 | 96/166 = **0.5783** | 102/166 = **0.6145** | ✅ +0.0362 |
| **全局地板（除 T13 的 158 条可比集）** | 69/158 = **0.4367** | 69/158 = **0.4367** | ✅ **逐位持平**（且 0.4367 与 P0 §8.1 历史基线逐位一致——harness 复跑保真度旁证） |
| **T13 定向组 top-1** | 0/8 = **0.0000** | 6/8 = **0.7500** | ✅ **定向组提升**（工具不存在→存在） |
| T13 定向组 top-3 | 0/8 | 6/8 | ✅ |
| negative false-recall | 16/130 = 0.1231 | 17/138 = 0.1232 | ➖ 分母 +8 = T13 条目 after 面才可比分；分子 +1 = T13-08（见下） |

**逐域对比（19 个非 T13 域）: 每一域 top1/top3 完全一致，零变化**——新工具进入词法候选集没有从任何既有域抢走路由（地板的最强形式）。

| domain | base top1/top3 | after top1/top3 |
|---|---|---|
| D01 | 4/6 of 9 | 4/6 of 9 |
| D02 | 5/8 of 10 | 5/8 of 10 |
| D03 | 2/3 of 5 | 2/3 of 5 |
| D04 | 5/7 of 9 | 5/7 of 9 |
| D05 | 6/7 of 11 | 6/7 of 11 |
| D06 | 2/5 of 10 | 2/5 of 10 |
| D07 | 3/5 of 10 | 3/5 of 10 |
| D08 | 2/3 of 8 | 2/3 of 8 |
| D09 | 4/6 of 10 | 4/6 of 10 |
| D10 | 2/3 of 7 | 2/3 of 7 |
| D11 | 5/5 of 8 | 5/5 of 8 |
| D12 | 6/7 of 8 | 6/7 of 8 |
| D13 | 3/5 of 9 | 3/5 of 9 |
| D14 | 5/6 of 7 | 5/6 of 7 |
| D15 | 3/3 of 7 | 3/3 of 7 |
| D16 | 3/3 of 8 | 3/3 of 8 |
| D17 | 4/6 of 11 | 4/6 of 11 |
| D18 | 2/4 of 5 | 2/4 of 5 |
| D19 | 3/4 of 6 | 3/4 of 6 |
| **T13** | **0/0 of 8** | **6/6 of 8** ← 唯一变化 |

## T13 定向组明细（after 面 2 条 miss 的归因）

| id | query 形态 | after top-1 | 归因 |
|---|---|---|---|
| T13-01..05, 07 | EN/混合（含 "scheduled research" 产品术语） | ✅ 命中（name-token 权重主导） | — |
| T13-06 | 纯中文（定时研究任务/市场扫描/简报） | ❌ → skill:sentiment-analysis | **词法代理盲区**：CJK bigram × 纯英文描述零重叠（P0 §8.1 A4 同款归因）；语义面由模型真实路由——live E2E 中文确认流已实证工作（t13-confirm-flow-results.json） |
| T13-08 | 对抗样本（"定时调度 swarm 每晚跑"） | ❌ → tool:run_swarm | **boundary-missing**：query 含 "swarm" name-token 而 "定时/调度" 无词法对应；同时贡献唯一的 negative false-recall +1（run_swarm 是其 negatives 之一）。真实语义下这是"提案一个跑 swarm 的定时任务"→scheduled_research，词法面不可分辨——保留为诚实盲区，不改描述去迎合打分器（A 批教训①：描述措辞不是杠杆） |

## 验收判定

- **全局地板不降**: ✅（158 条可比集逐位持平 0.4367；19 个非定向域逐域零变化）
- **定向组提升**: ✅（T13 组 0/8 → 6/8 = 0.7500）
- 复现: 两份 corpus（`corpus_baseline.yaml` 77 工具 / `corpus_after.yaml` 78 工具）+ 两份报告（`eval_baseline_report.md` / `eval_after_report.md`）+ stdout（`eval_baseline_stdout.txt` / `eval_after_stdout.txt`）+ 工作副本 queries/run_eval 全部落盘本目录。词法评测确定性：同 snapshot 两次运行输出逐字节一致（harness 文档保证）。
