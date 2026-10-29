# F1 计划符合性审计 — novice-onboarding-gprime

- 审计日期：2026-10-28
- 复审：2026-10-29（文末「Re-audit 2026-10-28」，被审 HEAD `ec22686e` / 49 commits；二次复核时 HEAD `4aab6cf4`（含上一版报告），`d6bbbd6c~1..HEAD` 50 commits）
- 审计范围：`.omo/plans/novice-onboarding-gprime.md` 的 todo 1–15、commit 粒度、DCO、AI trailer
- 分支：`mymain`
- 审计区间：`d6bbbd6c~1..HEAD`（45 个 commit）。任务书给的是 `4fcdad7e~1..HEAD`；该区间少算了一个 commit，把 todo 4 的 `d6bbbd6c` 排除在外。见 §6 G7。
- 只读审计：未修改任何产品文件；本报告是唯一写入物。

## 0. 方法与结论口径

- 每个 `[x]` 都对照文件和命令核验，不把 checkbox 当证据。
- 离线命令均在 `legonanobot` conda env 中执行；未启动容器、未构建镜像、未调用 LLM。
- DCO：`git log --format='%H %(trailers:key=Signed-off-by,valueonly)' d6bbbd6c~1..HEAD`。
- AI trailer：逐 commit 扫 `Co-Authored-By`、`AI-Model`、`AI-Contributed`、`Generated with` 及工具归属行；**零命中**。commit subject/正文里出现的 `opencode` / `OpencodeAgent` 是项目名和文件路径，不是归属 trailer。
- 证据命令：`pytest`、`run.py --dry-run`、`analyze_wave0.py --check`、`analyze_wave1.py --check`、`analyze_probe.py --check`、`judge.py --check`、`ab_analysis.py --check`。

## 1. 逐 todo 符合性

| todo | 交付物存在（路径） | Acceptance 证据（文件 / 命令） | commit(s) | 每 todo 一个 commit？ | verdict |
|---|---|---|---|---|---|
| 1 | `OpencodeAgent/tests/evals/intake/{corpus.jsonl,expected.json,events.schema.json,run.py,README.md,test_corpus_schema.py}` | `pytest tests/evals/intake/test_corpus_schema.py` → **19 passed**；`run.py --dry-run` 打印 10 条语料并 rc=0；`README.md` 记录两次运行 | `4fcdad7e`, `abcaf262` | 否（2：主提交 + harness 修正） | met |
| 2 | `results/baseline.jsonl`、`baseline_report.md`、`baseline_meta.json`、`fixtures/ask_first_protocol_snapshot.md`、`recompute.py`、`test_protocol_snapshot.py` | `baseline_meta.json`：`n_items=10`、`n_terminal=10`、`n_run_error=0`；`pytest tests/evals/intake/test_protocol_snapshot.py` → **6 passed**；快照 digest 冻结在 `abcaf262` | `aa3c2683`, `ce66b2b5` | 否（2：主提交 + 短会话恢复修正） | met |
| 3 | `slot_value/{matrix.json,results.json,verdict.md,judge_spec.json,judge.py,run_slots.py}`、`slot_value/v1_evidence/` | `judge.py --check`：**24 cells / 72 runs**，0 稳定 cell，6/6 槽位 `INCONCLUSIVE`，`kill criterion fired: False`（default-worthy 0/6，阈值 4/6） | `8d25e5d8`（v1）, `b0807c4c`（v2 重跑） | 否（2：v1 void + v2 重跑） | met（实验按预注册规则给出 INCONCLUSIVE；但**没有得到任何槽位裁决**，见 §6 G10） |
| 4 | `export_first_messages.py`、`results/request_labels.json`、`test_export_privacy.py` | `pytest tests/evals/intake/test_export_privacy.py` → **6 passed**；`request_labels.json` 只有 726 条的聚合计数/长度桶/注释，无原文；intake 目录 PII/secret 扫描无命中 | `d6bbbd6c` | 是 | met |
| 5 | `wave0_verdict.md`、`results/intake_load_rate.json`、`results/request_distribution.json`、`analyze_wave0.py`、`corpus40.jsonl`、`classifier_rubric.md` | `wave0_verdict.md`：菜单门 **PASS**（`都不是` 3/26 = 11.5%，Wilson 上界 29.0% ≤ 35%）；intake 触发 **0/40**，条件工作① deferred；分类器一致率 40/40 = 1.00。**初审时** `analyze_wave0.py --check` **FAIL（drift）**：发布值是 n=14/0-14，重算是 n=16/0-16（见 §6 B5）。**复审已修复**：v1 语料冻结回 `32f5e595~1`，六条 `--check` 全绿（见 R0） | `48371c41` | 是 | met（裁决仍成立；漂移已在复审前修复，属证据完整性问题） |
| 6 | `OpencodeAgent/skills/intake/SKILL.md`、`OpencodeAgent/AGENTS.md`、`skills/research-scenarios/SKILL.md`、`tests/test_agents_md.py` | `pytest tests/test_agents_md.py` → **37 passed**；`AGENTS.md` **329 行** ≤ 350；场景 C 为 ≤3 行单向指针；**40 条复跑 FAIL**（`wave1_report.md` attempt 2：route 3/10 vs 6/10，params 0/10 vs 9/10）；只有 **11 条 bounded probe PASS**（`wave1_frozen10_report.md`，10 个配对 id） | `b5d5a8cc`, `e0fc0cb6`, `2b615dae` | 否（3：主提交 + 40 条复跑 + instrument 修正） | **gap（计划现记 `[~]`，与 artifact 一致）**：Acceptance #6 要求 40 条语料重跑；40 条门仍 fail，11 条探针不能替代；但计划第 125 行已改 `[~]` 并逐项记录该 gap，checkbox 不再声称达标（见 R1） |
| 7 | `config/command/{checkup,screen,backtest,review}.md`、`Dockerfile:110`、`tests/smoke-v311.sh`、`results/image_delivery.md` | `pytest tests/test_config_render.py` → **49 passed**（命令文件存在性 + frontmatter schema）；`image_delivery.md` §5：`RUNTIME_COMMANDS_OK`（四个命令在 `GET /command?directory=/workspace` 列表中）、`CHECKUP_REPLY_OK`、`SMOKE_OK` | `ddbdd706` | 是 | met |
| 8 | `skills/research-scenarios/SKILL.md`、`skills/intake/SKILL.md`、`tests/evals/intake/checks.py`、`plugin_feasibility.md`、`tests/test_closing_block.py`、`results/wave1_frozen10_report.md` | `pytest tests/test_closing_block.py` → **18 passed**（结构契约）；字段证据：契约分母 **4/6 = 66.7%**（Wilson [30.0%, 90.3%]）、有产物分母 **4/5 = 80.0%**，均 < 90%；`c02` 600s 超时无产物，`c27` 把 `数据截止 T-1` 改写成具体日期 | `5ce2ca34`, `32f5e595`, `2eb53a82` | 否（3：契约 + bounded probe + 计分修正） | **gap（`[~]`，与 artifact 一致）**：4/6 = 66.7%、4/5 = 80%，阈值 90%，未达标；计划第 141 行 `[~]` 已记该 gap、`c02`/`c27` 细节与 `2eb53a82`，并登记 F-wave 硬追加插件（见 R1） |
| 9 | `AGENTS.md` 产物索引、`skills/research-scenarios/SKILL.md` 附录「能力索引（按需查阅）」、`tests/test_agents_md.py` | `pytest tests/test_agents_md.py` → **37 passed**：8 行产物索引、无能力索引表、附录 27 行 ±2、`AGENTS.md` 329 行；但 harness 无回归沿用 todo 6 判据 → 40 条门 fail，仅 11 条探针 pass | `6e635820`, `e0fc0cb6`, `2b615dae` | 否（3：主提交 + 复跑 + 修正） | **gap（计划现记 `[~]`，与 artifact 一致）**：同 todo 6 的 40 条 Acceptance；计划第 150 行已改 `[~]`，结构断言（37 passed）不变（见 R1） |
| 10 | `config/opencode.json.tmpl`、`.env.example:49`、`tests/test_config_render.py`、`results/question_tool_probe.md` | `pytest tests/test_config_render.py` → **49 passed**：每个用户可选 primary agent 的 `permission.question == "allow"`，subagent 从不 allow；`.env.example` 含 `OPENCODE_ENABLE_QUESTION_TOOL=`；headless `opencode run` 的 `question` deny 已由 `question_tool_probe.md` 实测并记录 | `2452260c` | 是 | met（模板面达标；运行时 question 在 headless 不可用，属计划允许的记录限制） |
| 11 | `results/image_delivery.md`、`results/image_build.log`、`tests/smoke-v311.sh` | 镜像 tag `opencode-serve:v3.11.0-onboarding-20261028-6e635820`；Image `Id sha256:7b6c5d1b…`；`image_build.log` 648 行；`SMOKE_OK`；**无 `RepoDigest`**（本地构建、从未 push），交付文档明确写「不要编造 digest」 | `967968cd` | 是 | met（偏差：没有 repo digest；用 Image Id 作内容身份并已记录，见 §6 G2） |
| 12 | `skills/intake/SKILL.md`、`tests/evals/intake/brief.schema.json`、`tests/test_intake_brief.py`、`brief_validation.py`、`fixtures/tool_list.json` | `pytest tests/test_intake_brief.py` → **13 passed**；槽位默认值表（六公共槽位 INCONCLUSIVE → 默认化，仅成本价/仓位规模提问）+ 选项→工具参数编译表；编译表工具名逐行在 `tool_list.json` 快照中存在 | `7d606d3e` | 是 | met |
| 13 | `AGENTS.md` 产物索引（场景/Skill 列 + 菜单外兜底行）、`tests/test_agents_md.py` | `pytest tests/test_agents_md.py` → **37 passed**：8 行映射逐格断言、兜底行存在、`AGENTS.md` ≤350 | `f3d39b40` | 是 | met |
| 14 | `tests/evals/intake/ab/{corpus_ab.jsonl,run_ab.py,ab_analysis.py,ab_report.md,judge_spec.json}`、`tests/test_ab_assets.py` | `pytest tests/test_ab_assets.py` → **7 passed**；`ab_analysis.py --check` **OK**；`ab_report.md`：n_pairs=**10**、eligible=**7**、p=**1.0**、verdict=**INCONCLUSIVE**（n_min=30，需 55 pairs） | `6ec79a9a` | 是 | **gap（`[~]`，与 artifact 一致）**：Acceptance 要求配对样本 ≥30；实际 n_pairs=**10**、eligible=**7**、verdict=**INCONCLUSIVE**、required N=**55**。计划 QA-failure 要求「INCONCLUSIVE **并升级**」：第 196 行已改 `[~]`，执行记录第 8 条记录升级（30 对 / 60 会话 / 约 5 小时，待操作者裁决），`[~]` 不再过报（见 R9） |
| 15 | `OpencodeAgent/README.md`、`mymain-wiki/agent-ux/2026-10-27-novice-onboarding-guidance.md`、`tests/test_readme_inventory.py` | `pytest tests/test_readme_inventory.py` → **8 passed**；README 6 skills / 4 commands；报告第 11 行有「已立项」；`git show --numstat ee620577 -- <report>` = **777 0**（0 deletions）；但 §13.5 仍写 `wave1_report.md` 尚未提交（实际已提交），见 §6 G6 | `ee620577` | 是 | met（README/链接/追加段达标；报告 §13.5 过期，计划 checkbox 仍为 `[ ]`） |

