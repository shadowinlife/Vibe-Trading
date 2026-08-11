# mymain 分支差异说明

> 维护者：shadowinlife ｜ 基线：HKUDS/Vibe-Trading `main` @ `c33133f4`（v0.1.13，2026-08-11 对齐，上游自 `6c44732` 前进 120 commit）

## 1. 分支定位

`mymain` 是 shadowinlife 个人维护的**锁定演进分支**，承载记忆系统 T4 迭代与本地 ClickHouse A 股数据源，功能上领先于社区 `main`。本分支作为个人生产/验证环境的稳定基线，所有改动最终以社区 PR 形式回流上游；PR 合入后相应条目从本文档移除，全部合入后本分支回归纯跟踪分支。

相对 `origin/main` 的差异总量：**38 个文件、+4408/−53 行**（含本文档与分支级 AGENTS.md 扩展）。历史组织为 **6 个单一功能 commit**（F1→F5 + docs），每个 commit 可独立作为社区 PR 候选。

## 2. 核心功能差异

### 2.1 独有 feature（相对上游）

| # | Feature | 能力 | 核心文件 | 开关 | 上游关系 |
|---|---|---|---|---|---|
| **F1** | 反思课程存储 | 按策略类型 append-only JSONL 课程库；标签/子串检索；置信度更新；`auto_reflect_from_run_dir` | `agent/src/memory/reflections.py`（新增）+ 测试 | `VT_MEMORY_REFLECTIONS`（被 `VT_MEMORY=full` 预设隐含） | 上游无对应（remember_tool / memory CLI 是不同暴露面） |
| **F2** | MCP 记忆工具 | 五个 MCP 工具 memory_save / recall / reinforce / reflect / status；never-raise dict 包络适配层；memory-lifecycle SKILL 工作流 | `agent/src/memory/mcp_adapter.py`（新增）、`agent/mcp_server.py`（注册段）、`agent/src/skills/memory-lifecycle/SKILL.md` | `VT_MEMORY_MCP_TOOLS`（默认 OFF；OFF=70 / ON=75 工具） | 上游无 MCP 面记忆工具（上游本轮扩展的是 quantlib / alpha zoo / 机构数据等其它域，MCP 面 62→70） |
| **F3** | 回测反思钩子 | `run_backtest` 成功后 fire-and-forget 提取 run_card 课程（MCP 与 in-process 入口均覆盖，非致命）；附延迟基准（bench marker，p50<200ms / p95<500ms）与 5 会话并发测试 | `agent/src/tools/backtest_tool.py`（钩子段）、`agent/tests/memory/test_latency_bench.py`、`test_concurrent_mcp.py`、`conftest.py`、`pyproject.toml` | 随 F1 联动 | 上游 post-backtest attribution 是 prompt 驱动，机制不同 |
| **F4** | MemoryGuard + 项目目录存储 | FastMCP middleware：工具调用后自动 memory_save + memory_reflect（零 LLM）；`VT_MEMORY_BASE_DIR` 支持记忆存项目目录；默认路径跟随 `get_runtime_root()`（`VIBE_TRADING_HOME` 感知） | `agent/src/memory/memory_guard.py`（新增）、`agent/src/memory/persistent.py`（`_default_memory_base`）、`agent/src/config/env_schema.py`（`MemoryConfig.base_dir`） | middleware **无条件注册**（债务 D1） | 上游 memory 仍锚定 `~/.vibe-trading`（上游本轮对 persistent.py 的改动仅 FTS5 排序衰减，不同关注面，两者并存） |
| **F5** | ClickHouse A 股数据源 | CH HTTP connector + OHLCV loader（DataLoaderProtocol）+ 基本面 Provider（回退 Tushare）+ 四只资金流工具 CH 优先回退；A 股 chain 与路由以 clickhouse 为首选 | `agent/src/clickhouse_connector.py`、`agent/backtest/loaders/clickhouse.py`、`agent/src/tools/clickhouse_fallbacks.py` 等 16 文件 | `CLICKHOUSE_*`（DataConfig） | 个人部署独有，不回流 |

### 2.2 已随对齐消除的历史分歧（上游已承接）

