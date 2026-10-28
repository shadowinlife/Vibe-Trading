# Slot decision-value verdict (v2, bounded observation)

Pre-registered scripted experiment. The decision rule lives in `judge_spec.json` v2
(sha256 `cb3f80079f614c859e47947ef306c57d4388badf3b951fbcb0327bb8dec4154c`); every number below is emitted by `judge.py --write` from
`matrix.json` + `transcripts/`, and `judge.py --check` recomputes all of it with zero
tolerance. No human or LLM judgement enters a verdict.

## Observation design (pre-registered)

A signature is the **first 3 distinct tool calls with non-empty args**, in order of
first appearance, canonicalised with the v1 argument rules. The driver reads the CLI
JSON stream line by line and kills the CLI process group as soon as the 3rd call is
observed; it also kills at the 300s per-run timeout. A run that observed at least one
call before the stop is `ok_partial` and usable; a run with zero calls is `run_error`.
Later execution calls are outside the observation window by design: parameter
compilation happens in the first calls.

## v1 evidence: what the first attempt proved and why it was void

| item | v1 |
|------|----|
| runs recorded | 72 |
| ok signatures | 3 |
| run_error / timeouts | 69 |
| cells with a majority signature | 0 |
| per-run timeout (s) | 240.000 |
| verdict | void: all six slots INCONCLUSIVE |

v1 is void because the observation window was unbounded: 69/72 runs hit the 240s
timeout, and the 3 isolated signatures never formed a cell-level majority. The v2
change is an observation-window change, not a threshold change; the slot rule and
kill criterion are identical and were frozen before the first v2 run.

## Execution

| item | value |
|------|-------|
| cells | 24 |
| repeats per cell | 3 |
| runs recorded | 72 |
| ok (natural completion) | 0 |
| ok_partial (early stop or timeout with >=1 call) | 72 |
| run_error (0 calls) | 0 |
| incomplete (budget) | 0 |
| killed by the 3-call early stop | 71 |
| hit the per-run timeout | 1 |
| recovered via session API | 0 |
| k-call distribution (0/1/2/3) | 0/1/0/71 |
| mean wall per usable run (s) | 14.556 |
| median wall per usable run (s) | 9.213 |
| image | opencode-serve:v3.10.0-mymain |
| containers | intake-slot-a,intake-slot-b |
| per-run timeout (s) | 300.000 |
| batch wall budget (s) | 9000.000 |
| run window (UTC) | 2026-10-28T04:59:16+00:00 -> 2026-10-28T05:10:00+00:00 |

## Per-slot verdicts

| 槽位 | 裁决 | stable cells | distinct majority signatures | differing value pairs | usable repeats |
|------|------|--------------|------------------------------|-----------------------|----------------|
| 市场 | INCONCLUSIVE | 0/4 | 0 | A股 vs 港股,A股 vs 美股,A股 vs 加密,港股 vs 美股,港股 vs 加密,美股 vs 加密 | 12/12 |
| 期限 | INCONCLUSIVE | 0/4 | 0 | 1周内 vs 1-3个月,1周内 vs 6-12个月,1周内 vs 1年以上,1-3个月 vs 6-12个月,1-3个月 vs 1年以上,6-12个月 vs 1年以上 | 12/12 |
| 风险 | INCONCLUSIVE | 0/4 | 0 | 最大回撤5% vs 最大回撤10%,最大回撤5% vs 最大回撤20%,最大回撤5% vs 最大回撤30%,最大回撤10% vs 最大回撤20%,最大回撤10% vs 最大回撤30%,最大回撤20% vs 最大回撤30% | 12/12 |
| 风格 | INCONCLUSIVE | 0/4 | 0 | 价值 vs 成长,价值 vs 动量,价值 vs 红利,成长 vs 动量,成长 vs 红利,动量 vs 红利 | 12/12 |
| 候选池 | INCONCLUSIVE | 0/4 | 0 | Top5 vs Top10,Top5 vs Top20,Top5 vs Top50,Top10 vs Top20,Top10 vs Top50,Top20 vs Top50 | 12/12 |
| 持仓 | INCONCLUSIVE | 0/4 | 0 | 空仓 vs 持仓且成本=现价,空仓 vs 持仓且成本高于现价10%,空仓 vs 满仓,持仓且成本=现价 vs 持仓且成本高于现价10%,持仓且成本=现价 vs 满仓,持仓且成本高于现价10% vs 满仓 | 12/12 |

### Per-slot evidence

#### 市场 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| A股 | market-0 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 港股 | market-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 美股 | market-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 加密 | market-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| A股 vs 港股 | 3 | 3 | true |
| A股 vs 美股 | 3 | 3 | true |
| A股 vs 加密 | 3 | 3 | true |
| 港股 vs 美股 | 3 | 3 | true |
| 港股 vs 加密 | 3 | 3 | true |
| 美股 vs 加密 | 3 | 3 | true |

