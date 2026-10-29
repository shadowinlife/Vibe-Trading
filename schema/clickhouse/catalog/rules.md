# RULES — A 股取数判定规则（agent 必读）

> 本文件是**行为契约**，不是表说明。表级说明见 `tables/<table>.md`，路由见 `INDEX.md`。
> 读不到数据时，**先按本文件判定，不要猜、不要静默换源、不要把近似值当官方值**。

---

## R1. 先判资产类别，再选表

| 用户说的是什么 | 判定 | 行情表 | 估值表 |
|---|---|---|---|
| 股票名/6 位代码 + `.SZ/.BJ`，或 `6xxxxx.SH`、`0xxxxx.SZ`、`3xxxxx.SZ`、`8xxxxx.BJ` | 个股 | `stk_factor_pro` | `stk_factor_pro`（pe/pe_ttm/pb） |
| ETF/LOF：`15/16/50/51/52/56/58` 开头的 6 位 + `.SH/.SZ` | 基金 | `fund_daily` | **无**（见 R3） |
| 指数：`000xxx.SH`、`399xxx.SZ`、`899xxx.BJ` | 指数 | `idx_factor_pro` | `idx_dailybasic`（残缺，见 R3） |
| 板块（BK 代码） | 板块 | `idx_daily_dc` / `idx_quote_dc` | **无** |

**后缀决定一切**：`000001.SZ` 是平安银行（个股），`000001.SH` 是上证指数（指数）。不要用 6 位数字本身判断。

```sql
-- 不确定名称对应哪个代码时，先解析，不要靠记忆
SELECT ts_code, name FROM stk_info WHERE name LIKE '%关键词%';   -- 个股
SELECT ts_code, name FROM idx_info WHERE name LIKE '%关键词%'
  AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ');               -- 指数
```

> `idx_info` 里有 8000 条，含大量 `.CSI` 外币/全收益变体（`000688USD07.CSI`）。**必须锚定 `.SH/.SZ`**，否则会选到同名外币指数。

---

## R2. ETF 找不到数据 / 需要指数：先解析「跟踪哪只指数」

库里**没有** ETF→指数的映射表（`fund_basic` 未同步）。按以下顺序，**不得跳步**：

1. **查已核验映射**（本文件 R2.1）。命中即用，并注明"映射来源：semantic-layer 已核验表"。
2. **用 ETF 名称推断**：`fund_daily` 只有代码没有名称，需先用外部工具（`search_symbol`）拿到全称，再按关键词到 `idx_info` 匹配。
3. **匹配不上就停下**，向用户确认指数代码。不要拿"科创50"直接去猜 `000688.SH` 之外的东西，也不要用 `.CSI` 变体顶替。

### R2.1 已核验映射（2026-10-29 校验，均存在于 `idx_factor_pro`）

| ETF | 名称 | 跟踪指数 | 指数名称 |
|---|---|---|---|
| `510300.SH` | 华泰柏瑞沪深300ETF | `000300.SH` | 沪深300 |
| `510500.SH` | 南方中证500ETF | `000905.SH` | 中证500 |
| `512100.SH` | 南方中证1000ETF | `000852.SH` | 中证1000 |
| `588000.SH` | 华夏上证科创板50成份ETF | `000688.SH` | 科创50 |
| `588080.SH` | 易方达上证科创板50成份ETF | `000688.SH` | 科创50 |
| `159915.SZ` | 易方达创业板ETF | `399006.SZ` | 创业板指 |
| `510050.SH` | 华夏上证50ETF | `000016.SH` | 上证50 |
| `512880.SH` | 国泰中证全指证券公司ETF | `399975.SZ` | 证券公司 |
| `512690.SH` | 鹏华中证酒ETF | `399987.SZ` | 中证酒 |

**未解析的（必须停下问）**：`512480.SH`（半导体）、`515000.SH`（科技龙头）、`516160.SH`（新能源）等——基准指数不在 `.SH/.SZ` 命名空间或不在 `idx_factor_pro` 内。