| 项 | 去向 |
|---|---|
| `run_gc(dry_run=True)` 压缩副作用门控 | 本分支 PR #973 **原样合入**上游（`397c76c`） |
| 层级路由 `.md` 后缀 writer 修复（PR #972） | 上游 #984 + 孤儿恢复 `5b638b2` + pin 测试 `9ae0f71` 等价落地；#972 的回归测试与注释被上游收编（Co-authored-by: shadowinlife） |
| README MCP 工具数同步（PR #974） | 上游 `7539577` 自行修正并新增 `test_readme_counts.py` 锚定 |
| 本地"读时容忍"无后缀条目（`_is_category_entry`） | 被上游 `recover_extensionless_entries()` 孤儿恢复取代，对齐时主动移除 |
| 本地 routed 命名 `<category>/{type}_{slug}.md` | 采纳上游 `<category>/<slug>.md`（上游 pin 测试明确排除本地方案） |

**2026-08-11 核查结论**：上游自 `6c44732` 前进 120 commit（v0.1.13 发布），逐项核对 F1–F5 均**未被上游取代**——上游本轮新增能力（quantlib_call / alpha zoo MCP 工具 / 机构持仓 / ETF 穿透 / 预测市场 / 论文检索 / 加拿大市场 / eToro / 桌面端等）与本地五项能力无重叠；本地 F4 的 `_default_memory_base()` 继续复用上游共享基础设施 `get_runtime_root()`，F5 沿用上游 loader 注册模式（`VALID_SOURCES` + `_loader_modules` + `FALLBACK_CHAINS`），无重复实现需要移除。

### 2.3 上游贡献队列（含代码点，按依赖序逐步推入以缩小分歧）

| 序 | Feature | 具体代码修改点 | 提交前置工作 |
|---|---|---|---|
| **① F4 路径部分** | memory 路径跟随 `VIBE_TRADING_HOME` | `persistent.py::_default_memory_base()`（改调用期求值）；新增旧路径 `~/.vibe-trading/memory` → 新根目录的一次性迁移 + 启动告警；新增 `VIBE_TRADING_HOME` 覆盖/迁移测试 | 同步更新上游文档"memory 仍锚定 ~/.vibe-trading"表述；`MemoryConfig.base_dir` 声明已就绪 |
| **② F1** | 反思课程存储 | `reflections.py` 整文件新增；`env_schema.py` 增加 `VT_MEMORY_REFLECTIONS` 与 `VT_MEMORY=full` 预设语义；`.env.example`；测试套件 | 顺手实施迭代笔记中的非阻塞建议（`_iter_lessons` 重命名、逐行读、自定义 encoder）减少 PR 往返 |
| **③ F2** | MCP 记忆工具 | `mcp_adapter.py` 整文件新增；`mcp_server.py` 五工具注册段（含 `VT_MEMORY_MCP_TOOLS` 门控）；`memory-lifecycle/SKILL.md`；**五份 README**（skills 89→90、Tool 类 10→11 及相关 prose）；`agent/SKILL.md`（skills=90、Finance Skills 小节标题）；测试 | README 计数更新必须随 PR 一并提交（上游 pin 测试强制）；注意上游 MCP 基数已变为 70（mcp_server.py 头注释按 70/75 表述） |
| **④ F3** | 回测反思钩子 | `backtest_tool.py` daemon 线程钩子；`conftest.py` bench marker；`pyproject.toml` markers；bench/并发测试 | 依赖 ②（反思存储 API） |
| **⑤ F4 中间件部分** | MemoryGuard | `memory_guard.py` 整文件新增；`mcp_server.py` 注册段 | **必须先解决 D1（加 env 门控开关）与 D2（dedup/增长）**，否则过不了社区评审 |
| ✗ F5 | ClickHouse | — | 暂不回流（个人部署独有） |

## 3. E2E 验证方式

### 3.1 测试套件与静态门禁（conda env `legonanobot`，macOS arm64 / Python 3.12）

