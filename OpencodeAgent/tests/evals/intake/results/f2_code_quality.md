# F2 代码质量审查 — novice-onboarding-gprime

- 审查日期：2026-10-29
- HEAD：`8c4feddf`
- 范围：`OpencodeAgent/tests/` 全量 pytest；`test_agents_md.py` 结构断言；`docker compose config`；plan commits 变更文件卫生
- 只读：未修改产品代码、测试、skill、config 或 plan；本报告与 notepad 追加是唯一写入物
- 方法：pytest / `--check` / ruff 均在 `legonanobot` conda env 中执行；未启动容器、未构建镜像、未调用 LLM。`smoke-v311.sh` 只做静态检查，未执行 Stage 1/2。

## 0. 结论

**VERDICT: REJECT**

唯一 blocker：`OpencodeAgent/tests/smoke-v311.sh:57` 把 `AGENTS.md` 行数硬编码为 **328**；HEAD 的 `AGENTS.md` 是 **329** 行，`Dockerfile:112` 原样 `COPY`，因此 F3 从 HEAD 重建镜像后 Stage 1 会在 runtime 断言之前失败。`image_delivery.md` 的 `SMOKE_OK` 是 `6e635820` 镜像（当时 328 行）的结果，不是 HEAD 的结果。

除该 blocker 外：pytest **184 passed**；四个重算门、两个生成器 `--check`、baseline `recompute.py --check` 全绿；`docker compose config -q` 通过；无 tracked 密钥/PII；结构断言不是空断言，但有几处是子串/启发式检查（minor）。

## 1. 测试套件

```text
$ conda run -n legonanobot python -m pytest OpencodeAgent/tests/ -q
........................................................................ [ 39%]
........................................................................ [ 78%]
........................................                                 [100%]
184 passed in 0.87s
```

- 失败/错误/skip：**0**；无 traceback 可贴。
- 关键文件单独计数：`tests/test_agents_md.py` **37 passed**；`tests/test_config_render.py` **49 passed**。
- pytest 不收集 `tests/smoke-v311.sh`。pytest 全绿不能证明 smoke 门是绿的，见 §4、§6 blocker。

## 2. 重算门与生成器

全部在 `OpencodeAgent/` 下执行，退出码均为 0：

| 命令 | 结果 |
|---|---|
| `python tests/evals/intake/analyze_wave0.py --check` | `[ok] intake_load_rate.json, request_distribution.json and wave0_verdict.md match recomputation` |
| `python tests/evals/intake/analyze_wave1.py --check` | `--check OK (.../results/wave1_report.md)` |
| `python tests/evals/intake/analyze_probe.py --check` | `--check OK (.../results/wave1_frozen10_report.md)` |
| `python tests/evals/intake/ab/ab_analysis.py --check` | `[ab] pairs=10 eligible=7 ... mcnemar_p=1.0 verdict=INCONCLUSIVE` + `up to date` |
| `python tests/evals/intake/gen_corpus40.py --check` | `corpus40.jsonl: 40 items, labels=v1, regeneration matches` |
| `python tests/evals/intake/ab/gen_corpus_ab.py --check` | `corpus_ab.jsonl: 40 items, regeneration matches` |
| `python tests/evals/intake/recompute.py --check` | `PASS baseline_meta.json` + `PASS baseline_report.md` |
| `python tests/evals/intake/slot_value/judge.py --check`（额外） | `[check] ok: 24 cells, 72 runs, 0 ok / 72 ok_partial / ...`，6/6 INCONCLUSIVE |

这些门都能失败，不是“恒绿”门：

