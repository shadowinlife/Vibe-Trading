---
title: 本地数据优先读取矩阵与迭代路线
description: F5 数据访问治理主文档：外部数据读取路径、真实 source 优先级、白名单/网络/沙箱限制、A 股实时联邦契约、本地数据补缺 backlog 与迭代验收证据。用于后续逐项减少公共源依赖，不把 auto/frozen/provenance 误当 CH 使用证明。
type: architecture
status: active
created: 2026-11-15
updated: 2026-11-15
tags: [clickhouse, local-first, data-access, provenance, realtime-federation, iteration-plan]
related: [CLICKHOUSE_ITERATION_PLAN.md, CLICKHOUSE_SYNC_DIAGNOSIS.md, ../features/f5-clickhouse-data-source.md]
---

# 本地数据优先读取矩阵与迭代路线

> 目标：任何外部数据读取都先尝试 ClickHouse（CH），或消费能够证明来源于 CH 的派生数据；无法由 CH 覆盖时，必须显式声明公共源例外，而不是静默换源。
>
> 审计基线：`feat/backtest-source-contract` 工作区，2026-11-15 静态代码审计。本文是持续维护的主文档；功能差异和上游关系仍以 `branch/MYMAIN_DIVERGENCE.md` 为权威台账。

## 1. 判定口径

| 标记 | 含义 |
|---|---|
| CH-direct | 直接查询 CH，或调用只读 CH 的固定 adapter |
| CH-derived | 本地缓存、frozen frame、技术指标、等权基准等输入能够证明来自 CH |
| federated | CH T-1 历史与公开源实时当日混合；两腿必须分别记录来源 |
| conditional | 是否 CH 取决于代码形态、配置、loader 可用性或运行环境 |
| public-first | 公共源先于 CH，或完全绕过 CH |
| high-risk | 除来源问题外，还存在误标、隐式联网、凭据或沙箱边界缺口 |

`source="auto"`、链首为 CH、`source="clickhouse"`、`frozen_data_manifest`、run card 中的来源字符串，都**不能单独证明**实际使用了 CH。

目标策略应区分：

- `CH_REQUIRED`：CH 无数据即失败，禁止公共源回退。
- `CH_PREFERRED`：先 CH，失败后允许公共源，但必须记录实际来源、回退原因和数据口径。
- `PUBLIC_EXCEPTION`：CH 尚无对应数据，显式登记公共源例外、负责人和补数计划。

## 2. A 股实时联邦契约（记录并使用）

实时 A 股允许并应当复用现有联邦路径：**CH T-1 历史 + 公开源实时今日数据**。这不是纯 CH 路径，属于 `federated`。

| 规则 | 契约 |
|---|---|
| 时间切分 | CH 查询 `[start, yesterday]`；公开源查询 `[max(start, today), end]`；仅查询当日时 CH 窗口为空 |
| 合并 | 按 `trade_date` outer join；重叠列优先 CH，实时源只补足当日 |
| 标的 | 股票 `stk_factor_pro`；ETF/LOF `fund_daily`；指数 `idx_factor_pro` |
| 实时源 | 由 A 股网络链选择，当前通常 Tencent；调用方必须读取实际 `served_by`，不得预设 |
| provenance | 必须分别记录 `history_source=clickhouse`、`history_table`、`realtime_source`、时间窗口和 `federated=true`；不得把整个 frame 简单标为 CH |
| 口径 | CH 为 raw/lots；公开源可能为加法复权/lots。拼接前必须明确复权与成交量单位，不能把不同口径当作无差别列合并 |
| 空结果 | CH 与实时源都为空才视为无数据；只有实时源时返回 `realtime_only`，只有 CH 时返回 `clickhouse_only` |
| 时间安全 | 禁止把当日未来 bar 引入历史回测；回测须按 evaluation cutoff 截断；实时分析须记录 timestamp 和延迟 |
| 使用入口 | `src.market_data.fetch_market_data(source="auto")` 与 `realtime/quote_adapter` 已复用该路径；其他实时消费者应复用同一 seam，不另写直连链 |