### 判定口径说明

> 复审更新（2026-10-29，见文末「Re-audit 2026-10-28」）：第 6、8、9、14 行的 verdict 已按当前 checkbox 状态改写；其余 11 行保持初审结论，本次未重跑。

- `met` = 交付物存在，Acceptance 的关键断言有可执行证据；括注的偏差不改变该 todo 的交付。
- `gap` = 交付物存在，但计划写明的 Acceptance 没有被满足，或只能靠一个计划未授权的替代测量成立。
- 初审时 todo 8 是唯一显式标 `[~]` 的行；todo 6/9/14 是 `[x]` 但 Acceptance 未满足，这正是初审要暴露的差异。复审时 6/8/9 已改为 `[~]`，14 仍为 `[x]`。

## 2. Wave 1 真实数字（计划要求 vs 实际测量）

`results/wave1_frozen10_report.md` 的 gate verdict 必须和 `results/wave1_report.md` 的 40 条门一起读，否则会把 11 条探针误当成 40 条复跑。

| 证据 | 样本 | todo 6 `todo6_behaviour` | todo 8 `todo8_closing_block` | todo 9 `todo9_no_regression` |
|---|---|---|---|---|
| `results/wave1_report.md` §1 attempt 1 | 40 条语料，39/40 complete | **fail**（route 4/10 vs 6/10；params 5/10 vs 9/10） | **fail**（9/13 = 69.2%） | **fail**（同 todo 6） |
| `results/wave1_report.md` §2 attempt 2（fix1，final 40-item gate） | 25 measured / 40 records，20 run errors | **fail**（route 3/10 vs 6/10；params 0/10 vs 9/10） | **fail**（0/20 = 0.0%） | **fail**（同 todo 6） |
| `results/wave1_frozen10_report.md` §6（bounded probe） | 11 条（10 paired + `c27`） | **pass**（route 7/10 vs 6/10；intake 8/10 vs 5/10；question 8/10 vs 8/10；params 8/10 vs 6/10） | **fail**（4/6 = 66.7%；有产物分母 4/5 = 80.0%） | **pass**（同 todo 6） |

- `wave1_frozen10_report.md` §7 自己写明：**“This is an 11-item bounded probe, not the 40-item gate.”**
- todo 8 的 `[~]` 行（计划第 141 行）记录的数字与 `wave1_frozen10_report.md` §3 一致：`4/5 = 80%`、`4/6 = 66.7%`、阈值 90%；`c27` 改写了 `数据截止 T-1`，`c02` 600s 超时未产出产物。**该 gap 是诚实的，没有任何 artifact 声称 90% 达标。**
- 预算：`wave1_frozen10_report.md` §5 记录 tokens **−2.5%**（pass）、wall **+40.6%**（阈值 ≤10%，**fail**）。计划第 63 行要求涨幅 >10% 触发复核；没有看到复核记录（见 §6 G9）。

## 3. Commit 审计（45 个）

DCO 命令：

```bash
git log --format='%H %(trailers:key=Signed-off-by,valueonly)' d6bbbd6c~1..HEAD
```

