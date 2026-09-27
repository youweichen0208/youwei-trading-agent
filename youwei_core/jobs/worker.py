"""Worker-side job operations: claiming with leases, heartbeats,
completion/failure with fencing, and lease-expiry reaping.

Every public operation is one short transaction. attempt_no doubles as
the fencing token: business writes only apply when the attempt is the
job's current attempt and the job is still running."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import attempts, events, jobs, runs


@dataclass
class ClaimedJob:
    job_id: uuid.UUID
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int  # fencing token
    kind: str
    payload: dict
    lease_expires_at: datetime
    # signed by the worker loop (not by the DB claim): binds this exact
    # (job, attempt, tenant) with scopes and an expiry <= the lease
    capability_token: str | None = None


def _lease_expiry(lease_ttl: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=lease_ttl)


async def _reap_expired(conn) -> int:
    """Mark lease-expired attempts and requeue/fail their jobs.

    Runs inside the claim transaction: for S02a's single-worker
    baseline every claim doubles as a reaper pass.
    """
    expired = (
        await conn.execute(
            update(attempts)
            .where(
                attempts.c.status == "running",
                attempts.c.lease_expires_at < func.now(),
            )
            .values(status="expired", finished_at=func.now())
            .returning(attempts.c.job_id, attempts.c.attempt_no)
        )
    ).all()
    for job_id, attempt_no in expired:
        job = (
            await conn.execute(
                select(jobs).where(jobs.c.id == job_id).with_for_update()
            )
        ).mappings().one()
        if job.attempt_count >= job.max_attempts:
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="failed", updated_at=func.now())
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=job.tenant_id,
                    run_id=job.run_id,
                    job_id=job_id,
                    event_type="job.expired_final",
                    payload={"attempt_no": attempt_no},
                )
            )
            await _maybe_finish_run(conn, job.run_id)
        else:
            # requeue only while the run is still active; a cancelled
            # (or otherwise terminal) run takes its jobs down with it
            run_status = (
                await conn.execute(
                    select(runs.c.status).where(runs.c.id == job.run_id)
                )
            ).scalar_one()
            if run_status in ("pending", "running"):
                await conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.status == "running")
                    .values(status="queued", updated_at=func.now())
                )
                await conn.execute(
                    events.insert().values(
                        tenant_id=job.tenant_id,
                        run_id=job.run_id,
                        job_id=job_id,
                        event_type="job.requeued",
                        payload={"attempt_no": attempt_no, "reason": "lease_expired"},
                    )
                )
            else:
                await conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.status == "running")
                    .values(status="cancelled", updated_at=func.now())
                )
    return len(expired)


async def claim_next_job(
    engine: AsyncEngine,
    worker_id: str,
    *,
    lease_ttl: float = 30.0,
) -> ClaimedJob | None:
    """Claim the oldest queued job of a non-terminal run.

    Short transaction: reap expired attempts, then claim with
    FOR UPDATE SKIP LOCKED, then insert the attempt row. attempt_no is
    the incremented job counter (monotonic fencing token).
    """
    async with engine.begin() as conn:
        await _reap_expired(conn)

        candidate = (
            select(jobs.c.id)
            .where(
                jobs.c.status == "queued",
                jobs.c.run_id.in_(
                    select(runs.c.id).where(runs.c.status.in_(("pending", "running")))
                ),
            )
            .order_by(jobs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        claimed = (
            await conn.execute(
                update(jobs)
                .where(jobs.c.id.in_(candidate))
                .values(
                    status="running",
                    attempt_count=jobs.c.attempt_count + 1,
                    updated_at=func.now(),
                )
                .returning(
                    jobs.c.id,
                    jobs.c.run_id,
                    jobs.c.tenant_id,
                    jobs.c.kind,
                    jobs.c.payload,
                    jobs.c.attempt_count,
                )
            )
        ).mappings().first()

        if claimed is None:
            return None

        # first claim flips the run to running
        await conn.execute(
            update(runs)
            .where(runs.c.id == claimed.run_id, runs.c.status == "pending")
            .values(status="running", updated_at=func.now())
        )

        attempt_id = uuid.uuid4()
        lease_expires_at = _lease_expiry(lease_ttl)
        await conn.execute(
            attempts.insert().values(
                id=attempt_id,
                job_id=claimed.id,
                attempt_no=claimed.attempt_count,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                status="running",
            )
        )
        await conn.execute(
            events.insert().values(
                tenant_id=claimed.tenant_id,
                run_id=claimed.run_id,
                job_id=claimed.id,
                event_type="job.claimed",
                payload={"attempt_no": claimed.attempt_count, "worker_id": worker_id},
            )
        )

        return ClaimedJob(
            job_id=claimed.id,
            run_id=claimed.run_id,
            tenant_id=claimed.tenant_id,
            attempt_id=attempt_id,
            attempt_no=claimed.attempt_count,
            kind=claimed.kind,
            payload=claimed.payload,
            lease_expires_at=lease_expires_at,
        )


async def heartbeat(
    engine: AsyncEngine,
    attempt_id: uuid.UUID,
    worker_id: str,
    *,
    lease_ttl: float = 30.0,
) -> bool:
    """Extend the lease of a still-running attempt. False when the
    attempt is no longer running (superseded, expired, finished)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.status == "running",
                attempts.c.worker_id == worker_id,
            )
            .values(lease_expires_at=_lease_expiry(lease_ttl))
        )
        return result.rowcount == 1


