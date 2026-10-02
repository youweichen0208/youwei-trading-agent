#!/usr/bin/env bash
# S07 gateway fail-closed drill: what does LiteLLM do when its Postgres
# (the spend/key counter database) is unreachable?
#
# Runs ON sg-prod. Uses a THROWAWAY parallel stack (same pinned digests and
# config shape as the production chat gateway) so the owner's chat stack is
# never touched:
#
#   gwtest-postgres  postgres:16-alpine@sha256:721873c3...   (temp data)
#   gwtest-litellm   ghcr.io/berriai/litellm@sha256:f8043... (temp config,
#                    dummy Volcengine key, published 127.0.0.1:4400)
#
# Procedure:
#   1. start temp PG + temp LiteLLM; wait healthy; create a virtual key
#   2. CONTROL request with the virtual key -> expect an UPSTREAM 401 (the
#      dummy Volc key proves the request passed gateway admission with the
#      DB up and was forwarded upstream)
#   3. stop the temp PG -> repeat the request:
#        DB/connection error  => FAIL-CLOSED (admission rejected)
#        upstream 401 again   => FAIL-OPEN  (admission passed without the DB)
#   4. master-key probe while the DB is down (informational: config-based
#      auth may not need the DB at all)
#   5. teardown everything
#
# The ephemeral master key and virtual key are generated in-process and never
# printed. Usage: ops/gw_failclosed_drill.sh [repo-checkout-dir]
# (default ~/youwei-trading-agent; needs infra/chat/litellm-config.yaml.template).

set -euo pipefail

REPO_DIR="${1:-$HOME/youwei-trading-agent}"
TEMPLATE="$REPO_DIR/infra/chat/litellm-config.yaml.template"
WORKDIR=/root/youwei-gwtest
PG_NAME=gwtest-postgres
LITELLM_NAME=gwtest-litellm
NET_NAME=gwtest-net
PG_IMAGE="postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
LITELLM_IMAGE="ghcr.io/berriai/litellm@sha256:f8043697479513b9ae6f3abac8e62d61b908cfd31541cc3c3c994e16a28d784e"
DUMMY_VOLC_KEY="gwtest-dummy-definitely-not-a-real-key"

[ -f "$TEMPLATE" ] || { echo "FATAL: template not found: $TEMPLATE" >&2; exit 2; }
command -v docker >/dev/null || { echo "FATAL: docker not found" >&2; exit 2; }

cleanup() {
    docker rm -f "$LITELLM_NAME" "$PG_NAME" >/dev/null 2>&1 || true
    docker network rm "$NET_NAME" >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

request() { # $1 = key, $2 = label
    local key="$1" label="$2" body status
    body=$(curl -s -m 30 -X POST -H "Authorization: Bearer $key" \
        -H "Content-Type: application/json" \
        -d '{"model": "glm-5.3", "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 8}' \
        -o "$WORKDIR/last_body.json" -w '%{http_code}' \
        "http://127.0.0.1:4400/v1/chat/completions" || echo "curl-error")
    status="$body"
    echo "$label: status=$status body=$(head -c 220 "$WORKDIR/last_body.json" 2>/dev/null || echo '(no body)')"
    echo "$status"
}

echo "== fail-closed drill: temp stack (production chat untouched) =="
mkdir -p "$WORKDIR" && chmod 700 "$WORKDIR"

MASTER_KEY=$(openssl rand -hex 24)
PG_PASSWORD=$(openssl rand -hex 16)
sed "s/__VOLC_KEY__/$DUMMY_VOLC_KEY/" "$TEMPLATE" > "$WORKDIR/config.yaml"
chmod 600 "$WORKDIR/config.yaml"

docker network create "$NET_NAME" >/dev/null
docker run -d --name "$PG_NAME" --network "$NET_NAME" \
    -e POSTGRES_USER=litellm -e POSTGRES_DB=litellm \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" \
    --cpus 0.5 --memory 512m "$PG_IMAGE" >/dev/null

for i in $(seq 1 30); do
    docker exec "$PG_NAME" pg_isready -U litellm -d litellm >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$PG_NAME" pg_isready -U litellm -d litellm >/dev/null

docker run -d --name "$LITELLM_NAME" --network "$NET_NAME" \
    --publish 127.0.0.1:4400:4000 \
    -e LITELLM_MASTER_KEY="$MASTER_KEY" \
    -e VOLC_API_KEY="$DUMMY_VOLC_KEY" \
    -e DATABASE_URL="postgresql://litellm:$PG_PASSWORD@$PG_NAME:5432/litellm" \
    -e STORE_MODEL_IN_DB=True \
    -v "$WORKDIR/config.yaml:/app/config.yaml:ro" \
    --cpus 1.0 --memory 1g \
    "$LITELLM_IMAGE" --config /app/config.yaml --port 4000 >/dev/null

for i in $(seq 1 60); do
    curl -sf -m 3 "http://127.0.0.1:4400/health/liveliness" >/dev/null 2>&1 && break
    sleep 2
done
curl -sf -m 3 "http://127.0.0.1:4400/health/liveliness" >/dev/null \
    || { echo "FATAL: temp litellm did not become healthy" >&2; exit 1; }
echo "temp stack healthy"

GWTEST_KEY=$(curl -sf -m 15 -X POST \
    -H "Authorization: Bearer $MASTER_KEY" -H "Content-Type: application/json" \
    -d '{"key_alias": "gwtest", "models": ["glm-5.3"]}' \
    "http://127.0.0.1:4400/key/generate" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
[ -n "$GWTEST_KEY" ] || { echo "FATAL: virtual key creation failed" >&2; exit 1; }
echo "virtual key created (value not printed)"

echo "-- control (DB up): expect UPSTREAM 401 (admission passed, dummy volc key rejected upstream)"
CONTROL_STATUS=$(request "$GWTEST_KEY" control)
# The control's upstream 401 puts the deployment in a short cooldown; let it
# expire so the DB-down probes are not confounded by cooldown 429s.
echo "-- waiting 15s for the upstream-401 deployment cooldown to expire"
sleep 15

echo "-- stopping temp postgres"
docker stop "$PG_NAME" >/dev/null
sleep 3

echo "-- test (DB down): DB error => fail-closed / upstream 401 => fail-open"
TEST_STATUS=$(request "$GWTEST_KEY" failclosed-probe-1)
sleep 10
TEST2_STATUS=$(request "$GWTEST_KEY" failclosed-probe-2)

echo "-- master-key probe (DB down, informational)"
MASTER_STATUS=$(request "$MASTER_KEY" master-key-probe)

echo "== verdict =="
echo "control=$CONTROL_STATUS test1=$TEST_STATUS test2=$TEST2_STATUS master=$MASTER_STATUS"
case "$TEST_STATUS$TEST2_STATUS" in
    *401*401*)
        echo "VERDICT: FAIL-OPEN (virtual-key requests pass gateway admission while the counter database is unreachable; observed for a previously-validated key — LiteLLM caches key info)" ;;
    401* | *401)
        echo "VERDICT: MIXED (see probes; one passed, one rejected)" ;;
    *)
        echo "VERDICT: FAIL-CLOSED (virtual-key requests are rejected while the counter database is unreachable)" ;;
esac
