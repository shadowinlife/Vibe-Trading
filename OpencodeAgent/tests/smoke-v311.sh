#!/usr/bin/env bash
# v3.11.0 onboarding-command smoke gate (2026-10-28, novice-onboarding-gprime
# todo 7). The slash commands are a discovery layer, so file existence is not
# the assertion: stage 2 asks the running server for its command list and then
# runs /checkup end to end.
#
# Stage 1 (in-container, no network): the four command files landed at
#   /workspace/.opencode/command/, their frontmatter is intact, and
#   render_config.py still produces a valid opencode.json.
# Stage 2 (real startup): GET /config?directory=/workspace returns 200, the
#   command-list endpoint is discovered from /doc (never guessed) and lists
#   checkup/screen/backtest/review, then one `opencode run --command checkup`
#   session must produce a non-empty assistant reply.
#
# Usage: bash tests/smoke-v311.sh [IMAGE]
#   IMAGE defaults to the onboarding image built from the current HEAD:
#   opencode-serve:onboarding-w1-$(git rev-parse --short HEAD).
set -euo pipefail

IMAGE="${1:-opencode-serve:onboarding-w1-$(git rev-parse --short HEAD)}"
PLATFORM="${PLATFORM:-linux/amd64}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
REPLY_FILE="${TMPDIR:-/tmp}/smoke-v311-checkup-$$.json"

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "FAIL: image $IMAGE not found; build it first (./build.sh --tag ${IMAGE#opencode-serve:})"
    exit 1
}

[ -f "$ENV_FILE" ] || { echo "FAIL: $ENV_FILE missing (OPENCODE_SERVER_PASSWORD + DASHSCOPE_API_KEY)"; exit 1; }
SERVER_PASSWORD="$(sed -n 's/^OPENCODE_SERVER_PASSWORD=//p' "$ENV_FILE" | tail -n1 | tr -d '\r')"
DASHSCOPE_KEY="$(sed -n 's/^DASHSCOPE_API_KEY=//p' "$ENV_FILE" | tail -n1 | tr -d '\r')"
if [ -z "$SERVER_PASSWORD" ] || [ -z "$DASHSCOPE_KEY" ]; then
    echo "FAIL: $ENV_FILE lacks OPENCODE_SERVER_PASSWORD or DASHSCOPE_API_KEY"
    exit 1
fi

echo "=== Stage 1: command files + config render ($IMAGE, $PLATFORM) ==="
# Required subset, not an exact-list snapshot: extra command files must pass.
docker run --rm --platform "$PLATFORM" --entrypoint bash "$IMAGE" -c '
set -e
for name in checkup screen backtest review; do
    file="/workspace/.opencode/command/$name.md"
    test -f "$file" || { echo "FAIL: $file missing"; exit 1; }
    grep -qx -- "---" <<< "$(head -n 1 "$file")" || { echo "FAIL: $file has no frontmatter"; exit 1; }
    grep -q "^description:" "$file" || { echo "FAIL: $file has no description"; exit 1; }
    grep -q "^argument-hint:" "$file" || { echo "FAIL: $file has no argument-hint"; exit 1; }
    grep -q "research-scenarios" "$file" || { echo "FAIL: $file does not route to research-scenarios"; exit 1; }
done
echo "COMMAND_FILES_OK"

# Onboarding content shipped by Wave 1: the intake skill, the trimmed
# AGENTS.md, and the question-tool permission on the primary agents.
#
# The AGENTS.md check asserts the documented plan cap (450 lines, the single
# source of truth is AGENTS_MD_MAX_LINES in tests/test_config_render.py) plus a
# sanity floor, not a pinned snapshot count. A pinned count fails on any
# legitimate content change — e.g. adding a table column shifts every following
# line number — so it would reject a healthy rebuild from a newer HEAD while
# saying nothing about whether the trim regressed.
test -f /workspace/.opencode/skills/intake/SKILL.md || { echo "FAIL: /workspace/.opencode/skills/intake/SKILL.md missing"; exit 1; }
actual_lines=$(wc -l < /workspace/AGENTS.md)
test "$actual_lines" -le 450 || { echo "FAIL: /workspace/AGENTS.md is $actual_lines lines, above the 450-line plan cap"; exit 1; }
test "$actual_lines" -ge 300 || { echo "FAIL: /workspace/AGENTS.md is $actual_lines lines, below the 300-line sanity floor (content likely lost)"; exit 1; }
echo "WORKSPACE_CONTENT_OK: AGENTS.md=$actual_lines lines (cap 450), skills/intake/SKILL.md present"

/opt/venv/bin/python3 /workspace/.opencode/render_config.py \
    --template /workspace/.opencode/opencode.json.tmpl \
    --manifest /workspace/.opencode/vibe-trading-tools.json \
    --target /tmp/opencode.json

