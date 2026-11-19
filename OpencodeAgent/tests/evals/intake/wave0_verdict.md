# Wave 0 verdict — intake trigger rate and product-menu coverage

Plan: `.omo/plans/novice-onboarding-gprime.md` todo 5. Corpus: `corpus40.jsonl` (10 frozen + 30 generated, `corpus40 v1 seed=20261027`). Run: `results/wave0_corpus40.jsonl`; image `opencode-serve:v3.10.0-mymain`; lane `intake-rate` (3 GB / 1.2 CPU, amd64 QEMU). Run window (record timestamps): 2026-10-28T02:28:49+00:00 → 2026-10-28T03:59:57+00:00. Observed runs: 40/40; no incomplete items. Recovered from the server session API: 25 (short-run race or after the CLI timeout; 16 have server wall >240s, max 2919.054s).

## Verdict 1 — intake trigger rate

Literal `intake_loaded` event: **0/40** = 0.0% (Wilson 95% [0.0%, 8.8%]).

**This zero is by construction, not a measurement of routing quality.** At the tested commit `OpencodeAgent/skills/intake/SKILL.md` does not exist, and the judge only emits `intake_loaded` when a tool argument mentions `skills/intake` or assistant text quotes `intake`. The baseline image predates todo 6, so the event cannot prove a skill load. It must not activate conditional work ①.

Operational note: the per-item CLI wait is capped at 240s. A timed-out CLI is killed and, when its server session completes afterwards, its transcript is recovered from `GET /session/{sessionID}/message`. A recovered record's `wall_s` is the server session duration and its evidence starts with `recovered_via_session_api;`; this is direct observation of the server transcript, not extrapolation.

On the items that expect intake (`open_ended` ∪ `clarify_private`, n=14), `intake_loaded` is **0/14** = 0.0% (Wilson 95% [0.0%, 21.5%]); this is the denominator the post-migration <60% gate will use. Pre-migration proxy — clarification (a numbered choice list or a `question` call) on the same subset: **5/14** = 35.7% (Wilson 95% [16.3%, 61.2%]); `question_called` was true for 0 of them. This is the only pre-migration routing evidence and it is descriptive, not a kill switch: the proxy's Wilson upper bound (61.2%) straddles the 60% threshold, so it cannot adjudicate the trigger gate either.

**Ruling:** conditional work ① (the deterministic `chat.message` hook trigger) is **deferred** to the post-migration 40-item run in todo 6. The kill criterion is evaluated there against a skill that exists: **`intake_loaded` rate < 60% on the `open_ended ∪ clarify_private` subset** (currently expected n=14), with the all-40 rate reported as context. If that post-migration rate is <60%, conditional work ① is activated at that point; a pre-migration zero is not that measurement.

## Verdict 2 — product-menu coverage

All 40 items (context only): `都不是` 15/40 = 37.5% (Wilson 95% [24.2%, 53.0%]). The 14 items whose `expected_product` is `none` are non-artifact by contract; including them would make the gate a function of corpus composition.

Artifact-eligible denominator (26 items whose contract names one of the eight products): `都不是` 3/26 = 11.5% (Wilson 95% [4.0%, 29.0%]); **upper bound 29.0%**. Misses: `c01`, `c02`, `c06`.

**Menu gate: PASS.** The Wilson upper bound 29.0% is ≤ 35.0%. Wave 1 todo 9 stays unblocked; conditional work ② (menu rework) is **not** activated.

Real-sample cross-check (non-decisive): `results/request_labels.json`, 726 root first messages from the developer machine, `都不是` 586/726 = 80.7% (Wilson 95% [77.7%, 83.4%]; order of magnitude matches the brief's ~82%). This is a non-representative developer-harness sample, not novice users; it is **not** used to decide the menu.

## Classifier consistency

Run A (corpus order) and Run B (`sha256(id + NUL + prompt)` order) agree on 40/40 items = 1.00, above the 0.8 threshold. No LLM judge was used because the rubric is a deterministic script; this satisfies the plan's "分类脚本或 LLM 分类器双跑" branch. Per-item labels are in `results/request_distribution.json`.

## Cleanup receipt

`docker compose -p intake-rate -f docker-compose.yml -f "$TMPDIR/lane-rate.yml" down --remove-orphans`
removed the `intake-rate` container and its network; `rm -rf "$TMPDIR/lane-rate" "$TMPDIR/rate-raw"`
removed the lane volumes and raw transcripts; `docker ps -a --filter name=intake-rate` returned no rows.
The peer lane `intake-slot` (port 4097) belongs to another todo and was left untouched.

## Machine block (recomputed by `analyze_wave0.py`)

<!-- wave0:begin -->
```json
{
  "classifier_agreement": 1.0,
  "corpus_n": 40,
  "coverage": {
    "all_corpus": {
      "k": 15,
      "n": 40,
      "rate": 0.375,
      "wilson_upper": 0.529675609
    },
    "artifact_eligible": {
      "k": 3,
      "misses": [
        "c01",
        "c02",
        "c06"
      ],
      "n": 26,
      "rate": 0.115384615,
      "wilson_upper": 0.289759032
    },
    "gate": "pass",
    "threshold": 0.35
  },
  "decisions": {
    "conditional_work_1_hook": "deferred_post_migration",
    "conditional_work_2_menu_rework": false,
    "todo_9": "unblocked"
  },
  "intake_loaded": {
    "attempted": 40,
    "expected_subset": {
      "clarification_k": 5,
      "inconclusive": false,
      "intake_loaded_k": 0,
      "intake_loaded_n": 14,
      "intake_loaded_rate": 0.0,
      "intake_loaded_wilson95": {
        "high": 0.215310803,
        "low": 0.0
      },
      "items": 14,
      "n": 14,
      "question_called_k": 0,
      "rate": 0.357142857,
      "wilson95": {
        "high": 0.612355769,
        "low": 0.163447318
      }
    },
    "inconclusive": false,
    "k": 0,
    "n": 40,
    "observed": 40,
    "rate": 0.0,
    "required_n": 40,
    "wilson95": {
      "high": 0.087621601,
      "low": 0.0
    }
  },
  "run": {
    "attempted": 40,
    "incomplete": 0,
    "incomplete_ids": [],
    "max_wall_s": 2919.054,
    "observed": 40,
    "over_240s": 16,
    "recovered": 25,
    "recovered_ids": [
      "c01",
      "c02",
      "c04",
      "c05",
      "c09",
      "c10",
      "c11",
      "c12",
      "c13",
      "c14",
      "c16",
      "c17",
      "c18",
      "c19",
      "c25",
      "c26",
      "c27",
      "c28",
      "c29",
      "c30",
      "c31",
      "c32",
      "c33",
      "c34",
      "c36"
    ]
  }
}
```
<!-- wave0:end -->

## Reproduction

```bash
cd OpencodeAgent
conda run -n legonanobot python tests/evals/intake/analyze_wave0.py --check
conda run -n legonanobot python -m pytest tests/evals/intake/ -q
wc -l tests/evals/intake/results/wave0_corpus40.jsonl
```
