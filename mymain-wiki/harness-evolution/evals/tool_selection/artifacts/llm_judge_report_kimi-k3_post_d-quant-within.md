# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (11 tools + 12 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 40 / 40

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 35/40 = 0.8750 |
| top-3 hit rate | 40/40 = 1.0000 |
| negative false-recall (conservative) | 0/22 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D06 | 20 | 17 | 20 | 0.8500 |
| D07 | 20 | 18 | 20 | 0.9000 |

## Cost

- API calls: 40 (retries counted individually)
- prompt tokens: 62284
- completion tokens: 8897
- estimated cost: $0.0596 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
