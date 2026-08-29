# D 子代理工作组 — 长程任务恢复点（2026-08-29 机器重启前快照）

> 新 session 读本文件即可全量接续。治理主文档：`HARNESS_EVOLUTION_D2_PLAN.md`
> （预注册判据 + 状态机 + revision log）。

## 分支地形

| 分支 | 用途 | 顶端 |
|---|---|---|
| `mymain`（worktree: `../vibe-trading-mymain`） | 生产部署分支 | `552c7bfe`（主循环收敛，单 commit 回滚单元） |
| `fix/trading-tool-routing-hints`（主 checkout） | 评测基建 + 证据归档 | 见 `git log` |

## 状态机（截至快照）

| 工作项 | 状态 | 证据 |
|---|---|---|
| D2-1 孪生仲裁门禁 | ✅ validated | `artifacts/d2/track_a_verdict.md`（两域 CI 下界为正） |
| D2-2 主循环收敛 | ✅ validated（生产已上） | `artifacts/d2/mainloop_convergence_verdict.md`；59→46 工具 |
| D2-3 D4 铺开评审 | ✅ **9/9 候选全过准入**（三轮迭代收敛） | `artifacts/d2/d4_final_verdict.md` + `d4_round1_verdict.md` |
| D2-4 C1 preset 试点 | ✅ validated | `artifacts/d2/preset_audit.md` 附录（sentiment 工具 54 次真实调用） |
| Track B 遥测观察窗 | 🟢 开启中（4 周兜底 2026-09-26） | `artifacts/d2/event_taxonomy.md` §6 |
| D2-5 通道混淆 | 跟踪类 | 检测器 `d2_telemetry/` 已备 |
| D2-6 软边界 | 已知限制（不排期） | S5f 案例已归档 |

## 无运行中任务（重启前全部落地）

D4 Round 3 traces 双判官 353/353 完整落盘（重启未造成数据损失——
判官 runner 逐条 append，断点安全）。所有裁决文档已写完。

## 新 session 从这里继续（按序）

1. **生产同步 D4 准入结果**（下一步主任务）：9 个准入候选合入 mymain——
   - 定义源：`agent/src/evals/tool_selection/d4_batch/candidates_d4.yaml`
     （v3 描述，三轮迭代收敛版）+ 拆分定义文件 9 个；
   - 为 9 个候选撰写生产 prompt（模仿 `OpencodeAgent/config/prompts/quant_agent.md`
     的 Tool contract + Twin arbitration + Output contract 结构）；
   - 合入 `OpencodeAgent/config/subagents.json`（prompt 用 `{file:./prompts/}`
     相对引用，colocation 机制已就绪）；
   - `OpencodeAgent/AGENTS.md` 路由政策从 2 子代理扩写至 11；
   - quant-agent 生产 description 应用 D4 侧 v2 NOT-for 收紧补丁
     （`d4_batch/subagent_quant_agent.yaml` 为参照）；
   - 跑 `OpencodeAgent/tests/`（33 测试）+ L2 冒烟（参照
     `artifacts/d2/mainloop_convergence_verdict.md` 的五场景协议）。
2. **trading-connector-agent 安全评审**：另立工作项（trading_* 全局 deny 中）。
3. **Track B 读出**：2026-09-26 前 twin_choice 事件 <30 → 按预授权记
   inconclusive-underpowered 关闭，不阻塞。
4. **C2**（27 个 preset 治理铺开）：C1 已过，可随时启动。

## 关键工程事实（重启后勿重推导）

- opencode `{file:}` 按**配置文件所在目录**解析；缺文件 = 启动即致命。
- agent 级 permission allow **压过**全局 deny（1.18.23 探针实证）。
- task() 派生子代理能收到 config prompt（加性签名探针实证；与任务冲突的
  单行指令会被任务框架压过——探针设计教训）。
- 判官基建：`run_d_judge.py --definitions/--queries-file/--extra-routes`；
  模板 hash 24809ade（路由）/ b0e0fb11（选择 v1）/ acc5eac5（选择 v2 政策块）。
- 语料纪律：动词不可区分即丢弃；验证器是筛子不是标注员；标签与生产契约
  冲突时以生产契约为准（D2_PLAN §8）。
- 判官格式噪声：kimi 偶发裸名作答（严格口径计 miss，方向保守）。
