---
title: A 股外部证据取数 skills（F13）
description: 六类 A 股公开信息取数（定期报告全文 / 临时公告 / 监管处罚 / 机构调研 / 券商研报 / gov.cn 政策）由「MCP 工具」改为「script-backed skills」；skills 装到全局配置目录而非工作区，因此工作区挂载无法抹掉能力。含契约归一、命中窗口合并与检索预算的实测依据。触发词：F13、ashare、外部检索、外部证据、年报全文、公告、问询函、监管处罚、机构调研、研报、gov.cn、policy、skills、nano-search、检索预算、过度探索。
type: reference
status: active
created: 2026-10-31
updated: 2026-10-31
tags: [feature, skills, retrieval, opencode, ashare]
related: [f7-opencode-agent.md, ../branch/MYMAIN_DIVERGENCE.md]
---

# F13 — A 股外部证据取数 skills

## 结论

六类 A 股公开信息从「一个从未接线的 MCP 服务」改为「6 个 skill + 9 个纯标准库脚本」：常驻工具 schema 从 12 个（约 3.5k tokens/请求）降到 6 条 skill description（约 300 tokens），并且全文类资料可以用 `--grep` / `--max-chars` 分片读取，而不是把 20 万字的年报灌进上下文。

改造前 `AGENTS.md` 把这六类写成「已知缺口」并要求模型「在结论中注明覆盖有限」——**文档本身在关闭能力**：模型不会去尝试一个它被告知不存在的工具。

## 背景：三个被同时搞错的事实

1. **那个 MCP 从未接线，不是「验证不够有效而下线」。** `nano-search-mcp` 存在于私有仓 `vibetrading-opencode-instruct`，其 12 个工具里 9 个是零凭证的 HTML 抓取，只有 3 个依赖 DashScope。
2. **公告与研报在 VT 树里本就有工具。** `get_cn_announcements` / `search_broker_reports`（`agent/src/tools/gildata_research_tools.py`）已注册，只是 fail-closed 于 `GILDATA_TOKEN`，而 `OpencodeAgent/.env.example` 没有这一项——「未配置」被写成了「不存在」。
3. **容器本来就挂着 4 个 OMO 自带 MCP**（`websearch` / `context7` / `grep_app` / `lsp`），搜索路径不止一条。因此本 feature 删除 nano-search 的 `search`、`search_deferred_topic` 两个工具，只补结构化取数，不新增第二套通用搜索。

## 设计

| skill | 脚本 | 覆盖 |
|---|---|---|
| `ashare-filings` | `company_report.py` | 年报/半年报/一季报/三季报全文 |
| `ashare-events` | `list_announcements.py`、`announcement_text.py` | 临时公告列表与正文 |
| `ashare-risk` | `penalties.py` | 监管处罚 / 违规处理 |
| `ashare-ir` | `ir_list.py`、`ir_text.py` | 机构调研、业绩说明会、参会机构 |
| `ashare-research` | `industry_reports.py`、`report_text.py` | 券商行业研报列表与正文 |
| `ashare-policy` | `industry_policies.py` | gov.cn 政策文件库站内检索（零 key） |

共享库 `skills/_lib/nanofetch/`：统一 UA / 退避重试、域名白名单、HTML 扁平化、命中窗口提取、结果信封。`_lib` 无 `SKILL.md`，OMO 不会当成 skill 加载。

### 位置：装到全局，不装进工作区

`skills/` 装到 `/home/opencode/.config/opencode/skills/`，**不是** `/workspace/.opencode/skills/`。两条依据：

- OMO 5.1.35 `getOpenCodeSkillDirs()` 从 `[OPENCODE_CONFIG_DIR, ${XDG_CONFIG_HOME:-~/.config}/opencode]` 各自 `join("skills")` 与 `join("skill")`；容器内这两个 env 均为空，全局根即 `/home/opencode/.config/opencode/skills`。
- 作用域优先级 `project(4) > user(3) > opencode(2)`：**保留一份项目副本会反过来遮蔽全局副本**，并在挂载时一起消失。所以是替换而不是新增。

由此 `/workspace` 允许被挂载而不影响能力。**不做 entrypoint 兜底**：挂载把 `AGENTS.md` 或 `scripts/` 遮掉属于部署配置错误，entrypoint 只打印 `[entrypoint] skills root=... loaded=N` 诊断行，不回填、不阻断。

### 契约

- **失败**：数据源不可达或页面结构变化 → stdout `{"available": false, "reason": ...}` + **exit 0**；只有参数非法才 exit 2 + stderr。沿用 `workspace/scripts/AGENTS.md` 的既有惯例，不另立第二套。
- **输出**：全文类统一 `available / source / chars / truncated / text`，带 `--grep` 时追加 `excerpts[]`（`offset` + `excerpt`）与 `matched`，带 `--out` 时追加 `path`（文件存完整正文）。列表类统一 `available / source / count / <domain>[]`，`ts_code` 一律带交易所后缀。

