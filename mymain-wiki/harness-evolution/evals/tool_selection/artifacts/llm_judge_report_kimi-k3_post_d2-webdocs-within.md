# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (3 tools + 2 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 160 / 144

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 146/160 = 0.9125 |
| top-3 hit rate | 147/160 = 0.9187 |
| negative false-recall (conservative) | 0/153 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D19 | 144 | 130 | 131 | 0.9028 |
| unknown | 16 | 16 | 16 | 1.0000 |

## Cost

- API calls: 160 (retries counted individually)
- prompt tokens: 84726
- completion tokens: 44839
- estimated cost: $0.1629 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