```bash
# memory 套件（含上游孤儿恢复/GC/pin 测试与上游新增 FTS5 衰减测试）——基线 321 passed / 2 skipped
python -m pytest agent/tests/memory/ agent/tests/test_persistent_memory.py \
  agent/tests/test_memory_orphan_recovery.py agent/tests/test_memory_gc.py \
  agent/tests/test_env_schema.py -q

# ClickHouse 套件——基线 13 passed / 8 skipped（skip = 需真实 CH 连接）
python -m pytest agent/tests/test_clickhouse_loader.py \
  agent/tests/test_clickhouse_fundamentals.py agent/tests/test_clickhouse_flow.py -q

# README/SKILL.md 计数门禁——基线 54 passed（上游本轮新增 6 条 pin 测试）
python -m pytest agent/tests/test_readme_counts.py agent/tests/test_distribution_skill_manifest.py -q

# env-var AST 门禁——基线 exit 0（1 条 WARN 来自上游 llm.py，与本分支无关）
python tools/ci_env_var_gate.py

# 延迟基准（默认跳过，显式运行）
python -m pytest agent/tests/memory/test_latency_bench.py -m bench
```

### 3.2 端到端冒烟（本地）

```bash
# MCP 工具计数门控：OFF=70 / ON=75（本分支 5 个 memory_* 工具，上游基数 70）
cd agent
python -c "import asyncio, mcp_server; print(len(asyncio.run(mcp_server.mcp.list_tools())))"
VT_MEMORY_MCP_TOOLS=1 python -c "import asyncio, mcp_server; print(len(asyncio.run(mcp_server.mcp.list_tools())))"

# memory 工具往返（VT_MEMORY_MCP_TOOLS=1 启动后）
#   memory_save → memory_recall → memory_status 应返回 ok 包络；
#   memory_reflect 在 VT_MEMORY_REFLECTIONS 关闭时应返回 skipped 而非 error

# 回测反思钩子：VT_MEMORY_REFLECTIONS=1 跑一次 run_backtest，
#   成功后数秒内 <runtime_root>/memory/reflections/<策略类型>.jsonl 应新增一条课程

# ClickHouse 联邦取数（CH 可达时）：get_market_data 拉取 000001.SZ，
#   日志应显示 clickhouse 命中；断开 CH 后应静默回退 tencent/mootdx 网络链
```

### 3.3 远端登录与接入方式

| 场景 | 登录/接入方式 |
|---|---|
| **远端 ClickHouse**（F5 数据源） | `agent/.env` 配置 `CLICKHOUSE_HOST`（个人部署缺省 `172.24.165.51`）、`CLICKHOUSE_PORT=8123`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_DATABASE=ashare`；HTTP 接口，无 TLS，密码仅存本地 `.env`（不入 commit） |
| **远端 MCP 服务**（F2 工具暴露） | 服务端 `python mcp_server.py --transport http --host 0.0.0.0 --port 8900`，并设 `VIBE_TRADING_MCP_ALLOWED_HOSTS=<客户端可见的主机名/IP>`（缺省仅放行 loopback，DNS-rebinding 防护 GHSA-p3c9）；客户端指向 `http://<host>:8900/mcp`（Streamable HTTP，单端点；旧客户端可用 `--transport sse`） |
| **远端 Web UI / API** | `vibe-trading serve` + `agent/.env` 设 `API_AUTH_KEY`；远端浏览器首次进入 Settings 输入一次 key，API 请求携带 `Authorization: Bearer <key>`；未带 key 的非 loopback 客户端敏感端点一律 403 |
| **GitHub fork 推送** | SSH 认证（`git@github.com:shadowinlife/Vibe-Trading.git`，本机 SSH key）；API 操作走 `gh` CLI（已登录 token）；注意 mymain 分支保护，见 §4.2 |

## 4. 维护约定

### 4.1 上游对齐流程（每次上游重大推进时）

