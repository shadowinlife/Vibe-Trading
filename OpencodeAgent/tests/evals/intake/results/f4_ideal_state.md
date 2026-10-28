# F4 理想态保真 — novice-onboarding-gprime

- 执行日期：2026-10-29（Asia/Shanghai）
- 审查对象：`.omo/plans/novice-onboarding-gprime.md` 的 IS-1..IS-6（计划第 19–24 行）
- HEAD：`b19d1793`；F3 交付镜像构建自 `4aab6cf4`；F3 报告落盘时 HEAD `07cee224`
- 只读边界：未修改产品代码、测试、skill、config 或 plan；本报告是本次唯一写入物
- 未执行：容器、镜像构建、LLM 调用。已执行的只有离线 pytest 与 `--check`（见 §6）
- 路径约定：`R = OpencodeAgent/tests/evals/intake/results`，`I = OpencodeAgent/tests/evals/intake`；其余路径相对仓库根

## 0. 结论

**2 条 `met`（IS-4、IS-5），4 条 `partial`（IS-1、IS-2、IS-3、IS-6），0 条 `unmet`。**

本计划**未**完全交付到理想态：F3 判 **REJECT**（40 条语料门未在交付镜像上执行），closing block 实测契约分母 **4/6 = 66.7%**、有产物分母 **4/5 = 80.0%**，均低于 90% 阈值；`c02` 无产物，`c27` 改写 `数据截止 T-1`。结构面（IS-4/IS-5）成立，行为面（IS-1/IS-2/IS-3）与回滚面（IS-6）只有部分证据。

## 1. IS-1..IS-6 判定表

