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

## 2026-10-28 — todo 3: slot decision-value experiment (Wave 0)

- **Lane recipe**: `docker compose -p intake-slot -f OpencodeAgent/docker-compose.yml -f "$TMPDIR/lane-slot.yml" up -d`; the override uses `!override` for `ports` (4097:4096) and `volumes` (cron-state / cron-logs / vt-memory under `$TMPDIR/lane-slot`), with `deploy.resources.limits` at 3G / 1.2 CPU. Readiness `GET /config?directory=/workspace` returned 200 in 98s on `opencode-serve:v3.10.0-mymain` (amd64 under QEMU).
- **Recovery/abort split**: the CLI attach URL is `http://localhost:4096` inside the container, but recovery and abort from the host must use `http://127.0.0.1:4097` (`--recover-base`). `GET /doc` exposes `/session/{id}/message` and `POST /session/{id}/abort`.
- **Timeout handling**: 69 of 72 sessions hit the 240s per-run cap. Killing the CLI does not stop the server-side session; every timeout must call `/session/{id}/abort`, otherwise orphan agent loops keep running (observed sessions continuing ~500s). A janitor reaped stray `/opt/venv/bin/python` tool processes older than 300s.
- **Wall**: 72 runs recorded, 3 ok / 69 run_error / 0 incomplete; ok runs were 161.4s, 166.4s, 195.2s (mean 174.326s). The three ok runs completed by validating artifacts left by earlier runs in the shared `/workspace`; no cell reached two usable repeats. Recovery count 0 (no short-answer race).
- **Verdict headline**: all six slots INCONCLUSIVE; kill criterion not fired (0/6 default-worthy). The 240s per-run budget is the binding constraint, not an observed absence of slot effect. Todo 12 must not take a default set from this run; a re-run with a larger per-run budget (baseline used 1800s) is required.
- **Harness**: `run_slots.py` reuses `../run.py`'s `parse_stream` / `recover_stream`; `judge.py --write` emits every verdict table row and `--check` recomputes with zero tolerance; the `judge_spec.json` digest is recorded in `results.json` before the first run.

## 2026-10-28 — todo 6: clarification protocol moved to intake skill (Wave 1)

- **Moved**: `OpencodeAgent/AGENTS.md:66-125`（四类分支细节、漏斗、槽位清单、轮次预算、Step-Back 全文）下沉到 `OpencodeAgent/skills/intake/SKILL.md`；AGENTS.md 只保留四类分流表 + 三行指针，389 → 343 行（≤350）。
- **Single-source files**: 轮次规则唯一存在于 `skills/intake/SKILL.md`（`tests/test_agents_md.py` 扫 AGENTS.md + `skills/**/SKILL.md` 断言唯一命中）；行数预算唯一常量 `tests/test_config_render.py:AGENTS_MD_MAX_LINES = 350`，由 test_agents_md 直接 import。场景 C（`research-scenarios`）改为一行指针，intake 正文不含「场景 C」，消除互指。
- **Second-round exception (intentional semantic change)**: 默认 1 轮 ≤3 问；仅当用户明确答「不知道 / 不确定」时追加 1 轮且只问该槽位；第三次提问仍禁止。旧 AGENTS.md 原文禁止任何第二轮，故这是有意的语义变更，不是等价搬移。
- **Pending**: 验收项⑥（40 条语料四层分率重跑，点估计不降且 CI 不含显著恶化）**未执行** —— 机器被 todo 3 兄弟 lane 占用；todo 6 仅完成结构面验证（58 passed），`intake_loaded` 期望 >0 待下个 checkpoint 确认，不得声称已行为验证。

