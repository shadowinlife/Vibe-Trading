#!/usr/bin/env bash
# check_startup_readiness.sh — 镜像打包期启动就绪门禁 (T9 / T8)
#
# 背景：`opencode serve` 在首次 /config bootstrap 完成之前就已绑定 :4096。
# 没有 startupProbe 时 Pod 会过早 Ready，bootstrap 期间的所有请求都撞上
# ReadTimeout（生产事故见 .omo/plans/sandbox-startup-probe-canary.md）。
# 自 T8 起容器还必须在 `${VT_STRATEGY_RUN_PORT:-8000}` 上提供专用的
# strategy-run entry；只测 opencode 会把「入口没起来」的镜像放行。
# 本脚本在镜像打包阶段把「启动慢 / 入口缺失」挡住，避免把缺陷带到生产。
#
# 做六件事（不接真实 LLM，不使用任何真实凭据，不挂载 RM token）：
#   1. 用一次性的 dummy OPENCODE_SERVER_PASSWORD 启动容器；
#   2. 等待日志出现 `opencode server listening`、`[entrypoint] warmup complete`、
#      `[entrypoint] VT MCP connected` 与 `strategy-run entry listening` 四条标记；
#   3. 计算「容器启动 → 首次认证 GET /config?directory=/workspace 成功」的墙钟。
#      entrypoint 的 warmup curl 就是这次请求，所以取该日志行的时间戳；
#      再用一次显式 `docker exec curl` 复核端点确实返回 200；
#   4. 计算「容器启动 → VT MCP 连上」的墙钟，并复核 `GET /mcp` 里
#      `vibe-trading` 为 `connected`——只验 import 不验连接会漏掉
#      「镜像能跑但模型看不到 vibe-trading_* 工具」这一类缺陷；
#   5. 计算「容器启动 → strategy-run entry /health 返回 200」的墙钟，同样用
#      `docker exec curl` 复核（/health 无需 token，未挂载 token 也必须 200）；
#   6. 标记缺失 / 任一首次 200 超过 STARTUP_BUDGET_S / 容器提前退出 → 非零退出；
#      无论成功失败都通过 trap 清理容器。
#
# 用法：
#   ./scripts/check_startup_readiness.sh [IMAGE]
#   IMAGE=opencode-serve:v3.8.2-mymain STARTUP_BUDGET_S=60 \
#     ./scripts/check_startup_readiness.sh
#
# 环境变量：
#   IMAGE              默认 opencode-serve:v3.8.2-mymain
#   STARTUP_BUDGET_S   默认 60（opencode 首次 /config 200 与入口首次 /health 200
#                      各自相对容器启动的秒数上限）
#   ENTRY_PORT         strategy-run entry 端口，默认 ${VT_STRATEGY_RUN_PORT:-8000}
#   CONTAINER_NAME     默认 opencode-readiness-<pid>-<epoch>
#   PLATFORM           默认 linux/amd64（与 tests/smoke-v370.sh 一致）
#   OFFLINE            默认 1：加 --network none，证明启动零下载；
#                      置 0 允许容器访问网络（排查用）
#   REQUIRE_IMAGE      默认 0：镜像不存在时 SKIP 并退出 0；置 1 则视为失败
#   HARD_TIMEOUT_S     默认 STARTUP_BUDGET_S + 60（等待标记的诊断上限）
#   KEEP_CONTAINER     默认 0：置 1 保留容器供排查
#   EXTRA_DOCKER_ARGS  可选，追加到 docker run（空格分隔，CI/预烘验证用）
#
# 注意：脚本里的密码是每次运行随机生成的丢弃值，LLM key 是字面 dummy，
# 不读取 .env，也不允许注入真实 secret。

set -euo pipefail

say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

IMAGE="${1:-${IMAGE:-opencode-serve:v3.8.2-mymain}}"
STARTUP_BUDGET_S="${STARTUP_BUDGET_S:-60}"
case "$STARTUP_BUDGET_S" in
    '' | *[!0-9]*) die "STARTUP_BUDGET_S 必须是正整数（当前：${STARTUP_BUDGET_S}）" ;;
esac
STARTUP_BUDGET_S="$((10#$STARTUP_BUDGET_S))"
PLATFORM="${PLATFORM:-linux/amd64}"
OFFLINE="${OFFLINE:-1}"
REQUIRE_IMAGE="${REQUIRE_IMAGE:-0}"
KEEP_CONTAINER="${KEEP_CONTAINER:-0}"
ENTRY_PORT="${ENTRY_PORT:-${VT_STRATEGY_RUN_PORT:-8000}}"
case "$ENTRY_PORT" in
    '' | *[!0-9]*) die "ENTRY_PORT 必须是正整数（当前：${ENTRY_PORT}）" ;;