现有实现证据：`agent/backtest/loaders/clickhouse.py:176-248`。当前主要缺口是网络腿 provenance 不够清晰，而非联邦机制不存在。

## 3. 核心行情路由矩阵

通用链定义：`agent/backtest/loaders/registry.py:181-256`；`get_market_data` 默认最多尝试 5 个 source。

| 路径 | 当前优先级 | 关键限制 | 判定 |
|---|---|---|---|
| 规范 A 股历史日线 | CH → tencent → eastmoney → baostock → akshare → tushare → local | CH 空/不可用即转公开源 | conditional |
| 裸 A 股、`.SS`、`.OF`、未知 A 股 | **tushare → CH → tencent → ...** | `detect_source` 对未知 A 股默认 tushare | public-first |
| A 股分钟线 | 名义 A 股链，实际 tencent/eastmoney 等 | CH 仅 `1D` | public-first |
| A 股含当日 | CH(T-1) + 首个可用网络源当日 | 见实时联邦契约 | federated |
| 显式公共 source | 显式 source → 原市场链 | 公共源可先于 CH | public-first |
| `MARKET_DATA_ORDER_*` | 用户排列 | 可把 CH 移出链首；沙箱不转发该配置 | conditional |
| US | yahoo → stooq → sina → eastmoney → yfinance → 其他 key 源 → local | 无 CH | public-first |
| HK | tencent → eastmoney → yahoo → futu → akshare → yfinance → tushare → longbridge → local | 无 CH | public-first |
| 印度 | yahoo → yfinance → india_broker → local | 券商凭据 | public-first |
| 韩国 | pykrx → yahoo → yfinance → local | 可选依赖 | public-first |
| 巴西 | yahoo → yfinance → b3_cotahist → local | B3 可离线 | public-first |
| 加拿大/阿根廷/英国 | yahoo → yfinance → local | 币种/单位限制 | public-first |
| 越南 `.VN` | 误路由 tushare → A 股链 | 没有 `vietnam_equity` chain | high-risk |
| 中国期货 | akshare → local | 无 CH | public-first |
| Yahoo 期货 | yahoo → stooq → sina → eastmoney → yfinance | source 反查导致进入 US 链 | public-first |
| 加密一般形态 | okx → binance → ccxt → yfinance → local | 无 CH | public-first |
| 加密 `/USDT` | ccxt → okx → binance → yfinance → local | CCXT 显式优先 | public-first |
| 外汇/贵金属 | mt5 → akshare → yfinance → local | 终端/托管配置 | public-first |
| 非 A 股指数 | yahoo → yfinance → local | 无 CH | public-first |
| fund/macro | tushare → akshare → local / akshare → tushare → local | auto 市场识别不完整 | public-first |
| `local:` | 本地 CSV/Parquet/DuckDB | 是否 CH-derived 取决于文件来源；路径不天然受限 | conditional |
| loader cache | 本地 parquet → 对应 loader | 只缓存 settled 窗口；必须保留原始 source | conditional |

CH A 股路由白名单：股票/ETF/指数代码、ETF 前缀 `15/16/50/51/52/56/58`、指数范围 `000xxx.SH/399xxx.SZ/899xxx.BJ`。CH 为 raw 价格；成交量常为 lots，amount 为千元。跨源比较必须同时检查 adjustment、volume unit 和 currency。

## 4. 工具与直接读取矩阵

