# T13 Freeze Pre-Check — harness-evolution 冻结状态裁决

- **日期**: 2026-09-13
- **执行者**: T13 agent（opencode-engine-bridge-v2 计划 Wave 5）
- **worktree**: `/Users/mgong/LegoNanoBot/vibe-trading-engine-bridge`，分支 `mymain-engine-bridge`，HEAD `06507813`（T10 租户容器）
- **受检冻结面**（计划 Context 冻结清单）: `agent/mcp_server.py` 工具面、opencode CLI 钉版、OmO 钉版、`OpencodeAgent/config/vibe-trading-tools.json`、`VT_MEMORY_MCP_TOOLS`
- **T13 触碰面**: `agent/mcp_server.py` 工具面（新增 1 个 wrapper：`scheduled_research`）——需冻结已解除

## 裁决：**LAPSED（冻结窗口已关闭）→ PROCEED**

## 证据链（逐条引用）

### E1. 评测窗口已收口，四批全部终局裁决

`mymain-wiki/harness-evolution/README.md`（裁决总表，研究周期 2026-08-21 ~ 08-30）：

| 批次 | 裁决 | 终态 |
|---|---|---|
| A 描述治理（A1-A8） | ❌ 路由中性 | **均已回滚，勿重测**（DO-NOT-RE-TEST） |
| B 暴露面工程（B1-B5） | ✅ 成立 | **暂缓上游**（用户裁决 2026-08-27）；改动已落生产 |
| C 路由层 | ❌ 全部回滚 | **思路标记失败** |
| D 领域子代理 | ✅ 生产落地 | mymain 12 子代理（`552c7bfe` / `07a08aab` / `b5a7265b`，08-29/30 部署） |

没有任何批次处于"测量中"状态。冻结面的存在理由（保护评测窗口内的基线测量）随四批裁决完成而消失。

### E2. 整个目录已归档 = 只读证据，评测基建已迁出工作树

- `mymain-wiki/INDEX.md`（状态语义定义）: "`archived` 归档冻结（只读证据，内容不改）"——harness-evolution 全部 17 份文档 + `evals/` 目录逐条标记 `archived`（仅 README 导航页为 active）。
- `mymain-wiki/harness-evolution/README.md` §evals: "归档后为**只读证据**，不再随分支演进；如需复跑，按各 verdict 文档记录的协议在原分支环境中执行。"
- 归档 commit `45b52750`（2026-08-31 18:07:52 +0800，"docs(mymain-wiki): add branch knowledge base with harness-evolution archive"）。
- 本 worktree `agent/src/evals/` **不存在**（tool_selection 评测基建原始位置为 `fix/trading-tool-routing-hints` 分支，从未在 mymain 工作树中）——没有 in-tree 基线正在被保护。

### E3. 计划 Context 的冻结限定语："评测窗口内"

桥计划（`.omo/plans/opencode-engine-bridge-v2.md`）Context: "harness-evolution（XL 评测裁决计划）冻结面：`agent/mcp_server.py` 工具面、…、ClickHouse 凭据状态（**评测窗口内**）"；Must-NOT: "**NO 评测冻结窗口内的** `agent/mcp_server.py` 工具面改动（Phase 4 wrapper 前置检查冻结状态，冻结中则顺延）"。冻结是窗口限定谓词，不是永久禁令。窗口 = E1 的测量周期（08-21 ~ 08-30），已关闭。

### E4. 唯一存留观察项显式声明"不构成阻塞"

`mymain-wiki/harness-evolution/README.md` §未闭合线索: "Track B 生产遥测（twin_choice 观察窗）| 观察中 | 2026-09-26 兜底读出；4 周 <30 事件则按'功效不足'关闭，**不构成阻塞**"。这是对已落生产之 B 批改动的被动遥测读出，不保护任何工具面基线；且 T13 改动落在 `mymain-engine-bridge` 分支（未部署生产），不污染该观察窗。

### E5. ROADMAP 全部 PLAN 终态（2026-08-30 补录）

`mymain-wiki/harness-evolution/HARNESS_EVOLUTION_ROADMAP.md`: 28 PLAN 状态列全部为 ✅ 完成 / 🟡 部分完成（维持现状）/ ⏸️ 暂缓 / ❌ 划掉·回滚；E1/E2 评测基建"✅ 完成"；E4（描述变更回归）"⬜ 未启动——A 批回滚后缺回归对象"。无 in-flight 评测。

### E6. 09-06 iter3 终局：最后一条活动评测轨关闭

`mymain-wiki/branch/MYMAIN_DIVERGENCE.md`（mymain 分支版，commit `9bf579ab`）§5 "2026-09-06 specialist-arch-iter3：终局裁决——built-in loop 子代理移植放弃，PR #1286 已关闭（贡献队列 ⑥ 终结）"。这是 harness 演进谱系上最后一条活动评测轨；其终局（2 PASS / 3 FAIL，放弃，不回退代码，gate 默认关）于 09-06 落账。此后无任何评测活动保护工具面。

### E7. 队列 ⑦ 行确认 T13 门控语义 = 前置检查（本次检查即履约）

同文件 §2.3 队列 ⑦（F8 上游候选）: "② `scheduled_research` MCP wrapper（T13，`mcp_server.py` 双表面 + 6 README 计数 + tool_selection eval）…② 受 harness-evolution 冻结门控（**T13 前置检查，冻结中顺延**）"。门控要求的是执行时前置检查——即本文档；检查结论为冻结已解除，顺延条件不成立。

### E8. T10 先例：冻结面在 HEAD 上已按治理规则被合法触碰

计划 D10 + HEAD `06507813`（T10）: opencode CLI `@latest`→`1.18.30`、OmO `@latest`→`4.19.4`、base image 钉 digest——计划明文"此编辑 = 冻结的执行，不算违反 Must-NOT"。T10 已于本 worktree 落地并通过验证，证明当前治理姿态是"窗口后版本钉死 + 描述类改动跑 eval 前后对比"（AGENTS.md 约定），而非活动测量冻结。

## 裁决规则套用

- 规则 (a) "docs show the eval window closed/archived with no active baseline protection → PROCEED"：**命中**（E1+E2+E3+E5+E6：窗口 08-30 收口、目录归档只读、评测基建迁出工作树、ROADMAP 全终态、最后活动轨 09-06 终结；E4 唯一观察项显式非阻塞）。
- 规则 (b) "active freeze or cannot determine unambiguously → DEFER"：不成立——无任何文档声明活动冻结；证据一致指向窗口关闭。

## 附注（执行期约束，随裁决一并生效）

1. `OpencodeAgent/config/vibe-trading-tools.json`（15 禁用工具治理清单）仍属冻结面——T13 **不改它**；`scheduled_research` 若不在禁用清单则天然可达（执行时验证）。
2. `VT_MEMORY_MCP_TOOLS` 门控与 `_resolve_session_id` 回退链（`mcp_server.py:350-389`）不动（T14 先例：冻结 diff=0）。
3. tool_selection eval 前后对比按 AGENTS.md 约定执行；因评测基建已归档（E2），复跑使用归档 harness（`mymain-wiki/harness-evolution/evals/tool_selection/`）对本 worktree 工具面测量，协议偏差如实记录在 eval 报告中。
4. 本裁决仅覆盖 T13 的 `mcp_server.py` 工具面触碰；mandate/下单类工具永不暴露（计划 Must-NOT，与冻结状态无关）。

**结论：冻结 LAPSED，T13 PROCEED。**
