---
name: ashare-events
description: |
  A 股上市公司临时公告列表与公告正文检索（新浪财经）。
  Use when: 公告、临时公告、问询函、停牌、重组、减持、业绩预告、诉讼
  不适用：监管处罚汇总（用 `ashare-risk`）、定期报告全文（用 `ashare-filings`）。
argument-hint: "股票代码 时间范围"
user-invocable: true
---

# 临时公告（ashare-events）

## 何时用

需要看某只 A 股"最近发生了什么"时：重组、停牌、减持、业绩预告、问询函、诉讼、审计意见、
会计差错更正、会计师事务所变更。先列公告标题判断事件类型，再对单条公告取正文核对细节。

## 命令示例

列公告（默认当年 1 月 1 日到今天，最多 50 条）：

```bash
python /home/opencode/.config/opencode/skills/ashare-events/scripts/list_announcements.py --code 300750 --limit 20
```

限定区间与类型（`--types` 逗号分隔：inquiry,audit,accountant_change,litigation,penalty,restatement,other）：

```bash
python /home/opencode/.config/opencode/skills/ashare-events/scripts/list_announcements.py --code 600519.SH --start 2025-01-01 --end 2025-12-31 --types inquiry,penalty,litigation
```

取正文（`--url` 用上一步返回的 `source_url`，必须加引号）：

```bash
python /home/opencode/.config/opencode/skills/ashare-events/scripts/announcement_text.py --url "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=300750&id=12651094" --grep "分红|回购|减值" --max-chars 4000
```

正文过长时先定位再分段，或落盘后续读：

```bash
python /home/opencode/.config/opencode/skills/ashare-events/scripts/announcement_text.py --url "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=300750&id=12651094" --out /tmp/ann.txt --max-chars 2000
python /home/opencode/.config/opencode/skills/ashare-events/scripts/announcement_text.py --url "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=300750&id=12651094" --grep "募集资金" --max-chars 8000
```

## 输出字段

`list_announcements.py`：`available`、`source`、`ts_code`、`count`、
`announcements[]` 每项 `ann_date`（YYYY-MM-DD）、`title`、`ann_type`（六类或 other）、`source_url`。

`announcement_text.py`：`available`、`source`、`source_url`、`chars`、`truncated`、`text`；
带 `--grep` 时另有 `excerpts[]`（`offset` + `excerpt`）与 `matched`；带 `--out` 时另有 `path`
（文件保存完整正文，stdout 的 `text` 仍受 `--max-chars` 限制）。

`ann_type` 由标题关键词判定，只是分诊信号，不是交易所正式分类；结论以正文为准。
判定关键词（标题命中即归类，按顺序取第一个命中）：

| ann_type | 关键词 |
|---|---|
| `inquiry` | 问询函、监管工作函、问询回复、关注函、问询函回复 |
| `audit` | 审计报告、审计意见、关键审计事项、非标准审计、保留意见、无法表示意见、鉴证报告 |
| `accountant_change` | 会计师事务所变更、更换会计师、续聘会计师、聘请会计师 |
| `litigation` | 诉讼、仲裁、法律纠纷 |
| `penalty` | 行政处罚、纪律处分、监管处罚、监管措施、立案调查、立案告知、被立案、警示函、通报批评、告知书 |
| `restatement` | 差错更正、财报重述、追溯调整、前期会计差错、前期差错更正、前期财务 |
| `other` | 以上都不命中 |

上表就是全部判定规则，**不要为了确认分类逻辑去读脚本源码**。需要全量分诊时用一次大 `--limit` 拉取后在本地过滤，不要用多组 `--types` 反复拉取。
公告日期来自列表页文本节点，个别条目缺失时 `ann_date` 为空字符串。

## 失败处理

- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- 遇到不可达时：换 `--start/--end` 缩短区间重试一次；仍失败则改用 `get_cn_announcements`（vibe-trading MCP）或交易所官网，并在结论中注明来源不可达。
- 参数错误（代码不是 6 位、日期格式错、未知 `--types`、URL 域名不在白名单）退出码 2 并写 stderr，此时不会输出 JSON，不要重试同一命令。

## 全文分片

公告正文动辄数万字，**必须**用 `--grep` 定位或用 `--max-chars` 分片，禁止把整篇正文直接读进上下文：
先用 `--grep` 拿到命中片段，再对需要的片段用 `--out` 落盘后按行区间读取。
`truncated: true` 说明 stdout 被截断，不代表正文只有这么多；需要全文请加 `--out`。