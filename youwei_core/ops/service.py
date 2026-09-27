"""Ops health snapshot and alerting (S02b closeout).

One read-only pass over the job tables answers the deployment's
first-version monitoring questions (architecture section 11):
queue backlog age, budget awaiting reconciliation, unpublished outbox
events, lease-expired attempts not yet reaped, runs past their
wall-clock deadline, and WAL archive staleness.

The API layer exposes this as /v1/ops/status (admin key); /healthz is
a DB-free liveness probe. Alert thresholds come from Settings so
operators tune them without code changes. The snapshot never mutates
state: reaping stays in the worker's claim path.

Alert semantics:
- age-based signals (queue, outbox, pending reconciliation, WAL
  archive) compare against configurable thresholds
- count-based signals (expired leases not reaped, overdue runs not
  reaped) alert on any occurrence: they mean the claim/reaper pass is
  not running or cannot keep up
- WAL archive alerts only when archiving is enabled; a NULL
  last_archived_time with archiving on means the pipeline never
  shipped (deployment sets archive_timeout, so silence is failure)
"""

from datetime import datetime

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.config import Settings
from youwei_core.db.meta import attempts, budget_entries, events, jobs, runs


def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return round((now - ts).total_seconds(), 3)


async def ops_snapshot(engine: AsyncEngine) -> dict:
    """Collect all health counters in one short read-only transaction."""
    async with engine.begin() as conn:
        now = (await conn.execute(select(func.now()))).scalar_one()

        queued = (
            await conn.execute(
                select(func.count(), func.min(jobs.c.created_at)).where(
                    jobs.c.status == "queued"
                )
            )
        ).one()

        unpublished = (
            await conn.execute(
                select(func.count(), func.min(events.c.occurred_at)).where(
                    events.c.published_at.is_(None)
                )
            )
        ).one()

        expired_leases = (
            await conn.execute(
                select(func.count(), func.min(attempts.c.lease_expires_at)).where(
                    attempts.c.status == "running",
                    attempts.c.lease_expires_at < now,
                )
            )
        ).one()

        overdue = (
            await conn.execute(
                select(func.count(), func.min(runs.c.wall_clock_deadline)).where(
                    runs.c.status.in_(("pending", "running")),
                    runs.c.wall_clock_deadline.is_not(None),
                    runs.c.wall_clock_deadline < now,
                )
            )
        ).one()

        # global pending reconciliation: reserves with no matching
        # settle or release in the same run (unknown cost)
        settle_match = budget_entries.alias("settle_match")
        release_match = budget_entries.alias("release_match")
        pending = (
            await conn.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(budget_entries.c.amount_micros), 0),
                    func.min(budget_entries.c.created_at),
                ).where(
                    budget_entries.c.entry_type == "reserve",
                    ~exists().where(
                        settle_match.c.run_id == budget_entries.c.run_id,
                        settle_match.c.entry_type == "settle",
                        settle_match.c.idem_key
                        == func.replace(budget_entries.c.idem_key, ":reserve", ":settle"),
                    ),
                    ~exists().where(
                        release_match.c.run_id == budget_entries.c.run_id,
                        release_match.c.entry_type == "release",
                        release_match.c.idem_key
                        == func.replace(budget_entries.c.idem_key, ":reserve", ":release"),
                    ),
                )
            )
        ).one()

        archiver = (
            await conn.execute(
                text(
                    "SELECT current_setting('archive_mode') <> 'off' AS enabled,"
                    " archived_count, failed_count, last_archived_time"
                    " FROM pg_stat_archiver"
                )
            )
        ).one()

    return {
        "queue": {
            "queued_jobs": queued[0],
            "oldest_queued_age_seconds": _age_seconds(now, queued[1]),
        },
        "budget": {
            "pending_reconciliation": pending[0],
            "pending_micros": pending[1],
            "oldest_pending_age_seconds": _age_seconds(now, pending[2]),
        },
        "events": {
            "unpublished": unpublished[0],
            "oldest_unpublished_age_seconds": _age_seconds(now, unpublished[1]),
        },
        "leases": {
            "expired_running_attempts": expired_leases[0],
            "oldest_expired_age_seconds": _age_seconds(now, expired_leases[1]),
        },
        "runs": {
            "over_deadline": overdue[0],
            "oldest_over_deadline_age_seconds": _age_seconds(now, overdue[1]),
        },
        "wal_archive": {
            "enabled": bool(archiver.enabled),
            "archived_count": archiver.archived_count,
            "failed_count": archiver.failed_count,
            "last_archived_age_seconds": _age_seconds(now, archiver.last_archived_time),
        },
    }


def evaluate_alerts(snapshot: dict, settings: Settings) -> list[str]:
    """Pure threshold check over a snapshot; stable alert names are
    part of the operator contract."""
    alerts: list[str] = []

    queue_age = snapshot["queue"]["oldest_queued_age_seconds"]
    if queue_age is not None and queue_age > settings.alert_queue_backlog_age_seconds:
        alerts.append("queue_backlog_stale")

    events_age = snapshot["events"]["oldest_unpublished_age_seconds"]
    if (
        events_age is not None
        and events_age > settings.alert_unpublished_events_age_seconds
    ):
        alerts.append("unpublished_events_stale")

    budget_age = snapshot["budget"]["oldest_pending_age_seconds"]
    if (
        budget_age is not None
        and budget_age > settings.alert_pending_reconciliation_age_seconds
    ):
        alerts.append("budget_pending_reconciliation_stale")

    if snapshot["leases"]["expired_running_attempts"] > 0:
        alerts.append("leases_expired_not_reaped")

    if snapshot["runs"]["over_deadline"] > 0:
        alerts.append("runs_over_deadline_not_reaped")

    wal = snapshot["wal_archive"]
    if wal["enabled"] and (
        wal["last_archived_age_seconds"] is None
        or wal["last_archived_age_seconds"] > settings.alert_wal_archive_stale_seconds
    ):
        alerts.append("wal_archive_stale")

    return alerts


async def ops_status(engine: AsyncEngine, settings: Settings) -> dict:
    snapshot = await ops_snapshot(engine)
    alerts = evaluate_alerts(snapshot, settings)
    return {"status": "alert" if alerts else "ok", "alerts": alerts, **snapshot}