## 2026-10-28 — todo 3 v2: bounded-observation rerun
- **Bounded observation**: signature = first 3 distinct non-empty tool calls (ordered, canonicalised); `subprocess.Popen` stdout read line-by-line; kill the CLI process group at the 3rd call or 300s; `ok_partial` is usable; two lanes (`intake-slot-a` :4097, `intake-slot-b` :4099), one worker each.
- **Wall**: 72/72 runs usable (0 ok / 72 ok_partial / 0 run_error / 0 incomplete); mean 14.556s, median 9.213s; whole batch ~11 min.
- **Early stop vs natural**: 71/72 stopped at the 3-call threshold; 1/72 hit the 300s timeout with k=1 (`risk-0` r0); no natural completions.
- **Verdict**: all six slots INCONCLUSIVE; 24/24 cells show 3 distinct usable signatures, 0 cells with a majority; kill criterion not fired (0/6); todo 12 must not default any public slot. The cause is run-to-run variation in the first calls, not missing observations (v2 acceptance condition met).
- **v1 evidence**: void (69/72 hit 240s, 3 isolated signatures, 0 cell-level majorities), preserved in `v1_evidence/`; v2 commit `test(opencode-agent): rerun slot value experiment with bounded observation`.
- **Closing block (todo 8)**: canonical four prefixes are `我假设了什么：` / `换一个会怎样：` / `下一步能做什么：` / `仅供研究，不构成投资建议；数据截止 T-1。`; the compliance line requires `仅供研究` + `不构成投资建议` + `T-1` on one line and is not satisfied by "投资有风险".
- **Hook verdict**: opencode 1.19.3 `experimental.text.complete` (`@opencode-ai/plugin@1.19.3` `dist/index.d.ts:306-312`) receives the final text part and its returned `.text` is assigned back before `updatePart` — static call-site support for hard append; runtime probe unproven because throwaway plugins never loaded (`.mjs` glob miss, then `.js`/`file://` silent), so the memo says `static-only` and F-wave owns the load-path proof.
- **Contract location**: one canonical section `## 产物结尾契约（closing block）` at the end of `skills/research-scenarios/SKILL.md` (223 lines); scenarios A/B/B2/E/F each add one reference line (C untouched), and `skills/intake/SKILL.md` 执行承诺 item 4 adds the same reference plus "block is the artifact's ending, not its replacement".
- **Gate pending**: the 40-item ≥90% closing-block occurrence gate has NOT run here; `tests/evals/intake/checks.py` (`find_closing_block` / `assert_closing_block(required=False)` for text-only items / CLI exit 0-1-2) is ready for the eval lane, but no occurrence rate is claimed.
- **Command landing dir (todo 7)**: `config/command/` (singular) is confirmed at runtime — `GET /command?directory=/workspace` lists `checkup`/`screen`/`backtest`/`review` with `source: command` and description prefix `(opencode-project)`; the image path is `/workspace/.opencode/command/` via the new `COPY config/command/`.
- **Command list endpoint (todo 7)**: `/doc` advertises both `/command` and `/api/command`, but only `GET /command?directory=/workspace` is scoped to the workspace project — `/api/command` ignores `directory` and answers from the global project (`location.directory: "/"`, only `init`/`review`), so smoke discovers the path from `/doc` and always passes `directory`.
- **`/checkup` runtime outcome (todo 7)**: one `opencode run --attach http://127.0.0.1:4096 --dir /workspace --command checkup --format json "600519"` session (7m41s wall on QEMU amd64) produced a non-empty assistant reply: "I detect implementation/analysis intent — a structured stock checkup on 600519 (贵州茅台) requiring the scenario A workflow…".
- **Dev image (todo 7)**: `opencode-serve:onboarding-w1-89e398f5` (`sha256:e0c397f440b9…`, 4.15GB), built with `DOCKER_BUILDKIT=0` because buildkit tries to pull `opencode-serve-base:latest` from the mirror and gets `pull access denied` (the cached local base must be used); todo 11 rebuilds the delivery image.
- **question 权限枚举 (todo 10)**: `config/oh-my-openagent.json` 的 14 个 agent 均未声明 `mode`，故 primary 集合 = `{build, plan}`；测试仍动态枚举 `mode == "primary"`，未来新增 primary 未加 permission 会失败而不是静默漏过。
- **Golden 再生成 (todo 10)**: `DIRECT_MODE_GOLDEN` 必须先跑 renderer（清空 CLICKHOUSE_*/LLM_*/HOSTNAME 的 fixture 环境）拿到真实输出再回填，手改 golden 即使碰巧通过也是缺陷；本次差异仅在 `agent` 段新增 build/plan 的 `permission.question = "allow"`。
- **非交互限制 (todo 10)**: 模板无法表达「question 仅交互会话可用」，该限制写入 `skills/intake/SKILL.md` 执行承诺第 5 条（cron / headless `opencode run` / IM 不得调用 question），并由 `test_non_interactive_sessions_must_not_ask` 断言。

