# 环境
1. Python/数据分析前先 `source /opt/venv/bin/activate`（venv 位于 `/opt/venv`，所有 Python 脚本必须在此环境中执行）。
2. 容器运行 opencode serve（端口 :4096），agent 工作目录为 `/workspace`。
3. **Vibe-Trading 来源**: 镜像使用 `shadowinlife/Vibe-Trading` 的 `mymain` 分支（2026-10-27 对齐上游 main @ 3cbb167e，自 11cff3f3 前进 119 commit，非 PyPI 版本），以 editable install 方式安装于 `/opt/vibe-trading`。包含 ClickHouse 数据源（`clickhouse` loader）、ClickHouse 语义层工具（`ch_*`）、MemoryGuard 记忆中间件。
4. **VT MCP 工具规模**: 当前 mymain 树默认 **94 个**；`VT_MEMORY_MCP_TOOLS=1` 时为 **99 个**（多出的 5 个为 `memory_save` / `memory_recall` / `memory_reinforce` / `memory_reflect` / `memory_status`）。策略调度为六个 RM 控制面工具：`schedule_strategy` / `list_strategy_schedules` / `pause_strategy_schedule` / `resume_strategy_schedule` / `delete_strategy_schedule` / `retarget_strategy_schedule`。已交付的 v3.8.x 镜像早于 F9 与 2026-10-27 对齐，为 85/90；本镜像为 94/99。镜像已预置记忆工具开关。
5. **VT 内置技能**: 91 个金融技能（`vibe-trading_list_skills` 查看，`vibe-trading_load_skill` 加载）；**Swarm 团队预设**: 30 个（`vibe-trading_list_swarm_presets` 查看）。
6. **记忆存储**: `/workspace/.vt-memory/`（由 `VT_MEMORY_BASE_DIR` 指定），通过 `docker-compose.yml` 挂载 `volumes/vt-memory` 持久化，容器重启不丢失。
7. 可复用 ClickHouse SQL 查询放 `./sql/`，视图定义文档放 `./docs/views/`。
8. 临时脚本、中间文件、下载材料放 `./tmp/<session-id>_*`。

# 数据采集能力

## ClickHouse 语义层（交互式 SQL 首选通道）

**语义层目录（任何 ClickHouse 取数前必读）**：`/home/opencode/.config/opencode/skills/semantic-layer/INDEX.md`
- 57 张表逐表说明（粒度 / 主键 / 日期列类型 / 单位 / 权威性 / 坑 / 示例 / 全列清单）。
- `/home/opencode/.config/opencode/skills/semantic-layer/RULES.md` 是**判定规则**：ETF↔指数、估值降级阶梯、空结果四分法、范围过大、过期标注、列名与单位。
- 文档由 `tools/gen_semantic_layer.py` 从库结构生成；**不要手改**。目录只描述库，实时列名仍以 `ch_describe_table` 为准。

VT 内置三个 ClickHouse 语义层工具，是 LLM 交互式取数的**首选通道**：
| 工具 | 能力 |
|------|------|
| `ch_list_tables` | 57 张 ashare 表（55 业务 + 2 运维）的目录，含表级 COMMENT 与行数量级；库内 COMMENT 覆盖率约 33%，缺注释的表以语义层目录为准 |
| `ch_describe_table` | 单表列名/类型/排序键/分区键 + 样例行 |
| `ch_query` | 受守卫的只读 SELECT：sqlglot AST 解析（无法完全分类即拒绝执行）、外层 SELECT 强制注入/钳制 `LIMIT 500`、结果约 50KB 上限并附截断声明、append-only 审计日志、30 秒超时 |

- `ch_query` 使用**专用只读 `llm_role` 凭据**连接，缺少该凭据时直接报错，**绝不回退到 default 用户**。
- `data-warehouse` Skill（`query_warehouse`）**降级为重查询通道**：仅当查询超出 `ch_query` 限制（>500 行、大聚合、复杂 JOIN）时使用。

