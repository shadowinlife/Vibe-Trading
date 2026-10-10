# LLM-judge tool-selection report — deepseek-v4-flash-0731 / baseline

- judge model: `deepseek-v4-flash-0731` (role: sensitivity, temperature 0.0, max_response_tokens 2000)
- surface: `baseline` — corpus captured_at `2026-08-26T01:18:43+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 89/158 = 0.5633 |
| top-3 hit rate | 96/158 = 0.6076 |
| negative false-recall (conservative) | 3/130 = 0.0231 |
| invalid responses (unparseable) | 3 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 5 | 5 | 0.5556 |
| D02 | 10 | 4 | 5 | 0.4000 |
| D03 | 5 | 2 | 2 | 0.4000 |
| D04 | 9 | 3 | 3 | 0.3333 |
| D05 | 11 | 9 | 9 | 0.8182 |
| D06 | 10 | 5 | 6 | 0.5000 |
| D07 | 10 | 7 | 7 | 0.7000 |
| D08 | 8 | 4 | 5 | 0.5000 |
| D09 | 10 | 7 | 7 | 0.7000 |
| D10 | 7 | 5 | 5 | 0.7143 |
| D11 | 8 | 4 | 4 | 0.5000 |
| D12 | 8 | 6 | 6 | 0.7500 |
| D13 | 9 | 9 | 9 | 1.0000 |
| D14 | 7 | 5 | 5 | 0.7143 |
| D15 | 7 | 4 | 4 | 0.5714 |
| D16 | 8 | 1 | 3 | 0.1250 |
| D17 | 11 | 5 | 5 | 0.4545 |
| D18 | 5 | 3 | 3 | 0.6000 |
| D19 | 6 | 1 | 3 | 0.1667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 1926313
- completion tokens: 109911
- estimated cost: $0.7098 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_deepseek-v4-flash-0731_baseline.jsonl`.
