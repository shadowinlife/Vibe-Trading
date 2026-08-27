# HARNESS_EVOLUTION · B 批暴露面工程 — 工作计划 + 测试计划（预注册）

> 状态：**执行版** ｜ 日期：2026-08-27 ｜ 维护者：opencode (Sisyphus)
> 上游依据：`HARNESS_EVOLUTION_ROADMAP.md` §3 工作流 B（B1-B5 详情卡）、§7（E2 终局）、
> `HARNESS_EVOLUTION_P0_PLAN.md` §7（DEC-1 已决：方案甲）、§8（上游裁决表）、
> `agent/src/evals/tool_selection/artifacts/llm_judge_design.md`（"Known methodology gaps"）。
> 本文定位：ROADMAP §0.2 约定的 B 批分拆产物——工作计划（TASK 卡）+ 测试计划 + **改进后的评测阈值（预注册判据）**。判据先于实验冻结，禁止事后改阈值。

---

## 0. 摘要（TL;DR）

- **执行范围**：B1（key 门控工具注册时门控）、B2（trading_* 条件暴露）、B3（运维工具移出 MCP 默认面）、B4（list_skills 补 category）、B5（按 DEC-1 降级 MCP 技能面）。全部 5 项；DEC-1 已决（方案甲），B5 无阻塞。
- **主导假设**（E2 终局 + PAPERS §F）：路由准确率的主导变量是**每次决策呈现的工具数量**，不是描述措辞。B 批裁剪无能力工具（无 key 必失败 / 无连接器纯死重 / 运维工具占研究面），预期**路由非劣 + 披露税大幅下降**。
- **判官面板**：沿用 `judge_config_a5a8.yaml` 两档 SOTA 开源模型 —— `qwen3.8-max`（主）+ `kimi-k3`（敏感性），同键 DashScope，temperature 0。预算无限。
- **阈值改进**（对 A6-A8 review 的 4 个方法学缺口逐条闭合，§5）：功效对齐阈值（主效力面预指定 + δ 按实测功效定）、margin 非劣（CI 下界判定，不再"无显著回归=非劣"）、strict 单一主口径、判官重测噪声地板。
- **预期暴露面**（无 key / 无连接器干净环境）：MCP 74 → **59**（B1 −5、B2 −8、B3 −2）；AGENT 无 key 注册表 106 → **89**（B2 对 17 个 trading_* 整体门控，见 §2.2 范围扩展）。
- **披露税预期**（AUDIT §5 估算，实测以语料 token 核算为准）：MCP 面 −~10.5k token/轮。

---

## 1. 执行前勘察（对 ROADMAP 假设的校正）

| # | 发现 | 影响 |
|---|---|---|
| K1 | opencode 全局配置的 vibe-trading MCP 注册 = `python /Users/mgong/LegoNanoBot/Vibe-Trading/agent/mcp_server.py`（直接跑仓库源码，非 pip 包） | 源码改动对**新启动**的 MCP 会话即时生效；当前会话的常驻 MCP 进程不感知。测试一律用新进程（MCP 协议子进程 / 新 `opencode run`），并在测试前核实注册指向（§6.3） |
| K2 | `mcp_server.py` **不加载** `.env`（无 load_dotenv）；`get_env_config()` 只读 `os.environ` | MCP 进程的 key 可见性 = 启动方进程环境。这与现状调用时门控的语义一致（看不见 key 本来就调用失败），故注册时门控不引入新行为差异；但"有 key 环境"测试必须把 key 注入进程环境，而非写 agent/.env |
| K3 | `list_profiles()` 恒返回 13 家券商的内置 seed profiles（`BUILTIN_PROFILES`），永不为空 | ROADMAP B2 的"无连接器 profile 配置"**不能**以 `list_profiles()` 为空判定；必须定义"用户已配置"探针（§2.2 设计决策 D-B2） |
| K4 | 各连接器 `check_status()` 在配置检查后部分会走网络（如 alpaca 直接 `get_account_snapshot`） | 注册时门控只能用**无网络**的配置完备性检查（`load_config()` + `_missing_fields()` 层），不可聚合 `check_status()` |
| K5 | `trading_connector_tool.py` 实际定义 **17 个** trading_* 工具（8 个在 MCP 面，9 个仅 agent 面：rehab / capital_flow / capital_distribution / history_deals / acc_cash_flow / financials / earnings_calendar / place_order / cancel_order） | B2 门控落在工具类 `check_available()` → 对 17 个整体生效（§2.2 范围扩展） |
| K6 | swarm preset 无一引用 `reap_stale_runs` / `refresh_strategy_evidence` / trading_*（grep 核实，crypto_trading_desk 仅名称含 trading_ 子串） | B2/B3 无 swarm 白名单破坏面 |
| K7 | `test_readme_counts.py::_mcp_tool_names()` 为**进程内**枚举（导入 mcp_server 直接 list_tools）——B1 后该计数将依赖测试进程环境 | 锚点测试必须改为子进程 + 清空门控变量 + 临时 VIBE_TRADING_HOME 的确定性测量（§3 全局验收规则） |
| K8 | FastMCP 3.2.4 提供 `remove_tool(name)`；镜像注册已有 `mcp.add_tool(FunctionTool(...))` 先例 | 注册时门控实现 = 装饰器注册照旧 + 模块末统一 `remove_tool`（最小爆炸半径，不动 74 个包装函数签名） |
| K9 | `queries.yaml` 158 条中 **15 条** expected 落在将被门控的工具上（iwencai×1、get_macro_series×2、trading_*×8、qveris_*×4） | 主效力集 = 143 条 × 2 模型 = 286 配对观测；15 条转为"期望缺席行为探针"（§5.3）。**实测核对的缺席 ID（2026-08-27，queries.yaml 权威）**：D01-007（iwencai_search）、D11-001、D11-002（get_macro_series×2）、D16-001..D16-008（trading_*×8）、D18-001、D18-002、D18-003、D18-005（qveris_*×4） |
| K10 | `Skill.category` 数据模型已存在（`src/agent/skills.py:37`，frontmatter 加载），MCP `list_skills` 仅输出 name+description | B4 纯输出层改动，双面（MCP 工具 + API `/skills` 路由）补齐 |