---

## R3. 估值缺失时的降级阶梯（禁止越级）

问「科创50ETF / 科创50 的 PE、PB」时，按顺序：

**第 1 级：官方指数估值 `idx_dailybasic`**

```sql
SELECT trade_date, pe, pe_ttm, pb
FROM idx_dailybasic
WHERE ts_code = '000688.SH'
ORDER BY trade_date DESC LIMIT 1;
```

先看两个前置条件，任一不满足就降到第 2 级：

- 该代码在表里存在（`000688.SH` **不存在**，只有 12 个指数）；
- 最新日期在 SLA 内（2026-10-29 实测停在 2025-07-04，**已过期**）。

**第 2 级：成分股加权近似（必须披露覆盖率）**

```sql
WITH w AS (
  SELECT con_code, weight
  FROM idx_weight
  WHERE index_code = '000688.SH'
    AND trade_date = (SELECT max(trade_date) FROM idx_weight WHERE index_code = '000688.SH')
)
SELECT
  count()                                                   AS covered_names,
  round(sum(w.weight), 2)                                   AS covered_weight_pct,
  round(sum(w.weight * f.pe_ttm) / nullIf(sum(w.weight), 0), 2) AS pe_ttm_weighted,
  round(sum(w.weight * f.pb)     / nullIf(sum(w.weight), 0), 2) AS pb_weighted,
  (SELECT max(trade_date) FROM idx_weight WHERE index_code = '000688.SH') AS weight_date,
  (SELECT max(trade_date) FROM stk_factor_pro)                            AS price_date
FROM w
JOIN stk_factor_pro f ON f.ts_code = w.con_code
WHERE f.trade_date = (SELECT max(trade_date) FROM stk_factor_pro)
  AND f.pe_ttm IS NOT NULL AND f.pb IS NOT NULL;
```

输出必须同时给出 4 个数：`covered_names` / `covered_weight_pct` / `weight_date` / `price_date`。
`covered_weight_pct < 90` 时，结论句必须写「加权近似，覆盖 X% 权重，不等于指数官方估值」。
`idx_weight` 是月度快照，且 2026-10-29 实测 000688.SH 最新快照只有 19 只、权重和约 77.6%——**不得省略这句话**。

**第 3 级：明确拒答**

若第 2 级覆盖率过低（< 50%）或成分估值缺失，直接回答：
「库里没有该指数的估值数据，且成分股权重覆盖不足，无法给出可靠 PE/PB」，并给出可补数路径（`idx_dailybasic` 回补 / `fund_basic` 同步）。**不得用板块估值、同类指数估值或历史均值替代。**

**ETF 本身永远没有 PE/PB**：`fund_daily` 只有价格。问 ETF 估值 = 问其跟踪指数的估值（走上面三级），或问成分股加权。

---

## R4. 问题范围过大（覆盖多个指数 / 全市场）

触发条件：用户说「所有指数」「哪些指数便宜」「全市场估值」「行业排名」等未指定标的集合的问法。

处理：

1. **先定义宇宙**，不要隐式全表扫。默认规模指数宇宙：
   `000300.SH`、`000905.SH`、`000852.SH`、`000016.SH`、`000688.SH`、`399006.SZ`。
2. **报覆盖率**：其中有多少个能拿到估值（当前 `idx_dailybasic` 只有 12 个代码且已停更，实际可用数可能为 0）。
3. **按覆盖率决定动作**：
   - ≥ 80%：可以排名，但标注"宇宙 = 上述 N 个，截至 <日期>"；
   - 50%–80%：只给覆盖到的，逐条标注缺失项；
   - < 50%：拒绝排名，改为"列出可算的标的 + 说明为什么不能做全市场排名"。
4. **禁止**：静默抽样、用板块指数冒充标准指数（`idx_daily_dc` 是 BK 代码）、用 `idx_quote_dc` 的板块涨跌代表宽基指数。

