---
title: P0.0 ClickHouse 同步停更诊断报告（2026-08-12）
description: CH 同步管道停更的诊断与修复全程记录——同步管道出问题时读。触发词：同步、停更、/opt/qdata、daily log、诊断。
type: postmortem
status: active
created: 2026-08-12
updated: 2026-08-12
tags: [clickhouse, sync, postmortem]
related: [CLICKHOUSE_ITERATION_PLAN.md]
---

# P0.0 同步停更诊断报告（2026-08-12）

> 状态：根因已锁定（双重缺陷），修复方案待人工批准。全部证据来自 `root@47.98.53.40` 只读诊断 + 受控复现脚本。

## 1. 现象

`ashare` 库全部 trade_date 维度表冻结在 2026-07-21 ~ 2026-07-28（实测水位）：

| 表 | max(trade_date) | 说明 |
|---|---|---|
| stk_factor_pro | 2026-07-28 | 缺 ~11 个交易日 |
| stk_suspend | 2026-07-28 | |
| fund_daily | 2026-07-28 | |
| stk_moneyflow | 2026-07-27 | |
| stk_st_daily | 2026-07-27 | |
| stk_margin | 2026-07-24 | |
| stk_dividend | 2026-07-21 (max ann_date) | |
| idx_weight | 2026-07-09 | |

period 表（fin_*）max(end_date)=2026-06-30；snapshot 表自 7-28 后未再刷新。

## 2. 时间线（日志 + 文件 mtime 重建）

| 时间 | 事件 |
|---|---|
| ≤7-22 | 旧管道正常（数据至 7-21/7-24 不等） |
| 7-22~7-23 | 管道重构（`sync/clickhouse/*` 全套 mtime 7-22~7-23；phase1/phase2 修复脚本与日志） |
| 7-23/7-24 | **daily_sync.sh 与新 CLI 不匹配**（`--group` 参数已不存在）→ 两日零同步（703 字节日志为证） |
| 7-26 23:59 / 7-27 00:00 | `__main__.py` / `daily_sync.sh` 更新为新 CLI（`clickhouse sync --mode ...`） |
| **7-28 11:34~11:37** | **投毒运行**：一次全日历同步把 `stk_factor_pro` 等表从 20260729 到 20271231 的全部日期标记 `is_sync=1`（未来日期 rows_written=0） |
| 7-28 起每日 18:30 | cron 运行：重试 is_sync=0 的错误日期 → `stk_suspend.suspend_timing` / `stk_dividend.record_date` None 序列化失败 → exit 1 → `set -e` 中止脚本 → snapshot/period 模式从未执行 |

## 3. 根因（两个独立缺陷叠加）

### 缺陷 A：状态投毒 —— 空结果被永久标记为已同步

`engine.py::sync_trade_date_table`：

```python
dates = get_trade_dates(self._pro, start_date, end_date)  # 空参数 → 全日历（含未来至 2027-12-31）
pending = [d for d in dates if d not in synced]
```

`_sync_pending` 中 `df.empty → state.write(is_sync=1, rows=0)`。于是未来日期（tushare 必然返回空）被永久标记已同步；**当这些日期真正到来时不再进入 pending，数据永久缺失**。7-28 的投毒运行把 20260729+ 全部锁死 → stk_factor_pro 冻结。

**铁证**：`table_sync_state` 中 stk_factor_pro 的 20260729~20260811 全部 `is_sync=1, rows_written=0, updated_at=2026-07-28 11:34`；而 tushare 实测 20260811 可返回 **5,536 行**、20260729 返回 5,524 行 —— 数据源完全正常。

### 缺陷 B：None 泄漏 —— pandas dtype 检查漏掉 Arrow 字符串列

`engine.py::normalize_dataframe` 只对 `dtype == object` 的列做 `fillna("")`。新版 tushare 客户端返回的字符串列是 **Arrow-backed `str` dtype**（实测 `record_date: dtype=str`、混合值时 `suspend_timing: dtype=str`），绕过清理；`align_column_types` 的 String 分支 `astype(str)` 也未能消除 NA（实测管道出口 `record_date` 样本仍含 `nan`）→ clickhouse_connect 写非空 String 列抛 `DataError: Invalid None value`。

**铁证**（受控复现，2026-08-12）：
- `dividend(ann_date=20260616)`：record_date NA=3/20，dtype=str；经 normalize+align 后 tolist 仍含 NA。
- `suspend_d(trade_date=20260513)`：suspend_timing NA=27/27（全 NA 时 dtype=object 侥幸被清理；混合值日期 dtype=str 则泄漏 —— 与"仅特定日期报错"吻合）。

### 放大器：daily_sync.sh 的 `set -euo pipefail`

任一模式 exit 1 即中止整个脚本 → trade_date 模式的错误（缺陷 B + stk_cyq_chips 全量 9041 日期报错，疑似 token 无筹码接口权限）连带杀死 snapshot/period 模式与健康检查。

