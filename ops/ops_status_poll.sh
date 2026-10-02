#!/usr/bin/env bash
# Poll the Core ops status endpoint and deliver alerts on change (S09c).
#
# The API already evaluates thresholds (queue backlog, unpublished
# events, expired leases, WAL archive staleness, ledger health) in
# /v1/ops/status; this script adds the missing delivery leg:
#
#   - every run records a heartbeat (state dir) so freshness is
#     checkable from the host even without any channel
#   - when the alert SET changes (new alerts, or resolution), the
#     transition is delivered once via the configured channel:
#       * YOUWEI_ALERT_WEBHOOK_URL set  -> POST a JSON payload in the
#         format selected by YOUWEI_ALERT_WEBHOOK_FORMAT
#         (generic | slack | discord | feishu | wecom | telegram;
#         telegram also needs YOUWEI_ALERT_TELEGRAM_CHAT_ID)
#       * otherwise                    -> append to ALERT_LOG
#   - a steady alert state does not re-notify (spam control); one "ok"
#     heartbeat line is appended to ALERT_LOG per day for the record
#
# The poll fails closed: if the API is unreachable, that is itself an
# alert ("api_unreachable") and is delivered like any other.
#
# It also watches the chat stack backup heartbeat written by
# ops/backup/chat_backup.sh (daily cron):
#   - no heartbeat yet            -> chat_backup_missing
#   - last success older than 26h -> chat_backup_stale (cron dead / silent fail)
#   - last status not "ok"        -> chat_backup_failed (last run failed)
# These join the same alert-set delivery (webhook + log), so a failed
# backup notifies exactly like an ops-status alert and resolves the
# same way.
#
# Install (sg-prod, root cron, every 5 minutes):
#   */5 * * * * /opt/youwei/production/ops_status_poll.sh \
#             >> /var/log/youwei-alert-poll.log 2>&1
# Chat stack daily backup (heartbeat watched above):
#   40 3 * * * /opt/youwei/chat/chat_backup.sh \
#             >> /var/log/youwei-chat-backup.log 2>&1
# Channel configuration (optional, 0600):
#   /opt/youwei/<env>/alert-channel.env:  YOUWEI_ALERT_WEBHOOK_URL=...
# Repo source of truth: ops/ops_status_poll.sh; deployed copy lives in
# the environment directory so cron does not depend on the repo checkout.

set -euo pipefail

BASE="${YOUWEI_DEPLOY_BASE:-/opt/youwei}"
ENV_NAME="${YOUWEI_DEPLOY_ENV:-production}"
ENV_DIR="$BASE/$ENV_NAME"
SECRETS="$BASE/secrets/$ENV_NAME.env"
CHANNEL_CONF="$ENV_DIR/alert-channel.env"
STATE_DIR="$ENV_DIR/alert-state"
ALERT_LOG="${YOUWEI_ALERT_LOG:-/var/log/youwei-alerts.log}"
API="${YOUWEI_OPS_API:-http://127.0.0.1:8000}"

mkdir -p "$STATE_DIR"
touch "$STATE_DIR/last-alerts"

# shellcheck disable=SC1090
[ -f "$SECRETS" ] && . "$SECRETS"
# shellcheck disable=SC1090
[ -f "$CHANNEL_CONF" ] && . "$CHANNEL_CONF"

now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
today=$(date -u +"%Y-%m-%d")

status_json=$(curl -sf -m 10 \
    -H "Authorization: Bearer ${YOUWEI_ADMIN_API_KEY:?admin key missing in $SECRETS}" \
    "$API/v1/ops/status" 2>/dev/null) \
    || status_json='{"status":"unreachable","alerts":["api_unreachable"]}'

# Normalize the alert list (sorted, newline-separated; empty when ok).
alerts=$(printf '%s' "$status_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {"status": "parse_error", "alerts": ["ops_status_parse_error"]}
alerts = d.get("alerts") or []
if d.get("status") not in ("ok", None) and not alerts:
    alerts = ["status_" + str(d.get("status"))]
print("\n".join(sorted(alerts)))
')
# Chat stack backup health (heartbeat from chat_backup.sh).
chat_state="$BASE/chat/backup-state"
chat_alert=""
if [ ! -f "$chat_state/last-success" ]; then
    chat_alert="chat_backup_missing"
else
    ok_epoch=$(date -u -d "$(cat "$chat_state/last-success")" +%s 2>/dev/null || echo 0)
    age=$(( $(date -u +%s) - ok_epoch ))
    if [ "$age" -gt $((26 * 3600)) ]; then
        chat_alert="chat_backup_stale"
    elif [ "$(head -1 "$chat_state/last-status" 2>/dev/null || true)" != "ok" ]; then
        chat_alert="chat_backup_failed"
    fi
fi
if [ -n "$chat_alert" ]; then
    alerts="${alerts}${alerts:+$'\n'}$chat_alert"
fi

last=$(cat "$STATE_DIR/last-alerts")

if [ "$alerts" != "$last" ]; then
    if [ -z "$alerts" ]; then
        message="youwei $ENV_NAME @ $now: alerts RESOLVED"
    else
        message="youwei $ENV_NAME @ $now: ALERTS: $(echo "$alerts" | tr '\n' ',')"
    fi
    if [ -n "${YOUWEI_ALERT_WEBHOOK_URL:-}" ]; then
        format="${YOUWEI_ALERT_WEBHOOK_FORMAT:-generic}"
        case "$format" in
            slack|generic)
                payload=$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$message") ;;
            discord)
                payload=$(python3 -c 'import json, sys; print(json.dumps({"content": sys.argv[1]}))' "$message") ;;
            feishu)
                payload=$(python3 -c 'import json, sys; print(json.dumps({"msg_type": "text", "content": {"text": sys.argv[1]}}))' "$message") ;;
            wecom)
                payload=$(python3 -c 'import json, sys; print(json.dumps({"msgtype": "text", "text": {"content": sys.argv[1]}}))' "$message") ;;
            telegram)
                chat="${YOUWEI_ALERT_TELEGRAM_CHAT_ID:?telegram format needs YOUWEI_ALERT_TELEGRAM_CHAT_ID}"
                payload=$(python3 -c 'import json, sys; print(json.dumps({"chat_id": sys.argv[1], "text": sys.argv[2]}))' "$chat" "$message") ;;
            *)
                echo "$now: unknown YOUWEI_ALERT_WEBHOOK_FORMAT '$format' (logged only)" >&2
                payload="" ;;
        esac
        if [ -n "$payload" ]; then
            curl -sf -m 10 -H 'Content-Type: application/json' \
                -d "$payload" "$YOUWEI_ALERT_WEBHOOK_URL" \
                || echo "$now: webhook delivery failed (logged only)" >&2
        fi
    fi
    echo "[$now] $message" >> "$ALERT_LOG"
    printf '%s\n' "$alerts" > "$STATE_DIR/last-alerts"
fi

# Heartbeat: last-poll freshness marker + one ok line per day.
api_status=$(printf '%s' "$status_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","?"))')
printf '%s %s\n' "$now" "$api_status" > "$STATE_DIR/last-poll"
if [ -z "$alerts" ] && [ "$today" != "$(cat "$STATE_DIR/last-ok-day" 2>/dev/null || true)" ]; then
    echo "[$now] ok (daily heartbeat)" >> "$ALERT_LOG"
    printf '%s\n' "$today" > "$STATE_DIR/last-ok-day"
fi
