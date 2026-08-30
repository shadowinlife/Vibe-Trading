# D4 准入评审 Round 1 裁决（2026-08-29）——**未通过，进入修订轮**

> 判官：qwen3.8-max + kimi-k3；语料 `queries_d4_routing_all_v2.yaml`
> （353 条 × 2 = 706 判官调用，trace `d_routing_trace_*_d4.jsonl`）。
> 门禁（D2_PLAN §5 冻结）：R1 候选召回 ≥0.85 / R2 误委派 ≤5% /
> R3 边界 ≥85%。

## 语料标签再基线（先于此裁决的修订，D2_PLAN §8 已登记）

D 批控制组的 `direct` 标签语义为"无代理人时代由主循环直办"。候选面从 2 → 11
后，该标签对新候选域失效——按语料自身的生成规则（"route labels derived by
DOMAIN_ROUTES"）机械扩展域映射，再基线 99 条控制标签。BND 逐条复核：
BND-005（交割单分析）→ user-analytics-agent、BND-010（涨幅榜）→
market-data-agent，其余维持 direct。真正 direct 控制组 = 41 条
（D05 技术分析 / D16 交易连接器 / D17 编排 / D18 QVeris + BND 编码类）。

## 门禁结果

| 门禁 | 结果 | 判定 |
|---|---|---|
| R1 候选召回 | 10/11 过（92.5%–100%）；**fundamentals-text-agent 53/64 = 82.8% < 85%** | ❌ 一候选未过线 |
| R2 误委派（41 真 direct × 2） | 66/82 守住，误委派 **19.51%** | ❌ 系统级未过 |
| R3 边界仲裁 | 38/40 = 95.0% | ✅ |
| 试点回归 | quant-agent 96.2%、web-docs-agent 100%（11 卡片竞争下不退化） | ✅ |

## 处置（按预写划掉语句执行）

1. **fundamentals-text-agent：本轮 FALSIFIED（R1 82.8% < 85%）**。
   失分分解（11 次 miss，双判官高度一致）：
   - 分析面声明不足 ×5：三表勾稽/盈利质量分析、10-K 信号提取被读成
     "分析非获取"→ direct（候选 description 过度 fetch 味）；
   - 与 market-data 的边界冲突 ×2：基本面字段筛选（PE/ROE）双方 description
     都可合法认领——真实设计缺口，需双侧 NOT-for 仲裁；
   - 标签争议 ×1：D03-005 "全球财经大新闻"被判 web-docs（per-stock 新闻 vs
     全局网读的措辞缺口）；
   - 边界条目自身欠消歧 ×1：D4R-B-069 "构建筛选流程"读作方法论，两判官分裂
     （direct/quant）——登记为措辞存疑条目，不改标签。
2. **R2 系统级 FAIL（19.51%）**，分解：
   - D18 QVeris → market-data ×4 条：market-data-agent description 的
     "数据源路由"措辞合法覆盖了"免费源没有→付费市场"——**描述词溢出**，
     修订其 NOT-for 排除付费市场；
   - D05 技术分析 → quant-agent ×2 条：quant-agent 的"K线形态识别"措辞
     外溢到纯指标读数——收紧 NOT-for；
   - D16-008/D17-010 → market-data 各 1 条：同源溢出；
   - BND-003（GitHub README）→ web-docs ×2 判官：D 批裁决为 direct 的
     既定标签，维持不改，计为候选面扩大的真实拉力成本；
   - BND-006（本地配置文件）→ quant-agent ×2：判官错误，无描述词成因。
   **即便剔除全部描述词溢出项，残余 7.3% 仍 >5%——候选面扩大本身产生
   委派拉力，需描述层负向约束（NOT-for）系统性收紧后复测。**
3. R3 边界 95% 通过，说明 NOT-for 仲裁条款在**写了的地方**有效——
   失败集中在没写到的接缝。

## Round 2 修订动作（修订后全量复测，非局部）

1. fundamentals-text-agent description v2：认领"财报分析/信号提取"语义面 +
   全局财经新闻馈送 + 基本面字段筛选；NOT-for 增"行情/涨跌幅筛选 →
   market-data-agent"。
2. market-data-agent description v2：NOT-for 增"付费数据市场（QVeris）→
   主循环"、"基本面字段筛选 → fundamentals-text-agent"、"技术指标信号读数
   → 主循环"。
3. quant-agent description v2：NOT-for 增"纯技术指标读数（无回测）→ 主循环"。
4. 修订只动 description 路由信号层，不动白名单。复测为全量 353×2
   （路由评估是全局博弈，不接受局部复测）。

## 如实记录

- 本轮拒绝为了过线而继续改标签：R2 的失败是真实系统属性（描述词溢出 +
  候选面拉力），不是测量 artifact——与再基线（语义失效标签的机械修复，
  规则先于测量冻结）性质不同，分别记录在案。
- 判官间一致性：kimi/qwen 在 fundamentals-text 失分条目上 8/11 一致，
  证据方向稳定。
