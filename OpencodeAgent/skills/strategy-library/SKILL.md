---
name: strategy-library
description: |
  公共策略库（vibetrading-strategy-library）的检索、筛选与引用协议。
  任何金融分析、投资类问题，回答前先查本库，再决定引用、组合还是弃权。
  触发词：个股分析、买卖判断、估值、顶部/底部、市场过热、市场拥挤、策略推荐、选股、仓位、择时；
  stock analysis, buy/sell decision, valuation, market top/bottom, overheating, crowded trade,
  strategy recommendation, stock selection, position sizing, market timing.
---

# 策略库检索与引用（strategy-library）

本技能规定四件事：什么时候查库、怎么查、怎么筛、怎么组合与引用。库是索引与判断框架，不是行情源。

## 1. 何时用

任何金融分析或投资类问题，回答前先查本库。包括但不限于：个股分析、买卖判断、估值、顶部与底部判断、市场过热、市场拥挤、策略推荐、选股、仓位、择时。

不要凭记忆直接作答，不要跳过检索直接给结论。问题里即使没有出现"策略库"三个字，只要涉及金融判断，就先查。

## 2. 怎么查

库位置按以下顺序确定：

1. 环境变量 `STRATEGY_LIBRARY_PATH` 指向的目录。
2. 本技能包自带的 `library/` 子目录（技能加载时给出的 Base directory 下的 `library/`），存在则优先使用。
3. 镜像约定路径 `/opt/strategy-library`。

```bash
LIB="${STRATEGY_LIBRARY_PATH:-<本技能 Base directory>/library}"
[ -d "$LIB" ] || LIB=/opt/strategy-library
cd "$LIB"

python3 library.py search 拥挤 波动      # 多词 AND
python3 library.py show kn-0001
```

也可以直接读 `index.json`，每个条目含 `id`、`type`、`title`、`status`、`markets`、`path`、`keywords`。`index.json` 缺失或条目有变动时，先运行 `python3 library.py index`。

检索语义：中文按子串匹配，英文按分词匹配，大小写不敏感；多个词之间为 AND，全部命中才返回。

与专项技能的分工：命中条目后，若镜像中存在对应的专项技能（例如 escape-top 微观结构技能），把计算与执行交给技能。库条目提供判断框架、适用边界与引用纪律，专项技能提供信号计算与集成输出。

## 3. 筛选

先按适用性筛，再按状态筛。

适用性：

- `markets` 必须覆盖问题所在市场（`ashare` / `hk` / `us` / `crypto`）。
- `asset_classes` 与标的类型匹配，例如个股、指数、ETF。
- `horizon` 与问题的持有或观察周期匹配，日内结论不要套用长周期条目。

状态纪律：

- `validated`：可作证据引用，引用时带上 evidence 指向的验证结论。
- `candidate` / `research_only`：必须标注"未验证，仅供参考"，只能作为线索，不能写成结论。
- `rejected`：不得作为决策依据，只能用于说明失败经验。
- `blocked_by_data` / `blocked_by_permission`：说明缺什么数据或权限，不要当成否定结论。

引用 escape-top 时，唯一允许的已验证表述是："其子信号两融背离(margin_divergence)/波动扩张(volatility_atr_expansion)已在镜像仓通过 7 门验证"。除此之外，不得声称任何条目做过新验证。

## 4. 组合协议（CRITICAL）

单个条目通常不足以支撑结论，按以下四步执行，不得跳步。

第一步，拆子判断。把问题拆成独立的判断维度，例如估值、趋势、拥挤度、风险、流动性、催化剂。每个维度单独成项。

第二步，逐子判断检索。每个子判断单独 `search`，记录命中的条目 id、状态与适用条件。一个子判断可以有多条候选。

第三步，组合。按以下规则处理：

- 相互印证：多条独立条目指向同一结论时，可以提高置信度，并说明它们为何相互独立。
- 相互冲突：必须显式呈现冲突双方、各自适用条件与状态，不得静默平均，不得只挑有利的一条，不得用"综合来看"掩盖分歧。
- 覆盖不足：明说哪个子判断没有条目支撑，不要用其他维度的结论替代。

第四步，输出逐条引用。每个被使用的条目给出 id 与验证状态。以下仅为输出格式示意，id 与状态均为虚构：

```text
kn-0001（candidate，未验证，仅供参考）提示拥挤度偏高；
st-0002（validated）给出减仓规则，evidence 见条目内引用。
```

禁止事项：把 `candidate` 写成确定结论；把多条弱证据叠加当成强证据；省略状态标注；引用不存在的 id。

## 5. 数字纪律

本库提供判断框架与历史经验，不提供当前市场数字。库里的阈值与分位数是经验参照，不是实时读数。

任何当前价格、估值、资金流、拥挤度、波动率读数，必须由量化工具（VT）取数，并注明口径与时间。没有取数就不要给出数字；不能用库条目的历史阈值冒充当前值。

## 6. 诚实

没有相关条目就明说"库中无对应条目"，然后说明依据什么作答，或选择弃权。不编造条目 id、来源、验证结论或数字。

经验不足、数据缺失、条目冲突无法裁决时，弃权优于猜测。给出"当前无法判断，缺什么"比给一个像样的错误结论更有价值。

## 7. 沉淀

新经验可以沉淀为条目：

1. 按 `schema.md` 起草 `candidate` 条目，写全 `intended_use` 与 `limitations`。
2. 运行 `python3 library.py register <file>` 做字段校验与 id 冲突检查。
3. 通过后走 git 评审入库，`register` 不移动文件。

验证是独立阶段，当前挂起。不要在新条目里写 `validated`，不要声称做过验证；等验证恢复后再流转状态并补 evidence。
