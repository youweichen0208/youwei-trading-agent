#!/usr/bin/env bash
# PITR drill for the youwei-core PostgreSQL (S02b closeout).
#
# Proves, on an isolated throwaway container pair:
#   1. local WAL archiving works (archive_command -> host directory)
#   2. a base backup (pg_basebackup -Fp -Xs) plus archived WAL
#      restores the cluster to a chosen point in time
#      (recovery_target_time)
#   3. data committed after the target time is absent; data before it
#      — including commits made AFTER the base backup — is present
#   4. the recovered cluster is usable: alembic sees head, and the
#      application read path (get_run_view) returns the expected rows
#
# Seeding goes through the real submission path (submit_run), so the
# recovered rows are real schema + real rows, not synthetic markers.
#
# Production RPO is bounded by archive_timeout + archiver lag; this
# drill prints its own phase timings and the archiver stats it
# observed. NOT covered here (host-level, S09 deployment acceptance):
# disk watermarks, cross-machine backup shipping, scheduled backup
# jobs, encrypted backup targets.
#
# Usage:  ops/backup/pitr_drill.sh
# Requires: docker (daemon reachable), uv, python3.
# On failure the workdir is kept (see output) for debugging.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PG_IMAGE="postgres:16-alpine"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/youwei-pitr-drill.XXXXXX")"
SRC="youwei-pitr-src-$$"
REST="youwei-pitr-rest-$$"
KEEP=0

cleanup() {
    if [ -n "${DOCKER:-}" ]; then
        "$DOCKER" rm -f "$SRC" "$REST" >/dev/null 2>&1 || true
    fi
    if [ "$KEEP" = "1" ]; then
        echo "drill workdir kept for inspection: $WORK"
    else
        rm -rf "$WORK"
    fi
}
trap 'KEEP=1' ERR
trap cleanup EXIT

step() { printf '\n== %s ==\n' "$*"; }
die() { echo "FAIL: $*" >&2; exit 1; }

# Resolve tools beyond a possibly restricted PATH (mirrors the test
# suite's conftest._docker()).
find_bin() { # $1 = name, $2.. = fallback candidates
    local name=$1 c
    shift
    if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
    for c in "$@"; do
        if [ -x "$c" ]; then echo "$c"; return 0; fi
    done
    return 1
}

DOCKER=$(find_bin docker /usr/local/bin/docker /opt/homebrew/bin/docker) \
    || die "docker not found"
