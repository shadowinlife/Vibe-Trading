# ClickHouse 数据获取最佳实践 — 迭代计划（v1.1 · 决策已锁定）

> **状态：D0–D3 决策已锁定（2026-08-12），待批准执行 Phase 0** — 所有触及生产库的步骤均为「dry-run 出稿 → 人工批准 → 执行 → 验证」。
> **依据**：[`CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md`](CLICKHOUSE_SEMANTIC_LAYER_RESEARCH.md)（正式调研结论，2026-08-12）。
> **验证**：2026-08-12 于 `root@47.98.53.40` 现场只读核验（全部为 SELECT/系统表查询，零变更）。

---

## 0. 真实数据验证结论

### 0.1 环境快照（实测）

| 项 | 实测值 |
|---|---|
| 实例 | ClickHouse **24.8.14.39**，容器 `clickhouse`（Up 2 weeks），8123/9000 映射 0.0.0.0 |
| 网络 | 公网 `47.98.53.40:8123/9000` 被安全组拦截（实测不可达 ✅）；VPC IP **172.24.165.51**（= connector 代码默认地址，即该机 eth0）内网**无密码可达** ⚠️ |
| 数据 | `ashare` 库 **56 张 MergeTree 表**；`stk_factor_pro` 18,295,590 行 × 6,119 标的，区间 1990-12-19 ~ **2026-07-28** |
| 用户 | 仅 `default`（**无密码**、全权限含 ACCESS MANAGEMENT/DROP/TRUNCATE、networks=`::/0`）；无 llm_role、无任何 role |
| 同步管道 | `/opt/qdata/sync/`（host cron `30 18 * * 1-5`，conda env `legonanobot`），**非 git 仓库**；`schema.py` 持 `CREATE TABLE IF NOT EXISTS`（DDL 真源）、`engine.py`/`migration.py` 持 `TRUNCATE`（快照表全量覆盖） |
| 运行时可达性 | 调用机器（MCP 运行时）位于**同 VPC 的其他 ECS/ECI**，直连 172.24.165.51:8123 无障碍（宿主机进程可用即通）；本地开发机走**公网 + 白名单**访问宿主机（SSH） |

### 0.2 假设核对（对照 RESEARCH.md）

| # | 调研假设 | 实测 | 方案调整 |
|---|---|---|---|
| 1 | `stk_factor_pro` ~199 列无标注 | ✅ **恰好 199 列**，COMMENT 0/199 | 无 |
| 2 | 数据库层零语义 | ✅ 56 表 / **~1,279 列 COMMENT 全空**（表级 comment 亦全空） | 无 |
| 3 | UInt64 损坏风险（官方 MCP #111） | ⬇️ 数据列**全为 Float64**；UInt64 仅 `table_sync_state.rows_written` 1 列 | 风险降级；L3 仍自建序列化（防御纵深，防未来 schema 演进） |
| 4 | 无只读用户 | ✅ 且比假设更差：default 无密码 + 全权限 + `::/0` | 安全加固优先级上调（P0.3） |
| 5 | 单位换算固化在代码层 | ✅ 真实数据锚点验证：`vol=手`、`amount=千元`（`amount×10/vol≈close` 三日均交叉验证通过）、`total_mv/north_money=万元`、`margin=元` | 无；Phase 1 获得真实回归锚点 |
| 6 | （新发现）数据新鲜度 | ❌ `stk_factor_pro` 停在 **2026-07-28**（缺 ~11 个交易日）；8-11 同步日志报 `Invalid None value in non-Nullable column record_date`（20260527~20260715 多个日期） | **新增 P0.0 同步健康项（决策 D0）** |
| 7 | （新发现）DDL 真源 | `/opt/qdata/sync/schema.py`（不在 git） | **DDL 治理决策（D1）** |
| 8 | （新发现）运行时可达性 | ✅ 已澄清：调用机器在同 VPC 其他 ECS/ECI，直连无障碍；本地开发机走公网+白名单（SSH 通道） | 无需决策。开发机若需直连 CH 调试：P0.3 完成（default 有密码、llm_role 就位）后，可在安全组为白名单 IP 开放 8123，以 `llm_role` 连接 |

### 0.3 方案适用性结论

**真实数据完全适用于 RESEARCH.md 的 L0–L4 方案**，且验证带来三处修正：
1. UInt64 风险降级 → 自建序列化从「必须」变为「防御纵深」；
2. 发现同步管道这一 DDL 真源 → COMMENT 持久性需要治理策略（D1）；
3. 发现数据停更 → 语义层不能建在过期数据上，P0.0 前置（D0）。

---

## 1. 目标 / 非目标

**目标**
1. 语义下沉数据库（L0）：DDL 入仓库 + 结构化 COMMENT + `llm_role` 只读用户；
2. 主通道强化（L1）：`SELECT *` 消除、`_provenance` 单位元数据、`get_valuation` 显式工具、元数据驱动换算；
3. 灵活性通道（L2/L3）：分层探索工具 + 受约束 SQL（按需启动）;
4. 治理：CI gate 防语义漂移。