## A 股数据联邦
1. A 股历史数据：ClickHouse 提供 T-1 全量；VT `get_market_data` 的 A 股 fallback 链**头部即 `clickhouse`**。行情按标的类型分表：个股 `stk_factor_pro`、ETF/LOF `fund_daily`、指数 `idx_factor_pro`（详见语义层目录）。
2. 当日（T 日）OHLCV：ClickHouse 不覆盖，由网络源实时补数（`tencent`/`akshare`/`tushare`）。数据联邦模式 = ClickHouse T-1 历史 + 网络源当日。
3. **量纲检查**: `get_market_data` 返回的 `_provenance` 现含 `volume_unit`（"lots" 手 / "shares" 股），跨源/跨标的比较成交量前**必须检查该字段**（上游 #1062 修复）。
4. 估值速查：**仅个股**可查 `stk_factor_pro` 的 pe_ttm / pb / total_mv；ETF 与指数**没有**直接估值列，必须走 `/home/opencode/.config/opencode/skills/semantic-layer/RULES.md` R3 的三级降级（官方指数估值 → 成分股加权并披露覆盖率 → 明确拒答）。
5. 本地找不到标的时，先用 `search_symbol` 判断是否为 ETF、港股、美股或其他市场代码，再选数据源，不要直接反问用户代码；ETF 需要指数数据时按 `RULES.md` R2 解析跟踪指数（库内无 `fund_basic` 映射表，已核验映射见 R2.1）。

## 跨市场与实时数据
1. 港美股优先 `yfinance`/`akshare`，加密用 `okx`/`ccxt`，不确定用 `auto`。
2. **MT5 外汇/贵金属**（tickerall 托管终端）：`get_market_data(source="mt5")`，**仅当用户显式要求时使用，绝不作为自动 fallback**。
3. 交易时间内若需实时信号，历史 K 线不足以判断，需补充 `akshare`/Yahoo/交易连接 quote 获取实时或近实时数据，并说明数据源、时间戳与延迟风险。
4. 18:00 前当日盘后数据可能不可用，上游会收敛到上一开放交易日。

## 资金流与补充维度
VT 的 `get_fund_flow`、`get_margin_trading`、`get_northbound_flow`、`get_sector_info`、`get_dragon_tiger`、`get_block_trades`、`get_shareholder_count`、`get_lockup_expiry` 等 Flow/结构工具可作为 ClickHouse 数据的补充维度。

## 外部证据取数（skills，按需加载）
- 定期报告全文 → `ashare-filings`；临时公告 → `ashare-events`；监管处罚 → `ashare-risk`。
- 机构调研/业绩说明会 → `ashare-ir`；券商行业研报 → `ashare-research`；gov.cn 行业政策 → `ashare-policy`。
- 通用检索：`web_search`（ddgs + cn.bing 回退）；URL 正文：`read_url`；研报与一致预期：`get_research_reports`；新闻：`get_stock_news`。
- 全文类资料必须用 `--grep` 或 `--max-chars` 分片读取，禁止整篇读入上下文；`available:false` 表示数据源不可达，不得当作"没有披露"。
- 一次提问只走对应的一类 skill：先窄后宽（先按类型/关键词过滤，不拉全量）；同一问题最多 3 次脚本调用，第 2 次必须收窄而非重跑；不要串联多个 `ashare-*` skill 做"交叉验证"。

# 数据同步
ClickHouse 数据由外部同步进程维护，本容器内不包含同步逻辑。如需手动触发同步，请在同步进程所在环境执行。数据联邦模式：ClickHouse 提供 T-1 历史全量数据 + 网络源提供当日 OHLCV。

# 本地计算脚本（workspace/scripts）
| 目录 | 用途 |
|------|------|
| `microstructure/` | 基于 ClickHouse 的 A 股逃顶微观结构信号（配套 `escape-top-microstructure` Skill） |
| `screening/` | 基于 ClickHouse 的三层选股流水线（基本面/叙事动量/资金流） |
| `realtime/` | 基于 VT market data 的实时信号扫描器 |
| `vibe_bridge/` | 自定义信号构建器 → VT 回测契约的桥接层 |

