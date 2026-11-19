---
name: ashare-ir
description: |
  从新浪财经抓取 A 股投资者关系活动记录（机构调研、业绩说明会）列表与纪要正文，并从正文抽取参会机构。
  Use when: 机构调研、投资者关系、业绩说明会、调研纪要、参会机构
argument-hint: "股票代码 时间范围"
user-invocable: true
---

# A 股投资者关系（IR）调研纪要

## 何时用

- 查某只股票近半年有哪些机构调研、业绩说明会、投资者说明会。
- 需要单条调研纪要原文，或从正文里抽取参会机构名单。
- 不适用：券商研报、评级、目标价 → 用 `ashare-research`；临时公告 → 用公告类 skill。

## 命令示例（逐字可复制）

列表：默认近 6 个月，最多 50 条，按日期倒序。

```bash
python /home/opencode/.config/opencode/skills/ashare-ir/scripts/ir_list.py --code 300750 --limit 20
```

按时间与会议类型过滤（`research|earnings_call|site_visit|other`）：

```bash
python /home/opencode/.config/opencode/skills/ashare-ir/scripts/ir_list.py --code 600519 --start 2026-01-01 --end 2026-06-30 --types earnings_call
```

纪要正文（必须分片：先 `--grep` 定位，或 `--max-chars` 截断）：

```bash
python /home/opencode/.config/opencode/skills/ashare-ir/scripts/ir_text.py --url "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=300750&id=12651293" --grep "参会|参与|调研" --max-chars 8000
```

把完整正文落盘，再按需读取：

```bash
python /home/opencode/.config/opencode/skills/ashare-ir/scripts/ir_text.py --url "http://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=300750&id=12651293" --max-chars 20000 --out /tmp/ir-300750.txt
```

## 输出字段

`ir_list.py`：

- `available` / `source` / `ts_code`（规范形式，带交易所后缀，如 `300750.SZ`）/ `count`
- `meetings[]`：`meeting_date`、`meeting_type`、`participants`（列表页恒为 `[]`，正文里才有）、`title`、`summary`（来源页不提供，恒为空串）、`source_url`

`ir_text.py`：

- `available` / `source` / `source_url` / `chars` / `truncated` / `text` / `participants[]`
- `text` 截断到 `--max-chars` 字；带 `--grep` 时仍返回，可能为空串
- 传 `--grep` 时追加 `excerpts[]`（每项含 `offset` 与 `excerpt`）和 `matched`
- 传 `--out` 时追加 `path`，该文件保存未截断的完整正文

`meeting_type` 由标题关键词判定：业绩说明会/电话会→`earnings_call`，实地调研/现场参观→`site_visit`，其余调研类→`research`，兜底→`other`。

## 参数与校验

- `--code` 必填，接受 `300750` 或 `300750.SZ`；非 6 位数字 → 退出码 2。
- `--start` / `--end` 为 `YYYY-MM-DD`，默认 `end - 182 天` 到今日。
- `--types` 逗号分隔；非法值 → 退出码 2 + stderr。
- `--limit` 1–200，默认 50。
- `--url` 只允许 `vip.stock.finance.sina.com.cn` / `finance.sina.com.cn`；其它域名 → 退出码 2。

## 失败处理

- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- `available: true` 且 `meetings: []` 才是“该时间区间内没有 IR 活动”。
- 参数错误不是 JSON：退出码 2，原因写 stderr。

## 分片纪律

- 纪要正文通常几千到数万字；不要直接拉全文进上下文。
- 先 `--grep` 拿命中窗口，确认相关后再放大 `--max-chars`。
- 需要全文时用 `--out` 落盘，避免把整篇正文塞进对话。