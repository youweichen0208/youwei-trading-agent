#!/usr/bin/env bash
# S12 rollout rehearsal — full-stack isolated acceptance of the staged images.
#
# Owner decision 2026-10-03 (option 2b): the production S12 rollout waits for
# the batch-1 seal; until then the fixes, builds and ISOLATED acceptance run
# without idling. This script stands up a throwaway stack on sg-prod —
# postgres + core-api/core-worker (r6) + sandbox-runner (s12e), research
# containers on the shared youwei-research network reaching the REAL LiteLLM
# gateway with the production research virtual key and model glm-5.3 — seeds
# synthetic data through the real service paths, submits ONE exploratory
# research via the public API, and asserts the full D2 attribution chain on
# the saved report. Production is never touched (separate project, networks,
# volumes, credentials; youwei-research is used read-only as the gateway
# path, exactly as the rollout will).
#
# Usage (sg-prod):  bash ops/s12_rollout_rehearsal.sh
# Exit 0 = REHEARSAL PASS; 1 = failure (details printed; stack torn down).

set -euo pipefail

CORE=ghcr.io/youweichen0208/youwei-core@sha256:f18c8289a8d05b1b9913ac22973532c8c1683703b2f38a955ad7ca8d264abe40
RUNNER=ghcr.io/youweichen0208/youwei-runner@sha256:c44c3d4a361422572edb258c58d83b8b2268181a8888a4c35d0062eda0955de9
AGENT=ghcr.io/youweichen0208/youwei-agent-runtime@sha256:8d210f9105c0a85aa89bc362ea87e1eddf93d278abd6190fd55e33f93ff3c6ab
SANDBOX_BASE=python:3.13-alpine@sha256:2dd78ad5cf13a0b68f5134dc49aa9950203a8cf4b7463431b9f3b398287c5059
PG=postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea

WORK=/tmp/s12-rehearsal
SPOOL=/var/lib/s12-rehearsal-spool
API_PORT=18100
PROJECT=s12-rehearsal
REPO="${YOUWEI_DEPLOY_REPO:-/root/youwei-trading-agent}"

