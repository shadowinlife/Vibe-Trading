# LLM-judge tool-selection report — kimi-k3 / baseline

- judge model: `kimi-k3` (role: sensitivity, temperature 0.0, max_response_tokens 1000)
- surface: `baseline` — corpus captured_at `2026-08-27T06:01:10+00:00` (74 tools + 90 skills)
- prompt template sha256: `b0e0fb112de70cddd9dee07d49f3e9353b5234bd5bbaf2947f8f58942dbf62e1`
- entries scored: 158 / 158

## Aggregate scores

| metric | value |
|---|---|
| top-1 accuracy | 144/158 = 0.9114 |
| top-3 hit rate | 155/158 = 0.9810 |
| negative false-recall (conservative) | 0/130 = 0.0000 |
| invalid responses (unparseable) | 0 |

## Per-domain breakdown

| domain | entries | top-1 | top-3 | top-1 accuracy |
|---|---|---|---|---|
| D01 | 9 | 9 | 9 | 1.0000 |
| D02 | 10 | 8 | 9 | 0.8000 |
| D03 | 5 | 4 | 5 | 0.8000 |
| D04 | 9 | 9 | 9 | 1.0000 |
| D05 | 11 | 11 | 11 | 1.0000 |
| D06 | 10 | 8 | 9 | 0.8000 |
| D07 | 10 | 10 | 10 | 1.0000 |
| D08 | 8 | 7 | 8 | 0.8750 |
| D09 | 10 | 10 | 10 | 1.0000 |
| D10 | 7 | 7 | 7 | 1.0000 |
| D11 | 8 | 8 | 8 | 1.0000 |
| D12 | 8 | 8 | 8 | 1.0000 |
| D13 | 9 | 9 | 9 | 1.0000 |
| D14 | 7 | 7 | 7 | 1.0000 |
| D15 | 7 | 7 | 7 | 1.0000 |
| D16 | 8 | 3 | 7 | 0.3750 |
| D17 | 11 | 10 | 11 | 0.9091 |
| D18 | 5 | 5 | 5 | 1.0000 |
| D19 | 6 | 4 | 6 | 0.6667 |

## Cost

- API calls: 158 (retries counted individually)
- prompt tokens: 1878679
- completion tokens: 43140
- estimated cost: $1.2351 USD — **estimate:true**, price table in judge_config.yaml must be verified before quoting externally

## Protocol notes

- Scoring, budget, resume and retry semantics: `artifacts/llm_judge_design.md`.
- Golden trace: `llm_judge_trace_kimi-k3_baseline.jsonl`.
