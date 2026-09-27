"""Artifact storage with fencing (S03): the restricted upload path.

The Core receives validated artifacts from the remote Runner through
this function only; it validates that the producing attempt is still
the job's current running attempt before writing — a late or fenced
worker's artifacts are recorded nowhere but its own result payload.
Rows are append-only (DB trigger).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.db.meta import artifacts, attempts, events, jobs
from youwei_contracts.sandbox import Artifact


class FencedArtifacts(Exception):
    """The producing attempt is no longer current; nothing stored."""


async def store_artifacts(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    attempt_no: int,
    collected: list[Artifact],
) -> list[uuid.UUID]:
    if not collected:
        return []
    async with engine.begin() as conn:
        # Same lock order as job completion: job, then attempt.
        await conn.execute(select(jobs.c.id).where(jobs.c.id == job_id).with_for_update())
        attempt = (
            await conn.execute(
                select(
                    attempts.c.status,
                    attempts.c.lease_expires_at,
                    jobs.c.attempt_count,
                    jobs.c.status.label("job_status"),
                    jobs.c.tenant_id,
                )
                .select_from(attempts)
                .join(jobs, jobs.c.id == attempts.c.job_id)
                .where(
                    attempts.c.id == attempt_id,
                    attempts.c.attempt_no == attempt_no,
                    jobs.c.id == job_id,
                    jobs.c.run_id == run_id,
                )
                .with_for_update(of=attempts)
            )
        ).mappings().one_or_none()
        db_now = (await conn.execute(select(func.clock_timestamp()))).scalar_one()
        if (
            attempt is None
            or attempt.attempt_count != attempt_no
            or attempt.job_status != "running"
            or attempt.status != "running"
            or attempt.lease_expires_at <= db_now
            or attempt.tenant_id != tenant_id
        ):
            raise FencedArtifacts(
                f"attempt {attempt_no} is not the job's current running "
                "attempt; artifacts not stored"
            )

        ids = []
        for art in collected:
            artifact_id = uuid.uuid4()
            ids.append(artifact_id)
            await conn.execute(
                artifacts.insert().values(
                    id=artifact_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    attempt_no=attempt_no,
                    path=art.path,
                    extension=art.extension,
                    size_bytes=art.size,
                    content_sha256=art.sha256,
                    content=art.content,
                )
            )
        await conn.execute(
            events.insert().values(
                tenant_id=tenant_id,
                run_id=run_id,
                job_id=job_id,
                event_type="sandbox.artifacts_stored",
                payload={
                    "attempt_no": attempt_no,
                    "count": len(collected),
                    "paths": [a.path for a in collected],
                },
            )
        )
    return ids


async def get_artifact(
    engine: AsyncEngine, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> dict | None:
    """Tenant-scoped artifact read."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(artifacts).where(
                    artifacts.c.id == artifact_id,
                    artifacts.c.tenant_id == tenant_id,
                )
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "job_id": str(row.job_id),
        "attempt_no": row.attempt_no,
        "path": row.path,
        "extension": row.extension,
        "size_bytes": row.size_bytes,
        "content_sha256": row.content_sha256,
        "content": row.content,
        "created_at": row.created_at.isoformat(),
    }
