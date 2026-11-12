#!/usr/bin/env bash
set -euo pipefail
ALPHA_ROOT=/opt/hermes-alpha-lab
export PATH="$ALPHA_ROOT/runtime/node-v26.0.0-linux-x64/bin:$ALPHA_ROOT/runtime/npm/bin:$ALPHA_ROOT/runtime/uv-x86_64-unknown-linux-gnu:$PATH"
cd "$ALPHA_ROOT/releases/20261107T0800Z"
set -a
source "$ALPHA_ROOT/private/database-owner.env"
set +a
if ! docker exec alpha-lab-postgres psql -U alpha_deploy -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='alpha_acceptance_test'" | grep -qx 1; then
  docker exec alpha-lab-postgres createdb -U alpha_deploy alpha_acceptance_test
fi
export TEST_DATABASE_URL="${DATABASE_URL%/*}/alpha_acceptance_test"
unset DATABASE_URL
export HERMES_IMAGE="$(docker image inspect alpha-lab-hermes:20261107 --format '{{.Id}}')"
export ALPHA_TEST_BIND_HOST=172.17.0.1
export ALPHA_MODEL_FILE="$ALPHA_ROOT/private/provider/claude-code-model.json"
export ALPHA_MARKET_FILE="$ALPHA_ROOT/private/provider/alpaca.json"
umask 077
pnpm test
uv run pytest -q tests/hermes/container_integration.py tests/hermes/container_system_integration.py tests/hermes/live_research_integration.py --basetemp "$ALPHA_ROOT/private/acceptance-20261107" --tb=short
