# Slot decision-value verdict

Pre-registered scripted experiment. The decision rule lives in `judge_spec.json`
(sha256 `cfd21df1a55d7d7eac9de0cfb3d97eac9195454264eda88685a9da564fe31e76`); every number below is emitted by `judge.py --write` from
`matrix.json` + `transcripts/`, and `judge.py --check` recomputes all of it with zero
tolerance. No human or LLM judgement enters a verdict.

## Execution

| item | value |
|------|-------|
| cells | 24 |
| repeats per cell | 3 |
| runs recorded | 72 |
| runs ok | 3 |
| runs run_error | 69 |
| runs incomplete | 0 |
| runs that hit the per-run timeout | 69 |
| recovered via session API | 0 |
| mean wall per ok run (s) | 174.326 |
| median wall per ok run (s) | 166.383 |
| image | opencode-serve:v3.10.0-mymain |
| container | intake-slot |
| per-run timeout (s) | 240.000 |
| batch wall budget (s) | 10800.000 |
| run window (UTC) | 2026-10-28T02:20:40+00:00 -> 2026-10-28T04:12:47+00:00 |

## Per-slot verdicts

| 槽位 | 裁决 | stable cells | distinct majority signatures | differing value pairs | usable repeats |
|------|------|--------------|------------------------------|-----------------------|----------------|
| 市场 | INCONCLUSIVE | 0/4 | 0 |  | 0/12 |
| 期限 | INCONCLUSIVE | 0/4 | 0 |  | 0/12 |
| 风险 | INCONCLUSIVE | 0/4 | 0 |  | 0/12 |
| 风格 | INCONCLUSIVE | 0/4 | 0 |  | 0/12 |
| 候选池 | INCONCLUSIVE | 0/4 | 0 |  | 2/12 |
| 持仓 | INCONCLUSIVE | 0/4 | 0 |  | 1/12 |

### Per-slot evidence

#### 市场 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| A股 | market-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| 港股 | market-1 | run_error,run_error,run_error | 0 | 0 | - | false |
| 美股 | market-2 | run_error,run_error,run_error | 0 | 0 | - | false |
| 加密 | market-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| A股 vs 港股 | 0 | 0 | false |
| A股 vs 美股 | 0 | 0 | false |
| A股 vs 加密 | 0 | 0 | false |
| 港股 vs 美股 | 0 | 0 | false |
| 港股 vs 加密 | 0 | 0 | false |
| 美股 vs 加密 | 0 | 0 | false |

#### 期限 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| 1周内 | horizon-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| 1-3个月 | horizon-1 | run_error,run_error,run_error | 0 | 0 | - | false |
| 6-12个月 | horizon-2 | run_error,run_error,run_error | 0 | 0 | - | false |
| 1年以上 | horizon-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 1周内 vs 1-3个月 | 0 | 0 | false |
| 1周内 vs 6-12个月 | 0 | 0 | false |
| 1周内 vs 1年以上 | 0 | 0 | false |
| 1-3个月 vs 6-12个月 | 0 | 0 | false |
| 1-3个月 vs 1年以上 | 0 | 0 | false |
| 6-12个月 vs 1年以上 | 0 | 0 | false |

#### 风险 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| 最大回撤5% | risk-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| 最大回撤10% | risk-1 | run_error,run_error,run_error | 0 | 0 | - | false |
| 最大回撤20% | risk-2 | run_error,run_error,run_error | 0 | 0 | - | false |
| 最大回撤30% | risk-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 最大回撤5% vs 最大回撤10% | 0 | 0 | false |
| 最大回撤5% vs 最大回撤20% | 0 | 0 | false |
| 最大回撤5% vs 最大回撤30% | 0 | 0 | false |
| 最大回撤10% vs 最大回撤20% | 0 | 0 | false |
| 最大回撤10% vs 最大回撤30% | 0 | 0 | false |
| 最大回撤20% vs 最大回撤30% | 0 | 0 | false |

#### 风格 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| 价值 | style-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| 成长 | style-1 | run_error,run_error,run_error | 0 | 0 | - | false |
| 动量 | style-2 | run_error,run_error,run_error | 0 | 0 | - | false |
| 红利 | style-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 价值 vs 成长 | 0 | 0 | false |
| 价值 vs 动量 | 0 | 0 | false |
| 价值 vs 红利 | 0 | 0 | false |
| 成长 vs 动量 | 0 | 0 | false |
| 成长 vs 红利 | 0 | 0 | false |
| 动量 vs 红利 | 0 | 0 | false |