#### 期限 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| 1周内 | horizon-0 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 1-3个月 | horizon-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 6-12个月 | horizon-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 1年以上 | horizon-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 1周内 vs 1-3个月 | 3 | 3 | true |
| 1周内 vs 6-12个月 | 3 | 3 | true |
| 1周内 vs 1年以上 | 3 | 3 | true |
| 1-3个月 vs 6-12个月 | 3 | 3 | true |
| 1-3个月 vs 1年以上 | 3 | 3 | true |
| 6-12个月 vs 1年以上 | 3 | 3 | true |

#### 风险 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| 最大回撤5% | risk-0 | ok_partial,ok_partial,ok_partial | 1,3,3 | 3 | 3 | - | false |
| 最大回撤10% | risk-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 最大回撤20% | risk-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 最大回撤30% | risk-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 最大回撤5% vs 最大回撤10% | 3 | 3 | true |
| 最大回撤5% vs 最大回撤20% | 3 | 3 | true |
| 最大回撤5% vs 最大回撤30% | 3 | 3 | true |
| 最大回撤10% vs 最大回撤20% | 3 | 3 | true |
| 最大回撤10% vs 最大回撤30% | 3 | 3 | true |
| 最大回撤20% vs 最大回撤30% | 3 | 3 | true |

#### 风格 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| 价值 | style-0 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 成长 | style-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 动量 | style-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 红利 | style-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 价值 vs 成长 | 3 | 3 | true |
| 价值 vs 动量 | 3 | 3 | true |
| 价值 vs 红利 | 3 | 3 | true |
| 成长 vs 动量 | 3 | 3 | true |
| 成长 vs 红利 | 3 | 3 | true |
| 动量 vs 红利 | 3 | 3 | true |

#### 候选池 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| Top5 | pool-0 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| Top10 | pool-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| Top20 | pool-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| Top50 | pool-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| Top5 vs Top10 | 3 | 3 | true |
| Top5 vs Top20 | 3 | 3 | true |
| Top5 vs Top50 | 3 | 3 | true |
| Top10 vs Top20 | 3 | 3 | true |
| Top10 vs Top50 | 3 | 3 | true |
| Top20 vs Top50 | 3 | 3 | true |

#### 持仓 — INCONCLUSIVE

Reason: 4 of 4 cells have no majority signature.

| value | cell | repeat statuses | k calls | usable | distinct sigs | majority hash | stable |
|-------|------|-----------------|---------|--------|---------------|---------------|--------|
| 空仓 | position-0 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 持仓且成本=现价 | position-1 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 持仓且成本高于现价10% | position-2 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |
| 满仓 | position-3 | ok_partial,ok_partial,ok_partial | 3,3,3 | 3 | 3 | - | false |

| value pair | comparable repeats | differing repeats | differs |
|------------|--------------------|-------------------|---------|
| 空仓 vs 持仓且成本=现价 | 3 | 3 | true |
| 空仓 vs 持仓且成本高于现价10% | 3 | 3 | true |
| 空仓 vs 满仓 | 3 | 3 | true |
| 持仓且成本=现价 vs 持仓且成本高于现价10% | 3 | 3 | true |
| 持仓且成本=现价 vs 满仓 | 3 | 3 | true |
| 持仓且成本高于现价10% vs 满仓 | 3 | 3 | true |

## Kill criterion

Default-worthy slots: 0 of 6 (threshold 4 of 6).
Kill criterion fired: **false**.

the question table keeps question-worthy public slots plus private slots 成本价, 仓位规模; default-worthy public slots are defaulted; INCONCLUSIVE slots are held, not guessed.

Counterfactual, fixed by the pre-registration: if at least 4 of 6 slots had been
default-worthy, the question table would shrink to private slots only: 成本价, 仓位规模.

## Diagnosis

72 of 72 runs yielded a usable signature; 71 stopped at the 3-call threshold and 1 hit the per-run timeout. Under the pre-registered majority rule a cell needs two usable
repeats that agree; no cell reached that bar. Every slot is therefore INCONCLUSIVE: the
experiment does not show that any slot value changes the first compiled calls, and it is
not evidence for defaulting any slot.

Acceptance check: 24 of 24 cells are INCONCLUSIVE; the smallest per-slot count is 4, so the >=2 INCONCLUSIVE cells per slot
condition is met. Every cell shows 3 distinct usable signatures; the cause is
run-to-run variation in the first three calls, not missing observations. Required N: rerun
until each cell has at least two usable repeats sharing a signature.

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
| compose projects down | True |
| lane temp dir removed | True |
| containers absent | True |
| lanes | intake-slot-a, intake-slot-b |
| checked at (UTC) | 2026-10-28T05:12:01Z |
| `docker ps -a` intake-slot rows |  |

