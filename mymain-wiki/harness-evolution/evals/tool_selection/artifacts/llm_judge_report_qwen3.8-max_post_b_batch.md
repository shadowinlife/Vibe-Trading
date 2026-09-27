# LLM-judge tool-selection report — qwen3.8-max / post

- judge model: `qwen3.8-max` (role: primary, temperature 0.0, max_response_tokens 500)
- surface: `post` — corpus captured_at `2026-08-27T07:46:41+00:00` (59 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 130/158 = 0.8228 |
| top-3 hit rate | 138/158 = 0.8734 |
| negative false-recall (conservative) | 3/130 = 0.0231 |
| invalid responses (unparseable) | 2 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 8 | 8 | 0.8889 |
| D02 | 10 | 9 | 9 | 0.9000 |
| D03 | 5 | 4 | 5 | 0.8000 |
| D04 | 9 | 8 | 8 | 0.8889 |
| D05 | 11 | 10 | 11 | 0.9091 |
| D06 | 10 | 8 | 9 | 0.8000 |
| D07 | 10 | 10 | 10 | 1.0000 |
| D08 | 8 | 6 | 7 | 0.7500 |
| D09 | 10 | 9 | 9 | 0.9000 |
| D10 | 7 | 7 | 7 | 1.0000 |
| D11 | 8 | 6 | 6 | 0.7500 |
| D12 | 8 | 8 | 8 | 1.0000 |
| D13 | 9 | 8 | 9 | 0.8889 |
| D14 | 7 | 7 | 7 | 1.0000 |
| D15 | 7 | 7 | 7 | 1.0000 |
| D16 | 8 | 0 | 0 | 0.0000 |
| D17 | 11 | 10 | 11 | 0.9091 |
| D18 | 5 | 1 | 1 | 0.2000 |
| D19 | 6 | 4 | 6 | 0.6667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 1733674
- completion tokens: 150557
- estimated cost: $5.8398 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_qwen3.8-max_post.jsonl`.
