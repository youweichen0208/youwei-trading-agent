"""S02a: outbox publisher and the worker loop (composition root)."""

import asyncio
import uuid

from youwei_core.jobs.service import JobSubmission, RunSubmission, get_run_view, submit_run
from youwei_core.jobs.outbox import publish_pending_events
from youwei_core.worker.loop import WorkerLoop, noop_handler


async def _make_run(engine, tenant_id, *, kind="noop", n_jobs=1, max_attempts=1):
    submission = RunSubmission(
        kind="research",
        total_budget_micros=1_000_000,
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