- **todo 9 removed block**: `OpencodeAgent/AGENTS.md:191-220` = `# 能力索引` heading + 29 行表（表头 + 分隔 + 27 数据行）；整表逐字迁至附录，与 `git show HEAD:OpencodeAgent/AGENTS.md` 逐行比对 29/29 identical、0 diff。
- **todo 9 appendix**: `OpencodeAgent/skills/research-scenarios/SKILL.md` 末尾新增 `## 附录：能力索引（按需查阅）`（第 225 行起；文件 223 → 257 行），表格行数 27，与冻结常量 `CAPABILITY_INDEX_DATA_ROWS = 27` 一致。
- **todo 9 AGENTS.md**: 343 → 328 行（≤350），`# 能力索引` 原位替换为 `# 产物索引（8 个具名产物）`（5 列 × 8 行）+ 兜底行；`pytest tests/test_agents_md.py tests/test_config_render.py` 81 passed。
- **todo 9 pending**: 40 条语料 harness behavioral regression（todo 6 同判据）**未运行**，由另一 lane 负责；本 todo 只做结构面验证，不得声称已行为验证。
- Delivery image: `opencode-serve:v3.11.0-onboarding-20261028-6e635820` (Id `sha256:7b6c5d1b2609…`, local-only, no repo digest) built from commit `6e635820`; baseline `v3.10.0-mymain` kept.
- Build gotcha: `DOCKER_BUILDKIT=0` is mandatory — BuildKit tries to pull the local-only `opencode-serve-base:latest` and dies with `pull access denied`; the legacy builder uses the local base cache.
- Smoke `tests/smoke-v311.sh` took ~5 min wall clock, 1 LLM session; `GET /config?directory=/workspace` first 200 at ~15 s, then `RUNTIME_COMMANDS_OK`, `CHECKUP_REPLY_OK`, `SMOKE_OK`.
- Todo 13 product→scenario map (AGENTS.md 产物索引): 个股体检→场景 A, 选股清单→场景 E, 策略回测→场景 B, 组合体检→组合（场景 A + risk-analysis）, 事件/宏观影响→场景 F, 交易复盘→场景 B2, 定时监控→场景 D, 报告→html-report.
- Two mapping targets are NOT OpencodeAgent skill dirs: `html-report` resolves to `OpencodeAgent/skills/html-report/SKILL.md`; `risk-analysis` is a VT built-in skill with no local OpencodeAgent dir — it resolves through the VT tree at `agent/src/skills/risk-analysis/SKILL.md` (repo root, loaded in-container via `vibe-trading_load_skill`) and is labelled `(VT)` in the research-scenarios capability appendix. Test header: `AGENTS.md` 329 lines after the change (budget 350).
- **todo 12 默认值表**: `skills/intake/SKILL.md` 六公共槽位（市场=A股 / 期限=1-3 个月 / 风险=最大回撤 10% / 风格=均衡 / 候选池=Top 10 / 持仓=空仓）+ 产出=结论+清单 全部 `不提问（默认值）`；只有成本价/仓位规模 `提问（私有、不可推断）`。
- **todo 12 工具名权威**: 编译表的工具名以运行时 `tools/list` 快照 `OpencodeAgent/tests/evals/intake/fixtures/tool_list.json`（94 个裸名）为准，`config/vibe-trading-tools.json` 只是 3 条 deny 的 disabled 清单、不得当注册表；opencode 调用面另加 `vibe-trading_` 前缀，schema `tool_plan.tool` 枚举 == 编译表工具集 == 快照子集。
- **todo 12 裁决落地**: 槽位价值实验 INCONCLUSIVE（24/24 cell 无多数签名，kill criterion 未触发）→ 无证据支持提问，按默认值执行；私有槽位保留提问。未新增实验；`tests/test_intake_brief.py` 13 passed，全量 `tests/` 147 passed。
- **todo 15 README 盘点**: `skills/` 实际 6 个目录（alphabetical: data-warehouse, escape-top-microstructure, html-report, intake, periodic-execution, research-scenarios），`config/command/` 4 个 md（checkup/screen/backtest/review）；README 计数与树同时改为 6，新增 Slash Commands 段声明运行时从 `/workspace/.opencode/command/` 发现。`OpencodeAgent/tests/test_readme_inventory.py` 离线钉住计数、目录名、命令名、intake 与报告链接。
- **todo 15 报告追加**: frontmatter 下插入 1 行 `已立项` blockquote，EOF 追加 §13（基线 6/10 route、0/10 intake/question、9/10 params；槽位 72/72 usable 但六槽全 INCONCLUSIVE；Wave 0 menu gate PASS 29.0%≤35%；hook 仅 `experimental.text.complete` static-only）；`results/wave1_report.md` 未提交，如实写「待 Wave 1 复跑（结果未出）」。`git diff --numstat` 777 additions / 0 deletions。
