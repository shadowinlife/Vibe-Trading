# =============================================================================
# Dockerfile: opencode-serve TENANT app image (multi-tenant container form)
#
# Dual-process single-tenant container (plan T10 / D2 / process-model ruling):
#   - opencode serve   -> 127.0.0.1:4096  (container-INTERNAL only, NOT exposed)
#   - vt gateway       -> 0.0.0.0:8080    (the SINGLE public port; uvicorn
#                          api_server, VIBE_TRADING_ENGINE=opencode, a PURE
#                          HTTP client of serve via the engine bridge)
#   managed by supervisord (see supervisord.conf). The gateway never spawns
#   serve; T6 liveness + _ensure_pumps resume-on-next-send is the recovery
#   story, and supervisord auto-restarts serve on crash (bridge heals without
#   a gateway restart).
#
# Base: opencode-serve-base (Ubuntu 22.04 + Python 3.12 + Node 20 + playwright
#       + pip packages). PINNED below — see the FROM comment (B1 / T2 §1.1:
#       `:latest` is a moving tag shared by TWO different images).
#
# Build (amd64 production artifact):
#   ./build.sh --app --tag v3.0.0-tenant
# =============================================================================

# --- Base image pin (B1 / freeze-compat row 7) --------------------------------
# `opencode-serve-base:latest` is ambiguous: the LOCAL base (ea738ee663d1, with
# the `useradd -m` home-ownership fix) and the REGISTRY base (437144c60370,
# without it) share that tag (T2 baseline_memo §1.1). We pin the LOCAL base
# under a versioned tag created by `docker tag ea738ee663d1
# opencode-serve-base:v3.0.0-tenant`. Pinned digest (image config sha256):
#   sha256:ea738ee663d189224d7f5e3025cabb944bf219a67918f55a5e2bc6653ae2fef7
# A literal `@sha256:<manifest-digest>` FROM requires a registry push, which is
# user-gated (not done here); the versioned tag + recorded config digest is the
# local-immutable equivalent and removes the `:latest` split-brain.
ARG BASE_IMAGE=opencode-serve-base:v3.0.0-tenant
FROM ${BASE_IMAGE}

# Source provenance (recorded by build.sh via --build-arg).
ARG VT_SOURCE_COMMIT=unknown
ARG VT_SOURCE_BRANCH=mymain-engine-bridge
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="opencode-serve tenant" \
      org.opencontainers.image.description="Single-tenant full-stack container: vt gateway + opencode serve + VT MCP (engine bridge)" \
      dev.vibe-trading.source-branch="${VT_SOURCE_BRANCH}" \
      dev.vibe-trading.source-commit="${VT_SOURCE_COMMIT}" \
      dev.vibe-trading.build-date="${BUILD_DATE}" \
      dev.vibe-trading.opencode-pin="opencode-ai@1.18.30" \
      dev.vibe-trading.omo-pin="oh-my-openagent@4.19.4"

USER root

RUN mkdir -p /root/.config/pip && \
    printf '[global]\nindex-url=https://mirrors.aliyun.com/pypi/simple/\n\n[install]\ntrusted-host=mirrors.aliyun.com\n' \
    > /root/.config/pip/pip.conf

# --- opencode CLI pin (B1 / freeze-compat row 3) ------------------------------
# WAS the `@latest` spec (a fictitious pin — T2 §1.4: `@latest` is neither
# reproducibly latest nor reproducibly pinned; layer-cache staleness shipped
# 1.18.18 even when npm latest was 1.18.21). PINNED to 1.18.30: the version ALL
# bridge validation ran on (T1 golden traces, T7 web E2E 67 checks, T8 IM parity,
# T8-1 liveness fix). Changing this pin REQUIRES the drift-alarm replay
# (record_traces.py re-run + diff, spike_report §8) — see docs/IMAGE-MANUAL.md.
# This app-layer install overrides the base image's baked 1.18.18.
RUN npm install -g opencode-ai@1.18.30

# OMO plugin is NOT installed here: executing the opencode binary at build
# time SIGILLs under cross-arch QEMU emulation (arm64 host -> amd64 image).
# entrypoint.sh renders "plugin": ["oh-my-openagent@4.19.4"] into
# opencode.json (PINNED — was @latest; T2 §1.3: 5.0.0 betas ship every 1-2
# days, one dist-tag move from a silent major flip), so opencode auto-installs
# it on first startup (~30s) into the persisted home volume.

