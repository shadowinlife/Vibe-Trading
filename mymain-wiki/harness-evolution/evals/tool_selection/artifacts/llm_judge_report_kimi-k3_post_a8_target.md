# LLM-judge tool-selection report — kimi-k3 / post

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `post` — corpus captured_at `2026-08-26T15:12:25+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 70 / 70

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 67/70 = 0.9571 |
| top-3 hit rate | 69/70 = 0.9857 |
| negative false-recall (conservative) | 0/70 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 10 | 8 | 10 | 0.8000 |
| D05 | 20 | 20 | 20 | 1.0000 |
| D09 | 10 | 10 | 10 | 1.0000 |
| D11 | 10 | 9 | 9 | 0.9000 |
| D12 | 10 | 10 | 10 | 1.0000 |
| D15 | 10 | 10 | 10 | 1.0000 |

## Cost

- API calls: 70 (retries counted individually)
- prompt tokens: 882335
- completion tokens: 21442
- estimated cost: $0.5830 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_post.jsonl`.
