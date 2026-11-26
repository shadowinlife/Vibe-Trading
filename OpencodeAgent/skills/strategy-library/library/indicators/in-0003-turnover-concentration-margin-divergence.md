---
id: in-0003
type: indicator
title: 成交集中度与两融余额背离（A 股）
status: candidate
markets: [ashare]
asset_classes: [equity]
horizon: 日度
related: [kn-0001]
formula: |
  成交集中度 = 成交额排名前 5% 个股的成交额之和 / 全市场成交额 × 100%
  两融背离 = 融资余额（或融资买入额）趋势与指数/两市成交趋势的背离：
  融资序列创新高而指数或成交额未创新高（或滚动增速差持续为负）记为背离触发。
data_source: |
  镜像环境 ClickHouse ashare 仓库（个股日成交额、指数行情）+ Tushare margin / margin_detail
  （融资融券每日汇总/明细，2010 年起，交易所每日更新）。
  注意：当前 worker 环境无 ClickHouse，本指标计算受限；镜像环境可用。
computation: |
  1. 取全市场个股日成交额，降序排序，取前 5% 个股成交额求和，除以全市场成交额得集中度；
  2. 取两融融资余额/融资买入额日度序列（Tushare margin 汇总或镜像 ClickHouse 表）；
  3. 对比融资序列与指数/两市成交序列的滚动新高状态与增速差，标记背离触发；
  4. 输出集中度序列与背离标记，附数据覆盖区间；数据不可达时明说（fail loud），不得静默降级。
unit: 成交集中度为 %；背离为布尔触发标记，增速差为百分点。
sources:
  - title: 镜像仓 escape-top-microstructure 技能文件（成交集中度/两融背离信号族定义与验证状态）
    url: https://github.com/shadowinlife/vibetrading-opencode-instruct/blob/main/skills/escape-top-microstructure/SKILL.md
    date: 2026-08-18
  - title: Tushare 融资融券交易明细接口文档（margin_detail，含融资余额/融资买入额字段与单位说明）
    url: https://tushare.pro/document/2?doc_id=59
    date: 2026-11-25
intended_use: A 股拥挤度与杠杆资金动能的日度度量，供 kn-0001（市场拥挤/过热与顶部风险）使用。
limitations: candidate，本条目口径未在本库独立复验；依赖 ClickHouse/Tushare（当前 worker 环境无 ClickHouse，计算受限，如实标注）；两融背离子信号已在镜像仓验证，成交集中度为研究级；Tushare 接口需积分权限。
created: 2026-11-25
updated: 2026-11-25
---

# 成交集中度与两融余额背离（A 股）

## 口径说明

- **成交集中度**：escape-top 框架定义为 top5% 个股成交额占全市场成交额份额。份额抬升 = 资金抱团、市场宽度恶化。
- **两融背离**：融资余额/融资买入额与指数或两市成交的趋势背离。镜像仓已验证子信号 `margin_divergence` 即属此类。
- **验证状态（引用纪律）**：其子信号两融背离（margin_divergence）/波动扩张（volatility_atr_expansion）已在镜像仓通过 7 门验证（证据：来源技能文件链接）；成交集中度信号族为研究级（RESEARCH_ONLY）。本指标条目整体标 candidate：口径整理自镜像技能与公开接口文档，未在本库独立复验。
- **数据可用性（如实标注）**：计算依赖镜像环境 ClickHouse ashare 仓库与 Tushare 两融接口；**当前 worker 环境无 ClickHouse，计算受限**。Tushare margin/margin_detail 需相应积分权限。数据不可达时必须明说，不得静默降级为中性结论。

## 计算步骤

1. 全市场个股日成交额降序排序，前 5% 个股成交额求和 / 全市场成交额 × 100%。
2. 取融资余额（rzye）与融资买入额（rzmre）日度序列（Tushare margin 汇总或镜像表；单位：元，注意千元/万元换算规则）。
3. 计算融资序列与指数/成交序列的滚动新高状态与增速差，标记背离。
4. 输出序列 + 触发标记 + 数据覆盖区间。

## 解读注意

- 集中度与背离是顶部风险的辅助证据，需与估值分位（in-0001）等组合印证，不单独作为买卖依据。
- 两融占成交比重趋势性下降时，背离信号含义减弱。
- 强趋势市中信号可能长期钝化；早预警与高选择性预设的取舍见 kn-0001。
- 单位换算（千元/万元/元、手/股）必须遵守数据源登记规则，不得绕过。