#### 候选池 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| Top5 | pool-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| Top10 | pool-1 | run_error,run_error,ok | 1 | 1 | - | false |
| Top20 | pool-2 | run_error,run_error,ok | 1 | 1 | - | false |
| Top50 | pool-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| Top5 vs Top10 | 0 | 0 | false |
| Top5 vs Top20 | 0 | 0 | false |
| Top5 vs Top50 | 0 | 0 | false |
| Top10 vs Top20 | 1 | 1 | false |
| Top10 vs Top50 | 0 | 0 | false |
| Top20 vs Top50 | 0 | 0 | false |

#### 持仓 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|--------|---------------|---------------|--------|
| 空仓 | position-0 | run_error,run_error,run_error | 0 | 0 | - | false |
| 持仓且成本=现价 | position-1 | run_error,run_error,ok | 1 | 1 | - | false |
| 持仓且成本高于现价10% | position-2 | run_error,run_error,run_error | 0 | 0 | - | false |
| 满仓 | position-3 | run_error,run_error,run_error | 0 | 0 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 空仓 vs 持仓且成本=现价 | 0 | 0 | false |
| 空仓 vs 持仓且成本高于现价10% | 0 | 0 | false |
| 空仓 vs 满仓 | 0 | 0 | false |
| 持仓且成本=现价 vs 持仓且成本高于现价10% | 0 | 0 | false |
| 持仓且成本=现价 vs 满仓 | 0 | 0 | false |
| 持仓且成本高于现价10% vs 满仓 | 0 | 0 | false |

## Kill criterion

Default-worthy slots: 0 of 6 (threshold 4 of 6).
Kill criterion fired: **false**.

the question table keeps question-worthy public slots plus private slots 成本价, 仓位规模; default-worthy public slots are defaulted; INCONCLUSIVE slots are held, not guessed.

Counterfactual, fixed by the pre-registration: if at least 4 of 6 slots had been
default-worthy, the question table would shrink to private slots only: 成本价, 仓位规模.


## Diagnosis

3 of 72 runs produced a signature; 69 hit the
pre-registered 240.0s per-run budget. Under the pre-registered
majority rule a cell needs two usable repeats that agree; no cell reached that bar.
Every slot is therefore INCONCLUSIVE: the experiment does not show that any slot value
changes the tool-call signature, and it is not evidence for defaulting any slot. The
limiting factor is the per-run time budget, not an observed absence of effect.

## Resulting question table (todo 12)

| 槽位 | 裁决 | 处置 |
|------|------|------|
| 市场 | INCONCLUSIVE | hold-inconclusive |
| 期限 | INCONCLUSIVE | hold-inconclusive |
| 风险 | INCONCLUSIVE | hold-inconclusive |
| 风格 | INCONCLUSIVE | hold-inconclusive |
| 候选池 | INCONCLUSIVE | hold-inconclusive |
| 持仓 | INCONCLUSIVE | hold-inconclusive |
| 成本价 | private | ask |
| 仓位规模 | private | ask |

All six public slots are INCONCLUSIVE. This experiment does not license defaulting
any of them; todo 12 must not consume a default set from this run.

Decision vocabulary: `default` = 默认化，不提问；`ask` = 保留提问；
`hold-inconclusive` = 证据不足，不得静默归入 default 或 ask。

## Cleanup receipt

| check | result |
|-------|--------|
| compose project down | True |
| lane temp dir removed | True |
| intake-slot container absent | True |
| checked at (UTC) | 2026-10-28T04:13:49Z |
| `docker ps -a` intake-slot rows |  |

## Caveats

- Empty signature (`[]`) means the run produced no tool call with non-empty arguments;
  it is a usable signature under the pre-registered rule, not a failed run.
- The container has no ClickHouse credentials, so `ch_*` tools fail fast. This is an
  environment limitation recorded once; it is not counted as a model failure.
- A `run_error` is retained, never replaced by an extra repeat. A cell with fewer than
  two usable repeats is INCONCLUSIVE.
- Runs share one persistent `/workspace`; a run that finds artifacts written by an earlier
  run can complete by validating them. That is measured behavior, not a controlled variable.
- Runs executed with 3 concurrent workers in one shared `/workspace`.

