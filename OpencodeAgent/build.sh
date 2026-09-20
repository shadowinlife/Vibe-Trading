#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="opencode-serve"
IMAGE_TAG="latest"
# Push target for --push. Not defaulted on purpose: this is a public fork and a
# registry namespace is infrastructure identity, not a build parameter. Export it
# in the calling shell; do not re-hardcode it here. This script does not read .env.
REGISTRY="${REGISTRY:-}"
PLATFORM="${DOCKER_PLATFORM:-}"
PUSH=false
DRY_RUN=false
MODE="app"

INVOCATION_ARGS="$*"
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
      echo "  --push        Push to \$REGISTRY after build (requires REGISTRY to be set)"
      echo "  --dry-run     Show commands without executing"
      exit 0
      ;;
    *) shift ;;
  esac
done

if $PUSH && [[ -z "$REGISTRY" ]]; then
  echo "ERROR: --push requires REGISTRY to be set, e.g." >&2
  echo "  REGISTRY=registry.<region>.aliyuncs.com/<namespace> $0 $INVOCATION_ARGS" >&2
  exit 1
fi

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
    FULL_IMAGE="${REGISTRY}/opencode-serve-base:${BASE_TAG}"
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
# The bridge module + all engine-bridge fixes live ONLY on mymain-engine-bridge
# (merge-back to mymain is a user-gated step). The tenant image must vendor that
# branch — every prior image predates the bridge (T2 baseline_memo §5).
EXPECTED_VT_BRANCH="${EXPECTED_VT_BRANCH:-mymain-engine-bridge}"

if [[ "$VT_SOURCE" == http* ]]; then
    echo "=== Cloning Vibe-Trading from $VT_SOURCE ($EXPECTED_VT_BRANCH branch) ==="
    echo "NOTE: $EXPECTED_VT_BRANCH must be pushed to the remote for the http path;"
    echo "      it is local-only until the user-gated merge-back. Prefer the local path."
    rm -rf "$VENDOR_DIR"
    git clone --depth 1 -b "$EXPECTED_VT_BRANCH" "$VT_SOURCE" "$VENDOR_DIR"
    echo "=== VT cloned: $(find "$VENDOR_DIR" -type f -name '*.py' | wc -l) Python files ==="
elif [ -d "$VT_SOURCE" ]; then
    echo "=== Vendoring Vibe-Trading from $VT_SOURCE ($EXPECTED_VT_BRANCH branch) ==="
    VT_BRANCH=$(cd "$VT_SOURCE" && git branch --show-current 2>/dev/null || echo "unknown")
    if [ "$VT_BRANCH" != "$EXPECTED_VT_BRANCH" ]; then
        echo "WARNING: VT source is on branch '$VT_BRANCH', expected '$EXPECTED_VT_BRANCH'"
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
    # Frontend SPA: git archive carries frontend SOURCES (tracked) but NOT dist
    # (gitignored build artifact). Build it on the host (native arch → the dist is
    # arch-independent static assets) and land ONLY dist where helpers.py::
    # _FRONTEND_DIST and api_server.py expect it: <VT-root>/frontend/dist. Without
    # this the gateway degrades API-only (T2 §4.2 gap). Building ≠ editing source.
    echo "=== Building frontend SPA (npm ci && npm run build) ==="
    ( cd "$VT_SOURCE/frontend" && npm ci && npm run build )
    mkdir -p "$VENDOR_DIR/frontend"
    cp -r "$VT_SOURCE/frontend/dist" "$VENDOR_DIR/frontend/dist"
    echo "=== Frontend dist landed: $(find "$VENDOR_DIR/frontend/dist" -type f | wc -l) files ==="
    echo "=== VT vendored: $(find "$VENDOR_DIR" -type f -name '*.py' | wc -l) Python files ==="
else
    echo "ERROR: Vibe-Trading source not found at $VT_SOURCE"
    exit 1
fi

# Source provenance for the image label (record the archived commit so a build is
# traceable; a parallel T14 commit may move HEAD — rebuild if it lands).
VT_COMMIT=$(git -C "$VT_SOURCE" rev-parse "$VT_BRANCH" 2>/dev/null || echo "unknown")
VT_COMMIT_SHORT=$(git -C "$VT_SOURCE" rev-parse --short "$VT_BRANCH" 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "=== Vendored source commit: ${VT_COMMIT_SHORT} (${VT_BRANCH}) built ${BUILD_DATE} ==="

echo "=== Building app image: ${IMAGE_NAME}:${IMAGE_TAG} ==="
run docker build \
  ${PLATFORM_ARG[@]+"${PLATFORM_ARG[@]}"} \
  --build-arg VT_SOURCE_COMMIT="$VT_COMMIT" \
  --build-arg VT_SOURCE_BRANCH="$VT_BRANCH" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  -t "${IMAGE_NAME}:${IMAGE_TAG}" \
  -f "$SCRIPT_DIR/Dockerfile" \
  "$SCRIPT_DIR"

if $PUSH; then
  FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
  run docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "$FULL_IMAGE"
  run docker push "$FULL_IMAGE"
  echo "=== Push complete: $FULL_IMAGE ==="
fi

echo "=== App image done: ${IMAGE_NAME}:${IMAGE_TAG} ==="
echo ""
echo "Run with docker-compose:"
echo "  cp .env.example .env"
echo "  docker compose up -d"