async def complete_attempt(
    engine: AsyncEngine,
    job_id: uuid.UUID,
    attempt_no: int,
    result: dict[str, Any],
) -> str:
    """Commit a business result with fencing.

    Returns:
    - "applied": the attempt is current, the job was running and the
      lease is still valid -> job/run transition to succeeded, result
      stored.
    - "fenced": the attempt was superseded (lease expired, job
      cancelled, or a newer attempt exists) -> the business result is
      NOT applied, but preserved on the attempt with status 'late' for
      audit; real costs are still booked by the budget layer
      regardless of this outcome.
    """
    async with engine.begin() as conn:
        job = (
            await conn.execute(
                select(jobs).where(jobs.c.id == job_id).with_for_update()
            )
        ).mappings().first()
        if job is None:
            return "unknown-job"

        attempt = (
            await conn.execute(
                select(attempts)
                .where(attempts.c.job_id == job_id, attempts.c.attempt_no == attempt_no)
                .with_for_update()
            )
        ).mappings().first()
        if attempt is None:
            return "unknown-attempt"

        db_now = (await conn.execute(select(func.now()))).scalar_one()
        current = (
            job.attempt_count == attempt_no
            and job.status == "running"
            and attempt.lease_expires_at > db_now
        )
        if current:
            await conn.execute(
                update(attempts)
                .where(attempts.c.id == attempt.id)
                .values(status="succeeded", result=result, finished_at=func.now())
            )
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="succeeded", updated_at=func.now())
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=job.tenant_id,
                    run_id=job.run_id,
                    job_id=job_id,
                    event_type="job.succeeded",
                    payload={"attempt_no": attempt_no},
                )
            )
            await _maybe_finish_run(conn, job.run_id)
            return "applied"

        # fenced: keep the result for audit; do not touch job state
        await conn.execute(
            update(attempts)
            .where(attempts.c.id == attempt.id)
            .values(status="late", result=result, finished_at=func.now())
        )
        await conn.execute(
            events.insert().values(
                tenant_id=job.tenant_id,
                run_id=job.run_id,
                job_id=job_id,
                event_type="job.completion_late",
                payload={"attempt_no": attempt_no},
            )
        )
        return "fenced"