- `analyze_wave0.py:369-373`：重新生成三个产物后逐字节比较，drift → `return 1`。
- `analyze_wave1.py:533-541`：报告缺失或字节不一致 → `return 1`。
- `analyze_probe.py:492-502`：报告缺失或字节不一致 → `return 1`。
- `ab_analysis.py:430-461`：报告缺失/过期时打印 unified diff 并 `return 1`。
- `gen_corpus40.py:183-187`、`gen_corpus_ab.py:243-251`：文件缺失或不匹配 → `return 1`。
- `recompute.py:271-283`：缺 meta/report 或缺 published block → `SystemExit`；`_diff` 对 dict/list 都先比长度/键集（`:242-263`），不会用 `zip` 截断掩盖差异。

静默失败检查：

- `brief_validation.py` 无 `except`、无 bare except、无错误路径返回 0；校验结果以 error list 返回。
- `analyze_wave0/1`、`analyze_probe`、`ab_analysis` 无 `except Exception: pass`、无 bare `except`。
- `run.py:151-158,198` 的宽 `except Exception` 只用于 session recovery；recovery 为空时 `_run_one` 落回 `run_error`（`:379-382`），不伪造成功。
- `export_first_messages.py:187` 有 `except sqlite3.DatabaseError: pass`，会吞掉 legacy schema 查询错误；主流程随后把来源标为 `unavailable` 并输出 synthetic，属于“降级但明说”，不是静默成功。列为 nit。

## 3. 结构断言（`test_agents_md.py`）

实际值由直接调用测试模块得到：

```text
turn_budget_hits= {'skills/intake/SKILL.md': ['最多 1 轮', '≤3 问', '绝不重复问第二轮', '追加 1 轮', '一次问清']}
agents_lines= 329
product_rows= 8 columns= 6
appendix_rows= 27
scenario_c_body= ['识别为开放型 / 待澄清型 → 加载 `intake` skill 执行漏斗与槽位澄清；本场景不再重复协议。']
intake_has_scenario_c= False
intake_lines= 155
```

| Plan 声称 | 断言 | 能否真的失败 | 评价 |
|---|---|---|---|
| 全仓只有一份轮次规则 | `TestSingleTurnBudgetRule::test_rule_lives_in_exactly_one_file`（`:118`）`assert set(hits) == {"skills/intake/SKILL.md"}`；`test_agents_md_does_not_restate_the_budget`（`:130`）；`test_intake_carries_the_canonical_markers`（`:125`） | **能**：第二个 `SKILL.md` 出现任一 marker，或 AGENTS.md 复述，即失败。实际 hits 只有 intake。 | **范围窄于“全仓”**：`_turn_budget_files()`（`:88-96`）只扫 `AGENTS.md` + `**/SKILL.md`。`tests/evals/intake/fixtures/ask_first_protocol_snapshot.md:82-84` 仍逐字保存旧协议（计划 todo 2 要求冻结），所以字面全仓断言本来就会失败。marker 是精确字符串，新改写可逃逸。minor。 |
| AGENTS.md ≤350 行 | `TestLineBudget::test_budget_constant_is_350`（`:100`）+ `test_agents_md_within_budget`（`:103`）`assert len(lines) <= AGENTS_MD_MAX_LINES` | **能**：当前 329；加 22 行即失败。常量被 `test_budget_constant_is_350` 钉死为 350，不能靠放宽常量绕过。 | 有效。`test_config_render.py:405` 还有一份同样的体积断言。 |
| intake frontmatter 合法 | `TestIntakeSkill::test_frontmatter_is_valid`（`:137`） | **部分能**：缺 `name: intake`/`description:`/`Use when:`/`argument-hint:`/`user-invocable: true`/`triggers:` 或空 triggers 即失败。 | **不是 YAML 解析**：只看 `startswith("---\n")` 和子串。当前文件用 `yaml.safe_load` 验证为合法 dict；但语法坏掉、仍含全部 token 的 frontmatter 会漏过。minor。 |
| 场景 C 单向 | `TestScenarioCPointer::test_scenario_c_body_is_one_line_pointer`（`:196`）`assert len(body) <= 3`；`test_intake_does_not_point_back`（`:205`）`assert "场景 C" not in intake` | **能**：场景 C 恢复多段协议（>3 非空行）或 intake 出现“场景 C”即失败。实际 body 1 行，intake 无“场景 C”。 | ≤3 行留了 2 行余量；且 marker 是精确列表，新的“不超过两轮”式改写可逃逸。单向性只由“不出现字面 场景 C”证明，不是链接图检查。minor。 |
| 产物索引 8 行 | `TestProductIndex::test_product_table_has_eight_rows_and_six_columns`（`:312`）`assert len(data_rows) == 8`；`test_every_product_row_keeps_all_six_columns`（`:323`）；`test_each_product_maps_to_its_scenario_or_skill`（`:329`）`assert actual == list(PRODUCT_SCENARIOS)` | **能**：增/删行、列数变化、映射改动都会失败。实际 8 行 × 6 列，逐格匹配。 | 强断言。 |
| 映射目标真实存在 | `test_scenario_targets_exist_in_the_scenarios_skill`（`:334`）检查 `## 场景 A/B/B2/D/E/F：`；`test_risk_analysis_resolves_to_the_vt_skill_tree`（`:340`）检查 `agent/src/skills/risk-analysis/SKILL.md`；`test_html_report_target_exists_locally`（`:351`） | **能**：删场景 heading、删 VT skill 文件、删 html-report 文件即失败。 | 强断言；`risk-analysis` 是 VT 树，不是 OpencodeAgent 目录，测试已明确区分。 |
| 能力附录行数一致 ±2 | `test_appendix_carries_the_migrated_table`（`:369`）`abs(len(data_rows) - 27) <= 2` + 3 个子串 | **能**：行数掉到 25 以下或涨到 30 以上即失败。实际 27。 | **只比行数，不比内容**：删一行再复制一行仍可 27 行通过。minor。 |