---

## 2. 工作计划（TASK 卡）

> 共同约定：每个暴露面变更 commit 必须同步 `test_readme_counts.py` 锚点机制与 6 份 README 正文（全局验收规则，§3）；提交规范 = Conventional Commits + DCO sign-off + `Part of #1218.`（禁 Co-Authored-By / AI 追溯行，仓库根 AGENTS.md）；prompt 缓存纪律——B 批不改既有工具描述文本结构（B5 的降级句为追加式）。

### 2.1 TASK-B1 · key 门控工具改 MCP 注册时门控（P1，仅 MCP 受益）

- **目标**：`qveris_*`×3、`get_macro_series`、`iwencai_search` 在无对应 key 的 MCP 会话中不出现在 `tools/list`——对齐 agent 侧 `check_available()` 语义（AUDIT Q12）。
- **落点**：`agent/mcp_server.py`。
- **实现**：
  1. 新增 `_apply_exposure_gates()`（模块末、全部注册完成后调用）：遍历 `_key_gated_tool_classes()`，`not cls.check_available()` → `mcp.remove_tool(name)`，记 INFO 日志（指明缺哪个 env，复用工具类判定源，禁止第二套 key 检测逻辑）；
  2. `_execute_key_gated` 保留为调用路径的纵深防御（不删）；
  3. 门控函数设计为可扩展容器——B2/B3 的门控项并入同一函数（单一收口，测试只打一个靶）。
- **验收**（确定性）：
  - 无 key 进程 `tools/list` 不含 5 工具（子进程实测，计数 74→69）；
  - 有 key 进程（环境注入）5 工具在列且可调用（调用路径回归 = 现有门控测试通过）；
  - 单测：`_apply_exposure_gates` 在 monkeypatched env 下的移除/保留两分支。
- **量化**：披露税 −~3.5k token/轮（以语料实测为准，§6.2）。

### 2.2 TASK-B2 · trading_* 条件暴露（P1，AGENT+MCP 双面生效）

- **目标**：无任何连接器配置时，trading_* 不注册（AUDIT Q10/K21）。
- **设计决策 D-B2（"已配置"语义，勘察 K3/K4 的必然产物）**：新增 `src/trading/availability.py::has_configured_connector() -> bool`——**无网络、可缓存**的并集探针，任一为真即"已配置"：
  1. **选择标记**：`trading-connections.json` 存在（用户执行过 `connector use` / `trading_select_connection`）；
  2. **本地配置**：IBKR local 配置文件存在（`connector configure` 产物）；
  3. **凭据完备**：任一 broker_sdk 连接器新增的公开 `is_configured()` 为真——各 SDK 模块把现有 `load_config()` + `_missing_fields()` 逻辑包成无网络、不依赖可选 SDK 包的公开函数（配置/凭据完备性，不做连通性探测）；
  4. **OAuth/远程配置**：remote_mcp profile 的 OAuth 缓存 / agent.json MCP 配置存在（robinhood、ibkr-live-official）；
  5. **本地插件**：`discover_plugins()` 非空（用户安装过只读连接器插件）。
  - **误判方向权衡**：探针取宽并集，宁可漏关（保留死重）不可误关（隐藏可用工具 = 路由回归）。新增连接器必须登记探针（文档化义务，写入 availability.py 模块 docstring）。
