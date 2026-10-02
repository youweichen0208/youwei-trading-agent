#!/usr/bin/env bash
# Daily backup for the youwei-chat stack (LiteLLM PG + Open WebUI).
#
# Owner-confirmed policy (2026-10-02):
#   - once per day, keep 7 daily copies + 4 weekly copies
#   - LiteLLM PG dump + Open WebUI SQLite (consistent copy), uploads,
#     vector_db (cache/ excluded) and the deployed compose.json
#   - secrets (secrets.env + rendered config.yaml, which contains the
#     VOLC key) go to a SEPARATE 0700/0600 tree, never into the regular
#     backup directory
#   - failures surface in the existing monitoring: this script writes a
#     heartbeat (backup-state/) that ops_status_poll.sh turns into
#     chat_backup_failed / chat_backup_stale alerts; failures are also
#     appended to ALERT_LOG immediately
#   - off-site backup stays deferred; copies live on the same host as
#     the stack (documented risk)
#
# Layout on the host (sg-prod):
#   /opt/youwei/backups/chat/daily/chat-backup-<ts>/
#       litellm.dump             pg_dump -Fc of the `litellm` database
#       openwebui-data.tar.gz    webui.db (consistent) + uploads/ + vector_db/
#       compose.json             deployed compose config (no secrets)
#   /opt/youwei/backups/chat/weekly/          Sunday copies, keep 4
#   /opt/youwei/backups/chat-secrets/         0700; daily/ + weekly/
#       chat-secrets-<ts>.tar.gz              0600; secrets.env + config.yaml
#   /opt/youwei/chat/backup-state/            last-success / last-status
#
# Usage: ops/backup/chat_backup.sh            (run on sg-prod, root cron)
#   env overrides: YOUWEI_CHAT_BASE / YOUWEI_CHAT_BACKUP_ROOT /
#                  YOUWEI_ALERT_LOG

set -euo pipefail

BASE="${YOUWEI_CHAT_BASE:-/opt/youwei/chat}"
BACKUP_ROOT="${YOUWEI_CHAT_BACKUP_ROOT:-/opt/youwei/backups}"
STATE_DIR="$BASE/backup-state"
ALERT_LOG="${YOUWEI_ALERT_LOG:-/var/log/youwei-alerts.log}"

PG_CONTAINER="youwei-chat-postgres-1"
OWUI_CONTAINER="youwei-chat-openwebui-1"

DAILY_KEEP=7
WEEKLY_KEEP=4
WEEKLY_DAY=7   # Sunday (date +%u)

ts=$(date -u +%Y%m%d-%H%M%S)
now_iso=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
stamp="$now_iso"
daily_dir="$BACKUP_ROOT/chat/daily/chat-backup-$ts"
secrets_dir="$BACKUP_ROOT/chat-secrets"

mkdir -p "$daily_dir" "$BACKUP_ROOT/chat/weekly" \
         "$secrets_dir/daily" "$secrets_dir/weekly" "$STATE_DIR"
chmod 700 "$BACKUP_ROOT/chat-secrets"

note() { echo "[$(date -u +%FT%TZ)] $*"; }

on_failure() {
    local rc=$?
    local msg="chat backup FAILED: ${1:-unknown error}"
    printf 'failed: %s\n' "${1:-unknown}" > "$STATE_DIR/last-status"
    echo "[$(date -u +%FT%TZ)] youwei chat backup @ $now_iso: ALERT: $msg" >> "$ALERT_LOG"
    note "$msg"
    exit "$rc"
}
trap 'on_failure "${BASH_COMMAND:-unknown}"' ERR

note "chat backup start"

# --- LiteLLM PostgreSQL dump -------------------------------------------
docker exec "$PG_CONTAINER" pg_dump -U litellm -Fc -d litellm \
    > "$daily_dir/litellm.dump"
[ -s "$daily_dir/litellm.dump" ] \
    || { echo "litellm dump is empty" >&2; false; }
note "litellm pg_dump done ($(du -h "$daily_dir/litellm.dump" | cut -f1))"

