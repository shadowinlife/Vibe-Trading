# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (11 tools + 12 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 120 / 80

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 118/120 = 0.9833 |
| top-3 hit rate | 120/120 = 1.0000 |
| negative false-recall (conservative) | 0/102 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D06 | 28 | 26 | 28 | 0.9286 |
| D07 | 52 | 52 | 52 | 1.0000 |
| unknown | 40 | 40 | 40 | 1.0000 |

## Cost

- API calls: 120 (retries counted individually)
- prompt tokens: 200149
- completion tokens: 35305
- estimated cost: $0.2084 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
