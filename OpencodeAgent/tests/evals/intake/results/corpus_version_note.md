# Corpus version note (v1 / v2)

## Why two versions exist

`32f5e595` corrected the labels of `c19` and `c22` from
`execute_with_defaults` to `clarify_private`. Wave 0 and Wave 1 evidence had
already been generated from the old labels: `results/intake_load_rate.json`,
`results/request_distribution.json`, `wave0_verdict.md` and
`results/wave1_report.md` publish numbers computed over that label set.
Regenerating those reports to match the corrected labels would rewrite
published evidence, and `analyze_wave0.py --check` /
`analyze_wave1.py --check` exist to prevent exactly that.

Instead the corpus is versioned:

- `corpus40.jsonl` — frozen **v1**, byte-identical to the corpus Wave 0/Wave 1
  consumed.
- `corpus40_v2.jsonl` — **v2**, identical prompts/ids, with the corrected
  `c19`/`c22` labels, rationales and a `labels_version: "v2"` marker on those
  two records. Every other line is byte-identical to v1.

## Provenance of v1

```bash
git show 32f5e595~1:OpencodeAgent/tests/evals/intake/corpus40.jsonl | shasum -a 256
# 69bb6f256d5e2afa7a521015b40d22101e47ceec7f84a4acfacac4cdbd410630
```

`test_corpus_schema.py::test_corpus40_v1_is_frozen_before_the_label_correction`
pins this hash, and `test_generator_reproduces_both_label_versions` proves
`gen_corpus40.py --labels v1` reproduces the file byte-for-byte.

## Which evidence reads which corpus

| Evidence | Corpus | Gate |
|---|---|---|
| Wave 0 reports (`intake_load_rate.json`, `request_distribution.json`, `wave0_verdict.md`) | v1 (`corpus40.jsonl`) | `analyze_wave0.py --check` |
| Wave 1 report (`results/wave1_report.md`) | v1 (`corpus40.jsonl`) | `analyze_wave1.py --check` |
| Bounded probe report (`results/wave1_frozen10_report.md`) | v2 (`corpus40_v2.jsonl`) | `analyze_probe.py --check` |
| A/B corpus (`ab/corpus_ab.jsonl`, frozen slice) | v2 (`corpus40_v2.jsonl`) | `ab/gen_corpus_ab.py --check`, `ab/ab_analysis.py --check` |

The probe and the A/B ran after the correction, so their consumers read v2;
the historical Wave 0/Wave 1 gates keep reading v1 and stay green without
editing their reports. The probe report's section 0 sentence naming
`corpus40.jsonl` is a frozen historical statement; the operative file is
`corpus40_v2.jsonl` as of this note.

## Changed ids

| id | prompt | v1 | v2 | why |
|---|---|---|---|---|
| `c19` | 看看我的账户组合近三年表现怎么样 | `execute_with_defaults` | `clarify_private` | needs the user's own holdings/cost basis, which no tool can obtain |
| `c22` | 帮我复盘一下近三个月的交易记录，看看盈亏 | `execute_with_defaults` | `clarify_private` | needs the user's own trade history / 交割单, which no tool can obtain |

Both are private-slot clarifications, not threshold or prompt changes.

## Regeneration

```bash
conda run -n legonanobot python tests/evals/intake/gen_corpus40.py --check             # v1
conda run -n legonanobot python tests/evals/intake/gen_corpus40.py --labels v2 --check # v2
```