"""S07m-2: Runner research-invocation endpoint (app layer, no Docker).

These tests exercise the research entry with a fake research_executor (no
Docker), pinning the Ed25519 authorization, invocation idempotency, binding,
and cancel semantics. The real docker run -i path is exercised on SG (S07m-3).
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.agent_runtime import (
    ResearchInvocationEnvelope,
    ResearchInvocationRequest,
    ResearchInvocationResult,
    ResearchRuntimeConfig,
)
from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_CANCEL,
    SCOPE_RESEARCH_RUN,
    SCOPE_RESEARCH_STATUS,
    generate_research_keypair,
    sign_research_token,
)


@pytest.fixture(scope="module")
def keys():
    priv, pub = generate_research_keypair()
    return priv, pub


def _evidence(tenant_id, run_id, case_id):
    import hashlib as _h, json as _j
    content = []
    content_sha = _h.sha256(
        _j.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return FrozenEvidence(
        contract_version="research-v1",
        run_id=run_id,
        tenant_id=tenant_id,
        case={
            "case_id": str(case_id),
            "security_id": str(uuid.uuid4()),
            "benchmark_security_id": str(uuid.uuid4()),
            "horizon_td": 20,
            "target_spec_id": "t",
            "target_spec_sha256": "t" * 64,
            "decision_cutoff_utc": "2026-10-01T12:00:00+00:00",
            "prediction_deadline_utc": "2026-10-01T13:00:00+00:00",
            "entry_at_utc": "2026-10-02T00:00:00+00:00",
            "exit_at_utc": "2026-10-30T00:00:00+00:00",
        },
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-10-01T12:00:00+00:00",
            "mode": "forward",
            "content_sha256": content_sha,
            "content": content,
            "manifest": {"content_sha256": content_sha},
        },
        target_policy_sha256="p" * 64,
        batch_manifest={"version": "1"},
    )


def _request(**overrides):
    tenant = uuid.uuid4()
    run = uuid.uuid4()
    case = uuid.uuid4()
    ev = _evidence(tenant, run, case)
    canonical = json.dumps(ev.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    base = dict(
        invocation_id=uuid.uuid4(),
        tenant_id=tenant,
        run_id=run,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=case,
        evidence=ev,
        evidence_sha256=hashlib.sha256(canonical).hexdigest(),
        exec_config_version="v1",
        config=ResearchRuntimeConfig(model="m"),
    )
    base.update(overrides)
    return ResearchInvocationRequest(**base)

def _proposal():
    """A minimal valid unavailable proposal (fixtures only need a well-formed
    output since the S08 contract requires proposal XOR experiment_request)."""
    from youwei_contracts.research import ResearchProposal

    return ResearchProposal(
        run_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        source_status="unavailable",
        reason="not_enabled",
    )


def _sign(priv, req, *, aud=AUD_RUNNER_EXEC, scopes=(SCOPE_RESEARCH_RUN,), exp=None):
    return sign_research_token(
        priv,
        kid="k1",
        aud=aud,
        invocation_id=req.invocation_id,
        tenant_id=req.tenant_id,
        run_id=req.run_id,
        job_id=req.job_id,
        attempt_no=req.attempt_no,
        case_id=req.case_id,
        evidence_sha256=req.evidence_sha256,
        exec_config_version=req.exec_config_version,
        scopes=scopes,
        exp=exp or (datetime.now(UTC) + timedelta(seconds=30)),
    )


def _app(keys, *, research_executor=None, **settings_overrides):
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings

    settings = dict(
        secret="test-runner-secret-at-least-32-bytes",
        development=True,
        agent_runtime_image="youwei-agent-runtime@sha256:" + "a" * 64,
        agent_runtime_gateway_url="http://gateway:8000",
        agent_runtime_public_keys={"k1": keys[1]},
    )
    settings.update(settings_overrides)
    return create_app(RunnerSettings(**settings), research_executor=research_executor)


def _headers(priv, req, *, aud=AUD_RUNNER_EXEC, scopes=(SCOPE_RESEARCH_RUN,), exp=None):
    return {"Authorization": "Bearer " + _sign(priv, req, aud=aud, scopes=scopes, exp=exp)}


def _envelope(priv, req):
    runtime_token = _sign(priv, req, aud=AUD_RUNTIME_RESEARCH, scopes=(SCOPE_RESEARCH_RUN,))
    return ResearchInvocationEnvelope(request=req, runtime_token=runtime_token).model_dump(mode="json")


async def test_research_submit_and_status_with_fake_executor(keys):
    priv, _ = keys
    seen = {}

    async def research_executor(req):
        seen["invocation_id"] = req.invocation_id
        return ResearchInvocationResult(
            ok=True, exit_code=0, image_digest="sha256:" + "a" * 64,
            proposal=_proposal(),
        )

    app = _app(keys, research_executor=research_executor)
    req = _request()
    headers = _headers(priv, req)
    path = f"/v1/research-invocations/{req.invocation_id}"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            resp = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert resp.status_code == 202
            for _ in range(100):
                status = await client.get(path, headers=_headers(priv, req, scopes=(SCOPE_RESEARCH_STATUS,)))
                if status.json()["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            assert status.json()["status"] == "succeeded"
            assert status.json()["result"]["ok"] is True
    assert seen["invocation_id"] == req.invocation_id


async def test_same_invocation_same_content_is_idempotent(keys):
    priv, _ = keys

    async def research_executor(req):
        return ResearchInvocationResult(ok=True, exit_code=0, image_digest="sha256:" + "a" * 64, proposal=_proposal())

    app = _app(keys, research_executor=research_executor)
    req = _request()
    headers = _headers(priv, req)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            first = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert first.status_code == 202
            replay = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert replay.status_code in (200, 202)


async def test_same_invocation_different_content_conflicts(keys):
    priv, _ = keys
    app = _app(keys)
    req = _request()
    headers = _headers(priv, req)
    # Different content, same invocation_id: 409.
    req2 = _request(invocation_id=req.invocation_id)
    headers2 = _headers(priv, req2)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            first = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert first.status_code == 202
            conflict = await client.post("/v1/research-invocations", json=_envelope(priv, req2), headers=headers2)
            assert conflict.status_code == 409


async def test_cross_tenant_or_wrong_case_rejected(keys):
    priv, _ = keys
    app = _app(keys)
    req = _request()
    # Sign a token for a DIFFERENT case than the request body.
    other_req = _request()
    bad_token = _sign(priv, other_req)  # bound to other_req's case
    headers = {"Authorization": "Bearer " + bad_token}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            resp = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert resp.status_code == 403


async def test_wrong_audience_rejected(keys):
    priv, _ = keys
    app = _app(keys)
    req = _request()
    # A runtime-research grant must not authorize the Runner entry (aud=runner-exec).
    headers = _headers(priv, req, aud=AUD_RUNTIME_RESEARCH)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            resp = await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)
            assert resp.status_code == 403


async def test_cancel_stops_execution(keys):
    priv, _ = keys
    started, stopped = asyncio.Event(), asyncio.Event()

    async def research_executor(req):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    app = _app(keys, research_executor=research_executor)
    req = _request()
    headers = _headers(priv, req)
    path = f"/v1/research-invocations/{req.invocation_id}"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            assert (await client.post("/v1/research-invocations", json=_envelope(priv, req), headers=headers)).status_code == 202
            await asyncio.wait_for(started.wait(), 1)
            cancel_resp = await client.delete(path, headers=_headers(priv, req, scopes=(SCOPE_RESEARCH_CANCEL,)))
            assert cancel_resp.status_code == 200
            await asyncio.wait_for(stopped.wait(), 2)
            assert cancel_resp.json()["status"] == "cancelled"


def test_research_entry_requires_gateway_and_digest():
    from youwei_runner.settings import RunnerSettings
    from pydantic import ValidationError
    # Production (development=False): digest required, gateway required.
    with pytest.raises(ValidationError):
        RunnerSettings(
            secret="test-runner-secret-at-least-32-bytes",
            agent_runtime_image="not-a-digest",
            agent_runtime_gateway_url="http://gateway:8000",
        )
    with pytest.raises(ValidationError):
        RunnerSettings(
            secret="test-runner-secret-at-least-32-bytes",
            agent_runtime_image="youwei-agent-runtime@sha256:" + "a" * 64,
            agent_runtime_gateway_url="",  # missing gateway
        )
