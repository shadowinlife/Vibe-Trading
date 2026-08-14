# Vibe-Trading 前端集成知识库（Frontend Integration Knowledge Base）

> **用途**: 为在 IM 插件视图层重建 Vibe-Trading 同类能力的前端团队提供**协议级**开发上下文。
> **定位**: 本知识库描述的是**后端对外契约**（REST / SSE / 产物文件格式），不是 React 参考实现的内部细节——参考实现（`frontend/`）仅作为契约消费侧的参照。
> **校对日期**: 2026-08-14 · **校对方法**: 每项端点/字段/枚举/事件均双向核对后端路由源码与 `frontend/src/lib/api.ts` 类型契约，不确定处显式标注「源码未明确」。
> **配套文档**: 能力差距与迭代优先级见 [`../FRONTEND_CAPABILITY_GAP_ANALYSIS.md`](../FRONTEND_CAPABILITY_GAP_ANALYSIS.md)。

---

## 阅读顺序建议

1. **先读 00 篇**——架构、认证（Bearer + SSE 一次性票据）、错误约定、SSE 传输通用机制。所有分篇都假定你已读 00 篇，且不重复其内容。
2. **再读 01 篇**——会话与聊天 SSE 事件协议是最大、最核心的协议面；IM 视图层的主要工作量在这里。
3. 按你要重建的功能选读分篇（见索引表）。
4. **实现前通读 09 篇**——全量枚举参考 + 数值校验指南 + 18 条集成陷阱 + 客户端验收清单。

## 文档索引

| # | 文档 | 覆盖 API 族 | 核心端点 |
|---|---|---|---|
| 00 | [架构与通用约定](./00-architecture-and-conventions.md) | 全局：部署形态 / 认证 / 错误 / CORS / SSE 传输机制 | `POST /auth/sse-ticket` |
| 01 | [会话 / 消息 / 研究目标 / 聊天 SSE 事件协议](./01-sessions-chat-sse.md) | 会话管理、流式聊天、工具进度、研究目标 | `/sessions*`、`/sessions/{id}/events` |
| 02 | [回测运行 / 产物 / 指标语义](./02-runs-backtest-artifacts.md) | 运行列表/详情、产物文件 Schema、指标单位 | `/runs*` |
| 03 | [Alpha Zoo 因子库](./03-alpha-zoo.md) | 因子目录 / Bench / Compare + SSE 进度流 | `/alpha/*` |
| 04 | [Swarm 多智能体团队](./04-swarm.md) | 预设 / 运行 / SSE 流 / 聊天桥接事件 | `/swarm/*` |
| 05 | [相关性与机制时间线](./05-correlation-regime.md) | 滚动相关矩阵、边密度 FUSED 机制 | `/correlation`、`/correlation/regime` |
| 06 | [定时研究](./06-scheduled-research.md) | 任务 CRUD、cron/interval 语义、Playbook 模板 | `/scheduled-runs*` |
| 07 | [实盘交易运行时](./07-live-trading-runtime.md) | Mandate 承诺 / Kill-switch / Runner / live SSE 事件 | `/live/*`、`/mandate/commit` |
| 08 | [设置 / IM 通道 / 上传 / 系统](./08-settings-channels-uploads.md) | LLM/数据源设置、通道状态、上传约束、健康探针 | `/settings/*`、`/channels/*`、`/upload`、`/health` |
| 09 | [枚举参考 / 数值校验 / 集成陷阱](./09-enums-validation-pitfalls.md) | 横切：全部枚举值、单位约定、验收清单 | （横切） |

## 关键事实速览（实现前必读）

- **认证**：配置 `API_AUTH_KEY` 后**所有**客户端（含 loopback）都必须携带 `Authorization: Bearer <key>`；SSE 流用一次性票据（`POST /auth/sse-ticket` → `?ticket=`，60 秒有效、首次使用即销毁）。详见 00 篇 §3。
- **SSE 续传**：会话流重连用**查询参数** `?Last-Event-ID=<id>`（swarm 流用 HTTP 头）；回放可能重复投递，消费端必须去重/幂等。详见 00 篇 §5、09 篇 §3。
- **时间戳三制并存**：session/goal 用 ISO UTC 字符串；scheduled jobs 用 **epoch 毫秒**；live runner `last_tick` 用 epoch 毫秒。详见 09 篇 §2.5。
- **数值约定**：收益/回撤/胜率类指标为**小数**（0.12 = 12%），`trades.csv` 的 `return_pct` 是**百分点**（5.23 = 5.23%）；产物 CSV 行是**字符串值**记录；`NaN`/`Infinity` 已被后端归一为 `null`。详见 02 篇 §5、09 篇 §2。
- **默认关闭项**：定时执行器（`VIBE_TRADING_ENABLE_SCHEDULER=1`）、shell 工具（HTTP 面默认不暴露）。
- **嵌入限制**：响应携带 `X-Frame-Options: DENY`——IM 插件若用 iframe 嵌入会被拦截，请走纯 API 渲染。

## 维护约定

- 后端新增/变更端点、SSE 事件、枚举值时，同步更新对应分篇并在 09 篇登记；事件类型变更必须同时核对 `frontend/src/hooks/useSSE.ts` 的 `knownTypes` 订阅清单（参考实现只订阅清单内事件）。
- 字段级不确定处保持「源码未明确」标注，不做推测性补全。
- 各篇头部「事实来源」列表是该篇的权威源码清单，审阅时优先对照这些文件。
