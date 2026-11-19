# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-26T15:12:25+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 142/158 = 0.8987 |
| top-3 hit rate | 153/158 = 0.9684 |
| negative false-recall (conservative) | 1/130 = 0.0077 |
| invalid responses (unparseable) | 2 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 9 | 9 | 1.0000 |
| D02 | 10 | 10 | 10 | 1.0000 |
| D03 | 5 | 4 | 5 | 0.8000 |
| D04 | 9 | 9 | 9 | 1.0000 |
| D05 | 11 | 11 | 11 | 1.0000 |
| D06 | 10 | 7 | 9 | 0.7000 |
| D07 | 10 | 10 | 10 | 1.0000 |
| D08 | 8 | 7 | 8 | 0.8750 |
| D09 | 10 | 10 | 10 | 1.0000 |
| D10 | 7 | 7 | 7 | 1.0000 |
| D11 | 8 | 7 | 7 | 0.8750 |
| D12 | 8 | 7 | 7 | 0.8750 |
| D13 | 9 | 9 | 9 | 1.0000 |
| D14 | 7 | 6 | 6 | 0.8571 |
| D15 | 7 | 7 | 7 | 1.0000 |
| D16 | 8 | 4 | 7 | 0.5000 |
| D17 | 11 | 9 | 11 | 0.8182 |
| D18 | 5 | 5 | 5 | 1.0000 |
| D19 | 6 | 4 | 6 | 0.6667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 2024710
- completion tokens: 133114
- estimated cost: $6.3929 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
