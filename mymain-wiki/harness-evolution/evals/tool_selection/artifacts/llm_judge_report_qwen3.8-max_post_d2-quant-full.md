# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-26T02:07:13+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 120 / 80

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 104/120 = 0.8667 |
| top-3 hit rate | 114/120 = 0.9500 |
| negative false-recall (conservative) | 0/102 = 0.0000 |
| invalid responses (unparseable) | 4 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D06 | 28 | 23 | 27 | 0.8214 |
| D07 | 52 | 46 | 51 | 0.8846 |
| unknown | 40 | 35 | 36 | 0.8750 |

## Cost

- API calls: 120 (retries counted individually)
- prompt tokens: 1452081
- completion tokens: 114349
- estimated cost: $4.7737 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