fail() { echo "REHEARSAL FAIL: $*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------
for img in "$CORE" "$RUNNER" "$AGENT" "$SANDBOX_BASE" "$PG"; do
  docker image inspect "$img" >/dev/null 2>&1 || fail "image not on host: $img"
done
docker network inspect youwei-research >/dev/null 2>&1 || fail "youwei-research network missing"
[ -f /opt/youwei/secrets/production.env ] || fail "production.env missing (research gateway key)"

# --- throwaway credentials ----------------------------------------------------
RUNNER_SECRET=$(openssl rand -hex 24)
PG_PASSWORD=$(openssl rand -hex 16)
CAP_SECRET=$(openssl rand -hex 24)
ADMIN_KEY=$(openssl rand -hex 24)

KEYPAIR_JSON=$(docker run --rm --network none --entrypoint python "$CORE" -c "
from youwei_contracts.research_capability import generate_research_keypair
import json
priv, pub = generate_research_keypair()
print(json.dumps({'priv': priv.replace(chr(10), chr(92)+'n'), 'pub': pub.replace(chr(10), chr(92)+'n')}))
") || fail "keypair generation failed"
PRIV_ESCAPED=$(printf '%s' "$KEYPAIR_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["priv"])')
PUB_ESCAPED=$(printf '%s' "$KEYPAIR_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pub"])')
[ -n "$PRIV_ESCAPED" ] && [ -n "$PUB_ESCAPED" ] || fail "keypair extraction failed"

# the research gateway key the production rollout will use (read, never printed)
set -a; . /opt/youwei/secrets/production.env; set +a
[ -n "${YOUWEI_RESEARCH_GATEWAY_KEY:-}" ] || fail "YOUWEI_RESEARCH_GATEWAY_KEY empty"
GATEWAY_KEY="$YOUWEI_RESEARCH_GATEWAY_KEY"

mkdir -p "$WORK" "$SPOOL"
chmod 700 "$SPOOL"

# --- env file: EXACT production format (single-quoted \n-escaped values) ------
cat > "$WORK/rehearsal.env" <<EOF
YOUWEI_DATABASE_URL=postgresql+asyncpg://rehearsal:${PG_PASSWORD}@postgres:5432/youwei
YOUWEI_ADMIN_API_KEY=${ADMIN_KEY}
YOUWEI_CAPABILITY_SECRET=${CAP_SECRET}
YOUWEI_RUNNER_URL=http://sandbox-runner:8091
YOUWEI_RUNNER_SECRET=${RUNNER_SECRET}
YOUWEI_RESEARCH_MODEL=glm-5.3
YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY='${PRIV_ESCAPED}'
YOUWEI_RESEARCH_PUBLIC_KEYS_JSON='{"research-key-1": "${PUB_ESCAPED}"}'
YOUWEI_RESEARCH_GATEWAY_KEY=${GATEWAY_KEY}
EOF
chmod 600 "$WORK/rehearsal.env"

# --- compose ------------------------------------------------------------------
cat > "$WORK/compose.json" <<EOF
{
  "name": "${PROJECT}",
  "services": {
    "postgres": {
      "image": "${PG}",
      "environment": {
        "POSTGRES_USER": "rehearsal",
        "POSTGRES_DB": "youwei",
        "POSTGRES_PASSWORD": "\${YOUWEI_PG_PASSWORD:?}"
      },
      "volumes": ["pg_data:/var/lib/postgresql/data"],
      "networks": ["core"],
      "healthcheck": {
        "test": ["CMD-SHELL", "pg_isready -U rehearsal -d youwei"],
        "interval": "5s", "timeout": "3s", "retries": 30
      }
    },
    "core-api": {
      "image": "${CORE}",
      "command": ["youwei-api"],
      "environment": {
        "YOUWEI_DATABASE_URL": "\${YOUWEI_DATABASE_URL:?}",
        "YOUWEI_ADMIN_API_KEY": "\${YOUWEI_ADMIN_API_KEY:?}",
        "YOUWEI_CAPABILITY_SECRET": "\${YOUWEI_CAPABILITY_SECRET:?}"
      },
      "ports": ["127.0.0.1:${API_PORT}:8000"],
      "networks": ["core"],
      "depends_on": {"postgres": {"condition": "service_healthy"}}
    },
    "core-worker": {
      "image": "${CORE}",
      "command": ["youwei-worker"],
      "init": true,
      "environment": {
        "YOUWEI_DATABASE_URL": "\${YOUWEI_DATABASE_URL:?}",
        "YOUWEI_CAPABILITY_SECRET": "\${YOUWEI_CAPABILITY_SECRET:?}",
        "YOUWEI_RUNNER_URL": "\${YOUWEI_RUNNER_URL:?}",
        "YOUWEI_RUNNER_SECRET": "\${YOUWEI_RUNNER_SECRET:?}",
        "YOUWEI_RESEARCH_MODEL": "\${YOUWEI_RESEARCH_MODEL:?}",
        "YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY": "\${YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY:?}",
        "YOUWEI_EXPERIMENT_EXPLORATION_ENABLED": "false"
      },
      "networks": ["core"],
      "depends_on": {"postgres": {"condition": "service_healthy"}}
    },
    "sandbox-runner": {
      "image": "${RUNNER}",
      "environment": {
        "YOUWEI_RUNNER_SECRET": "\${YOUWEI_RUNNER_SECRET:?}",
        "YOUWEI_RUNNER_IMAGE": "${SANDBOX_BASE}",
        "YOUWEI_RUNNER_RUNTIME": "runsc",
        "YOUWEI_RUNNER_SPOOL_ROOT": "${SPOOL}",
        "YOUWEI_RUNNER_AGENT_RUNTIME_IMAGE": "${AGENT}",
        "YOUWEI_RUNNER_AGENT_RUNTIME_GATEWAY_URL": "http://litellm:4000/v1",
        "YOUWEI_RUNNER_AGENT_RUNTIME_GATEWAY_HOST": "litellm:4000",
        "YOUWEI_RUNNER_AGENT_RUNTIME_PUBLIC_KEYS": "\${YOUWEI_RESEARCH_PUBLIC_KEYS_JSON:?}",
        "YOUWEI_RUNNER_AGENT_RUNTIME_TIMEOUT_SECONDS": "1800",
        "YOUWEI_GATEWAY_API_KEY": "\${YOUWEI_RESEARCH_GATEWAY_KEY:?}"
      },
      "volumes": [
        {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock", "bind": {"create_host_path": false}},
        {"type": "bind", "source": "${SPOOL}", "target": "${SPOOL}", "bind": {"create_host_path": false}}
      ],
      "networks": ["core"],
      "depends_on": {"postgres": {"condition": "service_healthy"}}
    }
  },
  "networks": {"core": {}},
  "volumes": {"pg_data": {}}
}
EOF

cleanup() {
  docker compose -p "$PROJECT" -f "$WORK/compose.json" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$SPOOL"
}
trap cleanup EXIT

dc() {
  # shellcheck disable=SC1090
  set -a; . "$WORK/rehearsal.env"; set +a
  export YOUWEI_PG_PASSWORD="$PG_PASSWORD"
  docker compose -p "$PROJECT" -f "$WORK/compose.json" "$@"
}

# --- 1. postgres + migrate + seed ---------------------------------------------
dc up -d postgres
for i in $(seq 1 60); do
  if docker exec "${PROJECT}-postgres-1" pg_isready -U rehearsal -d youwei >/dev/null 2>&1; then break; fi
  sleep 1
done
dc up -d postgres  # ensure healthy state registered
sleep 2

# shellcheck disable=SC1090
set -a; . "$WORK/rehearsal.env"; set +a
export YOUWEI_PG_PASSWORD="$PG_PASSWORD"

docker run --rm --network "${PROJECT}_core" \
  -e YOUWEI_DATABASE_URL="$YOUWEI_DATABASE_URL" \
  "$CORE" alembic upgrade head >/dev/null || fail "migration failed"

docker run --rm --network "${PROJECT}_core" \
  -v "$REPO/ops/s12_rehearsal_seed.py:/seed.py:ro" \
  -e YOUWEI_DATABASE_URL="$YOUWEI_DATABASE_URL" \
  --entrypoint python "$CORE" /seed.py > "$WORK/seed.json" \
  || { cat "$WORK/seed.json" >&2 || true; fail "seed failed"; }
python3 -c 'import json; d=json.load(open("'"$WORK"'/seed.json")); assert d["api_key"] and d["ticker"]=="REH1"' \
  || fail "seed output malformed"
echo "seed OK: $(python3 -c 'import json; d=json.load(open("'"$WORK"'/seed.json")); print("campaign", d["campaign_id"][:8], "window", d["window"][0], "->", d["window"][1])')"

# --- 2. services ----------------------------------------------------------------
dc up -d core-api core-worker sandbox-runner
for i in $(seq 1 60); do
  if curl -sf -m 3 "http://127.0.0.1:${API_PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sf -m 5 "http://127.0.0.1:${API_PORT}/healthz" >/dev/null || fail "api healthz failed"
docker exec "${PROJECT}-sandbox-runner-1" python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=3).status == 200" \
  || fail "runner healthz failed"
echo "services up: api + worker + runner healthy"

# --- 3. submit + poll -------------------------------------------------------------
API_KEY=$(python3 -c 'import json; print(json.load(open("'"$WORK"'/seed.json"))["api_key"])')
# Ledger baseline BEFORE the research. The guarantee under test is that the
# EXPLORATORY RESEARCH path writes no formal ledger: predictions,
# forecast_commits and the ledger chain head must not move. forecast_batches
# is informational only — the seeded ACTIVE campaign's scheduler legitimately
# plans batches during the run (campaign planning, not research writes).
LEDGER_BASE=$(docker exec "${PROJECT}-postgres-1" psql -U rehearsal -d youwei -Atc \
  "SELECT (SELECT count(*) FROM predictions), (SELECT count(*) FROM forecast_commits), (SELECT coalesce(max(head_seq), 0) FROM ledger_chains)") \
  || fail "ledger baseline query failed"
SUBMIT=$(curl -sf -m 15 -X POST "http://127.0.0.1:${API_PORT}/v1/research" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: s12-rehearsal-1" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "REH1", "horizon_td": 20}') || fail "submit failed"
RID=$(printf '%s' "$SUBMIT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["research_id"])')
echo "submitted: research_id=$RID (ticker REH1, D20, model glm-5.3)"

STATUS=""
for i in $(seq 1 450); do
  STATUS=$(curl -sf -m 10 "http://127.0.0.1:${API_PORT}/v1/research/$RID" \
    -H "Authorization: Bearer $API_KEY" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d.get("job_status", ""))') || STATUS="poll-error"
  case "$STATUS" in
    succeeded*|failed*|cancelled*) break ;;
  esac
  sleep 4
done
echo "final status: $STATUS"
case "$STATUS" in
  succeeded*) ;;
  *)
    echo "--- failure diagnostics:"
    curl -sf -m 10 "http://127.0.0.1:${API_PORT}/v1/research/$RID" \
      -H "Authorization: Bearer $API_KEY" | python3 -m json.tool || true
    echo "--- task failure reason (last exploratory events):"
    docker exec "${PROJECT}-postgres-1" psql -U rehearsal -d youwei -Atc \
      "SELECT payload FROM events WHERE payload::text LIKE '%research_id%' ORDER BY created_at DESC LIMIT 4" 2>/dev/null || true
    echo "--- job error:"
    docker exec "${PROJECT}-postgres-1" psql -U rehearsal -d youwei -Atc \
      "SELECT kind || ': ' || coalesce(error, '') FROM jobs ORDER BY created_at DESC LIMIT 3" 2>/dev/null || true
    echo "--- worker logs (non-poll, tail 60):"
    docker logs "${PROJECT}-core-worker-1" 2>&1 | grep -v "GET /v1/research-invocations" | tail -60 || true
    echo "--- runner logs (non-poll, tail 40):"
    docker logs "${PROJECT}-sandbox-runner-1" 2>&1 | grep -v "GET /v1/research-invocations" | tail -40 || true
    fail "research did not succeed (status: $STATUS)" ;;
esac

curl -sf -m 10 "http://127.0.0.1:${API_PORT}/v1/research/$RID/report" \
  -H "Authorization: Bearer $API_KEY" > "$WORK/report.json" || fail "report fetch failed"

# --- 4. assertions ------------------------------------------------------------------
python3 - "$WORK/report.json" <<'PY'
import json, re, sys
r = json.load(open(sys.argv[1]))
content = r["content"]
v = content["versions"]
HEX64 = re.compile(r"^[0-9a-f]{64}$")

assert v["research_model_configured"] == "glm-5.3", v
sink = v["research_attribution"]
assert sink is not None, "attribution sink empty"
assert sink["exec_config_version"] == "research-exec-v1"
assert sink["image_digest"].endswith("8d210f9105c0a85aa89bc362ea87e1eddf93d278abd6190fd55e33f93ff3c6ab"), sink["image_digest"]
usage = sink["usage"]
assert usage and usage.get("source") == "session_delta" and (usage.get("api_calls") or 0) >= 1, usage
attr = sink["attribution"]
assert attr is not None, "container attribution missing"
assert HEX64.match(attr["brief_sha256"]), attr["brief_sha256"]
cfg = attr["execution_config"]
assert cfg["model"] == "glm-5.3" and "api_key" not in cfg, cfg
assert attr["execution_config_sha256"] == __import__("hashlib").sha256(
    json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
).hexdigest()
mr = attr["model_returned"]
assert mr, f"model_returned empty: {attr}"
assert attr["model_returned_scope"] == "last_completed_provider_response"
summary = content["summary"]
assert summary["source_status"] in ("produced", "unavailable")
if summary["source_status"] == "produced":
    assert summary["quant_relation"] in ("kept", "adjusted"), summary
    assert 0.0 <= summary["p_outperform"] <= 1.0
assert content["quant"]["quant_model"] is not None
assert r.get("references_resolved"), "no resolved references"
print(f"report OK: source_status={summary['source_status']} quant_relation={summary.get('quant_relation')} "
      f"p={summary.get('p_outperform')} model_returned={mr} "
      f"usage={usage.get('prompt_tokens')}p/{usage.get('completion_tokens')}c/{usage.get('api_calls')}calls "
      f"brief_sha={attr['brief_sha256'][:12]}… refs={len(r['references_resolved'])}")
PY
[ $? -eq 0 ] || fail "report assertions failed"

# --- 5. ledger zero-write + leftovers ------------------------------------------------
LEDGER=$(docker exec "${PROJECT}-postgres-1" psql -U rehearsal -d youwei -Atc \
  "SELECT (SELECT count(*) FROM predictions), (SELECT count(*) FROM forecast_commits), (SELECT coalesce(max(head_seq), 0) FROM ledger_chains)")
BATCHES=$(docker exec "${PROJECT}-postgres-1" psql -U rehearsal -d youwei -Atc \
  "SELECT count(*) FROM forecast_batches")
[ "$LEDGER" = "$LEDGER_BASE" ] || fail "ledger was written by exploratory research (base=$LEDGER_BASE now=$LEDGER)"
echo "ledger untouched by exploratory research: predictions/commits/chain-head unchanged (base=$LEDGER_BASE; campaign batches planned by the scheduler: $BATCHES)"

LEFT=$(docker ps -a --format "{{.Names}}" | grep -c "agent-runtime" || true)
[ "$LEFT" -eq 0 ] || fail "leftover agent-runtime containers: $LEFT"

echo
echo "REHEARSAL PASS — staged images validated end-to-end (submit -> freeze -> worker wiring -> runner -> agent-runtime (patched) -> LiteLLM/glm-5.3 -> proposal -> report with full attribution); ledger untouched; no leftovers."
