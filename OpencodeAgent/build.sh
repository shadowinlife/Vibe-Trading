#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="opencode-serve"
IMAGE_TAG="latest"
# Push targets are FULL image repositories (only ":${IMAGE_TAG}" is appended).
# Resolution order: environment, then OpencodeAgent/.env (gitignored operator
# file). There is deliberately NO hardcoded fallback registry: a --push that
# cannot resolve a target fails fast instead of silently shipping the image to
# a stale registry. BASE_IMAGE_REGISTRY defaults to "${IMAGE_REGISTRY}-base".
IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
BASE_IMAGE_REGISTRY="${BASE_IMAGE_REGISTRY:-}"
PLATFORM="${DOCKER_PLATFORM:-}"
PUSH=false
DRY_RUN=false
MODE="app"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)        IMAGE_TAG="$2"; shift 2 ;;
    --tag=*)      IMAGE_TAG="${1#*=}"; shift ;;
    --push)       PUSH=true; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --base)       MODE="base"; shift ;;
    --app)        MODE="app"; shift ;;
    --help|-h)
      echo "Usage: $0 [--base|--app] [--tag TAG] [--push] [--dry-run]"
      echo ""
      echo "Modes:"
      echo "  --base    Build opencode-serve-base (heavy deps, rarely)"
      echo "  --app     Build opencode-serve app image (default)"
      echo ""
      echo "Options:"
      echo "  --tag TAG     Image tag (default: latest)"
      echo "  --push        Push after build. Target: \$IMAGE_REGISTRY (full image"
      echo "                repository, from env or OpencodeAgent/.env); base image:"
      echo "                \$BASE_IMAGE_REGISTRY (default \${IMAGE_REGISTRY}-base)."
      echo "                Fails fast when unresolved - no default registry."
      echo "  --dry-run     Show commands without executing"
      exit 0
      ;;
    *) shift ;;
  esac
done

run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo ">>> $*"
    "$@"
  fi
}

# Shared by both modes. The ${arr[@]+"${arr[@]}"} idiom keeps empty-array
# expansion safe under `set -u` on bash 3.2 (macOS default).
PLATFORM_ARG=()
[ -n "$PLATFORM" ] && PLATFORM_ARG=(--platform "$PLATFORM")

# Pick up push targets from the operator's .env when not already set in the
# environment. Targeted extraction only: the file also holds runtime secrets
# the build shell has no business inheriting.
if [ -f "$SCRIPT_DIR/.env" ]; then
  if [ -z "$IMAGE_REGISTRY" ]; then
    IMAGE_REGISTRY="$(sed -n 's/^IMAGE_REGISTRY=//p' "$SCRIPT_DIR/.env" | tail -n1 | tr -d '\r')"
  fi
  if [ -z "$BASE_IMAGE_REGISTRY" ]; then
    BASE_IMAGE_REGISTRY="$(sed -n 's/^BASE_IMAGE_REGISTRY=//p' "$SCRIPT_DIR/.env" | tail -n1 | tr -d '\r')"
  fi
fi
if [ -z "$BASE_IMAGE_REGISTRY" ] && [ -n "$IMAGE_REGISTRY" ]; then
  BASE_IMAGE_REGISTRY="${IMAGE_REGISTRY}-base"
fi

# ---------------------------------------------------------------------------
# Base image build
# ---------------------------------------------------------------------------
if [ "$MODE" = "base" ]; then
  BASE_TAG="${IMAGE_TAG:-latest}"
  echo "=== Building base image: opencode-serve-base:${BASE_TAG} ==="
  run docker build \
    ${PLATFORM_ARG[@]+"${PLATFORM_ARG[@]}"} \
    -t "opencode-serve-base:${BASE_TAG}" \
    -f "$SCRIPT_DIR/Dockerfile.base" \
    "$SCRIPT_DIR"

  if $PUSH; then
    if [ -z "$BASE_IMAGE_REGISTRY" ]; then
      echo "ERROR: --base --push needs BASE_IMAGE_REGISTRY (or IMAGE_REGISTRY to derive <repo>-base)."
      echo "       Set it in the environment or in OpencodeAgent/.env; there is no default registry."
      exit 1
    fi
    FULL_IMAGE="${BASE_IMAGE_REGISTRY}:${BASE_TAG}"
    run docker tag "opencode-serve-base:${BASE_TAG}" "$FULL_IMAGE"
    run docker push "$FULL_IMAGE"
    echo "=== Base push complete: $FULL_IMAGE ==="
  fi
  echo "=== Base image done: opencode-serve-base:${BASE_TAG} ==="
  exit 0