esac
CONTAINER_NAME="${CONTAINER_NAME:-opencode-readiness-$$-$(date +%s)}"
HARD_TIMEOUT_S="${HARD_TIMEOUT_S:-$((STARTUP_BUDGET_S + 60))}"

LISTEN_MARKER="opencode server listening"
# 镜像里的这行由 opencode 二进制打印（无 [entrypoint] 前缀），warmup 行由
# entrypoint 打印；入口行由 strategy_run_server 在成功 bind 后打印。
# 三者都必须出现。
WARMUP_MARKER="[entrypoint] warmup complete"
MCP_MARKER="[entrypoint] VT MCP connected"
ENTRY_LISTEN_MARKER="strategy-run entry listening"
PROJECT_DIR="/workspace"
CONFIG_URL="http://127.0.0.1:4096/config?directory=${PROJECT_DIR}"
# 与 entrypoint 的启动闸门同实例：会话目录为 /workspace，`/mcp` 支持 directory 参数。
MCP_URL="http://127.0.0.1:4096/mcp?directory=${PROJECT_DIR}"
ENTRY_HEALTH_URL="http://127.0.0.1:${ENTRY_PORT}/health"

CID=""

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    if [ -n "$CID" ] && [ "$KEEP_CONTAINER" != "1" ]; then
        docker rm -f "$CID" >/dev/null 2>&1 || true
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

# RFC3339Nano（docker inspect / docker logs -t）→ epoch 秒。
# macOS 自带 python3；没有 python3 时退回 GNU date（Linux/ECS）。
iso_to_epoch() {
    local ts="$1"
    if [ -z "$ts" ]; then
        return 1
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import datetime, sys; print(datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).timestamp())' "$ts" 2>/dev/null && return 0
    fi
    date -u -d "$ts" +%s.%N 2>/dev/null && return 0
    return 1
}

elapsed_since() {
    # $1 = 起点 epoch，$2 = 终点 epoch
    awk -v start="$1" -v end="$2" 'BEGIN { printf "%.1f", end - start }'
}

log_timestamp_for() {
    # $1 = 日志标记（固定字符串）
    docker logs --timestamps "$CID" 2>&1 | grep -F -- "$1" | tail -n 1 | awk '{print $1}' || true
}

command -v docker >/dev/null 2>&1 || die "找不到 docker CLI"

if ! [[ "$STARTUP_BUDGET_S" =~ ^[0-9]+$ ]]; then
    die "STARTUP_BUDGET_S 必须是非负整数秒，当前值：$STARTUP_BUDGET_S"
fi
if ! [[ "$HARD_TIMEOUT_S" =~ ^[0-9]+$ ]]; then
    die "HARD_TIMEOUT_S 必须是非负整数秒，当前值：$HARD_TIMEOUT_S"
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    if [ "$REQUIRE_IMAGE" = "1" ]; then
        die "本地不存在镜像 ${IMAGE}（REQUIRE_IMAGE=1 要求必须存在）"
    fi
    say "SKIP: 本地不存在镜像 ${IMAGE}，未执行启动就绪检查"
    say "      先构建镜像，或设置 REQUIRE_IMAGE=1 把「镜像缺失」视为失败"
    exit 0
fi

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE" | cut -c1-19)"
IMAGE_ARCH="$(docker image inspect --format '{{.Architecture}}' "$IMAGE")"
# 一次性丢弃密码：仅用于本次容器，不落盘、不回显。
DUMMY_PASSWORD="readiness-$(date +%s)-$$-${RANDOM}${RANDOM}"

say "=== 启动就绪检查 ==="
say "镜像: $IMAGE (${IMAGE_ID}, ${IMAGE_ARCH})"
say "预算: ${STARTUP_BUDGET_S}s | 平台: $PLATFORM | 离线: $OFFLINE | 容器: $CONTAINER_NAME"

RUN_ARGS=(--name "$CONTAINER_NAME" --platform "$PLATFORM"
    -e "OPENCODE_SERVER_PASSWORD=$DUMMY_PASSWORD"
    -e "DASHSCOPE_API_KEY=dummy-not-a-real-key"
    -e "VT_STRATEGY_RUN_PORT=$ENTRY_PORT"
    -e "VT_STARTUP_BUDGET_S=$STARTUP_BUDGET_S"
    -e "CLICKHOUSE_HOST=")
if [ "$OFFLINE" = "1" ]; then
    RUN_ARGS+=(--network none)