# --- Open WebUI: consistent SQLite copy --------------------------------
# The image has no sqlite3 CLI; use the Python sqlite3 backup API on a
# read-only connection (consistent snapshot even while Open WebUI is
# writing).
docker exec -i "$OWUI_CONTAINER" python3 - <<'PY'
import sqlite3
src = sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/webui-backup.db")
src.backup(dst)
dst.close()
src.close()
PY
docker cp "$OWUI_CONTAINER:/tmp/webui-backup.db" "$daily_dir/webui.db"
docker exec "$OWUI_CONTAINER" rm -f /tmp/webui-backup.db
[ -s "$daily_dir/webui.db" ] \
    || { echo "webui.db copy is empty" >&2; false; }

# uploads/ and vector_db/ must exist; a missing directory is a failure,
# not something to skip silently (silent skips are how backups rot).
docker cp "$OWUI_CONTAINER:/app/backend/data/uploads" "$daily_dir/uploads"
docker cp "$OWUI_CONTAINER:/app/backend/data/vector_db" "$daily_dir/vector_db"
tar -czf "$daily_dir/openwebui-data.tar.gz" \
    -C "$daily_dir" webui.db uploads vector_db
rm -rf "$daily_dir/webui.db" "$daily_dir/uploads" "$daily_dir/vector_db"
note "openwebui data archived ($(du -h "$daily_dir/openwebui-data.tar.gz" | cut -f1))"

# --- deployed compose config (no secrets) ------------------------------
cp "$BASE/compose.json" "$daily_dir/compose.json"

# sanity: every artifact must be a non-empty regular file
for f in litellm.dump openwebui-data.tar.gz compose.json; do
    [ -s "$daily_dir/$f" ] || { echo "missing artifact: $f" >&2; false; }
done

# --- secrets: separate 0700/0600 tree -----------------------------------
tar -czf "$secrets_dir/daily/chat-secrets-$ts.tar.gz" \
    -C "$BASE" secrets.env config.yaml
chmod 600 "$secrets_dir/daily/chat-secrets-$ts.tar.gz"
note "secrets archived (separate tree)"

# --- mark success before rotation (data is safe even if pruning fails) --
printf '%s\n' "$stamp" > "$STATE_DIR/last-success"
printf 'ok\n' > "$STATE_DIR/last-status"
note "backup set complete: $daily_dir"

# --- retention -----------------------------------------------------------
# find+sort instead of ls globs: an empty directory must not fail the
# pipeline under `set -o pipefail`.
# daily: keep the newest DAILY_KEEP sets
find "$BACKUP_ROOT/chat/daily" -maxdepth 1 -type d -name 'chat-backup-*' \
    | sort -r | tail -n +$((DAILY_KEEP + 1)) | xargs -r rm -rf --
find "$secrets_dir/daily" -maxdepth 1 -type f -name 'chat-secrets-*.tar.gz' \
    | sort -r | tail -n +$((DAILY_KEEP + 1)) | xargs -r rm -f --

# weekly: on Sunday, copy today's set to weekly/, keep the newest WEEKLY_KEEP
if [ "$(date +%u)" -eq "$WEEKLY_DAY" ] \
   && [ ! -e "$BACKUP_ROOT/chat/weekly/chat-backup-$ts" ]; then
    cp -a "$daily_dir" "$BACKUP_ROOT/chat/weekly/chat-backup-$ts"
    cp -a "$secrets_dir/daily/chat-secrets-$ts.tar.gz" \
        "$secrets_dir/weekly/chat-secrets-$ts.tar.gz"
    note "weekly copy created"
fi
find "$BACKUP_ROOT/chat/weekly" -maxdepth 1 -type d -name 'chat-backup-*' \
    | sort -r | tail -n +$((WEEKLY_KEEP + 1)) | xargs -r rm -rf --
find "$secrets_dir/weekly" -maxdepth 1 -type f -name 'chat-secrets-*.tar.gz' \
    | sort -r | tail -n +$((WEEKLY_KEEP + 1)) | xargs -r rm -f --

note "chat backup done (daily keep $DAILY_KEEP, weekly keep $WEEKLY_KEEP)"
