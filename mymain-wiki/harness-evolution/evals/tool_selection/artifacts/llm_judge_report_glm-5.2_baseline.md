# LLM-judge tool-selection report — glm-5.2 / baseline

- judge model: `glm-5.2` (role: sensitivity, temperature 0.0, max_response_tokens 2000)
- surface: `baseline` — corpus captured_at `2026-08-26T01:18:43+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 139/158 = 0.8797 |
| top-3 hit rate | 149/158 = 0.9430 |
| negative false-recall (conservative) | 2/130 = 0.0154 |
| invalid responses (unparseable) | 2 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 7 | 7 | 0.7778 |
| D02 | 10 | 9 | 9 | 0.9000 |
| D03 | 5 | 4 | 4 | 0.8000 |
| D04 | 9 | 9 | 9 | 1.0000 |
| D05 | 11 | 10 | 10 | 0.9091 |
| D06 | 10 | 10 | 10 | 1.0000 |
| D07 | 10 | 9 | 10 | 0.9000 |
| D08 | 8 | 7 | 8 | 0.8750 |
| D09 | 10 | 9 | 10 | 0.9000 |
| D10 | 7 | 6 | 6 | 0.8571 |
| D11 | 8 | 8 | 8 | 1.0000 |
| D12 | 8 | 6 | 6 | 0.7500 |
| D13 | 9 | 9 | 9 | 1.0000 |
| D14 | 7 | 7 | 7 | 1.0000 |
| D15 | 7 | 7 | 7 | 1.0000 |
| D16 | 8 | 4 | 8 | 0.5000 |
| D17 | 11 | 9 | 10 | 0.8182 |
| D18 | 5 | 5 | 5 | 1.0000 |
| D19 | 6 | 4 | 6 | 0.6667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 1863113
- completion tokens: 72153
- estimated cost: $2.1517 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_glm-5.2_baseline.jsonl`.
