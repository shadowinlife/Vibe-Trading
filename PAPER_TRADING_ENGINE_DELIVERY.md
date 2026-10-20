# 模拟盘撮合引擎交付通报（paper_trade_step · pt-v1.1）

> 交付方：vibe-trading 仓（分支 `feat/paper-trading-engine`）｜接收方：aliyun-acs-agent-service（RM 仓）
> 交付日期：2026-10-20｜契约正典：`docs/PAPER_TRADING_ENGINE_REQUIREMENTS.md` pt-v1.1（2026-10-19 RM 书面批准）
> 交付状态：**已交付待 RM E2E**。VT 侧义务止于本通报与 T1-T13 回执；三市场 E2E、连续 3 交易日演进、杀箱续跑、补跑收敛、负路径与 NOT_READY 重试链由 RM 侧按需求件 §10 执行。
> 证据目录（本地，gitignored）：`.omo/evidence/paper-trading-mymain/STEP{0,1,2,3,4,5,6,7,8,9A,9B}-*.md`
> 本通报共 12 节，与需求件 §8 原 8 项 + AMENDMENT §4 增补 4 项一一对应。

## 1. commit 清单

基线 `2e37c6ff`（Step 1 脚手架）。本 feature 以**单个 commit** 交付：tag `f9-pt-v1.1`（`feat(paper-trade): add deterministic paper-trading matching engine (pt-v1.1)`），`git commit -s` 签名，DCO 1 条、AI trailer 0 条。按本分支「每个 feature 一个独立 commit」的历史约定，T2–T10 的开发期细粒度提交已压缩进该 commit，压缩前后代码树 `git diff` 为空；T9b 无 tracked 改动、无独立 commit。T10 回执见 `.omo/evidence/paper-trading-mymain/STEP10-DELIVERY-RECEIPT.md`。

该 commit 覆盖 59 个文件（+9104/−172）。hash 已随压缩失效，按任务面保留工作分解：

| 任务面 | 内容 |
|---|---|
| T2 数据层 | 三市场 loader 接线、AM-9 联邦 readiness、K=120 回看、价格 sanity 门 |
| T3 撮合核心 | 逐日循环、AM-14 冻结拒单序、三市场规则矩阵、零 bar 目标日 in-band NOT_READY |
| T4 拆股 | 注记 2 扫描语义、fail-closed、NAV 连续性门、价格容差 docstring 对齐 1e-3 |
| T5 契约 | state_hash fee 键名冻结、schema 冻结、wire 信封、契约 JSON 转正、golden 与 JCS 门、错误路径 fail-closed |
| T6 注册 | `paper_trade_step` BaseTool + MCP wrapper + 七 README/SKILL 计数、universe 预取 |
| T7 strategy_path | AST 硬化子进程执行器、B1 超时/异常冻结语义、universe 声明 |
| T8 测试面 | 性能门、purity 门、TODO-G1 零残留 |
| T9a 镜像 | `opencode-serve:v3.8.0-mymain` 打包与 smoke 门 |
| T10 交付 | 治理收口通报、需求件状态行、DIVERGENCE 队列、F9 卡片与索引/timeline |

## 2. T1-T13 单测回执

宿主环境 conda `legonanobot`（Python 3.12），fixture 全离线：

```text
pytest agent/tests/test_paper_trade_*.py -q
409 passed in 99.49s

pytest agent/tests/test_paper_trade_*.py -q --disable-socket
409 passed in 98.14s

容器内（镜像 vendor 代码 + volume 只读注入测试面）
409 passed, 9 warnings in 42.74s
```

零 failed、零 error、零 skip、零 xfail。9 条 warning 为 `agent/backtest/loaders/ccxt_loader.py` 的 NumPy timedelta DeprecationWarning，既有基础设施告警，**仅容器出现**（镜像 numpy 2.5.4）；宿主基线同命令重跑 409 passed / 0 warnings，故该 9 条 warning 不计入宿主结论。T1-T13 分门映射与各门命令输出见 `STEP8-TEST-RECEIPT.md`、`STEP9B-CONTAINER-SELFTEST.md`。

