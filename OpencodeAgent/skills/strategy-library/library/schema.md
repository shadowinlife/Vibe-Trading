# 条目规范（schema.md）

本文件是 vibetrading-strategy-library 的人读条目规范，字段定义以本文件为准。`library.py validate` 校验 frontmatter 字段、id 唯一性与前缀一致性、引用存在性；正文结构（第 8 节）是撰写约定，由 git 评审把关。所有条目必须同时满足：frontmatter 字段合法、正文结构完整、引用可解析。

## 1. 文件形态

每个条目是一个 Markdown 文件，由两部分组成：

```markdown
---
id: kn-0001
type: knowledge
title: 一句话标题
status: candidate
# 其余 frontmatter 字段
---

# 标题

正文，按 type 对应的结构书写。
```

- frontmatter 必须由首行的 `---` 开始，由单独一行的 `---` 结束，内容为合法 YAML。
- frontmatter 之后是 Markdown 正文，正文不可为空。
- 一个文件只放一个条目。

## 2. 目录与命名

| 目录 | type | id 前缀 | 文件名 |
| --- | --- | --- | --- |
| `knowledge/` | `knowledge` | `kn-` | `kn-XXXX-<slug>.md` |
| `strategies/` | `strategy` | `st-` | `st-XXXX-<slug>.md` |
| `indicators/` | `indicator` | `in-` | `in-XXXX-<slug>.md` |

- `XXXX` 为 4 位数字，例如 `0001`。
- id 形如 `kn-0001`，只含前缀与 4 位数字，不含 slug。
- 文件名格式为 `<id>-<slug>.md`，例如 `kn-0001-market-crowding-top-risk.md`；slug 建议使用小写字母、数字与连字符。
- id 前缀必须与所在目录一致，`type` 必须与所在目录一致。
- id 全库唯一，不得与任何已有条目重复。
- 编号只增不改。条目废弃时改状态或在正文注明，不要复用旧编号。

## 3. 公共必填字段

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `id` | string | 前缀与目录一致，`XXXX` 为 4 位数字，全库唯一 | `kn-0001` |
| `type` | enum | `knowledge` / `strategy` / `indicator` | `knowledge` |
| `title` | string | 非空，一句话说清条目内容 | `两融背离作为顶部预警的子信号` |
| `status` | enum | 6 值之一，见第 6 节 | `candidate` |
| `markets` | string[] | 非空，取值只能是 `ashare` / `hk` / `us` / `crypto` | `[ashare, hk]` |
| `sources` | object[] | 至少 1 条，每条含 `title` / `url` / `date` | 见下方 |
| `intended_use` | string | 非空，声明条目用于什么判断 | `判断 A 股指数在高位的回落风险` |
| `limitations` | string | 非空，声明不适用场景与已知缺陷 | `不适用于单边牛市早期；样本以大盘股为主` |
| `created` | date | `YYYY-MM-DD` | `2026-01-15` |
| `updated` | date | `YYYY-MM-DD` | `2026-01-20` |

`sources` 的每条记录：

| 子字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `title` | string | 非空，来源名称 | `Vibe-Trading 公开仓` |
| `url` | string | 必须以 `http://` 或 `https://` 开头 | `https://github.com/HKUDS/Vibe-Trading` |
| `date` | string | `YYYY` / `YYYY-MM` / `YYYY-MM-DD` 三种之一 | `2026-01` |

`intended_use` 与 `limitations` 沿用 VT `strategy_store/models.py` 的治理规则：必填且非空。缺失或空串的条目视为未注册，校验不通过。

## 4. 公共可选字段

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `asset_classes` | string[] | 自由标签，建议小写英文 | `[equity, index]` |
| `horizon` | string | 观察或持有周期 | `swing` |
| `indicators` | string[] | indicator 条目 id，必须存在于库中 | `[in-0001]` |
| `related` | string[] | 条目 id，必须存在于库中，可跨类型 | `[kn-0003]` |
| `combination_hints` | string | 与其他条目组合使用时的提示 | `与趋势条目同向时提高置信，冲突时并列呈现` |

`asset_classes` 与 `horizon` 不设闭集，写清含义即可。`indicators` 只接受 `type: indicator` 的条目 id；`related` 接受任意 type 的条目 id。

## 5. 按 type 的额外字段

### 5.1 strategy 必填

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `entry_rules` | string | 非空，多条规则用换行或分号分隔 | `收盘价站上 20 日均线且成交量高于 20 日均量时入场` |
| `exit_rules` | string | 非空，写清止盈、止损与失效退出 | `跌破 20 日均线或亏损达 2 倍 ATR 时离场` |
| `position_sizing` | string | 非空，写清单笔风险与仓位上限 | `单笔风险不超过总资金 1%，单标的仓位不超过 20%` |

### 5.2 strategy 可选

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `signal_definition` | string | 信号的形式化定义 | `ATR(14) 高于其 60 日均值的 1.5 倍` |
| `vt_artifact_id` | string | VT strategy_store 台账返回的 artifact 标识，原样填写 | `a1b2c3d4` |

`vt_artifact_id` 是跨库交叉引用，不复制台账内容。填写前确认该 artifact 在 VT 台账中存在。

### 5.3 indicator 必填

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `formula` | string | 非空，公式或文字定义 | `ATR(14) / close` |
| `data_source` | string | 非空，数据来源与字段口径 | `日线 OHLCV，复权收盘价` |
| `computation` | string | 非空，可复现的计算步骤 | `先算真实波幅 TR，再取 14 日 Wilder 均值，最后除以收盘价` |
| `unit` | string | 非空，量纲或单位 | `ratio` |