**空断言判定**：没有发现严格意义的 vacuous assertion。

- `test_budget_constant_is_350` 是“常量对字面量”，单独看只证明常量没被改；但它与 `test_agents_md_within_budget` 成对，钉住了真正的预算，不能算空。
- `TestClosingBlock::test_constant_declares_the_four_prefixes` 同理，是常量 pin；行为测试 `test_each_line_is_required` 使用同一常量，二者成对有效。
- `test_config_render.py:326` 的 `test_non_interactive_sessions_must_not_ask` 只断言 intake skill 里有一句话，不测行为；计划本身已承认模板表达不了，要求写进 skill，因此是“文档契约”而非空断言。nit/minor。
- `test_readme_inventory.py:94` 只断言 `## 13. 立项后变更` heading 存在，不断言追加内容；弱，但可失败。nit。

## 4. Config 校验

```text
$ cd OpencodeAgent && docker compose config -q
$ echo $?
0
```

`-q` 无输出；compose 文件可解析、变量可插值。注意：本地 `OpencodeAgent/.env` 是 gitignored 的运维文件，里面的真实凭据会被 `docker compose config`（不带 `-q`）渲染出来；本次审查没有把渲染值写入报告或日志。

`test_config_render.py` **49 passed**，关键断言：

- `test_direct_mode_matches_golden_byte_for_byte`（`:205-208`）：`raw = render_config.render(...)`，`assert raw == DIRECT_MODE_GOLDEN`——**仍是逐字节比较**，不是 JSON 结构比较。
- `test_every_user_selectable_primary_agent_can_ask`（`:308`）：对 `{build, plan}` ∪ OMO `mode: primary` 的每个 agent 断言 `permission.question == "allow"`。当前 `oh-my-openagent.json` 没有 `mode` 键，集合就是 `{build, plan}`；实际模板两者都有。
- `test_subagents_never_get_question_permission`（`:314`）：`explore`/`multimodal-looker`/`librarian` 不 allow，且“所有 allow 的 agent 集合”必须等于预期 primary 集合——反向也锁住了。
- `TestPerAgentToolScoping::test_finance_mcp_tools_denied_for_non_finance_agents`（`:275`）：`explore`/`multimodal-looker` 的 `vibe-trading_*` deny 不回归。
- `test_non_interactive_sessions_must_not_ask`（`:326`）：只查 intake skill 文本，见 §3。