Golden 与 JCS sha256：

| 文件 | sha256 |
|---|---|
| `agent/tests/fixtures/paper_trade/golden/cn_a_orders_single_day.json` | `a19ac4342eaf37188567cd9a44e1e0ed67cf7ab5452a20b21a245b85388f1580` |
| `agent/tests/fixtures/paper_trade/golden/hk_orders_single_day.json` | `9257ea18f4a25f5f9318f04468c0c0dc61e64e9ec92b6d0c05f553034cd09d0f` |
| `agent/tests/fixtures/paper_trade/golden/us_orders_single_day.json` | `143896c9ada6bb437e9253fef124133ab3fd8fd74e3a0050af92492c2e8e0ca0` |
| `agent/tests/fixtures/paper_trade/golden/MANIFEST.json` | `bd170835eb749a0c0799f3383840f37ec5fcc4c48c361d3ba37e4e99040ee006` |
| `agent/tests/fixtures/paper_trade/jcs_vectors.json` | `416989a103cec460e015c8ab8fbe3b8a09ca5d0430ee39ae241d35b5a9945406` |

工程面：paper_trade 包与 `test_paper_trade_*.py` 的 ruff 全绿；`git diff --check` clean；TODO-G1 18 处零残留（已测试化）。全 `agent/tests` 树 ruff 存在 93 处**既有**错误（与本套件无关的上游测试文件），本交付不声称全树 ruff 干净。

## 3. 三市场 golden 出参样本

完整文件见上表路径，以下为压缩摘录（字段值与文件逐字一致）。

cn_a（AM-9 合并帧，limit 启发式 provenance）：

```json
{"account_id":"pa_golden_cn_a","schema_version":"pt-v1.1","market":"cn_a",
 "data_provenance":{"source":"clickhouse+tencent(merged)","ch_as_of":"2026-10-19","network_as_of":"2026-10-20","bars_as_of":"2026-10-20","limit_provenance":["600519.SH@2026-10-20:limits:heuristic"]},
 "effective_fee_model":{"commission_rate":0.00025,"commission_min":5.0,"stamp_tax_sell":0.0005,"transfer_fee":1e-05},
 "fills":[{"symbol":"600519.SH","side":"buy","qty":100.0,"price":1421.0,"fee":36.95,"trade_date":"2026-10-20"}],
 "end_state":{"cash":57863.05,"positions":[{"symbol":"600519.SH","qty":100.0,"avg_cost":1421.0}]},
 "nav_series":[{"date":"2026-10-20","cash":57863.05,"position_value":142100.0,"nav":199963.05}],
 "rejected_orders":[],"skipped_days":[],"warnings":[]}
```

hk（tencent，默认板手 warning）：

```json
{"account_id":"pa_golden_hk","schema_version":"pt-v1.1","market":"hk",
 "data_provenance":{"source":"tencent","bars_as_of":"2026-10-16","network_as_of":"2026-10-16"},
 "effective_fee_model":{"commission_rate":0.0003,"commission_min":3.0,"stamp_tax_rate":0.001,"regulatory_levy_rate":3e-05},
 "fills":[{"symbol":"00700.HK","side":"buy","qty":100.0,"price":417.8,"fee":55.79,"trade_date":"2026-10-16"}],
 "end_state":{"cash":58164.21,"positions":[{"symbol":"00700.HK","qty":100.0,"avg_cost":417.8}]},
 "nav_series":[{"date":"2026-10-16","cash":58164.21,"position_value":41780.0,"nav":99944.21}],
 "rejected_orders":[],"skipped_days":[],
 "warnings":["lot assumption: 0700.HK sized on the default 100-share board lot (no lot table entry)"]}
```

us（sina）：