结果：**39 个非 merge commit 全部非空**（`shadowinlife <shadowinlife@gmail.com>`）；6 个 merge 中 `d90d5308`、`585845ad`、`6f3e82e9` 为空，`9042d270`、`9033b16a`、`ed53eb26` 有 Signed-off-by。按任务口径，merge 一律记 `n/a (merge)`。AI trailer：**0**。

| sha | subject | Signed-off-by | AI trailer | 归属 | note |
|---|---|---|---|---|---|
| `20f269ec` | feat(strategy-runs): add RM service routes, token auth and run ledger | yes | none | external | strategy-run/state 线 |
| `9c1a9433` | feat(strategy-runs): persist run status with sweeper and restart recovery | yes | none | external | 同上 |
| `20b731ea` | feat(opencode-agent): supervise the strategy-run entry in the image | yes | none | external | 外部 feature，非本计划 todo |
| `08975bd8` | feat(strategy-runs): reject cross-box targets before claiming a run_key | yes | none | external | 同上 |
| `e373076e` | fix(gitignore): ignore the strategy-run ledger | yes | none | external | 同上 |
| `7f67ace8` | docs(f9): mark the P1 strategy-run entry delivered | yes | none | external | F9 文档，非本计划 |
| `61c9a436` | feat(strategy-state): add vt-state-v1 export/import and live-handle reopen | yes | none | external | state 线 |
| `f3475546` | feat(agent-schedules): relay scheduling to the RM control plane | yes | none | external | schedule 线 |
| `d6bbbd6c` | test(opencode-agent): add privacy-preserving first-message exporter | yes | none | todo 4 | **真正的首个 plan commit**；被任务书的 `4fcdad7e~1` 排除 |
| `4fcdad7e` | test(opencode-agent): add intake eval harness with frozen corpus and event schema | yes | none | todo 1 | 任务书区间的起点 |
| `abcaf262` | fix(opencode-agent): scope intake expectation and pin eval working directory | yes | none | todo 1 (fix) | review fix；见 §5 |
| `aa3c2683` | test(opencode-agent): record intake baseline and freeze ask-first protocol snapshot | yes | none | todo 2 | — |
| `ce66b2b5` | fix(opencode-agent): recover short-run transcripts from the server session API | yes | none | todo 2 (fix) | review fix；baseline 从 3 条丢失修复到 10/10 |
| `48371c41` | test(opencode-agent): wave-0 verdict on trigger rate and menu coverage | yes | none | todo 5 | — |
| `8d25e5d8` | test(opencode-agent): add slot decision-value experiment with scripted judging | yes | none | todo 3 (v1) | v1 69/72 run_error，后被 v2 取代 |
| `b5d5a8cc` | refactor(opencode-agent): move clarification protocol from AGENTS.md to intake skill | yes | none | todo 6 | 主提交 |
| `b0807c4c` | test(opencode-agent): rerun slot value experiment with bounded observation | yes | none | todo 3 (fix) | v2；72 次新会话 |
| `ddbdd706` | feat(opencode-agent): add slash commands as deterministic discovery layer | yes | none | todo 7 | — |
| `5ce2ca34` | feat(opencode-agent): mandate assumptions/diff/next-step/disclaimer closing block | yes | none | todo 8 | 主提交 |
| `2452260c` | feat(opencode-agent): enable question tool for primary agents in rendered config | yes | none | todo 10 | — |
| `6e635820` | refactor(opencode-agent): replace capability index with product index in AGENTS.md | yes | none | todo 9 | 主提交 |
| `967968cd` | chore(build): record onboarding image delivery | yes | none | todo 11 | — |
| `f3d39b40` | docs(opencode-agent): add product-to-scenario mapping and fallback routing | yes | none | todo 13 | — |
| `7d606d3e` | feat(opencode-agent): complete intake skill contract with defaults and compile table | yes | none | todo 12 | — |
| `ee620577` | docs: link onboarding plan and refresh skill/command inventory | yes | none | todo 15 | 报告 777/0；§13.5 过期 |
| `e0fc0cb6` | test(opencode-agent): wave-1 behavioural rerun against the delivered image | yes | none | todo 6/8/9 (evidence) | 40 条 attempt 1；非独立 todo |
| `2b615dae` | fix(opencode-agent): correct route classifier and open-ended branch, rerun wave-1 gate | yes | none | todo 6/8/9 (fix) | instrument 修正 + 40 条 attempt 2；见 §5 |
| `ed53eb26` | Merge mymain into feat/strategy-run-entry | n/a (merge) | none | external | merge（有 DCO） |
| `3d7563b8` | fix(strategy-runs): run the opt-in entry service and assert routes via OpenAPI | yes | none | external | strategy-run 线 |
| `c54e26b5` | chore(ci): regenerate the Windows lock and test mymain PRs | yes | none | external | PR #5 CI；见 §4 |
| `3eaff213` | fix(agent-schedules): read RM's detail 409 and report legacy directories | yes | none | external | schedule/state 线 |
| `49b370c0` | fix(state): report reopen, generation and truncation honestly | yes | none | external | state 线 |
| `169bc891` | docs: sync the MCP tool surface with control-plane scheduling | yes | none | external | 外部文档 |
| `9033b16a` | Merge mymain into feat/strategy-state-and-agent-tools | n/a (merge) | none | external | merge（有 DCO） |
| `eea86061` | test(ci): isolate the safety gate and confine os patches per module | yes | none | external | PR #5 CI |
| `508e8a55` | ci: run the safety gate in its own job, not the test leg | yes | none | external | PR #5 CI |
| `ddad9a05` | ci: give mymain's larger Python suite a 60-minute ceiling | yes | none | external | PR #5 CI |
| `6f3e82e9` | Merge pull request #5 from shadowinlife/chore/0009-windows-lock | n/a (merge) | none | external | PR #5 merge；见 §4 |
| `585845ad` | Merge pull request #3 from shadowinlife/feat/strategy-run-entry | n/a (merge) | none | external | PR #3 merge |
| `9042d270` | Merge mymain into feat/strategy-state-and-agent-tools | n/a (merge) | none | external | merge（有 DCO） |
| `d90d5308` | Merge pull request #4 from shadowinlife/feat/strategy-state-and-agent-tools | n/a (merge) | none | external | PR #4 merge |
| `bb27b8d4` | fix(release): harden the strategy-run entry after the unified review | yes | none | external | 外部 release review；触碰 `AGENTS.md` 工具数行，见 §6 G11 |
| `32f5e595` | test(opencode-agent): bounded wave-1 probe with corrected private-product labels | yes | none | todo 8 (probe) + corpus 修正 | c19/c22 label 修正 + 11 条探针；见 §5 |
| `2eb53a82` | fix(opencode-agent): score the wave-1 probe gate on agreement, not observed-true | yes | none | todo 8 (fix) | 计分修正；见 §5 |
| `6ec79a9a` | test(opencode-agent): add ask-first vs artifact-first A/B apparatus with pilot verdict | yes | none | todo 14 | 与计划写的 commit message 不同；pilot 非完整裁决 |

### 计数