**golden 是否重新生成**：没有生成器。`git show 2452260c -- OpencodeAgent/tests/test_config_render.py` 显示 `build`/`plan` 的 `question: allow` 块是直接插入 `DIRECT_MODE_GOLDEN` 字面量的；仓库内除该测试外没有 `DIRECT_MODE_GOLDEN` 的写入/生成代码。因此 golden 是**手工编辑**，不是脚本重生成。当前渲染与 golden 逐字节一致，测试通过；这是流程 nit，不是失败。

## 5. 卫生扫描

扫描范围：24 个 plan 侧 commit（F1 的 22 个 plan commit + `0093e625` F1 报告 + `8c4feddf` corpus versioning）的并集，**309 个唯一文件**；另外补扫了磁盘上未被 tracked 的 `wave1_texts_attempt2_harness_timeout/` 与 `wave1_texts_attempt3/`（各 50 文件，共 100 个），以覆盖 `results/wave1_texts*` 的全部样本。

### 5.1 行数（400 practical / 800 hard）

超过 400 practical cap 的 changed code files（无一个超过 800 hard cap）：

| 行数 | 文件 |
|---:|---|
| 715 | `OpencodeAgent/tests/evals/intake/slot_value/judge.py` |
| 667 | `OpencodeAgent/tests/evals/intake/slot_value/run_slots.py` |
| 545 | `OpencodeAgent/tests/evals/intake/analyze_wave1.py` |
| 522 | `OpencodeAgent/tests/evals/intake/ab/ab_report.py` |
| 515 | `OpencodeAgent/tests/test_config_render.py` |
| 506 | `OpencodeAgent/tests/evals/intake/analyze_probe.py` |
| 461 | `OpencodeAgent/tests/evals/intake/ab/ab_analysis.py` |
| 455 | `OpencodeAgent/tests/evals/intake/run.py` |

`CONTRIBUTING.md:293` 原文：`Keep files under 400 lines where practical, 800 hard cap.` 这些是 minor（practical cap），`judge.py` 715 最接近 hard cap。

### 5.2 ruff

```text
$ conda run -n legonanobot ruff check --output-format concise <changed .py files>
OpencodeAgent/tests/evals/intake/analyze_wave1.py:546:29: W292 [*] No newline at end of file
OpencodeAgent/tests/test_route_classifier.py:66:69: W292 [*] No newline at end of file
Found 2 errors.
```

仅 2 个 W292，均为文件末尾缺换行；没有逻辑/未使用导入类错误。nit。

### 5.3 标记

`TODO` / `FIXME` / `XXX` / `pass  # stub` 在 309 个 changed files 中：**0 命中**。

### 5.4 密钥 / PII

- 真实 `sk-` / `ghp_` / `Bearer`：**0**。`.env.example:7` 的 `DASHSCOPE_API_KEY=sk-your-dashscope-api-key` 是占位符，不是密钥；`.env` 本身未被 tracked（`git check-ignore -v OpencodeAgent/.env` 命中 `OpencodeAgent/.gitignore:12`）。
- CN 手机号 / 身份证：**0 个真实命中**。扫描器命中的 `15620166505` 是 `signature_hash` 的十六进制子串，`...c4e665...` 是 pip URL 的哈希子串，均非 PII。
- 邮箱：`f1_plan_conformance.md:65` 是 DCO 署名身份（已在 commit metadata 中公开），`image_build.log:513` 是 npm deprecation 联系人；均不是终端用户 PII。报告不复述具体地址。
- `results/wave1_texts/`（40 对）、`wave1_texts_fix1/`（25 对）、`wave1_texts_probe/`（11 对）为 tracked，共 **152 个文件**；`wave1_texts_attempt2_harness_timeout/` 与 `wave1_texts_attempt3/` 为磁盘上未 tracked 的 25 对样本，共 **100 个文件**。全部 252 个文件逐一扫描：无真实用户数据；这些是 synthetic corpus 的模型输出，不是导出的用户会话。
- `results/` 与 `slot_value/` 中没有 `"source": "real"` 记录；`export_first_messages.py` 的 raw export 路径不在 results 下，仓库内没有 first-message 原始导出。
- 真实验证：`grep -rl '"source": "real"' ...` → 无命中。