async def fail_attempt(
    engine: AsyncEngine,
    job_id: uuid.UUID,
    attempt_no: int,
    error: str,
) -> str:
    """Record an attempt failure with fencing. Retries the job when
    attempts remain; otherwise the job fails (and possibly the run)."""
    async with engine.begin() as conn:
        job = (
            await conn.execute(
                select(jobs).where(jobs.c.id == job_id).with_for_update()
            )
        ).mappings().first()
        if job is None:
            return "unknown-job"

        attempt = (
            await conn.execute(
                select(attempts)
                .where(attempts.c.job_id == job_id, attempts.c.attempt_no == attempt_no)
                .with_for_update()
            )
        ).mappings().first()
        if attempt is None:
            return "unknown-attempt"

        if (
            job.attempt_count != attempt_no
            or job.status != "running"
            or attempt.lease_expires_at
            <= (await conn.execute(select(func.now()))).scalar_one()
        ):
            await conn.execute(
                update(attempts)
                .where(attempts.c.id == attempt.id)
                .values(status="late", error=error, finished_at=func.now())
            )
            return "fenced"

        await conn.execute(
            update(attempts)
            .where(attempts.c.id == attempt.id)
            .values(status="failed", error=error, finished_at=func.now())
        )
        if job.attempt_count >= job.max_attempts:
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="failed", updated_at=func.now())
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=job.tenant_id,
                    run_id=job.run_id,
                    job_id=job_id,
                    event_type="job.failed",
                    payload={"attempt_no": attempt_no, "error": error[:500]},
                )
            )
            await _maybe_finish_run(conn, job.run_id)
        else:
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(status="queued", updated_at=func.now())
            )
            await conn.execute(
                events.insert().values(
                    tenant_id=job.tenant_id,
                    run_id=job.run_id,
                    job_id=job_id,
                    event_type="job.requeued",
                    payload={"attempt_no": attempt_no, "reason": "attempt_failed"},
                )
            )
        return "applied"


async def abandon_attempt(
    engine: AsyncEngine,
    job_id: uuid.UUID,
    attempt_no: int,
) -> str:
    """Mark a running attempt cancelled after its job was cancelled
    mid-flight (watchdog cancelled the handler). The job stays in its
    cancelled state; open budget reservations remain pending
    reconciliation (unknown cost)."""
    async with engine.begin() as conn:
        job = (
            await conn.execute(
                select(jobs).where(jobs.c.id == job_id).with_for_update()
            )
        ).mappings().first()
        if job is None:
            return "unknown-job"
        attempt = (
            await conn.execute(
                select(attempts)
                .where(attempts.c.job_id == job_id, attempts.c.attempt_no == attempt_no)
                .with_for_update()
            )
        ).mappings().first()
        if attempt is None:
            return "unknown-attempt"
        if attempt.status != "running":
            return attempt.status  # already terminal; nothing to abandon

        await conn.execute(
            update(attempts)
            .where(attempts.c.id == attempt.id)
            .values(status="cancelled", finished_at=func.now())
        )
        await conn.execute(
            events.insert().values(
                tenant_id=job.tenant_id,
                run_id=job.run_id,
                job_id=job_id,
                event_type="job.abandoned",
                payload={"attempt_no": attempt_no, "reason": "cancelled_midflight"},
            )
        )
        return "cancelled"


async def _maybe_finish_run(conn, run_id: uuid.UUID) -> None:
    """After a job reaches a terminal state: if no non-terminal jobs
    remain, transition the run (any failed -> failed, else succeeded).
    Cancelled runs stay cancelled."""
    run = (
        await conn.execute(select(runs).where(runs.c.id == run_id).with_for_update())
    ).mappings().one()
    if run.status not in ("pending", "running"):
        return
    remaining = (
        await conn.execute(
            select(func.count())
            .select_from(jobs)
            .where(
                jobs.c.run_id == run_id,
                jobs.c.status.in_(("queued", "running")),
            )
        )
    ).scalar_one()
    if remaining > 0:
        return
    any_failed = (
        await conn.execute(
            select(func.count())
            .select_from(jobs)
            .where(jobs.c.run_id == run_id, jobs.c.status == "failed")
        )
    ).scalar_one()
    final = "failed" if any_failed else "succeeded"
    await conn.execute(
        update(runs).where(runs.c.id == run_id).values(status=final, updated_at=func.now())
    )
    await conn.execute(
        events.insert().values(
            tenant_id=run.tenant_id,
            run_id=run_id,
            event_type=f"run.{final}",
            payload={},
        )
    )
