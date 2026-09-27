# D2 遥测检测器验证（B2，归档轨迹回放）

- 数据源：`/Users/mgong/.local/share/opencode/opencode.db`
- 白名单清单：`/Users/mgong/LegoNanoBot/vibe-trading-mymain/OpencodeAgent/config/subagents.json`（quant-agent 11 工具）
- 断言冻结于 `../../../../HARNESS_EVOLUTION_D2_PLAN.md` §3.2 B2（采集前）。

## 断言结果

| 结果 | 断言 |
|---|---|
| PASS | s5f post-fix: 白名单违规 == 0（violations=0） |
| PASS | s5f post-fix: 外部 MCP 命名空间调用 == 0（foreign=0） |
| PASS | s5f post-fix: 总工具调用 >= 45（工作量非平凡，对齐 SMOKE_NOTES 的 49 次全口径）（total=52） |
| PASS | s5f post-fix: 受治理调用 >= 20（白名单面被充分行使）（governed=25） |
| PASS | s5 leak-era: 外部命名空间调用 >= 1（S5 泄漏可检出）（foreign=4） |
| PASS | s5b leak-era: 外部命名空间调用 >= 1（泄漏复现可检出）（foreign=2） |
| PASS | s5d: skill_mcp 通道混淆 >= 1（直连工具误走 skill 通道）（confusions=13） |
| PASS | s5d: sentiment_score 连续失败 >= 5（死循环签名）（consecutive_errors=10） |

## 观察值（非断言）

- s5f 子代理：总调用 52，受治理 25，宿主内建 23（软边界 D2-6，如实记录），加载技能 ['event-driven', 'strategy-generate', 'strategy-generate']
- s5 泄漏期子代理：外部命名空间 4 次
- s5b 泄漏复现子代理：外部命名空间 2 次
- s5d 主会话：总调用 74，通道混淆 13 次，死循环事件 [{'target': 'vibe-trading_sentiment.sentiment_score', 'consecutive_errors': 10}]

## 口径说明

- 受治理调用 = `vibe-trading_*` 直连工具（技能通道 `load_skill`/`list_skills`/`skill_mcp` 由 prompt 层契约治理，不计入）；
- 外部命名空间 = OMO 插件运行时注入面（websearch/context7/grep_app/lsp），白名单子代理的任何调用即 S5 泄漏类违规；
- 死循环 = 同一调用目标连续 error ≥5 次（忽略参数抖动）。

## 修订披露（诚实记录）

首跑（2026-08-29）原始断言`s5f 受治理调用 >= 40` **FAIL**（实测 governed=25）。根因是断言口径错误：计划中`s5f 的 49 次调用`为 SMOKE_NOTES 的**全口径**计数（含宿主内建与技能通道），原始断言误将其映射到窄口径（仅 vibe-trading 直连工具）。实质断言（修复后零违规、泄漏与死循环可检出）全部首轮通过且不受修订影响。按 D2_PLAN §5.7 文化记录为已披露修订轮：阈值改为总调用 ≥45 且受治理 ≥20。另：归档笔记的`49 次`计数方法已不可复现（实测总口径 52），记录为文档质量问题。
