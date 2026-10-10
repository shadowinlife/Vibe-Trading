# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (3 tools + 2 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 160 / 144

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 155/160 = 0.9688 |
| top-3 hit rate | 160/160 = 1.0000 |
| negative false-recall (conservative) | 0/153 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D19 | 144 | 141 | 144 | 0.9792 |
| unknown | 16 | 14 | 16 | 0.8750 |

## Cost

- API calls: 160 (retries counted individually)
- prompt tokens: 77737
- completion tokens: 109948
- estimated cost: $1.2938 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