- **落点**：
  1. `src/trading/availability.py`（新）+ 各 `src/trading/connectors/*/sdk.py` 的 `is_configured()`；
  2. `trading_connector_tool.py`：17 个工具类统一加 `check_available()`（共享模块级辅助，结果缓存——build_registry 会逐类调用）；
  3. `mcp_server.py::_apply_exposure_gates()`：`not has_configured_connector()` → 移除 8 个 MCP trading 包装。
- **范围扩展声明**：ROADMAP B2 文本为"8 个 trading_*"（MCP 面视角）；因门控落点 = 工具类（§1.1 映射规则），agent 面 9 个仅内部 trading_* 同享死重逻辑，一并门控，避免"trading_account 隐藏而 trading_rehab 显示"的语义分裂。README 的 agent 树计数随之 106→89。
- **验收**（确定性）：
  - 干净环境（临时 `VIBE_TRADING_HOME` + 清空连接器凭据 env）：两面均不含 trading_*（MCP 69→61；agent 注册表子进程实测）；
  - 逐项翻转探针各分支（选择标记 / ibkr 配置 / okx env 凭据 / 插件）→ 恢复注册（参数化单测）；
  - `trading_check` 等工具行为不变（只动注册，不动 execute）。
- **量化**：披露税 MCP 面 −~5.6k token/轮、AGENT 面同比例（实测）。

### 2.3 TASK-B3 · 运维工具移出 MCP 默认面（P2）

- **目标**：`reap_stale_runs` / `refresh_strategy_evidence` 不再默认披露（AUDIT Q13）。
- **落点**：`mcp_server.py::_apply_exposure_gates()` 无条件移除两项（MCP 面）。
- **范围决策**：仅 MCP 面。agent 面保留——swarm 白名单经 `build_swarm_registry` 从全量注册表过滤，移出 agent 注册表将破坏潜在白名单引用（K6 虽核实当前无引用，但 agent 面裁剪属 C2 披露层级职责，先行移出仅限 MCP，ROADMAP 明示"可先行移出"）。MCP 面移出后两工具经 C1（search_tools 懒加载）恢复可达，本期接受 MCP 暂不可达。
- **验收**：默认面不含两工具（61→59）；agent 注册表仍含（子进程断言）；现有调用两工具的测试改走 agent 面或调整预期。
- **量化**：披露税 −~1.4k token/轮。

### 2.4 TASK-B4 · list_skills 输出补 category（P1）

- **目标**：`list_skills` 输出 `{name, description, category}`（AUDIT Q11）。
- **落点**：`mcp_server.py::list_skills`（工具面）+ `src/api/system_routes.py` 的 `/skills` 路由（同面对齐，防漂移）+ 两处 docstring。
- **验收**：输出含 category 且取值 ∈ frontmatter 9 类（断言）；90 技能全覆盖（90/90）；`test_readme_counts.py` 技能锚点不受影响（计数不变）。
- **量化**：category 覆盖率 = 90/90。

### 2.5 TASK-B5 · 双暴露降级 MCP 侧（P1，依 DEC-1 方案甲）

- **目标**：落实 DEC-1 要素 2——MCP 侧 `list_skills`/`load_skill` 保留但降级（纯 MCP 客户端仍需此面，不关闭）。
- **落点**：
  1. 两工具描述**追加**宿主优先指引句（追加式，不重排）："宿主已提供技能面（如 opencode 的 .opencode/skills）时优先走宿主原生技能路径；本工具服务于纯 MCP 客户端。"（英文等价句式，与现有描述语言一致）；
  2. **宿主环境探测评估成文**（DEC-1 将其列为 B5 评估项）：考察可用信号（启动目录 `.opencode/skills` 存在性、`VIBE_TRADING_HOST_SKILLS` 显式 env、父进程特征）——本期结论：**不实施注册时自动降权**（信号在 pip/ClawHub 分发态不可靠，误判将损害纯 MCP 客户端），以描述指引 + 文档声明实现"有效暴露路径 = 1"；评估记录写入裁决文档；
  3. `list_skills` 输出头部附一行提示（宿主环境探测命中时）列为后续选项，本期不做。
