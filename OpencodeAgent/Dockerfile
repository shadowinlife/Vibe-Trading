# =============================================================================
# Dockerfile: opencode-serve app image
# Base:  opencode-serve-base (Ubuntu 22.04 + Python 3.12 + Node 20 + OpenCode +
#        playwright + pip packages)
# 构筑: docker build --platform linux/amd64 -t opencode-serve:v2.0.0 .
# =============================================================================

FROM opencode-serve-base:latest

USER root

# The base image owns the opencode binary. Keep the image on the same v1 max
# version as the local harness; rebuild Dockerfile.base when this pin moves.
RUN test "$(opencode --version)" = "1.19.3" || { \
        echo "ERROR: base image must ship opencode-ai@1.19.3; rebuild --base"; \
        exit 1; \
    }

RUN mkdir -p /root/.config/pip && \
    printf '[global]\nindex-url=https://mirrors.aliyun.com/pypi/simple/\ntimeout=60\nretries=5\n\n[install]\ntrusted-host=mirrors.aliyun.com\n' \
    > /root/.config/pip/pip.conf

# opencode-ai comes from the base image (pinned in Dockerfile.base). The
# duplicate `npm install -g opencode-ai@latest` that used to live here added
# a ~594MB layer on top of the base install — removed (P2#7).

COPY vendor/Vibe-Trading/ /opt/vibe-trading/
# Primary aliyun index with a tuna fallback: aliyun periodically serves a
# partially-synced release whose advertised PEP 658 .metadata 404s, which pip
# treats as fatal (incident: pyjwt 2.15.1, v3.5.0 build).
RUN pip install --no-cache-dir -e /opt/vibe-trading/ || \
    pip install --no-cache-dir -e /opt/vibe-trading/ \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# Build-time gate for the VT MCP server: fail the build when the module
# cannot even be imported. The entrypoint keeps a runtime check for log
# visibility, but it can now be skipped (SKIP_VT_VERIFY=1) because this
# already ran at build time.
RUN /opt/venv/bin/python3 -c "import sys; sys.path.insert(0, '/opt/vibe-trading/agent'); from mcp_server import mcp; print('VT MCP import OK')"

# ── Pre-bake the OMO plugin into opencode's package cache ───────────────────
# opencode >=1.18 resolves config plugins via Npm.add(): when
#   ~/.cache/opencode/packages/<spec>/node_modules/<name>
# exists, it is loaded directly with ZERO network access; otherwise an npm
# install (~241MB) runs synchronously on the FIRST API request (30s..4min
# depending on registry reachability, hard failure when offline).
# We cannot execute the opencode binary at build time (SIGILL under cross-arch
# QEMU), but opencode installs via @npmcli/arborist, so plain `npm install`
# produces an identical cache layout. <spec> MUST match the plugin entry
# rendered from config/opencode.json.tmpl VERBATIM (directory name = spec).
# Upgrade SOP: bump OMO_VERSION here AND in opencode.json.tmpl, then rebuild.
ARG OMO_VERSION=5.1.35
RUN mkdir -p "/home/opencode/.cache/opencode/packages/oh-my-openagent@${OMO_VERSION}" && \
    cd "/home/opencode/.cache/opencode/packages/oh-my-openagent@${OMO_VERSION}" && \
    printf '{\n  "dependencies": {\n    "oh-my-openagent": "%s"\n  }\n}\n' "${OMO_VERSION}" > package.json && \
    npm install --no-audit --no-fund --cache /tmp/npm-cache && \
    rm -rf /tmp/npm-cache && \
    test -d node_modules/oh-my-openagent && \
    chown -R opencode:opencode /home/opencode/.cache

# ── Pre-bake the @opencode-ai/plugin SDK into BOTH config dirs ─────────────
# The first /config bootstrap runs opencode's bundled npm installer for
# `@opencode-ai/plugin` in every config directory it loads:
#   /home/opencode/.config/opencode  (global)  and
#   /home/opencode/.opencode         (project, next to the rendered opencode.json).
# With registry access that install costs 10s..1min (measured 57s under QEMU);
# without registry access it retries for ~70s and then logs
# "background dependency install failed" — a network-dependent first request,
# which is exactly the cost the startupProbe plan (sandbox-startup-probe-canary
# T9) wants removed. Measured on this image: first /config 200 67s online /
# 79s offline before the pre-bake, 8.0s offline after it.
# Same trick as the OMO cache above: plain `npm install` produces the tree
# opencode expects, so no opencode binary is executed at build time
# (SIGILL under cross-arch QEMU). The SDK version is read from the installed
# opencode-ai package instead of a hand-maintained ARG, so a base-image bump
# cannot leave a stale pin behind.
# Offline/slim builds can skip the extra registry call: --build-arg
# PREBAKE_PLUGIN_SDK=0 (the image still works, but the first request pays the
# install again). This layer changes nothing at runtime once it is baked in.
ARG PREBAKE_PLUGIN_SDK=1
RUN if [ "$PREBAKE_PLUGIN_SDK" = "1" ]; then \
        set -eux; \
        SDK_VERSION="$(node -e "console.log(require('/usr/lib/node_modules/opencode-ai/package.json').version)")"; \
        for dir in /home/opencode/.config/opencode /home/opencode/.opencode; do \
            mkdir -p "$dir"; \
            printf '{\n  "dependencies": {\n    "@opencode-ai/plugin": "%s"\n  }\n}\n' "$SDK_VERSION" > "$dir/package.json"; \
            cd "$dir"; \
            npm install --no-audit --no-fund --cache /tmp/npm-cache-sdk; \
            test -d node_modules/@opencode-ai/plugin; \
            printf 'node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore\n' > "$dir/.gitignore"; \
        done; \
        rm -rf /tmp/npm-cache-sdk; \
        chown -R opencode:opencode /home/opencode/.config /home/opencode/.opencode; \
    fi

