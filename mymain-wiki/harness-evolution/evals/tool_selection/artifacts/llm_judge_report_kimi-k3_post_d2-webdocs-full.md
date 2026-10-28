# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-26T02:07:13+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 160 / 144

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 99/160 = 0.6188 |
| top-3 hit rate | 154/160 = 0.9625 |
| negative false-recall (conservative) | 1/153 = 0.0065 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D19 | 144 | 89 | 138 | 0.6181 |
| unknown | 16 | 10 | 16 | 0.6250 |

## Cost

- API calls: 160 (retries counted individually)
- prompt tokens: 1902966
- completion tokens: 42689
- estimated cost: $1.2485 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
