---
title: F9 模拟盘撮合引擎（paper_trade_step）
description: pt-v1.1 确定性模拟盘撮合引擎：三市场逐日撮合、AM-14 冻结拒单序、Decimal 费用、JCS 状态哈希、in-band 错误信封、AM-9 联邦行情。查工具契约、费用/limit 语义、镜像交付证据或上游回流前置时读这里。触发词：paper_trade_step、模拟盘、撮合、pt-v1.1、paper trading、F9。
type: delta
status: active
created: 2026-10-20
updated: 2026-10-20
tags: [paper-trading, engine, mcp, tool-contract, pt-v1.1]
related: [../branch/MYMAIN_DIVERGENCE.md, ../../PAPER_TRADING_ENGINE_DELIVERY.md]
---

# F9 模拟盘撮合引擎

> 一句话定位：给 RM 侧模拟盘账本提供确定性撮合与记账面的独立工具包。agent 只产委托，撮合、费用、持仓与 NAV 100% 在引擎内计算；research-only，绝不下实盘单。

## 能力

- pt-v1.1 冻结契约：`orders` 单日模式（AM-3）与 `strategy_path` 多日模式（R2，AST 硬化子进程执行器）。
- 三市场规则矩阵：cn_a T+1、涨跌停与 100 股整手；hk 逐标的 board lot + T+0；us 1 股 + T+0。费用全程 `Decimal` + `ROUND_HALF_UP`，金额路径禁用内建 `round()`。
- AM-14 冻结拒单序：结构性拒因先于财务性拒因，同一委托取首个命中。
- AM-7 limit 语义：买 `close <= limit` 成交、卖 `close >= limit` 成交，成交价 = close；违反返回 `limit_not_marketable`；涨跌停优先。
- AM-9 联邦行情：cn_a = ClickHouse T-1 历史 + 网络源当日 bar 合并；hk/us = 网络源。hk/us 零日历 fail-closed（AM-6），目标日全市场零 bar 返回 in-band `PAPER_DATA_NOT_READY`。
- R6 拆股检测器：干净比率 + 成交量确认 + NAV 连续性三重门；非干净不连续 fail-closed 拒单，不静默调账。
- 确定性：引擎模块零墙钟入算、顺序冻结、JCS `received_state_hash` 回显、错误信封走 `result.content[0].text` 单 JSON 通道，绝不走 transport error。
- 零副作用 / 零凭据 / 零实盘 connector：purity 门对 runtime root 与 cwd 做运行前后文件树 sha 对账。

## 关键文件与开关

| 文件 / 开关 | 作用 |
|---|---|
| `agent/src/tools/paper_trade/` | 引擎整包：`schema` / `state_hash` / `calendar` / `limits_cn_a` / `data` / `rules` / `engine` / `corporate_actions` / `wire` / `strategy_exec` / `tool` |
| `agent/mcp_server.py` | 手写 `@mcp.tool paper_trade_step` wrapper（VD-12 双层注册；裸名 `paper_trade_step`，MCP 面自动加前缀） |
| `schema/tool_contracts/paper_trade_step-v1.1.json` | 仓内首个工具契约 schema（成功与错误信封，`final`） |
| `agent/tests/test_paper_trade_*.py` + `agent/tests/fixtures/paper_trade/` | T1-T13 测试面、三市场 golden、JCS 12 向量、录制与合成 fixture、策略样例 |
| 无功能开关 | MCP 面恒注册（OFF=86 / ON=91）。cn_a 数据腿依赖 `CLICKHOUSE_*`；不可达时 bar 腿降级 network-only，而 cn_a 日历硬门转 transport |

## 开发历史

- 2026-10-18 需求件派遣（pt-v1.0）与 Planbook 建立；DD 探针批完成（DD-2 raw 活体确认、DD-4 港股手数 idx-60、DD-5/DD-6 活体闭环）。
- 2026-10-19 pt-v1.1 获 RM 书面批准（14/14 AM，5 条非阻塞注记），G1 开门；同日 T2 数据层至 T5 契约面收口（`de069528` 至 `8150f203`）。
- 2026-10-20 T6 注册（`53f669a5`，MCP 86/91、agent tools 117）、T7 strategy_path（`c1c71bcc`、`54d915aa`）、T8 测试面（`750f2920`）、T9a 镜像（`a34c6ffc`）、T9b 容器自测与活体 smoke，T10 交付。
- 按「每个 feature 一个独立 commit」约定以单个 DCO commit tag `f9-pt-v1.1` 交付（开发期 22 个细粒度提交已压缩，压缩前后 `git diff` 为空），DCO 签名、零 AI trailer。完整清单与逐任务证据见仓根 [交付通报](../../PAPER_TRADING_ENGINE_DELIVERY.md) 与 `.omo/evidence/paper-trading-mymain/STEP{2..9B}-*.md`。

## 验证

- `agent/tests/test_paper_trade_*.py`：**409 passed**（宿主普通模式 99.49s，`--disable-socket` 98.14s）；容器内 **409 passed / 9 warnings / 42.74s**（告警来自既有 ccxt loader）。
- 镜像 `opencode-serve:v3.8.0-mymain`，`.Id sha256:1fa24461da83...`，amd64/linux，已 push 至 `registry.cn-huhehaote.aliyuncs.com/gm_1/opencode@sha256:d442a38f3ebc57bfeef6493ca86794280d38119432217ca09bbfacdc14a66fd5`。`smoke-v380.sh` Stage 1 / 1b / 2 全绿。
- golden sha：cn_a `a19ac434...`、hk `9257ea18...`、us `143896c9...`、MANIFEST `bd170835...`；JCS `416989a1...`（12 向量零改动）。
- 活体（2026-10-20，容器内）：hk `00700.HK buy 100 @417.80`（tencent，fee 55.79），us `AAPL.US buy 10 @342.94`（sina，fee 0.99）均 PASS；cn_a 合并帧 `source=clickhouse+tencent(merged)`、`ch_as_of=2026-10-16`、`network_as_of=2026-10-19` 数据层实证，**fill 级被既有 `get_trade_calendar` 日期拼写缺陷阻断**，不声称 PASS。
- 证据：`.omo/evidence/paper-trading-mymain/STEP9B-CONTAINER-SELFTEST.md`、`STEP9B-LIVE-SMOKE.md`、`STEP10-DELIVERY-RECEIPT.md`；限制与阻断明细见交付通报 §11.2 与 §11.3。

## 状态与上游关系

- mymain 本地能力，**上游无对应**；尚未回流，当前**不提交 PR**。
- 贡献队列 **⑨**（[../branch/MYMAIN_DIVERGENCE.md](../branch/MYMAIN_DIVERGENCE.md) §2.3）：前置 = RM E2E 通过 + ≥1 周生产运行，之后以独立 PR 回流，PR 按当时上游基数重述计数。
- 交付状态：**已交付待 RM E2E（2026-10-20）**。RM E2E 前必须修复 cn_a 日历缺陷（`agent/src/clickhouse_connector.py:501`），并核查 CH `stk_factor_pro` 周末数据入库。
- 编号纪律：F6 不存在（语义层归 F5），F8 为引擎桥墓碑；本卡不复用也不修改这两个编号。