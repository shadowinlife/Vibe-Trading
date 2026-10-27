# Intake eval harness (Wave 0)

Frozen corpus + event contract for measuring the novice-onboarding routing
design. The labels here describe the **target artifact-first behavior**, not
the current build: a baseline run is expected to disagree on several items.
That disagreement is the measurement, not a harness bug.

Everything in this directory is offline by default. `--dry-run` and the
pytest suite never touch the network, Docker, or a model provider.

## Files

| File | Purpose |
|------|---------|
| `corpus.jsonl` | 10 frozen first-user-turn prompts, one JSON object per line |
| `expected.json` | Per-id expectation detail (category, product, turn budget, markers) |
| `events.schema.json` | JSON Schema draft 2020-12 for one instrumented event record |
| `run.py` | Corpus driver: `--dry-run` plus `opencode run --attach` mode |
| `test_corpus_schema.py` | Offline pytest contract suite (7 tests, including a failure probe) |
| `README.md` | This file |

## Corpus

| id | prompt | expect | expected_product | negative | open_ended |
|----|--------|--------|------------------|----------|------------|
| c01 | 帮我看看 600519 | execute_with_defaults | 个股体检 | false | false |
| c02 | 今天买什么 | execute_with_defaults | 选股清单 | false | true |
| c03 | 最近有什么机会？ | execute_with_defaults | 选股清单 | false | true |
| c04 | 帮我回测一个双均线策略，标的是沪深300，近三年 | execute_with_defaults | 策略回测 | false | false |
| c05 | 我的持仓怎么样 | clarify_private | 组合体检 | false | false |
| c06 | 我的科创50ETF亏了20%，怎么办 | clarify_private | 个股体检 | false | false |
| c07 | 俄乌战争对后续市场有什么影响 | execute_with_defaults | 事件/宏观影响 | false | true |
| c08 | 什么是因子 | direct_answer | none | true | false |
| c09 | 明天能涨吗 | direct_answer | none | true | false |
| c10 | 帮我把账户里的钱转到券商 | refuse | none | false | false |

`expected_product` uses the nine planned artifact names: 个股体检 / 选股清单 /
策略回测 / 组合体检 / 事件/宏观影响 / 交易复盘 / 定时监控 / 报告 / none.

## Label semantics

- `execute_with_defaults` — produce a named artifact using declared defaults.
  Covers the plan's 明确可执行, 开放型 and 宏观型 classes: asking the user for
  market, horizon or risk is not allowed when a defensible default exists.
- `clarify_private` — ask only for user-private slots (cost basis, position
  size, holdings), in at most one turn with numbered choices. Market data and
  symbol identity are resolved with tools, not questions.
- `direct_answer` — answer (or refuse to predict) directly and produce **no**
  parameters. This is the negative class: c08 is a concept question, c09 is a
  low-conversability prediction request.
- `refuse` — no capability / out of scope (c10 is a money-transfer request).

`expected.json` carries `max_user_turns: 2` for every item (the IS-1 budget),
`negative`, and `required_markers` for later closing-block/content checks.
Markers that are intentionally required by the plan: `["因子"]` for c08,
`["不预测", "免责声明"]` for c09, and `["成本价", "仓位"]` for c05/c06.

## Event contract

`events.schema.json` describes one JSONL record per event. Every record
requires `run_id`, `corpus_id`, `event`, `ts`, `observed`, `expected` and
`evidence`; optional fields are `tool`, `args_digest`, `question_count`,
`user_turns`, `tokens` and `wall_s`.

| event | judge rule (implemented in `run.py`) |
|-------|--------------------------------------|
| `route_class` | A `question` tool call, or a numbered choice list (≥2 numbered lines plus a choice cue such as 选项/请选择/成本价/仓位), in the first assistant turn → `clarify_private`; otherwise a `vibe-trading_*` / `ch_*` tool call with non-empty arguments → `execute_with_defaults`; otherwise an explicit capability refusal marker → `refuse`; otherwise text-only → `direct_answer`. |
| `intake_loaded` | A read-style tool call whose arguments mention `skills/intake`, or assistant text quoting the intake skill name. Expected true for every item; a pre-intake baseline is expected to observe false. |
| `question_called` | Any tool part named `question`, `question.*` or `question_*`; `question_count` is the number of calls. Expected true only for `clarify_private`. |
| `params_compiled` | Any `vibe-trading_*` / `ch_*` call with non-empty arguments, i.e. options were compiled into tool parameters. `tool` + `args_digest` identify the first such call. Expected true only for `execute_with_defaults`. |

`run_error` is an operational fifth record, not one of the four measured
layers. It is written when the CLI exits nonzero, times out, cannot be
executed, or yields no parseable assistant text/tool call; `evidence` carries
the stderr tail and the run continues to the next corpus item.

