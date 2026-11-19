# D4 准入评审最终裁决（2026-08-29，Round 3）——**全部通过**

> 三轮迭代的完整轨迹：R1（FAIL：fundamentals-text 82.8% + R2 19.5%）→
> R2（FAIL：valuation 84.8% 差 0.2pp + R2 6.25%）→ R3（全过）。
> 每轮修订只动 description 路由信号层（白名单不动），修订内容全部在
> 采集前冻结并记录于 D2_PLAN §8。判官：qwen3.8-max + kimi-k3；
> 353 条语料 × 2 判官 = 706 调用/轮；trace `d_routing_trace_*_d4r3.jsonl`。

## Round 3 结果（冻结门禁）

| 门禁 | 结果 | 判定 |
|---|---|---|
| R1 候选召回 ≥0.85 | **11/11 全过**（87.5%–100%） | ✅ |
| R2 误委派 ≤5% | **4/80 = 5.00%**（含边界） | ✅ |
| R3 边界仲裁 ≥85% | **39/40 = 97.5%** | ✅ |
| 试点回归 | quant 95.1% / web-docs 100%（11 卡片竞争下） | ✅ |

R2 残余 4 次误委派（如实记录）：D05-010→quant（无动词的策略概念提及，
语义接缝）、BND-003→web-docs ×2（GitHub README 单页读——D 批既定 direct
标签，双判官两轮一致反对该标签，记录为**持续争议标签**，是候选面扩大的
已知拉力成本）、BND-006→quant ×1（判官错误）。R3 唯一 miss（B-071）为
判官输出无效（None），非误路由。

## 准入裁决

**9 个候选子代理全部通过准入**（quant-agent、web-docs-agent 为既有试点，
回归通过）：

market-data-agent、fundamentals-text-agent、derivatives-agent、
risk-portfolio-agent、valuation-agent、macro-sector-agent、altdata-agent、
funds-fi-agent、user-analytics-agent

- **orchestrator**：定义评审阶段诚实拒绝（编排是主循环本职），不进入评测。
- **trading-connector-agent**：安全评审挂起（trading_* 全局 deny 中），
  本轮不评测、不准入。

## 三轮迭代证据链（修订全部限于 description 路由信号层）

| 轮 | fundamentals-text | valuation | R2 | 关键修订 |
|---|---|---|---|---|
| R1 | 82.8% ❌ | —（R1 未测出） | 19.5% ❌ | FT 补分析面/全局新闻/字段筛选；MD 排除付费市场等 |
| R2 | 96.9% ✅ | 84.8% ❌（差0.2pp） | 6.25% ❌ | VL 补三表联动/事件驱动构建/瓶颈挖掘/报告撰写动词；FT 回收"预测建模"越界 |
| R3 | 98.4% ✅ | 98.5% ✅ | 5.00% ✅ | — |

**核心经验**：description 即路由信号，NOT-for 条款做负向工作——失败的
接缝集中在没写 NOT-for 的地方；R3 的 97.5% 说明写到位的地方仲裁有效。

## 后续（准入后的生产同步，新 session 继续）

1. 将 9 个准入候选合入生产 mymain（subagents.json + prompts/ 撰写 +
   render_config 无需改动 + AGENTS.md 路由政策扩写 + L2 冒烟）；
2. quant-agent 的 D4 侧 NOT-for 收紧补丁回写生产（当前生产为 v1 描述）；
3. trading-connector 安全评审另立工作项；
4. D4R-B-069 措辞存疑条目与 BND-003 争议标签归档为语料已知限制。