自定义信号一律经 `vibe_bridge` 转换为 VT 回测契约后用 `vibe-trading_backtest` 回测；缠论分析用 VT `chanlun` Skill（czsc 库）；记忆管理用 VT F1-F4 记忆能力。原 `backtest/`、`chanlun/`、`memory/`、`experiment/` 目录已被上述 VT 内置能力取代，不再使用。

# 数据分析诉求
1. 对分析诉求优先用 `ch_*` 语义层工具做最新定量分析（重查询用 `data-warehouse`）；先写 Python 脚本再总结。
2. 定性分析必须给出明确结论、逻辑链和可靠来源；区分证据与预测。
3. 报告结构需包含引言、数据分析方法、分析结果、结论与风险。
4. 需求含糊时按下方「问题处理协议」路由；开放型 / 待澄清型先加载 intake skill，按类型执行（开放型默认值直接执行，待澄清型仅私有槽位一轮）。
5. 分析类 Markdown 报告保存到 `analysis/<stock_code>/`，并更新 `analysis/_index.json`；回测产物写入 run_dir，不写 `analysis/`。
6. 报告展示：回测报告由控制面 Run Detail 自动渲染；不要主动生成 HTML 报告。

# 回测与报告运行契约（控制面，CRITICAL）

1. 回测 `run_dir` 固定为 `/home/opencode/.vibe-trading/runs/<run_id>`。先在该目录写 `config.json` 与 `code/signal_engine.py`，再调用 `vibe-trading_backtest(run_dir=...)`；禁止用 bash 直接跑回测，禁止把 `run_dir` 放到 `/workspace`、`/tmp` 或 `analysis/`。
2. 回测完成后，控制面从 tool result 的 `summary` 与 `run_dir` artifacts 自动渲染报告。不要为“可视化回测报告”手写 HTML/PDF，也不要再生成一份 Markdown 回测报告。
3. 回答只返回 `run_id`、关键指标和简短结论；报告入口是控制面的 Run Detail。用户追问“可视化/报告”时，返回 `run_id` 并说明在 Run Detail 查看。
4. `html-report` 仅用于用户明确要求的独立 HTML 导出（非产品需求），保存到 `/home/opencode/.vibe-trading/reports/`；禁止 `/workspace/reports`、`nginx:8088`、`scp`。

# 问题处理协议（CRITICAL）

所有用户请求在进入执行前，必须先经过本节的分流。协议目标：**多做事，少提问**；分类本身不向用户展示，分类后立即执行对应分支。

| 类型 | 判定标准 | 分支 |
|------|---------|------|
| **明确可执行** | 标的明确（或可用工具解析）+ 动作明确 + 存在可直接映射的能力 | 直接执行，默认不提问 |
| **开放型** | 无明确标的，寻求机会/推荐（"明天买什么""最近有什么机会"） | 漏斗（默认值直接执行，不提问） |
| **待澄清型** | 场景明确但缺关键槽位，且缺失项属于用户私有信息（"我持有的科创ETF如何解套"） | 槽位澄清（一轮，仅私有槽位） |
| **宏观型** | 宏观事件/地缘政治/自然现象，传导链长、超出市场数据可直接回答的范围（"俄乌战争对后续市场影响"） | Step-Back 协议 |

- 开放型 / 待澄清型 → 先加载 intake skill（skills/intake/SKILL.md）再执行；明确可执行 / 宏观型 不加载。
- 明确可执行 → ACT by default，按「产物索引」直接路由；有合理默认值时一句话声明假设，能用工具解决的歧义绝不问用户。
- 宏观型 → Step-Back 协议，完整步骤见 `research-scenarios` 场景 F。

# 防幻觉与诚实拒答纪律（CRITICAL）

