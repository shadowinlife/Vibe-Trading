# D4-TC：trading-connector-agent 准入裁决（mini-admission，DEC-5 执行）

> 日期：2026-08-30 ｜ 协议：《子代理准入协议》v1.0 全流程 ｜ 裁决：**ADMIT**
> 前置：DEC-5 通过（Tier-0/Tier-1 分层；写族永不进子代理；用户已有连接器配置）

## 门禁结果（语料 v3 = 395 条；判官 qwen3.8-max + kimi-k3，temp 0，模板 hash 24809ade 不变）

| 判据 | 阈值 | 实测 | 裁决 |
|---|---|---|---|
| R1 域内召回 | ≥ 0.85 | **74/76 = 0.974**（38 目标 query × 2 判官） | ✅ |
| R2 误委派率 | ≤ 5% | **0/714 = 0.0000**（零误入） | ✅ |
| R3 边界仲裁 | ≥ 85% | **40/40 = 1.000**（block C 边界 20 条 × 2 判官） | ✅ |
| 回归 | 各已准入路由降幅 ≤ 5pp | 全部在容差内（web-docs −2.8pp 最大；market-data/derivatives/funds-fi/risk-portfolio/valuation 反而上升） | ✅ |
| 噪声地板 | 探针一致率 | 0.875 / 0.875（8 query × 3 repeat；D01-008 在 qwen 下 0.67 不稳定——与 B 批 qwen ρ=0.875 同带宽，判据余量远超噪声带） | ✅ 记录 |

**一轮通过**（未触发修订轮）。

## 唯一失分 query 与争议登记

- **D16-008**（"用连接器取AAPL的历史K线"，v2 冻结标签 expected=trading_history）：
  qwen → market-data-agent、kimi → direct，两判官以不同方向同失。
  **争议定性**：query 的对象是 K 线 bars，而连接器族没有 bars 工具
  （quote=快照/history=成交记录），判官的"失分"在语义上可辩护——这是
  v2 冻结期的标签问题，不是 description 缺陷。**按纪律不改标签凑分**
  （R1 已远超门槛），登记为第 4 项争议（与 BND-003 / D4R-B-069 / D05-010 并列留痕）。

## 语料与定义

- 定义：`d4_batch/subagent_trading_connector_agent.yaml`（v1；Tier-0 只读八件套，
  K21 入口顺序，NEED_INPUT 交回下单请求，无连接器时全域坍缩声明）
- 语料：`d4_batch/queries_d4_routing_all_v3.yaml`（395 条 = v2 353 + block C 42；
  v2 的 D16-001..008 按 DEC-5 新契约改判 route，同 D05-006 先例，source 字段留痕）
- 验证器：`d4_batch/d4_corpus_validate_v3.py`（PASS）
- 轨迹：`artifacts/d_routing_trace_{qwen3.8-max,kimi-k3}_d4tc.jsonl` + probe 同名文件
- 裁决脚本：`d4_batch/d4tc_verdict.py`（可复跑）

## 生产同步清单（本裁决的执行侧）

mymain 侧：vibe-trading-tools.json 放开 Tier-0 八件读工具（写族保持全局 deny）→
subagents.json 第 12 条 → prompts/trading_connector_agent.md → AGENTS.md 路由政策
+1 行 → README 11→12 → 测试锚点 → 渲染验证 + 冒烟。
