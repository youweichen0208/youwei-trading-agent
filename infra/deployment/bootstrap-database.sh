#!/usr/bin/env bash
set -euo pipefail
ALPHA_ROOT=/opt/hermes-alpha-lab
export PATH="$ALPHA_ROOT/runtime/node-v26.0.0-linux-x64/bin:$ALPHA_ROOT/runtime/npm/bin:$PATH"
cd "$ALPHA_ROOT/releases/20261107T0800Z"
umask 077
python3 - <<'PY'
from pathlib import Path
import secrets
root = Path('/opt/hermes-alpha-lab/private')
compose = root / 'postgres.env'
owner = root / 'database-owner.env'
if not compose.exists() and not owner.exists():
    password = secrets.token_hex(32)
    compose.write_text('ALPHA_POSTGRES_IMAGE=postgres@sha256:ff87138949ad9a46f32ff23bd6852b52ce024a13dffe9b4087be98292cdd3b4f\nALPHA_DATABASE_PASSWORD=' + password + '\n')
    owner.write_text('DATABASE_URL=postgresql://alpha_deploy:' + password + '@127.0.0.1:55439/alpha_production\n')
elif not compose.exists() or not owner.exists():
    raise SystemExit('Incomplete database configuration: reconcile before proceeding')
PY
docker compose --env-file "$ALPHA_ROOT/private/postgres.env" -f infra/deployment/production.compose.yaml up -d --wait
set -a
source "$ALPHA_ROOT/private/database-owner.env"
set +a
pnpm db:migrate
if [[ ! -d "$ALPHA_ROOT/private/database-services" ]]; then
  pnpm db:access alpha_live "$ALPHA_ROOT/private/database-services"
fi
python3 - <<'PY'
import json
from pathlib import Path
state = json.loads(Path('/opt/hermes-alpha-lab/private/database-services/state.json').read_text())
assert state['status'] == 'provisioned', 'Database roles require reconciliation'
print('database_bootstrap_passed')
PY
