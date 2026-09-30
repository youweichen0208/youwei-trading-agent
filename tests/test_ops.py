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
    assert "wal_archive_stale" not in body["alerts"]


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


def _snap(**wal) -> dict:
    """Minimal healthy snapshot; WAL section overridable."""
    return {
        "queue": {"queued_jobs": 0, "oldest_queued_age_seconds": None},
        "events": {"unpublished": 0, "oldest_unpublished_age_seconds": None},
        "leases": {"expired_running_attempts": 0, "oldest_expired_age_seconds": None},
        "runs": {"over_deadline": 0, "oldest_over_deadline_age_seconds": None},
        "wal_archive": {
            "enabled": False,
            "archived_count": 0,
            "failed_count": 0,
            "last_archived_age_seconds": None,
            **wal,
        },
    }


def test_wal_archive_alert_only_when_enabled():
    disabled = _snap(enabled=False)
    assert "wal_archive_stale" not in evaluate_alerts(
        disabled, Settings(alert_wal_archive_stale_seconds=1800)
    )

    never_archived = _snap(enabled=True)
    assert "wal_archive_stale" in evaluate_alerts(
        never_archived, Settings(alert_wal_archive_stale_seconds=1800)
    )

    fresh = _snap(enabled=True, last_archived_age_seconds=30.0)
    assert "wal_archive_stale" not in evaluate_alerts(
        fresh, Settings(alert_wal_archive_stale_seconds=1800)
    )

    stale = _snap(enabled=True, last_archived_age_seconds=9999.0)
    assert "wal_archive_stale" in evaluate_alerts(
        stale, Settings(alert_wal_archive_stale_seconds=1800)
    )