| 入口 | 当前来源 | 特殊规则/缺口 | 判定 |
|---|---|---|---|
| `technical_indicators` / `portfolio_risk_xray` | 继承 market-data 链 | 二次计算只继承输入来源 | conditional |
| `alpha_bench` CSI300 | Tushare 直接读取 | CH 指数/成分未接线 | public-first |
| `alpha_bench` S&P500 / BTC | Wikipedia / crypto 公共链 | 幸存者偏差；无 CH | public-first |
| `paper_trade_step` A 股日线 | CH + 网络腿，CH 优先合并 | 网络腿只取第一个 available loader | federated |
| `paper_trade_step` A 股 5m / HK / US | Tushare 分钟 / 公共市场链 | 无 CH | public-first |
| `get_valuation` | CH `stk_factor_pro` → Tushare `daily_basic` | 来源标注正确；未暴露 MCP | CH-direct |
| `get_stock_profile` | Yahoo only | CH `stk_info/stk_company` 未使用 | public-first |
| `get_financial_statements` | A/HK Eastmoney；US SEC；UK Yahoo；BR CVM→Yahoo | CH `fin_*` 未接线 | public-first |
| `get_fundamentals` | US SEC XBRL only | US-only | public-first |
| `get_fund_nav` | Gildata | token + feature gate | public-first |
| `search_symbol` | connector → 公共交易所 → Eastmoney → Yahoo → Gildata → SEC | CH `stk_info` 未使用 | public-first |
| `get_options_chain` / `orderbook_depth` | Yahoo / OKX/Binance | 无 CH | public-first |
| `get_southbound_flow` | Eastmoney → HKEX | 因 2024 后语义漂移刻意不用 CH `south_money` | public-first，需方法论裁决 |
| `screen_market` / `get_sector_info` | Eastmoney | CH 股票/行业表未使用 | public-first |
| `get_block_trades` | Eastmoney | CH `stk_block_trade` 已存在 | public-first |
| `get_shareholder_count` | Eastmoney | CH `stk_holdernumber` 已存在 | public-first |
| `get_lockup_expiry` | Eastmoney | CH `stk_share_float` 已存在 | public-first |
| `get_stock_news` | Eastmoney / Yahoo | 无 CH 新闻表 | public-first |
| `web_search` | IQS → Parallel → ddgs → sogou → cn.bing | 通用网页例外，无 CH | public-first |
| `read_url` | Jina Reader | 30s、8000 字符、私网 URL 拒绝 | public-first |
| SEC/13F/ETF 持仓/研报/FRED/Gildata/iWenCai/论文/预测市场/情绪 | 对应官方或公共 provider | 无 CH；需要例外登记或同步入仓 | public-first |

## 5. CH 语义层与 Flow 矩阵

| 入口 | 当前优先级 | 特殊规则 | 判定 |
|---|---|---|---|
| `ch_list_tables` | CH `system.tables` | 默认 CH 用户，非强制 llm_role；无公共回退 | CH-direct |
| `ch_describe_table` | CH metadata + sample 3 行 | 表名正则；默认 CH 用户；无列白名单/审计 | CH-direct |
| `ch_query` | CH only | llm_role、单 SELECT、表白名单、500 行、约 50KB、30s、审计 | CH-direct |
| `get_fund_flow` daily | CH `stk_moneyflow` → Tushare → Eastmoney | 分钟/非 A 股直接 Eastmoney；来源标注较完整 | conditional |
| `get_margin_trading` | CH `stk_margin` → Tushare → Eastmoney | 外层始终标 CH，可能误标 | high-risk |
| `get_northbound_flow` | CH `stk_moneyflow_hsgt` → Tushare → Eastmoney | 外层始终标 CH；2024-08-19 后为 turnover 语义 | high-risk |
| `get_dragon_tiger` | CH `stk_top_list` → Tushare → Eastmoney | 外层标 CH；席位始终 Tushare，尽管 CH 有 `stk_top_inst` | high-risk |

## 6. 回测、冻结与执行边界矩阵