| IS | requirement (verbatim) | measurable proxy | evidence path | verdict | one-line reason |
|---|---|---|---|---|---|
| IS-1 | 新手输入"今天买什么"：≤2 个用户轮次内拿到具名产物；产物含 closing block（假设/diff/下一步/免责声明）；无未处理工具错误；无需术语。（可测代理指标替代主观判断，Metis H4 修正） | 四信号合取：① `user_turns ≤ 2`；② artifact 存在（磁盘路径 + 字节数）；③ closing block 四行 `complete=true`；④ 无未处理工具错误（无 `run_error`、无非零退出）。n=1 两轮探针（c05）四信号全绿；n=6 契约项 artifact 5/6、closing 4/6；n=11 探针 0 `run_error`；n=10/11 的单轮 JSONL 不能观察第 2 轮。 | `R/two_turn_probe.md`（n=1，turn-level）；`R/wave1_frozen10_report.md` §3（n=6）与 §1–2（n=11）；`R/wave1_report.md` §1（n=40 attempt 1） | partial | 四信号只在 n=1（c05）上同时成立；语料级 closing 4/6=66.7%（有产物 4/5=80%）< 90%；IS-1 字面 prompt `c02` 在探针中无产物；`≤2 用户轮` 在单轮 JSONL 上不可观测；`无需术语` 未被四信号代理测量。 |
| IS-2 | 能力发现不靠模型自觉：`/` 命令菜单在运行时可被发现（server 断言，非仅文件存在）；closing block 在 40 条语料上的出现率有阈值验收。 | ① `GET /command?directory=/workspace` 的运行时命令列表包含 `checkup/screen/backtest/review`，且 `/checkup` 产生非空回复；② 40 条契约分母 closing block 出现率 ≥90%。 | `R/f3_real_qa.md` §2–3；`R/image_delivery.md` §5；`R/wave1_frozen10_report.md` §3；`R/wave1_report.md` §1 | partial | 命令发现已由 server 断言证明（29 个运行时命令，四个必需项在列；`/checkup` 非空回复）；closing block 有产物分母 4/5=80%、契约分母 4/6=66.7%，40 条 attempt 1 亦仅 9/13=69.2%，全部 < 90% → 第二半未达标。 |
| IS-3 | 提问只发生在私有槽位（成本价/仓位），结构化选择题；市场/期限/风险默认化 + diff 卡；headless/cron/IM 会话中 question 自动跳过不挂起。 | `clarification_surfaced = route_class == clarify_private OR question_called`（计划执行记录第 3 条的操作者裁决；`question_called` 仅作诊断）；两轮探针的私有槽位数 / 选项数 / 逃生门；`intake` 默认值表 + brief schema；headless session 的 `question: deny`。 | `R/two_turn_probe.md`（n=1）；`R/question_tool_probe.md`；`OpencodeAgent/skills/intake/SKILL.md`；`OpencodeAgent/tests/test_intake_brief.py`；`R/wave1_frozen10_report.md` §4 | partial | n=1 证明私有槽位文本三问、每题三选项、逃生门与第 2 轮产物；headless `question` 被 deny 已实测并记录为限制（按裁决文本反问合格）；但结构化 question 工具从未真正触发（0/11、0/40）、cron/IM 未测、六公共槽位的默认化缺少实验授权（`slot_value/verdict.md` 6/6 INCONCLUSIVE）→ partial。 |
| IS-4 | 每层改动分层可测：路由率 / intake 触发率 / question 调用率 / 参数编译正确率，事件有 schema，与冻结基线配对对照。 | `events.schema.json` 的四个 `$defs`（`route_class` / `intake_loaded` / `question_called` / `params_compiled`）+ 三个报告 `--check` 门（`analyze_wave0.py`、`analyze_wave1.py`、`analyze_probe.py`）+ 语料 v1 sha256 冻结（`69bb6f25…`）与 v2（c19/c22 修正）+ 冻结基线配对（n=10）。 | `I/events.schema.json`；`R/corpus_version_note.md`；`I/test_corpus_schema.py`；`R/wave1_frozen10_report.md` §2；`R/wave1_report.md`；见 §6 的三条 `--check` 输出 | met | 四层 schema、三个 `--check` 门（本次复核全部 OK）、v1/v2 版本化与 baseline 配对均存在且可复算；配对 n=10 功效低是统计限制，不是分层可测性缺失。 |
| IS-5 | AGENTS.md ≤350 行；澄清协议单份存在于 intake skill；场景 C 为单向指针；全仓只有一份轮次规则。 | `tests/test_agents_md.py`（37 passed）：AGENTS.md 329 ≤ 350；turn-budget marker 只命中 `skills/intake/SKILL.md`；场景 C body 1 行且 intake 不含「场景 C」；产物索引 8×6、附录 27±2；`tests/test_config_render.py`（49 passed）。 | `OpencodeAgent/AGENTS.md`（329 行）；`OpencodeAgent/skills/intake/SKILL.md`（155 行）；`OpencodeAgent/skills/research-scenarios/SKILL.md`（场景 C 1 行）；`OpencodeAgent/tests/test_agents_md.py` | met | 结构断言全绿；单活源成立。字面「全仓一份」的唯一例外是计划 todo 2 强制的冻结对照快照 `I/fixtures/ask_first_protocol_snapshot.md`（test-only，不进运行时提示）与历史 wiki 文档（不加载）；测试范围按 AGENTS.md + `**/SKILL.md` 断言。 |
| IS-6 | 每阶段有可判假出口判据 + kill criteria；失败即回滚到上一稳定态。 | 每个出口判据的裁决记录：菜单门（`都不是` Wilson 上界 ≤ 35%）、槽位 kill（≥4/6 default-worthy）、hook kill（`intake_loaded` < 60%）、A/B `n_min=30`、Wave 1 40 条门、closing 90% 门、token/延迟 ≤10% 门；以及失败后的回滚动作是否有记录。 | `I/wave0_verdict.md`；`I/slot_value/verdict.md`；`R/wave1_report.md` §1；`I/ab/ab_report.md`；`R/wave1_frozen10_report.md` §5–6；`I/ab/ab_report.md` §8（回滚步骤） | partial | 判据与裁决齐全：菜单门 PASS（29.0% ≤ 35%）、槽位 kill 未触发（0/6 < 4/6）、hook 不触发（10/14 = 71.4% ≥ 60%）、A/B INCONCLUSIVE（n=10 < 30）；但「失败即回滚」未演练：Wave 1 40 条门 fail 未回滚、closing/延迟门 fail 只记 `[~]` 与 F-wave 跟进，且 Wave 2 todo 12/13（14:36/14:46）早于第一份 40 条 attempt 1 JSONL（17:40/17:54），出口门未在时序上阻断下一阶段。 |

## 2. 逐条证据与样本量台账

### 2.1 样本量台账（先看 n，再看结论）

