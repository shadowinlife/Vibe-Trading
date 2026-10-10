# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (59 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 40 / 40

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 38/40 = 0.9500 |
| top-3 hit rate | 39/40 = 0.9750 |
| negative false-recall (conservative) | 0/22 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D06 | 20 | 18 | 19 | 0.9000 |
| D07 | 20 | 20 | 20 | 1.0000 |

## Cost

- API calls: 40 (retries counted individually)
- prompt tokens: 439008
- completion tokens: 33996
- estimated cost: $1.4375 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
