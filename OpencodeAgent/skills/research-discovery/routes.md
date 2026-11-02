# 按研究问题选择能力组合

路线 ID 对应 [recipes.json](recipes.json)。这些卡是组合模板，不是工具注册表；运行时要查工具 schema、金融 skills 与 swarm variables。

| 路线 | 用户要判断什么 | 所需输入 / 增量证据 | 成本 | 市场与不能证明的边界 |
|---|---|---|---|---|
| strategy_catalog | 有哪些策略值得进一步研究？ | 目录、策略 ID、实际 per-regime 证据与新鲜度 | 目录查询 | A股/美股/加密按真实策略配置核验；没有 adequate 证据不自动放宽门槛，目录不等于推荐 |
| strategy_test | 一条规则在这个市场是否有效？ | 明确规则、标的、独立样本窗口、成本、基准；生成契约→回测→结果 | 单次或约定批量回测 | 各市场分别核验数据/费用/日历；样本内有效不等于样本外/未来有效，不自动优化 |
| technical_structure | 是否有反转/趋势结构证据？ | 有序 OHLCV→单一指标/形态/chanlun→风险解释 | 标的数据与计算 | A股/美股/加密有数据才可做；skill 不是同名 MCP 函数，顶底形态不是预测未来价格 |
| ashare_top_risk | 大盘是否有拥挤或逃顶风险？ | ClickHouse/网络源→微观结构脚本→验证分类与覆盖 | 市场级取数/计算 | **仅 A股**；只引用已验证信号的相应证据，其余保留 RESEARCH_ONLY/BLOCKED 标签，不移植到美股/加密 |
| thesis_review | 多空论据和风险是否一致？ | 目标与证据缺口→list_swarm_presets→核对变量→用户选择→run_swarm→结果 | 多代理研究 | 按真实 preset 和数据覆盖判断；不为简单查询/完整回测启动团队，swarm 不替代回测 |
| opportunity_screen | 在选定范围找哪些候选？ | 用户选定市场/范围、筛选规则→数据→候选及理由 | 市场扫描 | screen_market 仅 a/us/hk；加密先限定交易对，用真实可用数据计算，不传 market=crypto |
| event_risk | 事件通过什么渠道影响目标？ | 事件、标的、窗口→来源/传导假设→对照/情景 | 文档研究与计算 | A股用已接线 ashare-*；美股用 SEC/新闻；加密用实际可用来源。历史事件/情景不是事件预测 |
| allocation_review | 不同资产的风险暴露与取舍是什么？ | 用户提供持仓或非个性化范围→分市场取数→风险比较；需要时再 swarm | 多资产研究 | 统一币种/时区/交易日/费用/资金费率并披露交集；缺私有信息不编造仓位，不默认读取账户 |

## 组合步骤

- **策略**：先 `list_strategies`/`query_strategies`/`get_strategy_evidence`；只有用户选定测试才加载 `strategy-generate`。adapt_strategy 仅用于符合其 fresh/adequate SDM 条件的父策略，不虚构 strategy_id 或复用父回测指标。
- **技术顶底**：选择回答问题所需的方法。单方法先做；用户明确要多方法共振才选 `technical_analysis_panel`。大盘逃顶和单股/交易对反转不能互换。
- **swarm**：通过 `list_swarm_presets` 查 required variables；选用 investment/risk/global allocation 或其他确切 preset，不固定传 target/market。按既有 start_only 与 run_id 恢复；VT swarm 和 OMO 不嵌套。
- **风险/事件**：成本价、仓位 unknown 时先做非个性化风险/事件研究；只有个性化解释需要它们才追问。用户拒绝提供不影响公开资料研究。
- **跨市场**：共享探索协议，不共享虚构数据覆盖或默认基准。窗口过短、缺基准、成本无法核验则在验收中记 blocked/insufficient，不让 LLM 补数字。

## 能力状态的四层

1. 目录存在：当前工具、skill 或 preset 是否存在？
2. 配置允许：是否被禁用、是否需要额外权限？
3. 数据可用：标的、窗口、频率、字段是否真的返回？
4. 证据合格：是否足以回答这个研究问题？

任一失败都明确返回原因与可回答的替代问题；不要用“工具列表有它”当作承诺。
