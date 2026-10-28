# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-26T12:09:29+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 60 / 60

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 50/60 = 0.8333 |
| top-3 hit rate | 56/60 = 0.9333 |
| negative false-recall (conservative) | 0/60 = 0.0000 |
| invalid responses (unparseable) | 2 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 10 | 9 | 10 | 0.9000 |
| D03 | 3 | 2 | 3 | 0.6667 |
| D05 | 10 | 10 | 10 | 1.0000 |
| D06 | 4 | 4 | 4 | 1.0000 |
| D08 | 3 | 3 | 3 | 1.0000 |
| D09 | 10 | 9 | 10 | 0.9000 |
| D16 | 10 | 4 | 6 | 0.4000 |
| D19 | 10 | 9 | 10 | 0.9000 |

## Cost

- API calls: 60 (retries counted individually)
- prompt tokens: 744138
- completion tokens: 52605
- estimated cost: $2.3864 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