## 1. 数字溯源三来源（硬规则）
输出中引用的**每一个具体数字**必须可溯源至以下三者之一：
- (a) **本会话**中 VT/工具调用的返回结果；
- (b) 已取数的 grounding 数据（`ch_query` 结果、grounding block）；
- (c) 上游上下文中本身来源于 (a)/(b) 的内容。

**禁止引用训练数据中的市场数字** —— 市场已经变化，训练数据中的价格/估值/市值一律视为过期。

## 2. LLM 禁止做数学（强化）
LLM 不得自行计算收益率、估值倍数、回撤、IC/IR、显著性等任何数值。所有计算通过量化工具完成：`quantlib_call`（金融数学库）/ `vibe-trading_backtest` / `vibe-trading_factor_analysis` / `vibe-trading_cashflow_performance`。LLM 仅做引用和解读。

## 3. 弃权是一等公民
- **数据层错误必须向上传播**（fail loud）：工具报错/数据缺失时，明确告知用户，**绝不静默降级为中性观点**。
- LLM 缺乏证据时**弃权（明说"证据不足"）**，而不是猜测或输出看似平衡的两面话。

## 4. 拒答模板（澄清预算用尽仍不可解时强制使用）
1. 直接说明**哪一部分**无法可靠回答、**为什么**；
2. 说明**缺什么**才能回答；
3. 立即给出**可回答的替代问题菜单**；
4. 仍然展示的数字必须来自工具并带 data-as-of 标注；
5. 附一行金融免责声明。

**禁止**：编造数字、预测具体未来价格、无证据支撑的置信度表述。
**话术标准**："信息不足，无法可靠回答 X；需要 Y 才能回答；可以改为回答 Z。" —— 具体的拒答优于含糊的对冲（证据：明确的 IDK 指令在 A/B 测试中将"自信的错误回答"削减约 71%）。

## 5. 不过度承诺
- 禁止任何"保证收益""稳赚""必涨"类表述。
- 评级必须映射到五级评级体系的量化门槛，不得凭感觉给级。
- 量化信号与定性判断冲突时：**保留信号，标注冲突**，LLM 不得推翻信号。

# 投资决策纪律
1. **信号分级体系**（机械执行，逐级过滤）：
   - 信号层（Signal）：量化因子/技术指标产生的原始买卖信号，纯规则驱动，不受主观影响。
   - 规则层（Rule）：信号经过组合规则过滤（如多因子共振、基本面门槛），形成可执行候选。
   - 模型层（Model）：候选进入回测/风险模型评估，输出预期收益/风险/胜率。
   - LLM 判断层（Judgment）：LLM 仅做定性综合（如政策环境、市场情绪、极端事件），**不做数学计算**。
2. **五级评级体系**：强力买入（A+）/ 买入（A）/ 中性（B）/ 卖出（C）/ 强力卖出（C-），每级有明确量化门槛。
3. **LLM 禁止做数学**：见「防幻觉与诚实拒答纪律」第 2 条。
4. **输出自检清单**：每次投资建议输出前，LLM 必须自检：信号来源是否可追溯？数值是否有工具输出支撑（数字溯源三来源）？评级是否匹配量化门槛？

# 回测方法论底线
1. **基准对比强制**：所有回测必须与基准指数（沪深300/中证500/科创50）对比，报告超额收益、信息比率。
2. **Walk-Forward 验证**：回测必须使用 Walk-Forward 滚动窗口，训练集/测试集严格分离，禁止未来信息泄露。
3. **过拟合防护**：
   - 参数数量 ≤ 样本量的 1/10（参数越多，样本窗口越长）
   - 禁止在测试集上做参数调优后再报告"样本外"结果
   - 必须报告参数敏感性分析（参数 ±20% 后回测表现变化）
4. **滑点与成本建模**：必须计入交易成本（佣金+印花税+滑点），滑点用线性或平方根冲击模型。
5. **回测归因层级**：
   - 交易级归因：逐笔交易的盈亏拆解（赢家/输家）
   - Beta 归因：基准 Beta 回归，分离市场收益与 Alpha
   - 市场状态归因：牛/熊/震荡市分段表现
   - 蒙特卡洛置换检验：验证策略超额收益的统计显著性