| 信号 | n | 观测 | 来源 |
|---|---|---|---|
| `user_turns ≤ 2` | 1 个 session | 2 轮 | `R/two_turn_probe.md` |
| artifact 存在 | 6 个契约项 | 5/6（`c02` 无产物） | `R/wave1_frozen10_report.md` §3 |
| closing block 四行齐全 | 6 / 5 个分母 | 4/6 = 66.7%；有产物 4/5 = 80.0% | 同上 |
| 无未处理工具错误 | 11 / 40 | 0/11 `run_error`；40 条 attempt 1 有 1 个 `run_error`（`c17`） | `R/wave1_frozen10_report.md` §1；`R/wave1_report.md` §1 |
| 四层 agreement | 10 个配对 id | route 7/10 vs 6/10；intake 8/10 vs 5/10；question 8/10 vs 8/10；params 8/10 vs 6/10 | `R/wave1_frozen10_report.md` §2 |
| closing block（40 条） | 13 个 observed-route 产物 | 9/13 = 69.2% | `R/wave1_report.md` §1 |
| closing block（attempt 2） | 20 个 | 0/20 = 0.0% | `R/wave1_report.md` §2 |
| 运行时命令发现 | 1 个交付镜像 | 29 个命令，四个必需项在列；`/checkup` 非空回复 | `R/f3_real_qa.md` §2–3 |
| A/B 配对 | 10 对（有效 7 对） | INCONCLUSIVE；所需 N=55 | `I/ab/ab_report.md` |
| 槽位实验 | 72 runs / 24 cells | 6/6 槽位 INCONCLUSIVE；kill 未触发 | `I/slot_value/verdict.md` |

### 2.2 IS-1 — 四个可测信号，只有一个在 n=1 上同时成立

- **`≤2 用户轮`：仅 n=1。** 两轮探针（`c05`，session `ses_e7d0938b5ffezQWuSkvB9tOqkA`）turn 1 提出 3 问，turn 2 用 `1A 2B 3C` 收尾，session 内正好 2 条 user message；turn 2 exit 0、179 s。单轮 JSONL 不能观察第 2 轮，`ab_report.md` §7 明写 "Single-turn corpus records cannot observe turn 2; the two-turn probe is n=1 and is not a pair." 因此该信号**只在 n=1 上成立**，不能外推到 10/11/40。
- **artifact 存在：n=6 契约项 5/6。** 两轮探针写出 `/workspace/analysis/portfolio_2026-10-28/report.md`（12,432 bytes / 171 行）并更新 `analysis/_index.json`。但 IS-1 的字面 prompt `c02`（"今天买什么"）在探针中 **没有产物**：600 s cap 触发，恢复的 session 在 tool loop 中被截断，按契约计为 miss。
- **closing block：n=6 契约分母 4/6 = 66.7%；有产物分母 4/5 = 80.0%。** 四行齐全的只有 `c01/c03/c04/c07`；`c02` 无产物、`c27` 改写 T-1（见 §3.3）。阈值 90% → fail。
- **无未处理工具错误：n=11 探针 0 `run_error`，n=40 attempt 1 有 1 个。** 探针 44 条记录、11/11 四层齐全；40 条 attempt 1 的 `c17` 回测工具挂起产生 `run_error`。所以该信号在 n=11 上成立、在 n=40 上不成立。
- **`无需术语`：未被四信号代理测量。** 计划用四信号替代主观判断，本报告不额外声称它成立。
- **样本边界一句话：** `≤2 用户轮` **只在 n=1** 的两轮探针上成立；`无未处理工具错误` 在 **n=11**（探针）与 **n=40**（attempt 1）上有对照；`artifact` 与 `closing block` 的语料级分母只有 **n=6 个契约项**（嵌在 n=10/11 探针里），不是 n=40。四层 agreement 的配对分母是 **n=10**，加 `c27` 后为 n=11。任何把 n=1 的四个信号写成「语料级通过」的说法都不成立。

### 2.3 IS-2 — 「命令可发现」已证明，「每个产物都带 closing block」未证明

- **命令可发现：proven。** F3 在交付镜像 `opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4` 上执行 `GET /command?directory=/workspace`，返回 29 个运行时命令，`checkup/screen/backtest/review` 是其中的必需子集；`opencode run --command checkup` exit 0 且回复非空（`CHECKUP_REPLY_OK`）。这不是「文件存在」断言：端点是从 `GET /doc` 发现的，列表来自运行中的 server。
- **closing block 阈值：not proven（实测为 fail）。** 有产物分母 4/5 = 80.0%，契约分母 4/6 = 66.7%，都 < 90%；40 条 attempt 1 为 9/13 = 69.2%，attempt 2 为 0/20（20 个 300 s 超时）。操作者裁决把该机制定义为「契约 + 阈值」而非硬追加，所以 80% 是实测 gap，不能用「契约存在」替代。
- **F3 的 caveat：** 上述命令发现是交付镜像上的重跑；closing block 的 80% 来自 probe 镜像，不是交付镜像（见 §3.2）。

### 2.4 IS-3 — 文本反问合格，但结构化 question 与公共槽位默认化只有部分证据

