# LLM-judge tool-selection report — qwen3.8-max / baseline

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `baseline` — corpus captured_at `2026-08-26T12:09:29+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 70 / 70

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 62/70 = 0.8857 |
| top-3 hit rate | 68/70 = 0.9714 |
| negative false-recall (conservative) | 0/70 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 10 | 7 | 10 | 0.7000 |
| D05 | 20 | 20 | 20 | 1.0000 |
| D09 | 10 | 9 | 9 | 0.9000 |
| D11 | 10 | 9 | 10 | 0.9000 |
| D12 | 10 | 9 | 9 | 0.9000 |
| D15 | 10 | 8 | 10 | 0.8000 |

## Cost

- API calls: 70 (retries counted individually)
- prompt tokens: 868065
- completion tokens: 66380
- estimated cost: $2.8340 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_baseline.jsonl`.
