"""S02a: outbox publisher and the worker loop (composition root).
S02b: cancellation propagates from a cancelled run to the in-flight
handler (watchdog cancels the handler task; the attempt lands
'cancelled')."""

import asyncio
import uuid

import pytest

from youwei_core.jobs.service import (
    JobSubmission,
    RunSubmission,
    cancel_run,
    get_run_view,
    submit_run,
)
from youwei_core.jobs.outbox import publish_pending_events
from youwei_core.worker.loop import WorkerLoop, noop_handler


async def _make_run(engine, tenant_id, *, kind="noop", n_jobs=1, max_attempts=1):
    submission = RunSubmission(
        kind="research",
        jobs=[
            JobSubmission(kind=kind, payload={}, max_attempts=max_attempts)
            for _ in range(n_jobs)
        ],
    )
    result = await submit_run(engine, tenant_id, submission, f"lp-{uuid.uuid4().hex[:8]}")
    return result.run_id


async def test_worker_loop_drains_queue_and_succeeds(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, n_jobs=2)

    loop = WorkerLoop(db_engine, handlers={"noop": noop_handler})
    ran1 = await loop.run_once()
    ran2 = await loop.run_once()
    ran3 = await loop.run_once()
    assert (ran1, ran2, ran3) == (True, True, False)

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "succeeded"
    assert all(j["status"] == "succeeded" for j in view["jobs"])


async def test_handler_failure_fails_attempt(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, kind="boom", max_attempts=1)

    async def boom_handler(claimed):
        raise RuntimeError("upstream exploded")

    loop = WorkerLoop(db_engine, handlers={"boom": boom_handler})
    assert await loop.run_once() is True
    assert await loop.run_once() is False

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["jobs"][0]["status"] == "failed"
    assert view["status"] == "failed"


async def test_unknown_kind_fails_attempt(db_engine, tenant_id):
    run_id = await _make_run(db_engine, tenant_id, kind="mystery")
    loop = WorkerLoop(db_engine, handlers={})  # no handlers at all
    assert await loop.run_once() is True

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["jobs"][0]["status"] == "failed"


async def test_publisher_marks_events_in_order(db_engine, tenant_id, client, tenant_headers):
    run_id = await _make_run(db_engine, tenant_id)
    loop = WorkerLoop(db_engine, handlers={"noop": noop_handler})
    await loop.run_once()

    marked = await publish_pending_events(db_engine)
    assert marked >= 3  # run.submitted + job.claimed + job.succeeded + run.succeeded

    ev = await client.get(f"/v1/runs/{run_id}/events", headers=tenant_headers)
    events = ev.json()["events"]
    assert all(e["published_at"] is not None for e in events)

    # idempotent: nothing left to mark
    assert await publish_pending_events(db_engine) == 0


async def test_cancel_propagates_to_running_handler(db_engine, tenant_id):
    """Cancel a run while its handler is executing: the watchdog
    cancels the handler task, the attempt lands 'cancelled', and the
    handler never completes."""
    from youwei_core.config import Settings
    from youwei_core.db.meta import attempts, jobs
    from sqlalchemy import select

    run_id = await _make_run(db_engine, tenant_id)
    settings = Settings(
        database_url="unused",
        heartbeat_interval_seconds=0.05,
        lease_ttl_seconds=5.0,
    )
    handler_entered = asyncio.Event()

    async def slow_handler(claimed):
        handler_entered.set()
        await asyncio.sleep(30)
        return {"never": True}

    loop = WorkerLoop(db_engine, handlers={"noop": slow_handler}, settings=settings)
    run_task = asyncio.create_task(loop.run_once())

    # wait until the attempt is running
    for _ in range(100):
        await asyncio.sleep(0.05)
        async with db_engine.begin() as conn:
            status = (
                await conn.execute(select(attempts.c.status))
            ).scalar_one_or_none()
            job_status = (
                await conn.execute(select(jobs.c.status))
            ).scalar_one_or_none()
        if status == "running" and job_status == "running":
            break
    else:
        raise AssertionError("attempt never started")
    assert await asyncio.wait_for(handler_entered.wait(), timeout=5)

    # cancel the run mid-flight
    await cancel_run(db_engine, tenant_id, run_id)

    # the watchdog should cancel the handler; run_once returns
    # Shield: a wait_for timeout must not itself cancel the handler and make
    # the cancellation test pass without a functioning watchdog.
    assert await asyncio.wait_for(asyncio.shield(run_task), timeout=2) is True

    # attempt cancelled (not late/succeeded), handler never completed
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(select(attempts).order_by(attempts.c.attempt_no))
        ).mappings().first()
    assert row["status"] == "cancelled"
    assert row["result"] is None

    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "cancelled"


async def test_failed_lease_renewal_leaves_active_job_recoverable(db_engine, tenant_id):
    """A real database rejection of heartbeat must not strand a running job."""
    from sqlalchemy import select, text

    from youwei_core.config import Settings
    from youwei_core.db.meta import attempts

    run_id = await _make_run(db_engine, tenant_id, max_attempts=2)
    settings = Settings(heartbeat_interval_seconds=0.05, lease_ttl_seconds=0.3)

    async def waiting_handler(claimed):
        await asyncio.Event().wait()

    loop = WorkerLoop(db_engine, handlers={"noop": waiting_handler}, settings=settings)
    async with db_engine.begin() as conn:
        await conn.execute(text("""
            CREATE FUNCTION reject_test_lease_renewal() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'injected lease update rejection'; END;
            $$ LANGUAGE plpgsql
        """))
        await conn.execute(text("""
            CREATE TRIGGER reject_test_lease_renewal
            BEFORE UPDATE OF lease_expires_at ON attempts
            FOR EACH ROW EXECUTE FUNCTION reject_test_lease_renewal()
        """))
    task = asyncio.create_task(loop.run_once())
    try:
        assert await asyncio.wait_for(asyncio.shield(task), timeout=2) is True
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with db_engine.begin() as conn:
            await conn.execute(text("DROP TRIGGER reject_test_lease_renewal ON attempts"))
            await conn.execute(text("DROP FUNCTION reject_test_lease_renewal()"))

    # Recovery is the normal lease-expiry path after the database is available.
    await asyncio.sleep(0.35)
    recovery = WorkerLoop(db_engine, handlers={"noop": noop_handler})
    assert await recovery.run_once() is True
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "succeeded"
    async with db_engine.begin() as conn:
        states = (await conn.execute(
            select(attempts.c.status).order_by(attempts.c.attempt_no)
        )).scalars().all()
    assert states == ["expired", "succeeded"]


async def test_worker_shutdown_propagates_and_preserves_lease_recovery(db_engine, tenant_id):
    from youwei_core.config import Settings

    run_id = await _make_run(db_engine, tenant_id, max_attempts=2)
    entered = asyncio.Event()

    async def waiting_handler(claimed):
        entered.set()
        await asyncio.Event().wait()

    loop = WorkerLoop(
        db_engine, handlers={"noop": waiting_handler},
        settings=Settings(heartbeat_interval_seconds=0.05, lease_ttl_seconds=0.3),
    )
    task = asyncio.create_task(loop.run_once())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.35)
    recovery = WorkerLoop(db_engine, handlers={"noop": noop_handler})
    assert await recovery.run_once() is True
    view = await get_run_view(db_engine, tenant_id, run_id)
    assert view["status"] == "succeeded"