- **验收**：描述含宿主优先句（关键词断言）；两工具功能不变（纯 MCP 客户端路径保留）；暴露路径语义 = 1 的论证写入裁决文档。
- **量化**：技能重复竞争面的**有效**消除（宿主会话内）；工具计数不变（74 基准中不扣减）。

### 2.6 TASK-ANCHOR · 锚点机制与 README 同步（全局验收规则的代码化）

- **目标**：`test_readme_counts.py` 在门控化暴露面下保持确定性（勘察 K7）。
- **落点**：
  1. `_mcp_tool_names()` 改子进程测量：清空 `_CREDENTIAL_GATES`（扩充 B2 的连接器凭据 env 清单）+ 临时 `VIBE_TRADING_HOME`（隔离文件型探针）→ 输出 keyless 表面工具名序列；
  2. README ×6：`**MCP tools exposed (N):**` 枚举行改为 keyless 表面（59）+ 紧随其后一行**条件工具说明**（5 key 门控 + 8 连接器门控 + 2 运维工具的恢复条件）——枚举测试对账 keyless 集合；`74 tools` 正文表述同步；agent 树计数 106→89 同步；
  3. 条件工具说明行的格式冻结为测试可断言的固定句式（防未来漂移）。
- **验收**：`pytest agent/tests/test_readme_counts.py` 在带/不带 key 的开发机上**同分**（确定性断言）。

---

## 3. 全局验收规则（每个 B 项 TASK 自动继承）

1. 每个暴露面变更 commit：`pytest agent/tests/test_readme_counts.py` + 受影响工具族测试通过；6 份 README 同步；
2. 全量门槛（批末）：`pytest --ignore=agent/tests/e2e_backtest --tb=short -q` 通过（允许记录既有失败，不允许新增）；
3. 干净环境实测：无 key / 无连接器子进程 `tools/list` = **59**（B 批完成后）；
4. 代码风格：black + ruff；文件 ≤400 行软约束（mcp_server.py 已超限，只减不增）；
5. 提交：每 TASK 一个 commit，DCO sign-off，`Part of #1218.`。

---

## 4. 测试计划（三层）

### 4.1 L1 · 确定性测试（零 LLM，CI 级）

| 测试 | 机制 | 通过判据 |
|---|---|---|
| 门控单测（B1/B2/B3） | pytest + monkeypatch env / 临时 VIBE_TRADING_HOME | 移除/保留两分支全过；探针各分支参数化翻转 |
| keyless MCP 计数 | 子进程 `mcp.list_tools()`（清门控 + 临时 home） | 74→69（B1）→61（B2）→59（B3），逐步断言 |
| keyless agent 注册表计数 | 子进程 `build_registry()`（既有 `_keyless_agent_tool_count` 模式扩充连接器门控） | 106→89（B2 后） |
| 有 key / 有配置恢复 | 子进程注入 env / 写临时配置 | 对应工具回到表面且可调用 |
| MCP 协议端到端 | 对 `python agent/mcp_server.py` 走 stdio JSON-RPC：initialize → tools/list → tools/call（list_skills 验 category） | 场景矩阵（§6.3）全过 |
| B4 覆盖断言 | list_skills 输出解析 | 90/90 技能含 category ∈ 9 类 |
| README 锚点 | test_readme_counts.py（改造后） | 带/不带 key 同分 |

### 4.2 L2 · MCP 模式实测（opencode + omo + vibe-trading MCP，用户指定组合）

- **注册版本核实（前置，每次源码变更后）**：opencode 全局配置的 vibe-trading MCP = 直接执行本仓库 `agent/mcp_server.py`（勘察 K1）——核实方式：新起 MCP 子进程读 `APP_VERSION` + 比对当前 HEAD；若注册指向变更为 pip 包则先纠正注册再测；
- **CLI 并发场景**（`opencode run`，多场景并行）：
  | 场景 | 环境 | 期望行为 |
  |---|---|---|
  | S1 无 key 宏观查询 | 清 FRED_API_KEY | 模型**看不到** get_macro_series，改走替代（web_search 等）且不幻觉调用已移除工具 |
  | S2 无连接器行情 | 临时 home（无配置） | "取 AAPL 行情"走 get_market_data，不误入 trading_quote（K21 仲裁生效的结构性保证） |
  | S3 有配置恢复 | 临时 home + 写选择标记 | trading_* 恢复可见 |
  | S4 技能路由 | 宿主面（本仓库 .opencode/skills symlink 在） | 技能经宿主面加载；MCP list_skills 仍可用（纯客户端兼容） |
