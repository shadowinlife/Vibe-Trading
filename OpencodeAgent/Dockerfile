# =============================================================================
# Dockerfile: opencode-serve app image
# Base:  opencode-serve-base (Ubuntu 22.04 + Python 3.12 + Node 20 + OpenCode +
#        playwright + pip packages)
# 构筑: docker build --platform linux/amd64 -t opencode-serve:v2.0.0 .
# =============================================================================

FROM opencode-serve-base:latest

USER root

RUN mkdir -p /root/.config/pip && \
    printf '[global]\nindex-url=https://mirrors.aliyun.com/pypi/simple/\n\n[install]\ntrusted-host=mirrors.aliyun.com\n' \
    > /root/.config/pip/pip.conf

# opencode-ai comes from the base image (pinned in Dockerfile.base). The
# duplicate `npm install -g opencode-ai@latest` that used to live here added
# a ~594MB layer on top of the base install — removed (P2#7).

COPY vendor/Vibe-Trading/ /opt/vibe-trading/
RUN pip install --no-cache-dir -e /opt/vibe-trading/

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
ARG OMO_VERSION=5.1.0
RUN mkdir -p "/home/opencode/.cache/opencode/packages/oh-my-openagent@${OMO_VERSION}" && \
    cd "/home/opencode/.cache/opencode/packages/oh-my-openagent@${OMO_VERSION}" && \
    printf '{\n  "dependencies": {\n    "oh-my-openagent": "%s"\n  }\n}\n' "${OMO_VERSION}" > package.json && \
    npm install --no-audit --no-fund --cache /tmp/npm-cache && \
    rm -rf /tmp/npm-cache && \
    test -d node_modules/oh-my-openagent && \
    chown -R opencode:opencode /home/opencode/.cache

# ── Pre-bake the models catalog ─────────────────────────────────────────────
# opencode reads ~/.cache/opencode/models.json from disk before any fetch, and
# OPENCODE_DISABLE_MODELS_FETCH=1 (ENV below) stops the background refresh
# loop — startup performs zero downloads. Rebuild the image to refresh the
# catalog; override the env flag to empty to re-enable live refresh.
RUN curl -sfL https://models.opencode.ai/api.json -o /home/opencode/.cache/opencode/models.json && \
    chown opencode:opencode /home/opencode/.cache/opencode/models.json

COPY config/opencode.json.tmpl /workspace/.opencode/opencode.json.tmpl
COPY config/oh-my-openagent.json /workspace/.opencode/oh-my-openagent.json
COPY config/tui.json /workspace/.opencode/tui.json
COPY config/package.json /workspace/.opencode/package.json
COPY config/vibe-trading-tools.json /workspace/.opencode/vibe-trading-tools.json
COPY config/render_config.py /workspace/.opencode/render_config.py

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

EXPOSE 4096

# opencode serve requires HTTP basic auth when OPENCODE_SERVER_PASSWORD is set;
# an unauthenticated probe gets 401 and the container reports unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf -u opencode:"$OPENCODE_SERVER_PASSWORD" http://localhost:4096/health || exit 1

USER opencode

ENTRYPOINT ["/workspace/entrypoint.sh"]