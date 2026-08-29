# D2-1 Track B B0：生产遥测事件分类学（观察窗前置，2026-08-29 冻结）

> 依据 `HARNESS_EVOLUTION_D2_PLAN.md` §3.2。Track B 是生态确认层（非门禁）。
> 本文冻结事件定义、eligibility 重建方法与判定口径；观察窗开启后不得修改。

## 1. 事件定义

| 事件类 | 定义 | 数据源字段 |
|---|---|---|
| `twin_choice` | 一个会话中出现了某孪生对的**任一侧**调用（工具直连或技能加载） | `part.data.tool` / `load_skill` 的 `input.name` |
| `whitelist_violation` | 子代理会话出现白名单外 `vibe-trading_*` 直连调用 | detectors.detect |
| `foreign_namespace` | 子代理会话出现 websearch/context7/grep_app/lsp 调用 | 同上 |
| `channel_confusion` | `skill_mcp` 通道调用了 `vibe-trading_*` 直连工具目标 | 同上 |
| `repeated_failure` | 同一调用目标连续 error ≥5 次（死循环签名，D2-5 探测面） | 同上 |

## 2. 孪生对清单（当前生产面）

quant 域：`alpha_zoo`×`alpha-zoo`、`backtest`×`strategy-generate`、
`backtest`×`backtest-diagnose`、`factor_analysis`×`factor-research`、
目录三工具×`strategy-discovery`、`backtest`×`ml-strategy`。
webdocs 域：`read_url`×`web-reader`、`read_document`×`doc-reader`、
`web_search`×`web-reader`。
（A3 语料同构；新增子代理时按同名 stem 规则扩充。）

## 3. Eligibility 重建（轨迹只记录"调用了什么"的补偿）

轨迹不记录"考虑过什么"。孪生选择事件的 eligibility 重建规则（确定性，
不过 LLM 判官）：

1. 会话属于某子代理（`session.parent_id` 非空且 `agent` 字段命中子代理名）
   或主循环中含委派意图（`task` 调用 subagent_type 命中）；
2. 该会话时间窗内某孪生对**至少一侧**被调用（工具调用或 load_skill）；
3. 满足 1+2 即记一个 `twin_choice` 事件，记录选择了哪一侧。

**已知局限（如实记录）**：用户在主循环直接完成孪生命名任务而未触发任何
一侧调用的情形不可见（无事件）；本分类学测的是"发生选择时的选择质量"，
不是"选择发生的频率"。

## 4. 正误判定（启发式 + 抽样人工）

- 工具侧调用 + 任务动词为执行类 → 正确；技能侧加载 + 任务动词为方法类 →
  正确。任务动词从主代理的 `task` 调用 prompt 首句提取（确定性关键词表，
  与 d2_corpus_validate 的 HOWTO_MARKERS 同族）。
- 无法确定判别的 → `ambiguous` 桶，按周抽样 5 条人工复核，复核结果回填
  校准关键词表（修订须记录于 D2_PLAN §8）。

## 5. 读出指标（B4）

- 仲裁句遵守率 = 正确 twin_choice / (正确+错误)；对照 A3 合成复测值
  （quant 95.8% / webdocs 94.1% 的 within 臂准确率），确认判据：
  生产值 ≥ 合成值 −10pp。
- D2-5 跟踪：`channel_confusion` 与 `repeated_failure` 的绝对计数与
  会话占比。
- 兜底（用户预授权）：窗开启 4 周内 twin_choice <30 → 记
  `inconclusive-underpowered`，不阻塞任何项。