### 5.5 提交的缓存 / 原始 transcript

- `git ls-files | grep -E '__pycache__|\.pyc$|\.DS_Store$'` → **0**。
- `OpencodeAgent/__pycache__/brief_validation.cpython-312.pyc` 在磁盘上存在，但 `OpencodeAgent/.gitignore:2` 已忽略，未 tracked。
- `slot_value/transcripts/*.json` 是 72 个 synthetic cell 的工具调用 trace（`user_message` 是矩阵里的 synthetic 场景提示），不是真实用户原文；`wave1_texts*` 是 synthetic prompt 的模型输出。无真实 transcript 入库。

## 6. Findings

| severity | file:line | finding | evidence |
|---|---|---|---|
| **blocker** | `OpencodeAgent/tests/smoke-v311.sh:57` | smoke Stage 1 断言 `AGENTS.md` 恰好 328 行；HEAD 是 329 行，`Dockerfile:112` 原样 COPY，F3 重建镜像后 Stage 1 必失败（runtime 命令断言根本不会执行）。 | `wc -l < OpencodeAgent/AGENTS.md` → `329`；`git show HEAD:OpencodeAgent/AGENTS.md \| wc -l` → `329`；`sed -n '56,57p'` 显示 `test "$actual_lines" -eq 328`；`git show 967968cd:OpencodeAgent/AGENTS.md \| wc -l` → `328`，说明 328 是旧镜像 `6e635820` 的值；后续 `2b615dae` 把 AGENTS.md 改成 329 时没有更新 smoke。`image_delivery.md` 的 `SMOKE_OK` 只属于 6e635820。 |
| minor | `OpencodeAgent/tests/test_agents_md.py:118` | “全仓只有一份轮次规则”实际只扫 `AGENTS.md` + `**/SKILL.md`；冻结快照 `fixtures/ask_first_protocol_snapshot.md:82-84` 和 wiki 文档仍含旧协议，字面全仓断言不可成立。 | `_turn_budget_files()` `:88-96`：`candidates = [AGENTS_MD_PATH, *sorted(SKILLS_DIR.rglob("SKILL.md"))]`；实际 hits 只有 `skills/intake/SKILL.md`。 |
| minor | `OpencodeAgent/tests/test_agents_md.py:137` | “frontmatter 合法”是子串检查，不是 YAML 解析；语法坏但 token 齐全的 frontmatter 会漏过。 | `:138-155` 只用 `startswith`/`in`；当前 `yaml.safe_load` 验证为合法 dict（keys: argument-hint, description, name, triggers, user-invocable）。 |
| minor | `OpencodeAgent/tests/test_agents_md.py:196` | 场景 C 单向断言允许 ≤3 非空行，且只匹配精确 marker；2 行改写可重新引入第二套轮次规则。 | `assert len(body) <= 3`；实际 body 1 行；marker 列表在 `:31-38`。 |
| minor | `OpencodeAgent/tests/test_agents_md.py:369` | 能力索引附录只比行数 27±2 和 3 个子串；删一行复制另一行仍可 27 行通过。 | `assert abs(len(data_rows) - 27) <= 2`；实际 appendix_rows=27。 |
| minor | `OpencodeAgent/tests/test_config_render.py:326` | 非交互 question 禁令只断言 intake skill 里有一句话，不测 runtime 行为；计划已接受该限制，但断言本身不能证明“headless 不会问”。 | `assert "非交互会话（cron / headless `opencode run` / IM）不得调用 question" in body`。 |
| minor | `OpencodeAgent/tests/test_readme_inventory.py:94` | 报告“立项后变更”只断言 heading 存在，不断言内容。 | `assert CHANGES_HEADING in _read(REPORT_PATH)`。 |
| minor | `OpencodeAgent/tests/evals/intake/slot_value/judge.py:1` 等 8 个文件 | 8 个 changed Python 文件超过 400 行 practical cap，无一个超过 800 hard cap；最大 `judge.py` 715。 | `wc -l`：715 / 667 / 545 / 522 / 515 / 506 / 461 / 455，见 §5.1。 |
| nit | `OpencodeAgent/tests/evals/intake/analyze_wave1.py:546` | ruff W292，文件末尾缺换行。 | `ruff check` 输出。 |
| nit | `OpencodeAgent/tests/test_route_classifier.py:66` | ruff W292，文件末尾缺换行。 | `ruff check` 输出。 |
| nit | `OpencodeAgent/tests/test_config_render.py:46` | golden 是手工编辑，不是脚本重生成；当前逐字节比较仍通过。 | `git show 2452260c -- OpencodeAgent/tests/test_config_render.py` 把 `build`/`plan` 的 `question` 块插入 `DIRECT_MODE_GOLDEN`；仓库无生成器。 |
| nit | `OpencodeAgent/tests/evals/intake/export_first_messages.py:187` | `except sqlite3.DatabaseError: pass` 吞掉 legacy schema 查询错误，主流程只报“no first user message”，不报 DB 错误。 | 该行 `pass`；main 的 notes 走 `unavailable`/synthetic 降级。 |
| nit | `OpencodeAgent/.env.example:7` | 扫描器命中 `sk-`，但值是占位符，不是 secret。 | `DASHSCOPE_API_KEY=sk-your-dashscope-api-key`。 |
| nit | `OpencodeAgent/tests/evals/intake/results/f1_plan_conformance.md:65`；`results/image_build.log:513` | 邮箱模式命中 DCO 署名身份与 npm deprecation 联系人；均为公开身份，不是用户 PII。 | 两处 file:line；报告不复述具体地址。 |