- **操作者裁决（计划执行记录第 3 条）：** `clarification_surfaced = route_class == clarify_private OR question_called`；`question_called` 仅作诊断。headless `opencode run` 给每个 session 注入 `question: deny`，`OPENCODE_ENABLE_QUESTION_TOOL=1` 不生效（`R/question_tool_probe.md`：session permission 为 `{"permission":"question","pattern":"*","action":"deny"}`）。这是**已记录的限制，不是失败**。
- **私有槽位 + 结构化选择题：n=1。** 两轮探针 turn 1 的 3 个问题（体检哪一组持仓 / 成本价怎么算 / 仓位占比）各带 3 个选项（A/B/C，编号列表），每题有「以上都不是 / 未知」逃生门，并给出「默认 = 1A + 2B + 3C」的直接执行出口。`question` tool calls = 0，文本反问按裁决合格。
- **市场/期限/风险默认化 + diff 卡：结构存在，行为 n=1。** `skills/intake/SKILL.md` 的默认值表把市场=A股、期限=1-3个月、风险=最大回撤 10%、风格=均衡、候选池=Top 10、持仓=空仓写成默认值；`research-scenarios` 的 closing block 第 2 行「换一个会怎样」即 diff 卡。两轮探针 turn 1 没有询问市场/期限/风险，符合默认化。但 `slot_value/verdict.md` 的 6 个公共槽位全部 INCONCLUSIVE，并明确写 "This experiment does not license defaulting any of them"；默认集是计划预先指定的，不是该实验的裁决产物。
- **headless/cron/IM：headless 已证，cron/IM 未测。** headless 路径不挂起（question 被 deny、无挂起）；cron/IM 只有 skill 文本约束（"非交互会话不得调用 question"），没有运行时断言。

### 2.5 IS-4 — 四层可测、三道重算门、语料版本化

- **事件 schema：** `I/events.schema.json` 是 draft 2020-12，`$defs` 含 `route_class` / `intake_loaded` / `question_called` / `params_compiled` 四个事件，每个 `judge_rule` 写明 observed 的推导顺序；`run_error` 是运营终态记录，不属于四层。
- **三个 `--check` 门（本次复核全部 OK）：**
  - `analyze_wave0.py --check` → `[ok] intake_load_rate.json, request_distribution.json and wave0_verdict.md match recomputation`
  - `analyze_wave1.py --check` → `--check OK (.../results/wave1_report.md)`
  - `analyze_probe.py --check` → `--check OK (.../results/wave1_frozen10_report.md)`
- **语料版本化：** `corpus40.jsonl` 是冻结 v1，sha256 `69bb6f256d5e2afa7a521015b40d22101e47ceec7f84a4acfacac4cdbd410630`，由 `test_corpus40_v1_is_frozen_before_the_label_correction` 钉住；`corpus40_v2.jsonl` 只改 `c19`/`c22` 的 `clarify_private` 标签，供探针与 A/B 使用。理由与消费者矩阵见 `R/corpus_version_note.md`。
- **与冻结基线配对：** `R/wave1_frozen10_report.md` §2 给出 10 个配对 id 的四层 agreement 与 McNemar p；n=10 功效低，报告明确只作审计、不作显著性声明。

### 2.6 IS-5 — 结构断言与行数预算

- `OpencodeAgent/AGENTS.md` = **329 行**（≤350）；`tests/test_agents_md.py` 37 passed，`tests/test_config_render.py` 49 passed（F2 复核）。
- 轮次预算 marker（`最多 1 轮`、`≤3 问`、`绝不重复问第二轮`、`追加 1 轮`、`一次问清`）只命中 `skills/intake/SKILL.md`；AGENTS.md 不复述。
- 场景 C body 为 1 行单向指针（"识别为开放型 / 待澄清型 → 加载 `intake` skill 执行漏斗与槽位澄清；本场景不再重复协议。"）， intake 不含「场景 C」。
- 产物索引 8 行 × 6 列；能力索引迁到 `research-scenarios` 附录，27 行（±2）。
- **唯一例外：** 计划 todo 2 要求逐字冻结旧协议，`I/fixtures/ask_first_protocol_snapshot.md` 因此保留第二份文本；它是 test-only 对照臂，不进运行时提示。历史 `mymain-wiki` 文档也描述旧规则，但不被 agent 加载。测试按 AGENTS.md + `**/SKILL.md` 的范围断言单活源。

### 2.7 IS-6 — 判据齐全，回滚未演练

