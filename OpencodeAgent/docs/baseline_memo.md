# Phase-0 Baseline Inventory Memo — opencode-serve Production Image Family

> **Task**: T2 of `.omo/plans/opencode-engine-bridge-v2.md` (Wave 1 / Phase 0).
> **Date of survey**: 2026-09-12 (local Docker daemon, arm64 macOS host).
> **Feeds**: T1 (spike must run version-matched serve), T10 (pin conversion + tenant image), go/no-go gate.
> **Addresses**: Oracle re-review blocking issues **B1** (the "pin" is fictitious — `@latest` in both image recipes) and **B5** (`ENV_PATH` vs `VIBE_TRADING_HOME` vs volume split-brain).
> **Method caveat**: all images are `linux/amd64`; host is arm64. The `opencode` binary SIGILLs under QEMU and was **never executed**. Versions were extracted via `cat`/`grep`/`ls` entrypoints and `docker create`+`docker cp`; `python3` entrypoints (import + `mcp.list_tools()` counting) ran reliably under emulation and are the source of the *measured runtime* tool counts.

---

## 1. VERSION MATRIX (actual values, not Dockerfile declarations)

### 1.1 Image family inventory

| Image | ID | Built (+08:00) | Registry tags (digest) | Role |
|---|---|---|---|---|
| `opencode-serve:v2.1.0-mymain` | `2cce00b4fa18…` | 2026-08-18 10:55 | `…jiefengnewsv2/opencode-serve:v2.1.0-mymain` **and `:latest`** (`sha256:40ea24d5b07e…`) | first mymain-lineage package; has the root-owned `/home/opencode` ownership bug |
| `opencode-serve:v2.1.1-mymain` | `b215de7151b6…` | 2026-08-18 16:37 | `…jiefengnewsv2/opencode-serve:v2.1.1-mymain` (`sha256:2382403f6475…`) | home-ownership fix rebuild; **last mymain-lineage image actually run on ECS** (stopped 2026-08-28, kept for rollback per archive/DEPLOY-GUIDE-opencode-web-host-direct.md §10) |
| `opencode-serve:v2.2.0-harness-evolution` | `59db50ae8e0f…` | 2026-08-23 15:43 | **none — never pushed** (`RepoDigests=[]`) | harness-evolution eval image, built from `NewAgentMain` branch |
| `opencode-serve-base:latest` (local) | `ea738ee663d1…` | 2026-08-18 16:26 | none (never pushed under this ID) | registry base + 2 layers (`useradd -m opencode`, `mkdir /workspace`) |
| `…jiefengnewsv2/opencode-serve-base:latest` (registry) | `437144c60370…` | 2026-08-18 10:31 | `sha256:2010b037ee57…` | base of the v2.1.0 build; **no `opencode` useradd -m layer** — same tag, *different image* than the local base |

⚠️ Two distinct images share the tag `opencode-serve-base:latest` (local `ea738ee663d1` vs registry `437144c60370`), and the registry's `opencode-serve:latest` points at **v2.1.0**, not v2.1.1 (`deploy/ecs-build.sh` defaults `VERSION=v2.1.0-mymain`; `build.sh` defaults `--tag latest`). Any `FROM …:latest` / `image: …:latest` reference is therefore ambiguous — this is exactly the moving-tag surface T10 must convert to digests.

### 1.2 Component versions per image (measured)