```json
{"account_id":"pa_golden_us","schema_version":"pt-v1.1","market":"us",
 "data_provenance":{"source":"sina","bars_as_of":"2026-10-16","network_as_of":"2026-10-16"},
 "effective_fee_model":{"commission_per_share":0.0049,"commission_min":0.99,"sec_fee_rate":0.0},
 "fills":[{"symbol":"AAPL.US","side":"buy","qty":10.0,"price":343.46,"fee":0.99,"trade_date":"2026-10-16"}],
 "end_state":{"cash":46564.41,"positions":[{"symbol":"AAPL.US","qty":10.0,"avg_cost":343.46}]},
 "nav_series":[{"date":"2026-10-16","cash":46564.41,"position_value":3434.6,"nav":49999.01}],
 "rejected_orders":[],"skipped_days":[],"warnings":[]}
```

## 4. DD-1..6 答案

### DD-1 既有 backtest 引擎接受度与 R2 工作量

既有 `BaseEngine` 不接受单日增量与起始持仓注入，也没有逐日事务语义。VT 侧没有修改 `agent/backtest/engines/*` 或 `BaseEngine`，而是新建独立 `agent/src/tools/paper_trade/` 包，只 import 既有公开原语（`ChinaAEngine.round_size`、`calc_commission`、`GlobalEquityEngine._hk_board_lot` 等），既有文件零修改。

工作量实测（boulder ledger 时间戳 + `git log`，非估算）：

| 项 | 观测值 |
|---|---|
| 实现窗口 | T2 开工 `2026-10-19T14:10:00Z`（22:10 CST）至 T9b 收尾 `2026-10-20T02:51:28Z`（10:51 CST），约 12.7 小时 |
| 日历跨度 | 2026-10-19 至 2026-10-20，2 个日历日（分支切出同日，T2 于当晚开工） |
| 任务数 | T2 至 T10 共 10 个任务；执行尝试 14 次（T3/T5/T6/T7 各 2 次） |
| commit 数 | 1 个 feature commit（tag `f9-pt-v1.1`）；开发期 22 个细粒度提交已按分支约定压缩，压缩前后代码树零差异 |
| 测试数 | 409 passed，宿主双 socket 模式各 99.49s / 98.14s，容器 42.74s |

R2 `strategy_path` 与 P0 在同一镜像周期交付：AST 硬化子进程执行器、每日 30s 预算、B1 冻结错误 message、三日链式账本与逐字节重放均有测试。RM 侧 P1 验收档期可按上述工作量排期，不需要等待第二个实现周期。

### DD-2 sina US dailyK 复权口径

**`raw` CONFIRMED LIVE**（2026-10-18 探针，`STEP0-PROBES.md` LANE-A）：

- AAPL 2020-08-31 4:1 拆股日 close-to-close 比率 **0.2585**（499.23 → 129.04）。纯拆股比率 0.25，残差为当日真实 +3.3% 行情。
- Yahoo 对照：`quote.close` 跨拆股连续，08-28 至 08-31 比率 1.0339；拆股事件 4:1（epoch 1598880600）显式返回。
- 拆股后逐 bar：sina 与 yahoo **Δ=0.00**；拆股前：sina = 4 × yahoo，误差仅 ±0.02（两位小数舍入）。
- 成交量：拆股前 yahoo/sina = **4.0000**，拆股后 = **1.0000**。
- 便携结论：US 来自 sina 的序列必须按 raw 处理，拆股由 R6 检测器处理，不能做 close-to-close 朴素读取。

### DD-3 当日 bar 生成时刻

- cn_a：**NOT OBSERVED**。仓内无 A 股会话元数据，周末探针无法观察盘中发布时刻。
- hk：**NOT OBSERVED**。`market_is_open("hk_equity")` 在仓内 fail-loud。T9b 观察：2026-10-20 10:27 CST，tencent 日线 frame 止于 2026-10-19，盘中不供当日 bar。
- us：**PARTIAL**。交易所在 16:00 America/New_York 收盘（早收盘 13:00 ET）后 bar 定型，DST 由 `ZoneInfo` 处理；源侧发布滞后未在 Step 0 观察。T9b 观察：2026-10-20 02:23 UTC（周一收盘后约 6 小时 23 分）可取到 2026-10-19 的 sina bar。

### DD-4 港股每股票手数