<!-- judge:begin -->
```json
{
  "cleanup": {
    "checked_at": "2026-10-28T04:13:49Z",
    "compose_down": true,
    "container_absent": true,
    "container_rows": "",
    "lane_dir_removed": true
  },
  "kill_criterion": {
    "consequence": "the question table keeps question-worthy public slots plus private slots 成本价, 仓位规模; default-worthy public slots are defaulted; INCONCLUSIVE slots are held, not guessed",
    "default_worthy_count": 0,
    "fired": false,
    "threshold": "4 of 6"
  },
  "matrix_sha256": "6c916cffe3b4af748fbcbff0a4851a2e8b2d08cc19396da2994b167f28d37e45",
  "question_table": [
    {
      "decision": "hold-inconclusive",
      "slot": "市场",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "hold-inconclusive",
      "slot": "期限",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "hold-inconclusive",
      "slot": "风险",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "hold-inconclusive",
      "slot": "风格",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "hold-inconclusive",
      "slot": "候选池",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "hold-inconclusive",
      "slot": "持仓",
      "verdict": "INCONCLUSIVE"
    },
    {
      "decision": "ask",
      "slot": "成本价",
      "verdict": "private"
    },
    {
      "decision": "ask",
      "slot": "仓位规模",
      "verdict": "private"
    }
  ],
  "runs": {
    "incomplete": 0,
    "mean_wall_s": 174.326,
    "median_wall_s": 166.383,
    "ok": 3,
    "recovered": 0,
    "run_error": 69,
    "timeouts": 69,
    "total": 72
  },
  "slots": {
    "horizon": {
      "distinct_majority_signatures": [],
      "name": "期限",
      "pair_diffs": [
        {
          "a_value": "1周内",
          "b_value": "1-3个月",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "1周内",
          "b_value": "6-12个月",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "1周内",
          "b_value": "1年以上",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "1-3个月",
          "b_value": "6-12个月",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "1-3个月",
          "b_value": "1年以上",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "6-12个月",
          "b_value": "1年以上",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    },
    "market": {
      "distinct_majority_signatures": [],
      "name": "市场",
      "pair_diffs": [
        {
          "a_value": "A股",
          "b_value": "港股",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "A股",
          "b_value": "美股",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "A股",
          "b_value": "加密",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "港股",
          "b_value": "美股",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "港股",
          "b_value": "加密",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "美股",
          "b_value": "加密",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    },
    "pool": {
      "distinct_majority_signatures": [],
      "name": "候选池",
      "pair_diffs": [
        {
          "a_value": "Top5",
          "b_value": "Top10",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "Top5",
          "b_value": "Top20",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "Top5",
          "b_value": "Top50",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "Top10",
          "b_value": "Top20",
          "comparable": 1,
          "different": 1,
          "differs": false
        },
        {
          "a_value": "Top10",
          "b_value": "Top50",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "Top20",
          "b_value": "Top50",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    },
    "position": {
      "distinct_majority_signatures": [],
      "name": "持仓",
      "pair_diffs": [
        {
          "a_value": "空仓",
          "b_value": "持仓且成本=现价",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "空仓",
          "b_value": "持仓且成本高于现价10%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "空仓",
          "b_value": "满仓",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "持仓且成本=现价",
          "b_value": "持仓且成本高于现价10%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "持仓且成本=现价",
          "b_value": "满仓",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "持仓且成本高于现价10%",
          "b_value": "满仓",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    },
    "risk": {
      "distinct_majority_signatures": [],
      "name": "风险",
      "pair_diffs": [
        {
          "a_value": "最大回撤5%",
          "b_value": "最大回撤10%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "最大回撤5%",
          "b_value": "最大回撤20%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "最大回撤5%",
          "b_value": "最大回撤30%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "最大回撤10%",
          "b_value": "最大回撤20%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "最大回撤10%",
          "b_value": "最大回撤30%",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "最大回撤20%",
          "b_value": "最大回撤30%",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    },
    "style": {
      "distinct_majority_signatures": [],
      "name": "风格",
      "pair_diffs": [
        {
          "a_value": "价值",
          "b_value": "成长",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "价值",
          "b_value": "动量",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "价值",
          "b_value": "红利",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "成长",
          "b_value": "动量",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "成长",
          "b_value": "红利",
          "comparable": 0,
          "different": 0,
          "differs": false
        },
        {
          "a_value": "动量",
          "b_value": "红利",
          "comparable": 0,
          "different": 0,
          "differs": false
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    }
  },
  "spec_sha256": "cfd21df1a55d7d7eac9de0cfb3d97eac9195454264eda88685a9da564fe31e76"
}
```
<!-- judge:end -->