| 出口判据 | 裁决 | 证据 |
|---|---|---|
| 菜单覆盖：`都不是` Wilson 上界 ≤ 35% | **PASS**：3/26 = 11.5%，上界 29.0% ≤ 35%；条件工作②不激活，todo 9 放行 | `I/wave0_verdict.md` Verdict 2 |
| 槽位 kill：≥4/6 default-worthy | **未触发**：default-worthy 0/6 < 4/6；6/6 INCONCLUSIVE → hold-inconclusive | `I/slot_value/verdict.md` |
| hook kill：`intake_loaded` < 60% | **不触发**：attempt 1 的 `open_ended ∪ clarify_private` 子集 10/14 = 71.4% ≥ 60%；pre-migration 0/40 是构造性零，不用于激活 | `R/wave1_report.md` §1；`I/wave0_verdict.md` Verdict 1 |
| A/B `n_min = 30` | **INCONCLUSIVE**：n_pairs=10（有效 7）< 30；所需 N=55；不声称方向 | `I/ab/ab_report.md` |
| Wave 1 40 条门 | **fail / 未完成**：attempt 1 route 4/10 vs 6/10、params 5/10 vs 9/10；attempt 2 route 3/10 vs 6/10、params 0/10 vs 9/10；todo 6/9 记 `[~]` | `R/wave1_report.md` §1–2；计划执行记录第 2 条 |
| closing 90% 门 | **fail**：4/6 = 66.7%（有产物 4/5 = 80.0%）；记 `[~]` + F-wave 插件跟进 | `R/wave1_frozen10_report.md` §3、§6 |
| token/延迟 ≤10% 门 | **fail**：tokens −2.5% 通过，wall +40.6% 失败 | `R/wave1_frozen10_report.md` §5 |

- **回滚条款未演练。** `ab_report.md` §8 预写了 ASK_FIRST 时的 `git revert` 序列（todo 6/8/9 + 从快照恢复 AGENTS.md:66-125 与场景 C），但 A/B 没有返回 ASK_FIRST，所以未触发。Wave 1 的 40 条门 fail 后没有回滚，而是把 todo 6/9 改成 `[~]` 并记录 gap；closing/延迟门 fail 后也没有回滚，而是登记 `F-wave: closing-block hard-append plugin`。这两条是**已接受的前向修复**，但它们说明「失败即回滚到上一稳定态」没有被执行。
- **时序证据：** Wave 2 的 todo 12（`7d606d3e`，14:46）和 todo 13（`f3d39b40`，14:36）早于第一份 40 条 attempt 1 JSONL（`wave1_corpus40.jsonl` 17:40 / `…attempt1.jsonl` 17:54）。计划的「Wave 1 完成后必须用 40 条语料…才进入 Wave 2」在时序上没有成立。

## 3. Cross-gate conflicts

### 3.1 F1/F2 APPROVE vs F3 REJECT：不是矛盾，是三个不同的门

| 门 | 裁决 | 审的是什么 | 是否覆盖行为面 |
|---|---|---|---|
| F1 计划符合性 | **APPROVE**（复审；初审 REJECT 的 6 条 blocking 已 resolved） | todo 交付物、Acceptance 证据、commit 粒度、DCO、AI trailer | 否；40 条行为门由 F3 承担 |
| F2 代码质量 | **APPROVE**（Re-check；初审 REJECT 的唯一 blocker 是 `smoke-v311.sh` 钉死 328 行，`ec22686e` 改为 `-le 350` / `-ge 300` 后 resolved） | pytest 184 passed、六条 `--check`、`docker compose config`、结构断言强度 | 否；F2 只做静态/离线检查 |
| F3 真实 QA | **REJECT** | 从 HEAD 全量构建 + smoke + 四条 `/` 命令运行时触发 + 40 条语料门 + token/延迟预算 | 是；40 条门未执行，probe 的 80% closing 与 +40.6% wall 均未达标 |

**读法：** F1/F2 的 APPROVE 说明「计划文档与代码质量过关」，F3 的 REJECT 说明「理想态行为未被证明」。两者可以同时成立；对 IS-1/IS-2/IS-6 的判定，F3 的 REJECT 是决定性的。F1 报告也明确把 40 条行为证据列为 `[~]` 的 gap，F2 报告明确说 pytest 全绿不能证明 smoke/行为门是绿的。

### 3.2 Probe 镜像 vs 交付镜像的行为漂移（F3 §4）