1. `git fetch origin && git branch backup/mymain-<date> mymain`（先留回退点）。
2. 检查 `git log mymain..origin/main` 与 `gh pr list --author shadowinlife`：确认本分支哪些 PR/hunk 已被上游吸收 → 对齐时**主动丢弃**这些 hunk，不留重复实现；同时逐项核对 F1–F5 是否被上游同类能力取代。
3. 对齐并重整历史，原则：**上游机制优先**（上游有等价或更强实现时采纳上游、删除本地分叉），本地只保留纯增量能力；最终历史必须是「上游 main 为祖先 + 每个 feature 一个独立 commit + 单一作者」。两种等价做法：
   - `git rebase origin/main` 逐 commit 解冲突（适合 patch 少、冲突面小的轮次）；
   - **merge + carve**（本轮采用）：临时分支上 `git merge mymain` 一次性解冲突得到目标树，再从 `origin/main` 逐 feature 切出干净 commit，最后校验目标树与 merge 树逐字节一致（适合上游推进大、共享文件多的轮次）。
4. 全量复验 §3.1/§3.2 基线。
5. 更新本文档（基线 commit、差异总览、已消除分歧、验证基线）。

### 4.2 分支保护与推送

- fork/mymain 保护：**禁止 force-push、禁止删除**（enforce_admins=true）；保持普通提交通道开放。
- 历史重写后只能 force-push，流程：**GitHub API 临时启用 `allow_force_pushes` → `git push fork mymain --force-with-lease` → 立即恢复保护**（保护空窗控制在分钟级）。
- 不在本分支直接开发新特性；新改动走独立 feature 分支，验证通过后合入。

### 4.3 上游 pin 测试带来的持续义务

- **README/SKILL.md 计数**（`test_readme_counts.py`、`test_distribution_skill_manifest.py`）：每增删一个 skill / loader / MCP 顶层工具，必须同步五份 README + `agent/SKILL.md` 计数（当前：skills=90、Tool 类=11、sources=25、MCP 头注释 70/75、SKILL.md `Available MCP Tools (70)` 与 `Finance Skills (90)` 小节标题）。
- **命名一致性**（`test_recovered_orphan_and_new_write_agree_on_the_same_path`）：routed 条目命名保持 `<category>/<slug>.md`，不得改回带类型前缀方案。
- **env-var AST 门禁**（`tools/ci_env_var_gate.py`）：新增环境变量必须声明进 `env_schema.py` 并经 config accessor 读取，禁止裸 `os.getenv` / `os.environ`。

### 4.4 提交规范

- `git commit -s` DCO 签名；commit message 与 PR 描述**禁止** AI 归属行（`Co-Authored-By` / `AI-Model` / `AI-Contributed`）。
- 所有 commit 作者保持单一身份 `shadowinlife <shadowinlife@gmail.com>`，与社区规范一致。
- 社区 PR 提交前逐条检查 `CONTRIBUTING.md` / `AGENT_CONTRIBUTOR_GUIDE.md` / `SECURITY.md` / PR 模板；未完成检查的 PR 保持 Draft。

### 4.5 已知债务（上游化前必须处理）

| # | 债务 | 影响 | 处理时机 |
|---|---|---|---|
| D1 | MemoryGuard **无条件注册**，无 env 开关 | 社区评审必拒 | 贡献队列 ⑤ 前置 |
| D2 | guard 存储按日命名 + 时间戳内容，dedup 失效、GC 默认关闭下无限增长 | 本地磁盘缓慢膨胀 | 贡献队列 ⑤ 前置 |
| D3 | `VIBE_TRADING_HOME` 迁移缺口：上游 #925 迁移不覆盖 memory，旧路径历史记忆不自动迁移 | 设了 `VIBE_TRADING_HOME` 的环境丢历史记忆可见性 | 贡献队列 ① 内容 |
| D4 | `MEMORY_BASE` import 期求值，env 变更需重启进程 | monkeypatch 测试不便 | 贡献队列 ① 顺手改调用期求值 |

## 5. 迭代笔记

### 2026-07-27 五-agent 并行评审（Goal/QA/CodeQuality/Security/ContextMining）

- **死代码**：`build_default_adapter()` 从未被调用 → 移除（`mcp_adapter.py`）。
- **同步阻塞**：`auto_reflect_from_run_dir()` 在回测成功路径同步 I/O → daemon 线程 fire-and-forget（`backtest_tool.py`）。
- **误导错误信息**：锁超时误报为"启用 VT_MEMORY_REFLECTIONS" → `save_lesson` 用 `None`（关闭）vs `""`（锁超时）区分。
- **BLOCKING 文档**：早期 commit 含 `AI-Contributed` 行 → 提交社区 PR 前必须 `rebase -i` 清理。
- 非阻塞建议（留待社区 PR）：`_iter_lessons` 重命名为 `_read_lessons`；大 JSONL 逐行读取；`json.dumps(default=str)` 改自定义 encoder + 警告。

