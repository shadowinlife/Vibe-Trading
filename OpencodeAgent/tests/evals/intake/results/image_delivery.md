# Image delivery — novice-onboarding-gprime todo 11

Status: **delivered, local-only** (never pushed to a registry).
Date: 2026-10-28 (Asia/Shanghai); image created `2026-10-28T06:12:15Z`.
Code commit baked into the image: `6e635820189d5ee0d484daee2a26c23a7bf3f1d5` (`6e635820`).

## 1. Image identity

| Field | Value |
|---|---|
| Tag | `opencode-serve:v3.11.0-onboarding-20261028-6e635820` |
| Image `Id` | `sha256:7b6c5d1b2609a1dd7a2249c6d09bfa9ab6000e2c9f9ab15e0b8a9295e1776926` |
| `RepoDigests` | **none (local-only build)** — `docker image inspect … RepoDigests` returns `[]` |
| Size | 4,149,179,262 bytes (4.15 GB) |
| Created | `2026-10-28T06:12:15.021898668Z` |
| Platform | `linux/amd64` |
| Base image | `opencode-serve-base:latest`, `Id` `sha256:a15af069e67bb7309977effd4e8e708cb8ece60b518edf61d7f45db1bba29277`, created `2026-10-25T09:21:36+08:00` (also local-only, `RepoDigests` empty) |
| OMO version baked in | `OMO_VERSION=5.1.35` |

**Identity semantics:** a never-pushed image has no repo digest. Until a push assigns one,
the content identity is the immutable `Id` above. Do not invent or quote a digest for this
tag; when it is eventually pushed, append the assigned `repo@sha256:…` here.

### Tag construction caveat

The tag suffix comes from the exact required command `$(git rev-parse --short HEAD)`,
which in this clone prints **8** characters (`6e635820`, `core.abbrev=auto`), not 7.
The tag is therefore `…-20261028-6e635820`. It is immutable in the sense that it is
version + date + the commit the image vendors, and it is never `:latest`.

## 2. Build

Exact command (run from the repo root):

```bash
cd OpencodeAgent
DOCKER_BUILDKIT=0 ./build.sh --tag v3.11.0-onboarding-20261028-$(git rev-parse --short HEAD) 2>&1 \
  | tee tests/evals/intake/results/image_build.log
```

- **Builder: classic/legacy Docker builder.** The log carries the builder's deprecation
  notice and runs `Step 1/33` … `Step 33/33`.
- **`DOCKER_BUILDKIT=0` is mandatory.** With BuildKit enabled, Docker tries to resolve and
  pull `opencode-serve-base:latest` from a registry (the base is local-only) and dies with
  `pull access denied`; the legacy builder uses the local base cache. Measured in a previous
  lane; reproduced here only with the legacy builder.
- Build start `2026-10-28T05:49:51Z`, image created `06:12:15Z`: **≈22 min 24 s wall clock**
  on the local arm64 host running the `linux/amd64` build under QEMU.
- Build context 59.15 MB; final step `ENTRYPOINT ["/workspace/entrypoint.sh"]`.
- **Source provenance:** `build.sh` re-vendors `../` with `git archive mymain` (1329 Python
  files), so only the committed tree (`6e635820`) is baked in. Uncommitted work is not
  included. The host working tree did contain the todo-11 `tests/smoke-v311.sh` edit at build
  time, but `Dockerfile` does not `COPY tests/` — the smoke script is host-side only and is
  not part of the image.
- Base-image step not rebuilt (`./build.sh` app mode); `opencode-serve-base:latest` was reused
  as-is. The copy step `COPY vendor/Vibe-Trading/ /opt/vibe-trading/` is the only place the
  vendored tree enters the image.

Build final lines:

```text
Successfully built 7b6c5d1b2609
Successfully tagged opencode-serve:v3.11.0-onboarding-20261028-6e635820
=== App image done: opencode-serve:v3.11.0-onboarding-20261028-6e635820 ===
```

`docker image ls` for the tag:

```text
REPOSITORY       TAG                                    IMAGE ID       CREATED         SIZE
opencode-serve   v3.11.0-onboarding-20261028-6e635820   7b6c5d1b2609   6 minutes ago   4.15GB
```

## 3. Change list (previous delivery → `6e635820`)

Previous delivery (baseline, **kept**, not overwritten):