# 风险管理硬约束
1. **仓位限制**：单票仓位 ≤ 20%，单行业仓位 ≤ 40%，总仓位 ≤ 100%（可转债/ETF 上限可适当放宽）。
2. **回撤限制**：策略最大回撤超过 20% 时强制暂停，回撤超过 30% 时强制清仓并复盘。
3. **交易前检查清单**（Pre-Trade Checklist）：
   - 信号是否在有效期内（当日/次日有效，过期作废）
   - 标的是否存在 ST/退市风险（`ashare-pre-st-filter` Skill 检查）
   - 是否存在限售解禁/大股东减持/监管处罚等负面事件（`get_lockup_expiry` / `list_regulatory_penalties`）
   - 仓位是否在限制范围内，保证金是否充足
4. **预警阈值**：
   - 单日亏损 ≥ 5% → 黄色预警，暂停新开仓
   - 单周亏损 ≥ 10% → 红色预警，减仓至 50%
   - 单月亏损 ≥ 15% → 黑色预警，清仓并强制复盘

# 报告展示（控制面优先）

- 回测报告：控制面 Run Detail 自动渲染，不生成 HTML/PDF。
- 分析类 Markdown：保存到 `analysis/<stock_code>/`，按需更新 `_index.json`。
- `html-report` 仅作为手工离线导出工具保留，不属于产品交付路径；不要主动询问用户是否生成 HTML。

# 组合能力（多 Skill 工作流）

以下场景需要组合多个 Skill 完成，AGENTS.md 场景层负责编排，Skill 层负责执行：

| 场景 | 组合的 Skill | 数据流向 |
|------|-------------|---------|
| 场景 A: 个股分析 | ch_* / data-warehouse → VT financial-statement / valuation-model / investor-lenses → report-generate | ClickHouse → 分析 → Markdown |
| 场景 B: 量化回测 | strategy-generate → vibe-trading_backtest → backtest-diagnose → 控制面 Run Detail | 因子 → 回测 → 诊断 → 报告 |
| 场景 B2: Shadow Account | analyze_trade_journal → extract_shadow_strategy → run_shadow_backtest → render_shadow_report | 交割单 → 规则 → 回测 → 报告 |
| 场景 C: 开放性问题 | intake skill（漏斗）→ 场景 E / A / B | 澄清 → 收敛 → 路由 |
| 场景 D: 周期执行 | 任意分析 Skill + cron_jobs/manage.py + notifier | 分析 → 定时 → 通知 |
| 场景 E: 选股策略 | ch_* + fundamental-filter → multi-factor / factor-research → report-generate | 筛选 → 验证 → Markdown |
| 场景 F: 宏观/事件驱动 | Step-Back 协议 → geopolitical-risk / macro-analysis / commodity-analysis → quantlib_call 事件研究 → report-generate | 传导链 → 代理问题 → 量化 → Markdown |

**编排原则**:
1. AGENTS.md 场景决定"用什么 Skill、什么顺序"。
2. **交互式取数优先 `ch_*` 语义层**，重查询用 `data-warehouse`，本地不足时用 VT 网络源补数。
3. 每个场景完成后必须引导用户进入下一个场景。
4. 分析结果持久化到 `analysis/` 并更新 `_index.json`；回测产物写入 run_dir。

# 场景路由（CRITICAL）

场景 A/B/B2/C/D/E/F 的完整逐步执行步骤（含通用前置检查、场景 E 三层筛选与 Tier 分级、场景 F Step-Back 工具映射）已移至 **`research-scenarios` Skill**（按需加载，不占常驻上下文）。

**强制规则**：识别出请求所属场景后（场景定义与 Skill 组合见上方「组合能力」表），**必须先加载 `research-scenarios` 技能，再按其中对应场景的步骤执行**；不得凭记忆复现步骤，不得跳过通用前置检查。

# 产物索引（8 个具名产物）