**FOUND**：`qt.gtimg.cn` HK quote 载荷的 **0-based 字段 idx 60** 即逐标的 board lot。判别样本 14 只、9 种手数（20/100/300/400/500/600/1000/1250/2000），全部与 HKEX 官方 List of Securities（2026-10-20）一致；idx 0 恒 `100`、idx 77 恒 `50` 为陷阱字段，已排除。tencent kline 载荷无 lot 字段，必须走 quote host。实现侧保留 `GlobalEquityEngine._hk_board_lot` 内置表 + 缺表回落 100 + `lot_assumptions` 诊断；T9b 中 00700.HK 无 lot 表条目，按默认 100 股成交并给出 warning，结果恰好正确。

### DD-5 ClickHouse 涨跌停/停牌/日历表

**CLOSED（静态 + 活体）**：`trade_calendar(exchange String, cal_date String, is_open Int64, pretrade_date String)`；`stk_limit(trade_date Date, ts_code, up_limit, down_limit)` 是涨跌停价表；`stk_limit_list_d` 是辅助标志表且**自 2024-05-30 停更**，不得依赖；`stk_suspend(ts_code, trade_date Date, ...)`；`stk_st_daily(ts_code, name, trade_date Date, ...)`。活体验证 `{trade_date:String}` 绑定对 `Date` 列成立（`stk_limit` 返回 5655 行）。`trade_calendar.cal_date` 的存储拼写为 `YYYYMMDD`，这一事实正是 §11.2 所记 cn_a 缺陷的根因。

### DD-6 cn_a 当日数据就绪时刻

**CLOSED**：`table_sync_state` 活体显示同步活动在交易日 **10:30 至 10:31 UTC** 完成，服务器 TZ=UTC，即 **18:31 CST**，仅周一至周五。2026-10-16 当日 25 行全部落在该窗口。T9b 观察：2026-10-20 10:27 CST，`stk_factor_pro` 对 600519.SH 的 `max(trade_date)` = **2026-10-16**，周一 2026-10-19 尚未入库；同日 STEP2 还观察到 `table_sync_state` 对该标的自标 partial-write 待重试。CH 同步面滞后属 RM 侧管道核查项，见 §11.2。

## 5. 镜像 tag 与身份

| 项 | 值 |
|---|---|
| Tag | `opencode-serve:v3.8.0-mymain` |
| Image Id | `sha256:1fa24461da83c686765ef435a927b38e0638575d470ac1806e4422a021010934` |
| Architecture / OS | `amd64` / `linux`（Apple Silicon 宿主跨架构构建） |
| Size | 3,994,225,792 bytes（约 3.99 GB） |
| Created | 2026-10-20T08:56:16.958990156+08:00 |
| RepoDigests | `["registry.cn-huhehaote.aliyuncs.com/gm_1/opencode@sha256:d442a38f3ebc57bfeef6493ca86794280d38119432217ca09bbfacdc14a66fd5"]`（2026-10-20 用户显式批准后 push；仓库路径 `registry.cn-huhehaote.aliyuncs.com/gm_1/opencode:v3.8.0-mymain`） |

构建源 = 压缩前 T8 提交 `750f2920`（该 hash 现仅存于本地备份分支 `backup/paper-trading-22-commits-20261020`；代码面与 tag `f9-pt-v1.1` 逐字节等价），vendoring 只含 committed 树，1285 个 Python 文件。`agent/tests`、fixtures、`jcs_vectors.json`、`.omo`、`.qoder`、`research`、`tmp` 均未入镜像（双面核对见 `STEP9A-BUILD-RECEIPT.md` §6）。

## 6. 容器内自测回执

- 镜像身份执行前后复核一致，`.Id` 未漂移，零重建、零 retag；push 于 2026-10-20 按用户显式批准执行（digest 见 §5）。
- 测试面经 `-v agent/tests:/opt/vibe-trading/agent/tests:ro` 只读注入，被测 `agent/src` 为镜像 vendor 树。
- `409 passed, 9 warnings in 42.74s`，与宿主基线 409 一致；19 个 `test_paper_trade_*.py` 全部入套。父级独立复核为 409 passed in 49.75s。
- T8 重点门：单日 10 持仓 10 委托、5 日 orders 补跑、5 日 strategy_path 单调用（真实 spawn）全部在预算内；purity 三动态用例运行前后 runtime root 与 cwd 文件树逐字节不变。
- `smoke-v380.sh` Stage 1 / 1b / 2 全绿：MCP 计数 91、`paper_trade_step` 在位、R5 env 与 override 双断言、entrypoint 健康。