| Component | v2.1.0-mymain | v2.1.1-mymain | v2.2.0-harness-evolution | base:latest (local) | Worktree HEAD (`mymain` @ `5eda88d1`) |
|---|---|---|---|---|---|
| **opencode CLI** (`/usr/lib/node_modules/opencode-ai/package.json`) | **1.18.18** | **1.18.18** | **1.18.18** | 1.18.18 | — (dev machine `~/.opencode/bin/opencode` = **1.18.30**; npm `latest` = 1.18.30 since 2026-09-09) |
| **OmO (oh-my-openagent)** | not baked — `@latest` resolved at first container start | not baked — same | not baked — same | n/a | tmpl + entrypoint fallback both say `oh-my-openagent@latest`; **de-facto = 4.19.4** (see §1.3) |
| **nano-search-mcp** | 0.1.0 (editable, `/opt/nano-search-mcp`; 12 `@mcp.tool()` registrations) | 0.1.0, same | 0.1.0, same | absent | 0.1.0; image `server.py` blob **identical** to worktree HEAD |
| **Vendored VT** (`/opt/vibe-trading`, `git archive`, no `.git`) | mymain @ **`8b89d1b3`** (pre-rebase; C:2026-08-18 09:57) | mymain @ **`3b132b3a`** (pre-rebase; C:2026-08-18 12:17) | `NewAgentMain` @ **`01b07974`** (C:2026-08-23 15:05) | absent | `mymain` @ `5eda88d1` (2026-08-31) |
| `vibe-trading-ai` dist-info in `/opt/venv` | 0.1.13 | 0.1.13 | **0.1.14** | — | 0.1.14 |
| **MCP tool count (runtime-measured under QEMU, `mcp.list_tools()`)** | not run (identical `mcp_server.py` blob to v2.1.1 → 73/78) | **73 OFF / 78 ON** | **77 OFF / 82 ON** | — | docstring + `OpencodeAgent/AGENTS.md`: **77 / 82** |
| Tool governance (manifest → permission denies) | **none** — manifest COPYed but dead (inline Jinja render never reads it; manifest = 1 entry `trading_select_connection`) | **none** — same as v2.1.0 | `render_config.py` compiles manifest; manifest = `trading_*` only | — | `render_config.py` + `subagents.json` + prompts; manifest = **15 disabled entries** (incl. `backtest`, `read_file`, `web_search`…) |
| Domain subagents (12-roster) | absent | absent | absent (`subagents.json`/`prompts/` not in image) | — | present (`config/subagents.json` + 12 prompts) |
| Model config generation | `qwen3.7-max` (tmpl + oh-my-openagent.json + image ENV `LANGCHAIN_MODEL_NAME`) | `qwen3.7-max` | `qwen3.8-max` | — | `qwen3.8-max` |
| OpenCode skills in `/workspace/.opencode/skills` | 4 (no `research-scenarios`) | **4** | 5 | — | 5 |
| Node / Python in image | v20.20.2 / 3.12.13 (`/opt/venv`) | same | same | same | — |
| `sqlglot` (ch_* tools hard dep) | — | **30.17.0 present** (ch_* live; count matches docstring) | present (82 confirms) | — | required |
| fastapi / uvicorn in image | — | 0.141.1 / 0.52.3 | — | — | — |

**Plan-expectation check (T2: "MCP 工具计数 77/82 校验")**: passes **only** on `v2.2.0-harness-evolution` and current mymain HEAD. The leading production-candidate image **v2.1.1-mymain is one generation behind: 73/78**. The 82-count verification recorded on 2026-08-31 (wiki timeline) was performed on the **host-direct deployment**, not on any image.

**Vendored-commit identification method** (no `.git` in `/opt/vibe-trading`): `git archive` sets every file's mtime to the commit's committer date — mtimes gave exact commit times (08-18 09:57 / 08-18 12:17 / 08-23 15:05); `git hash-object` on files pulled via `docker cp` then matched 5/5 (v2.1.1), 5/5 (v2.2.0), 3/3 (v2.1.0) blob hashes against those commits (full table in Appendix A). Note `3b132b3a`/`8b89d1b3` are the **pre-rebase originals** (reachable today via local branch `NewAgentMain`); their post-rebase replays on current `mymain` (`bfdf0228`, `9470019f`) have *different* blobs for `mcp_server.py`/`README.md`/`CHANGELOG.md` because the 08-30/08-31 rebases moved them onto newer upstream bases — proof the images were built from the pre-rebase mymain of 2026-08-18.

### 1.3 OmO de-facto version situation (B1 core finding)

**Mechanism**: OmO is *not* installed at image build time (Dockerfile comment: executing opencode under QEMU SIGILLs). `entrypoint.sh` renders `"plugin": ["oh-my-openagent@latest"]` into `/home/opencode/.opencode/opencode.json` (both via `opencode.json.tmpl` and via the minimal-fallback block, entrypoint.sh:70), and opencode auto-installs the plugin **on first startup** into the runtime config dir. The entrypoint's plugin-cache symlink (`/workspace/.opencode/node_modules` → `/home/opencode/.opencode/node_modules`) is a no-op in v2.1.x/v2.2.0: **no `node_modules` exists in either image** (only `Dockerfile.amd64`, the old 0.0.x variant, ran `npm install --production` there).

