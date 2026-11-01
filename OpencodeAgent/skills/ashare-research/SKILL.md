---
name: ashare-research
description: |
  从新浪财经抓取券商行业研报列表与正文，可按个股所属申万二级行业自动路由或指定行业名，并支持标题关键词过滤。
  Use when: 研报、券商研报、行业研报、评级、目标价、盈利预测
argument-hint: "股票代码 或 行业名 + 关键词"
user-invocable: true
---

# A 股券商行业研报

## 何时用

- 查某只股票所属行业的券商研报，或按行业名（申万二级）检索行业研报。
- 需要单篇研报正文，定位盈利预测、评级、目标价、风险提示等段落。
- 不适用：公司公告、调研纪要、业绩说明会 → 用 `ashare-ir`；临时公告 → 用公告类 skill。

## 命令示例（逐字可复制）

按个股自动路由到其申万二级行业：默认近 365 天、最多 50 条。

```bash
python /home/opencode/.config/opencode/skills/ashare-research/scripts/industry_reports.py --code 300750 --limit 10
```

按行业名检索（`--industry` 必须是申万二级行业名），并用关键词收窄：

```bash
python /home/opencode/.config/opencode/skills/ashare-research/scripts/industry_reports.py --industry 电池 --keywords 固态,储能 --days 180 --limit 20
```

研报正文（必须分片：先 `--grep` 定位，或 `--max-chars` 截断）：

```bash
python /home/opencode/.config/opencode/skills/ashare-research/scripts/report_text.py --url "https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/846524348599/index.phtml" --grep "盈利预测|目标价|评级" --max-chars 12000
```

把完整正文落盘，再按需读取：

```bash
python /home/opencode/.config/opencode/skills/ashare-research/scripts/report_text.py --url "https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/846524348599/index.phtml" --max-chars 20000 --out /tmp/report.txt
```

## 输出字段

`industry_reports.py`：

- `available` / `source` / `count`
- `industry_sw_l2`：`--industry` 时为规范化行业名；`--code` 时为解析出的申万二级代码（个股页面不提供行业名）
- `reports[]`：`report_date`、`publisher`、`title`、`industry_tags`、`source_url`、`summary`（列表页不提供，通常为空串）

`report_text.py`：

- `available` / `source` / `source_url` / `chars` / `truncated` / `text`
- `text` 截断到 `--max-chars` 字；带 `--grep` 时仍返回，可能为空串
- 传 `--grep` 时追加 `excerpts[]`（每项含 `offset` 与 `excerpt`）和 `matched`
- 传 `--out` 时追加 `path`，该文件保存未截断的完整正文

## 参数与校验

- `--code` 与 `--industry` 二选一且必填；同时给或都不给 → 退出码 2。
- `--code` 接受 `300750` / `300750.SZ` / `600519.SH`；非 6 位数字或非法后缀 → 退出码 2。
- `--industry` 只接受申万二级行业名（如 `电池`、`汽车零部件`、`光伏设备`），未知名称 → 退出码 2。
- `--keywords` 逗号分隔，标题任一命中即保留。
- `--days` 默认 365；`--limit` 1–200，默认 50。
- `--url` 只允许 `vip.stock.finance.sina.com.cn` / `finance.sina.com.cn`；其它域名 → 退出码 2。

## 失败处理

- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- `available: true` 且 `reports: []` 才是“该区间内没有匹配研报”。
- 参数错误不是 JSON：退出码 2，原因写 stderr。

## 分片纪律

- 研报正文常含大量图表转写文本；不要直接拉全文进上下文。
- 先 `--grep` 拿命中窗口（盈利预测、目标价、评级、风险提示），再决定是否放大 `--max-chars`。
- 需要全文时用 `--out` 落盘，避免把整篇正文塞进对话。