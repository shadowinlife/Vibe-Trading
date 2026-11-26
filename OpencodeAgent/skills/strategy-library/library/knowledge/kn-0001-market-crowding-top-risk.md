---
id: kn-0001
type: knowledge
title: 市场拥挤/过热与顶部风险（A 股）
status: research_only
markets: [ashare]
asset_classes: [equity]
horizon: 数周至数月（顶部预警窗口）
indicators: [in-0003]
related: [kn-0003]
sources:
  - title: 镜像仓 escape-top-microstructure 技能文件（A 股顶部预警信号框架与验证状态纪律）
    url: https://github.com/shadowinlife/vibetrading-opencode-instruct/blob/main/skills/escape-top-microstructure/SKILL.md
    date: 2026-08-18
intended_use: A 股大盘与组合层面的顶部风险预警、拥挤度判断与仓位风控辅助；与估值类条目（kn-0003）组合使用。
limitations: 本条目整体为 research_only，集成判定未在本库独立复验；依赖 ClickHouse/Tushare 数据（当前 worker 环境无 ClickHouse，计算受限）；仅适用 A 股；信号是决策辅助，不是交易指令。
created: 2026-11-25
updated: 2026-11-25
---

# 市场拥挤/过热与顶部风险（A 股）

## 主张

A 股成交集中度抬升、两融加杠杆与价格/成交出现背离等过热信号共振时，大盘顶部风险显著上升，应降低风险暴露而非追涨。

## 机制（为什么）

- **成交集中度**：成交额排名前 5% 个股占全市场成交额的份额抬升，说明资金向少数热门股抱团，市场宽度恶化，边际买家趋于枯竭，一旦抱团瓦解容易引发连锁回撤。
- **两融背离**：融资余额或融资买入额继续创新高，而指数或两市成交额不再创新高，说明杠杆资金在高位接盘、上涨动能衰竭，是典型的顶部区域特征。
- **波动扩张**：顶部区域常伴随恐慌性换手与波动率放大，与拥挤信号互为印证。

镜像仓 escape-top 框架将约 15 个信号族经 7 门验证（覆盖度/选择性/方向性/统计分离/稳健性/子周期/相关性）分级后，集成输出 RED/YELLOW/GREEN 市场状态判定。

**验证状态（引用纪律）**：其子信号两融背离（margin_divergence）/波动扩张（volatility_atr_expansion）已在镜像仓通过 7 门验证，证据见来源链接的[技能文件](https://github.com/shadowinlife/vibetrading-opencode-instruct/blob/main/skills/escape-top-microstructure/SKILL.md)；其余信号族为 RESEARCH_ONLY 或 BLOCKED_BY_DATA（如 winner_rate_pressure 依赖的 stk_cyq_perf 已停止同步），引用时必须标注其验证分类。本条目作为集成框架整体未在本库独立复验，故标 research_only。

## 用法

1. 用 [in-0003](../indicators/in-0003-turnover-concentration-margin-divergence.md) 计算成交集中度与两融背离，取得当前读数与数据覆盖区间。
2. 与估值分位（[kn-0003](kn-0003-valuation-percentile-mean-reversion.md) / in-0001）交叉印证：过热 + 高估值分位共振时顶部风险置信度提高；信号冲突时必须显式呈现，不得只挑有利证据。
3. 任何信号输出必须附带其验证分类与数据覆盖区间；数据不可达时明说，不得静默降级为中性结论。

## 失效情形

- 强趋势市中过热信号可能长期钝化，早预警预设下误报率高（宁缺毋滥的 strong 预设与早预警的 early 预设取舍不同）。
- 数据缺失或停更（如 stk_cyq_perf 停止同步）导致部分信号族不可用。
- 市场杠杆结构变化（两融占成交比重趋势性下降）会削弱两融背离信号的含义。
- 信号是决策辅助，不是交易指令；不能单独作为买卖依据。
