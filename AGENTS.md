1. 当前项目在开发时需要使用`legonanobot`这个`conda env`
2. 2. tushare的token是`4656856ecdd7b737e0dd182a965289133190e515b1b56c3431afac43`, 这个token可用于本地调试, 不能写入到commit中
3. 3. 整个项目的编码风格必须符合开源社区硬性标准, 所有提交需要先经过通过`code-review`的skills进行复核
4. 4. 最终输出给我的内容使用`:zh`中文`
5. 5. 对 GitHub PR/Issue 进行评论时, 使用 `gh` CLI (如 `gh pr comment <number> --repo <owner/repo> --body '...'`) 而非 GitHub API MCP 工具, 因为 PAT token 对上游仓库没有写入权限
6. 6. **提交PR前必须完整阅读所有社区约束性文件** — 每次提交PR前, 必须先逐条阅读以下全部文件并检查所有细节, 确认全部符合后才能将PR从Draft转为正式提交。未完成完整检查的PR一律只能保持Draft形态:
7.    - `CONTRIBUTING.md` — DCO签发(`git commit -s`)、禁止AI trailers、代码风格(black/ruff)、文件行数上限(400行practical/800行hard cap)、无硬编码密钥/路径/URL、删除未使用代码
8.    - `AGENT_CONTRIBUTOR_GUIDE.md` — 安全本地检查命令、高风险操作需explicit approval(broker订单/OAuth/MCP/部署/force-push等)、安全规则(禁止commit密钥/token)、PR必须包含(goal/affected areas/out of scope/test plan/risk/rollback)、禁止AI-assistant attribution trailers
9.    - `SECURITY.md` — 安全漏洞报告政策
10.    - `.github/PULL_REQUEST_TEMPLATE.md` — PR模板: Summary/Why/Changes/Test Plan/Checklist(无保护区域变更/无硬编码值/遵循CONTRIBUTING.md/文档已更新)
11. 7. **提交commit时禁止包含任何AI标记** — CONTRIBUTING.md和AGENT_CONTRIBUTOR_GUIDE.md均明确规定: 禁止添加`Co-Authored-By:` trailers和AI归属行(AI-Model, AI-Contributed等)到commit message或PR description中。DCO `Signed-off-by:`是唯一允许的commit trailer。每次commit使用`git commit -s`签名。此规则优先于全局AGENTS.md中的AI Commit Tracking规则。

# 8. mymain 分支特有约定

> **当前分支定位**: `mymain` 是开源社区 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) `main` 分支的一个**独立部署分支**。
>
> **分支知识库**: `mymain-wiki/AGENTS.md` 是本分支持久记忆的路由入口（功能差异、开发历史、验证证据、研究裁决、未落地资产）。在此分支上工作前先读它，按需深入。

## 8.1 与上游的关系

- **周期性 rebase**: 本分支需要周期性从开源社区 `main` rebase 回全套 patch（通常每周一次，或重大发布后立即跟进）。rebase 命令: `git fetch upstream && git rebase upstream/main`。
- **差异追踪**: 所有与上游 `main` 的功能差异记录在 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` 中。每次 rebase 后需更新该文档。
- **冲突处理**: rebase 冲突优先保留本分支的本地改造逻辑，但需确保不与上游新增功能冲突。

## 8.2 本地独特改造

本分支在以下方面拥有独特的本地改造，这些差异集中在以下目录：

| 改造领域 | 涉及目录/文件 | 说明 |
|----------|--------------|------|
| **数据访问层** | `agent/backtest/loaders/`、`agent/src/market_data.py`、`agent/src/tools/` 中的 flow 工具 | ClickHouse 作为 A 股主力数据源（`clickhouse` 优先于 `tencent`），数据联邦模式（CH 提供 T-1 历史 199 列 + 网络源提供当日 OHLCV） |
| **记忆管理** | `agent/src/memory/`（reflections、mcp_adapter、lifecycle、hierarchy、persistent、memory_guard） | 反思课程存储（JSONL append-only）、MCP 记忆工具（5 个）、MemoryGuard 中间件（FastMCP middleware，自动触发 memory_save + memory_reflect）、回测自动反思钩子、层级路由文件名修复、`VT_MEMORY_BASE_DIR` 支持项目目录存储 |
| **Agent harness 层** | `OpencodeAgent/`（2026-08-17 引入，源自 vibetrading-opencode-instruct） | opencode + omo + 本仓库 MCP 的独立部署 harness（Docker 镜像 opencode-serve）：问题处理协议（四类分流 + 轮次预算）、防幻觉与诚实拒答纪律、escape-top 微观结构信号、三层选股、cron 通知基础设施、nano-search-mcp；消费 ch_* 语义层工具与记忆能力，个人部署独有不回流 |

## 8.3 社区贡献约定

1. **所有功能性 patch 必须遵守社区规范**: 每个 patch 在提交前必须通过 `CONTRIBUTING.md`、`AGENT_CONTRIBUTOR_GUIDE.md`、`SECURITY.md` 的完整检查。
2. **随演进随时贡献回社区**: 每个独立的功能 patch 在本地验证稳定后，立即以独立 PR 形式提交到上游 `HKUDS/Vibe-Trading`。PR 保持 Draft 形态直到完整检查通过。
3. **commit message 清洁**: 提交到社区的 commit 不得包含 `AI-Contributed`、`AI-Model`、`Co-Authored-By` 等 AI 归属行。使用 `git commit -s` 签名 DCO。
4. **PR 描述模板**: 每个社区 PR 必须包含: Summary / Why / Changes / Test Plan / Checklist（参考 `.github/PULL_REQUEST_TEMPLATE.md`）。
5. **差异文档同步**: 社区 PR 合入后，从 `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` 移除对应条目。全部合入后本分支回归为纯跟踪分支。