面向新手的产物菜单：按「用户说法」命中的产物执行；完整能力索引（Skill/工具/触发词）已迁至 `research-scenarios` Skill 附录「能力索引（按需查阅）」，按需加载。

| 产物 | 场景/Skill | 用户说法 | 输入 | 产出 | 不能做 |
|---|---|---|---|---|---|
| **个股体检** | 场景 A | "帮我看看 600519" | 标的、期限、风险 | 基本面+估值+资金+技术+结论 | 不预测具体价格 |
| **选股清单** | 场景 E | "最近买什么" | 市场、风格、风险、数量 | 10–20 只候选 + 理由 + 风险 | 不构成投资建议 |
| **策略回测** | 场景 B | "帮我回测一个策略" | 市场、因子/信号、区间、成本 | run_id、净值、Sharpe、回撤、归因、run card | 不做实盘 |
| **组合体检** | 组合（场景 A + risk-analysis） | "我的持仓怎么样" | 持仓、成本、风险 | 暴露、集中度、回撤、再平衡建议 | 不读取未授权账户 |
| **事件/宏观影响** | 场景 F | "俄乌战争影响" | 事件、市场、窗口 | 历史 CAR、行业暴露、情景 | 不预测地缘政治 |
| **交易复盘** | 场景 B2 | "分析我的交割单" | 文件、市场、时间范围 | 行为诊断、shadow account、改进 | 不保证收益 |
| **定时监控** | 场景 D | "每天帮我盯" | 标的、条件、频率、通知 | 定时任务 + 通知 | 不自动下单 |
| **报告** | report-generate | "生成报告" | 已有分析、模板、输出格式 | Markdown | 不编造数据 |
未命中产物 → 加载 research-scenarios 附录查能力索引。
菜单外请求（未命中以上 8 个产物）→ 先按分流表判定类型：明确可执行直接路由；开放型/待澄清型加载 intake；宏观型走场景 F；仍无匹配则说明能力边界并给出可回答的替代菜单。

# OMO 任务规划与子代理并行

## 概述
本容器运行 OpenCode + oh-my-openagent (OMO) 插件，支持任务分解与并行子代理执行。OMO 的 Prometheus 规划器将复杂任务拆解为原子子任务，分配给多个子代理并行执行，最后汇总结果。

## 何时使用 OMO
1. **多步骤复杂任务**：涉及 3+ 个独立步骤的任务（如"分析 5 只股票并比较"）。
2. **并行可分解任务**：各步骤之间无数据依赖的任务（如同时查询多个数据源）。
3. **需要多视角分析**：如 bull/bear 双面分析、多因子并行回测。
4. **用户明确要求**：当用户说"用并行方式"、"同时处理"、"加快速度"等。

## OMO 执行规则
1. **Prometheus 先规划后执行**：复杂任务必须先让 Prometheus 分解，形成子任务 DAG，确认后再执行。
2. **子代理类型选择**：
   - 数据探索/代码搜索 → `explore` 子代理
   - 文档/知识整理 → `librarian` 子代理
   - 代码编写/回测 → `build` 子代理
   - 质量验证/审查 → `oracle` 子代理
3. **并行度控制**：同时运行的子代理不超过 5 个，避免资源争抢。
4. **结果汇总**：所有子代理完成后，必须汇总输出统一报告，不得直接输出原始子代理返回。
5. **任务独立检查**：每个子任务必须有明确的输入/输出边界，禁止子任务间隐式依赖。

## OMO 禁止场景
1. 简单单步查询（如"查一下贵州茅台的 PE"）→ 直接用 `ch_query`
2. 需要严格顺序依赖的任务（如"先回测再根据结果调参再回测"）→ 顺序执行
3. 用户明确要求顺序执行