## 7. 总体裁决

**VERDICT: REJECT**

- Blocker：1 个（`tests/smoke-v311.sh:57` 的 328 vs 329）。
- Major：0。
- Minor：7 个（结构断言的 4 处弱检查、非交互文档断言、README 追加段断言、8 个 >400 行文件）。
- Nit：6 个（2 个 W292、golden 手工编辑、exporter 的 `pass`、占位符 `sk-`、公开邮箱模式）。

除 blocker 外，pytest、四个重算门、两个生成器、baseline recompute、compose config 全部通过；没有硬编码真实密钥，没有真实用户 PII，没有 tracked 缓存文件。修复 blocker 的最小改动是把 `smoke-v311.sh:57` 从 `-eq 328` 改为 `-le 350`（与计划 cap 一致）或 329，并同步注释；这属于 F3 之前的必要修复，本报告不修改代码。

## Re-check 2026-10-29

复查对象：HEAD `7e76673a`（F2 原报告提交于 `2b4d57c0`）。本轮只复核 blocker 与后续提交，不重跑全量审计。

### 修复证据

`ec22686e` 把「钉死快照行数」换成「计划 cap + 下限」。当前行号（HEAD）：

- `OpencodeAgent/tests/smoke-v311.sh:63` — `actual_lines=$(wc -l < /workspace/AGENTS.md)`
- `OpencodeAgent/tests/smoke-v311.sh:64` — `test "$actual_lines" -le 350 || { echo ...; exit 1; }`，cap 与 `AGENTS_MD_MAX_LINES == 350` 一致（`tests/test_agents_md.py:101` 钉死该常量）
- `OpencodeAgent/tests/smoke-v311.sh:65` — `test "$actual_lines" -ge 300 || { echo ...; exit 1; }`，内容丢失的 sanity floor
- `OpencodeAgent/tests/smoke-v311.sh:66` — echo 打印实际 `$actual_lines`，不再打印常量