行业类问题：`idx_sw_classify` 是**纯分类字典**（无日期、无涨跌幅），不能算行业涨幅；正确路径是 `idx_sw_member_all` 展开成分股 → `stk_factor_pro` 聚合。

---

## R5. 空结果的四分法（不要一句「没有数据」）

| 症状 | 判定 | 下一步 |
|---|---|---|
| 表存在但该代码 0 行 | **错表** | 按 R1 换表（最常见：ETF 查了 `stk_factor_pro`、指数查了 `stk_factor_pro`） |
| 代码在表里但区间内 0 行 | **区间无数据** | 放宽区间，或该标的当时未上市/已退市 |
| 表有数据但最新日期落后 | **停更** | 查 R5.1 水位；结论里标注日期，不得当"今日数据" |
| 表整体 0 行 | **未同步/被排除** | 查 `table_sync_state`；`stk_cyq_chips` 即此类 |

### R5.1 空结果必做的两次探测

```sql
-- 1) 这张表到底有没有这个标的 / 最新到哪天
SELECT count() AS n, max(trade_date) AS latest
FROM <table> WHERE ts_code = '<code>';

-- 2) 是"没同步"还是"同步失败"
SELECT target_table, dimension_type, dimension_value, is_sync, rows_written,
       substring(error_message, 1, 80) AS err, updated_at
FROM table_sync_state
WHERE target_table = '<table>'
ORDER BY updated_at DESC LIMIT 5;
```

判定：
- `n = 0` 且 `table_sync_state` **无任何记录** → 该表**没被纳入同步体系**（脱管），不是当天失败；
- `n > 0` 但 `latest` 远早于 `trade_calendar` 的应有交易日 → **停更**，按 R6 标注；
- `table_sync_state` 有 `is_sync=0` 且 `err` 非空 → **同步失败**，引用错误原文。

---

## R6. 过期数据必须显式标注

任何查询结果，只要 `max(trade_date)` 落后于「应有交易日」超过该表 SLA，结论第一行必须写：

> 数据截至 `<max(trade_date)>`（落后 `<N>` 个交易日，超过 SLA `<S>` 天）

「应有交易日」用 `trade_calendar`：

```sql
SELECT max(cal_date)
FROM trade_calendar
WHERE is_open = 1 AND cal_date <= today();
```

**禁止**：把 2025-07-04 的指数估值说成"当前估值"；把 2026-07-22 的北向数据说成"最新"。

---

## R7. 列名与单位：跨表不要照抄

| 语义 | 个股表（`stk_*`） | 指数/板块表（`idx_*`） |
|---|---|---|
| 涨跌幅 | `pct_chg` | `pct_change` |
| 成交量 | `vol`（手） | `vol`（手） |
| 成交额 | `amount`（千元） | `amount`（千元） |

- `stk_factor_pro.close` **不复权**，`close_hfq` 后复权；`idx_factor_pro` / `fund_daily` **只有不复权 close**。
- 因此：指数/ETF 的多年收益只能表述为**价格收益**；要全收益必须叠加分红事件（`stk_dividend`，仅个股）。
- 单位未登记的列，先看 `tables/<table>.md` 的列注释；仍无注释时，**在结论里写"单位未登记"**，不要默认。

---

## R8. 禁止事项（硬性）

1. 禁止编造数据、用相邻日期/相邻标的替代、把近似值写成官方值。
2. 禁止静默换源：从 ClickHouse 换到网络源必须在结论里写明「数据源 = X，原因 = Y」。
3. 禁止用 `idx_daily_dc` / `idx_quote_dc`（BK 板块）代表标准宽基指数。
4. 禁止用 `stk_top_inst` 声称"全部席位"（只有每边前 5 名）。
5. 禁止把 `stk_moneyflow_hsgt.north_money`（2024-08-19 后为成交额）当净流入。
6. 禁止在 `stk_cyq_chips`（0 行）上做任何分析。
7. 禁止跨表照抄 `pct_chg` / `pct_change`。