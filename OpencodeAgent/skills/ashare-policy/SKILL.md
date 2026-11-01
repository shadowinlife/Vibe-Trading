---
name: ashare-policy
description: |
  基于 gov.cn 政策文件库站内检索（无需 API key）查询中央部委行业政策，标题命中优先、按发布时间倒序返回最近文件。
  Use when: 政策、行业政策、部委文件、工信部、发改委、补贴、规划
argument-hint: "行业名 时间范围"
user-invocable: true
---

# 行业政策检索（gov.cn）

用 gov.cn 政策文件库的公开检索接口，按行业名找中央部委最近发布的政策文件，不需要任何 API key。

## 何时用

- 查某行业的产业政策、补贴、规划、规范、部委通知
- 需要政策原文链接与发文机关，用于研究或基本面判断
- 不适用：地方政策、征求意见稿、法规条文语义问答

## 命令

```bash
python /home/opencode/.config/opencode/skills/ashare-policy/scripts/industry_policies.py \
  --industry 光伏 --days 365 --limit 5
```

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--industry` | 是 | — | 行业名，如 光伏、新能源、汽车零部件 |
| `--days` | 否 | 365 | 从今天回溯的天数 |
| `--limit` | 否 | 5 | 返回条数，硬上限 5 |

## 输出

stdout 输出单个 JSON 对象：

| 字段 | 说明 |
|---|---|
| `available` | 检索源是否可用 |
| `source` | 固定为 `gov.cn` |
| `industry` / `window_days` | 回显参数 |
| `count` | 返回政策条数 |
| `policies[]` | `publish_date`（YYYY-MM-DD）、`title`、`issuer`、`level`（central/ministry/local）、`source_url`、`summary` |
| `coverage_note` | 仅空结果时出现 |
| `endpoint` | 实际使用的检索接口 |

排序：标题命中优先，其次按发布时间倒序。

## 失败处理

- `available:false` 表示数据源不可达（或列表页可达但无匹配），**不得当作「没有披露 / 没有记录」**；进程仍以退出码 0 结束。
- 参数非法（含 `--industry ""`）：stderr 报错，退出码 2。
- 网络不可达时退回 VT `web_search`，并在结论中注明覆盖有限。
- 检索成功但窗口内无结果：`available: true`、`policies: []`，看 `coverage_note`；可放宽 `--days`。

## 覆盖局限

- gov.cn 政策文件库只覆盖**中央部委公开发布**，不含地方政策与征求意见稿。
- 检索是关键词匹配而非语义检索；空格在 gov.cn 侧按分词 OR 处理，本工具只保留行业名连续出现在标题或摘要中的文件，但仍可能包含仅在正文提及该行业的文件。
- 收录有滞后；空结果不等于没有政策。