UV=$(find_bin uv "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv) \
    || die "uv not found"
PY3=$(find_bin python3 /usr/bin/python3 /opt/homebrew/bin/python3) \
    || die "python3 not found"

free_port() {
    "$PY3" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

psql_src() { "$DOCKER" exec "$SRC" psql -U youwei -d youwei -v ON_ERROR_STOP=1 -qtAc "$1"; }
psql_rest() { "$DOCKER" exec "$REST" psql -U youwei -d youwei -v ON_ERROR_STOP=1 -qtAc "$1"; }

wait_ready() { # $1 = container name
    for _ in $(seq 1 120); do
        if "$DOCKER" exec "$1" pg_isready -U youwei -d youwei >/dev/null 2>&1; then return 0; fi
        sleep 0.5
    done
    return 1
}

wait_archive() { # $1 = archived_count target (>=)
    local n
    for _ in $(seq 1 120); do
        n=$(psql_src "SELECT archived_count FROM pg_stat_archiver")
        if [ "${n:-0}" -ge "$1" ]; then return 0; fi
        sleep 0.5
    done
    echo "archiver stuck below $1 (at ${n:-?})" >&2
    return 1
}

switch_and_wait() {
    local base
    base=$(psql_src "SELECT archived_count FROM pg_stat_archiver")
    psql_src "SELECT pg_switch_wal()" >/dev/null
    wait_archive $((base + 1))
}

seed() { # $1 = idempotency key; prints run_id
    (cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$SRC_URL" "$UV" run --frozen python - "$1" <<'PY'
import asyncio
import sys
import uuid

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run

DRILL_TENANT = uuid.UUID("00000000-0000-4000-8000-000000000001")


async def main(key: str) -> None:
    engine = make_engine(Settings().database_url, pool_size=1)
    try:
        submission = RunSubmission(
            kind="pitr-drill",
            total_budget_micros=1_000_000,
            jobs=[JobSubmission(kind="noop", payload={"marker": key})],
        )
        result = await submit_run(engine, DRILL_TENANT, submission, key)
        print(result.run_id)
    finally:
        await engine.dispose()


asyncio.run(main(sys.argv[1]))
PY
)
}

# --- preflight -----------------------------------------------------------

step "preflight"
"$DOCKER" info >/dev/null 2>&1 || die "docker daemon unreachable"
SRC_PORT=$(free_port)
REST_PORT=$(free_port)
SRC_URL="postgresql+asyncpg://youwei:youwei@127.0.0.1:${SRC_PORT}/youwei"
REST_URL="postgresql+asyncpg://youwei:youwei@127.0.0.1:${REST_PORT}/youwei"
echo "source:    127.0.0.1:${SRC_PORT} (container ${SRC})"
echo "restore:   127.0.0.1:${REST_PORT} (container ${REST})"
echo "workdir:   ${WORK}"

# --- source cluster with WAL archiving ------------------------------------

step "start source postgres (archive_mode=on -> ${WORK}/wal_archive)"
mkdir -p "$WORK/wal_archive" "$WORK/backup"
"$DOCKER" run -d --name "$SRC" \
    -e POSTGRES_USER=youwei -e POSTGRES_PASSWORD=youwei -e POSTGRES_DB=youwei \
    -v "$WORK/wal_archive":/wal_archive \
    -v "$WORK/backup":/backup \
    -p 127.0.0.1:${SRC_PORT}:5432 \
    "$PG_IMAGE" \
    postgres -c wal_level=replica -c archive_mode=on -c archive_timeout=30 \
             -c 'archive_command=test ! -f /wal_archive/%f && cp %p /wal_archive/%f' \
    >/dev/null
# bind mounts arrive owned by the host user; postgres (uid 70) writes both
"$DOCKER" exec -u root "$SRC" chown postgres:postgres /wal_archive /backup
wait_ready "$SRC" || die "source postgres never became ready"
echo "source ready"

# --- schema + marker A (inside the base backup) ----------------------------

step "migrate + seed marker A"
(cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$SRC_URL" "$UV" run --frozen alembic upgrade head) \
    || die "alembic upgrade failed on source"
RUN_A=$(seed drill-A) || die "seeding marker A failed"
echo "marker A run: ${RUN_A}"
switch_and_wait || die "WAL with marker A never archived"

# --- base backup -----------------------------------------------------------

step "base backup (pg_basebackup -Fp -Xs)"
T0=$(date +%s)
"$DOCKER" exec "$SRC" pg_basebackup -D /backup -Fp -Xs -c fast -U youwei \
    || die "pg_basebackup failed"
T1=$(date +%s)
echo "base backup took $((T1 - T0))s"

# --- marker B, target time, marker C (B must survive, C must not) ----------

step "seed marker B, capture recovery target, seed marker C"
RUN_B=$(seed drill-B) || die "seeding marker B failed"
echo "marker B run: ${RUN_B}  (committed after the base backup)"
TARGET_TIME=$(psql_src "SELECT clock_timestamp()")
echo "recovery_target_time: ${TARGET_TIME}"
sleep 1  # strict separation between target time and marker C's commit
RUN_C=$(seed drill-C) || die "seeding marker C failed"
echo "marker C run: ${RUN_C}  (committed after the target time)"
switch_and_wait || die "WAL with marker C never archived"

echo "archiver stats on source:"
psql_src "SELECT archived_count, failed_count, last_archived_time FROM pg_stat_archiver"

# --- restore to target time -------------------------------------------------

step "restore: copy base backup + PITR to target time"
cp -R "$WORK/backup" "$WORK/restore"
touch "$WORK/restore/recovery.signal"
cat >> "$WORK/restore/postgresql.auto.conf" <<EOF
restore_command = 'cp /wal_archive/%f %p'
recovery_target_time = '${TARGET_TIME}'
recovery_target_action = 'promote'
EOF
T2=$(date +%s)
"$DOCKER" run -d --name "$REST" \
    -e POSTGRES_USER=youwei -e POSTGRES_PASSWORD=youwei -e POSTGRES_DB=youwei \
    -v "$WORK/restore":/var/lib/postgresql/data \
    -v "$WORK/wal_archive":/wal_archive:ro \
    -p 127.0.0.1:${REST_PORT}:5432 \
    "$PG_IMAGE" \
    >/dev/null
wait_ready "$REST" || die "restore postgres never became ready"
promoted=0
for _ in $(seq 1 120); do
    if [ "$(psql_rest 'SELECT pg_is_in_recovery()')" = "f" ]; then promoted=1; break; fi
    sleep 0.5
done
[ "$promoted" = "1" ] || die "recovery never reached the target (still in recovery)"
T3=$(date +%s)
echo "restore + recovery took $((T3 - T2))s (promoted at target time)"

# --- verification -------------------------------------------------------------

step "verify: schema at head, markers A+B present, marker C absent"
(cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$REST_URL" "$UV" run --frozen alembic current) \
    | tee "$WORK/alembic-current.txt"
grep -q "(head)" "$WORK/alembic-current.txt" || die "recovered schema is not at alembic head"

KEYS=$(psql_rest "SELECT idempotency_key FROM runs ORDER BY created_at")
echo "recovered idempotency keys: $(echo "$KEYS" | tr '\n' ' ')"
[ "$(echo "$KEYS" | grep -cx 'drill-A')" = "1" ] || die "marker A missing after PITR"
[ "$(echo "$KEYS" | grep -cx 'drill-B')" = "1" ] || die "marker B missing after PITR (archived WAL replay failed)"
[ "$(echo "$KEYS" | grep -cx 'drill-C')" = "0" ] || die "marker C present after PITR (recovery overshot the target)"
EVENTS=$(psql_rest "SELECT count(*) FROM events")
echo "recovered events: ${EVENTS}"

(cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$REST_URL" "$UV" run --frozen python - \
    "$RUN_A" "$RUN_B" "$RUN_C" <<'PY'
import asyncio
import sys
import uuid

from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.jobs.service import RunNotFound, get_run_view

DRILL_TENANT = uuid.UUID("00000000-0000-4000-8000-000000000001")


async def main(run_a: str, run_b: str, run_c: str) -> None:
    engine = make_engine(Settings().database_url, pool_size=1)
    try:
        for key in (run_a, run_b):
            view = await get_run_view(engine, DRILL_TENANT, uuid.UUID(key))
            assert view["status"] == "pending", f"unexpected status for {key}"
            assert view["jobs"][0]["kind"] == "noop"
        try:
            await get_run_view(engine, DRILL_TENANT, uuid.UUID(run_c))
        except RunNotFound:
            print("app read path: A and B readable, C correctly absent")
            return
        raise AssertionError("marker C readable after PITR")
    finally:
        await engine.dispose()


asyncio.run(main(*sys.argv[1:4]))
PY
) || die "application read-path verification failed"

step "PASS"
echo "local WAL archiving + PITR verified:"
echo "  - base backup + archived WAL restored to recovery_target_time"
echo "  - post-backup commit (marker B) present; post-target commit (marker C) absent"
echo "  - recovered cluster passes alembic head and the application read path"
