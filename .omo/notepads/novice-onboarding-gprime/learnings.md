# Learnings - novice-onboarding-gprime

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /ulw-execute. Append new entries below - never overwrite._

---

## todo 4 — first-message exporter (2026-10-28)

- **Where sessions actually live on this host**: `~/.local/share/opencode/opencode.db` (14 GiB, WAL mode, plus `opencode.db-wal` / `-shm`); legacy JSON storage also exists at `~/.local/share/opencode/storage/{session,message,part}/`. The `opencode-serve:v3.10.0-mymain` image has **no** `.db` under `/home/opencode/.local/share/opencode/` (empty dir), so image-side extraction is unavailable.
- **Discovered schema (opencode 1.19.x)**: `session_message` (`session_id`, `type`, `seq`, `data` JSON with `text`) + `session_v2` (`parent_id`); legacy `message` (`session_id`, `id`, `time_created`, `data` JSON with `role`) and `part` (`message_id`, `data` JSON with `type`/`text`). First user message = min `seq` for `session_message.type='user'`, else earliest `message.role='user'` plus its `part.type='text'` rows. `session` / `session_v2` `parent_id` separates root from child/subagent sessions.
- **Real counts**: 3,480 sessions (725 root), 123,136 messages, 564,684 parts. Root-only extraction found 726 first user messages; 586 classified `都不是` — this is a developer harness machine, not a novice-user corpus, and the aggregate notes say so.
- **Read-only proof**: `shutil.copy2` of the DB + WAL/SHM siblings into `tempfile.mkdtemp()`, `sqlite3.connect("file:"+copy+"?mode=ro", uri=True)`, `shutil.rmtree` in `finally`. The 14 GiB copy took ~20 s on APFS.
- **Scanner patterns**: CN mobile `1[3-9]\d{9}` with digit boundaries; 18-char ID shape `\d{17}[\dXx]`; email; money with suffix units (`元|万元|万|USD|CNY|¥|$`) and prefix `¥`/`$`; secrets `sk-…`, `ghp_…`, `Bearer …`, 32+ hex, PEM private-key header. The aggregate is scanned as serialized JSON and aborted on any hit.

## 2026-10-28 — todo 1: intake eval harness (Wave 0)

- **Corpus label vocabulary**: `expect` ∈ {`direct_answer`, `execute_with_defaults`, `clarify_private`, `refuse`}; `expected_product` ∈ the 9 planned artifacts (个股体检/选股清单/策略回测/组合体检/事件/宏观影响/交易复盘/定时监控/报告/none). Negatives are c08 `什么是因子` and c09 `明天能涨吗` — both `direct_answer` + product `none`; c10 is `refuse`.
- **Event judge rules implemented in `run.py`** (also embedded as `judge_rule` in `events.schema.json`):
  - `route_class`: `question` tool call, or ≥2 numbered lines plus a choice cue (选项/请选择/成本价/仓位…), ⇒ `clarify_private`; else non-empty `vibe-trading_*`/`ch_*` call ⇒ `execute_with_defaults`; else capability-refusal markers (无法转账/无法执行/不支持/超出…) ⇒ `refuse`; else text-only ⇒ `direct_answer`. Bare 无法/不能 were deliberately excluded so c09's “无法预测” stays `direct_answer`.
  - `intake_loaded`: tool args mention `skills/intake`, or assistant text quotes `intake`. Expected true for every item in the target design (baseline pre-intake is expected false).
  - `question_called`: tool named `question`, `question.*`, or `question_*`; `question_count` counts them. Expected true only for `clarify_private`.
  - `params_compiled`: any `vibe-trading_*`/`ch_*` call with non-empty args; `tool` + `args_digest` identify the first. Expected true only for `execute_with_defaults` (first-turn clarify has no params yet).
  - `run_error` is the operational terminal record for CLI failure/timeout/no parseable output; one failed item does not abort the batch.
- **`opencode run --format json` gotchas**: event shapes are not guaranteed — parse each line, ignore non-JSON logs, walk nested objects, and accept `type` ∈ {tool, tool_use, tool_call} with `tool`/`name`; args live under `state.input` / `input` / `args` / `arguments`. Tokens may appear under `tokens` or `usage` and may be absent → record `null`, never fabricate. Do not assume a single top-level assistant message or a stable event name set.
- **Contract stability**: `corpus.jsonl`, `expected.json`, `events.schema.json` and `run.py` are consumed by todos 2/3/5/6/8/14; field names must not drift. `validate_corpus(items, expected=None)` is the shared validation entry point (test imports it via `importlib`).
- **Operational**: `run.py` is exactly 400 lines (practical cap); keep new logic out of it unless the task allows splitting. Sibling workers also write under `OpencodeAgent/tests/evals/intake/` (`export_first_messages.py`, `test_export_privacy.py`, later `checks.py`/`results/`/`slot_value/`); stage explicit file paths, never `git add <dir>`.
