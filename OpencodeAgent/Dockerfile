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

RUN npm install -g opencode-ai@latest

# OMO plugin is NOT installed here: executing the opencode binary at build
# time SIGILLs under cross-arch QEMU emulation (arm64 host -> amd64 image).
# entrypoint.sh renders "plugin": ["oh-my-openagent@latest"] into
# opencode.json, so opencode auto-installs it on first startup (~30s) —
# the runtime-verified pattern of the previously deployed images.

COPY vendor/Vibe-Trading/ /opt/vibe-trading/
RUN pip install --no-cache-dir -e /opt/vibe-trading/

COPY nano-search-mcp/ /opt/nano-search-mcp/
RUN pip install --no-cache-dir -e /opt/nano-search-mcp

COPY config/opencode.json.tmpl /workspace/.opencode/opencode.json.tmpl
COPY config/oh-my-openagent.json /workspace/.opencode/oh-my-openagent.json
COPY config/tui.json /workspace/.opencode/tui.json
COPY config/package.json /workspace/.opencode/package.json
COPY config/vibe-trading-tools.json /workspace/.opencode/vibe-trading-tools.json

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
    LANGCHAIN_MODEL_NAME=qwen3.7-max \
    DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
    LANGCHAIN_TEMPERATURE=0.3 \
    TZ=Asia/Shanghai \
    VT_MEMORY=full \
    VT_MEMORY_MCP_TOOLS=1 \
    VT_MEMORY_BASE_DIR=/workspace/.vt-memory

EXPOSE 4096

# opencode serve requires HTTP basic auth when OPENCODE_SERVER_PASSWORD is set;
# an unauthenticated probe gets 401 and the container reports unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf -u opencode:"$OPENCODE_SERVER_PASSWORD" http://localhost:4096/health || exit 1

USER opencode

ENTRYPOINT ["/workspace/entrypoint.sh"]