**Evidence hunt (exhaustive, local)**:
- `docker ps -a`: **zero** opencode-serve containers (running or exited) on this host; `docker volume ls`: no related volumes → no container-side plugin cache to recover.
- In-image: `/workspace/.opencode/node_modules` absent; `/home/opencode` contains only bash dotfiles; `/root/.cache/opencode`, `/root/.local/share/opencode/{log,repos}`, `/root/.config/opencode` all **empty** (checked as root) → no baked or build-time-cached OmO anywhere.
- **No local artifact of the production-resolved OmO version exists. That absence is itself the finding**: the de-facto production version is unrecoverable from the image and re-resolves from npm on *every* container recreation.
- Dev-machine references (corroborating, not production): global npm root `/Users/mgong/.nvm/versions/node/v24.15.0/lib/node_modules/oh-my-openagent` = **4.19.4**; dev opencode's plugin cache `~/.cache/opencode/packages/oh-my-openagent@latest/package.json` = `{"dependencies":{"oh-my-openagent":"4.19.4"}}` — an **empirical demonstration that the `@latest` spec resolves to 4.19.4** through opencode's own auto-install path.
- npm registry timeline: `oh-my-openagent@4.19.4` published **2026-08-01** and has held the `latest` dist-tag continuously through 2026-09-12 (no other stable since; `next`=4.5.12, `beta`=5.0.0-beta.53 are separate tags).

**Conclusion (high confidence, inference-based)**: every container start and every host (re)start between 2026-08-01 and today resolved `oh-my-openagent@latest` → **4.19.4**. The de-facto production OmO version is **4.19.4**, identical to the dev-machine reference. **Drift risk is live**: 5.0.0 betas are shipping every 1–2 days (beta.53 on 2026-09-10); the day a 5.0.0 stable takes the `latest` tag, every freshly started container/host silently jumps a major version. T10 must convert the tmpl **and** the entrypoint fallback block to `oh-my-openagent@4.19.4`.

### 1.4 opencode CLI drift chain (B1, second half)

| Locus | Version | Source of truth |
|---|---|---|
| All 4 local images (incl. base) | **1.18.18** (published 2026-08-13) | measured `package.json` |
| ECS host-direct production | **1.18.23** (documented; published 2026-08-25) | `docs/archive/DEPLOY-GUIDE-opencode-web-host-direct.md` §4 "当前部署版本 1.18.23"（该文档已于 2026-09-20 归档）; corroborated by `render_config.py` docstring "probed on 1.18.23" |
| npm `latest` today | 1.18.30 (published 2026-09-09) | `npm view opencode-ai dist-tags` |
| Dev machine (`~/.opencode/bin`) | 1.18.30 | `opencode --version` on host |

Two independent drift mechanisms observed:
1. **Rebuild re-resolution**: `Dockerfile:16` and `Dockerfile.base:29` both say `opencode-ai@latest` — any cache-busted rebuild silently moves the CLI (1.18.18 → 1.18.30 today, 12 patch releases).
2. **Layer-cache staleness (the inverse drift)**: v2.2.0 was built 2026-08-23, when npm `latest` was already **1.18.21** (published 08-21), yet the image contains **1.18.18** — the `RUN npm install -g opencode-ai@latest` layer was a cache hit from the 08-18 builds (an identical command string on identical parent layers does not invalidate cache; a registry-mirror lag is the less likely alternative). So `@latest` is *neither* reproducibly latest *nor* reproducibly pinned.

**T1 input**: the spike must run serve at **1.18.18** (image-provided, per plan B1 amendment) — the dev machine's 1.18.30 is **not** version-matched; if the spike cannot run the amd64 image natively (QEMU SIGILL), a local 1.18.18 install is required, or the rig must run on an amd64 host. The 1.18.18 (image) vs 1.18.23 (host production) spread must be explicitly resolved at T10 pin time (one pin to rule both, or accept documented divergence).

---

## 2. ENV/STATE LAYOUT (B5 evidence base)

### 2.1 Identity and environment

