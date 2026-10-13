# AGENTS.md — 仓库导航索引（本地 main，勿推 origin）

> 本文件是**本地 `main` 分支专属**的 agent 导航索引（上游 HKUDS/Vibe-Trading `main` 无此文件，
> 且上游 `.gitignore` 含裸 `AGENTS.md` 规则——本文件经 `git add -f` 强制跟踪，仅存于本地历史）。
> 本仓库的个人部署分支是 **`mymain`**（锁定演进分支：F1–F7 本地能力 + OpencodeAgent 镜像 harness）。
> 在 `mymain` 上执行**上游对齐 / 发布打 tag / 镜像打包**任务前，必须先读下表文档（均在 `mymain` 分支）。

## mymain 维护文档索引

| 文档 | 路径（mymain 分支） | 读取方式 | 内容 |
|---|---|---|---|
| **差异台账（流程权威）** | `mymain-wiki/branch/MYMAIN_DIVERGENCE.md` | `git show mymain:mymain-wiki/branch/MYMAIN_DIVERGENCE.md` | §4.1 上游对齐流程、§4.2 分支保护与 force-push 窗口、§4.3 上游 pin 测试义务（七份 README 计数）、§4.4 提交规范（DCO / 单一作者 / 零 AI trailer）、§3 验证门禁命令清单、§5 迭代笔记（历轮实战记录与事故教训） |
| **发布记录** | `mymain-wiki/branch/MYMAIN_README.md` | `git show mymain:mymain-wiki/branch/MYMAIN_README.md` | `release/mymain` 移动 tag 约定、每轮发布 changelog（上游基线 / 差异总量 / 对齐方式 / 验证基线 / 镜像交付）、附录发布检查清单 |
| **镜像手册** | `OpencodeAgent/docs/IMAGE-MANUAL.md` | `git show mymain:OpencodeAgent/docs/IMAGE-MANUAL.md` | opencode-serve 镜像：构建 / 升级 SOP / 部署 / 冒烟 / 超时与载荷契约 |
| wiki 总索引 | `mymain-wiki/INDEX.md` | `git show mymain:mymain-wiki/INDEX.md` | 分支知识库全部文档路由 |

## 上游对齐七步速记（细节以 DIVERGENCE §4.1 为准）

1. **留回退点**：`git fetch origin && git branch backup/mymain-<date> mymain`
2. **吸收/取代核查**：`git log mymain..origin/main` + `gh pr list --author shadowinlife`——已被上游吸收的 hunk 主动丢弃；逐项核对 F1–F7 是否被上游取代
3. **对齐重整**：原则「上游机制优先」；上游推进大 → **merge + carve**（temp 分支一次解冲突得目标树 → 从 `origin/main` 逐 feature 切干净 commit → `git diff temp mymain-aligned` = 0 行逐字节校验）；推进小 → rebase 或直接 merge
4. **全量门禁复验**（DIVERGENCE §3.1/§3.2）：memory / ClickHouse / schema / README 计数 / env-var AST / MCP 计数（OFF=80 / ON=85）/ 冒烟
5. **更新台账** `MYMAIN_DIVERGENCE.md`（基线、差异总量、已消除分歧、验证基线）
6. **写发布记录** `MYMAIN_README.md` 新增条目 + 回填上一条的发布 commit SHA
7. **tag + 推送**：`git tag -f release/mymain <commit>`；历史重写后按 §4.2 保护窗口推送（API 临时放行 force-push → `--force-with-lease` → 立即恢复保护）

## 镜像打包速记（OpencodeAgent，细节以 IMAGE-MANUAL 为准）

- **升级 SOP**：opencode pin = 改 `Dockerfile.base` 后重建 **base + app**；OMO pin = `Dockerfile` ARG + `config/opencode.json.tmpl` + `config/tui.json` **三处逐字一致**（另有 `entrypoint.sh` fallback 与 `tests/test_config_render.py` pin 需同步）；models 目录冻结于构建期，重建即刷新
- **构建**：仓库根须在 `mymain` 分支（vendoring 走 `git archive` 取已提交树）；`cd OpencodeAgent && DOCKER_PLATFORM=linux/amd64 ./build.sh --base` → `./build.sh --tag vX.Y.Z-mymain [--push]`
- **凭据**：`OpencodeAgent/.env`（gitignored）；registry 受 IP 白名单保护——push denied/timeout **不重试轰炸，转人工**
- **冒烟**：`tests/smoke-vXYZ.sh`（Stage1 render / COUNT / deny / FUNC + Stage1b LLM 代理渲染 + Stage2 真 entrypoint HEALTH_OK），新版本脚本随交付入库
- **交付记录**：`MYMAIN_README.md` 追加条目（版本 / 大小 / digest / 冒烟结果 / 事故）

## 提交规范（mymain 及本仓库所有本地提交）

- `git commit -s`（DCO）；单一作者 `shadowinlife <shadowinlife@gmail.com>`；**禁止** AI 归属行（`Co-Authored-By` / `AI-Model` / `AI-Contributed`）