- **probe 证据来自** `opencode-serve:v3.11.1-onboarding-20261028-71572199-fix1`，构建于 2026-10-28 18:29，tree `71572199`。
- **交付镜像是** `opencode-serve:v3.11.2-onboarding-20261028-4aab6cf4`，构建自 `4aab6cf4`。
- **行为面 diff 非空**（`git diff --stat 71572199..b19d1793 -- OpencodeAgent/AGENTS.md OpencodeAgent/skills OpencodeAgent/config OpencodeAgent/config/command`）：

  ```text
   OpencodeAgent/AGENTS.md              | 2 +-
   OpencodeAgent/skills/intake/SKILL.md | 1 -
   2 files changed, 1 insertion(+), 2 deletions(-)
  ```

  - `AGENTS.md` 第 4 行 VT MCP 工具规模 `89 / 94` → `94 / 99`（`bb27b8d4`）；这是系统提示里的文本，会改变模型看到的工具数量与调度工具描述。
  - `skills/intake/SKILL.md` 私有槽位默认值表删除「资金性质」一行（`2b615dae`）；这直接改变可提问的私有槽位集合。
  - 更广的差异：`d90d5308`/`bb27b8d4` 的 strategy control-plane 合并改了 `Dockerfile`、`entrypoint.sh`、`scripts/check_startup_readiness.sh`、`agent/mcp_server.py`、`agent/src/agent_schedules/*`、`state_export/*`、`strategy_runs/*`；交付构建 vendor **1349** 个 Python 文件（probe 构建 1329），entrypoint 记录 `VT MCP server OK — 99 tools registered`。
- **因此：** probe 的四层一致率、closing 80%、两轮探针、token/延迟都**不是**交付镜像的测量；只有结构性/静态部分（命令文件与 frontmatter、config 渲染、运行时命令发现、`/checkup`）在交付镜像上重跑过。
- **HEAD 前移的等价性：** F3 构建时 HEAD `4aab6cf4`；`4aab6cf4..b19d1793` 只改了三个报告文件（`f1_plan_conformance.md`、`f2_code_quality.md`、`f3_real_qa.md`），这些不在镜像 `COPY` 列表里。F3 §1.2 对 `4aab6cf4` 与 `07cee224` 的 vendor tree 做过等价性重建（两边 2250 个文件、摘要相同），但 npm 预烘层不可复现，`07cee224` 变体本身没有重跑 smoke。

### 3.3 `c27` 的 closing block / `T-1` miss

- `c27`（个股体检）**有产物**（text_chars 2363），但把契约要求的 `仅供研究，不构成投资建议；数据截止 T-1。` 改写成 `数据截止 2026-10-28`。`checks.find_closing_block` 的四行精确匹配因此报 `complete=false`，缺四行；这是**契约合规 miss**，不是路由 miss。
- `c27` 的 observed route 还是 `refuse`（拒答优先分类器误路由），与 `c06` 执行而非澄清、`c09` 执行预测而非回答一起，是 probe 记录的三处路由缺陷之一。
- `c02` 的 miss 不同：600 s cap 触发、恢复的 session 在 tool loop 中被截断，**没有产物**；按契约计为 miss。
- 结果：closing block 有产物分母 **4/5 = 80.0%**、契约分母 **4/6 = 66.7%**，阈值 90% → **fail**。操作者已裁决该机制在插件落地前是「契约 + 阈值」，所以本报告不把 80% 写成通过，也不把 `c27` 的改写隐藏为路由问题。

### 3.4 其他 QA 噪声（记录，不改变裁决）

- F3 的第一次 smoke 在 Stage 2 打印头之后没有出现 `CONFIG_READY`，15 分钟后被工具超时终止；同一镜像、同一命令的第二次 smoke 在 `CONFIG_READY after ~15s` 后完整通过。F3 把它归因为 `smoke-v311.sh` 的 readiness 循环 `curl -sf` 没有 `--max-time`，单次请求可能挂过名义 300 s 预算——属于 harness 硬化项，不是可复现的镜像失败。
- F3 的构建日志有一条非致命依赖告警（`alpaca-trade-api` 要求 `urllib3<2` / `websockets<11`，环境是 `urllib3 2.8.0` / `websockets 17.3`）；pip 继续并完成安装。F3 不对依赖策略下结论。

## 4. Proposed follow-up todos

以下 4 项按计划现有语法书写；N 从 15 续。每项都给出「为什么在本环境关不掉」与「关闭命令」。这些条目**不**修改计划文件，由 orchestrator 决定是否落入 todo 列表。