| 路径 | 当前行为 | 特殊规则/缺口 | 判定 |
|---|---|---|---|
| A 股 auto backtest | CH → tencent → ... | CH 不可用/空/分钟均回退公开源 | conditional |
| 显式 `source="clickhouse"` | CH 不可用时仍可 same-market fallback | 不等于 CH_REQUIRED | conditional |
| backtest source 缺省 | tushare | 不是 auto/CH | public-first |
| 显式 local/no-network source | registry 本应 fail-closed，`_get_loader` 却最终兜底 Tushare | 可能把显式 local 转为公网数据 | high-risk |
| frozen OHLCV | 本地 frame + hash + 路径/口径校验 | 只有实际 provenance 为 CH 才是 CH-derived | conditional |
| frozen provenance | 校验声明自洽 | 不能证明 CH 声明为真 | conditional |
| auto/CH 外部 benchmark | 可转 yfinance | auto 不传 loader；显式 CH 的 snapshot wrapper 缺 ticker 时也转公网 | high-risk |
| frozen benchmark / equal-weight | 外部基准缺失 fail-closed；等权从 data_map 派生 | 等权是否 CH-derived 取决于 data_map | conditional |
| `fund:` | SEC EDGAR | frozen 跳过；非 CH | public-first |
| `val:` | Tushare → Baostock | frozen 跳过；CH 估值表未接线 | public-first |
| `fundamental_fields` | Tushare provider | frozen 仍执行；CH provider 已存在但未接线 | high-risk |
| `event_feeds` | RSSHub | frozen 仍执行 | high-risk |
| Runner 沙箱 | 仅转发 CH host/port/database；不转发 CH 用户密码；sandbox 下禁 dotenv | 认证 CH 在子进程不可用时，auto 会退公开源 | conditional |
| NativeSupervisor | 继承完整 os.environ 直接 spawn | 无 Runner allowlist、临时 HOME、marker、UID/rlimit | high-risk |
| UI reconstruct | 父进程重新 fetch_data_map | 不是读取原快照，也不回写 provenance | conditional |
| 生成策略直接取数 | AST 黑名单 | 未覆盖 SDK、pandas URL I/O、相对 `.env` 读取 | high-risk |
| `local:` 文件 | 本地 YAML 配置路径 | path/db_path 不受 run-root 限制 | conditional |
| `loader_health` | 直接公共 loader canary | 不 fallback、无 CH canary | public-first |

## 7. OpenCode 技能与脚本矩阵

| 路径 | 当前来源 | 特殊规则/缺口 | 判定 |
|---|---|---|---|
| `skills/data-warehouse/query.py` | CH only | 默认 CH 用户、任意 SQL、无 ch_query 的 AST/LIMIT/30s 守卫 | CH-direct，安全约束不足 |
| `workspace/scripts/screening/` | CH only | 无 AST/超时/LIMIT；不查 trade_calendar | CH-direct |
| `realtime/quote_adapter.py` | VT `fetch_market_data(source="auto")` | 已复用实时联邦；实际来源看 served_by | federated |
| `realtime/signal_scanner.py` | watchlist + quote adapter | `data_source` 被忽略；示例 ma20/vol_ma5 未定义 | conditional |
| CH-only microstructure | CH 多表 | 无统一 AST/超时/LIMIT；部分表停更 | CH-direct |
| `valuation_percentile.py` | Tushare → CH；force_local 可翻转 | 默认公共源优先 | public-first |
| `northbound_flow_divergence.py` | Tushare 北向 + CH 指数 overlay | 不读 CH 北向表 | public-first |
| `liquidity_tightening.py` | Tushare only | CH 无等价表 | public-first |
| `macro_credit_impulse.py` | Tushare 宏观 + 可选 CH 指数 overlay | 宏观主体非 CH | public-first |
| `external_probes.py` / `tushare_probe.py` | Tushare 为主，少量 CH | 诊断通道，无统一 CH-first 策略 | public-first |
| `ashare-events/filings/ir/research/risk` | Sina 直连 | nanofetch 20s/3 retries；host allowlist 不一致 | public-first |
| `ashare-policy` | gov.cn | 输出 URL 限 gov.cn；无 CH | public-first |
| cron prompt / script trigger | OpenCode/MCP 或直接脚本 | script trigger 无统一 source 约束 | conditional |
| `source_registry/external_source_matrix` | 元数据 | 无实时公共读取 | 不适用 |