## 7. 费用默认终值

引擎按 `Decimal` + `ROUND_HALF_UP` 计算，金额路径禁用内建 `round()`。RM dispatch 始终显式注入 `fee_model`，引擎透传并在 `effective_fee_model` 全量回显。

| 市场 | 键名与终值 | 与需求件 §4 的差异 |
|---|---|---|
| cn_a | `commission_rate=0.00025`，`commission_min=5.0`，`stamp_tax_sell=0.0005`，`transfer_fee=0.00001` | 无 |
| hk | `commission_rate=0.0003`，`commission_min=3.0`，`stamp_tax_rate=0.001`（每笔每边 notional × 0.1%，quantize 至整元 HALF_UP），`regulatory_levy_rate=0.00003` | 印花税取整用 HALF_UP，HKEX 实盘为 ceil；属 VD-9 允许的微调级选择 |
| us | `commission_per_share=0.0049`，`commission_min=0.99`，`sec_fee_rate=0.0` | SEC 费按 §4 原文简化为 0 |

手工核算样本：cn_a buy 100 @1421.00 fee 36.95，hk buy 100 @417.80 fee 55.79，us buy 10 @343.46 fee 0.99，均与 golden 逐分一致。

## 8. limit 单语义登记

采用 AM-7 冻结语义：

- 买入：仅当 `close <= limit` 成交，成交价 = `close`。
- 卖出：仅当 `close >= limit` 成交，成交价 = `close`。
- 违反：`reason_code = limit_not_marketable`，入 11 码冻结词汇表。
- 涨跌停校验优先于 limit 校验：涨停买入拒 `limit_up`，跌停卖出拒 `limit_down`。判定顺序按 AM-14 冻结梯：`unknown_symbol -> non_trading_day -> suspended -> corporate_action_unresolved -> lot_size -> limit_up/limit_down -> limit_not_marketable -> t_plus_1(仅 cn_a 卖) -> insufficient_position -> insufficient_cash`。

## 9. R6 检测器达标声明

**达标，不裁剪市场范围。** hk/us 保留在发布面，理由是 T4 与 T7 的检测器门全绿：

- 干净拆股：合成 4:1 与冻结集全部 12 个乘数逐一 CLEAN；hk/us 双市场 engine 用例验证持仓 qty 换算、avg_cost 调整、NAV 连续性（drift <= 1e-4）与 `data_provenance.corporate_actions` 记录。
- 注记 2：只扫描 `[from, to]` 内被模拟日界；K=120 回看窗内的历史不连续零事件、零拒单，T4 与 T7 双形态测试在位。
- fail-closed：非干净比率、成交量不可佐证、NAV 门不过，一律 `corporate_action_unresolved` 拒单，绝不静默调账。
- strategy_path 形态：T7 三日链与除权日卖出 400 股用例证明调整先于撮合。

如实登记一项运维特征：真实拆股的 ex-date close 含当日市况漂移（AAPL 2020-08-31 观测比率 0.2585，偏离 0.25 约 3.4%），在 1e-3 价格门下会判 UNRESOLVED 并拒单，而不是误调账本。这是保守的 fail-closed 行为，活体真实拆股误拒率属 RM E2E 观察项（本交付窗口未观察到真实拆股日）。不构成 AM-2-4 范围裁剪，也不需要裁剪。

## 10. T11 JCS 向量

| 项 | 值 |
|---|---|
| 路径 | `agent/tests/fixtures/paper_trade/jcs_vectors.json` |
| sha256 | `416989a103cec460e015c8ab8fbe3b8a09ca5d0430ee39ae241d35b5a9945406` |
| 向量数 | 12 条，IMMUTABLE（Step 1 入库，T5 复断言零改动） |

