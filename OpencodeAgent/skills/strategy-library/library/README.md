# vibetrading-strategy-library · 公共策略库

jetwind 产品的公共策略库正典（canon）。本库把可复用的投研经验沉淀为三类结构化条目：先验知识（knowledge）、策略（strategy）、指标（indicator），并提供一套录入校验 CLI 与面向镜像 coding agent 的引导技能。

公开仓：https://github.com/shadowinlife/vibetrading-strategy-library

本库的定位：

- 正典，不是数据库。条目是经过评审的判断框架与经验记录，不是行情接口，也不是策略运行时。
- 只收录可解释、可追溯、有明确适用边界的条目。每条都必须声明用途与局限。
- 供人和 agent 共同消费。人读正文，agent 读 frontmatter 与 index.json。

## 与 Vibe-Trading 的关系

量化策略台账复用 Vibe-Trading（VT）现有的 `strategy_store`（SDM MCP 工具），本库不另造策略数据库。策略条目通过可选字段 `vt_artifact_id` 交叉引用台账中的 artifact：本库负责知识索引、判断框架与引用纪律，台账负责策略版本、回测记录与运行状态。

VT 公开仓：https://github.com/HKUDS/Vibe-Trading

治理规则沿用 VT `strategy_store/models.py`：`intended_use` 与 `limitations` 必填且非空。没有声明用途与局限的条目视为未注册，不能进入索引。

## 目录结构

```text
vibetrading-strategy-library/
├── README.md
├── LICENSE
├── schema.md                  # 条目规范，字段定义以它为准
├── library.py                 # 录入校验 CLI
├── index.json                 # 由 index 命令生成，不入人工编辑
├── knowledge/                 # kn-XXXX-<slug>.md
├── strategies/                # st-XXXX-<slug>.md
├── indicators/                # in-XXXX-<slug>.md
├── skills/
│   └── strategy-library/
│       └── SKILL.md           # 引导技能：回答前先查库
├── docs/
│   └── agents-patch.md        # 可直接插入镜像 AGENTS.md 的章节
└── tests/                     # 校验器与 CLI 的单元测试
```

三类条目的编号前缀固定：`knowledge/` 用 `kn-`，`strategies/` 用 `st-`，`indicators/` 用 `in-`，`XXXX` 为 4 位数字。id 形如 `kn-0001`，只含前缀与 4 位数字；文件名在 id 后追加 slug，例如 `kn-0001-market-crowding-top-risk.md`。id 全库唯一。

## 验证状态

状态只有 6 个取值，引用纪律随状态而定。

| 状态 | 含义 | 引用纪律 |
| --- | --- | --- |
| `candidate` | 新录入，未评估 | 必须标注"未验证，仅供参考"，只能作为线索 |
| `validated` | 已通过独立验证 | 可作为证据引用，必须附 evidence（≥1 条，含 summary/artifact/date） |
| `research_only` | 研究级，未过完整验证 | 引用必须标注"未验证，仅供参考" |
| `rejected` | 验证未通过 | 不得作为决策依据，只能用于说明失败经验 |
| `blocked_by_data` | 缺数据无法验证 | 必须说明缺什么数据，不能当成否定结论 |
| `blocked_by_permission` | 缺权限无法验证 | 必须说明缺什么权限，不能当成否定结论 |

当前约束：验证流水线挂起。新种子条目一律为 `candidate` 或 `research_only`，不得声称做过新的验证。唯一例外：引用 escape-top 已验证信号时，可注明"其子信号两融背离(margin_divergence)/波动扩张(volatility_atr_expansion)已在镜像仓通过 7 门验证"。除此之外不加任何已验证表述。

escape-top 参考（镜像仓公开技能文件）：https://github.com/shadowinlife/vibetrading-opencode-instruct/blob/main/skills/escape-top-microstructure/SKILL.md

## 条目生命周期

```text
起草 candidate
  → python3 library.py register <file>   # 字段校验 + id 冲突检查
  → git 评审入库
  → 独立验证阶段（当前挂起）
  → validated / rejected / blocked_by_data / blocked_by_permission
```

`research_only` 用于有研究价值但未过完整验证的条目，可在任意阶段使用，引用时必须标注。状态流转只在验证阶段发生，评审合并本身不改变状态。完整字段与示例见 [schema.md](schema.md)。

## 安装

依赖 python3 与 PyYAML：

```bash
python3 -m pip install --user pyyaml
```

## CLI 用法

所有命令在仓库根目录执行。

### validate

```bash
python3 library.py validate
```

扫描三个条目目录的全部文件，逐条校验字段、id 全库唯一性，以及 `indicators` 与 `related` 的引用是否存在。输出报告；任何违规均以非 0 退出码结束。

### index

```bash
python3 library.py index
```

在校验通过后生成 `index.json`，每个条目记录 `id`、`type`、`title`、`status`、`markets`、`path`、`keywords`。校验不通过时不生成索引。

### search

```bash
python3 library.py search 拥挤 波动
python3 library.py search margin divergence
```

对 `index.json` 的关键词做匹配：中文按子串匹配，英文按分词匹配，大小写不敏感；多个词之间为 AND，全部命中才返回。输出 id、标题、状态与路径。若 `index.json` 缺失或条目已变动，先运行 `python3 library.py index`。

### show

```bash
python3 library.py show kn-0001
```

打印条目全文（frontmatter 与正文）。

### register

```bash
python3 library.py register drafts/kn-0101-new-idea.md
```

校验任意路径下的候选条目，并做 id 冲突检查，输出是否可以入库。该命令不移动文件：入库一律走 git 评审。

## 运行测试

在仓库根目录运行，优先使用镜像内的现成环境：

```bash
/opt/vibe-trading/venv/bin/python -m unittest discover -s tests -v
```

若该 venv 不存在：

```bash
python3 -m pip install --user pyyaml
python3 -m unittest discover -s tests -v
```

## 贡献流程

1. 按 [schema.md](schema.md) 起草条目，状态写 `candidate`，`intended_use` 与 `limitations` 必须写实。
2. 本地运行 `python3 library.py register <file>`，按报告修到可入库。
3. 提交 PR 或走 git 评审，评审关注来源真实性、适用边界与引用完整性。
4. 合并入库。验证是独立阶段，当前挂起，合并不会把条目变成 `validated`。
5. 验证恢复后，按验证结论流转状态并补 evidence。

## 引导技能与镜像集成

- [skills/strategy-library/SKILL.md](skills/strategy-library/SKILL.md)：镜像 agent 的查库协议，回答任何金融分析或投资类问题前先检索本库。
- [docs/agents-patch.md](docs/agents-patch.md)：可直接插入镜像 AGENTS.md 的强制章节（路由义务、库位置、分工）；完整组合协议与引用纪律在 SKILL.md，补丁不重复。

库位置解析顺序：① 环境变量 `STRATEGY_LIBRARY_PATH` 指向的目录；② 技能包自带的 `library/` 子目录（把本库整体放入 `skills/strategy-library/library/` 即可随技能分发，镜像 delta 构建友好）；③ 镜像约定路径 `/opt/strategy-library`。

## 诚实声明

种子条目的来源真实可查，但验证流水线当前挂起。所有种子条目为 `candidate` 或 `research_only`，未经本库的新验证，仅供参考，不构成投资建议。库提供判断框架与历史经验，不提供当前市场数字；当前数字必须由量化工具（VT）取数。

## License

MIT，见 [LICENSE](LICENSE)。Copyright (c) 2026 shadowinlife。