## Caveats

- A signature of length 1 or 2 means the run stopped before the 3rd call; it is usable
  under the pre-registered rule, not a failed run.
- A `run_error` is retained, never replaced by an extra repeat. A cell with fewer than
  two usable repeats is INCONCLUSIVE.
- Each lane runs one session at a time and clears scratch artifacts and agent memory
  before every run, so a run cannot compile against another run's workspace.
- Runs executed on 2 lanes with 1 worker per lane.

<!-- judge:begin -->
```json
{
  "cleanup": {
    "checked_at": "2026-10-28T05:12:01Z",
    "compose_down": true,
    "container_rows": "",
    "containers_absent": true,
    "lane_dir_removed": true,
    "lanes": [
      "intake-slot-a",
      "intake-slot-b"
    ]
  },
  "kill_criterion": {
    "consequence": "the question table keeps question-worthy public slots plus private slots 成本价, 仓位规模; default-worthy public slots are defaulted; INCONCLUSIVE slots are held, not guessed",
    "default_worthy_count": 0,
    "fired": false,
    "threshold": "4 of 6"
  },
  "matrix_sha256": "dffb188680363ac48ae55d68f3763d9eb9eedc1e228cca32de2c71fa3290319a",
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
    "early_stop_count": 71,
    "incomplete": 0,
    "k_distribution": {
      "0": 0,
      "1": 1,
      "2": 0,
      "3": 71
    },
    "mean_wall_s": 14.556,
    "median_wall_s": 9.213,
    "ok": 0,
    "ok_partial": 72,
    "recovered": 0,
    "run_error": 0,
    "timeout_count": 1,
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "1周内",
          "b_value": "6-12个月",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "1周内",
          "b_value": "1年以上",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "1-3个月",
          "b_value": "6-12个月",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "1-3个月",
          "b_value": "1年以上",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "6-12个月",
          "b_value": "1年以上",
          "comparable": 3,
          "different": 3,
          "differs": true
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "A股",
          "b_value": "美股",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "A股",
          "b_value": "加密",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "港股",
          "b_value": "美股",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "港股",
          "b_value": "加密",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "美股",
          "b_value": "加密",
          "comparable": 3,
          "different": 3,
          "differs": true
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "Top5",
          "b_value": "Top20",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "Top5",
          "b_value": "Top50",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "Top10",
          "b_value": "Top20",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "Top10",
          "b_value": "Top50",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "Top20",
          "b_value": "Top50",
          "comparable": 3,
          "different": 3,
          "differs": true
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "空仓",
          "b_value": "持仓且成本高于现价10%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "空仓",
          "b_value": "满仓",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "持仓且成本=现价",
          "b_value": "持仓且成本高于现价10%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "持仓且成本=现价",
          "b_value": "满仓",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "持仓且成本高于现价10%",
          "b_value": "满仓",
          "comparable": 3,
          "different": 3,
          "differs": true
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "最大回撤5%",
          "b_value": "最大回撤20%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "最大回撤5%",
          "b_value": "最大回撤30%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "最大回撤10%",
          "b_value": "最大回撤20%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "最大回撤10%",
          "b_value": "最大回撤30%",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "最大回撤20%",
          "b_value": "最大回撤30%",
          "comparable": 3,
          "different": 3,
          "differs": true
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
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "价值",
          "b_value": "动量",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "价值",
          "b_value": "红利",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "成长",
          "b_value": "动量",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "成长",
          "b_value": "红利",
          "comparable": 3,
          "different": 3,
          "differs": true
        },
        {
          "a_value": "动量",
          "b_value": "红利",
          "comparable": 3,
          "different": 3,
          "differs": true
        }
      ],
      "reason": "4 of 4 cells have no majority signature",
      "stable_cells": 0,
      "verdict": "INCONCLUSIVE"
    }
  },
  "spec_sha256": "cb3f80079f614c859e47947ef306c57d4388badf3b951fbcb0327bb8dec4154c",
  "spec_version": 2,
  "v1_evidence": {
    "available": true,
    "cells_with_majority": 0,
    "incomplete": 0,
    "mean_wall_s": 174.326,
    "note": "v1 is void: 69/72 runs hit the 240s per-run timeout; the 3 isolated signatures formed 0 cell-level majorities. Full v1 transcripts remain in commit 8d25e5d8.",
    "ok": 3,
    "per_run_timeout_s": 240.0,
    "run_error": 69,
    "runs": 72,
    "spec_version": 1
  }
}
```
<!-- judge:end -->