fi
if [ -n "${EXTRA_DOCKER_ARGS:-}" ]; then
    # shellcheck disable=SC2206  # 允许调用方传多个以空格分隔的参数
    EXTRA_ARGS=(${EXTRA_DOCKER_ARGS})
    RUN_ARGS+=("${EXTRA_ARGS[@]}")
fi

T_BEFORE_RUN="$(date +%s)"
if ! CID="$(docker run -d "${RUN_ARGS[@]}" "$IMAGE")"; then
    CID=""
    die "docker run 失败（镜像 ${IMAGE}）"
fi
say "容器已启动: $(printf '%s' "$CID" | cut -c1-12)"

STARTED_AT="$(docker inspect --format '{{.State.StartedAt}}' "$CID" 2>/dev/null || true)"
START_EPOCH="$(iso_to_epoch "$STARTED_AT" || true)"
if [ -z "$START_EPOCH" ]; then
    START_EPOCH="$T_BEFORE_RUN"
    warn "无法解析 StartedAt（${STARTED_AT}），改用 docker run 之前的墙钟，误差 <1s"
fi

DEADLINE=$(( $(date +%s) + HARD_TIMEOUT_S ))
saw_listening=0
saw_warmup=0
saw_mcp=0
saw_entry=0

while :; do
    LOGS="$(docker logs --timestamps "$CID" 2>&1 || true)"
    if printf '%s\n' "$LOGS" | grep -Fq -- "$LISTEN_MARKER"; then
        saw_listening=1
    fi
    if printf '%s\n' "$LOGS" | grep -Fq -- "$WARMUP_MARKER"; then
        saw_warmup=1
    fi
    if printf '%s\n' "$LOGS" | grep -Fq -- "$MCP_MARKER"; then
        saw_mcp=1
    fi
    if printf '%s\n' "$LOGS" | grep -Fq -- "$ENTRY_LISTEN_MARKER"; then
        saw_entry=1
    fi
    if [ "$saw_listening" = "1" ] && [ "$saw_warmup" = "1" ] \
        && [ "$saw_mcp" = "1" ] && [ "$saw_entry" = "1" ]; then
        break
    fi

    RUNNING="$(docker inspect --format '{{.State.Running}}' "$CID" 2>/dev/null || echo false)"
    if [ "$RUNNING" != "true" ]; then
        EXIT_CODE="$(docker inspect --format '{{.State.ExitCode}}' "$CID" 2>/dev/null || echo unknown)"
        say "--- 容器日志（尾部） ---"
        printf '%s\n' "$LOGS" | tail -n 40
        die "容器提前退出（exit=${EXIT_CODE}），未出现 listening/warmup/mcp/entry 标记"
    fi
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        say "--- 容器日志（尾部） ---"
        printf '%s\n' "$LOGS" | tail -n 40
        die "在 ${HARD_TIMEOUT_S}s 内未看到启动标记（listening=${saw_listening} warmup=${saw_warmup} mcp=${saw_mcp} entry=${saw_entry}）"
    fi
    sleep 1
done

PROJECT_CWD="$(docker exec "$CID" sh -c 'pwd' 2>/dev/null || true)"
if [ "$PROJECT_CWD" != "$PROJECT_DIR" ]; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "容器工作目录是 ${PROJECT_CWD:-<unknown>}，期望 ${PROJECT_DIR}（opencode 会把 /.omo 写到根目录）"
fi
if ! docker exec "$CID" sh -c "test -w '$PROJECT_DIR'"; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "容器工作目录不可写：${PROJECT_DIR}"
fi
say "[ok] container cwd=${PROJECT_CWD} (writable)"

LISTEN_TS="$(log_timestamp_for "$LISTEN_MARKER")"
WARMUP_TS="$(log_timestamp_for "$WARMUP_MARKER")"
MCP_TS="$(log_timestamp_for "$MCP_MARKER")"
ENTRY_TS="$(log_timestamp_for "$ENTRY_LISTEN_MARKER")"
LISTEN_EPOCH="$(iso_to_epoch "$LISTEN_TS" || true)"
WARMUP_EPOCH="$(iso_to_epoch "$WARMUP_TS" || true)"
MCP_EPOCH="$(iso_to_epoch "$MCP_TS" || true)"
ENTRY_EPOCH="$(iso_to_epoch "$ENTRY_TS" || true)"

if [ -z "$WARMUP_EPOCH" ]; then
    die "无法解析 warmup 时间戳（${WARMUP_TS}）"
fi
if [ -z "$LISTEN_EPOCH" ]; then
    die "无法解析 listening 时间戳（${LISTEN_TS}）"
fi
if [ -z "$MCP_EPOCH" ]; then
    die "无法解析 VT MCP 时间戳（${MCP_TS}）"