- Tag `opencode-serve:v3.10.0-mymain`
- `Id` `sha256:77548a02382efb27f3ef69d1abb238a32dc19167e78c00a367fcdcff4a6a0e37` (`77548a0238`)
- `RepoDigests` `registry.cn-hangzhou.aliyuncs.com/jiefengnewsv2/opencode@sha256:64f9566215ce9b6f6aa3a71bb6aaee37e4d421864a19d1cd010f723805956a46`

There is **no `v3.10.0-mymain` git tag** in this clone (`git tag --list 'v3.10.0-mymain'`
is empty), so the list uses the onboarding range `git log --oneline 4fcdad7e~1..HEAD`
as instructed. The range contains **12** commits (the lane grew two fix commits beyond the
planned 10). Oldest → newest:

```text
4fcdad7e test(opencode-agent): add intake eval harness with frozen corpus and event schema
abcaf262 fix(opencode-agent): scope intake expectation and pin eval working directory
aa3c2683 test(opencode-agent): record intake baseline and freeze ask-first protocol snapshot
ce66b2b5 fix(opencode-agent): recover short-run transcripts from the server session API
48371c41 test(opencode-agent): wave-0 verdict on trigger rate and menu coverage
8d25e5d8 test(opencode-agent): add slot decision-value experiment with scripted judging
b5d5a8cc refactor(opencode-agent): move clarification protocol from AGENTS.md to intake skill
b0807c4c test(opencode-agent): rerun slot value experiment with bounded observation
ddbdd706 feat(opencode-agent): add slash commands as deterministic discovery layer
5ce2ca34 feat(opencode-agent): mandate assumptions/diff/next-step/disclaimer closing block
2452260c feat(opencode-agent): enable question tool for primary agents in rendered config
6e635820 refactor(opencode-agent): replace capability index with product index in AGENTS.md
```

Range anchor: the parent of `4fcdad7e` is `d6bbbd6c` (`test(opencode-agent): add
privacy-preserving first-message exporter`, eval/test files only). It is excluded from the
range above; because it post-dates the baseline image build (baseline created 2026-10-25,
`d6bbbd6c` committed 2026-10-28), a literal image-to-image diff also contains `d6bbbd6c`.
That commit changes no runtime or workspace content.

Runtime-facing changes carried by this image (from the three Wave-1 feature commits):

- `ddbdd706` — `/workspace/.opencode/command/{checkup,screen,backtest,review}.md` discovery layer.
- `5ce2ca34` — closing block (assumptions / diff / next step / disclaimer) in the output contract.
- `b5d5a8cc` — clarification protocol moved out of `AGENTS.md` into `skills/intake/SKILL.md`.
- `2452260c` — `agent.build` / `agent.plan` `permission.question = "allow"` in the rendered config.
- `6e635820` — product index replaces the capability index in `AGENTS.md` (328 lines).

## 4. Content checks (image actually contains the new work)

Command run (host, against the built image):

```bash
docker run --rm --platform linux/amd64 --entrypoint bash \
  opencode-serve:v3.11.0-onboarding-20261028-6e635820 -c '…'
```

Output (verbatim):

```text
--- command files ---
/workspace/.opencode/command/checkup.md: 14
/workspace/.opencode/command/screen.md: 14
/workspace/.opencode/command/backtest.md: 15
/workspace/.opencode/command/review.md: 14
--- AGENTS.md ---
/workspace/AGENTS.md: 328
--- intake skill ---
-rw-r--r-- 1 opencode opencode 7251 Oct 28 13:45 /workspace/.opencode/skills/intake/SKILL.md
--- rendered config ---
agent.build.permission.question = allow
agent.plan.permission.question  = allow
primary agents in config        = ['build', 'explore', 'multimodal-looker', 'plan']
```

The same five checks are now assertions in `tests/smoke-v311.sh` stage 1 (added in this
todo, together with the render assertions for `agent.build`/`agent.plan`
`permission.question == "allow"`), so a future build cannot silently regress them.

## 5. Smoke evidence

Command:

```bash
cd OpencodeAgent
bash tests/smoke-v311.sh opencode-serve:v3.11.0-onboarding-20261028-6e635820
```

Budget: **1 LLM session** (stage 2b `/checkup`). Wall clock ≈5 min; the server answered
`GET /config?directory=/workspace` after ~15 s (5 s poll granularity, QEMU). Full output:

```text
=== Stage 1: command files + config render (opencode-serve:v3.11.0-onboarding-20261028-6e635820, linux/amd64) ===
COMMAND_FILES_OK
WORKSPACE_CONTENT_OK: AGENTS.md=328 lines, skills/intake/SKILL.md present
OK
RENDER_OK: agent.build/plan permission.question=allow
=== Stage 1 PASS ===
=== Stage 2: real startup + runtime command discovery ===
CONFIG_READY after ~15s
COMMAND_ENDPOINT: /command?directory=/workspace
RUNTIME_COMMANDS_OK: backtest checkup customize-opencode data-warehouse debugging escape-top-microstructure frontend git-master goal handoff html-report hyperplan init init-deep intake periodic-execution playwright refactor remove-ai-slops research-scenarios review review-work screen security-research security-review stop-continuation ulw-execute visual-qa websearch:web_search_help
=== Stage 2b: /checkup runtime trigger (one LLM session) ===
CHECKUP_REPLY_OK
=== SMOKE_OK ===
image: opencode-serve:v3.11.0-onboarding-20261028-6e635820
runtime command list: backtest checkup customize-opencode data-warehouse debugging escape-top-microstructure frontend git-master goal handoff html-report hyperplan init init-deep intake periodic-execution playwright refactor remove-ai-slops research-scenarios review review-work screen security-research security-review stop-continuation ulw-execute visual-qa websearch:web_search_help
checkup reply (first 200 chars): I detect a **research/analysis** intent for a specific instrument (600519 贵州茅台). My approach: load `research-scenarios` skill → execute Scenario A steps with live ClickHouse + VT tool data, then synth
```

Notes:

- `RUNTIME_COMMANDS_OK` includes the four required commands; the rest are built-in skills the
  server also enumerates. The assertion checks the four names, not the full list.
- The render check is the `RENDER_OK` line: valid JSON after `render_config.py`, MCP entry
  `local`, model + plugin present, and `question=allow` on the primary agents.
- `/checkup` produced a non-empty assistant reply (≥40 chars) and routed through
  `research-scenarios` as designed.
- `smoke-v370.sh` was intentionally **not** run: it pins the tool counts and OMO version of an
  older delivery, so it fails for reasons unrelated to this image.

## 6. Handoff — sister plan `novice-onboarding-frontend.md` todo 3

Exact tag to pull/run:

```text
opencode-serve:v3.11.0-onboarding-20261028-6e635820
```

Content identity (no repo digest exists):

```text
Id: sha256:7b6c5d1b2609a1dd7a2249c6d09bfa9ab6000e2c9f9ab15e0b8a9295e1776926
RepoDigests: none (local-only build)
```

What changed vs the `v3.10.0-mymain` baseline: the four `/` commands are discoverable at
runtime (`GET /command?directory=/workspace`); `AGENTS.md` is the 328-line product index;
the clarification protocol lives in `skills/intake/SKILL.md`; primary agents can ask
structured questions (`permission.question=allow`); the closing-block output contract is in
effect. Full commit list in §3.

Readiness profile to expect (frontend / ACS):

```yaml
startupProbe:
  exec:
    command:
      - /bin/bash
      - -c
      - 'curl -sf --max-time 40 -u "opencode:${OPENCODE_SERVER_PASSWORD}" "http://127.0.0.1:4096/config?directory=/workspace"'
  periodSeconds: 5
  timeoutSeconds: 45
  failureThreshold: 6
```

- Probe the real warm path `GET /config?directory=/workspace`, not a static `/health`.
- First 200 observed at **~15 s** in the local smoke (QEMU, amd64 image on arm64 host) with
  the pre-baked plugin SDK (`PREBAKE_PLUGIN_SDK=1`); budget ≲60 s. Total probe budget
  `6 × (45 s + 5 s) = 300 s`, aligned with the route-wait budget.
- Runtime env used by the smoke: `OPENCODE_SERVER_PASSWORD`, `DASHSCOPE_API_KEY`,
  `CLICKHOUSE_HOST=` (empty, offline smoke).

How the sister plan obtains the image:

1. Same Docker daemon: the tag is already present — verify with
   `docker image inspect opencode-serve:v3.11.0-onboarding-20261028-6e635820`.
2. Different daemon/host: it cannot pull this tag (no registry push). Rebuild from commit
   `6e635820` on the target host with the command in §2, or ship the image with `docker save`
   / `docker load` over an allowed channel. `scp` is forbidden by repo rules; use
   `docker save | ssh <host> 'docker load'`, `rsync`, or `git` as appropriate.

`image_build.log` (648 lines) is the full build log; the tail is quoted in §9.

## 7. What was NOT done, and why

- **No registry push.** Not requested, and no push credentials were verified. The tag is
  **local-only**; `RepoDigests` is empty by construction. The sister plan must build or
  receive the image by the means in §6.