- Runtime user: `USER opencode` (uid from `useradd -m -d /home/opencode`) → **`HOME=/home/opencode`** (no `HOME` in image Env; derived from passwd). In v2.1.0 `/home/opencode` is **root-owned** (`drwxr-xr-x root root`) — the ownership bug fixed by the 16:26 base rebuild; v2.1.1+ is `drwxr-x--- opencode opencode`.
- Image Env (identical across v2.1.0/v2.1.1/v2.2.0): `PATH=/opt/venv/bin:…`, `TZ=Asia/Shanghai`, `LANGCHAIN_PROVIDER=dashscope`, **`LANGCHAIN_MODEL_NAME=qwen3.7-max`** (stale vs `.env.example`'s `qwen3.8-max`; compose `env_file` overrides at runtime), `DASHSCOPE_BASE_URL=…compatible-mode/v1`, `LANGCHAIN_TEMPERATURE=0.3`, **`VT_MEMORY=full`**, **`VT_MEMORY_MCP_TOOLS=1`**, **`VT_MEMORY_BASE_DIR=/workspace/.vt-memory`**, playwright vars.
- **`VIBE_TRADING_HOME` is set nowhere** — not in image Env, not in `docker-compose.yml`, not in `.env.example`.
- `docker-compose.yml` adds only `TZ`; credentials come from `.env` (`env_file`).
- MCP subprocess env is **fixed at spawn** by the rendered `opencode.json` `mcp.vibe-trading.env` block: `CLICKHOUSE_*` (7 vars, Jinja-injected), `VT_MEMORY=full`, `VT_MEMORY_MCP_TOOLS=1`, `VT_MEMORY_BASE_DIR=/workspace/.vt-memory`. `HOME` is inherited from the opencode server process (= `/home/opencode`).

### 2.2 Path map: where state lands vs what persists

| State | Resolved path in container | Set by | Volume-backed? | Survives `docker restart`? | Survives container **recreation**? |
|---|---|---|---|---|---|
| Settings `.env` (**`ENV_PATH`**) | `/home/opencode/.vibe-trading/.env` | `agent/src/api/helpers.py:31` — `Path.home()/".vibe-trading"/".env"`, **hardcoded to `Path.home()`, ignores `VIBE_TRADING_HOME`** (blob `c21b3434…` identical in both images and worktree HEAD); written by `settings_routes.py:229,548` (incl. `/settings/data-sources` keys) | **NO** | yes (container layer) | **NO — lost** |
| VT runtime root (runs / sessions / uploads / swarm / workspace, FTS, **goal store `sessions.db`** — `agent/src/goal/store.py:35` module-level `_DEFAULT_DB_PATH = get_runtime_root()/"sessions.db"`) | `/home/opencode/.vibe-trading/` | `agent/src/config/paths.py:13-34`: `VIBE_TRADING_HOME` override → else `Path.home()/".vibe-trading"`; env unset → default | **NO** | yes | **NO — lost** (MCP-side goal/evidence writes are ephemeral **today**, pre-bridge) |
| VT memory (F2) | `/workspace/.vt-memory` | image ENV + entrypoint default + tmpl MCP env | **YES** — compose `./volumes/vt-memory:/workspace/.vt-memory` | yes | yes |
| cron state / logs | `/workspace/cron_jobs/{state,logs}` | compose bind mounts | **YES** | yes | yes |
| opencode server state (session store, auth) | `/home/opencode/.local/share/opencode`, `/home/opencode/.local/state`, `/home/opencode/.cache` | opencode defaults under `$HOME`; **not pre-created in the built base** (worktree `Dockerfile.base:64-67` pre-creates them — recipe is ahead of every built base) | **NO** | yes | **NO — lost** |
| OmO plugin install | `/home/opencode/.opencode/node_modules` (auto-install target; symlink from `/workspace/.opencode/node_modules` is a no-op — source absent) | opencode plugin loader from rendered config | **NO** | yes | **NO — `@latest` re-resolves on every recreation** (§1.3 drift mechanism) |
| Rendered config | `/home/opencode/.opencode/opencode.json` (+ `prompts/` in v2.2.0-era recipe) | entrypoint render at each start | NO (re-rendered each start — fine) | — | — |

### 2.3 B5 verdict

- **Today (image family as-is)**: the images run only `opencode serve` + MCP subprocesses — **no vt gateway process, no Settings UI** — so user-facing Settings writes do not occur in containers yet. But the split-brain is already real for MCP-side state: research-goal storage (`sessions.db`), session/run artifacts and any `ENV_PATH` consumer land under `/home/opencode/.vibe-trading`, which **no compose volume covers** → silently wiped on container recreation. Only `.vt-memory` and cron dirs persist.
- **At T10 (gateway moves in)**: every Settings write — data-source API keys, `LANGCHAIN_*` (D11 auto-title dependency), `VIBE_TRADING_SSE_TIMEOUT` — goes to `host.ENV_PATH` = `$HOME/.vibe-trading/.env` on the **ephemeral** layer unless the per-tenant volume is mounted at exactly `$HOME/.vibe-trading` with `VIBE_TRADING_HOME` unset-or-equal (plan T10 / oracle B5 fix). `VT_MEMORY_BASE_DIR` must point inside a volume (today `/workspace/.vt-memory` is volume-backed only via the compose bind — the tenant image must preserve or relocate it).
- **Host-direct production comparison**: `/opt/my-vibe-trading/{repo,.opencode,.env,.vt-memory}` with `VT_MEMORY_BASE_DIR=/opt/my-vibe-trading/.vt-memory` (persisted on host disk). Note the same `ENV_PATH` trap exists there in latent form: the systemd unit runs `User=root`, so a gateway Settings write would go to `/root/.vibe-trading/.env`, **not** the managed `/opt/my-vibe-trading/.env` (EnvironmentFile). Not exercised today (no gateway on host either), but T15's data-sources semantics ruling should mention it.
- **MCP env parity (B6/T10 assertion input)**: inside the container, gateway and MCP subprocess would share `HOME=/home/opencode`; the tmpl env block pins `VT_MEMORY_*`/`CLICKHOUSE_*` explicitly. `.env` hot-apply is impossible for MCP subprocesses (env fixed at spawn) → degradation-list item 13 stands.

---

## 3. FREEZE COMPAT LIST (frozen surfaces + de-facto pin conversion record)

Frozen surfaces per plan Context (harness-evolution XL eval ruling; "评测窗口内禁触"). No standalone freeze-window status document was found under `mymain-wiki/harness-evolution/` (roadmap references are corpus/judge freezes: E1 frozen judge infra, D2 "修订先于采集冻结"); the Wave-0 window-status confirmation remains open — recorded here as-is.

| # | Frozen surface | De-facto state (this survey) | What the `@latest`→exact pin conversion (T10) must record |
|---|---|---|---|
| 1 | `agent/mcp_server.py` tool surface | HEAD: **77 OFF / 82 ON** (docstring + `OpencodeAgent/AGENTS.md`; runtime-verified on v2.2.0 image). v2.1.1 image: **73/78** (runtime-verified). README-count test anchors 6 READMEs | Tool count per shipped image generation; any T13 wrapper addition is freeze-gated and must move 77/82 → 78/83 across all anchored docs |
| 2 | `OpencodeAgent/config/vibe-trading-tools.json` | HEAD: **15 disabled entries** (trading_place_order, trading_cancel_order, alpha_zoo, alpha_bench, factor_analysis, list_strategies, query_strategies, get_strategy_evidence, backtest, pattern_recognition, read_file, write_file, web_search, read_url, read_document). v2.1.1 image: 1 entry, **dead config** (never compiled — IMAGE-MANUAL §note confirms "v2.1.0 期间清单曾被 COPY 进镜像但无消费者…2026-08-21 起经 render_config.py 真正生效"). v2.2.0 image: `trading_*` only | The *worktree* file is the frozen artifact; images predate it. T10 rebuild inherits HEAD manifest automatically — record that governance semantics change vs v2.1.1 (denies become real) |
| 3 | opencode CLI version | **No declarative pin exists**: `Dockerfile:16` + `Dockerfile.base:29` = `opencode-ai@latest`. De-facto: images **1.18.18**; ECS host production **1.18.23** (documented); npm latest today 1.18.30 | Pin `opencode-ai@1.18.18` (image-lineage de-facto) **or** `@1.18.23` (host-production parity) — explicit decision required; record chosen value + the 1.18.18/1.18.23/1.18.30 spread; T1 golden traces are the drift alarm (D10) |
| 4 | OmO plugin pin | **No declarative pin exists**: `opencode.json.tmpl:48` + `entrypoint.sh:70` fallback = `oh-my-openagent@latest`. De-facto **4.19.4** (stable `latest` since 2026-08-01; dev-cache-verified resolution) | Pin `oh-my-openagent@4.19.4` in **both** tmpl and entrypoint fallback block; record 5.0.0-beta cadence as the drift trigger; `config/package.json`'s `@opencode-ai/plugin: ^1.18.0` (caret) — decide exact pin (only consumed by `Dockerfile.amd64`'s `npm install --production`, not by v2.1.x/v2.2.0 builds) |
| 5 | `VT_MEMORY_MCP_TOOLS` | **=1 (ON)** in image ENV, entrypoint default, and tmpl MCP env block — consistent everywhere | Record ON as the frozen state; tenant image must keep parity between gateway env and MCP subprocess env (T10 assertion) |
| 6 | ClickHouse credential state | `CLICKHOUSE_LLM_*` wired through `.env.example` (commented), tmpl Jinja vars, `render_config.py` ctx; **no secrets baked into any image** (verified: tmpl placeholders only). ch_* tools live in images (sqlglot 30.17.0 present; 82-count on v2.2.0 confirms) | Record "llm_role configured on host / absent in images" as the eval-window state; T10 provisioning must inject per-tenant creds via env, never image layers |
| 7 | Base image tag | `FROM opencode-serve-base:latest` — moving tag, **two different images** under it (§1.1) | Pin by digest: registry base `sha256:2010b037ee57…` (`437144c60370`) — but note it lacks the `useradd -m` ownership fix and the worktree `Dockerfile.base` state-dir pre-creation; T10 should rebuild base from current `Dockerfile.base`, push under a versioned tag, and pin that digest |

