"""Authorize frozen input -> remote Runner -> fenced artifact persistence."""
import uuid
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from youwei_contracts.capability import sign_capability
from youwei_contracts.sandbox import SandboxRequest, SnapshotBundle, request_digest
from youwei_core.data.snapshots import read_snapshot
from youwei_core.db.meta import attempts, jobs, snapshots
from youwei_core.jobs.worker import ClaimedJob
from youwei_core.sandbox.client import RunnerClient
from youwei_core.sandbox.store import FencedArtifacts, store_artifacts


class SandboxPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field(min_length=1, max_length=1_000_000)
    snapshot_id: uuid.UUID | None = None
    argv: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict)


async def _active_lease(engine, claimed):
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(attempts.c.lease_expires_at).join(jobs, jobs.c.id == attempts.c.job_id)
            .where(attempts.c.id == claimed.attempt_id, attempts.c.attempt_no == claimed.attempt_no,
                   jobs.c.id == claimed.job_id, jobs.c.run_id == claimed.run_id,
                   jobs.c.tenant_id == claimed.tenant_id, jobs.c.status == "running",
                   jobs.c.attempt_count == claimed.attempt_no, attempts.c.status == "running",
                   attempts.c.lease_expires_at > func.clock_timestamp()))).first()
    if row is None:
        raise FencedArtifacts("runner request no longer authorized by an active attempt")
    return row.lease_expires_at


async def _authorized_snapshot(engine, claimed, snapshot_id):
    # Market snapshots without a creating attempt are global. Job snapshots
    # inherit their creating tenant. Runner never queries this database.
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(snapshots.c.created_by_attempt, jobs.c.tenant_id)
            .outerjoin(attempts, attempts.c.id == snapshots.c.created_by_attempt)
            .outerjoin(jobs, jobs.c.id == attempts.c.job_id)
            .where(snapshots.c.id == snapshot_id))).first()
    if row is None or (row.created_by_attempt is not None and row.tenant_id != claimed.tenant_id):
        raise FencedArtifacts("snapshot not authorized for this tenant")
    snap = await read_snapshot(engine, snapshot_id)
    return SnapshotBundle(snapshot_id=snapshot_id, content=snap["content"], manifest=snap["manifest"])


def make_sandbox_handler(engine: AsyncEngine, client: RunnerClient, *, secret: str):
    if len(secret) < 32:
        raise ValueError("configure a dedicated runner secret of at least 32 characters")

    async def handle_sandbox(claimed: ClaimedJob) -> dict:
        payload = SandboxPayload.model_validate(claimed.payload)
        await _active_lease(engine, claimed)
        snapshot = (await _authorized_snapshot(engine, claimed, payload.snapshot_id)
                    if payload.snapshot_id is not None else None)
        request = SandboxRequest(job_id=claimed.job_id, run_id=claimed.run_id,
                                 attempt_id=claimed.attempt_id, attempt_no=claimed.attempt_no,
                                 tenant_id=claimed.tenant_id, script=payload.script,
                                 argv=payload.argv, env=payload.env, snapshot=snapshot)
        digest = request_digest(request)

        async def token():
            expires = await _active_lease(engine, claimed)
            return sign_capability(secret, job_id=claimed.job_id, attempt_no=claimed.attempt_no,
                                   tenant_id=claimed.tenant_id,
                                   scopes=("sandbox:execute", f"payload:{digest}"), exp=expires)

        result = await client.execute(request, token)
        stored = await store_artifacts(
            engine, tenant_id=claimed.tenant_id, run_id=claimed.run_id, job_id=claimed.job_id,
            attempt_id=claimed.attempt_id, attempt_no=claimed.attempt_no, collected=result.artifacts)
        return {"exit_code": result.exit_code, "duration_seconds": result.duration_seconds,
                "stdout": result.stdout, "stderr": result.stderr,
                "snapshot": ({"snapshot_id": str(snapshot.snapshot_id),
                              "content_sha256": snapshot.manifest["content_sha256"]} if snapshot else None),
                "artifacts": [{"path": a.path, "size": a.size, "sha256": a.sha256} for a in result.artifacts],
                "artifact_ids": [str(i) for i in stored]}

    return handle_sandbox
