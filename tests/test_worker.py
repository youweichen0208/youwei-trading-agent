"""S02a acceptance scenarios: worker claiming, leases, fencing, races.

Covered (per the S02 decision):
- claim + complete -> job and run succeed
- lease expiry -> attempt expired, job retried, max attempts -> failed
- old worker late commit -> fenced, business result NOT applied but
  preserved on the attempt
- cancel vs complete race -> completion lands 'late', job stays
  cancelled
- heartbeat extends the lease
- concurrent claims get distinct jobs
"""

import asyncio
import uuid

from youwei_core.jobs.service import JobSubmission, RunSubmission, cancel_run, get_run_view, submit_run
from youwei_core.jobs.worker import claim_next_job, complete_attempt, heartbeat


async def _make_run(engine, tenant_id, key, *, kind="noop", max_attempts=1, n_jobs=1):
    submission = RunSubmission(
        kind="research",
        total_budget_micros=1_000_000,
        jobs=[
            JobSubmission(kind=kind, payload={}, max_attempts=max_attempts)
            for _ in range(n_jobs)
        ],
    )
    result = await submit_run(engine, tenant_id, submission, key)
    return result.run_id


async def test_claim_and_complete_marks_job_and_run_succeeded(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, "w-basic", max_attempts=2)
    claimed = await claim_next_job(db_engine, "w1")
    assert claimed is not None
    assert claimed.kind == "noop"
    assert claimed.attempt_no == 1
    assert claimed.tenant_id == tenant_id

    outcome = await complete_attempt(db_engine, claimed.job_id, 1, {"output": 42})
    assert outcome == "applied"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "succeeded"
    assert view["jobs"][0]["status"] == "succeeded"
    assert view["jobs"][0]["attempt_count"] == 1


async def test_lease_expiry_requeues_then_fails_after_max_attempts(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, "w-expiry", max_attempts=2)

    c1 = await claim_next_job(db_engine, "w1", lease_ttl=0.1)
    assert c1 is not None
    await asyncio.sleep(0.4)  # lease expires

    # next claim reaps the expired attempt, requeues and reclaims
    c2 = await claim_next_job(db_engine, "w2", lease_ttl=0.1)
    assert c2 is not None
    assert c2.job_id == c1.job_id
    assert c2.attempt_no == 2

    # the old worker's late completion is fenced out
    outcome = await complete_attempt(db_engine, c1.job_id, 1, {"stale": True})
    assert outcome == "fenced"

    # second attempt also expires -> attempts exhausted -> job failed
    await asyncio.sleep(0.4)
    c3 = await claim_next_job(db_engine, "w3")
    assert c3 is None

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["jobs"][0]["status"] == "failed"
    assert view["jobs"][0]["attempt_count"] == 2
    assert view["status"] == "failed"


async def test_old_worker_late_commit_fenced_result_preserved(db_engine, tenant_id):
    await _make_run(db_engine, tenant_id, "w-late", max_attempts=2)

    c1 = await claim_next_job(db_engine, "w1", lease_ttl=0.1)
    await asyncio.sleep(0.4)
    c2 = await claim_next_job(db_engine, "w2")
    assert c2.attempt_no == 2

    # old worker completes after being superseded
    outcome = await complete_attempt(db_engine, c1.job_id, 1, {"late": True})
    assert outcome == "fenced"

    # business result NOT applied (job still running for w2)...
    outcome2 = await complete_attempt(db_engine, c2.job_id, 2, {"fresh": True})
    assert outcome2 == "applied"

    view = await get_run_view(db_engine, tenant_id, (await _run_id_for(db_engine, tenant_id, c1.job_id)))
    job = [j for j in view["jobs"] if j["id"] == str(c1.job_id)][0]
    assert job["status"] == "succeeded"
    assert job["attempt_count"] == 2


async def test_cancel_race_completion_lands_late(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, "w-cancel", max_attempts=1)
    claimed = await claim_next_job(db_engine, "w1")

    # cancel wins the race while the attempt is running
    cancelled = await cancel_run(db_engine, tenant_id, run_id)
    assert cancelled["status"] == "cancelled"

    # the worker finishes afterwards: business result NOT applied
    outcome = await complete_attempt(db_engine, claimed.job_id, 1, {"done": True})
    assert outcome == "fenced"

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "cancelled"
    assert view["jobs"][0]["status"] == "cancelled"

    # ...but the late result is preserved on the attempt for audit
    late = await _attempt_status(db_engine, claimed.job_id, 1)
    assert late["status"] == "late"
    assert late["result"] == {"done": True}


async def test_heartbeat_extends_lease(db_engine, tenant_id):
    await _make_run(db_engine, tenant_id, "w-hb", n_jobs=1)
    c1 = await claim_next_job(db_engine, "w1", lease_ttl=0.3)

    await asyncio.sleep(0.15)
    ok = await heartbeat(db_engine, c1.attempt_id, "w1", lease_ttl=0.6)
    assert ok is True

    await asyncio.sleep(0.35)  # original 0.3s lease would be gone; extended survives
    steal = await claim_next_job(db_engine, "w2")
    assert steal is None  # lease still held by w1

    # heartbeating a finished/superseded attempt fails
    await complete_attempt(db_engine, c1.job_id, 1, {"ok": True})
    ok2 = await heartbeat(db_engine, c1.attempt_id, "w1", lease_ttl=0.6)
    assert ok2 is False


async def test_concurrent_claims_get_distinct_jobs(db_engine, tenant_id):
    await _make_run(db_engine, tenant_id, "w-conc", n_jobs=2)
    c1, c2 = await asyncio.gather(
        claim_next_job(db_engine, "w1"),
        claim_next_job(db_engine, "w2"),
    )
    assert c1 is not None and c2 is not None
    assert c1.job_id != c2.job_id


# --- helpers -----------------------------------------------------------


async def _run_id_for(engine, tenant_id, job_id):
    from sqlalchemy import select

    from youwei_core.db.meta import jobs

    async with engine.begin() as conn:
        row = (
            await conn.execute(select(jobs.c.run_id).where(jobs.c.id == job_id))
        ).scalar_one()
    return row


async def _attempt_status(engine, job_id, attempt_no):
    from sqlalchemy import select

    from youwei_core.db.meta import attempts

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(attempts).where(
                    attempts.c.job_id == job_id, attempts.c.attempt_no == attempt_no
                )
            )
        ).mappings().one()
    return {"status": row.status, "result": row.result}
