#!/usr/bin/env bash
set -euo pipefail

REGISTRY="registry.cn-hangzhou.aliyuncs.com/jiefengnewsv2"
VERSION="${1:-v2.1.0-mymain}"
VT_BRANCH="mymain"

echo "=== ECS AMD64 Build: opencode-serve:${VERSION} ==="

# OpencodeAgent lives inside the Vibe-Trading repo (mymain branch); a single
# clone provides both the harness (OpencodeAgent/) and the VT source to vendor.
if [ ! -d "Vibe-Trading" ]; then
    git clone -b "$VT_BRANCH" https://github.com/shadowinlife/Vibe-Trading.git
fi

cd Vibe-Trading && git fetch origin "$VT_BRANCH" && git checkout "$VT_BRANCH" && git pull origin "$VT_BRANCH"

echo "=== Step 1: Build base image ==="
cd OpencodeAgent
./build.sh --base --tag latest --push

echo "=== Step 2: Build app image ==="
./build.sh --app --tag "$VERSION" --push

echo "=== Done: ${REGISTRY}/opencode-serve:${VERSION} ==="