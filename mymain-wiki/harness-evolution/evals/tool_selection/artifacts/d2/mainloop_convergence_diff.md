# D2-2 预注册：主循环收敛工具清单 diff（执行前冻结，2026-08-29）

> 依据 `../../../../HARNESS_EVOLUTION_D2_PLAN.md` §4：执行前必备产物的第 1、2 项。
> 本文件在任何配置改动前冻结；执行与此 diff 的偏差即触发回滚。

## 1. 撤下清单（13 个工具，从主代理 MCP 面移入 deny）

| 工具 | 去向 | 理由 |
|---|---|---|
| alpha_zoo | quant-agent 独占 | 子代理白名单核心 |
| alpha_bench | quant-agent 独占 | 同上 |
| factor_analysis | quant-agent 独占 | 同上 |
| list_strategies | quant-agent 独占 | 同上 |
| query_strategies | quant-agent 独占 | 同上 |
| get_strategy_evidence | quant-agent 独占 | 同上 |
| backtest | quant-agent 独占 | 同上 |
| pattern_recognition | quant-agent 独占 | 读回测工作区产物 |
| read_file (vibe-trading) | quant-agent 独占 | 回测工作区读写归子代理 |
| write_file (vibe-trading) | quant-agent 独占 | 同上 |
| web_search | web-docs-agent 独占 | 检索归文档专员 |
| read_url | web-docs-agent 独占 | 同上 |
| read_document | web-docs-agent 独占 | 同上 |

## 2. 双驻例外（1 个，不撤）

| 工具 | 位置 | 论证 |
|---|---|---|
| quantlib_call | 主代理 **和** quant-agent | 生产路由契约（quant-agent description 的 NOT-for 条款）明确规定"存量组合的 VaR/Sharpe 等独立风险指标 → 主代理的 quantlib_call"。若从主面撤下，主代理将无法履行该条款，路由政策与工具面自相矛盾。quant-agent 持有它是回测数学所需。双驻是契约设计，不是漏洞。 |

## 3. 主代理保留面（收敛后）

59 − 13 = **46 个 MCP 工具** + 宿主内建 + 技能面不变。子代理权限块不动
（deny 全命名空间 + 白名单 allow 已在生产）。

## 4. 待验证的开放技术问题（探针先于 L2）

opencode 1.18.23 权限求值顺序：**agent 级 allow 是否压过全局 deny**
（生产冒烟只证过"同一块内后匹配胜出"——子代理块内 `vibe-trading_*: deny`
+ 白名单 allow）。收敛后形态变为全局 deny 13 个工具 + 子代理块内 allow
同名工具。若子代理 allow 不能压过全局 deny，子代理将失去全部 13 个工具
——L2 前必须先探针验证，失败则回滚本 diff 并改走"主代理侧按 agent
（主代理=build/Sisyphus）单独 deny"的替代路径。

## 5. 回滚约定

收敛改动 = mymain 上**单一可回滚 commit**（只动
`OpencodeAgent/config/vibe-trading-tools.json` 一处清单 + 重渲染产物）。
回滚 = `git revert` 该 commit。任何伴随改动（文档等）另起 commit，
不混入回滚单元。

## 6. 通过判据（冻结，对齐 D2_PLAN §4）

L2 五场景复跑：S1c（量化任务委派 quant-agent 并完成真实回测）、S2
（检索委派 web-docs-agent）、S3/S4（边界任务不委派且正常完成）、S5f
（对抗任务子代理零越权 + 披露契约）。5/5 通过 = 收敛成立；任何一场景
失败先按 diff 回滚再诊断。