- 注：opencode run 会话自带全部宿主工具，场景断言聚焦"是否调用了 VT 侧正确工具 / 是否尝试调用已移除工具"，从会话 trace 提取。

### 4.3 L3 · LLM-judge E2 式对比（语义级裁决，改进阈值后）

- **设计**：基线表面（B 批前冻结语料 `corpus_b_baseline.yaml`，74 工具）vs 后测表面（B 批后 keyless 语料 `corpus_b_post.yaml`，59 工具）；配对设计（每条 query 自身对照）；
- **面板**：`judge_config_a5a8.yaml`（qwen3.8-max 主 + kimi-k3 敏感性，temp 0，cap 不下调）；
- **规模**：2 模型 × 2 表面 × 158 query = 632 主调用 + 确定性探针（8×3×2 表面）+ 重测噪声地板探针（§5.2）≈ **~750 calls**；预算无限；
- **产物**：黄金 trace（模板 sha256 钉扎）+ 统计报告 + 裁决文档（`artifacts/b_batch_verdict.md`）。

---

## 5. 改进评测阈值（对 4 个方法学缺口的逐条闭合，预注册）

> 来源：`llm_judge_design.md` "Known methodology gaps (2026-08-27 review)"。本节为 B 批 L3 测试的**唯一判据来源**，实验前冻结。

### 5.1 缺口① 功效对齐阈值 → 主效力面预指定 + δ 按功效定

- **问题**：A7 靶点 n=120 对 3pp 阈值功效仅 25-47%，MDE 实为 6.5-9pp。
- **B 批闭合**：
  - **主效力面预指定 = 全集主效力集**（143 条非缺席 query，§1 K9）×2 模型池化 = **286 配对观测**；不设靶点集效力判据（B 批无描述靶点，改动是表面裁剪）；
  - **功效计算**：配对 McNemar 差值 Δ=(b−c)/n；基线池化 top-1≈0.90、设不一致对比例 ≤15% 时，SE(Δ)≈√(b+c)/n≈2.3pp，95% CI 半宽 ≈4.5pp；
  - **非劣边界 δ = 5pp**（覆盖最坏 CI 半宽，且 ≈ 天花板(0.90)到崩塌阈值空间的一半，路由意义可解释）；
  - 若实测不一致对比例显著偏离假设，报告实际 CI 并以**实际 CI 下界**裁决（δ 不事后放宽）。

### 5.2 缺口④ 判官重测噪声地板 → 先测噪声，后裁 delta

- **问题**：E2 qwen first-pick 一致率 0.9167（<0.95），小 delta 无法与判官噪声分离。
- **B 批闭合**：正式矩阵前跑 **test-retest 探针**——同一表面（post）× 每模型 20 条分层抽样 query × 2 次独立施测（独立 trace），报告 first-pick 一致率 `ρ_model`；
- **裁决规则**：池化 |Δtop-1| ≤ max(1−ρ_qwen, 1−ρ_kimi) 时，判为**噪声带内不可解释 → 记为无效应（诚实 null）**，不得解读为改进或回归；
- 探针与主矩阵同 cap 同模板（模板 hash 不变）。

### 5.3 缺口② margin 非劣 → CI 下界判定

- **问题**："无显著回归 = 非劣"统计无效（接受 H₀）。
- **B 批闭合**：非劣成立 ⟺ **池化配对差值的确切 95% CI 下界 > −δ（−5pp）**。实现：对不一致对 (b, c) 用精确二项 CI 推 Δ 的区间（或等价 Wilson 法，实现时固定其一并写入 stats 模块 docstring）。p 值仅作报告，不参与非劣裁决。
- **缺席探针（15 条）**：expected 工具不在后测表面 → 结构性 miss，**不入主效力集**；单独报告模型在缺席 query 上的替代选择分布（描述性，无准确率断言）——验证"模型不幻觉调用已移除工具"（候选表里没有即选不到，结构性保证）。

### 5.4 缺口③ 主口径指定 → strict 唯一主口径

- **问题**：strict/lenient 并列有事后择优风险。
- **B 批闭合**：**strict 为唯一主口径**——全部 §5.1/§5.3 判据只在 strict 上裁决；lenient 仅作敏感性分析报告，不得反转 strict 裁决。

