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
- **Fix 2026-10-28 (follow-up)**: `intake_loaded.expected` now follows the routing class — `bool(item["open_ended"]) or item["expect"] == "clarify_private"` (true c02/c03/c05/c06/c07; false c01/c04/c08/c09/c10); `events.schema.json` judge_rule, the README row and a pytest case were updated.
- **Fix 2026-10-28 (follow-up)**: container mode must run with a working directory — `docker exec -w <dir> ...` plus `opencode run --dir <dir>` (default `/workspace`), otherwise `/workspace/AGENTS.md` and `/workspace/.opencode/skills/` are not loaded and any baseline is meaningless.
- **cwd rule**: `--dir` is the single source for both the docker exec cwd and the CLI `--dir`; keep `--attach` unchanged; never run the eval from `/`.

## 2026-10-28 — todo 2: baseline + ask-first protocol snapshot (Wave 0)

- **Frozen protocol**: `OpencodeAgent/tests/evals/intake/fixtures/ask_first_protocol_snapshot.md` captures `AGENTS.md:66-125` (60 lines, sha256 `598dbd1546f426baff3e890ff39c15b9b25cce6b87f567a297d20973a17f068f`) and `skills/research-scenarios/SKILL.md:64-70` scene C (7 lines, sha256 `b23b6da54e1f4b48aea2706144bb2883eb09c6cc3d0fe8dbc2645f12f27bac11`) at commit `abcaf2629b4537c7e909b607dd99702c860728b8`. `test_protocol_snapshot.py` recomputes both digests from the fixture only (it never opens `AGENTS.md`) and hardcodes the two constants; it stays green after todo 6 deletes the source lines.
- **Image**: `opencode-serve:v3.10.0-mymain` (id `sha256:77548a02382efb27f3ef69d1abb238a32dc19167e78c00a367fcdcff4a6a0e37`, digest `sha256:64f9566215ce9b6f6aa3a71bb6aaee37e4d421864a19d1cd010f723805956a46`), linux/amd64 under QEMU on Colima arm64. Compose override outside the repo only replaces the image; `docker-compose.yml` pins `opencode-serve:latest`, which does not exist, so plain `docker compose up -d` fails.
- **Readiness**: `GET /config?directory=/workspace` returned 200 in 41s on the first start and 47s on the fresh restart (budget 300s). Container limits from compose: 6G memory / 0.8 CPU; per-item wall time ranged 8.7s–672.2s.
- **Volumes gotcha**: the first `compose up` failed with `error while creating mount source path .../volumes/cron-state: chown ... permission denied` under Colima. Pre-creating `OpencodeAgent/volumes/{cron-state,cron-logs,vt-memory}` makes the same compose command succeed. Docker's auto-create path is what fails, not the mount itself.
- **Harness timeout**: the suggested `--timeout 300` is too small for this QEMU rig. c01's server session completed at ~398s while the harness recorded a timeout `run_error`. The full baseline used `--timeout 1800`.
- **Real per-item wall times (harness)**: c01 479.085s, c02 491.669s, c03 49.909s, c04 386.886s, c05 16.684s, c06 672.218s, c07 32.273s, c08 12.020s, c09 12.279s, c10 8.716s; total 2161.739s. 10/10 terminal records, 7 event-complete, 3 `run_error`.
- **Fast-response CLI race (important)**: `opencode run --attach --format json` exits 0 after printing only `step_start` for fast answers (~<15s). The server sessions c08/c09/c10 did complete with non-empty assistant text and `finish=stop`; a manual c08 re-run reproduced the one-line stream in 11.3s. The harness correctly records `run_error`, which is excluded from all four layers. Consequence: `direct_answer` and `refuse` have n=0 in the published baseline. Do not "fix" this by editing `run.py` in todo 2; document it and revisit the driver separately.
- **Loading evidence**: c01's raw stream contains a `skill` tool call with `name=research-scenarios` and output headed `/workspace/.opencode/skills/research-scenarios`; `opencode debug skill` run from `-w /workspace` lists the same path. Root `AGENTS.md` is system-prompt-injected and is not persisted in the `--format json` stream, but c09's server-side reasoning explicitly cites "Per AGENTS.md problem-handling protocol" and c08's cites "given the AGENTS.md context". The pinned `--dir /workspace` plus `docker exec -w /workspace` is what makes both happen.
- **Question tool**: not callable in this build. `OPENCODE_ENABLE_QUESTION_TOOL` is unset, and the `opencode run` handler itself injects `{permission:"question",action:"deny"}` into every session (confirmed in the binary handler and in every session record). `question_called=0/7` is mechanism availability, not model reluctance. Text-only clarification exists: c09's server-side message contains numbered choices; c10's contains a refusal.
- **Token accounting reality**: `run.py` records the first usage block per item, and `parse_stream` sums top-level `total` + `input` + `output` + `reasoning`, so `tokens_total=420592` double-counts `total` (e.g. c05 recorded 59,076 vs the stream's own `total` 58,466 and 57,856 cache-read). Server session totals (input+output+reasoning, excluding cache) sum to 675,836; cache reads sum to 16,625,664. `baseline_meta.json` carries both; never compare harness `tokens` to session totals.
- **Judge-rule caveat**: `route_class`/`params_compiled` count any `vibe-trading_*`/`ch_*` call with non-empty args. c05's only call was `vibe-trading_memory_recall`, so the frozen rule labels it `execute_with_defaults`; c06 called data tools but still never asked for cost basis/position. Keep the label and state the caveat; do not rewrite the record.
- **Budget**: 10 baseline sessions + 1 smoke + 1 manual probe + 1 c08 re-run = 13 sessions (plan allocation 11; the 2 extra are diagnostics). Baseline wall total 2161.739s.
- **Cleanup**: `docker compose -f docker-compose.yml -f "$TMPDIR/baseline-override.yml" down` removed the container and network; `$TMPDIR/baseline-raw` deleted; `docker ps -a` has no `opencode-web`. Raw transcripts never entered the repo.
- **Artifacts**: `results/baseline.jsonl` (31 records = 28 events + 3 run_error), `results/baseline_meta.json`, `baseline_report.md`, `fixtures/tool_list.json` (94 tools, one object per line, sorted), `recompute.py` (stdlib, `--check` 0 tolerance), `test_protocol_snapshot.py`.
- Recovery endpoint confirmed: `GET /doc` (OpenAPI 3.1.0) lists `/session/{sessionID}/message`; fetch with basic auth from `--username`/`--password` (pass `--password` or export `OPENCODE_SERVER_PASSWORD`; `/doc` is 401 otherwise).
- Recovery rule: only when `opencode run --attach --format json` yields no text and no tool call; extract `sessionID`/`session_id`/`session.id`, parse assistant messages with `_walk`/`_tool_name`/`_tool_args`, prefix every evidence with `recovered_via_session_api;`; empty recovery stays `run_error`.
- `--only c08 --only c09` is repeatable and applied before `--limit`; `--disable-recovery` proves the raw CLI path. The first repair attempt lost 3 sessions because the host did not export `OPENCODE_SERVER_PASSWORD`; pass `--password` explicitly.

## todo 5 — Wave 0 intake trigger + menu coverage verdicts (2026-10-28)

- **Corpus40 composition**: `corpus40.jsonl` = 10 frozen items copied byte-identically from `corpus.jsonl` (`head -10` cmp clean) + 30 generated by `gen_corpus40.py` (`corpus40 v1 seed=20261027`, `source: extended_generated`). Classes: execute_with_defaults 20, clarify_private 6, direct_answer 8 (all negatives), refuse 6; expected_product covers all 8 artifacts + 14 `none`; 8 open_ended; 8 negatives total (2 frozen + 6 generated); 6 refuse total (1 frozen + 5 generated). Generated prompts are templates × slot vocabularies, not hand-written; `gen_corpus40.py --check` reproduces the file byte-for-byte.
- **Run**: `opencode-serve:v3.10.0-mymain`, private lane `intake-rate` on host 4098, 3 GB / 1.2 CPU, amd64 QEMU; 40 sessions (c01-c40) driven with `run.py --corpus ... --timeout 240`; batch wall ~91 min from 02:28:49Z to 03:59:57Z, inside the 150-min budget. 25 of 40 item transcripts were recovered from the server session API (8 short-run CLI races + 17 post-timeout/missing sessions); 16 recovered sessions had server wall >240s (max 2919s for c18). The 240s cap is the CLI wait, not the server duration. No incomplete items; `results/wave0_corpus40.jsonl` = 160 records (40 × 4 events).
- **Operational gotcha**: `subprocess.run(docker exec ..., timeout=240)` kills only the host `docker exec`, not the in-container `opencode run`; the first batch accumulated zombie CLIs (c04/c05 still alive after their host timeout) and was stopped at 4 items. `run.py` now runs `docker exec <container> pkill -f "opencode run .*--title cNN"` in the timeout branch. The server session survives the CLI kill and is recoverable via `GET /session/{id}/message`; that is how c01/c04/c05 and the tail timeouts were completed. The harness's existing short-run recovery (exit 0 with only `step_start`) also fired for 8 items.
- **intake_loaded finding**: 0/40 (Wilson 95% [0.0%, 8.8%]) and 0/14 on the expected subset (Wilson 95% [0.0%, 21.5%]). This is by construction: `OpencodeAgent/skills/intake/SKILL.md` does not exist in the tested image, and the judge only fires on a `skills/intake` tool argument or the literal `intake` in assistant text. It does not measure routing quality and does not activate conditional work ①. The pre-migration clarification proxy is 5/14 = 35.7% (Wilson 95% [16.3%, 61.2%]; `question_called` 0/14); the upper bound straddles 60%, so the proxy cannot adjudicate the gate either. Ruling: conditional work ① (hook trigger) is deferred to the post-migration 40-item run in todo 6; threshold `<60% intake_loaded on open_ended ∪ clarify_private` (n=14 expected).
- **Menu coverage**: all-40 `都不是` 15/40 = 37.5% (Wilson 95% [24.2%, 53.0%]); artifact-eligible denominator 26 items, `都不是` 3/26 = 11.5% (Wilson 95% [4.0%, 29.0%], upper 29.0%). Menu gate **PASS** (upper ≤35%): Wave 1 todo 9 stays unblocked; conditional work ② (menu rework) is not activated. Misses were the frozen natural-language items c01/c02/c06. The 14 `none` items are non-artifact by contract; including them would make the gate a corpus-composition function (their floor is 14/40 = 35%). This denominator was pre-registered in `classifier_rubric.md` before the run. The 726-message developer sample is 586/726 = 80.7% `都不是` (Wilson 95% [77.7%, 83.4%]), reported as non-representative and not used for the gate.
- **Classifier**: frozen keyword/priority table identical to `export_first_messages.py::LABEL_KEYWORDS`; Run A (corpus order) and Run B (sha256 order) agree 40/40 = 1.00 (threshold 0.8). No LLM judge needed (the plan's script-classifier branch).
- **Verdicts**: ① trigger gate **deferred** (by-construction zero; proxy descriptive only); ② menu **PASS**, todo 9 unblocked.
- **Cleanup**: `docker compose -p intake-rate -f docker-compose.yml -f "$TMPDIR/lane-rate.yml" down --remove-orphans`; `$TMPDIR/lane-rate` and `$TMPDIR/rate-raw` deleted; `docker ps -a --filter name=intake-rate` empty; peer `intake-slot` (4097) left untouched. Receipt in `wave0_verdict.md`.
- **Per-item wall time (server session duration where recovered, seconds)**:

| id | expect | route | wall_s | recovered |
|---|---|---|---|---|
| c01 | execute_with_defaults | clarify_private | 595.176 | true |
| c02 | execute_with_defaults | direct_answer | 13.89 | true |
| c03 | execute_with_defaults | execute_with_defaults | 85.951 | false |
| c04 | execute_with_defaults | clarify_private | 695.587 | true |
| c05 | clarify_private | clarify_private | 185.013 | true |
| c06 | clarify_private | execute_with_defaults | 161.973 | false |
| c07 | execute_with_defaults | direct_answer | 22.643 | false |
| c08 | direct_answer | execute_with_defaults | 23.49 | false |
| c09 | direct_answer | direct_answer | 12.791 | true |
| c10 | refuse | refuse | 9.23 | true |
| c11 | execute_with_defaults | execute_with_defaults | 470.347 | true |
| c12 | execute_with_defaults | execute_with_defaults | 427.574 | true |
| c13 | execute_with_defaults | execute_with_defaults | 505.593 | true |
| c14 | execute_with_defaults | clarify_private | 713.751 | true |
| c15 | execute_with_defaults | execute_with_defaults | 42.328 | false |
| c16 | execute_with_defaults | execute_with_defaults | 1024.828 | true |
| c17 | execute_with_defaults | clarify_private | 2240.555 | true |
| c18 | execute_with_defaults | execute_with_defaults | 2919.054 | true |
| c19 | execute_with_defaults | direct_answer | 325.912 | true |
| c20 | execute_with_defaults | execute_with_defaults | 72.1 | false |
| c21 | execute_with_defaults | direct_answer | 22.799 | false |
| c22 | execute_with_defaults | direct_answer | 20.022 | false |
| c23 | execute_with_defaults | execute_with_defaults | 160.211 | false |
| c24 | execute_with_defaults | execute_with_defaults | 203.581 | false |
| c25 | execute_with_defaults | execute_with_defaults | 606.506 | true |
| c26 | clarify_private | clarify_private | 309.07 | true |
| c27 | clarify_private | clarify_private | 644.847 | true |
| c28 | clarify_private | clarify_private | 635.902 | true |
| c29 | clarify_private | execute_with_defaults | 457.727 | true |
| c30 | direct_answer | refuse | 27.905 | true |
| c31 | direct_answer | clarify_private | 13.795 | true |
| c32 | direct_answer | direct_answer | 22.844 | true |
| c33 | direct_answer | direct_answer | 17.858 | true |
| c34 | direct_answer | clarify_private | 460.801 | true |
| c35 | direct_answer | execute_with_defaults | 136.409 | false |
| c36 | refuse | clarify_private | 15.236 | true |
| c37 | refuse | execute_with_defaults | 214.197 | false |
| c38 | refuse | execute_with_defaults | 31.678 | false |
| c39 | refuse | execute_with_defaults | 21.237 | false |
| c40 | refuse | execute_with_defaults | 21.368 | false |
