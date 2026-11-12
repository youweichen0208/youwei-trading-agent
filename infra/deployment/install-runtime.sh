#!/usr/bin/env bash
set -euo pipefail
# Project-local binaries only; no system Node/Python replacement.
ALPHA_ROOT=/opt/hermes-alpha-lab
ALPHA_RUNTIME="$ALPHA_ROOT/runtime"
test "$(id -u)" = 0
test "$(uname -m)" = x86_64
install -d -m 755 "$ALPHA_RUNTIME/downloads"
cd "$ALPHA_RUNTIME/downloads"
if [ ! -x "$ALPHA_RUNTIME/node-v26.0.0-linux-x64/bin/node" ]; then
  curl --fail --location --proto '=https' -o node-v26.0.0-linux-x64.tar.xz https://nodejs.org/dist/v26.0.0/node-v26.0.0-linux-x64.tar.xz
  curl --fail --location --proto '=https' -o node-SHASUMS256.txt https://nodejs.org/dist/v26.0.0/SHASUMS256.txt
  sha256sum --check --ignore-missing node-SHASUMS256.txt
  tar -xJf node-v26.0.0-linux-x64.tar.xz -C "$ALPHA_RUNTIME"
fi
export PATH="$ALPHA_RUNTIME/node-v26.0.0-linux-x64/bin:$PATH"
node --version || {
  printf '%s\n' 'Node could not start. On this Ubuntu deployment, install the libatomic1 prerequisite.' >&2
  exit 1
}
if [ ! -x "$ALPHA_RUNTIME/npm/bin/pnpm" ]; then
  npm install --prefix "$ALPHA_RUNTIME/npm" --global pnpm@9.15.4 --cache "$ALPHA_RUNTIME/npm-cache"
fi
if [ ! -x "$ALPHA_RUNTIME/uv-x86_64-unknown-linux-gnu/uv" ]; then
  curl --fail --location --proto '=https' -o uv-x86_64-unknown-linux-gnu.tar.gz https://github.com/astral-sh/uv/releases/download/0.11.16/uv-x86_64-unknown-linux-gnu.tar.gz
  curl --fail --location --proto '=https' -o uv.sha256 https://github.com/astral-sh/uv/releases/download/0.11.16/uv-x86_64-unknown-linux-gnu.tar.gz.sha256
  ALPHA_UV_SHA=$(awk '{print $1}' uv.sha256)
  [[ "$ALPHA_UV_SHA" =~ ^[a-f0-9]{64}$ ]]
  printf '%s  %s\n' "$ALPHA_UV_SHA" uv-x86_64-unknown-linux-gnu.tar.gz | sha256sum --check -
  tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz -C "$ALPHA_RUNTIME"
fi
"$ALPHA_RUNTIME/npm/bin/pnpm" --version
"$ALPHA_RUNTIME/uv-x86_64-unknown-linux-gnu/uv" --version