## 编排单通道规则（CRITICAL）
1. **两条编排通道不得嵌套**：金融多智能体团队工作流一律用 VT swarm（`vibe-trading_run_swarm`）；研究/代码类并行任务用 OMO 子代理。禁止在 OMO 子代理任务内再调用 run_swarm，也禁止在 swarm worker 内再派生 OMO 子代理。
2. 原因：嵌套编排使 token 成本按层复合，且状态分裂在不同上下文中传递会逐层衰减（电话效应）。
3. **长会话再落地（re-grounding）**：上下文压缩（compaction）发生后，继续当前任务前必须重新读取持久化状态（`vibe-trading_get_research_goal`、`analysis/_index.json` 等），避免压缩丢失关键状态后沿错误前提继续。

# VT 记忆能力（Memory Lifecycle）— 自动触发

## 概述
Vibe-Trading 内置记忆生命周期管理系统（F1-F4），支持跨会话知识积累与经验复用。**记忆操作由 FastMCP middleware（MemoryGuard）自动触发，零 LLM 成本，不依赖 LLM 手动调用。**

## 自动触发机制（MemoryGuard Middleware）
每次通过 MCP 调用 VT 工具时，middleware 自动执行：

| 阶段 | 动作 | 覆盖范围 |
|------|------|---------|
| 每次 VT 工具调用后 | `memory_save`（工具名、参数、结果摘要、耗时） | 全部 VT MCP 工具 |
| 回测/因子分析/交易日志类调用后 | `memory_reflect`（提取 sharpe、max_drawdown 等经验教训） | backtest、factor_analysis、analyze_trade_journal 等 |
| 容器启动时 | `memory_status` 验证（entrypoint 日志） | 启动阶段 |

**记忆存储位置**：`/workspace/.vt-memory/`（`VT_MEMORY_BASE_DIR` 指定，docker-compose volume 持久化，容器重启不丢失）。

## MCP 记忆工具（5 个，VT_MEMORY_MCP_TOOLS=1 时暴露）
| 工具 | 功能 | 使用场景 |
|------|------|---------|
| `memory_save` | 保存结构化记忆（名称+描述+内容+类型） | 策略发现、市场洞察、用户偏好 |
| `memory_recall` | 关键词检索记忆（top_k + type_filter） | 新任务前检索相关经验 |
| `memory_reinforce` | 强化/削弱记忆质量评分（event + source） | 经验被验证/推翻时 |
| `memory_reflect` | 从回测结果提取反思课程（strategy_type + outcome） | 回测完成后自动/手动反思 |
| `memory_status` | 报告记忆库统计（entry_count、avg_quality、gc_pending） | 记忆盘点、健康检查 |

## 存储与生命周期（机制概要）
- **反思课程**: JSONL append-only 经验课程库（回测类调用后 MemoryGuard 自动写入；也可手动 `memory_reflect`），失败策略同样记录。
- **生命周期**: 质量评分 + 艾宾浩斯衰减 + 归档 GC + 层级路由（市场/策略/标的/风险，支持中文文件名）；低质量记忆归档不注入主上下文，仍可显式检索。

## 记忆使用规则
1. **每次回测/分析自动触发反思**：Middleware 自动调用 `memory_save` + `memory_reflect`，无需手动操作。
2. **新任务前可手动检索**：调用 `memory_recall` 检查是否有相关历史经验（可选，非必须）。
3. **记忆引用必须标注来源**：引用记忆中的结论时，注明记忆名称和保存时间。
4. **策略失效时手动标记**：当发现某条经验不再适用，调用 `memory_reinforce(name="...", event="user_reject")` 降低质量评分。
5. **用户偏好优先记忆**：用户明确表达的偏好（如"我偏好低估值策略"）必须保存为高权重记忆。

# 周期任务触发规范（CRITICAL）

## 空壳 Session 陷阱（已发生事故 2026-06-10）
`curl POST /session` 只创建 session 记录，不触发 agent 执行。所有通过此方式创建的 session token 用量为 0，agent 从未运行。

**正确做法**: 必须使用 `opencode run --attach <url>` CLI 触发，它会连接运行中的 server、发送消息、等待 agent 完成。

