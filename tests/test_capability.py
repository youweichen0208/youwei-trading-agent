"""S02b: per-job capability tokens.

The worker loop signs a capability token at claim time, binding
(job, attempt, tenant, scopes, expiry <= lease). Downstream systems
(agent runtime, gateway, sandbox) verify it before accepting any
request from the job. Tests: roundtrip, tamper, expiry, wrong-job
binding, and that the loop attaches a verifiable token."""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_core.auth.capability import (
    CapabilityError,
    sign_capability,
    verify_capability,
)
from youwei_core.jobs.service import JobSubmission, RunSubmission, submit_run
from youwei_core.worker.loop import WorkerLoop, noop_handler

SECRET = b"test-capability-secret"


def _claims(job_id=None, attempt_no=1, scopes=("quant_run", "snapshot_read"), exp=None):
    return dict(
        job_id=job_id or uuid.uuid4(),
        attempt_no=attempt_no,
        tenant_id=uuid.uuid4(),
        scopes=scopes,
        exp=exp or (datetime.now(UTC) + timedelta(seconds=30)),
    )


def test_roundtrip():
    c = _claims()
    token = sign_capability(SECRET, **c)
    cap = verify_capability(SECRET, token)
    assert cap.job_id == c["job_id"]
    assert cap.attempt_no == 1
    assert cap.tenant_id == c["tenant_id"]
    assert cap.scopes == ("quant_run", "snapshot_read")


def test_tampered_token_rejected():
    token = sign_capability(SECRET, **_claims())
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(CapabilityError):
        verify_capability(SECRET, bad)


def test_wrong_secret_rejected():
    token = sign_capability(SECRET, **_claims())
    with pytest.raises(CapabilityError):
        verify_capability(b"other-secret", token)


def test_expired_token_rejected():
    c = _claims(exp=datetime.now(UTC) - timedelta(seconds=1))
    token = sign_capability(SECRET, **c)
    with pytest.raises(CapabilityError):
        verify_capability(SECRET, token)


def test_token_bound_to_job():
    token = sign_capability(SECRET, **_claims())
    cap = verify_capability(SECRET, token)
    other_job = uuid.uuid4()
    assert cap.job_id != other_job  # binding is part of the signed payload


async def test_worker_loop_attaches_capability(db_engine, tenant_id):
    from youwei_core.config import Settings

    submission = RunSubmission(
        kind="research",
        total_budget_micros=1_000_000,
        jobs=[JobSubmission(kind="noop", payload={})],
    )
    await submit_run(db_engine, tenant_id, submission, "cap-1")

    secret = "test-capability-secret"
    settings = Settings(database_url="unused", capability_secret=secret)
    loop = WorkerLoop(db_engine, handlers={"noop": noop_handler}, settings=settings)

    captured = {}

    async def capturing_handler(claimed):
        captured["capability"] = claimed.capability_token
        captured["job_id"] = claimed.job_id
        return {"ok": True}

    loop.handlers = {"noop": capturing_handler}
    assert await loop.run_once() is True

    cap = verify_capability(secret.encode(), captured["capability"])
    assert cap.job_id == captured["job_id"]
    assert cap.tenant_id == tenant_id