The validator in `run.py` is a focused, hand-written subset of draft 2020-12:
it checks the schema's required-key list, event names, field types and the
`observed`/`expected` narrowing per event. It is not a general JSON Schema
engine, and `jsonschema` is intentionally not a dependency.

## Offline checks

```bash
cd OpencodeAgent
conda run -n legonanobot python -m pytest tests/evals/intake/test_corpus_schema.py -q
conda run -n legonanobot python tests/evals/intake/run.py --dry-run
```

The failure probe deletes `expect` from a temporary copy of c01 and feeds the
copy through the same `validate_corpus()` used by the driver. Any green suite
that cannot fail is a defect, so this probe is part of the pytest suite and is
also reproducible from the shell:

```bash
conda run -n legonanobot python -c '
import importlib.util, json, pathlib, tempfile
spec = importlib.util.spec_from_file_location("intake_run", "tests/evals/intake/run.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
lines = pathlib.Path("tests/evals/intake/corpus.jsonl").read_text(encoding="utf-8").splitlines()
item = json.loads(lines[0]); del item["expect"]
probe = pathlib.Path(tempfile.mkdtemp()) / "corpus.jsonl"
probe.write_text("\n".join([json.dumps(item, ensure_ascii=False), *lines[1:]]) + "\n", encoding="utf-8")
errors = mod.validate_corpus(mod.load_corpus(probe))
print(f"probe_errors={len(errors)}")
for error in errors: print(error)
raise SystemExit(0 if errors else 1)
'
```

## Driving a live container

Real mode costs one model session per corpus item. The plan caps total eval
sessions at 150, so use `--limit` / `--repeat` deliberately.

```bash
cd OpencodeAgent
docker compose up -d
conda run -n legonanobot python tests/evals/intake/run.py --container opencode-web --limit 1
```

- Without `--container`, the driver expects `opencode` on `PATH` and connects
  to `--attach http://localhost:4096` by default.
- With `--container opencode-web`, the driver prefixes `docker exec
  opencode-web`; the attach URL must be reachable **inside** that container
  (the default `localhost:4096` works because the server listens there).
- `--model`, `--agent`, `--command`, `--timeout`, `--repeat`, `--limit` and
  `--out` pass through to the run.
- Each item is invoked as
  `opencode run --attach <url> --format json --title <corpus_id> <prompt>`.
- Default output: `results/run_<utcstamp>.jsonl`. Raw stdout/stderr is kept
  under `$TMPDIR/vt-intake-eval-raw/` (or `--raw-dir`). Nothing is written
  outside those two locations.
- Exit code is 0 when every attempted item produced a terminal record (four
  events, or a `run_error`); a single failed item does not abort the batch.

## Reading the JSONL

One JSON object per line, in `events.schema.json` shape. Typical slice:

```bash
python - <<'PY'
import json
for line in open("results/run_<stamp>.jsonl", encoding="utf-8"):
    record = json.loads(line)
    print(record["corpus_id"], record["event"], record["observed"], record["expected"])
PY
```

`tokens` and `wall_s` are recorded per run when discoverable; they are `null`
rather than guessed. `run_error` lines are terminal for their item and should
be counted separately from routing accuracy.

## Recorded runs

Captured on 2026-10-28 against the frozen contract (no network, no Docker).

### pytest

```text
$ cd OpencodeAgent && conda run -n legonanobot python -m pytest tests/evals/intake/test_corpus_schema.py -q
.......                                                                  [100%]
7 passed in 0.16s
```

### dry run

```text
$ conda run -n legonanobot python tests/evals/intake/run.py --dry-run
c01 | execute_with_defaults | 个股体检 | false | 帮我看看 600519
c02 | execute_with_defaults | 选股清单 | false | 今天买什么
c03 | execute_with_defaults | 选股清单 | false | 最近有什么机会？
c04 | execute_with_defaults | 策略回测 | false | 帮我回测一个双均线策略，标的是沪深300，近三年
c05 | clarify_private | 组合体检 | false | 我的持仓怎么样
c06 | clarify_private | 个股体检 | false | 我的科创50ETF亏了20%，怎么办
c07 | execute_with_defaults | 事件/宏观影响 | false | 俄乌战争对后续市场有什么影响
c08 | direct_answer | none | true | 什么是因子
c09 | direct_answer | none | true | 明天能涨吗
c10 | refuse | none | false | 帮我把账户里的钱转到券商
$ echo $?
0
```

### failure probe (deleted `expect` from c01)

```text
$ conda run -n legonanobot python -c '<probe shown above>'
probe_errors=2
c01: missing or empty required field 'expect'
c01: expect must be one of ('direct_answer', 'execute_with_defaults', 'clarify_private', 'refuse')
```

The probe exits 0 only because it asserts that errors are non-empty; the
underlying validation failed as intended. The same condition is asserted by
`test_failure_probe_missing_expect_fails_validation` in the pytest suite.