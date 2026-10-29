# Frozen classifier rubric — Wave 0 request distribution

**Status:** frozen before the 40-item run was analysed (2026-10-28).
**Scope:** labels the first user message of each corpus item as one of the eight
product artifacts or `都不是`. This is the same rubric used by
`export_first_messages.py::classify` for the 726-message real sample, so the
two distributions are comparable.

## Label semantics

| Label | The first message asks for |
|-------|----------------------------|
| `个股体检` | a single instrument's diagnosis, valuation, fundamentals or chart read |
| `选股清单` | a screened/shortlisted basket, ranking, opportunity list or recommendation set |
| `策略回测` | a backtest, strategy, factor, Sharpe/win-rate/drawdown or parameter study |
| `组合体检` | the user's portfolio/holdings/account diagnosis or rebalancing review |
| `事件/宏观影响` | a macro event, policy, war, rate decision or transmission-chain analysis |
| `交易复盘` | a review of the user's own trades, fills, P&L or liquidation history |
| `定时监控` | a scheduled/recurring monitor, alert, pre-market or post-market job |
| `报告` | a report/document/HTML/summary export that is not one of the above |
| `都不是` | none of the eight product intents are expressed in the message |

`都不是` is a **menu miss for the product index**, not a judgement that the
message is low quality. Concept questions, prediction requests and
out-of-capability requests are expected to land here.

## Keyword/priority table (frozen)

The classifier lowercases the message and returns the first label whose
keyword list contains any substring match. Order is significant: a specific
action resolves before the generic `报告`, and `定时监控`/`交易复盘`/`策略回测`
win over the broader analysis labels.

```text
定时监控      定时 监控 每天 每日 提醒 预警 盘前 盘后 cron schedule
交易复盘      复盘 交割单 交易记录 成交记录 盈亏 清仓 割肉
策略回测      回测 策略 因子 夏普 胜率 回撤 walk-forward 参数
事件/宏观影响  宏观 事件 战争 地缘 加息 降息 美联储 政策 俄乌
组合体检      组合 持仓 仓位 账户 portfolio 解套
选股清单      选股 筛选 清单 候选 股票池 排行 机会 推荐
个股体检      个股 体检 诊断 这只股 这只票 单只 估值 基本面
报告          报告 html 汇总 总结 导出
```

No keyword hit ⇒ `都不是`. The table is byte-identical to
`export_first_messages.py::LABEL_KEYWORDS` at the commit that froze this
rubric; `analyze_wave0.py` carries a frozen copy so the published numbers stay
reproducible even if a later patch changes the exporter, and the pytest suite
fails if the exporter and the frozen copy drift.

## Double-run consistency protocol

The rubric is a deterministic script, so the plan's "分类脚本或 LLM 分类器双跑"
branch is satisfied without an LLM judge:

1. **Run A** classifies the 40 prompts in corpus order (`c01 … c40`).
2. **Run B** classifies the same prompts ordered by
   `sha256(id + "\x00" + prompt)` — a stable hash, not Python's salted
   `hash()`.
3. Per-item labels must match. Agreement = matching items / 40. The threshold
   is **≥ 0.8**; a script classifier is expected to score 1.0. A lower score
   means the corpus or the runner is non-deterministic and the distribution is
   not admissible.

Run B exists to catch order-dependent state (e.g. a mutable default or a
cached label), not to introduce a second opinion. No LLM judge is used because
the rubric is a script, and an LLM would add variance without adding
information about a fixed keyword table.

## Pre-registered statistics and decision rules

- **Interval:** Wilson score interval, 95%, `z = 1.959964`, upper bound used
  for every kill-criterion decision (never the point estimate).
- **Coverage sample:** the 40-item corpus. The plan's `n < 40 ⇒ INCONCLUSIVE`
  floor applies to corpus size and observed runs; 40 items are classified, so
  the floor is met.
- **Coverage denominator (primary gate):** items whose `expected_product` is
  one of the eight products (`artifact-eligible`, n = 26). The other 14 items
  are `direct_answer`/`refuse` by contract and carry `expected_product: none`;
  counting them would make the gate a function of corpus composition (their
  floor alone is 14/40 = 35%). The all-40 distribution is still published for
  transparency.
- **Menu gate:** `都不是` Wilson upper bound on the artifact-eligible
  denominator.
  - `upper ≤ 0.35` ⇒ **PASS**; Wave 1 todo 9 stays unblocked; conditional
    work ② is not activated.
  - `upper > 0.35` ⇒ **FAIL**; conditional work ② (menu rework) is activated
    and Wave 1 todo 9 pauses until the menu is re-decided.
- **Trigger rate:** the literal `intake_loaded` rate on the 40 observed runs.
  The pre-migration build has no intake skill, so the expected value is 0 by
  construction. The `< 60%` kill criterion is **deferred** to the
  post-migration 40-item run in todo 6; a pre-migration 0 does not activate
  conditional work ①.
- **Pre-migration proxy:** clarification rate
  (`route_class == clarify_private`, i.e. a `question` call or a numbered
  choice list) on the union of `open_ended` and `clarify_private` items. It is
  descriptive evidence about the current protocol, not a substitute for the
  post-migration trigger gate.
- **Small samples:** if fewer than 40 items are observed, the affected rate is
  labelled INCONCLUSIVE with the required N; no extrapolation.