fi
if [ -z "$ENTRY_EPOCH" ]; then
    die "无法解析 entry listening 时间戳（${ENTRY_TS}）"
fi

LISTEN_S="$(elapsed_since "$START_EPOCH" "$LISTEN_EPOCH")"
FIRST_200_S="$(elapsed_since "$START_EPOCH" "$WARMUP_EPOCH")"
MCP_S="$(elapsed_since "$START_EPOCH" "$MCP_EPOCH")"
ENTRY_LISTEN_S="$(elapsed_since "$START_EPOCH" "$ENTRY_EPOCH")"

# 显式复核：entrypoint 之外再发一次认证请求，确认端点此刻确实是 200。
HTTP_CODE="$(docker exec "$CID" curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    -u "opencode:$DUMMY_PASSWORD" "$CONFIG_URL" 2>/dev/null || true)"
# MCP 状态是模型能否看到 vibe-trading_* 工具的唯一真凭据；只验 import 不够。
MCP_BODY="$(docker exec "$CID" curl -s --max-time 10 \
    -u "opencode:$DUMMY_PASSWORD" "$MCP_URL" 2>/dev/null || true)"
if printf '%s' "$MCP_BODY" | grep -Eq '"vibe-trading"[[:space:]]*:[[:space:]]*\{[^}]*"status"[[:space:]]*:[[:space:]]*"connected"'; then
    MCP_OK=1
else
    MCP_OK=0
fi
# 入口 /health 无需认证；未挂载 RM token 时也必须返回 200（服务路由才 403）。
ENTRY_HTTP_CODE="$(docker exec "$CID" curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    "$ENTRY_HEALTH_URL" 2>/dev/null || true)"
PROBE_S="$(elapsed_since "$START_EPOCH" "$(date +%s)")"
ENTRY_HEALTH_S="$PROBE_S"

say "[ok] opencode server listening @ ${LISTEN_S}s"
say "[ok] warmup complete（首次 /config 200）@ ${FIRST_200_S}s"
say "[ok] docker exec 复核 GET /config → HTTP ${HTTP_CODE:-N/A} @ ≈${PROBE_S}s"
say "[ok] VT MCP connected @ ${MCP_S}s"
say "[ok] docker exec 复核 GET /mcp → vibe-trading connected=$([ "$MCP_OK" = "1" ] && echo yes || echo no)"
say "[ok] strategy-run entry listening @ ${ENTRY_LISTEN_S}s"
say "[ok] docker exec 复核 GET ${ENTRY_HEALTH_URL} → HTTP ${ENTRY_HTTP_CODE:-N/A} @ ≈${ENTRY_HEALTH_S}s"

if [ "$HTTP_CODE" != "200" ]; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "复核请求未返回 200（实际：${HTTP_CODE:-无响应}）"
fi

if [ "$MCP_OK" != "1" ]; then
    say "--- /mcp 响应 ---"
    printf '%s\n' "${MCP_BODY:-<empty>}"
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "VT MCP 未连接：/mcp 中 vibe-trading 不是 connected（模型将看不到 vibe-trading_* 工具）"
fi

if [ "$ENTRY_HTTP_CODE" != "200" ]; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "strategy-run entry /health 未返回 200（实际：${ENTRY_HTTP_CODE:-无响应}）"
fi

if awk -v value="$FIRST_200_S" -v budget="$STARTUP_BUDGET_S" 'BEGIN { exit !(value > budget) }'; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "首次 /config 200 用了 ${FIRST_200_S}s，超过预算 ${STARTUP_BUDGET_S}s（镜像应预烘插件 SDK，见 Dockerfile PREBAKE_PLUGIN_SDK）"
fi

if awk -v value="$MCP_S" -v budget="$STARTUP_BUDGET_S" 'BEGIN { exit !(value > budget) }'; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "VT MCP 连接用了 ${MCP_S}s，超过预算 ${STARTUP_BUDGET_S}s"
fi

if awk -v value="$ENTRY_HEALTH_S" -v budget="$STARTUP_BUDGET_S" 'BEGIN { exit !(value > budget) }'; then
    say "--- 容器日志（尾部） ---"
    docker logs --timestamps "$CID" 2>&1 | tail -n 40
    die "strategy-run entry 首次 /health 200 用了 ${ENTRY_HEALTH_S}s，超过预算 ${STARTUP_BUDGET_S}s"
fi

say "PASS: 首次 /config 200 = ${FIRST_200_S}s ≤ 预算 ${STARTUP_BUDGET_S}s；VT MCP = ${MCP_S}s；entry /health 200 = ${ENTRY_HEALTH_S}s ≤ 预算 ${STARTUP_BUDGET_S}s（端口 ${ENTRY_PORT}）"