**非目标（本轮不做，调研结论 §6.4）**
- 不引入 dbt SL / Cube / 官方 mcp-clickhouse；
- 不改回测引擎数据契约（envelope 只做增量字段）；
- 不触及任何交易/下单路径（本计划全部为只读数据层）。

---

## 2. Phase 0 — 地基与健康（估 1.5~2.5 天，零运行时变更）

### P0.0 同步健康诊断与修复 ✅ 已决策：纳入计划，Phase 0 第一项
- **症状**：`stk_factor_pro` max(trade_date)=2026-07-28，而 cron 日志更新至 8-11；日志报 `record_date` 非空违约（疑似 `stk_dividend` 类表），可能拖累 trade_date 模式整体同步。
- **动作**：读全 `/opt/qdata/sync/logs/daily_*.log` → 定位失败表与根因 → 出修复方案（Nullable 化 / 空值跳过 / tushare 参数修正）→ **修复脚本改动单独审批**（`/opt/qdata` 不在 git：改动前 `tar` 备份，改动后记录于 `MYMAIN_DIVERGENCE.md` 附录）。
- **验收**：手动触发一次同步后，`stk_factor_pro` max(trade_date) 追平 T-1；日志 error 计数归零。

### P0.1 DDL 入仓库 ✅ 已决策：演进式（Phase 0 仓库快照+CI gate → Phase 3 回写管道并 git 化）
- 导出全部 56 表 `SHOW CREATE TABLE` → `schema/clickhouse/ashare__<table>.sql`（每表一文件，git 版本化）。
- 附 `schema/clickhouse/README.md`：声明真源关系（仓库 = 语义契约快照 + CI gate 依据；`/opt/qdata/sync/schema.py` = 物理建表者）与漂移处理流程。
- 交付：`schema/clickhouse/` + 可重跑导出脚本 `tools/clickhouse_export_ddl.py`。

### P0.2 结构化 COMMENT ✅ 已决策：Tier 1 = 9 表 ≈450 列
- **约定**：`unit=<单位>; adjust=<raw|hfq|qfq|bfq>; caliber=<口径>; source=tushare <api>; desc=<中文说明>; ambiguous_with=<易混列>`。
- **范围（推荐）**：Tier 1 = connector 实际访问的 8+1 表 ≈ **450 列**：`stk_factor_pro`(199)、`fin_indicator`(168)、`stk_moneyflow`(20)、`stk_top_list`(15)、`stk_margin`(11)、`stk_info`(10)、`stk_top_inst`(10)、`stk_moneyflow_hsgt`(7)、`trade_calendar`(4)。
  - 注：`fin_indicator` 168 列占 Tier 1 的 37%，若逐列人工标注成本过高，可降档为「关键列精标 + 其余列挂 tushare 官方文档口径链接」。
- **执行**：COMMENT 定义存 `schema/clickhouse/comments.yaml`（仓库内，可评审）→ `tools/clickhouse_apply_comments.py` 生成 ALTER 语句 → **dry-run 出稿 → 人工批准 → 批量执行** → `system.columns` 复核。
- **持久性**：日常同步只有 `CREATE TABLE IF NOT EXISTS`（不重建已有表）与 `TRUNCATE`（不动 schema），ALTER COMMENT 可存活；真正的长期风险是管道重建表 → 由 D1 的演进路径兜底。
- **验收**：`SELECT count() FROM system.columns WHERE database='ashare' AND table IN (<Tier1>) AND comment=''` = 0。

### P0.3 llm_role 与权限收紧 ✅ 已决策：llm_role + default 设密码 + networks 收紧（与 agent/.env 原子执行）
- `CREATE USER llm_role IDENTIFIED WITH sha256_password BY '<生成密码>'`；`GRANT SELECT ON ashare.*`；settings profile：`max_execution_time=30`、`max_memory_usage=2G`、`max_rows_to_read=1000000`、`max_bytes_to_read=50MB`。
- **推荐**：同时给 `default` 设密码 + networks 收紧至 VPC 段；**必须与 `agent/.env` 增加 `CLICKHOUSE_USER`/`CLICKHOUSE_PASSWORD` 原子执行**（否则本地 connector 断连——当前以空凭据连接 default）。
- 密码只存 `agent/.env`（gitignored），绝不入仓库。
- **验收**：llm_role 的 INSERT/CREATE/DROP 全部被拒、SELECT 正常；default 无密码登录被拒。

### P0.4 CI gate
- `tools/ci_clickhouse_comments_gate.py`：断言 `schema/clickhouse/` 覆盖表的每一列都有非空 COMMENT 定义（仿 `tools/ci_env_var_gate.py` 模式）。
- **验收**：删除仓库中任一 COMMENT 定义 → CI 红。

---

## 3. Phase 1 — 主通道强化（估 3~5 天，mymain 代码）

