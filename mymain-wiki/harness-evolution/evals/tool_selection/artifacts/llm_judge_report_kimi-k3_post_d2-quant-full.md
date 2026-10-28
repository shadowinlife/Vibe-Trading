# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-26T02:07:13+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 120 / 80

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 110/120 = 0.9167 |
| top-3 hit rate | 115/120 = 0.9583 |
| negative false-recall (conservative) | 2/102 = 0.0196 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D06 | 28 | 24 | 25 | 0.8571 |
| D07 | 52 | 47 | 51 | 0.9038 |
| unknown | 40 | 39 | 39 | 0.9750 |

## Cost

- API calls: 120 (retries counted individually)
- prompt tokens: 1427029
- completion tokens: 34399
- estimated cost: $0.9422 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