- 总 commit：**45**（39 非 merge + 6 merge）。
- plan commit：**22**（含 `d6bbbd6c`；不含任务书区间遗漏的那一个）。
- external commit：**23**（17 非 merge + 6 merge）。
- DCO：39/39 非 merge 有；merge 3 有 / 3 无，均按 `n/a (merge)`。
- AI trailer：**0**。
- 合并提交里 `6f3e82e9`、`585845ad`、`d90d5308` 无 DCO；按任务口径不构成违规。

## 4. 外部历史核验

任务书说 `6f3e82e9` 是 PR #5 + 四个 CI commit（`ddad9a05`、`508e8a55`、`eea86061`、`c54e26b5`），不属于本计划。核验：

- `6f3e82e9` 的 parents 是 `2238efd0`（plan 前的 mymain）和 `ddad9a05`。
- `ddad9a05` → `508e8a55` → `eea86061` → `c54e26b5`；四者的 diff 只碰 `.github/workflows/*`、`desktop/electron/requirements-windows-lock.txt`、`agent/tests/test_backtest_progress.py`，**没有碰** `OpencodeAgent/tests/evals/intake/`。
- 因此四个 CI commit 与 `6f3e82e9` 记为 **external**；`6f3e82e9` 同时是把 plan 分支带入 mymain 的集成点，但不是 plan todo commit。
- 其余 external：`20f269ec`、`9c1a9433`、`20b731ea`、`08975bd8`、`e373076e`、`7f67ace8`、`61c9a436`、`f3475546`、`3d7563b8`、`3eaff213`、`49b370c0`、`169bc891`、`bb27b8d4`，以及 merge `ed53eb26`、`9033b16a`、`9042d270`、`585845ad`、`d90d5308`。这些提交的 diff 都落在 strategy-run/state/schedule/CI/release 面，不是本计划的 15 个 todo。
- `bb27b8d4` 是唯一的边界情况：它改了 `OpencodeAgent/AGENTS.md` 的工具数行（89/94 → 94/99），但不触碰 intake 协议、产物索引或行数预算；`AGENTS.md` 仍是 329 行。**不构成本计划的交付，也不构成协议回退。**

## 5. Review 驱动的额外 commit

计划要求“每个 todo 一个 commit”。实际 22 个 plan commit 覆盖 15 个 todo：15 个主提交 + 6 个 review 驱动的额外 commit + 1 个 40 条复跑测量提交（`e0fc0cb6`）。逐一判定 6 个 review 驱动的额外 commit：

| sha | 修复的 todo | 内容 | 判定 |
|---|---|---|---|
| `abcaf262` | todo 1 | 给 `expect` 加 scope、固定 eval 工作目录 | **justified review fix**：harness 自身正确性；否则语料期望和 cwd 不可复现 |
| `ce66b2b5` | todo 2 | 从 server session API 恢复短会话 | **justified review fix**：baseline 从丢失 3 条修复到 10/10；否则 todo 2 Acceptance 不成立 |
| `b0807c4c` | todo 3 | v1 全部 void（69/72 run_error）→ v2 有界观察 | **justified methodology fix**：v2 预注册、判定规则不变；但新增 72 次会话（预算问题，见 §6 G5） |
| `2b615dae` | todo 6/8/9 | 修 route classifier 顺序、open-ended 分支、artifact guard，并跑 40 条 attempt 2 | **justified instrument fix**，但它是 eval commit 里的行为变更，且 40 条门仍然 fail；不能据此把 todo 6/9 记成 met |
| `32f5e595` | todo 8 | c19/c22 label 修正 + 11 条 bounded probe | **不是 review fix，是 scope substitution**：把 40 条门替换为 11 条探针；同时在发布 wave0/wave1 报告之后改了 `corpus40.jsonl`，导致两份报告 `--check` 漂移（§6 B5）。这一条必须显式批准，不能当作 40 条 Acceptance 的证据 |
| `2eb53a82` | todo 8 | gate 计分从 observed-true 改为 agreement | **justified methodology fix**：observed-true 会奖励过度执行；修正后仍如实给出 fail |

另外 `e0fc0cb6` 不是 review fix，是 40 条 attempt 1 的测量提交；`d6bbbd6c` 是 todo 4 的主提交，只是被任务书区间漏掉。两者都不是粒度违规，但说明“一个 todo 一个 commit”在 Wave 1/2 实际没有字面成立。

## 6. Gap 与风险

### B1. Todo 6 Acceptance #6 未满足：40 条复跑被 11 条探针替代（blocking）

- 计划第 128 行：`harness 用 40 条语料（非 n=10）重跑，四层分率相对基线点估计不降且 CI 不含显著恶化`。
- `results/wave1_report.md` §2（final 40-item gate）：40 records / 25 ids，**5/25 complete，20 run errors**；`todo6_behaviour` **fail**（route 3/10 vs 6/10；params 0/10 vs 9/10）。
- `results/wave1_frozen10_report.md` §6：`todo6_behaviour` **pass**，但 §7 明说这是 **11-item bounded probe, not the 40-item gate**。
- 结论：`[x] 6` 与 artifact 不一致。bounded probe 可以作为补充证据，不能替代计划写死的 40 条 Acceptance。

### B2. Todo 9 Acceptance #4 未满足：同一个 40 条判据（blocking）

- 计划第 155 行：`harness 无回归（同 todo 6 判据）`。
- 40 条 attempt 2：`todo9_no_regression` **fail**（同 todo 6 的 3/10 vs 6/10、params 0/10 vs 9/10）。
- 11 条探针：`todo9_no_regression` **pass**。
- 结论：`[x] 9` 的 40 条部分不成立；结构断言（8 行产物索引、附录 27±2、无能力索引表、329 行）是成立的，但“无回归”只在 11 条上成立。

### B3. Todo 14 Acceptance #1 未满足：配对样本 10 < 30（blocking）

- 计划第 199 行：`配对样本 ≥30`。
- `ab_report.md` §3.3/§6：n_pairs=**10**，eligible=**7**，p=**1.0**，required N=**55**，verdict=**INCONCLUSIVE**。
- 计划第 200 行的 QA failure 允许 `INCONCLUSIVE 并升级`；报告记录了 INCONCLUSIVE 和重跑命令，但**没有升级记录**（没有用户裁决、没有新 todo）。commit subject 也写的是 `pilot verdict`，不是计划里的 `paired A/B verdict`。
- 结论：`[x] 14` 不成立；A/B 装置是完整的，裁决不是。

### B4. Wave 1 出口门被跳过（blocking）

- 计划第 65 行：`Wave 1 完成后必须用 40 条语料做“捆绑包 vs 基线”配对对照，显著改善（或至少非劣 + 触发率达标）才进入 Wave 2`。
- 实际顺序：`2b615dae`（40 条 attempt 2，三门前部 fail）→ `7d606d3e`/`ee620577`/`f3d39b40`（Wave 2 的 todo 12/15/13）→ `32f5e595`/`2eb53a82`（11 条探针）。Wave 2 在 40 条门 fail 之后继续执行。
- 结论：这是计划层面的门禁违规，不只是某个 todo 的 Acceptance 偏差。

### B5. 证据可复算性漂移（blocking，影响 todo 5 与 Wave 1 报告）