Adjacent frozen-recipe facts T10 inherits: MCP entries in the tmpl are already the correct `{type:"local",command:[…]}` array shape (GolemBot #42 trap — **passes** in all generations); rendered config target is `$HOME/.opencode/opencode.json` (not `OPENCODE_CONFIG` env — kimaki #90 note: host deployment uses `OPENCODE_CONFIG` explicitly, container relies on HOME discovery).

---

## 4. GATEWAY DEPS VERIFICATION (dry-run only — nothing installed into images or active envs)

### 4.1 `.[channels]` extras resolvability

Extras name confirmed: **`channels`** in root `pyproject.toml:223` (repo root, not `agent/`). Scratch venv `/tmp/t2-venv` (uv 0.9.9, Python 3.12.13 from legonanobot conda — same minor as image `/opt/venv`):

- `uv pip install --dry-run -e ".[channels]"` (host platform macos/arm64): **Resolved 215 packages, zero conflicts/errors.**
- `uv pip install --dry-run --python-platform x86_64-manylinux2014 --python-version 3.12 -e ".[channels]"` (image target platform): **Resolved 218 packages, zero conflicts** — `matrix-nio 0.26.0` + `vodozemac 0.10.0`, `neonize 0.4.3.post0`, `uvloop 0.22.1` all have linux/amd64 wheels.

Notable resolved pins (linux target): `python-telegram-bot 22.8`, `dingtalk-stream 0.24.3`, `lark-oapi 1.7.3` (feishu), `discord-py 2.7.1`, `qq-botpy 1.2.1`, `slack-sdk 3.44.1` + `slackify-markdown 0.2.4`, `wecom-aibot-sdk 1.0.8`, `python-socketio 5.16.4`, `aiohttp 3.14.3`, `cryptography 50.0.1`, `pyjwt 2.14.0`, `qrcode 8.2`, `msgpack 1.2.2`, `mistune 3.3.4`, `nh3 0.3.7`, `websockets 15.0.1`.

Caveats for T10: (a) dry-run resolved against default PyPI — the image build uses the **aliyun pip mirror** (`/root/.config/pip/pip.conf`); mirror availability of the long-tail channel wheels (neonize, wecom-aibot-sdk, vodozemac) must be proven at real build time; (b) images already carry the gateway core (fastapi 0.141.1, uvicorn 0.52.3, sqlglot 30.17.0) via the full `vibe-trading-ai` editable install, so the channels layer is incremental (~30 packages), not a from-scratch gateway install.

QA-scenario check (plan T2 happy path): **`python -c "import src.api"` succeeds inside v2.1.1-mymain** (measured under QEMU; resolves to `/opt/vibe-trading/agent/src/api/__init__.py`).

### 4.2 Frontend dist presence / serve path

- **No SPA dist anywhere in the images**: `/opt/vibe-trading/frontend` does not exist — `build.sh:98` explicitly strips `frontend/` from the vendored tree; `/workspace/frontend` absent too.
- Serve path (worktree code): `agent/api_server.py:351` `frontend_dist = <repo>/frontend/dist`; mounted at `/` via `SPAStaticFiles` (`agent/src/api/spa.py:11`) only `if frontend_dist.exists()` (api_server.py:366-368); deep-link fallback `helpers.py:40` `_FRONTEND_DIST` = same path. Missing dist degrades gracefully to API-only with `[warn] No frontend build found`.
- **T10 requirement**: add a frontend build layer (`npm ci && npm run build` in `frontend/`, output to `/opt/vibe-trading/frontend/dist`) or COPY a prebuilt dist to that exact path; otherwise the tenant container serves no Web UI. (Frontend source itself is a zero-diff protected surface — building it is not modifying it.)

### 4.3 supervisord

- **Absent in v2.1.1-mymain and v2.2.0-harness-evolution**: `which supervisord` → empty; `dpkg -l | grep -i supervisor` → empty; `/opt/venv/bin` → no supervisor entry. No s6/runit overlay observed either (entrypoint is a single `exec opencode serve`).
- **Recorded as a T10 gap**: the plan mandates supervisord-managed dual process (gateway + `opencode serve` on fixed 127.0.0.1:4096). T10 must install supervisor (apt or pip into `/opt/venv`) and replace the `exec opencode serve` tail of `entrypoint.sh` with a supervisord foreground config.

---

## 5. PRODUCTION CANDIDATE AMBIGUITY (explicit note)

**Question**: which image tag is most likely running on ECS?

**Evidence chain**:
1. `docs/archive/DEPLOY-GUIDE-opencode-web-host-direct.md` (rewritten 2026-08-28; was current at HEAD at survey time, **archived 2026-09-20** — superseded by `DEPLOYMENT-PROD-ENGINE-BRIDGE.md`) §0: "当前线上形态（2026-08-28 晚起）：**宿主机 systemd 直部署 `opencode web`**，对外由 nginx :4096 固定串码网关代理，取代此前的 `opencode serve` 直出方案与**更早的 Docker 容器方案**". §10 disposal table: container `opencode-serve` (**镜像 v2.1.1-mymain**, 4097→4096) → `docker stop` + `restart=no`, container and named volumes **retained for rollback** (`docker start opencode-serve`).
2. `mymain-wiki/history/timeline.md` 2026-08-31: production deployment = ECS host repo synced to `273520d0` (D-batch 12 subagents + main-loop convergence), host `.opencode/` re-rendered with `subagents.json`/prompts/new `render_config.py`; verified **MCP 82**, gateway 401/200, memory_status ok, ch_list_tables 57. → The "12 subagents live since 2026-08-31" production is the **host-direct form**, which no image contains (subagents.json/prompts exist in zero images).
3. Registry state: `…/opencode-serve:v2.1.1-mymain` pushed (digest `sha256:2382403f…`); **`…/opencode-serve:latest` = v2.1.0-mymain** (`2cce00b4fa18`, digest `sha256:40ea24d5…`) because `ecs-build.sh` defaults to `v2.1.0-mymain` and `build.sh` defaults to `--tag latest`. `docker-compose.yml` says `image: opencode-serve:latest` — a compose-driven ECS redeploy would pull **v2.1.0**, the generation with the root-owned-home bug and 73/78 tools.
4. `v2.2.0-harness-evolution`: never pushed (`RepoDigests=[]`), built from the `NewAgentMain` eval branch — an **eval artifact, not a production candidate**, despite being the newest and the only image whose tool surface (77/82) matches the current freeze baseline.

**Verdict**: strictly, **no image is running on ECS today** — de-facto production is the host-direct deployment (opencode **1.18.23** documented, VT @ `273520d0` ≡ 0.1.14-generation, MCP 82, OmO 4.19.4 by @latest resolution). *Among the image family*, **`v2.1.1-mymain` is the leading production candidate**: it is the last mymain-lineage image that actually ran on ECS (stopped, retained for rollback), the only version-tagged mymain push, and the only image with the home-ownership fix. The task framing "registry push makes it the leading production candidate" holds with two corrections: (a) the registry's `latest` tag does **not** point at it; (b) the live production runtime is host-direct, one opencode patch generation ahead (1.18.23 vs 1.18.18) and one tool-surface generation ahead (77/82 vs 73/78).

**Consequences for the plan**:
- **T1**: "production-isomorphic" has two candidate baselines. Per the B1 amendment the spike must use the image-provided serve → **1.18.18**; the 1.18.23 host value must be recorded in `spike_report.md` line 1 alongside it, and any trace-shape difference between 1.18.18 and 1.18.23 is unknown (unverifiable from repo — go/no-go input).
- **T10**: rebuilding from `v2.1.1-mymain`'s recipe inherits a **stale generation** (no subagents, no governance compilation, qwen3.7 config, 73/78, 4 skills). The tenant image must rebuild from **current mymain HEAD recipes** (which are ahead of every built image) — making the pin-conversion (§3) mandatory *before* the rebuild, since the rebuild re-resolves `@latest` to 1.18.30/OmO-4.19.4-or-whatever-latest. Also fix the registry `latest`-tag ambiguity (push versioned tags; move `latest` deliberately or stop using it in compose).

---

## Appendix A — blob-identity evidence (vendored source)

`git hash-object` on files extracted via `docker create`+`docker cp` vs `git rev-parse <commit>:<path>`:

| File | v2.1.1 image blob | `3b132b3a` (pre-rebase mymain) | v2.2.0 image blob | `01b07974` (NewAgentMain) | v2.1.0 image blob | `8b89d1b3` |
|---|---|---|---|---|---|---|
| `agent/mcp_server.py` | `85f254df…` | `85f254df…` ✓ | `12df0849…` | `12df0849…` ✓ | `85f254df…` | `85f254df…` ✓ |
| `pyproject.toml` | `d129629b…` | `d129629b…` ✓ | `72048292…` | `72048292…` ✓ | `d129629b…` | `d129629b…` ✓ |
| `README.md` | `0614c326…` | `0614c326…` ✓ | `72f842c9…` | `72f842c9…` ✓ | — | — |
| `agent/src/api/helpers.py` | `c21b3434…` | `c21b3434…` ✓ | `c21b3434…` | `c21b3434…` ✓ | — | — |
| `CHANGELOG.md` | `f4a13005…` | `f4a13005…` ✓ | `f7385ff0…` | `f7385ff0…` ✓ | `f4a13005…` | `f4a13005…` ✓ |

`helpers.py` blob `c21b3434…` is **also identical at worktree HEAD** (`git rev-parse HEAD:agent/src/api/helpers.py`) → `ENV_PATH = Path.home()/".vibe-trading"/".env"` at **helpers.py:31** holds unchanged across both images and HEAD (B5 citation verified in-image). Post-rebase replays (`bfdf0228`, `9470019f` on current mymain) carry *different* mcp_server/README/CHANGELOG blobs (newer upstream bases) — the images are pre-rebase builds. Commit-date mtimes from `git archive`: v2.1.0 → 2026-08-18 09:57; v2.1.1 → 2026-08-18 12:17; v2.2.0 → 2026-08-23 15:05.

## Appendix B — npm release timeline (correlation data)

- `opencode-ai`: 1.18.18 published 2026-08-13 (latest at all three 08-18 image builds); 1.18.21 on 08-21 (latest at v2.2.0 build 08-23 — **not** installed → layer-cache hit); 1.18.23 on 08-25 (host production value); 1.18.25 on 08-28 (latest at 08-31 host deployment — host stayed 1.18.23, i.e. host installs are manual, not auto-latest); 1.18.30 on 09-09 (latest today).
- `oh-my-openagent`: 4.19.4 published 2026-08-01 → `latest` dist-tag unchanged through 2026-09-12; 5.0.0-beta.10…beta.53 published 08-18…09-10 under the `beta` tag (major-version flip is one dist-tag move away).

## Appendix C — survey commands (reproducibility)

```bash
# versions without executing the opencode binary (QEMU SIGILL):
docker run --rm --platform linux/amd64 --entrypoint grep <image> -m1 '"version"' \
  /usr/lib/node_modules/opencode-ai/package.json
# runtime MCP tool counts (python3 is QEMU-safe):
docker run --rm --platform linux/amd64 [-e VT_MEMORY_MCP_TOOLS=0] \
  --entrypoint /opt/venv/bin/python3 <image> -c \
  "import sys,asyncio; sys.path.insert(0,'/opt/vibe-trading/agent'); \
   from mcp_server import mcp; print(len(asyncio.run(mcp.list_tools())))"
# vendored-source identity:
docker create --platform linux/amd64 <image> && docker cp <ctr>:/opt/vibe-trading/<f> /tmp/… && git hash-object …
# gateway extras dry-run (scratch venv only):
uv venv /tmp/t2-venv --python …/python3.12 && cd <worktree> && \
  VIRTUAL_ENV=/tmp/t2-venv uv pip install --dry-run -e ".[channels]" \
  [--python-platform x86_64-manylinux2014 --python-version 3.12]
```

*Survey artifacts kept outside the repo: `/tmp/t2-extract/` (extracted image files, `channels-dryrun.txt`, npm timelines). No image, container, source file, or pin was modified by this survey.*