## 11. 联邦帧活体 smoke（AM-9）与阻断项

### 11.1 活体回执

数据层证据（`STEP9B-LIVE-SMOKE.md`，2026-10-20 10:09 至 10:35 CST，容器内直调 `run_paper_trade_step`）：

| 腿 | 回执 |
|---|---|
| hk | **PASS**：`00700.HK buy 100 @417.80`，2026-10-16，fee 55.79，`source=tencent`，nav 99944.21。首试目标日 2026-10-19 返回 in-band `PAPER_DATA_NOT_READY`（港交所重阳节补假休市，外部信源独立核实），回落到最近完成交易日 2026-10-16。 |
| us | **PASS**：`AAPL.US buy 10 @342.94`，2026-10-19，fee 0.99，`source=sina`（镜像烘焙 R5 链首生效）。 |
| cn_a | **数据层 LIVE-PROVEN，fill 级 BLOCKED**：`source=clickhouse+tencent(merged)`，`ch_as_of=2026-10-16`，`network_as_of=2026-10-19`，`bars_as_of=2026-10-19`，readiness 通过；无 `PAPER_DATA_NOT_READY`。fill 被 §11.2 的日历缺陷阻断。 |

禁用源合规：全程仅 tencent / sina / ClickHouse，零 yahoo、stooq、OKX、Binance、eastmoney kline、Wikipedia 调用。凭据仅经 shell 变量透传，零打印、零落盘。

### 11.2 Known issues / RM E2E blockers

1. **cn_a fill 级阻断（必须在 RM E2E cn_a fill 验收前修复）**：`agent/src/clickhouse_connector.py:501 get_trade_calendar` 把 `YYYY-MM-DD` 参数直接绑进 `WHERE cal_date >= ... AND cal_date <= ...`，而 `trade_calendar.cal_date` 存储为 `String(YYYYMMDD)`。字典序下任意 `YYYYMMDD` 行恒大于任意 `YYYY-MM-DD` 上界，查询对任何日期恒返 0 行，cn_a 任意日期被误判 `non_trading_day`。数据层与 AM-9 已实证，但填充级不可用。**本交付不声称 cn_a fill-level PASS。** 修复归属与回归用例需在 RM E2E 前裁决并落地。
2. **CH `stk_factor_pro` 数据滞后**：2026-10-20 10:27 CST 观察，600519.SH `max(trade_date)=2026-10-16`，周一 2026-10-19 未入库；同日 `table_sync_state` 有 partial-write 待重试行。RM 侧同步管道需核查。
3. **CH 公网 8123 白名单**：当前本机出口为 IPv6，白名单（前任 IPv4 `106.11.34.238/32`）不覆盖，公网 `/ping` 超时。活体证据经 §0.1 注册的 SSH 腿建立临时隧道取得，隧道已拆除并复测端口关闭。RM E2E 需具备同 VPC 或等价通道。

### 11.3 限制与披露（需求件 §11）

1. **HK/US 为 price-return 口径**：raw 序列下现金分红永不入账，持仓净值只反映价格回报；cn_a 为加法 qfq，近似 total-return。跨市场净值曲线不可直接比较，产品面须用户可见披露。
2. **退市不可分辨**：raw 口径下退市与长期停牌都表现为序列终止，持仓按最后 close 永久 carry，可能隐藏 -100%。建议 RM 侧对 symbol 数据 stale 超过 N 日告警。
3. **decide-after-close / idealized close fills**：v1 在收盘后数据就绪时决策，以当日收盘价成交。这不是 look-ahead，但严格强于真实 MOC（真实 MOC 须在收盘竞价截止前提交）。产品宣称必须写 "idealized close fills"；D+1 open-fill 登记为 P2 选项。
4. **cn_a additive-qfq catch-up 失真**：跨除息日的多日补跑以调整后（从未真实存在过的）价格成交，现金账与真实券商口径偏离，NAV 总量近似守恒但成交明细失真；catch-up 越长失真越大，AM-10 的每 run 不超过 10 交易日即此敞口约束。日增量正常运行不受影响。

