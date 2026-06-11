#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CODEX_VERSION="${CODEX_VERSION:-0.110.0}"
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-2.1.162}"
CODEX_IMAGE="${CODEX_IMAGE:-apd-codex-runner:latest}"
CLAUDE_IMAGE="${CLAUDE_IMAGE:-apd-claude-runner:latest}"

if [[ "${1:-all}" == "codex" || "${1:-all}" == "all" ]]; then
  docker build \
    --build-arg CODEX_VERSION="$CODEX_VERSION" \
    -t "$CODEX_IMAGE" \
    -f docker/codex-runner/Dockerfile \
    docker/codex-runner
fi

if [[ "${1:-all}" == "claude" || "${1:-all}" == "all" ]]; then
  docker build \
    --build-arg CLAUDE_CODE_VERSION="$CLAUDE_CODE_VERSION" \
    -t "$CLAUDE_IMAGE" \
    -f docker/claude-runner/Dockerfile \
    docker/claude-runner
fi