| # | 任务 | 要点 |
|---|---|---|
| P1.1 | `SELECT *` 消除 | `loaders/clickhouse.py` 的 `SELECT *` 改显式列清单（契约固化，物理关闭 Flow B 泄漏） |
| P1.2 | `_provenance` 单位元数据 | envelope 增量附加 `volume_unit=lot`、`amount_unit`、`price_adjust`、`caliber`；与上游 #1065/#1067 以 rebase-friendly 形式协同（mymain 先行，上游合入后对齐） |
| P1.3 | `get_valuation` 显式工具 | 固定模板 pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate + COMMENT 口径注释 + tushare daily_basic 兜底 |
| P1.4 | 元数据驱动单位换算 | 新增 unit registry（读 `comments.yaml` 约定）；`clickhouse_fallbacks.py` 的 `×10⁴`/`×100` 硬编码移除（过渡期留断言） |

**验收**
- CH 测试基线（13 passed / 8 skipped）保持绿 + 新增单测；
- 回测回归：2 个固定配置（A 股单标的/多标的）输出前后 bit-for-bit 一致；
- 真实数据锚点通过：600519.SH 2026-07-27（close=1289.5，vol=31990.44 手，amount=4129228.56 千元，total_mv=161198022.32 万元，north_money 万元，margin 元）。

## 4. Phase 2 — 灵活性通道（估 3~5 天，默认 Phase 1 验收后启动）

- `ch_list_tables`：56 表名 + 一行描述（读表级 COMMENT）；
- `ch_describe_table`：列/类型/COMMENT/2~3 样本行/分区与排序键；
- `ch_query`：以 `llm_role` 连接；sqlglot SELECT-only AST 守卫 + 表白名单 + 参数化 + 强制 LIMIT 注入（默认 500 行 / 50KB 上限，超限显式截断声明）+ 30s 超时 + 查询审计日志；自建结果序列化（#111 防御纵深）；
- 社区约束：README/SKILL.md 数量门槛（五个 README + agent/SKILL.md）同步更新。

**验收**：golden set 通过率 ≥90%；所有非 SELECT 被拒；超限结果截断且带显式声明。

## 5. Phase 3 — 可选演进（按需）

- golden 问题集（10~20 题：单位陷阱/复权陷阱/相似列陷阱）固化为回归基线；
- COMMENT 回写 `/opt/qdata/sync/schema.py` 并将 `/opt/qdata` 纳入 git（长期单一真源，D1 演进策略的终点）;
- Altinity 式 view→tool 自动化；ODCS YAML 契约；
- 上游社区：语义层以独立 PR 提交 HKUDS/Vibe-Trading（Phase 1/2 本地稳定后）。

---

## 6. 风险与回滚

| 操作 | 风险 | 回滚 |
|---|---|---|
| ALTER COMMENT | 无（纯元数据） | `ALTER ... COMMENT COLUMN ''` 清空 |
| CREATE USER llm_role | 无 | `DROP USER llm_role` |
| default 设密码 | 本地 connector 断连 | 与 `agent/.env` 更新原子执行；回滚 = 恢复 users.xml + 移除 env 项 |
| `/opt/qdata/sync` 修复 | 数据写入行为变化 | 改动前 `tar` 全量备份；脚本级回退 |
| Phase 1 envelope 变更 | 下游消费方契约漂移 | 单位字段只增不改；必要时 feature flag |

## 7. 合规注意（执行期）

- 代码风格：black/ruff，文件 ≤400 行（practical）/800 行（hard cap）；
- 提交：`git commit -s`（DCO），**禁止** AI trailers（Co-Authored-By / AI-Model / AI-Contributed）；
- 仓库内不出现任何密码/token（llm_role 密码仅存 `agent/.env`）；
- 提交前经 `code-review` skill 复核。

## 8. 人工决策记录（2026-08-12 锁定）

| # | 决策 | 结果 |
|---|---|---|
| D0 | 同步停更处置 | ✅ **纳入计划，Phase 0 第一项**（P0.0：诊断根因→修复方案→脚本改动单独审批，改动前 tar 备份） |
| D1 | DDL/COMMENT 真源策略 | ✅ **演进式**：Phase 0 仓库 DDL 快照 + CI gate + ALTER 写活库；Phase 3 COMMENT 回写 `/opt/qdata/sync/schema.py` 并将 `/opt/qdata` 纳入 git |
| D2 | 安全加固范围 | ✅ **llm_role + default 设密码 + networks 收紧至 VPC 段**，与 `agent/.env` 凭据更新原子执行 |
| D3 | COMMENT 覆盖范围 | ✅ **Tier 1：9 表 ≈450 列**（connector 访问面），其余表 Phase 1/2 间隙推进 |

> 网络可达性已澄清（调用机器同 VPC 直连；开发机公网+白名单），不作为决策点。

**执行顺序**：P0.0 → P0.1/P0.2/P0.3（可部分并行）→ P0.4 → Phase 1 → 验收 → Phase 2 → Phase 3。
