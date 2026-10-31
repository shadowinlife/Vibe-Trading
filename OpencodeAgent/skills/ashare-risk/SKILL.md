---
name: ashare-risk
description: |
  A 股上市公司监管处罚与违规处理记录检索（新浪财经违规处理专页）。
  Use when: 监管处罚、违规处理、立案调查、警示函、纪律处分、证监会
  不适用：常规公告检索（用 `ashare-events`）。
argument-hint: "股票代码 时间范围"
user-invocable: true
---

# 监管处罚（ashare-risk）

## 何时用

需要判断某只 A 股是否有监管污点时：证监会/地方证监局的行政处罚、警示函、立案调查，
交易所的纪律处分、通报批评、整改通知，以及处罚原因与处理结果。用于排雷与尽调，
不用于推断后续经营影响。

## 命令示例

```bash
python /home/opencode/.config/opencode/skills/ashare-risk/scripts/penalties.py --code 300750
```

限定时间区间（默认为不限，页面本身只给该股票的全部历史记录）：

```bash
python /home/opencode/.config/opencode/skills/ashare-risk/scripts/penalties.py --code 600519.SH --start 2020-01-01 --end 2025-12-31
```

## 输出字段

`available`、`source`、`ts_code`、`count`、`penalties[]`，每条含：

| 字段 | 含义 |
|---|---|
| `punish_date` | 公告日期 YYYY-MM-DD，缺失为空字符串 |
| `event_type` | 来源页面原文，如 `处罚决定` / `警示` / `立案调查` / `整改通知` / `问讯` |
| `title` | 违规事件标题（常含处罚决定书文号） |
| `reason` | 批复原因原文 |
| `content` | 批复内容原文（处理措施与金额） |
| `issuer` | 处理机构，如 `内蒙古证监局` / `上交所` |
| `source_url` | 违规处理列表页 URL，数据源不提供逐条详情页 |

`event_type` 是页面原文而非受控枚举，按关键词自行归类；`issuer` 优先归一到简称，
识别不到时保留原文。同一事件可能在页面出现多条（不同处理对象），按 `title` 去重前先看 `content`。
`source_url` 只指向列表页，报告里引用时应说明"来源为该公司违规处理汇总页"。

## 失败处理

- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- 遇到不可达时：重试一次；仍失败则改用 `get_cn_announcements`（vibe-trading MCP）检索处罚类公告，或直接查证监会/交易所官网，并在结论中注明"处罚汇总页不可达，结论仅覆盖已查公告"。
- 空数组 `penalties: []` 且 `available: true` 才表示该股票在此数据源下无记录。
- 参数错误（代码不是 6 位、日期格式错、`--start` 晚于 `--end`）退出码 2 并写 stderr，此时不会输出 JSON。
- 处罚记录只看公告日期，`punish_date` 缺失的条目不参与时间过滤，避免静默丢弃。

## 使用边界

处罚列表页是历史汇总，更新可能滞后于临时公告——重大处罚应同时用 `ashare-events` 的
`--types penalty` 交叉核对。本 skill 只给原始记录，合规判断与影响评估由分析环节完成。