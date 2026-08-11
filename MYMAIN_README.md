# MYMAIN 发布记录（Release Log）

> `mymain` 是开源社区 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) `main` 的独立部署分支
> （分支定位与开发约定见 `MYMAIN_DIVERGENCE.md` 与根目录 `AGENTS.md` §8）。
>
> 本文档是 `mymain` 的**发布 changelog**：每次对齐 / 修正后的发布都会在这里记录
> 该版本相对上游的**核心差异**与本次发布的**核心迭代**。

## Tag 约定

- 发布 tag：**`release/mymain`** —— 移动 tag，始终指向 `mymain` 上最新一次发布 commit。
- 更新流程（每次发布完成后）：

  ```bash
  git tag -f release/mymain <release-commit>
  git push fork mymain
  git push -f fork release/mymain
  ```

- 历史不丢失：下表逐条永久记录每次发布的 commit SHA、上游基线与核心变更；
  移动 tag 前，先把 `git rev-parse release/mymain` 的 SHA 回填进上一条记录的
  「发布 commit」字段，即可随时 `git diff <sha>` 追溯任意历史版本。

## 发布记录（新 → 旧）

---

### release/mymain · 2026-08-11 — 基线：上游 v0.1.13（`c33133f4`）

- **发布 commit**：`release/mymain` tag 所指 commit（即首次引入本文档的 commit）
- **上游基线**：`c33133f4`（v0.1.13；本次对齐覆盖 `6c44732` 之后的 120 个上游 commit）
- **差异总量**：39 个文件、+4,487/−53 行（含本文档与 `MYMAIN_DIVERGENCE.md`、`AGENTS.md` 等分支级文档；复核命令 `git diff c33133f4 release/mymain --shortstat`）

#### 核心差异（相对上游 v0.1.13 的 5 项本地能力，均未被上游取代）

| # | 能力 | Commit | 核心内容 | 规模 |
|---|------|--------|----------|------|
| F1 | 反思课程存储 | `c4aa2774` | JSONL append-only 反思存储（`reflections.py`）+ `VT_MEMORY_REFLECTIONS` 等特性开关 | 5 文件 +643 |
| F2 | MCP 记忆工具 | `c0909374` | 记忆生命周期 5 个工具经 MCP 暴露（`mcp_adapter.py`）+ `memory-lifecycle` SKILL 文档 + 5 语言 README 更新 | 11 文件 +870/−42 |
| F3 | 回测自动反思钩子 | `7291d2d0` | 回测完成后自动触发 memory_save + memory_reflect（`backtest_tool.py`）+ 并发 / 延迟性能测试 | 6 文件 +413 |
| F4 | MemoryGuard + 存储路径 | `c459eebb` | FastMCP middleware 自动触发 save/reflect（`memory_guard.py`）；`VT_MEMORY_BASE_DIR` 支持项目级存储 | 5 文件 +229/−6 |
| F5 | ClickHouse A 股数据源 | `0fc4a455` | ClickHouse 作为 A 股主力数据源（T-1 历史 199 列 + 网络源联邦当日 OHLCV），覆盖资金流 / 龙虎榜 / 融资融券 / 北向 4 类 flow 工具 | 16 文件 +2049/−6 |

> F1–F5 的上游取代核查结论（2026-08-11）：上游本轮新增（quantlib_call、alpha zoo MCP、
> 机构数据、加拿大市场、eToro、桌面端等）与上述能力**均无重叠**，全部保留。
> 详见 `MYMAIN_DIVERGENCE.md` §2。

#### 核心迭代（本次发布完成的工作）

1. **上游对齐**：merge 上游 `6c44732` 之后的 120 个 commit（v0.1.13），仅 2 处真冲突
   （`agent/SKILL.md` 计数区、`agent/mcp_server.py` 工具数头注释），均已解决。
2. **历史重整**：原 11 个交错 commit 经 merge + carve 重整为 **6 个单一功能 commit**
   （F1→F2→F3→F4→F5→docs），carve 树与 merge 树逐字节一致；每个 commit 可独立作为社区 PR 候选。
3. **计数基线同步**：MCP 工具 62→70（`VT_MEMORY_MCP_TOOLS=1` 时 75）、skills 89→90、数据源 24→25。
4. **文档同步**：`MYMAIN_DIVERGENCE.md` 全面重写至新基线；新增本文档作为发布追踪入口。

#### 验证基线（`legonanobot` 环境，全部通过）

| 门禁 | 结果 |
|------|------|
| memory 套件 | 321 passed / 2 skipped |
| ClickHouse 套件 | 13 passed / 8 skipped |
| README / SKILL 计数门禁 | 54 passed |
| env-var AST 门禁（`tools/ci_env_var_gate.py`） | exit 0 |
| MCP 工具计数 | OFF=70 / ON=75 |
| 提交规范 | 单一作者 shadowinlife + DCO 签名 + 无 AI 归属行 |

---

## 附录：发布检查清单

每次发布打 tag 前必须完成：

1. [ ] merge / rebase 上游 `main` 并解决冲突
2. [ ] 取代核查：确认本地能力未被上游取代，更新 `MYMAIN_DIVERGENCE.md`
3. [ ] `MYMAIN_DIVERGENCE.md` §3.1 全部测试门禁通过
4. [ ] 提交规范：单一作者 `shadowinlife` + `git commit -s` DCO + 无 AI 归属行
5. [ ] 本文档新增一条发布记录（核心差异 + 核心迭代），并回填上一条记录的发布 commit SHA
6. [ ] `git tag -f release/mymain <commit>` + 推送分支与 tag
