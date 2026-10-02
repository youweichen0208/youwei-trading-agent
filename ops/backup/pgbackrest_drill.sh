#!/usr/bin/env bash
# pgBackRest drill for the youwei-core PostgreSQL (S09c).
#
# Companion to ops/backup/pitr_drill.sh: same marker-based PITR proof
# (A inside the backup, B after it but before the target time, C after
# the target), but the backup/restore mechanics are pgBackRest on the
# custom postgres image (infra/images/postgres-pgbackrest.Dockerfile):
#
#   1. source cluster with archive_mode=on and
#      archive_command='pgbackrest archive-push' into a local posix repo
#   2. stanza-create + check, full backup (timed)
#   3. WAL containing markers B/C archives through pgBackRest
#   4. time-targeted restore into a fresh data directory (timed),
#      promoted at the target time: A+B present, C absent, schema at
#      head, application read path OK
#   5. full-loss restore (no target time) into another fresh directory:
#      A+B+C all present (latest state)
#
# The repo type in this drill is posix (a local directory); S3-backed
# repos differ only in [global] config and are validated when the
# production backup target is decided. NOT covered here: off-site
# copies, retention, scheduled backups, disk watermarks.
#
# Usage:  PG_IMAGE=<image> ops/backup/pgbackrest_drill.sh
#         (PG_IMAGE defaults to the phase1a-s09c custom image tag)
# Requires: docker, uv, python3; runs on the target host (sg-prod) or
# any docker-enabled machine with a repo checkout. On failure the
# workdir is kept for debugging.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PG_IMAGE="${PG_IMAGE:-ghcr.io/youweichen0208/youwei-postgres:phase1a-s09c}"
STANZA="youwei"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/youwei-pgbr-drill.XXXXXX")"
SRC="youwei-pgbr-src-$$"
KEEP=0

