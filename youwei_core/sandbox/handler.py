"""The `sandbox.execute` job handler: spool -> restricted execution ->
validated artifacts (S03).

The job payload carries only untrusted text (script, argv, env) and a
snapshot_id to materialize — never an image, mount, network mode or
host path. Infrastructure failures (timeout, kill, artifact
violations) fail the job; the script's own nonzero exit code is a
result, not an infrastructure failure.
"""

import tempfile
import uuid
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.jobs.worker import ClaimedJob
from youwei_core.sandbox.runner import (
    SandboxConfig,
    cleanup_spool,
    materialize_snapshot,
    run_sandbox,
)
from youwei_core.sandbox.store import store_artifacts


class SandboxPayload(BaseModel):
    script: str = Field(min_length=1, max_length=1_000_000)
    snapshot_id: uuid.UUID | None = None
    argv: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict)


def make_sandbox_handler(engine: AsyncEngine, config: SandboxConfig | None = None):
    """Build the `sandbox.execute` job handler around an injected
    engine and runner configuration."""

    async def handle_sandbox(claimed: ClaimedJob) -> dict:
        payload = SandboxPayload.model_validate(claimed.payload)
        cfg = config or SandboxConfig()

        with tempfile.TemporaryDirectory(prefix="youwei-sbx-") as spool:
            spool_path = Path(spool)
            inputs = spool_path / "inputs"
            inputs.mkdir()
            materialized = None
            if payload.snapshot_id is not None:
                materialized = await materialize_snapshot(
                    engine, payload.snapshot_id, inputs
                )
            (inputs / "job_script.py").write_text(payload.script, encoding="utf-8")

            try:
                result = await run_sandbox(
                    cfg,
                    inputs_dir=inputs,
                    argv=payload.argv,
                    env=payload.env,
                )
                stored = await store_artifacts(
                    engine,
                    tenant_id=claimed.tenant_id,
                    run_id=claimed.run_id,
                    job_id=claimed.job_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    collected=result.artifacts,
                )
            finally:
                cleanup_spool(spool_path)

        return {
            "exit_code": result.exit_code,
            "duration_seconds": round(result.duration_seconds, 3),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "snapshot": materialized,
            "artifacts": [
                {"path": a.path, "size": a.size, "sha256": a.sha256}
                for a in result.artifacts
            ],
            "artifact_ids": [str(i) for i in stored],
        }

    return handle_sandbox
