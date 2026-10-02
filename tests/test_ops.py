"""S02b closeout: ops health & alerting endpoints.

/v1/ops/status (admin key) answers the deployment's first-version
monitoring questions (architecture section 11) straight from the job
tables: queue backlog, unpublished outbox events, lease-expired
attempts not yet reaped, runs past their wall-clock deadline, and WAL
archive staleness. /healthz is an unauthenticated liveness probe with no
DB dependency, so a database blip does not get the API process killed by
a restart policy.

Alert thresholds come from Settings; count-based signals
(expired leases, overdue runs) alert on any occurrence.
"""

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient

from youwei_core.api.app import create_app
from youwei_core.config import Settings
from youwei_core.ops.service import evaluate_alerts

SUBMISSION = {
    "kind": "research",
    "jobs": [{"kind": "noop", "payload": {}}],
}


async def _strict_client(pg_url, admin_key):
    """App whose age thresholds are all zero: any nonzero age alerts."""
    settings = Settings(
        database_url=pg_url,
        admin_api_key=admin_key,
        alert_queue_backlog_age_seconds=0,
        alert_unpublished_events_age_seconds=0,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


async def _ops(client, admin_headers) -> dict:
    r = await client.get("/v1/ops/status", headers=admin_headers)
    assert r.status_code == 200
    return r.json()


# --- /healthz -----------------------------------------------------------


async def test_healthz_is_unauthenticated_liveness(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- access control -----------------------------------------------------


async def test_ops_status_requires_admin(client, tenant_headers):
    assert (await client.get("/v1/ops/status")).status_code == 401
    assert (await client.get("/v1/ops/status", headers=tenant_headers)).status_code == 403


# --- healthy empty system -----------------------------------------------


async def test_ops_status_empty_system_ok(client, admin_headers):
    body = await _ops(client, admin_headers)
    assert body["status"] == "ok"
    assert body["alerts"] == []
    assert body["queue"]["queued_jobs"] == 0
    assert body["queue"]["oldest_queued_age_seconds"] is None
    assert body["events"]["unpublished"] == 0
    assert body["leases"]["expired_running_attempts"] == 0
    assert body["runs"]["over_deadline"] == 0
    # the test container runs with archiving off: reported, not alerted
    assert body["wal_archive"]["enabled"] is False
    assert body["wal_archive"]["pending_wal_files"] == 0
    assert "wal_archive_stale" not in body["alerts"]
    assert "wal_archive_monitor_error" not in body["alerts"]


# --- reported signals ----------------------------------------------------


async def test_ops_status_reports_backlog_and_events(
    client, admin_headers, tenant_headers
):
    r = await client.post(
        "/v1/runs",
        headers={**tenant_headers, "Idempotency-Key": "ops-1"},
        json=SUBMISSION,
    )
    assert r.status_code == 201

    body = await _ops(client, admin_headers)
    assert body["queue"]["queued_jobs"] == 1
    assert body["queue"]["oldest_queued_age_seconds"] >= 0
    assert body["events"]["unpublished"] >= 1  # run.submitted is in the outbox
    # fresh rows, default thresholds: observed but not yet stale
    assert body["status"] == "ok"
    assert body["alerts"] == []


async def test_ops_status_reports_expired_leases(client, admin_headers, db_engine, tenant_id):
    from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
    from youwei_core.jobs.worker import claim_next_job

    submission = RunSubmission(
        kind="research",
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    await submit_run(db_engine, tenant_id, submission, "ops-leases")
    await claim_next_job(db_engine, "w1", lease_ttl=0.1)
    await asyncio.sleep(0.3)  # lease expires; no further claim reaps it

    body = await _ops(client, admin_headers)
    assert body["leases"]["expired_running_attempts"] == 1
    assert "leases_expired_not_reaped" in body["alerts"]
    assert body["status"] == "alert"


async def test_ops_status_reports_overdue_runs(client, admin_headers, db_engine, tenant_id):
    from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run

    submission = RunSubmission(
        kind="research",
        wall_clock_seconds=1,
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    await submit_run(db_engine, tenant_id, submission, "ops-overdue")
    await asyncio.sleep(1.2)  # deadline passes, no claim pass reaps it

    body = await _ops(client, admin_headers)
    assert body["runs"]["over_deadline"] == 1
    assert "runs_over_deadline_not_reaped" in body["alerts"]
    assert body["status"] == "alert"


# --- threshold alerts (strict settings app) ------------------------------


async def test_stale_ages_alert_under_strict_thresholds(pg_url, tenant_headers, admin_headers):
    async for client in _strict_client(pg_url, admin_headers["Authorization"].removeprefix("Bearer ")):
        r = await client.post(
            "/v1/runs",
            headers={**tenant_headers, "Idempotency-Key": "ops-strict"},
            json=SUBMISSION,
        )
        assert r.status_code == 201
        await asyncio.sleep(0.2)  # let the rows age past the 0s thresholds

        body = await _ops(client, admin_headers)
        assert body["status"] == "alert"
        assert "queue_backlog_stale" in body["alerts"]
        assert "unpublished_events_stale" in body["alerts"]


# --- WAL archive alert logic (not producible on the test container) -------
#
# The alert is driven by archive BACKLOG, not by wall-clock time since
# the last successful archive: an idle database produces no WAL and
# has nothing to archive, so a purely time-based rule fired forever on
# the idle production DB (owner decision 2026-10-02: fix the rule, do
# not raise the threshold).
#
#   wal_archive_stale         archiving on AND pending WAL exists AND
#                             the oldest pending file has waited past
#                             the threshold (or its age is unreadable
#                             — never silently "fresh")
#   wal_archive_monitor_error WAL metrics could not be read at all;
#                             an unreadable backlog is never treated
#                             as zero
#
# last_archived_age / counts / LSN / stats_reset stay in the snapshot
# as diagnostics only.

THRESHOLD = 1800


def _snap(**wal) -> dict:
    """Minimal healthy snapshot; WAL section overridable."""
    return {
        "queue": {"queued_jobs": 0, "oldest_queued_age_seconds": None},
        "events": {"unpublished": 0, "oldest_unpublished_age_seconds": None},
        "leases": {"expired_running_attempts": 0, "oldest_expired_age_seconds": None},
        "runs": {"over_deadline": 0, "oldest_over_deadline_age_seconds": None},
        "wal_archive": {
            "enabled": False,
            "pending_wal_files": 0,
            "oldest_pending_age_seconds": None,
            "archived_count": 0,
            "failed_count": 0,
            "last_archived_age_seconds": None,
            "stats_reset": None,
            "current_lsn": None,
            **wal,
        },
    }


def _alerts(snap):
    return evaluate_alerts(snap, Settings(alert_wal_archive_stale_seconds=THRESHOLD))


def test_wal_archive_idle_database_does_not_alert():
    """No pending WAL = nothing to archive, however long ago the last
    archive was (the idle production DB case; old rule fired forever)."""
    idle = _snap(enabled=True, pending_wal_files=0, last_archived_age_seconds=99999.0)
    assert "wal_archive_stale" not in _alerts(idle)
    assert "wal_archive_monitor_error" not in _alerts(idle)


def test_wal_archive_pending_within_grace_does_not_alert():
    fresh_backlog = _snap(
        enabled=True, pending_wal_files=3, oldest_pending_age_seconds=600.0
    )
    assert "wal_archive_stale" not in _alerts(fresh_backlog)


def test_wal_archive_pending_past_threshold_alerts():
    stuck = _snap(
        enabled=True, pending_wal_files=2, oldest_pending_age_seconds=2000.0
    )
    assert "wal_archive_stale" in _alerts(stuck)


def test_wal_archive_recovers_after_backlog_clears():
    """The very snapshot that used to be alerting stops alerting once
    the backlog is archived (delivery of RESOLVED stays with the
    polling layer's alert-set diff)."""
    stuck = _snap(
        enabled=True, pending_wal_files=2, oldest_pending_age_seconds=2000.0
    )
    assert "wal_archive_stale" in _alerts(stuck)
    recovered = _snap(enabled=True, pending_wal_files=0, last_archived_age_seconds=30.0)
    assert "wal_archive_stale" not in _alerts(recovered)


def test_wal_archive_pending_with_unknown_age_alerts_conservatively():
    """Pending WAL whose wait time could not be determined is treated
    as stale — an unreadable age must not read as fresh."""
    unknown = _snap(enabled=True, pending_wal_files=1, oldest_pending_age_seconds=None)
    assert "wal_archive_stale" in _alerts(unknown)


def test_wal_archive_disabled_is_never_alerted():
    disabled = _snap(
        enabled=False, pending_wal_files=5, oldest_pending_age_seconds=99999.0
    )
    assert "wal_archive_stale" not in _alerts(disabled)


def test_wal_archive_monitor_error_alerts_and_never_means_zero_backlog():
    """Unreadable WAL metrics are their own alert; the backlog is
    unknown, not zero, and must not be reported as healthy."""
    unreadable = _snap(enabled=True, monitor_error="permission denied for function")
    alerts = _alerts(unreadable)
    assert "wal_archive_monitor_error" in alerts
    assert "wal_archive_stale" not in alerts