fi

# ---------------------------------------------------------------------------
# App image build
# ---------------------------------------------------------------------------
VT_SOURCE="${VT_SOURCE:-..}"
VENDOR_DIR="$SCRIPT_DIR/vendor/Vibe-Trading"

if [[ "$VT_SOURCE" == http* ]]; then
    echo "=== Cloning Vibe-Trading from $VT_SOURCE (mymain branch) ==="
    rm -rf "$VENDOR_DIR"
    git clone --depth 1 -b mymain "$VT_SOURCE" "$VENDOR_DIR"
    echo "=== VT cloned: $(find "$VENDOR_DIR" -type f -name '*.py' | wc -l) Python files ==="
elif [ -d "$VT_SOURCE" ]; then
    echo "=== Vendoring Vibe-Trading from $VT_SOURCE (mymain branch) ==="
    VT_BRANCH=$(cd "$VT_SOURCE" && git branch --show-current 2>/dev/null || echo "unknown")
    if [ "$VT_BRANCH" != "mymain" ]; then
        echo "WARNING: VT source is on branch '$VT_BRANCH', expected 'mymain'"
    fi
    # Fresh copy of the COMMITTED tree via git archive — guarantees no
    # untracked dev artifacts (.omo/.qoder sessions, screenshots, caches)
    # leak into the vendor dir, and no stale files survive from prior builds.
    rm -rf "$VENDOR_DIR"
    mkdir -p "$VENDOR_DIR"
    git -C "$VT_SOURCE" archive "$VT_BRANCH" | tar -x -C "$VENDOR_DIR"
    # Same exclusions the app image does not need (kept in sync with Dockerfile COPY)
    rm -rf "$VENDOR_DIR/frontend" "$VENDOR_DIR/node_modules" "$VENDOR_DIR/tests" \
           "$VENDOR_DIR/agent/tests" "$VENDOR_DIR/assets" "$VENDOR_DIR/.codex" \
           "$VENDOR_DIR/OpencodeAgent"
    echo "=== VT vendored: $(find "$VENDOR_DIR" -type f -name '*.py' | wc -l) Python files ==="
else
    echo "ERROR: Vibe-Trading source not found at $VT_SOURCE"
    exit 1
fi

echo "=== Building app image: ${IMAGE_NAME}:${IMAGE_TAG} ==="
run docker build \
  ${PLATFORM_ARG[@]+"${PLATFORM_ARG[@]}"} \
  -t "${IMAGE_NAME}:${IMAGE_TAG}" \
  -f "$SCRIPT_DIR/Dockerfile" \
  "$SCRIPT_DIR"

if $PUSH; then
  if [ -z "$IMAGE_REGISTRY" ]; then
    echo "ERROR: --push needs IMAGE_REGISTRY (a FULL image repository path,"
    echo "       e.g. spark-daily-it-registry.cn-hangzhou.cr.aliyuncs.com/test/opencode)."
    echo "       Set it in the environment or in OpencodeAgent/.env; there is no default registry."
    exit 1
  fi
  FULL_IMAGE="${IMAGE_REGISTRY}:${IMAGE_TAG}"
  run docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "$FULL_IMAGE"
  run docker push "$FULL_IMAGE"
  echo "=== Push complete: $FULL_IMAGE ==="
fi

echo "=== App image done: ${IMAGE_NAME}:${IMAGE_TAG} ==="
echo ""
echo "Run with docker-compose:"
echo "  cp .env.example .env"
echo "  docker compose up -d"