## 关键文件与开关

| 路径 | 说明 |
|---|---|
| `OpencodeAgent/skills/ashare-*/` | 6 个 skill 与 9 个脚本 |
| `OpencodeAgent/skills/_lib/nanofetch/` | 共享 HTTP / 文本 / 信封 |
| `OpencodeAgent/Dockerfile` | `COPY skills/ /home/opencode/.config/opencode/skills/`（含 `--chown`） |
| `OpencodeAgent/Dockerfile.base` | `beautifulsoup4` 显式入 pip 列表 |
| `OpencodeAgent/AGENTS.md` | 46–50 行「外部证据取数」映射 + 检索预算 |
| `OpencodeAgent/entrypoint.sh` | skills 根与加载数诊断行 |
| `OpencodeAgent/tests/test_nano_skills.py` | CLI 契约 / 白名单 / 窗口合并（10 例，离线） |
| `OpencodeAgent/tests/test_agents_md.py` | 6 个 skill 存在且被 AGENTS.md 路由 |

无环境变量开关；`ashare-policy` 若 gov.cn 检索不可达会返回 `available:false`，不依赖任何 API key。

## 验证

| 项 | 结果 |
|---|---|
| 单元/契约 | `pytest OpencodeAgent/tests/` **209 passed / 1 skipped** |
| 强制路径会话 | `skill(ashare-filings)` → `bash` ×2，24.1s；答案引用年报「主营业务分析」原文，储能营收 57,290,460 千元、毛利率 26.84% |
| 自然提问路由（不提示） | 「有没有问询函/诉讼公告」→ 只加载 `ashare-events`；「有没有被监管处罚」→ 只加载 `ashare-risk`。2/2 自动路由成功 |
| 挂载免疫 | 用空 volume 遮蔽 `/workspace/.opencode`，`GET /skill` 仍返回 16 个技能、6 个 `ashare-*` 全在 |
| 镜像内执行 | `company_report.py` 在镜像 `/opt/venv` 下返回 202,639 字正文，窗口上限 958 字符 |

附带发现：`/workspace` **整体**挂载时容器根本起不来（`ENTRYPOINT ["/workspace/entrypoint.sh"]`，exit 127），所以真实威胁只有子路径遮蔽——而本 feature 已使其与能力无关。

## 实测踩过的坑

1. **四份脚本四种契约**：四个子任务并行产出 `hits`/`grep`/`excerpts`、`hit_count`/`match_count`/`matched`、`full_text`/`text` 三套命名。已归一，并把纪律句固定为每个 SKILL 恰好出现一次。
2. **命中窗口重叠**：`--grep 储能` 因释义段落密集命中，返回 3 份几乎相同的片段；合并后单窗口曾达 5123 字符。现按 `context * 4` 封顶合并（实测最大 958）。
3. **模型读脚本源码**：13 次 bash 里有 2 次是 `sed -n` 读 `list_announcements.py`，因为 SKILL 只写「`ann_type` 由标题关键词判定」而没给规则。补关键词表后该行为消失。
4. **过度探索**：单个提问曾用 13 次 bash、加载 3 个 skill、跑与问题无关的 `company_report` 与 `penalties`。处置见下节。

## 检索预算（唯一一处）

`AGENTS.md` 外部证据取数节写死一条：先窄后宽、同一问题最多 3 次脚本调用、第 2 次必须收窄而非重跑、不串联多个 `ashare-*` 做「交叉验证」。同时给 3 个重叠 skill 的 `description` 加「不适用」反向边界。

实测（同一提问，改前 → 改后）：问询函/诉讼 **13 → 4** 次 bash、skill 2 → 1；监管处罚 **6 → 5** 次、skill 3 → 1。答案质量未下降，处罚一例还逐条区分了「处罚对象是自然人/分析师/发行人本身」。

## 状态与上游关系

- 个人部署独有，**不回流上游**（依赖本分支的 OpenCode harness 与 ClickHouse 语义层）。
- 关闭 [DIVERGENCE §4.6](../branch/MYMAIN_DIVERGENCE.md) 的 **R2「A 股结构化检索替换引擎」**：五类缺口全部由零 key 脚本承接，gov.cn 政策亦已打通站内检索。
- 已知局限：`ashare-policy` 只覆盖中央部委公开发布，不含地方政策与征求意见稿；`ashare-ir` 列表页 `participants` 恒为空（正文里才有）；`ashare-events` 的 `ann_type` 是标题关键词分诊，不是交易所正式分类。