### 5.5 预注册裁决表（B 批 L3 的"什么算通过"）

| # | 判据 | 阈值 | 主/辅 |
|---|---|---|---|
| C1 | 池化 strict 非劣（主效力集 286 观测） | 确切 95% CI 下界 > −5pp | **主（放行门槛）** |
| C2 | 噪声带规则 | 池化 \|Δ\| ≤ 噪声带 → 记无效应（不判改进亦不判回归） | 主（解释规则） |
| C3 | 分模型 strict 非劣 | 报告项，不设放行权 | 辅 |
| C4 | lenient 敏感性 | 报告项，不得反转 C1 | 辅 |
| C5 | 缺席探针 | 无"调用已移除工具"事件（结构性）；替代分布描述 | 辅 |
| C6 | 披露税降幅（确定性，非 LLM） | MCP 面语料 token 降幅 ≥ 8k/轮（对标 AUDIT 10.5k 估算的 76%） | **主（收益门槛）** |

**裁决树**：C1 过 + C6 过 → **B 批放行（暴露面裁剪收益成立且路由无损）**；C1 落入噪声带（C2）且 C6 过 → **放行但标注路由证据为"无损不可测"**（收益由 C6 独立承载）；C1 失败（CI 下界 ≤ −5pp）→ **否决，回滚对应暴露面改动**；C6 未达 → 收益不成立，即使 C1 过亦不上游。

---

## 6. 执行工作流（顺序与依赖）

```
Phase 0  预飞：
         0a  冻结基线语料 corpus_b_baseline.yaml（capture_corpus.py，先于任何 B 改动！）
         0b  判官冒烟：2 模型各 1 调用（key 可用、cap 合理）
         0c  基线计数复核：keyless MCP = 74（已核实 2026-08-27）
Phase 1  并行实施：
         轨 1（委派）eval 基建：§5 四缺口闭合（stats 模块 + retest 支持）
         轨 2（委派）B2 基建：availability.py + SDK is_configured + 工具类 check_available
         轨 3（主线）B1 → B3 → B4 → B5（mcp_server.py 串行，同文件防冲突）
                   每步：锚点测试 + README×6 同步 + 子进程计数断言
Phase 2  集成验证（L1 全量 + L2 opencode CLI 场景矩阵）
Phase 3  L3 LLM-judge：捕获 corpus_b_post.yaml → 噪声探针 → 主矩阵（2 模型并发）→ stats
Phase 4  裁决：对照 §5.5 预注册表出 b_batch_verdict.md；回写 ROADMAP/P0_PLAN；代币税实测
```

**放行门槛**：§5.5 裁决树。批末跑全量 pytest（§3 规则 2）。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| B2 探针误关（隐藏用户可用工具） | 宽并集设计（§2.2 D-B2）+ 分支参数化测试 + 文档化新连接器登记义务；误判方向明确为"宁漏关不误关" |
| README ×6 多次同步漂移 | 每暴露面 commit 内同步；锚点测试带/不带 key 同分作为漂移哨兵 |
| MCP 进程 env 可见性与用户预期差（K2） | 现状语义不变（看不见 key 本就调用失败）；条件工具说明行写明恢复条件 |
| 重测噪声过大致裁决不可解释 | §5.2 噪声带规则预注册——诚实 null 优于过度解读 |
| opencode 常驻 MCP 进程不感知源码变更（K1） | 全部测试用新进程；L2 场景前核实注册指向 + APP_VERSION |
| 缺席探针被误读为回归 | §5.3 预注册：结构性 miss 不入主效力集，单独描述性报告 |

---

## 8. 产物清单

| 产物 | 位置 |
|---|---|
| 本文档（计划 + 预注册判据） | `HARNESS_EVOLUTION_B_TEST_PLAN.md` |
| 基线/后测语料 | `agent/src/evals/tool_selection/corpus_b_baseline.yaml` / `corpus_b_post.yaml` |
| 黄金 trace + 探针记录 | `agent/src/evals/tool_selection/artifacts/llm_judge_trace_*_b*.jsonl` |
| 统计报告 | `agent/src/evals/tool_selection/artifacts/llm_judge_stats_report_b.md` |
| 裁决文档 | `agent/src/evals/tool_selection/artifacts/b_batch_verdict.md` |
| ROADMAP/P0_PLAN 回写 | §7/§8 家族章节追加 B 批结果 |