- [ ] 16. 在交付镜像上完成 40 条语料门 — 40/40 条目产生终态记录；冻结十的四层 agreement 点估计不低于基线（route ≥6/10、intake ≥5/10、question ≥8/10、params ≥6/10）；closing block 在 40 条契约分母上 ≥90%；未处理的 `run_error` 为 0。
  - **为什么本环境关不掉（推理吞吐 / 缺少更快主机）：** 本机 arm64 + QEMU 上三次 40 条尝试分别出现 1 个 `run_error`（`c17` 回测挂起）、20 个 300 s 超时、驱动卡死 83 分钟；per-item 预算提到 900 s 后仍有 2 个超时。继续重跑只会重复 F3 §7 的三种失败形状。
  - **关闭命令：**
    ```bash
    cd OpencodeAgent
    conda run -n legonanobot python tests/evals/intake/run.py \
      --container opencode-web --corpus tests/evals/intake/corpus40_v2.jsonl \
      --timeout 900 --out tests/evals/intake/results/wave1_corpus40_delivered.jsonl \
      --text-out tests/evals/intake/results/wave1_texts_delivered
    conda run -n legonanobot python tests/evals/intake/analyze_wave1.py \
      --input tests/evals/intake/results/wave1_corpus40_delivered.jsonl --write
    ```
    （`--input` 是本 todo 要为 `analyze_wave1.py` 补的唯一参数；现有分析器固定读 attempt1/fix1。其余 CLI 已存在。）

- [ ] 17. closing block 硬追加插件与 40 条出现率复测 — 镜像内插件在 `experimental.text.complete` 上按 `messageID` 去重追加四行；session API 读回的持久化 assistant part 含四行；40 条契约分母出现率为 100%，或如实记录插件开启后的 ≥90% 实测值（不得用「契约存在」替代）。
  - **为什么本环境关不掉（插件加载路径未打通 + 推理吞吐）：** `plugin_feasibility.md` 的 5 次 probe 都没有观察到 `plugin.added` 或 evidence 文件，结论仍是 `static-only`；hook 的静态面支持 `output.text` 回写，但运行时加载路径未证明。40 条复测同样受推理吞吐限制。
  - **关闭命令：**
    ```bash
    cd OpencodeAgent
    DOCKER_BUILDKIT=0 ./build.sh --tag v3.12.0-closing-block-$(git rev-parse --short HEAD)
    docker compose up -d
    docker exec -w /workspace opencode-web opencode run --attach http://localhost:4096 \
      --dir /workspace --format json "帮我看看 600519"
    conda run -n legonanobot python tests/evals/intake/run.py \
      --container opencode-web --corpus tests/evals/intake/corpus40_v2.jsonl \
      --timeout 900 --out tests/evals/intake/results/wave1_corpus40_plugin.jsonl \
      --text-out tests/evals/intake/results/wave1_texts_plugin
    conda run -n legonanobot python -m pytest tests/test_closing_block.py -q
    ```
    验收同时检查 probe 会话的持久化 part（`GET /session/{id}/message`）含 marker，而不只是 stdout。

- [ ] 18. ask-first vs artifact-first 配对 A/B 达到 n≥30 — `n_pairs ≥ 30`（有效 artifact-bearing 对 ≥30）；`ab_analysis.py --write` 输出 p 值与效应方向；若仍不足则按预注册规则给出 INCONCLUSIVE 与所需 N，不得以 n=10 的 pilot 代替。
  - **为什么本环境关不掉（缺少更快主机 / 推理吞吐）：** 30 对需要 60 次会话，`ab_report.md` 与计划执行记录第 8 条估计约 5 小时连续推理；本机没有更快主机，且当前 pilot 只有 10 对（有效 7）。
  - **关闭命令：**
    ```bash
    cd OpencodeAgent
    conda run -n legonanobot python tests/evals/intake/ab/run_ab.py \
      --container opencode-ab-control --corpus tests/evals/intake/ab/corpus_ab.jsonl \
      --out tests/evals/intake/ab/control_full.jsonl \
      --text-out tests/evals/intake/ab/texts_control
    conda run -n legonanobot python tests/evals/intake/ab/run_ab.py \
      --container opencode-ab-experiment --corpus tests/evals/intake/ab/corpus_ab.jsonl \
      --out tests/evals/intake/ab/experiment_full.jsonl \
      --text-out tests/evals/intake/ab/texts_experiment
    conda run -n legonanobot python tests/evals/intake/ab/ab_analysis.py \
      --control tests/evals/intake/ab/control_full.jsonl \
      --experiment tests/evals/intake/ab/experiment_full.jsonl \
      --corpus tests/evals/intake/ab/corpus_ab.jsonl --write
    ```
    若裁决为 ASK_FIRST，执行 `ab_report.md` §8 预写的 `git revert` 序列（todo 6/8/9）并从 `fixtures/ask_first_protocol_snapshot.md` 恢复协议；这是 IS-6 回滚条款的唯一演练路径。