- `conda run -n legonanobot python tests/evals/intake/analyze_wave0.py --check` → **FAIL**：`[drift] results/intake_load_rate.json, results/request_distribution.json, wave0_verdict.md`。
- `conda run -n legonanobot python tests/evals/intake/analyze_wave1.py --check` → **FAIL**：`--check: report drift detected; rerun analyze_wave1.py --write`。
- 在 HEAD 的临时副本里重算：wave0 的 intake 子集从 **n=14 / 0-14** 变为 **n=16 / 0-16**（Wilson 上界 21.5% → 19.4%）；wave1 attempt 1 的 intake 子集从 **10/14** 变为 **12/16**，attempt 2 从 **1/10** 变为 **3/12**；`request_distribution.json` 的 `corpus_sha256` 从 `69bb6f25…` 变为 `65fe92e8…`。
- 根因：`32f5e595` 在 wave0/wave1 报告提交之后修改了 `corpus40.jsonl` 的 c19/c22 标签（`execute_with_defaults` → `clarify_private`），但没有重新生成 `wave0_verdict.md`、`intake_load_rate.json`、`request_distribution.json`、`wave1_report.md`。`32f5e595` 的 body 说“regeneration keeps head -10 byte-identical”，但没有做这两份报告的重生成。
- 影响：菜单门结论（3/26，上界 29.0%）仍成立，intake 触发结论（0/40，deferred）也仍成立；但**发布数字与当前输入不一致**，`--check` 这条“数字可复算”的保证在 HEAD 失效。
- 通过的命令：`analyze_probe.py --check`、`judge.py --check`、`ab_analysis.py --check` 均 OK；所以最终选择的 11 条探针本身是可复算的，失效的是被替代的 40 条/ Wave 0 发布件。

### G1. Todo 8 的 `[~]` 是诚实的

- 4/6 = 66.7%（Wilson [30.0%, 90.3%]），4/5 = 80.0%，阈值 90% → fail。
- `c02`：600s 上限触发，session 在 tool loop 中被截断，没有产物；按契约计为 miss。
- `c27`：有产物，但把 `数据截止 T-1` 改写成 `数据截止 2026-10-28`；四行 canonical 全缺，属 contract-compliance miss，不是 routing miss。
- `plugin_feasibility.md` 记录 `experimental.text.complete` 可改 `output.text`，但运行时插件加载未证明；硬追加插件登记为 `F-wave: closing-block hard-append plugin`。**没有 artifact 声称 90% 达标。**

### G2. Todo 11 没有 repo digest（已记录，非阻塞）

- `image_delivery.md` §1：`RepoDigests` 为 `[]`，因为镜像本地构建、从未 push；文档明确写“不要编造 digest”，用 Image `Id sha256:7b6c5d1b…` 作内容身份。
- 计划第 169 行写“构建日志与 digest 记录”；严格字面上 digest 缺位。tag（`v3.11.0-onboarding-20261028-6e635820`，非 `:latest`）、Image Id、648 行构建日志、`SMOKE_OK` 都在。记为**已记录的偏差**，不是伪造。

### G3. Todo 10 的 question 运行时限制（已记录）

- `question_tool_probe.md`：容器环境 `OPENCODE_ENABLE_QUESTION_TOOL=1`，但 `opencode run` 创建的 session 仍带 `{"permission":"question","action":"deny"}`；`question_called=false`，agent 用文本选择题。
- 计划 todo 10 的 Acceptance 允许“若模板表达不了，记录为限制并在 intake skill 写明”；`skills/intake/SKILL.md` 的「执行承诺」第 5 条和「加载机制」都写了非交互会话不得调用 question。
- 模板面 met；IS-3 的运行时 question 能力**未被证明**，属 F4 的范围。

### G4. 见 B5（证据漂移）

### G5. 计划级 LLM 会话预算超限（blocking risk）

- 计划第 63 行：全计划 LLM 评测会话上限 **150** 次，超限即停并报告；Wave 0 约 80、Wave 1 约 40、Wave 2 约 30。
- 实际下限：
  - baseline：`baseline_meta.json` `sessions_used_total=20`；
  - Wave 0 触发率：40；
  - slot：v1 **72**（`v1_evidence/results.json`：69 run_error + 3 ok）+ v2 **72**（`results.json`）= **144**；
  - Wave 1：attempt 1 **40** + attempt 2 **25** + probe **11** + two-turn **1** = **77**；
  - smoke：delivery `/checkup` **1** + question probe **1** = **2**。
- 合计 **≥283** 次，约为上限的 1.9 倍；即使把 v1 的 72 次全部剔除，仍有 **≥211** 次。没有看到“超限即停并报告”的 artifact。
- 这与 40 条复跑“跑不动”是同一件事的两面：预算在 Wave 0 就被 slot v1+v2 吃掉，Wave 1 只能退到 11 条探针。

### G6. Todo 15 的报告 §13.5 已过期（非阻塞，但文档收口不完整）

- `ee620577` 在 `mymain-wiki/agent-ux/2026-10-27-novice-onboarding-guidance.md` §13.5 写：`40 条 Wave 1 复跑结果未出：results/wave1_report.md 尚未提交。待 Wave 1 复跑（结果未出）`。
- 在 HEAD，`results/wave1_report.md` 已由 `e0fc0cb6`/`2b615dae` 提交，`results/wave1_frozen10_report.md` 也已提交；§13.5 没有追加更新。
- `ee620577` 的 numstat 是 **777 0**（0 deletions），满足“报告 commit 0 deletions”；但该文件是这次 commit 才首次入库的，所以“append-only”只能在“新增 777 行、删除 0 行”的意义上确认，不能和更早的 tracked 版本做 diff。
- 计划 todo 15 的 checkbox 仍是 `[ ]`；README 计数/链接/追加行本身通过 `test_readme_inventory.py`（8 passed）。这是状态滞后 + 文档过期，不是代码回归。

### G7. 审计区间少算一个 commit（审计口径）

- 任务书写 `4fcdad7e~1..HEAD`。`4fcdad7e~1` 是 `d6bbbd6c`，而 `d6bbbd6c` 正是 todo 4 的提交（`test(opencode-agent): add privacy-preserving first-message exporter`）。
- 本次审计用 `d6bbbd6c~1..HEAD`（45 个 commit），把 todo 4 纳入；否则 todo 4 的 DCO/交付物无人核对。
- 这不影响任何 verdict，但影响“首个 plan commit”的认定：真正的首个 plan commit 是 `d6bbbd6c`，不是 `4fcdad7e`。

### G8. 计划顺序与 commit 顺序不一致（非阻塞）

- 计划 §Execution strategy：`1 → {2,3} 并行；4 → 5`。
- 实际 todo 4（`d6bbbd6c`）先于 todo 1（`4fcdad7e`）提交；todo 3 的 v2（`b0807c4c`）在 todo 6（`b5d5a8cc`）之后。没有依赖倒置导致的返工，但“按计划顺序执行”不成立。

### G9. Wall-clock 预算门未复核（非阻塞风险）

- `wave1_frozen10_report.md` §5：tokens **−2.5%**（pass），wall **+40.6%**（阈值 ≤10%，fail）。
- 计划第 63 行：任一波次均值相对基线涨幅 >10% 触发复核。没有看到复核记录；报告只记了 fail，没有后续动作。

### G10. Todo 3 没有产生任何槽位裁决（非阻塞，但削弱 IS-3）

