---
name: ashare-filings
description: |
  抓取 A 股上市公司指定年份的定期报告全文（年报/半年报/一季报/三季报），可按关键词返回上下文片段。
  Use when: 年报、半年报、季报、定期报告全文、附注、管理层讨论、会计政策
  不适用：临时公告列表（用 `ashare-events`）、券商研报（用 `ashare-research`）。
argument-hint: "股票代码 年份 报告类型"
user-invocable: true
---

# ashare-filings — A 股定期报告全文

## 何时用
- 读某家公司某一年定期报告的原文：主营业务、收入构成、财务报表附注、会计政策、管理层讨论、风险提示。
- 在原文里定位关键词并看上下文：`--grep 储能`、`--grep 毛利率`、`--grep 关联交易`。
- 不用于公告列表、券商研报、招股书；英文版与摘要会被跳过。`--report-type` 只接受 `annual` / `semi` / `q1` / `q3`。

## 命令示例（逐字可复制）

按关键词检索（推荐）：
```bash
python /home/opencode/.config/opencode/skills/ashare-filings/scripts/company_report.py --code 300750 --year 2024 --report-type annual --grep 储能 --max-chars 4000
```

读取开头一段（没有 `--grep` 时返回 `text`）：
```bash
python /home/opencode/.config/opencode/skills/ashare-filings/scripts/company_report.py --code 300750.SZ --year 2024 --report-type annual --max-chars 20000
```

把全文落盘，再按 `offset` 定位：
```bash
python /home/opencode/.config/opencode/skills/ashare-filings/scripts/company_report.py --code 300750 --year 2024 --report-type annual --grep 毛利率 --out /tmp/300750-2024.txt
```

## 输出字段说明
| 字段 | 含义 |
|---|---|
| `available` | `true` 才表示取到了报告正文 |
| `source` | 固定为 `sina` |
| `title` / `publish_date` / `url` | 报告标题、披露日期、新浪详情页 |
| `chars` | 正文总字数，不受 `--max-chars` 影响 |
| `truncated` | `text` 是否被 `--max-chars` 截断 |
| `text` | 截断到最多 `--max-chars` 字；带 `--grep` 时仍返回，可能为空串 |
| `excerpts` | 有 `--grep` 时返回 `[{offset, excerpt}]`，`offset` 是全文坐标 |
| `matched` | `--grep` 命中片段数（相邻命中已合并）；`0` 表示没搜到，不代表报告缺失 |
| `path` | 传了 `--out` 时，全文文件的路径（保存未截断全文） |

## 失败处理
- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- 需要交叉核实时改用交易所或巨潮等其他渠道。
- `--code` 不是 6 位数字（可带 `.SZ`/`.SH` 后缀）时退出码为 2，错误只写 stderr，stdout 没有 JSON。

## 读取纪律
- 正文动辄十几万字：**必须先 `--grep` 或 `--max-chars` 分片读取，禁止一次性把全文读入上下文**。
- `--grep` 返回的 `offset` 是全文坐标；需要继续读时，用 `--out` 保存的全文按偏移取用。