## 4. 修复方案（待批准）

> 全部改动限于 `/opt/qdata/sync/`（改动前 `tar` 备份）与 ClickHouse 状态表；不动业务数据表结构。

### F1. engine.py 补丁（2 处，~10 行）
1. **String 列 NA 清理**：`align_column_types` 中凡 CH 类型含 `String`（含 LowCardinality/Nullable 包装）一律 `series.fillna("").astype(str)` + 保留现有 replace 守卫；
2. **未来日期防护**：`sync_trade_date_table` 计算 pending 前过滤 `d <= today`；`_sync_pending` 空结果仅在 `dim_val <= today-7d`（宽限期）时标记已同步，近 7 日空结果保持 pending 次日重试（防 tushare 延迟发布导致的二次投毒）。

### F2. daily_sync.sh 补丁
去除 `set -e` 对三个模式的连带中止：逐模式捕获退出码、全部执行、末尾以最大退出码退出（cron 仍能看到失败）。

### F3. 状态表清理（ClickHouse mutation）
```sql
-- 重开所有被投毒的"空且已同步"日期（过去+未来），保留错误行(is_sync=0)继续重试
ALTER TABLE ashare.table_sync_state DELETE
WHERE dimension_type = 'trade_date' AND is_sync = 1 AND rows_written = 0;
```

### F4. 回填运行（F1+F3 之后）
手动依次执行 `--mode trade_date` / `--mode period` / `--mode snapshot` 各一次；验收：全部 trade_date 表 max(trade_date) ≥ T-1，stk_factor_pro 行数追平（20260811 应 ~5,536 行/日）。

### F5. 遗留问题（需人工决策，不阻塞 F1-F4）
- `stk_cyq_chips`（9041 日期全错）/ `stk_cyq_perf`：疑似 token 无筹码分布接口权限 → 已移入 `EXCLUDED_TABLES`（2026-08-12 批准执行），待权限确认后恢复；
- 8-11 上午 10:31~13:51 的非 cron 全量运行已确认为**用户手动运行**（无需排查）。

### F6. idx_weight 深度根因（2026-08-12 下午追加，已修复）

初步曾判断为"tushare 数据源侧限制"，经 doc_id=96 文档对照与分页探测后**推翻**，真实根因如下：

1. **tushare 末页语义缺陷**：`index_weight` 单日期数据量 10~12 万行（需 17+ 页 × 6000 行/页）。当 `offset` 越过数据末尾时，tushare **报错**（"查询数据失败，请确认参数"）而非返回空页 → 引擎 `_paginate` 把末页错误当致命错误 → **所有有数据的日期必然失败**（391 个错误日期 = 全部月末快照日期，2012-10 → 2026-06）。
2. **深分页上限**：实测 offset ≥ ~102000 被拒（20260630 数据止于 96000~102000 页间）；offset ≤ 96000 全部正常。
3. **瞬态失败叠加**：高密度突发请求下 tushare 还会对合法调用随机返回同一错误信息（直连单次调用成功、管道批量调用失败的对照实验证实）。

**修复（已落地 `/opt/qdata/sync/clickhouse/engine.py`）**：
- `_query_with_retry`：页级 3 次重试 + 2s/4s 退避（吸收瞬态失败）；
- `_paginate` 末页容错：已成功取页后的页级错误视为**数据末尾**（break 返回已取数据），首页错误仍致命；
- 端到端验证：20260630 成功写入 102,000 行（17 页 + 末页容错），state `rows_written=102000`，落位日期正确。

### F7. tushare 上游结构漂移治理（2026-08-12 用户指令，已落地）

**要求**：tushare 上游表结构变更时，ClickHouse 必须保持一致。

**管道侧（engine.py `_apply_upstream_drift`）**：每次非空抓取比对 tushare DataFrame 列与 CH 列——
- 新列 → 自动 `ALTER TABLE ADD COLUMN IF NOT EXISTS`（Nullable 类型按数据推断，可回滚）；
- 上游消失的列 → 仅记录，绝不自动删列；
- 全部事件审计入 `ashare.schema_drift_log`（event_time/target_table/event_type/column_name/detail）+ 日志 `[DRIFT]` 前缀。

**仓库侧闭环**（`schema/clickhouse/README.md` 已文档化）：漂移事件后必须 re-export DDL 快照 + 补 `comments.yaml`（CI gate 强制新列必须有注释）+ 对照 tushare 官方文档复核。

## 5. 回滚

- F1/F2：改动前 `tar -czf /opt/qdata/sync_backup_$(date +%Y%m%d).tar.gz /opt/qdata/sync`，回退 = 解包覆盖；
- F3：mutation 只删 rows_written=0 的状态行，误删也可由下一次同步重建（状态表本身是缓存）；
- F4：纯数据追平，无结构变更。