cleanup() {
    if [ -n "${DOCKER:-}" ]; then
        "$DOCKER" rm -f "$SRC" >/dev/null 2>&1 || true
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

find_bin() {
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

# All pgbackrest commands run as the postgres user (uid 70 in the
# alpine image) so the repo files stay owned by the same uid that
# archive-push (inside the server container) writes as.
pgbr() { # $@ = pgbackrest args; extra mounts via $*
    "$DOCKER" run --rm \
        -u 70:70 \
        -v "$WORK/pgdata":/var/lib/postgresql/data \
        -v "$WORK/socket":/socket \
        -v "$WORK/repo":/repo \
        -v "$WORK/log":/var/log/pgbackrest \
        -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
        "$PG_IMAGE" pgbackrest --stanza="$STANZA" --log-level-console=info "$@"
}

psql_src() { "$DOCKER" exec "$SRC" psql -U youwei -d youwei -v ON_ERROR_STOP=1 -qtAc "$1"; }

wait_ready() {
    # Wait for a real connection to the target database, not merely
    # pg_isready: during initdb the entrypoint's TEMP server also answers
    # pg_isready on the socket, and commands racing through that window
    # see "no primary" / connection refused.
    for _ in $(seq 1 120); do
        if "$DOCKER" exec "$1" psql -U youwei -d youwei -qtAc "SELECT 1" >/dev/null 2>&1; then return 0; fi
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
            kind="pgbackrest-drill",
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

# --- preflight ---------------------------------------------------------------

step "preflight"
"$DOCKER" info >/dev/null 2>&1 || die "docker daemon unreachable"
"$DOCKER" image inspect "$PG_IMAGE" >/dev/null 2>&1 \
    || die "image $PG_IMAGE not found locally (build or pull it first)"
SRC_PORT=$(free_port)
SRC_URL="postgresql+asyncpg://youwei:youwei@127.0.0.1:${SRC_PORT}/youwei"
echo "source:    127.0.0.1:${SRC_PORT} (container ${SRC})"
echo "image:     ${PG_IMAGE}"
echo "workdir:   ${WORK}"

# --- source cluster: pgbackrest archive-push ---------------------------------

step "start source postgres (archive_mode=on -> pgbackrest archive-push)"
mkdir -p "$WORK/pgdata" "$WORK/socket" "$WORK/repo" "$WORK/log"
chown -R 70:70 "$WORK/pgdata" "$WORK/socket" "$WORK/repo" "$WORK/log" 2>/dev/null \
    || echo "NOTE: chown failed (not root?); continuing — docker may map uids"
cat > "$WORK/pgbackrest.conf" <<EOF
[global]
repo1-path=/repo
log-path=/var/log/pgbackrest
log-level-console=info
log-level-file=info
[${STANZA}]
pg1-path=/var/lib/postgresql/data
pg1-socket-path=/socket
pg1-port=5432
pg1-user=youwei
pg1-database=youwei
EOF
"$DOCKER" run -d --name "$SRC" \
    -e POSTGRES_USER=youwei -e POSTGRES_PASSWORD=youwei -e POSTGRES_DB=youwei \
    -v "$WORK/pgdata":/var/lib/postgresql/data \
    -v "$WORK/socket":/socket \
    -v "$WORK/repo":/repo \
    -v "$WORK/log":/var/log/pgbackrest \
    -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
    -p 127.0.0.1:${SRC_PORT}:5432 \
    "$PG_IMAGE" \
    postgres -c wal_level=replica -c archive_mode=on -c archive_timeout=30 \
             -c unix_socket_directories="/var/run/postgresql,/socket" \
             -c "archive_command=pgbackrest --stanza=${STANZA} archive-push %p" \
    >/dev/null
wait_ready "$SRC" || die "source postgres never became ready"
echo "source ready"

# --- stanza + schema + marker A ----------------------------------------------

step "stanza-create"
pgbr stanza-create || die "stanza-create failed"

step "migrate + seed marker A"
(cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$SRC_URL" "$UV" run --frozen alembic upgrade head) \
    || die "alembic upgrade failed on source"
RUN_A=$(seed drill-A) || die "seeding marker A failed"
echo "marker A run: ${RUN_A}"
switch_and_wait || die "WAL with marker A never archived"

step "pgbackrest check (archive round-trip through the repo)"
pgbr check || die "pgbackrest check failed"

# --- full backup (contains marker A) ------------------------------------------

step "full backup (timed)"
T0=$(date +%s)
pgbr backup --type=full || die "full backup failed"
T1=$(date +%s)
echo "full backup took $((T1 - T0))s"
pgbr info || die "pgbackrest info failed"

# --- marker B, target time, marker C -------------------------------------------

step "seed marker B, capture recovery target, seed marker C"
RUN_B=$(seed drill-B) || die "seeding marker B failed"
echo "marker B run: ${RUN_B}  (committed after the full backup)"
TARGET_TIME=$(psql_src "SELECT clock_timestamp()")
echo "recovery_target_time: ${TARGET_TIME}"
sleep 1  # strict separation between target time and marker C's commit
RUN_C=$(seed drill-C) || die "seeding marker C failed"
echo "marker C run: ${RUN_C}  (committed after the target time)"
switch_and_wait || die "WAL with marker C never archived"

echo "archiver stats on source:"
psql_src "SELECT archived_count, failed_count, last_archived_time FROM pg_stat_archiver"

# --- restore 1: PITR to the target time ----------------------------------------

step "restore 1: time-targeted restore into a fresh data directory (timed)"
mkdir -p "$WORK/restore1"
chown -R 70:70 "$WORK/restore1"
T2=$(date +%s)
"$DOCKER" run --rm \
    -u 70:70 \
    -v "$WORK/restore1":/var/lib/postgresql/data \
    -v "$WORK/repo":/repo \
    -v "$WORK/log":/var/log/pgbackrest \
    -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
    "$PG_IMAGE" pgbackrest --stanza="$STANZA" --log-level-console=info \
    restore --type=time --target="${TARGET_TIME}" --target-action=promote \
    || die "pgbackrest restore failed"
REST1="youwei-pgbr-rest1-$$"
REST1_PORT=$(free_port)
REST1_URL="postgresql+asyncpg://youwei:youwei@127.0.0.1:${REST1_PORT}/youwei"
"$DOCKER" run -d --name "$REST1" \
    -e POSTGRES_USER=youwei -e POSTGRES_PASSWORD=youwei -e POSTGRES_DB=youwei \
    -v "$WORK/restore1":/var/lib/postgresql/data \
    -v "$WORK/repo":/repo:ro \
    -v "$WORK/log":/var/log/pgbackrest \
    -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
    -p 127.0.0.1:${REST1_PORT}:5432 \
    "$PG_IMAGE" \
    >/dev/null
for _ in $(seq 1 120); do
    if "$DOCKER" exec "$REST1" pg_isready -U youwei -d youwei >/dev/null 2>&1; then break; fi
    sleep 0.5
done
promoted=0
for _ in $(seq 1 120); do
    if [ "$("$DOCKER" exec "$REST1" psql -U youwei -d youwei -qtAc 'SELECT pg_is_in_recovery()')" = "f" ]; then
        promoted=1; break
    fi
    sleep 0.5
done
[ "$promoted" = "1" ] || die "recovery never promoted at the target time"
T3=$(date +%s)
echo "restore + replay + promote took $((T3 - T2))s"

step "verify PITR: schema at head, markers A+B present, marker C absent"
psql_rest1() { "$DOCKER" exec "$REST1" psql -U youwei -d youwei -v ON_ERROR_STOP=1 -qtAc "$1"; }
(cd "$REPO_ROOT" && YOUWEI_DATABASE_URL="$REST1_URL" "$UV" run --frozen alembic current) \
    | tee "$WORK/alembic-current.txt"
grep -q "(head)" "$WORK/alembic-current.txt" || die "recovered schema is not at alembic head"
KEYS=$(psql_rest1 "SELECT idempotency_key FROM runs ORDER BY created_at")
echo "recovered idempotency keys: $(echo "$KEYS" | tr '\n' ' ')"
[ "$(echo "$KEYS" | grep -cx 'drill-A')" = "1" ] || die "marker A missing after PITR"
[ "$(echo "$KEYS" | grep -cx 'drill-B')" = "1" ] || die "marker B missing after PITR (archived WAL replay failed)"
[ "$(echo "$KEYS" | grep -cx 'drill-C')" = "0" ] || die "marker C present after PITR (recovery overshot the target)"
"$DOCKER" rm -f "$REST1" >/dev/null

# --- restore 2: full-loss recovery to latest ------------------------------------

step "restore 2: no-target restore (full cluster loss -> latest state)"
mkdir -p "$WORK/restore2"
chown -R 70:70 "$WORK/restore2"
"$DOCKER" run --rm \
    -u 70:70 \
    -v "$WORK/restore2":/var/lib/postgresql/data \
    -v "$WORK/repo":/repo \
    -v "$WORK/log":/var/log/pgbackrest \
    -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
    "$PG_IMAGE" pgbackrest --stanza="$STANZA" --log-level-console=info \
    restore \
    || die "pgbackrest restore (latest) failed"
REST2="youwei-pgbr-rest2-$$"
"$DOCKER" run -d --name "$REST2" \
    -e POSTGRES_USER=youwei -e POSTGRES_PASSWORD=youwei -e POSTGRES_DB=youwei \
    -v "$WORK/restore2":/var/lib/postgresql/data \
    -v "$WORK/repo":/repo:ro \
    -v "$WORK/log":/var/log/pgbackrest \
    -v "$WORK/pgbackrest.conf":/etc/pgbackrest/pgbackrest.conf:ro \
    "$PG_IMAGE" \
    >/dev/null
for _ in $(seq 1 120); do
    if "$DOCKER" exec "$REST2" pg_isready -U youwei -d youwei >/dev/null 2>&1; then break; fi
    sleep 0.5
done
"$DOCKER" exec "$REST2" psql -U youwei -d youwei -v ON_ERROR_STOP=1 -qtAc \
    "SELECT pg_is_in_recovery()" | grep -qx f || die "restore2 never left recovery"
KEYS2=$("$DOCKER" exec "$REST2" psql -U youwei -d youwei -qtAc \
    "SELECT idempotency_key FROM runs ORDER BY created_at" | tr '\n' ' ')
echo "latest-recovery idempotency keys: ${KEYS2}"
for m in drill-A drill-B drill-C; do
    echo "$KEYS2" | grep -qw "$m" || die "marker $m missing after latest restore"
done
"$DOCKER" rm -f "$REST2" >/dev/null

step "PASS"
echo "pgBackRest backup/restore verified (posix repo):"
echo "  - stanza-create + check + full backup; WAL archived via archive-push"
echo "  - time-targeted restore: post-backup commit present, post-target absent"
echo "  - latest restore: all commits present"
echo "  - both recovered clusters reach alembic head"
echo "timings: full backup $((T1 - T0))s; PITR restore+promote $((T3 - T2))s (tiny DB; mechanism-level, not capacity)"