`cn_news`、`holistic_review`、`hot_money_flow` 在当前工作区无实现；对应替代入口为 `get_stock_news`、`/review`/`portfolio_review_board`、`get_fund_flow`/`get_dragon_tiger`。

## 8. 迭代路线

| 阶段 | 目标 | 主要工作 | 验收门禁 | 状态 |
|---|---|---|---|---|
| L0 审计与来源契约 | 知道实际用了什么 | 读取矩阵；run card 逐标的 provenance；frozen 自洽校验；options warning；实时联邦登记 | 相关回归通过；source/adjustment/fallback 可审计 | 已完成，纳入 L1 RC |
| L1 执行与来源完整性 | 堵住“以为 CH/local/frozen，实际公网”的缺口 | no-network 兜底；benchmark CH-first；frozen enrich fail-closed；NativeSupervisor 环境；联邦/flow 来源标注 | 公共网络在 frozen 下不可达；每次回退有真实 source | 已完成：独立 gate APPROVE，RC 已构建并推送；L2 待启动 |
| L2 A 股已有表接入 | 优先消灭“库里有、工具不用” | 财报 fin_*、估值、龙虎榜席位、大宗、股东户数、解禁、行业、北向/南向下 | 先 CH 后公开；表格/单位/空结果测试；公开回退显式标注 | 未开始 |
| L3 实时与缓存硬化 | 降低实时公共调用频率 | 联邦 provenance 双腿；quote/scanner；cache 原始来源；交易日历/freshness；规则预计算 | 当日联邦可复现；cache 可证明 CH 血统；scanner 示例可用 | 未开始 |
| L4 微观结构与宏观迁移 | 减少 Tushare 依赖 | valuation/northbound/liquidity/macro/probes 逐模块评估 CH 表、停更和同步补数 | 每模块有来源矩阵、SLA 和回退披露；禁静默吞错 | 未开始 |
| L5 市场与例外治理 | 处理无 CH 数据域 | 其他市场、新闻、网页、SEC/FRED、加密等；决定同步入仓或 PUBLIC_EXCEPTION | 每个公共源都有例外 owner/理由/补数计划；CH_REQUIRED 下拒绝 | 未开始 |

## 9. 本地补数 backlog

| 数据域 | 本地候选 | 当前公共路径 | 下一步 |
|---|---|---|---|
| A 股估值 | `stk_factor_pro` | Tushare/Baostock `val:`、部分 microstructure | 统一 CH-first adapter 与 caliber 测试 |
| 财务报表 | `fin_balance/income/cashflow/indicator` | Eastmoney/SEC | A 股先接 fin_*；其他市场保留例外 |
| 龙虎榜席位 | `stk_top_inst` | Tushare `top_inst` | connector 增加固定只读查询与混合来源标注 |
| 大宗交易 | `stk_block_trade` | Eastmoney | CH-first adapter |
| 股东户数 | `stk_holdernumber` | Eastmoney | CH-first adapter |
| 限售解禁 | `stk_share_float` | Eastmoney | CH-first adapter |
| 行业分类/成员 | `idx_sw_*`、`idx_member_daily` | Eastmoney | CH-first adapter 与历史有效日期检查 |
| 北向/南向 | `stk_moneyflow_hsgt` | Tushare/Eastmoney/HKEX | 先裁决停更和 2024 后口径，再决定是否使用 |
| A 股实时 | CH T-1 + 公开当日 | Tencent 等 | 使用联邦，不追求错误的“纯 CH 实时”；补齐双腿 provenance |
| 流动性/宏观 | 当前无完整 CH 表 | Tushare shibor/lpr/cn_m/sf_month | 新建同步表或登记 PUBLIC_EXCEPTION |
| 新闻/公告/研报/IR/政策 | 当前无统一 CH 文档库 | Sina/gov.cn/Eastmoney/Gildata | 保留公共证据例外；评估文档入仓与 hash 存档 |
| US/HK/其他市场 | 当前无 CH 行情 | Yahoo/Tencent/SEC 等 | 明确例外或建立对应同步域，禁止伪称 CH |