- 6 个公共槽位全部 `INCONCLUSIVE`；24/24 cell 无多数签名；72/72 为 `ok_partial`（71 次观察到第 3 个调用后主动停止，1 次 300s 超时）；kill criterion 未触发（default-worthy 0/6）。
- 计划 todo 3 的预注册规则允许 INCONCLUSIVE；`verdict.md` 和报告 §13.2 都如实写了“没有证明哪些槽位可默认化，也没有证明哪些必须提问”。
- todo 12 的默认值表因此是**产品判断**，不是实验授权；报告 §13.2 明确承认这一点。这不算 todo 3 的 Acceptance 违规，但 IS-3 的证据强度要按“未达成”理解。

### G11. 外部 commit 触碰计划文件（非阻塞）

- `bb27b8d4` 改了 `OpencodeAgent/AGENTS.md` 的工具数行（89/94 → 94/99），属于外部 strategy-run 交付。它没有触碰 intake 协议、产物索引、行数预算；`AGENTS.md` 仍 329 行。记录为边界情况，不构成计划交付。

### G12. 既有 `scp` 文档/脚本（超出本次 diff，仓库级风险）

- 根 `AGENTS.md` §9 禁止 `scp`；但 `OpencodeAgent/skills/html-report/docs/nginx-reports-setup.md` 和 `OpencodeAgent/skills/html-report/scripts/reports/deploy_report.py` 仍含 `scp`，且本计划区间（初审 45、复审 49 个 commit）没有触碰它们（该文件最后一次变更是 `b83e0c4c`）。
- 这是先于本计划的既有违规，不是 F1 的 plan-conformance blocker；但若按根规则执行，应在后续独立清理，不应借本次审计修改。

## 7. F1 的边界

**F1 只是计划符合性审计，不能单独证明计划正确。** F1 不替代：

- **F2**：`pytest OpencodeAgent/tests/` 全量、`test_agents_md.py` 结构断言、`docker compose config` 校验；
- **F3**：容器全量构建后 40 条语料 + 4 个 `/` 命令运行时触发断言、四层分率相对基线、token/延迟 ≤10%；
- **F4**：IS-1..IS-6 的逐条保真（IS-1 的可测代理指标、IS-2 的 closing block 阈值、IS-3 的 question/私有槽位、IS-4 的埋点、IS-5 的单份协议、IS-6 的 kill criteria）。

F1 的 `met` 只表示“该 todo 的交付物和 Acceptance 证据在仓库里能对上”，不表示行为效果达标。尤其是 todo 3/5/6/8/9/14 的 `met`/`gap` 与 F3 的 40 条行为门是两套判断；F1 已经把 todo 6/9 的 40 条门 fail 单独列出，F3 不应把它当作已通过。

## 8. 总体裁决（初审，已被文末「Re-audit 2026-10-28」取代）

**VERDICT: REJECT**

Blocking findings：

1. **Todo 6** 的 Acceptance #6 要求 40 条语料复跑；最终 40 条门（`wave1_report.md` attempt 2）`todo6_behaviour` **fail**，只有 11 条 bounded probe **pass**。`[x] 6` 不能成立。
2. **Todo 9** 的 Acceptance #4 沿用 todo 6 判据；40 条门 `todo9_no_regression` **fail**，只有 11 条探针 **pass**。`[x] 9` 不能成立。
3. **Todo 14** 的 Acceptance #1 要求配对样本 ≥30；实际 n_pairs=**10**、eligible=**7**，verdict=**INCONCLUSIVE**，且没有升级记录。`[x] 14` 不能成立。
4. **Wave 1 出口门被跳过**：计划第 65 行要求 40 条配对对照通过后才进入 Wave 2；实际 40 条门 fail 后仍执行了 Wave 2 的 todo 12/13/15。
5. **证据漂移**：`analyze_wave0.py --check` 与 `analyze_wave1.py --check` 在 HEAD 均 FAIL；`32f5e595` 改了 `corpus40.jsonl` 的 c19/c22 标签却没重新生成 wave0/wave1 报告，发布数字（n=14）与重算（n=16）不一致。
6. **预算超限**：计划上限 150 次 LLM 会话；实际下限 ≥283 次（baseline 20 + wave0 40 + slot 144 + wave1 77 + smoke 2），没有“超限即停并报告”的 artifact。

非阻塞但需记录的偏差：todo 8 的 `[~]` 诚实（4/6=66.7%，4/5=80%，<90%）；todo 11 无 repo digest（本地镜像，已记录 Image Id）；todo 10 的 question 在 headless 被 deny（已记录限制）；todo 3 六槽全 INCONCLUSIVE（预注册规则允许，但无槽位裁决）；todo 15 报告 §13.5 过期；任务书审计区间漏掉 `d6bbbd6c`；`bb27b8d4` 外部 commit 触碰 `AGENTS.md` 工具数行；既有 html-report 文档仍含 `scp`。

DCO 与 AI trailer 本身干净：39/39 非 merge commit 有 `Signed-off-by`，0 个 AI trailer；3 个 merge 无 DCO，按任务口径 `n/a (merge)`。**REJECT 的原因是 Acceptance/门禁/证据完整性，不是 DCO。**

> 本初审的 REJECT 结论与上述 6 条 blocking findings 已被文末「Re-audit 2026-10-28」逐条重判；保留原文以保持审计可追溯。

## Re-audit 2026-10-28

> 标题按复审轮次日期（2026-10-28）；实际执行为 2026-10-29（UTC+8）。复审中途 HEAD 从 `2b4d57c0` 前进到 `ec22686e`（F2 的 smoke 修复落地），本节数字以收尾时重读的 `git log` 为准。

- 复审区间：`d6bbbd6c~1..HEAD`，被审 HEAD **`ec22686e`**，**49 个 commit**（43 非 merge + 6 merge）。初审为 45 个；新增 4 个：`8c4feddf`、`0093e625`、`2b4d57c0`、`ec22686e`。本报告自身的 commit 不在被审集合内。
- 只读：本次复审只写本报告；未改代码、测试、skill、config 或 plan；未启动容器、未调用 LLM。
- DCO：43/43 非 merge commit 有 `Signed-off-by`；6 个 merge 中 3 有 / 3 无，按任务口径 `n/a (merge)`。AI trailer 全区间 **0**。
- 复审对象：初审的 6 条 blocking findings + §1 表内 todo 6/8/9/14 的 checkbox 状态。

### R0. 证据可复算性（原 B5）— RESOLVED

`cd OpencodeAgent`，`legonanobot` env，六条命令全部 rc=0：

| 命令 | 输出 |
|---|---|
| `python tests/evals/intake/analyze_wave0.py --check` | `[ok] intake_load_rate.json, request_distribution.json and wave0_verdict.md match recomputation` |
| `python tests/evals/intake/analyze_wave1.py --check` | `--check OK (.../results/wave1_report.md)` |
| `python tests/evals/intake/analyze_probe.py --check` | `--check OK (.../results/wave1_frozen10_report.md)` |
| `python tests/evals/intake/ab/ab_analysis.py --check` | `pairs=10 eligible=7 ... p=1.0 verdict=INCONCLUSIVE`；`ab_report.md: up to date` |
| `python tests/evals/intake/gen_corpus40.py --check` | `40 items, labels=v1, regeneration matches` |
| `python tests/evals/intake/gen_corpus40.py --labels v2 --check` | `40 items, labels=v2, regeneration matches` |

语料冻结核验（v1 必须与 `32f5e595~1` 逐字节相同）：