其余三项要求逐条确认：

- **四命令仍是 required subset**：`:43-48`（镜像内 command 文件）与 `:139-145`（runtime discovery）两个 `for name in checkup screen backtest review` 循环，缺任一即 `exit 1`；两处均加了「required subset, not an exact-list snapshot」注释。
- **无 `|| true` 吞失败**：全文件共 4 处 `|| true`，全部在非断言路径 —— `:93` trap 清理、`:107` 失败时的 `docker logs`、`:152` / `:188` 错误信息 tail；所有断言分支都是 `exit 1`。
- **不再对合法内容变更误报**：当前 HEAD `OpencodeAgent/AGENTS.md` 为 329 行，落在 `[300, 350]`，修复后 Stage 1 通过。
- `bash -n OpencodeAgent/tests/smoke-v311.sh` → 语法通过。

### 六条命令结果

| # | 命令（工作目录 `OpencodeAgent/`） | 结果 |
|---|---|---|
| 1 | `conda run -n legonanobot python -m pytest tests/ -q` | `184 passed in 0.90s` |
| 2 | `conda run -n legonanobot python tests/evals/intake/analyze_wave0.py --check` | `[ok] intake_load_rate.json, request_distribution.json and wave0_verdict.md match recomputation`（exit 0） |
| 3 | `conda run -n legonanobot python tests/evals/intake/analyze_wave1.py --check` | `--check OK (.../results/wave1_report.md)` |
| 4 | `conda run -n legonanobot python tests/evals/intake/analyze_probe.py --check` | `--check OK (.../results/wave1_frozen10_report.md)` |
| 5 | `conda run -n legonanobot python tests/evals/intake/ab/ab_analysis.py --check` | exit 0；`pairs=10 eligible=7 control_success=7 experiment_success=6 control_manual=0 experiment_manual=1 mcnemar_p=1.0 verdict=INCONCLUSIVE`；`ab_report.md: up to date` |
| 6 | `docker compose config -q` | 通过（`COMPOSE_OK`，exit 0） |

路径更正：`ab_analysis.py` 实际位于 `tests/evals/intake/ab/ab_analysis.py`，不存在 `tests/evals/ab/` 目录；按真实路径执行通过。

### 提交分类（`2b4d57c0..7e76673a`）

| commit | 触及文件 | 是否引入新 blocker |
|---|---|---|
| `8c4feddf` | eval corpus 版本化：`corpus40.jsonl`/`corpus40_v2.jsonl`、`gen_corpus40.py`、`gen_corpus_ab.py`、`analyze_probe.py`、`test_corpus_schema.py`、`results/corpus_version_note.md`、notepad | 否。全部是测试侧生成器/分析器/数据与文档，无产品面；F2 原审计已覆盖该版本化，且其后四条 `--check` 与 pytest 全绿。 |
| `2b4d57c0` | `results/f2_code_quality.md` | 否。纯报告。 |
| `ec22686e` | `tests/smoke-v311.sh`（+13/−4） | 否。即本 blocker 的修复本身。 |
| `4aab6cf4` | `results/f1_plan_conformance.md` | 否。纯报告。 |
| `7e76673a` | `results/f1_plan_conformance.md` | 否。纯报告。 |

五个提交均未触及产品代码、skills、容器配置或 Dockerfile，未引入新的硬编码密钥/路径/URL；工作树仅有 notepad 与 wiki 的未跟踪改动，不在本轮评审范围。

### 裁决

- Blocker：1 → **resolved**（`ec22686e`，`OpencodeAgent/tests/smoke-v311.sh:63-66`）。
- 新增 blocker：0。
- 原报告的 7 个 minor 与 6 个 nit 不在 blocker 口径内，状态不变，不阻塞交付。

VERDICT: APPROVE