- [ ] 19. 语料级两轮预算测量（把 IS-1 的 `turn ≤2` 从 n=1 提升到 n≥10） — ≥10 条 `open_ended ∪ clarify_private` 条目在固定回答（`1A 2B 3C` 或按题面回答）后产生第 2 轮；每条 `user_turns ≤2`、artifact 存在、closing block 四行齐全；任何未完成项写 `run_error`，不得补写。
  - **为什么本环境关不掉（harness 单轮限制 + 推理吞吐）：** 现有 `run.py` 每条只投递一次，`ab_report.md` §7 明写单轮 JSONL 不能观察 turn 2；两轮探针是手工在同一 session 上续跑一次，n=1。要语料化必须先加 `--turn2` 续跑模式，且每条第 2 轮 179 s 起。
  - **关闭命令：**
    ```bash
    cd OpencodeAgent
    conda run -n legonanobot python tests/evals/intake/run.py \
      --container opencode-web --corpus tests/evals/intake/corpus40_v2.jsonl \
      --only c02 --only c03 --only c05 --only c06 --only c19 --only c22 \
      --timeout 900 --turn2 "1A 2B 3C" \
      --out tests/evals/intake/results/two_turn_corpus.jsonl \
      --text-out tests/evals/intake/results/two_turn_texts
    ```
    （`--turn2` 是本 todo 的交付物；其余参数沿用现有 `run.py`。）

## 5. Overall fidelity statement

- **`met`：2 条** — IS-4（四层 schema + 三个 `--check` 门 + 语料 v1/v2 版本化 + 冻结基线配对）、IS-5（329 行 AGENTS.md + 单活源 + 场景 C 单向指针 + 结构断言 37 passed）。
- **`partial`：4 条** — IS-1（四信号只在 n=1 上同时成立；`c02` 无产物；closing 80% < 90%）、IS-2（命令发现已证明，closing 阈值未达标）、IS-3（n=1 的私有槽位文本三问；question 工具不可用；公共槽位默认化无实验授权）、IS-6（判据齐全，回滚未演练）。
- **`unmet`：0 条。**
- **明确结论：本计划没有完全交付到理想态（not fully delivered to the ideal state）。** 阻断点是 F3 的 **REJECT**：40 条语料门没有在交付镜像 `v3.11.2-onboarding-20261028-4aab6cf4` 上执行；可用的替代证据只到 11 条有界探针（n=10 配对），其 closing block 为有产物分母 4/5 = 80.0%、契约分母 4/6 = 66.7%，低于 90% 阈值，且 `c02` 无产物、`c27` 改写 `T-1`。结构面（IS-4/IS-5）可以判 met，但不能用它替代行为面。
- 本报告与计划现状一致：todo 6/8/9/14 为 `[~]`，F3 在本报告之后也应保持 `[~]`（本报告不修改 plan checkbox）。

## 6. 附录：本次复核执行的离线命令

```text
$ cd OpencodeAgent && conda run -n legonanobot python -m pytest tests/ -q
184 passed in 0.89s

$ conda run -n legonanobot python tests/evals/intake/analyze_wave0.py --check
[ok] intake_load_rate.json, request_distribution.json and wave0_verdict.md match recomputation

$ conda run -n legonanobot python tests/evals/intake/analyze_wave1.py --check
--check OK (.../results/wave1_report.md)

$ conda run -n legonanobot python tests/evals/intake/analyze_probe.py --check
--check OK (.../results/wave1_frozen10_report.md)

$ wc -l OpencodeAgent/AGENTS.md
329 OpencodeAgent/AGENTS.md

$ git diff --stat 4aab6cf4..b19d1793 -- . ':!mymain-wiki' ':!.omo/notepads'
 .../results/f1_plan_conformance.md    |  27 ++-
 .../results/f2_code_quality.md        |  55 ++++-
 .../results/f3_real_qa.md             | 242 +++++++++++++++++++++++

$ git diff --stat 71572199..b19d1793 -- OpencodeAgent/AGENTS.md OpencodeAgent/skills OpencodeAgent/config OpencodeAgent/config/command
 OpencodeAgent/AGENTS.md              | 2 +-
 OpencodeAgent/skills/intake/SKILL.md | 1 -
```

**未执行：** 容器启动、镜像构建、任何 LLM 调用、40 条语料重跑、11 条探针重跑。本报告的所有行为数字均来自已提交的 evidence 文件；本次新增的只有离线重算门的复核。