```text
$ shasum -a 256 OpencodeAgent/tests/evals/intake/corpus40.jsonl
69bb6f256d5e2afa7a521015b40d22101e47ceec7f84a4acfacac4cdbd410630  ...
$ git show 32f5e595~1:OpencodeAgent/tests/evals/intake/corpus40.jsonl | shasum -a 256
69bb6f256d5e2afa7a521015b40d22101e47ceec7f84a4acfacac4cdbd410630  -
$ git diff --quiet 32f5e595~1:OpencodeAgent/tests/evals/intake/corpus40.jsonl \
      OpencodeAgent/tests/evals/intake/corpus40.jsonl && echo BYTE_IDENTICAL
BYTE_IDENTICAL
```

- `pytest tests/evals/intake/test_corpus_schema.py -q` → **22 passed**，含钉住上述 sha256 的 `test_corpus40_v1_is_frozen_before_the_label_correction` 与「v2 只改 c19/c22」断言。
- `results/corpus_version_note.md` 明确列出「哪份证据读哪份语料」：wave0/wave1 读 v1，probe/AB 读 v2。历史报告未被改写。
- 判定：**resolved**。初审「发布数字与当前输入不一致」不再成立——当前输入（v1）就是发布数字所用的输入；修正后的标签在 v2 中，只被新证据消费。

### R1. 初审 6 条 blocking 的逐条重判

| # | 初审 finding | 复审状态 | 证据 |
|---|---|---|---|
| 1 | Todo 6 的 40 条 Acceptance 未满足 | **resolved（表示层）** | 计划第 125 行改为 `[~]`，写出三次 attempt 的失败原因与 11 条探针四层数字；数字与 `wave1_frozen10_report.md` §6 逐项一致（route 7/10 vs 6/10、intake 8/10 vs 5/10、question 8/10 vs 8/10、params 8/10 vs 6/10）。40 条门未达标的事实保留，未用探针冒充 |
| 2 | Todo 9 同判据未满足 | **resolved（表示层）** | 计划第 150 行改为 `[~]`，写明「同 todo 6，40 条非回归门未完成」；`pytest tests/test_agents_md.py` 37 passed 不变 |
| 3 | Todo 14 配对样本 10 < 30 且无升级 | **resolved（表示层 + 升级记录）** | 计划第 196 行改 `[~]` 并写明「10 对（有效 7）< 30，INCONCLUSIVE，N=55」；执行记录第 8 条给出升级动作（60 会话 / 约 5 小时 / `run_ab.py --dry-run` / 阈值冻结在 `judge_spec.json`）。`ab_report.md` 仍为 INCONCLUSIVE、N=55（`--check` OK）。见 R9 |
| 4 | Wave 1 出口门被跳过 | **resolved（记录层）** | 计划「执行记录」第 2 条记录三次 attempt 与探针替代，并据此把 6/9 改 `[~]`。历史违规不可撤销；计划不再声称门通过，后果由 6/9 的 `[~]` 与 F3 承接。该条是计划级顺序门，不属 F1 的 per-todo 交付物口径，不另立 blocker |
| 5 | 证据漂移 | **resolved** | R0 |
| 6 | 预算 150 → ≥283 | **resolved（裁决 + 记录）** | 计划「执行记录」第 1 条：操作者裁决 Q1=A，上限 150 → ~320；按 artifact 重算实际 280（R4），≤ 放宽后的上限。计划第 63 行的「超限即停并报告」被满足：停了、报告了、操作者追加了预算 |

三个点名要的决定：

- **todo 14（n=10<30）是否 blocker？** 复核时曾判 blocker（缺升级记录），现 **已解除**：计划第 196 行改 `[~]`，执行记录第 8 条把升级写成可执行动作（30 对 / 60 会话 / ~5 小时 / 复跑命令与冻结阈值）。INCONCLUSIVE 仍是合法裁决句，checkbox 也不再过报。见 R9。
- **Wave 1 门被跳过，文档 + 诚实 `[~]` 是否可接受？** 是。F1 能要求的是「不冒充通过」，现在做到了；门本身没通过无法用文档修好，已如实交给 6/9 的 `[~]` 与 F3。
- **预算超限是否因操作者裁决解除？** 是。Q1=A 把上限改到 ~320，实际 280 ≤ 320；走的是计划预设的「停下 → 报告 → 操作者裁决」路径。

### R2. 四个 checkbox 与 artifact 的一致性

| todo | 初审状态 | 复审状态 | 与 artifact 一致？ |
|---|---|---|---|
| 6 | `[x]`（与 40 条 fail 矛盾） | `[~]` + gap 文本 | **一致** |
| 8 | `[~]` + gap 文本 | `[~]` + gap 文本 + commit + F-wave 项 | **一致** |
| 9 | `[x]`（与 40 条 fail 矛盾） | `[~]` + gap 文本 | **一致** |
| 14 | `[x]`（与 n=10、INCONCLUSIVE 矛盾） | `[~]` + gap 文本 + 升级指针 | **一致** |

四条 `[~]`（6/8/9/14）都写了具体数字、失败原因和证据文件，未出现「已达标」字样；与初审把 todo 8 的 `[~]` 判为「诚实」的口径保持连续。

### R3. Todo 14：复核时的 blocker（已由 R9 关闭）

> 二次复核（R9）已关闭本条：计划改 `[~]`、升级记录落地。下文保留当时的判定依据。

事实：

- 计划第 196 行仍 `- [x] 14.`，没有 gap 说明。
- `ab/ab_report.md` 首行：`Verdict: INCONCLUSIVE — n_pairs=10 < n_min=30. No direction is claimed`；§6 给出 required N=55 与两臂重跑命令；`ab_analysis.py --check` 复算 `pairs=10 eligible=7 p=1.0 verdict=INCONCLUSIVE`。
- 计划第 199 行 Acceptance 三项：①`配对样本 ≥30`（**未满足**：10）；②`报告含 p 值与效应方向`（p=1.0 有；报告明确不声称方向，因为 n<30 规则禁止 → **只成立一半**）；③裁决句明确（**满足**：INCONCLUSIVE + 原因）。
- 计划第 200 行：`样本不足 30 → INCONCLUSIVE 并升级，不得硬下结论`。

判定：INCONCLUSIVE 这一半做到了，缺的是「并升级」。计划自己定义了「升级」的语义：

- 第 91 行（todo 2 的 QA failure）：`记录阻断原因，本 todo 不算完成，升级给用户`——在本计划里，升级与 todo 完成互斥。
- 第 116 行（todo 5）：`样本 <40 时直接标 INCONCLUSIVE 并给出所需 N`——没有「升级」，那才是终态；todo 5 的 `[x]` 正因此成立。
- todo 14 比 todo 5 多出的正是「升级」二字，而它没有落地：计划执行记录只有 Q1=A（预算）一条操作者裁决；notepad 只有 pilot 数字，没有升级决定；没有新 todo 承接。

同向支持（非独立 blocker）：计划写的交付物路径是 `tests/evals/intake/ab_report.md`，实际是 `tests/evals/intake/ab/ab_report.md`；计划写的 commit 是 `test(opencode-agent): paired A/B verdict on ask-first vs artifact-first`，实际是 `... add ask-first vs artifact-first A/B apparatus with pilot verdict`。「apparatus + pilot」与现状一致，「verdict」不一致。