# --- VT gateway + channels extras + supervisor --------------------------------
# `.[channels]` adds the 16 IM platform adapters (T2 §4.1: 218 pkgs resolve clean
# on linux/amd64). supervisor manages the dual process (T2 §4.3 gap).
COPY vendor/Vibe-Trading/ /opt/vibe-trading/
RUN pip install --no-cache-dir supervisor \
    && pip install --no-cache-dir -e "/opt/vibe-trading[channels]"

COPY nano-search-mcp/ /opt/nano-search-mcp/
RUN pip install --no-cache-dir -e /opt/nano-search-mcp

COPY config/opencode.json.tmpl /workspace/.opencode/opencode.json.tmpl
COPY config/oh-my-openagent.json /workspace/.opencode/oh-my-openagent.json
COPY config/tui.json /workspace/.opencode/tui.json
COPY config/package.json /workspace/.opencode/package.json
COPY config/vibe-trading-tools.json /workspace/.opencode/vibe-trading-tools.json
COPY config/render_config.py /workspace/.opencode/render_config.py
COPY config/subagents.json /workspace/.opencode/subagents.json
COPY config/prompts/ /workspace/.opencode/prompts/

COPY AGENTS.md /workspace/AGENTS.md

COPY skills/ /workspace/.opencode/skills/

COPY workspace/scripts/ /workspace/scripts/
COPY workspace/cron_jobs/ /workspace/cron_jobs/

COPY entrypoint.sh /workspace/entrypoint.sh
COPY gateway-start.sh /workspace/gateway-start.sh
COPY supervisord.conf /etc/supervisord.conf
RUN chmod +x /workspace/entrypoint.sh /workspace/gateway-start.sh

RUN mkdir -p /workspace/analysis \
             /workspace/reports \
             /workspace/runs \
             /workspace/tmp \
             /workspace/cron_jobs/logs \
             /workspace/cron_jobs/state

# --- B5: pre-create every stateful dir under the runtime user's home ----------
# The tenant volume mounts at /home/opencode (see docker-compose.yml), so ALL
# stateful paths are volume-backed: Settings .env + runtime root under
# .vibe-trading, opencode state under .local/.cache, OmO plugin cache under
# .opencode/node_modules. The local base (ea738ee663d1) predates Dockerfile.base's
# state-dir pre-creation (T2 §2.2), so we create them here and chown to the
# runtime user — a named volume seeds from these on first mount, and opencode
# then writes as `opencode` (never root → no EACCES lockout).
RUN mkdir -p /home/opencode/.vibe-trading \
             /home/opencode/.vibe-trading/.vt-memory \
             /home/opencode/.local/share/opencode \
             /home/opencode/.local/state \
             /home/opencode/.cache \
             /home/opencode/.opencode \
    && chown -R opencode:opencode /home/opencode

# The runtime user must be able to create files under the writable
# workspace subdirectories when they are not volume-mounted.
RUN chown -R opencode:opencode /workspace

# --- Runtime env (non-secret defaults; secrets come from compose .env) --------
# VT_MEMORY_BASE_DIR is consolidated INSIDE the persisted home volume (B5) —
# was /workspace/.vt-memory with its own mount. VIBE_TRADING_HOME is set EXACTLY
# equal to the runtime root default (Path.home()/.vibe-trading, helpers.py:31
# hardcodes Path.home() for ENV_PATH) so Settings writes never diverge into the
# ephemeral layer.
ENV LANGCHAIN_PROVIDER=dashscope \
    LANGCHAIN_MODEL_NAME=qwen3.8-max \
    DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
    LANGCHAIN_TEMPERATURE=0.3 \
    TZ=Asia/Shanghai \
    VT_MEMORY=full \
    VT_MEMORY_MCP_TOOLS=1 \
    VT_MEMORY_BASE_DIR=/home/opencode/.vibe-trading/.vt-memory \
    VIBE_TRADING_HOME=/home/opencode/.vibe-trading \
    VIBE_TRADING_ENGINE=opencode \
    OPENCODE_BASE_URL=http://127.0.0.1:4096 \
    ENABLE_SESSION_RUNTIME=true \
    GATEWAY_PORT=8080

# The gateway (8080) is the single public port; serve (4096) is internal-only.
EXPOSE 8080

# Health = the gateway's unauthenticated /health (system_routes.py), which only
# answers AFTER the lifespan preflight (bridge → serve tool-map + reconcile)
# succeeds, so a green healthcheck implies the full stack is wired. start_period
# covers the first-boot OmO plugin auto-install (~30s) + serve startup.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -sf http://localhost:8080/health || exit 1

USER opencode

ENTRYPOINT ["/workspace/entrypoint.sh"]
