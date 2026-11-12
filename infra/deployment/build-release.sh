#!/usr/bin/env bash
set -euo pipefail
ALPHA_ROOT=/opt/hermes-alpha-lab
export PATH="$ALPHA_ROOT/runtime/node-v26.0.0-linux-x64/bin:$ALPHA_ROOT/runtime/npm/bin:$ALPHA_ROOT/runtime/uv-x86_64-unknown-linux-gnu:$PATH"
cd "${1:?absolute release directory required}"
case "$PWD" in "$ALPHA_ROOT"/releases/*) ;; *) exit 2 ;; esac
pnpm install --frozen-lockfile
uv sync --frozen --python /usr/bin/python3.12
pnpm typecheck
pnpm build
printf 'release_build_passed\n'
