#!/usr/bin/env bash
# Isolated restore verification for the youwei-chat backups.
#
# Verifies a backup set WITHOUT touching the running chat stack:
#   1. litellm.dump    -> pg_restore into a throwaway postgres:16-alpine
#                         container, then count key tables
#                         (LiteLLM_SpendLogs, LiteLLM_VerificationToken)
#   2. openwebui tar   -> untar to a temp dir, PRAGMA integrity_check on
#                         webui.db, list expected tables, count chats,
#                         list uploads/vector_db entries
#   3. secrets tar     -> list members and check permissions (0600)
#
# Usage (on sg-prod):
#   ops/backup/chat_backup.sh                  # produce a backup first
#   ops/backup/chat_backup_verify.sh [DIR]     # DIR defaults to newest
#                                              # daily chat-backup-*
# Env: YOUWEI_CHAT_BACKUP_ROOT (default /opt/youwei/backups)
#      YOUWEI_VERIFY_PG_IMAGE (default postgres:16-alpine, same family
#      as the stack; the verifier never writes to it beyond the
#      throwaway container)

set -euo pipefail

BACKUP_ROOT="${YOUWEI_CHAT_BACKUP_ROOT:-/opt/youwei/backups}"
VERIFY_PG_IMAGE="${YOUWEI_VERIFY_PG_IMAGE:-postgres:16-alpine}"

if [ $# -ge 1 ]; then
    dir="$1"
else
    dir=$(find "$BACKUP_ROOT/chat/daily" -maxdepth 1 -type d -name 'chat-backup-*' 2>/dev/null | sort -r | head -1)
fi
[ -n "$dir" ] && [ -d "$dir" ] || { echo "no backup dir to verify" >&2; exit 1; }

secrets_tar=$(find "$BACKUP_ROOT/chat-secrets/daily" -maxdepth 1 -type f -name 'chat-secrets-*.tar.gz' 2>/dev/null | sort -r | head -1)

fail=0
pass() { echo "PASS: $*"; }
bad()  { echo "FAIL: $*"; fail=1; }

work=$(mktemp -d /tmp/youwei-chat-verify.XXXXXX)
cleanup() { rm -rf "$work"; docker rm -f youwei-chat-verify-pg >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== verifying $dir =="

# --- 1. LiteLLM PostgreSQL restore ---------------------------------------
docker rm -f youwei-chat-verify-pg >/dev/null 2>&1 || true
docker run -d --name youwei-chat-verify-pg \
    -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=verify \
    "$VERIFY_PG_IMAGE" >/dev/null
for i in $(seq 1 30); do
    docker exec youwei-chat-verify-pg pg_isready -h 127.0.0.1 -U postgres -d verify >/dev/null 2>&1 && break
    sleep 1
done
if docker exec youwei-chat-verify-pg pg_isready -h 127.0.0.1 -U postgres -d verify >/dev/null 2>&1; then
    pass "verify postgres ready"
else
    bad "verify postgres did not become ready"
fi

docker exec youwei-chat-verify-pg createdb -U postgres litellm_verify
docker cp "$dir/litellm.dump" youwei-chat-verify-pg:/restore.dump
docker exec youwei-chat-verify-pg pg_restore -U postgres -d litellm_verify --no-owner /restore.dump \
    && pass "pg_restore completed" \
    || bad "pg_restore failed"

for table in LiteLLM_SpendLogs LiteLLM_VerificationToken; do
    n=$(docker exec youwei-chat-verify-pg \
        psql -U postgres -d litellm_verify -tAc \
        "SELECT count(*) FROM \"$table\"" 2>/dev/null || echo -1)
    if [ "$n" -ge 0 ] 2>/dev/null; then
        pass "$table restored ($n rows)"
    else
        bad "$table missing after restore"
    fi
done
docker rm -f youwei-chat-verify-pg >/dev/null

# --- 2. Open WebUI archive -------------------------------------------------
tar -xzf "$dir/openwebui-data.tar.gz" -C "$work" \
    && pass "openwebui archive extracts" \
    || bad "openwebui archive broken"

python3 - "$work/webui.db" <<'PY' && pass "webui.db integrity_check + tables" || bad "webui.db verification failed"
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
check = db.execute("PRAGMA integrity_check").fetchone()[0]
assert check == "ok", f"integrity_check: {check}"
tables = {r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
required = {"user", "chat", "model", "auth"}
missing = required - tables
assert not missing, f"missing tables: {missing}"
n = db.execute("SELECT count(*) FROM chat").fetchone()[0]
print(f"  chat rows: {n}; tables: {len(tables)}")
db.close()
PY

[ -d "$work/uploads" ] && pass "uploads/ present in archive" || bad "uploads/ missing"
[ -d "$work/vector_db" ] && pass "vector_db/ present in archive" || bad "vector_db/ missing"

[ -s "$dir/compose.json" ] && pass "compose.json archived" || bad "compose.json missing"

# Assistant archive required when the saved deployment contains the service.
assistant_enabled=$(python3 - "$dir/compose.json" <<'PY_CHECK'
import json, sys
print(int('hermes-assistant' in json.load(open(sys.argv[1]))['services']))
PY_CHECK
)
if [ "$assistant_enabled" = 1 ] || [ -e "$dir/hermes-data.tar.gz" ]; then
    image_args=()
    if [ -n "${YOUWEI_VERIFY_ASSISTANT_IMAGE:-}" ]; then
        image_args=(--image "$YOUWEI_VERIFY_ASSISTANT_IMAGE")
    fi
    python3 "$(dirname "$0")/assistant_image.py" verify \
        --archive "$dir/hermes-data.tar.gz" --metadata "$dir/hermes-image.json" \
        "${image_args[@]}" \
        && pass "Hermes image-isolated restore hashes/SQLite/knowledge" || bad "Hermes restore failed"
fi

# --- 3. secrets archive -----------------------------------------------------
if [ -n "$secrets_tar" ]; then
    members=$(tar -tzf "$secrets_tar")
    echo "$members" | grep -qx "secrets.env" \
        && pass "secrets archive contains secrets.env" \
        || bad "secrets.env missing from secrets archive"
    echo "$members" | grep -qx "config.yaml" \
        && pass "secrets archive contains config.yaml" \
        || bad "config.yaml missing from secrets archive"
    perms=$(stat -c '%a' "$secrets_tar")
    [ "$perms" = "600" ] \
        && pass "secrets archive is 0600" \
        || bad "secrets archive perms are $perms, expected 600"
else
    bad "no secrets archive found"
fi

# ---------------------------------------------------------------------------
if [ "$fail" -eq 0 ]; then
    echo "== verify result: ALL PASS =="
else
    echo "== verify result: FAILURES PRESENT =="
fi
exit "$fail"
