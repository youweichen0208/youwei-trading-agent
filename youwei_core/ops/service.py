"""Ops health snapshot and alerting (S02b closeout).

One read-only pass over the job tables answers the deployment's
first-version monitoring questions (architecture section 11):
queue backlog age, unpublished outbox events, lease-expired attempts
not yet reaped, runs past their wall-clock deadline, and WAL archive
staleness.

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

from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.config import Settings
from youwei_core.db.meta import (
    attempts,
    events,
    forecast_cases,
    forecast_commit_events,
    forecast_commits,
    jobs,
    outcome_revisions,
    runs,
)


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

        archiver = (
            await conn.execute(
                text(
                    "SELECT current_setting('archive_mode') <> 'off' AS enabled,"
                    " archived_count, failed_count, last_archived_time"
                    " FROM pg_stat_archiver"
                )
            )
        ).one()

        # --- ledger health (S05 closeout) ------------------------------
        # A commit without a durable confirmation whose deadline has
        # passed means the confirmation was lost: an incident on any
        # occurrence (time-protocol §4). Late confirmations are a
        # recorded legitimate state (excluded from the on-time set).
        confirmation = forecast_commit_events.alias("confirmation")
        unconfirmed = (
            await conn.execute(
                select(func.count(), func.min(forecast_cases.c.prediction_deadline_utc))
                .select_from(forecast_commits)
                .join(
                    forecast_cases, forecast_cases.c.id == forecast_commits.c.case_id
                )
                .outerjoin(
                    confirmation,
                    (confirmation.c.commit_id == forecast_commits.c.id)
                    & (confirmation.c.event_type == "durable_confirmation"),
                )
                .where(
                    confirmation.c.id.is_(None),
                    forecast_cases.c.prediction_deadline_utc < now,
                )
            )
        ).one()
        late = (
            await conn.execute(
                select(func.count()).where(
                    forecast_commit_events.c.event_type == "durable_confirmation",
                    forecast_commit_events.c.payload["timeliness"].as_string()
                    == "late",
                )
            )
        ).scalar_one()

        # cases whose exit passed long ago with no outcome head at all:
        # the resolver's grace policy (5 trading days) must have decided
        # them by now — 14 calendar days safely clears any holiday
        # stretch, so any occurrence means the scheduler is stuck
        unheaded = (
            await conn.execute(
                select(func.count(), func.min(forecast_cases.c.exit_at_utc))
                .select_from(forecast_cases)
                .where(
                    forecast_cases.c.exit_at_utc < now - timedelta(days=14),
                    ~exists(
                        select(1).where(outcome_revisions.c.case_id == forecast_cases.c.id)
                    ),
                )
            )
        ).one()

    return {
        "queue": {
            "queued_jobs": queued[0],
            "oldest_queued_age_seconds": _age_seconds(now, queued[1]),
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
        "ledger": {
            "unconfirmed_past_deadline": unconfirmed[0],
            "oldest_unconfirmed_age_seconds": _age_seconds(now, unconfirmed[1]),
            "late_confirmations": late,
            "unheaded_overdue_outcomes": unheaded[0],
            "oldest_unheaded_age_seconds": _age_seconds(now, unheaded[1]),
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

    ledger = snapshot.get("ledger", {})
    if ledger.get("unconfirmed_past_deadline", 0) > 0:
        alerts.append("ledger_unconfirmed_past_deadline")
    if ledger.get("unheaded_overdue_outcomes", 0) > 0:
        alerts.append("ledger_outcomes_not_resolved")

    return alerts


async def ops_status(engine: AsyncEngine, settings: Settings) -> dict:
    snapshot = await ops_snapshot(engine)
    alerts = evaluate_alerts(snapshot, settings)
    return {"status": "alert" if alerts else "ok", "alerts": alerts, **snapshot}