/opt/venv/bin/python3 - << "PYEOF"
import json

config = json.load(open("/tmp/opencode.json"))
assert config["mcp"]["vibe-trading"]["type"] == "local"
assert config["model"] and config["plugin"]
assert config["agent"]["build"]["permission"]["question"] == "allow"
assert config["agent"]["plan"]["permission"]["question"] == "allow"
assert "question" not in config["agent"]["explore"].get("permission", {})
print("RENDER_OK: agent.build/plan permission.question=allow")
PYEOF
'
echo "=== Stage 1 PASS ==="

echo "=== Stage 2: real startup + runtime command discovery ==="
CID=$(docker run -d --platform "$PLATFORM" \
    -e "DASHSCOPE_API_KEY=$DASHSCOPE_KEY" \
    -e "OPENCODE_SERVER_PASSWORD=$SERVER_PASSWORD" \
    -e CLICKHOUSE_HOST= \
    "$IMAGE")
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true; rm -f "$REPLY_FILE" "${REPLY_FILE}.err"' EXIT

READY=0
for i in $(seq 1 60); do
    if docker exec "$CID" curl -sf -u "opencode:$SERVER_PASSWORD" \
        "http://127.0.0.1:4096/config?directory=/workspace" >/dev/null 2>&1; then
        echo "CONFIG_READY after ~$((i * 5))s"
        READY=1
        break
    fi
    sleep 5
done
if [ "$READY" != "1" ]; then
    echo "FAIL: /config not ready within 300s"
    docker logs --tail 40 "$CID" || true
    exit 1
fi

COMMAND_NAMES=$(docker exec -i "$CID" bash -s << 'CONTAINER_EOF'
set -euo pipefail
AUTH="opencode:${OPENCODE_SERVER_PASSWORD}"
DOC=$(curl -sf -u "$AUTH" "http://127.0.0.1:4096/doc")
CMD_PATH=$(printf '%s' "$DOC" | /opt/venv/bin/python3 -c '
import json, sys
paths = json.load(sys.stdin).get("paths", {})
found = sorted(p for p in paths if p.rstrip("/") in ("/command", "/commands"))
print(found[0] if found else "")
')
if [ -z "$CMD_PATH" ]; then
    echo "FAIL: GET /doc exposes no command-list endpoint" >&2
    exit 1
fi
echo "COMMAND_ENDPOINT: $CMD_PATH?directory=/workspace" >&2
curl -sf -u "$AUTH" "http://127.0.0.1:4096${CMD_PATH}?directory=/workspace" | /opt/venv/bin/python3 -c '
import json, sys
payload = json.load(sys.stdin)
items = payload if isinstance(payload, list) else (
    payload.get("data") or payload.get("commands") or []
)
names = sorted({item.get("name", "") for item in items if isinstance(item, dict)} - {""})
print(" ".join(names))
'
CONTAINER_EOF
) || { echo "FAIL: runtime command discovery failed"; exit 1; }

# Required subset, not an exact-list snapshot: extra runtime commands must pass.
for name in checkup screen backtest review; do
    case " $COMMAND_NAMES " in
        *" $name "*) ;;
        *) echo "FAIL: /$name missing from runtime command list: [$COMMAND_NAMES]"; exit 1 ;;
    esac
done
echo "RUNTIME_COMMANDS_OK: $COMMAND_NAMES"

echo "=== Stage 2b: /checkup runtime trigger (one LLM session) ==="
docker exec "$CID" bash -lc \
    'opencode run --attach http://127.0.0.1:4096 --dir /workspace --command checkup --format json "600519"' \
    > "$REPLY_FILE" 2>"${REPLY_FILE}.err" || {
        echo "FAIL: opencode run --command checkup exited non-zero"
        tail -20 "${REPLY_FILE}.err" || true
        exit 1
    }

PREVIEW=$(docker exec -i "$CID" /opt/venv/bin/python3 -c '
import json, sys

texts = []


def walk(node):
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            texts.append(node["text"])
        for value in node.values():
            walk(value)
    elif isinstance(node, list):
        for value in node:
            walk(value)


raw = sys.stdin.read()
for line in raw.splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        walk(json.loads(line))
    except json.JSONDecodeError:
        continue
reply = " ".join(texts).strip()
if len(reply) < 40:
    sys.exit(1)
print(" ".join(reply.split())[:200])
' < "$REPLY_FILE") || {
    echo "FAIL: /checkup produced no non-empty assistant reply (first 200 chars of raw output below)"
    head -c 200 "$REPLY_FILE" || true
    exit 1
}
echo "CHECKUP_REPLY_OK"

echo "=== SMOKE_OK ==="
echo "image: $IMAGE"
echo "runtime command list: $COMMAND_NAMES"
echo "checkup reply (first 200 chars): $PREVIEW"