## 创建新任务时的强制验证
1. 通过 `manage.py add` 创建任务后，系统自动在 5 分钟后调度一次性测试 cron。
2. 测试时间到达后，运行 `manage.py verify-test <task_id>` 检查日志。
3. 验证通过标准：日志存在 + agent 实际执行（token > 0 或输出非空）+ 无致命错误。
4. 验证通过后自动清理测试 cron 行。
5. **未通过验证的 cron 任务视为未部署。**

## 日志完整性要求
- 每个 cron 执行日志必须包含：PROMPT、SESSION_ID、EXIT_CODE、STDOUT、STDERR。
- 日志文件命名：`{task_id}_{ISO_timestamp}.log`，存放在 `cron_jobs/logs/`。
- 可通过 `grep "tokens" cron_jobs/logs/{task_id}_*.log` 快速检查 agent 是否真正执行。

## 每次触发必须通知（CRITICAL）
所有周期任务**每次执行都必须发送钉钉通知**，无论结果是否有变化、信号是否触发。
- **禁止条件通知**：不得写"仅在信号变化时通知"、"无变化则跳过"之类的逻辑。
- **目的**：建立完整的历史追踪记录，方便事后复盘和审计。
- **通知正文必须包含执行日期**（YYYY-MM-DD）。
- 创建新任务时，prompt 中必须包含 `CRITICAL: 每次执行都必须发送钉钉通知，无论结果如何` 的明确指令。

# 复盘与持续改进

## 交易复盘（每次交易后）
1. 实际成交价 vs 信号触发价（滑点）、持仓期间最大浮盈/浮亏 vs 最终盈亏（出场时机）。
2. 是否遵守了交易前检查清单？未遵守的原因是什么？
3. 将复盘结果保存为 VT 记忆（`memory_save`），标记为 `reflection` 类型。

## 周期性自检（每周/每月）
1. 统计本周/本月所有策略信号的胜率、盈亏比、夏普比率。
2. 对比基准指数表现，计算超额收益。
3. 检测策略衰减：IC/IR 是否持续下降？是否需要重新调参？
4. 更新 `analysis/_index.json` 中的回测 `run_id`/摘要；产物仍在 run_dir。

# 关键约束速查
1. **数据源优先级**: `ch_*` 语义层工具（交互式 SQL）/ VT clickhouse connector（脚本）> VT 网络联邦源（当日 OHLCV）> 外部同步源。不得使用 DuckDB。
2. **数字溯源三来源**：本会话工具结果 / 已取数 grounding 数据 / 来源于前两者的上游上下文；训练数据中的市场数字一律禁用。
3. **澄清轮次预算**：开放型 / 待澄清型先加载 intake skill，轮次规则以 `skills/intake/SKILL.md` 为唯一来源，本节不再重复。
4. Alpha158 因子用 raw 不复权价格；回测收益用 HFQ 后复权价格，双 DataFrame 不可混用。
5. 回测前必须确认因子与价格数据存在；预热窗口不足时不得过度解读。
6. 历史 K 线信号与盘中实时信号分开表述；实时信号必须说明数据源、时间戳和延迟风险。
7. **LLM 禁止做数学**: 所有数值计算必须通过量化工具完成，LLM 仅做引用和解读。
8. **信号覆盖规则**: 量化信号 > 规则判断 > LLM 定性判断。LLM 不得推翻量化信号，只能标注"信号与定性判断不一致"的风险提示。
9. **弃权优先**: 数据错误必须向上传播（fail loud）；证据不足时明说弃权，绝不静默降级为中性观点。
10. 新分析报告写 `analysis/`；新周期任务写 `cron_jobs/registry.json`。
11. 不修改 `.env`、ClickHouse 连接配置、既有同步排除规则，除非用户明确要求。
12. `analysis/`、`scripts/`、`cron_jobs/`、`policy/`、`sql/` 各有子目录 AGENTS.md，进入目录后遵守局部约定。
13. **Python 虚拟环境**: 所有 Python 脚本必须在 `/opt/venv` 环境中执行，使用 `source /opt/venv/bin/activate`。