## 10. 验证与发布门禁

每个阶段必须同时提供：

1. public seam 的红→绿测试：证明旧行为会公共源优先/绕过，修复后先 CH 或按契约 fail-closed。
2. 来源证据：实际 source、表名、adjustment、volume unit、fallback reason；实时联邦还要 realtime_source。
3. 网络证据：CH_REQUIRED 与 frozen 路径在 stub 禁止网络时不会偷偷发起请求。
4. 数据口径证据：raw/复权、lots/shares、币种、交易日和 stale SLA。
5. 回归与安全：相关 pytest、Ruff、Black、`git diff --check`、LSP；无真实 secret 写入日志或镜像。
6. 镜像：`check_startup_readiness.sh` 离线通过；记录镜像 tag、digest、构建上下文是否包含未提交工作树。

## 11. 当前证据日志

- 2026-11-15：完成四路静态审计（market data、backtest、OpenCode、CH/tools）并建立本矩阵。
- 2026-11-15：L0 provenance/frozen 补丁相关综合回归 **589 passed / 2 skipped / 1 deselected**；公共入口 QA **13 passed**。一个 deselect 为既有无关 mootdx 失败。
- 2026-11-15：首次 Oracle 发现 dotenv 重注入、frozen 来源/attrs/warning 不自洽、options warning 丢失，均已修复并有红绿测试。后续独立复审因模型限流/超时而未形成批准结论，状态保持 `review-inconclusive`，不得写成已通过独立复审。
- 2026-11-15：L1 完整实现后综合回归 **795 passed / 9 skipped / 1 deselected**，公共入口 QA **74 passed**；Ruff、Black、`git diff --check` 与核心 LSP 均通过。deselect 仍为既有无关 mootdx 失败。
- 2026-11-15：独立 L1 gate 初次 **REJECT**，指出 auto resolver substitution 缺 `source_unavailable`、CH 查询异常误记 `no_data`；两项均已修复并增加真实 resolver/异常路径测试。解封复审 **APPROVE（高置信度）**。此前 L0 的 review-inconclusive 由本次完整 L0+L1 审查承接，不宣称另有一次独立 L0 批准。
- 2026-11-15：`source=clickhouse` 保持 CH_PREFERRED，未实现 CH_REQUIRED；A 股实时继续采用 CH T-1 + 公开当日 federation，三种 mode、逐腿 caliber/unit 和 fallback reason 可经 run-card/freeze/replay 往返。
- 2026-11-15：RC 已构建并推送：`registry.cn-huhehaote.aliyuncs.com/gm_1/opencode:v3.19.0-l1-source-integrity-rc1-20261115`，远端 manifest digest `sha256:0464f4ff4ce7b42a6d2764488be7e1de9258f04c39c366e807474139d8917a28`，image ID `sha256:c5776df26125148dc529db72a1b227b1a1a2591507bd6ba2a98455f58865da87`。源码为 HEAD `084207fb` 上的**未提交工作树候选**，vendor 2363 文件逐字节核验，manifest SHA-256 `1170908de27a59f7d91939888e684ca1d323a317f279536bf9d899eac1fb5025`；未创建 Git commit/tag。
- 2026-11-15：RC 镜像内 `--network none` 行为测试 **74 passed**；`check_startup_readiness.sh` PASS：listening 5.5s、首次 `/config` 200 = 23.9s、VT MCP connected = 27.8s、strategy `/health` 200 = 32.8s，预算均为 300s。宿主机 QA JUnit 与构建 manifest 保存在仓库外 `.../opencode/l1-source-integrity-rc1/`。
