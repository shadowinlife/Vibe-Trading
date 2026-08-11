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
| D2-3 D4 铺开评审 | ✅ 9/9 候选全过准入（三轮迭代收敛） | `artifacts/d2/d4_final_verdict.md` + `d4_round1_verdict.md` |
| D2-4 C1+C2 preset 治理 | ✅ validated（30/30 preset 全覆盖） | `artifacts/d2/preset_audit.md` + 附录；commits `442c8c62`/`71963e2f` |
| **D4 生产同步** | ✅ **完成（mymain `07a08aab`）** | 11 子代理节 + 11 prompt + AGENTS.md 2→11 + quant-agent NOT-for 收紧；渲染验证 11 节全出；冒烟：quant 委派回归 ✓、fundamentals-text 新域委派 ✓、轻量取数走 escape hatch 直答（记录为校准观察项，见下） |
| Track B 遥测观察窗 | 🟢 开启中（4 周兜底 2026-09-26） | `artifacts/d2/event_taxonomy.md` §6；**新增观察项**：轻量数据取数（≤2 调用）是否绕过委派走主循环直答 |
| D2-5 通道混淆 | 跟踪类 | 检测器 `d2_telemetry/` 已备 |
| D2-6 软边界 | 已知限制（不排期） | S5f 案例已归档 |

## 无运行中任务（重启前全部落地）

D4 Round 3 traces 双判官 353/353 完整落盘（重启未造成数据损失——
判官 runner 逐条 append，断点安全）。所有裁决文档已写完。

## 新 session 从这里继续（按序）

~~1. 生产同步 D4 准入结果~~ ✅ 已完成（mymain `07a08aab`）。
~~2. trading-connector 安全评审~~ ✅ 已完成（DEC-5 通过 → mini-admission ADMIT
   `artifacts/d2/d4tc_verdict.md`（R1 0.974 / R2 0 / R3 1.000，一轮无修订）→
   生产同步 mymain `b5a7265b`，12 子代理花名册；写族永不进子代理已测试锚定）。
~~3. F2/F4/F3/D3 缺口~~ ✅ 全部闭合（DEC-3/4/6）：F2 镜像上 MCP（59→61，
   `eebf48af`）；F4 技能改写；F3 symlink + 漂移断言（`check_skills_link.py`）；
   D3 降级按需（映射数据在 AUDIT §8.1）。准入协议已资产化：
   `mymain-wiki/harness-evolution/evals/tool_selection/SUBAGENT_ADMISSION_PROTOCOL.md`。

1. **新域的主循环收敛评审**（下一个实质决策）：10 个新子代理的工具目前与
   主面双驻（staging 设计）。是否把 fundamentals-text / market-data 等域的
   工具也从主面撤下（复刻 D2-2 的 diff+探针+五场景流程），需要先回答
   Track B 观察到的校准问题：轻量取数（≤2 调用）应不应留在主循环。
2. **Track B 读出**：2026-09-26 前 twin_choice 事件 <30 → 按预授权记
   inconclusive-underpowered 关闭，不阻塞。新增观察项：轻量数据取数是否
   绕过委派走主循环直答。

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
