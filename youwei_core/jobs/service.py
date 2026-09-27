"""Run/job lifecycle services: submission with tenant-bound idempotency,
queries, cancellation. Each public operation is one short transaction."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import func

from youwei_core.db.meta import events, jobs, runs, tenants


class JobSubmission(BaseModel):
    kind: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)
    max_attempts: int = Field(default=1, ge=1, le=10)


class RunSubmission(BaseModel):
    kind: str = Field(min_length=1, max_length=100)
    total_budget_micros: int = Field(ge=0)
    wall_clock_seconds: int | None = Field(default=None, ge=1)
    jobs: list[JobSubmission] = Field(min_length=1, max_length=1000)


class IdempotencyConflict(Exception):
    """Same idempotency key with a different payload."""


class RunNotFound(Exception):
    pass


@dataclass
class SubmitResult:
    run_id: uuid.UUID
    created: bool


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


async def _ensure_tenant(conn, tenant_id: uuid.UUID) -> None:
    # S02a convenience: tenant rows are trivially auto-created; real
    # identity management arrives in S02b.
    await conn.execute(
        pg_insert(tenants)
        .values(id=tenant_id, slug=f"tenant-{tenant_id}")
        .on_conflict_do_nothing(index_elements=[tenants.c.id])
    )


async def submit_run(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    submission: RunSubmission,
    idempotency_key: str,
) -> SubmitResult:
    """Idempotent run submission.

    Same (tenant, key) + same payload  -> existing run, created=False.
    Same (tenant, key) + new payload   -> IdempotencyConflict.
    Concurrent same-key submits        -> unique constraint picks a winner;
                                         the loser re-reads and replies.
    """
    payload_sha = sha256_hex(submission.model_dump(mode="json"))

    try:
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(runs).where(
                        runs.c.tenant_id == tenant_id,
                        runs.c.idempotency_key == idempotency_key,
                    )
                )
            ).mappings().first()
            if existing is not None:
                if existing.idempotency_payload_sha256 != payload_sha:
                    raise IdempotencyConflict(str(existing.id))
                return SubmitResult(run_id=existing.id, created=False)

            await _ensure_tenant(conn, tenant_id)

            run_id = uuid.uuid4()
            wall_clock_deadline = None
            if submission.wall_clock_seconds is not None:
                wall_clock_deadline = func.now() + timedelta(
                    seconds=submission.wall_clock_seconds
                )

            await conn.execute(
                runs.insert().values(
                    id=run_id,
                    tenant_id=tenant_id,
                    kind=submission.kind,
                    idempotency_key=idempotency_key,
                    idempotency_payload_sha256=payload_sha,
                    status="pending",
                    total_budget_micros=submission.total_budget_micros,
                    wall_clock_deadline=wall_clock_deadline,
                )
            )
            for job_spec in submission.jobs:
                await conn.execute(
                    jobs.insert().values(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        tenant_id=tenant_id,
                        kind=job_spec.kind,
                        payload=job_spec.payload,
                        status="queued",
                        max_attempts=job_spec.max_attempts,
                    )
                )
            await conn.execute(
                events.insert().values(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type="run.submitted",
                    payload={
                        "kind": submission.kind,
                        "job_count": len(submission.jobs),
                        "total_budget_micros": submission.total_budget_micros,
                    },
                )
            )
        return SubmitResult(run_id=run_id, created=True)
    except IntegrityError:
        # Lost a concurrent insert on the same idempotency key: the
        # winner's row now exists; re-read it and reply idempotently.
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(runs).where(
                        runs.c.tenant_id == tenant_id,
                        runs.c.idempotency_key == idempotency_key,
                    )
                )
            ).mappings().one()
        if existing.idempotency_payload_sha256 != payload_sha:
            raise IdempotencyConflict(str(existing.id)) from None
        return SubmitResult(run_id=existing.id, created=False)


async def get_run_view(engine: AsyncEngine, tenant_id: uuid.UUID, run_id: uuid.UUID) -> dict:
    async with engine.begin() as conn:
        run = (
            await conn.execute(
                select(runs).where(runs.c.id == run_id, runs.c.tenant_id == tenant_id)
            )
        ).mappings().first()
        if run is None:
            raise RunNotFound(str(run_id))
        job_rows = (
            (
                await conn.execute(
                    select(jobs).where(jobs.c.run_id == run_id).order_by(jobs.c.created_at)
                )
            )
            .mappings()
            .all()
        )
    return {
        "id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "total_budget_micros": run.total_budget_micros,
        "reserved_micros": run.reserved_micros,
        "settled_micros": run.settled_micros,
        "created_at": run.created_at.isoformat(),
        "jobs": [
            {
                "id": str(j.id),
                "kind": j.kind,
                "status": j.status,
                "attempt_count": j.attempt_count,
                "max_attempts": j.max_attempts,
            }
            for j in job_rows
        ],
    }


async def cancel_run(engine: AsyncEngine, tenant_id: uuid.UUID, run_id: uuid.UUID) -> dict:
    """Cancel a run.

    Race rules (S02a):
    - all non-terminal jobs (queued AND running) -> cancelled immediately
    - running attempts discover this at their next side-effect commit:
      their completion is fenced and lands as 'late' (business result NOT
      applied, preserved on the attempt; real costs still booked)
    - cancelling a terminal run is a no-op returning current state
    """
    async with engine.begin() as conn:
        run = (
            await conn.execute(
                select(runs)
                .where(runs.c.id == run_id, runs.c.tenant_id == tenant_id)
                .with_for_update()
            )
        ).mappings().first()
        if run is None:
            raise RunNotFound(str(run_id))
        if run.status in ("succeeded", "failed", "cancelled"):
            return {"id": str(run.id), "status": run.status, "already_terminal": True}

        await conn.execute(
            update(runs)
            .where(runs.c.id == run_id)
            .values(status="cancelled", updated_at=func.now())
        )
        await conn.execute(
            update(jobs)
            .where(jobs.c.run_id == run_id, jobs.c.status.in_(("queued", "running")))
            .values(status="cancelled", updated_at=func.now())
        )
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                run_id=run_id,
                event_type="run.cancelled",
                payload={},
            )
        )
        return {"id": str(run_id), "status": "cancelled", "already_terminal": False}


async def list_events(
    engine: AsyncEngine, tenant_id: uuid.UUID, run_id: uuid.UUID, after_seq: int = 0
) -> list[dict]:
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    select(events)
                    .where(
                        events.c.tenant_id == tenant_id,
                        events.c.run_id == run_id,
                        events.c.seq > after_seq,
                    )
                    .order_by(events.c.seq)
                )
            )
            .mappings()
            .all()
        )
    return [
        {
            "seq": r.seq,
            "event_type": r.event_type,
            "payload": r.payload,
            "occurred_at": r.occurred_at.isoformat(),
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for r in rows
    ]