结论：`[x]` 仍然过报（三项 Acceptance：一项成立、一项半成立、一项未成立）。关闭只需一行计划改动（F1 无权改 plan），二选一：

```text
- [~] 14. ask-first vs artifact-first 配对 A/B 裁决 — 未达标：n_pairs=10 < 30，INCONCLUSIVE，required N=55；升级未落（待操作者裁决是否追加约 80 会话重跑），ab/ab_report.md §6 有待执行的复跑命令
```

或补一条操作者裁决，把「INCONCLUSIVE 作为已升级的终态」写进执行记录（与 Q1=A 同形）。任一落地后 checkbox 才与 artifact 一致。

### R4. 预算重算（辅助核验）

按 artifact 可复算的会话数：

| 项 | 会话 | 证据 |
|---|---|---|
| baseline | 17 | `baseline_meta.json` `sessions=10` + `budget.note` 的 7 次 repair |
| Wave 0 触发率 | 40 | `wave0_corpus40.jsonl` 40 个唯一 `run_id` |
| slot v1 | 72 | `v1_evidence/results.json` `runs_total=72` |
| slot v2 | 72 | `results.json` `runs_total=72` |
| Wave 1 attempt 1 | 40 | `wave1_corpus40_attempt1.jsonl` 40 个唯一 `run_id` |
| Wave 1 attempt 2 | 25 | `wave1_corpus40_attempt2_harness_timeout.jsonl` 25 个唯一 `run_id` |
| 有界探针 | 11 | `wave1_probe.jsonl` 11 个唯一 `run_id` |
| two-turn | 1 | `two_turn_probe.md` |
| 命令 smoke | 2 | `image_delivery.md`、`question_tool_probe.md` |
| A/B | 0 | pilot 复用已提交 JSONL |
| **合计** | **280** | |

执行记录写「约 285」（与 artifact 重算差 1.8%），但括号分项有三处与 artifact 不符：`槽位 v2 74`（实测 72）、`Wave 1 attempt 1 61`（实测 40）、`基线 20`（实测 17），其分项自身相加只有 271。记录 headline 285 / artifact 280 / 分项 271 三个口径都 >150 且 ≤ 放宽后的 ~320，结论不变。建议把分项改成 17/40/72/72/40/25/11/1/2。**记录精度问题，非 blocker。**

### R5. F2 交叉发现（不计入 F1）

- F2 报告（`2b4d57c0`，`results/f2_code_quality.md`）的唯一 blocker 是 `tests/smoke-v311.sh:57` 把 `AGENTS.md` 钉在 328 行，而 HEAD 是 329。
- 复审期间 `ec22686e` 落地：`smoke-v311.sh` 现在断言 `-le 350`（计划 cap）与 `-ge 300`（sanity floor）（`sed -n '64,65p'`），`AGENTS.md` 329 行；`bash -n` OK。方向与 F2 建议一致。
- 因此 F2 报告在 `2b4d57c0` 时点成立、在 `ec22686e` 后过期；这是 F2 自己的收口问题，F1 只记录状态，不改判 F2。

### R6. 新增 commit 的分类与 DCO

| sha | subject | 归属 | DCO | AI trailer |
|---|---|---|---|---|
| `8c4feddf` | fix(opencode-agent): version the eval corpus to stop wave-0/1 evidence drift | plan（todo 5/8 证据完整性修复，非功能增量） | yes | none |
| `0093e625` | docs(opencode-agent): add F1 plan conformance audit | audit 文档（不属任何 todo） | yes | none |
| `2b4d57c0` | docs(opencode-agent): add F2 code quality review | audit 文档（不属任何 todo） | yes | none |
| `ec22686e` | fix(opencode-agent): assert the AGENTS.md line cap instead of a pinned count | plan（todo 7 的 review fix：smoke 断言是 todo 7 的 Acceptance 工具） | yes | none |

- 计数：**49 = 24 plan + 2 audit + 23 external**（初审 45 = 22 plan + 23 external、无 audit 文档；新增 4 = 2 plan + 2 audit）。
- commit 粒度结论不变：「每个 todo 一个 commit」在本计划从未字面成立（初审 §5）；新增的 `8c4feddf`/`ec22686e` 属同一类 review/evidence fix，`0093e625`/`2b4d57c0` 是审计产物，不应计入 todo。这是既有的、已记录的偏差，不是本轮新增的 gate 问题。

### R7. 复审仍记录的非阻塞项

1. **todo 15 checkbox 仍 `[ ]`**（初审 G6 未变）：交付物已入库且 `test_readme_inventory.py` 8 passed，状态滞后。
2. **计划不在 git track**（`.gitignore:210` 忽略 `.omo/`）：checkbox 修正无法用 commit 复核，本次只能读工作树；「计划文本的变更史」不可审计。属复审口径限制，非 blocker。
3. **执行记录第 2 条把 attempt 2 写成「排除出行为裁决」**，而 `wave1_report.md` §2 仍称其为 `fixed instrument — final gate` 并发布 `fail`（`params_compiled` 0/25；配对 9/10 vs 0/10，p=0.0039）。两种口径都指向「40 条门未达标」，不改变 `[~]`；但记录不应声称报告已排除该 attempt。
4. **执行记录的分项数字三处过期**（R4）：headline 不受影响。
5. **`wave1_frozen10_report.md` 正文仍写 `corpus40.jsonl`**：冻结的历史陈述，`corpus_version_note.md` 已标明操作文件是 v2，`--check` 绿。
6. **§1 其余 11 行未重跑**：本次只核验 6 条 finding 与 4 个 checkbox 相关证据；全量 pytest 由 F2 承担（184 passed）。

### R8. 复审裁决

- 6 条初审 blocking：**6 条 resolved**（todo 6、todo 9、Wave 1 门、证据漂移、预算；todo 14 在 R9 关闭）。
- DCO 与 AI trailer 仍然干净：43/43 非 merge 有 `Signed-off-by`，0 个 AI trailer；3 个 merge 无 DCO 按任务口径 `n/a (merge)`。
- 全部 todo 的 checkbox 与 artifact 一致（6/8/9/14 均为 `[~]` 并带 gap）；证据漂移、`[~]` 诚实性、预算授权与 todo-14 升级均已收口。F1 放行。

### R9. 二次复核（todo-14 升级落地，2026-10-29）

- 复核四点全部成立：计划第 196 行 `[~]` + gap「10 对（有效 7）< 30，INCONCLUSIVE，N=55」+ 升级指针；执行记录第 8 条给出升级动作（30 对 / 60 会话 / 约 5 小时 / `run_ab.py --dry-run` / 阈值冻结在 `judge_spec.json`）；`ab_report.md` 仍 INCONCLUSIVE、N=55（`ab_analysis.py --check` OK）。
- 区间变化：相对初审 45 个 commit，当前 50 个，新增 5 个——`8c4feddf`（plan，证据修复）、`0093e625`（audit，F1 报告）、`2b4d57c0`（audit，F2 报告）、`ec22686e`（plan，todo 7 smoke 修复）、`4aab6cf4`（audit，本报告）；相对被审 HEAD `ec22686e` 只多 `4aab6cf4`。复核时 HEAD `4aab6cf4`（本报告自身的 commit 不计入被审集合），无 blocker 残留。

VERDICT: APPROVE