- **No repo digest claimed.** A never-pushed image has an `Id`, not a digest. The `Id` in §1
  is the content identity until a push assigns `repo@sha256:…`.
- **No 40-item corpus run.** That belongs to the next checkpoint (F3), not this todo.
- **`smoke-v370.sh` not run** (pin mismatch with this delivery, by design).
- **No registry copy of the image, no `:latest`.** `:latest` was never assigned.
- **Baseline preserved.** `opencode-serve:v3.10.0-mymain` was not deleted or retagged.
- **No `scp`, anywhere.**
- **Smoke script edit is host-side.** The stage-1 additions are in the delivery commit but not
  inside the image (`Dockerfile` does not `COPY tests/`).

## 8. Verification receipts

Pre-build working tree (`git status --porcelain`): only the pre-existing `mymain-wiki/*`
entries (`M mymain-wiki/AGENTS.md`, `M mymain-wiki/INDEX.md`, `?? mymain-wiki/agent-ux/`)
plus this todo's `M OpencodeAgent/tests/smoke-v311.sh`. `build.sh` vendors the committed
tree (`git archive mymain`), so none of these reached the image.

Post-smoke cleanup — no containers left behind (the smoke's `trap` removed its own):

```text
$ docker ps -a --format '{{.ID}} {{.Image}} {{.Names}}'
(end ps -a)
```

Lane temp files removed: `$TMPDIR/smoke-v311-delivery.log`,
`$TMPDIR/image-content-evidence.log`. Images kept (delivery + baseline):

```text
opencode-serve:v3.11.0-onboarding-20261028-6e635820 7b6c5d1b2609
opencode-serve:v3.10.0-mymain 77548a02382e
```

Record-time `git status --porcelain` (the two result files are untracked only because
`.gitignore:15` ignores `results/`; they are force-added individually, never `git add -A`):

```text
 M OpencodeAgent/tests/smoke-v311.sh
 M mymain-wiki/AGENTS.md
 M mymain-wiki/INDEX.md
?? mymain-wiki/agent-ux/
```

Receipts for the remaining required artifacts: build final lines and `docker image ls` in §2,
content checks in §4, `SMOKE_OK` in §5, build log tail in §9.

## 9. Build log tail (last 40 lines)

```text
 ---> Running in 5533f110eed2
 ---> Removed intermediate container 5533f110eed2
 ---> e75185e4b22a
Step 28/33 : ENV LANGCHAIN_PROVIDER=dashscope     LANGCHAIN_MODEL_NAME=qwen3.8-max     DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1     LANGCHAIN_TEMPERATURE=0.3     TZ=Asia/Shanghai     VT_MEMORY=full     VT_MEMORY_MCP_TOOLS=1     VT_MEMORY_BASE_DIR=/workspace/.vt-memory
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in 26193e75b50d
 ---> Removed intermediate container 26193e75b50d
 ---> ff12fe9b7e30
Step 29/33 : ENV OMO_DISABLE_POSTHOG=1     OMO_SEND_ANONYMOUS_TELEMETRY=0     OPENCODE_DISABLE_MODELS_FETCH=1     OPENCODE_DISABLE_AUTOUPDATE=1     OMO_VERSION=${OMO_VERSION}
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in aa14b6e3b158
 ---> Removed intermediate container aa14b6e3b158
 ---> bd2cc967eb56
Step 30/33 : EXPOSE 4096
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in ba9f09542c14
 ---> Removed intermediate container ba9f09542c14
 ---> 8898d1f7a64d
Step 31/33 : HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3     CMD curl -sf -u opencode:"$OPENCODE_SERVER_PASSWORD" http://localhost:4096/health || exit 1
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in 8f940feb3c3b
 ---> Removed intermediate container 8f940feb3c3b
 ---> 4438a04bc593
Step 32/33 : USER opencode
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in 40a03c2d13c4
 ---> Removed intermediate container 40a03c2d13c4
 ---> 31fdee1b9cba
Step 33/33 : ENTRYPOINT ["/workspace/entrypoint.sh"]
 ---> [Warning] The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8) and no specific platform was requested
 ---> Running in 4da533d375e0
 ---> Removed intermediate container 4da533d375e0
 ---> 7b6c5d1b2609
Successfully built 7b6c5d1b2609
Successfully tagged opencode-serve:v3.11.0-onboarding-20261028-6e635820
=== App image done: opencode-serve:v3.11.0-onboarding-20261028-6e635820 ===

Run with docker-compose:
  cp .env.example .env
  docker compose up -d
```