### 5.4 indicator 可选

| 字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `thresholds` | string 或 mapping | 阈值及对应解读 | `{high: 0.03, note: 高于 0.03 视为波动扩张}` |

## 6. 状态与 evidence

`status` 只能取以下 6 个值：

| 状态 | 含义 | 额外要求 |
| --- | --- | --- |
| `candidate` | 新录入，未评估 | 无 |
| `validated` | 已通过独立验证 | 必须附 `evidence`，至少 1 条 |
| `research_only` | 研究级，未过完整验证 | 引用时必须标注 |
| `rejected` | 验证未通过 | 不得作为决策依据 |
| `blocked_by_data` | 缺数据无法验证 | 必须写明缺什么数据 |
| `blocked_by_permission` | 缺权限无法验证 | 必须写明缺什么权限 |

`evidence` 仅 `validated` 必填，其余状态可选。结构如下：

| 子字段 | 类型 | 格式 | 示例 |
| --- | --- | --- | --- |
| `summary` | string | 非空，验证结论摘要 | `2015-2024 A 股样本，方向性与统计分离通过` |
| `artifact` | string | 证据位置：路径、链接或运行编号 | `reports/2026-01-margin-divergence.pdf` |
| `date` | date | `YYYY-MM-DD`，验证完成日期 | `2026-01-20` |

状态纪律：

- `validated` 缺少 `evidence`，或 evidence 条目缺字段，校验不通过。
- `blocked_by_data` 与 `blocked_by_permission` 的缺口说明写在 `limitations` 或正文中，不能只在状态里体现。
- 验证流水线当前挂起。新条目只能写 `candidate` 或 `research_only`，不得凭空写 `validated`。

## 7. 引用完整性

- `id` 全库唯一，重复即校验失败。
- `indicators` 里的每个 id 必须存在于库中，且按约定应指向 `type: indicator` 的条目（校验器检查存在性与 id 格式，类型匹配由评审把关）。
- `related` 里的每个 id 必须对应库中任意类型的条目。
- 引用不存在的 id 时校验失败，不允许留下悬空引用。
- `vt_artifact_id` 指向 VT 台账，不在本库校验范围内。

## 8. 正文结构

正文用二级标题（`##`）分节，各节内容不可为空。标题文字按 type 固定：

| type | 必需章节 |
| --- | --- |
| `knowledge` | `## 主张` / `## 机制（为什么）` / `## 用法` / `## 失效情形` |
| `strategy` | `## 规则详述` / `## 适用场景` / `## 风险` |
| `indicator` | `## 口径说明` / `## 计算步骤` / `## 解读注意` |

各节写什么：

- 主张：一句话说清可检验的判断。
- 机制（为什么）：写清因果链条与前提条件。
- 用法：在哪个判断环节使用，需要哪些配套数据，与哪些条目组合。
- 失效情形：什么市场、周期、数据条件下不成立，给出反例。
- 规则详述：把 `entry_rules`、`exit_rules`、`position_sizing` 展开成可执行的规则。
- 适用场景：市场、品种、周期与流动性条件。
- 风险：模型风险、数据风险、执行风险与极端情形。
- 口径说明：指标定义、数据来源、复权与缺失值处理。
- 计算步骤：可复现的步骤，参数写明默认值。
- 解读注意：阈值的方向性、滞后性、共线性与误用方式。

## 9. 完整示例（knowledge）

以下为格式示例，id 使用保留号段 `kn-9999`（示例文件名为 `kn-9999-example-only.md`），仅用于演示字段与正文骨架，不写入 `knowledge/` 目录，也不构成任何研究结论。

```markdown
---
id: kn-9999
type: knowledge
title: 示例条目：波动扩张期的仓位收缩
status: candidate
markets:
  - ashare
  - hk
asset_classes:
  - equity
horizon: swing
sources:
  - title: 示例引用，仅演示来源字段格式
    url: https://github.com/shadowinlife/vibetrading-opencode-instruct/blob/main/skills/escape-top-microstructure/SKILL.md
    date: 2026-01
intended_use: 演示 knowledge 条目的完整字段与正文骨架，不作为研究结论使用。
limitations: 示例条目，主张与机制均为格式示意，不构成投资依据，不得入库。
combination_hints: 与同主题条目组合时先看状态再看结论，candidate 只能作为线索。
created: 2026-01-15
updated: 2026-01-15
---

# 示例条目：波动扩张期的仓位收缩

本条目为格式示例，正文内容仅示意写法。

## 主张

波动率快速抬升时，顺势仓位的胜率下降，应同步收缩仓位。

## 机制（为什么）

波动扩张通常伴随止损距离变大与跳空风险上升。同样的仓位在波动扩张期承担的实际风险更高，因此需要按波动率反向调整仓位。

## 用法

用于仓位与风控环节。先确认波动率处于扩张状态，再按目标波动率折算仓位上限。配套数据为日线 OHLCV 与已实现的波动率序列。

## 失效情形

在低波动、强趋势且流动性充足的市场中，机械收缩仓位会显著降低收益。样本不足以覆盖极端跳空时，不应单独依赖本条做决策。
```

若要给示例补上 `indicators` 或 `related`，其中的 id 必须已在库中存在，否则 `validate` 会因悬空引用失败。

## 10. 校验与入库

```bash
python3 library.py validate                 # 全库校验
python3 library.py register drafts/kn-0101-new-idea.md   # 单文件校验 + id 冲突检查
```

`register` 只回答"这个文件能不能入库"，不移动文件。通过后提交 PR 或走 git 评审，合并即入库。状态流转属于验证阶段，与评审分离。
