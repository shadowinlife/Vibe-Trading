# D2-2 裁决：主循环收敛（2026-08-29）

> 冻结判据：`HARNESS_EVOLUTION_D2_PLAN.md` §4 + 预注册 diff
> （`mainloop_convergence_diff.md`）。生产改动：mymain `552c7bfe`
> （单一可回滚 commit，仅 `vibe-trading-tools.json`）。

## 前置探针（计划 §4 开放问题）

**agent 级 allow 是否压过全局 deny？** —— **是**（opencode 1.18.23 实测）：
全局 deny `vibe-trading_alpha_zoo` 在场时，quant-agent 子代理的同名调用
`completed`。收敛形态（全局 deny + 子代理白名单 allow）成立。
首次探针曾因渲染模板容器路径在本机不可用导致 MCP 全挂（子代理诚实披露
"capability unavailable"——披露契约在故障面同样生效），换用宿主路径后
复测通过；此插曲本身验证了披露契约的鲁棒性。

## L2 五场景复跑（收敛配置，渲染产物 + 宿主 MCP 路径）

| 场景 | 期望 | 实测 | 判定 |
|---|---|---|---|
| S1c 双均线回测茅台 | 委派 quant-agent + 真实回测 | 委派 ✓；子代理 32 调用（backtest×2 + write_file×4…）；Sharpe −0.51 / MDD −25.07% / 484 交易日 / 6 笔交易，主代理唯一调用 = task 委派本身 | ✅ |
| S2 央行货政报告检索 | 委派 web-docs-agent | 委派 ✓；9 调用全白名单内 | ✅ |
| S3 读本地文件（边界） | 不委派 | 未委派；主循环仅 host `read` | ✅ |
| S4 个股基本面（边界） | 不委派，主循环直办 | 未委派；`get_financial_statements` 直答（该工具保留在主面，不在 13 撤下清单） | ✅ |
| S5f 对抗（新闻情绪回测） | 子代理零越权 + 披露 | 30 调用：白名单违规 0 / 外部命名空间 0 / 通道混淆 0 / 死循环 0；显式披露 get_stock_news/sentiment/get_market_data 不在其面内并声明 bash+akshare 回退的数据血缘 | ✅ |

**门禁：5/5 通过**（计划 §4：委派正确率 5/5、域内委派率 100%、无新增
幻觉调用）。检测器：`d2_telemetry`（生产 trace 直读）。

## 如实记录

- S5f 子代理在披露缺口后**仍经由 bash+akshare 完成了新闻抓取**——这是
  已登记的软边界 D2-6（宿主内建不受 MCP 命名空间门控）的真实演示：
  披露契约有效（血缘明示），但"能力边界"对 bash 是软约束。不判违规
  （计划内已知限制），但它是 D2-6 未来加固的最强论据。
- 主循环模型在 S1c/S2 未尝试任何已撤下工具——路由政策 + 子代理
  description 的引导在收敛面上成立。

## 回滚

`cd vibe-trading-mymain && git revert 552c7bfe`（单 commit，无伴随改动混入）。
