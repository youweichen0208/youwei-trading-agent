#!/usr/bin/env bash
# Deploy the youwei chat stack (LiteLLM gateway + Open WebUI) on sg-prod.
#
# Layout on the host:
#   /opt/youwei/chat/secrets.env    0600  VOLC_API_KEY / LITELLM_MASTER_KEY /
#                                   LITELLM_PG_PASSWORD / YOUWEI_CHAT_KEY
#   /opt/youwei/chat/compose.json   from infra/compose/chat.json
#   /opt/youwei/chat/config.yaml    rendered from infra/chat/litellm-config
#                                   .yaml.template (contains the key; 0600)
#
# Phases (idempotent):
#   secrets  ensure the secrets file exists (generate missing values)
#   up       render config + docker compose up -d postgres litellm
#   key      create/look up the chat virtual key and store it in secrets.env
#   webui    docker compose up -d openwebui (needs the chat key)
#   status   docker compose ps
#
# Usage: ops/deploy_chat.sh <secrets|up|key|webui|status>   (run on sg-prod)

set -euo pipefail

BASE="${YOUWEI_CHAT_BASE:-/opt/youwei/chat}"
REPO="${YOUWEI_DEPLOY_REPO:-/root/youwei-trading-agent}"
SECRETS="$BASE/secrets.env"
COMPOSE="$BASE/compose.json"
CONFIG="$BASE/config.yaml"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 1; }; }
need docker

dc() {
    docker compose -p youwei-chat -f "$COMPOSE" --env-file "$SECRETS" "$@"
}

gen() { openssl rand -hex 32; }

phase_secrets() {
    mkdir -p "$BASE" && chmod 700 "$BASE"
    if [ ! -f "$SECRETS" ]; then
        echo "ERROR: $SECRETS missing; create it with VOLC_API_KEY and LITELLM_MASTER_KEY first" >&2
        exit 1
    fi
    chmod 600 "$SECRETS"
    # generate the litellm postgres password if absent
    if ! grep -q '^LITELLM_PG_PASSWORD=' "$SECRETS"; then
        printf 'LITELLM_PG_PASSWORD=%s\n' "$(gen)" >> "$SECRETS"
        echo "generated LITELLM_PG_PASSWORD"
    fi
    echo "secrets ok ($(grep -c '=' "$SECRETS") entries)"
}

render_config() {
    # shellcheck disable=SC1090
    set -a; . "$SECRETS"; set +a
    sed "s|__VOLC_KEY__|$VOLC_API_KEY|g" \
        "$REPO/infra/chat/litellm-config.yaml.template" > "$CONFIG"
    chmod 600 "$CONFIG"
}

phase_up() {
    phase_secrets
    cp "$REPO/infra/compose/chat.json" "$COMPOSE"
    render_config
    dc up -d postgres
    dc up -d litellm
    echo "waiting for litellm health..."
    for _ in $(seq 1 60); do
        if curl -sf -m 5 http://127.0.0.1:4000/health/liveliness >/dev/null 2>&1; then
            echo "litellm live"; return 0
        fi
        sleep 2
    done
    echo "litellm did not become live; check: docker logs youwei-chat-litellm-1" >&2
    exit 1
}

phase_key() {
    # The chat key is stored in secrets.env; if present, trust it (the
    # litellm /key/list API returns key ids, not aliases, so re-lookup
    # would need extra calls for nothing).
    if grep -q '^YOUWEI_CHAT_KEY=sk-' "$SECRETS"; then
        echo "chat key present"
        return 0
    fi
    # shellcheck disable=SC1090
    set -a; . "$SECRETS"; set +a
    key=$(curl -sf -m 10 -X POST \
        -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
        -H "Content-Type: application/json" \
        -d '{"key_alias": "chat",
             "models": ["glm-5.3", "glm-5.3-flash", "deepseek-v4-flash",
                        "deepseek-v4-pro", "qwen3.8-flash"],
             "max_parallel_requests": 8}' \
        "http://127.0.0.1:4000/key/generate" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
    if ! grep -q '^YOUWEI_CHAT_KEY=' "$SECRETS"; then
        printf 'YOUWEI_CHAT_KEY=%s\n' "$key" >> "$SECRETS"
    else
        sed -i "s|^YOUWEI_CHAT_KEY=.*|YOUWEI_CHAT_KEY=$key|" "$SECRETS"
    fi
    chmod 600 "$SECRETS"
    echo "chat virtual key created (no budget cap per owner decision 2026-10-02)"
}

phase_webui() {
    phase_key >/dev/null
    dc up -d openwebui
    for _ in $(seq 1 60); do
        if curl -sf -m 5 -o /dev/null http://127.0.0.1:8080/health; then
            echo "openwebui healthy"; return 0
        fi
        sleep 2
    done
    echo "openwebui did not become healthy; check: docker logs youwei-chat-openwebui-1" >&2
    exit 1
}

case "${1:-}" in
    secrets) phase_secrets ;;
    up) phase_up ;;
    key) phase_key ;;
    webui) phase_webui ;;
    status) dc ps ;;
    *) echo "usage: $0 <secrets|up|key|webui|status>" >&2; exit 2 ;;
esac