### 2026-08-04 rebase（基线 `3a752d5`）合规修复

- env-var AST 门禁 6 处违规修复（`VT_MEMORY_BASE_DIR` 入 `MemoryConfig`、`CH_*` → `CLICKHOUSE_*`、移除 `__import__("os")` 规避写法）。
- MemoryGuard 排除 `memory_*` 前缀工具自触发（消除自录制噪音）。
- 回测双重反思去重：guard 的 `_TOOLS_THAT_PRODUCE_INSIGHTS` 移除 `backtest`，保留 run_card 钩子为唯一反思入口。
- 对抗性审查（含 Oracle 复核）确认 mymain 五项能力均未与上游重复。

### 2026-08-07 rebase（基线 `6c44732`）

- 上游前进 52 commit；本分支 10 个 patch 全部重放，冲突集中在 memory 三文件、mcp_server、README、SKILL.md、market_data。
- **简化**：上游吸收 #973（原样合入）、以 #984 + 孤儿恢复等价替代 #972、以 `test_readme_counts` 替代 #974 后，本地主动移除读时容忍、统一 routed 命名、丢弃已回流 hunk——PR2 的"bugfix 半"完全由上游承接，本地只保留"feature 半"，memory 分歧面从 7 文件收窄到 5 个纯增量文件。
- **新增义务**：memory-lifecycle skill 使 bundled skills 89→90、Tool 类 10→11，五份 README + SKILL.md 已同步（过上游 pin 测试）。
- 验证基线：memory **317/2**、ClickHouse **13/8**、README+manifest 门禁 **48 passed**、env gate **exit 0**、MCP **OFF=62 / ON=67**；纯本地新增文件 black 24.10 / ruff clean（共享文件不整体 reformat，上游 CI 不跑 black --check 且其自身文件亦有漂移；`test_tier2_integration.py` 的 3 处 ruff 报告为上游既有问题）。

### 2026-08-11 merge + carve 对齐（基线 `c33133f4`，本次）

- 上游前进 120 commit（v0.1.13）；采用 **merge + carve** 流程：临时分支一次性合并解冲突，再从 `origin/main` 切出 6 个单一功能 commit（F1 反思存储 → F2 MCP 记忆工具 → F3 回测钩子 → F4 MemoryGuard/路径 → F5 ClickHouse → docs），carve 结果与 merge 树逐字节一致。
- **冲突面极小**：仅 2 处真冲突——`agent/SKILL.md`（上游 Canada 表述 + 计数区）与 `agent/mcp_server.py` 头注释（上游 62→70 工具计数）；其余 13 个共同改动文件全部自动合并（README×5、env_schema、persistent、registry、market_data、test_tier2、test_env_schema、pyproject、.env.example）。
- **取代核查**：F1–F5 均未被上游取代；上游新增 MCP 工具（quantlib_call、alpha_zoo/alpha_bench、机构数据四件套等）与本地能力无重叠。本地 MCP 计数随之更新：头注释 70/75、冒烟门控 OFF=70 / ON=75；skills 89→90 差异依旧（上游本轮未新增 skill）。
- **共享基础设施**：F4 继续复用上游 `get_runtime_root()`；F5 沿用上游 loader 注册/chain 模式，与上游新增 `ca_equity` chain 并存无冲突；上游 FTS5 排序衰减修复（`454364eb`）与本地 `_default_memory_base()` 在 persistent.py 不同区域并存。
- 验证基线：memory **321/2**（+4 为上游新增 FTS5 衰减测试）、ClickHouse **13/8**、README+manifest 门禁 **54 passed**（上游新增 6 条 pin 测试）、env gate **exit 0**、MCP **OFF=70 / ON=75**；ruff clean。注：本机 black 升至 26.1.0，对 2 个 CH 测试文件有纯风格重排要求，为保持与已验证树逐字节一致未跟随重排（上游 CI 不跑 black --check）。