## 12. DD-2 扩围探针与残留台账

### 12.1 DD-2 扩围答案

- **sina US 复权端点变体：NOT FOUND。** `type=qfq` / `fq=qfq` 返回与基础请求**逐字节相同**的载荷（`cmp` exit 0，参数被忽略）；`getQfqDailyK` / `getDailyKQfq` / `getHfqDailyK` / `getAdjustDailyK` 全部返回 `__ERROR:3 "Service not found"`（HTTP 200 的错误体，234 字节判别）。源码侧 `sina_loader.py` 对 qfq/hfq/adj/复权 零命中。
- **tencent/sina HK/US 指数 kline 可达性：**
  - `^HSI` 经 **tencent FOUND**：`fqkline/get?param=hkHSI,day,...` 返回 11 根 bar（2026-10-02 至 2026-10-16），载荷键为 `day` 而非 `qfqday`，与 #1493 注释一致。
  - `^GSPC` 经 **sina FOUND**：符号用 `.INX`，返回 5737 根 bar（2004-01-02 至 2026-10-16）；`.DJI` 亦可用，`.GSPC` / `GSPC` 返回 `null`。
  - 交叉组合 **NOT FOUND**：sina 无 HSI 日 K（`HK_MinKService` / `HK_KlineService` 报 Service not valid，`rt_hkHSI` 仅 quote）；tencent 的 `usINX` / `usDJI` / `usIXIC` / `us.GSPC` / `usSPX` 全部 `day: []`。
  - 结论：指数可达性不能按市场对称假设，配置必须把每个指数路由到其可用源。不可达时 `market_closed_inferred` 仅靠账户 universe 全零 bar 判定，保守可用。

### 12.2 残留台账（observed / not observed / residual）

| # | 项 | 状态 | 说明与下一步 |
|---|---|---|---|
| 1 | DD-3 cn_a 盘中 bar 定型时刻 | not observed | 需交易日盘中每 5 至 15 分钟轮询收盘钟；DD-6 已给 CH 入库 18:31 CST 下界 |
| 2 | DD-3 hk 盘中 bar 定型时刻 | not observed | T9b 仅证明盘中不供当日日线 bar；仍需收盘钟轮询 |
| 3 | DD-3 us 源侧发布滞后 | residual（单点观察） | 仅一次观察（收盘后约 6h23m）；需多日样本 |
| 4 | DD-4 1000/2000 手 spot-check | closed（探针） | 14 只 / 9 种手数已验证；产品侧 `hk_lot_sizes` 默认未接线，缺表回落 100 + warning，RM 可按 quote idx-60 注入 |
| 5 | DD-4 产品侧逐标的 lot 接线 | residual | `tool.py` seam 已留，默认 None；T9b 00700 走默认 100 并告警 |
| 6 | DD-6 CH 周一数据入库 | residual（观察异常） | 2026-10-19 数据在 2026-10-20 10:27 CST 仍未入库；RM 侧管道核查 |
| 7 | cn_a fill 级活体 | blocked-by-defect | 见 §11.2 第 1 条；修复后按 `STEP9B-LIVE-SMOKE.md` §3.2 命令原样复跑 |
| 8 | 真实拆股活体误拒率 | residual | 交付窗口未遇真实拆股日；保守 fail-closed 已由合成样本钉死 |
| 9 | 全 `agent/tests` 树 ruff | residual（既有） | 93 处存量错误，均在 paper_trade 之外；本套件 scoped ruff 全绿 |
| 10 | 镜像 registry digest | done | 已 push：`registry.cn-huhehaote.aliyuncs.com/gm_1/opencode@sha256:d442a38f3ebc57bfeef6493ca86794280d38119432217ca09bbfacdc14a66fd5`；`.Id` 仍为构建回执 |

---

*本通报与 `docs/PAPER_TRADING_ENGINE_REQUIREMENTS.md` pt-v1.1 冲突时，以需求件正典为准并回报 RM 侧修订。*