# ── Pre-bake the models catalog ─────────────────────────────────────────────
# opencode reads ~/.cache/opencode/models.json from disk before any fetch, and
# OPENCODE_DISABLE_MODELS_FETCH=1 (ENV below) stops the background refresh
# loop — startup performs zero downloads. Rebuild the image to refresh the
# catalog; override the env flag to empty to re-enable live refresh.
RUN curl -sfL https://models.opencode.ai/api.json -o /home/opencode/.cache/opencode/models.json && \
    chown opencode:opencode /home/opencode/.cache/opencode/models.json

# Immutable copy of the renderer inputs. The entrypoint falls back to this path
# when a volume mount shadows /workspace/.opencode, so a masked workspace can
# never downgrade the container to a chat-only config.
COPY config/opencode.json.tmpl config/vibe-trading-tools.json config/render_config.py /opt/opencode-config/

COPY config/opencode.json.tmpl /workspace/.opencode/opencode.json.tmpl
COPY config/oh-my-openagent.json /workspace/.opencode/oh-my-openagent.json
COPY config/tui.json /workspace/.opencode/tui.json
COPY config/package.json /workspace/.opencode/package.json
COPY config/vibe-trading-tools.json /workspace/.opencode/vibe-trading-tools.json
COPY config/render_config.py /workspace/.opencode/render_config.py
COPY config/command/ /workspace/.opencode/command/

COPY AGENTS.md /workspace/AGENTS.md

COPY skills/ /workspace/.opencode/skills/

COPY workspace/scripts/ /workspace/scripts/
COPY workspace/cron_jobs/ /workspace/cron_jobs/

COPY entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

RUN mkdir -p /workspace/analysis \
             /workspace/reports \
             /workspace/runs \
             /workspace/tmp \
             /workspace/cron_jobs/logs \
             /workspace/cron_jobs/state

# The runtime user must be able to create files under the writable
# workspace subdirectories when they are not volume-mounted.
RUN chown -R opencode:opencode /workspace

# ── Pre-bake the PROJECT-scope plugin tree ──────────────────────────────────
# opencode resolves plugins per project and installs the merged set (the SDK
# plus the plugin named by the global config) into <project>/.opencode/
# node_modules before serving the first request. Without this layer a cold
# /workspace pays a 63 MB registry install inside that request: measured
# 70-82s on the packaged image, which the strategy-run entry then waits
# behind. Baking the same tree here makes the first request find it already
# satisfied (6s control with a pre-seeded node_modules).
RUN set -eux; \
    SDK_VERSION="$(node -e "console.log(require('/usr/lib/node_modules/opencode-ai/package.json').version)")"; \
    mkdir -p /workspace/.opencode; \
    printf '{\n  "dependencies": {\n    "@opencode-ai/plugin": "%s"\n  }\n}\n' "$SDK_VERSION" > /workspace/.opencode/package.json; \
    cd /workspace/.opencode; \
    npm install --no-audit --no-fund --cache /tmp/npm-cache-project; \
    npm install --no-audit --no-fund --no-save "oh-my-openagent@${OMO_VERSION}" --cache /tmp/npm-cache-project; \
    test -d node_modules/@opencode-ai/plugin; \
    test -d "node_modules/oh-my-openagent"; \
    printf 'node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore\n' > /workspace/.opencode/.gitignore; \
    rm -rf /tmp/npm-cache-project; \
    chown -R opencode:opencode /workspace/.opencode

ENV LANGCHAIN_PROVIDER=dashscope \
    LANGCHAIN_MODEL_NAME=qwen3.8-max \
    DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
    LANGCHAIN_TEMPERATURE=0.3 \
    TZ=Asia/Shanghai \
    VT_MEMORY=full \
    VT_MEMORY_MCP_TOOLS=1 \
    VT_MEMORY_BASE_DIR=/workspace/.vt-memory

# Zero-download startup: suppress OMO telemetry (posthog + daily-active
# claim), the models-catalog background refresh, and opencode auto-update
# checks. To re-enable live catalog refresh, override the flag to empty:
#   docker run -e OPENCODE_DISABLE_MODELS_FETCH= ...
# OMO_VERSION is promoted to ENV so entrypoint.sh's fallback config stays in
# sync with the pre-baked plugin cache automatically.
ENV OMO_DISABLE_POSTHOG=1 \
    OMO_SEND_ANONYMOUS_TELEMETRY=0 \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    OMO_VERSION=${OMO_VERSION}

EXPOSE 4096 8000

# Both supervised services define container health: opencode on :4096 and the
# strategy-run entry on ${VT_STRATEGY_RUN_PORT:-8000} (the entrypoint exits if
# either process dies; this probe also covers a hung entry). opencode requires
# HTTP basic auth when OPENCODE_SERVER_PASSWORD is set; an unauthenticated
# probe gets 401 and the container reports unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf -u opencode:"$OPENCODE_SERVER_PASSWORD" http://localhost:4096/health \
     && curl -sf --max-time 5 "http://localhost:${VT_STRATEGY_RUN_PORT:-8000}/health" || exit 1

USER opencode

ENTRYPOINT ["/workspace/entrypoint.sh"]