# LLM-judge tool-selection report — deepseek-v4-flash-0731 / post

- judge model: `deepseek-v4-flash-0731` (role: sensitivity, temperature 0.0, max_response_tokens 2000)
- surface: `post` — corpus captured_at `2026-08-26T02:07:13+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 90/158 = 0.5696 |
| top-3 hit rate | 96/158 = 0.6076 |
| negative false-recall (conservative) | 3/130 = 0.0231 |
| invalid responses (unparseable) | 3 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 5 | 5 | 0.5556 |
| D02 | 10 | 3 | 3 | 0.3000 |
| D03 | 5 | 1 | 1 | 0.2000 |
| D04 | 9 | 3 | 3 | 0.3333 |
| D05 | 11 | 7 | 7 | 0.6364 |
| D06 | 10 | 6 | 8 | 0.6000 |
| D07 | 10 | 7 | 7 | 0.7000 |
| D08 | 8 | 5 | 5 | 0.6250 |
| D09 | 10 | 7 | 7 | 0.7000 |
| D10 | 7 | 7 | 7 | 1.0000 |
| D11 | 8 | 6 | 6 | 0.7500 |
| D12 | 8 | 7 | 7 | 0.8750 |
| D13 | 9 | 8 | 8 | 0.8889 |
| D14 | 7 | 4 | 4 | 0.5714 |
| D15 | 7 | 5 | 6 | 0.7143 |
| D16 | 8 | 1 | 1 | 0.1250 |
| D17 | 11 | 4 | 5 | 0.3636 |
| D18 | 5 | 0 | 1 | 0.0000 |
| D19 | 6 | 4 | 5 | 0.6667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 1940059
- completion tokens: 108593
- estimated cost: $0.7123 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_deepseek-v4-flash-0731_post.jsonl`.
