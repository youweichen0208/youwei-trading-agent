import uuid
import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest


async def test_runner_rejects_unauthenticated_execution():
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings

    app = create_app(RunnerSettings(secret="test-runner-secret-at-least-32-bytes", development=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
        response = await client.post("/v1/executions", json={})
    assert response.status_code == 401


def request_body():
    return {"contract_version": "sandbox-v1", "job_id": str(uuid.uuid4()),
            "run_id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()),
            "attempt_no": 1, "tenant_id": str(uuid.uuid4()), "script": "print('ok')"}


SECRET = "test-runner-secret-at-least-32-bytes"


def authorization(body, *, expires=None):
    from youwei_contracts.capability import sign_capability
    from youwei_contracts.sandbox import SandboxRequest, request_digest
    request = SandboxRequest.model_validate(body)
    return {"Authorization": "Bearer " + sign_capability(
        SECRET, job_id=request.job_id, attempt_no=request.attempt_no,
        tenant_id=request.tenant_id,
        scopes=("sandbox:execute", f"payload:{request_digest(request)}"),
        exp=expires or datetime.now(UTC) + timedelta(seconds=30))}


async def test_execution_is_bound_to_signed_payload_and_replay_returns_same_result():
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings
    from youwei_contracts.sandbox import ExecutionResult

    async def execute(request):
        return ExecutionResult(exit_code=0, stdout="ok\n", stderr="", artifacts=[], duration_seconds=0.1)

    app = create_app(RunnerSettings(secret=SECRET, development=True), executor=execute)
    body = request_body()
    headers = authorization(body)
    path = f"/v1/executions/{body['job_id']}/1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
        forged = await client.post("/v1/executions", json={**body, "script": "changed"}, headers=headers)
        assert forged.status_code == 403
        created = await client.post("/v1/executions", json=body, headers=headers)
        assert created.status_code == 202
        for _ in range(100):
            status = await client.get(path, headers=headers)
            if status.json()["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert status.json()["result"]["stdout"] == "ok\n"
        replay = await client.post("/v1/executions", json=body, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == status.json()
        different = {**body, "script": "another script"}
        conflict = await client.post("/v1/executions", json=different, headers=authorization(different))
        assert conflict.status_code == 409


@pytest.mark.parametrize("explicit_cancel", [True, False])
async def test_cancel_or_expired_controller_lease_stops_execution(explicit_cancel):
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings

    started, stopped = asyncio.Event(), asyncio.Event()

    async def execute(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    app = create_app(RunnerSettings(secret=SECRET, development=True), executor=execute)
    body = request_body()
    headers = authorization(body, expires=datetime.now(UTC) + timedelta(seconds=0.3))
    path = f"/v1/executions/{body['job_id']}/1"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            assert (await client.post("/v1/executions", json=body, headers=headers)).status_code == 202
            await asyncio.wait_for(started.wait(), 1)
            if explicit_cancel:
                assert (await client.delete(path, headers=headers)).status_code == 200
            await asyncio.wait_for(stopped.wait(), 2)
            response = await client.get(path, headers=authorization(body))
            assert response.json()["status"] == "cancelled"


def test_production_runner_requires_digest_and_gvisor():
    from youwei_runner.settings import RunnerSettings
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="digest"):
        RunnerSettings(secret=SECRET)
    with pytest.raises(ValidationError, match="runsc"):
        RunnerSettings(secret=SECRET, image="python@sha256:" + "a" * 64, runtime="")


async def test_core_client_rejects_a_result_for_another_request():
    from youwei_core.sandbox.client import RunnerClient, RunnerError
    from youwei_contracts.sandbox import SandboxRequest

    body = request_body()
    async def token():
        return authorization(body)["Authorization"][7:]

    def transport(request):
        return httpx.Response(200, json={
            "job_id": body["job_id"], "attempt_no": 1, "request_sha256": "wrong",
            "status": "succeeded", "result": {"exit_code": 0, "stdout": "", "stderr": "",
                                                   "artifacts": [], "duration_seconds": 0.1}})

    async with RunnerClient("http://runner", transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(RunnerError, match="binding"):
            await client.execute(SandboxRequest.model_validate(body), token)


async def test_runner_receipts_have_a_total_byte_budget():
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings
    from youwei_contracts.sandbox import ExecutionResult
    async def execute(request):
        return ExecutionResult(exit_code=0, stdout="x" * 10000, stderr="", artifacts=[], duration_seconds=0)
    app = create_app(RunnerSettings(secret=SECRET, development=True, max_cached_bytes=8192), executor=execute)
    body = request_body()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as client:
            assert (await client.post("/v1/executions", json=body, headers=authorization(body))).status_code == 202
            for _ in range(100):
                response = await client.get(f"/v1/executions/{body['job_id']}/1", headers=authorization(body))
                if response.json()["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            assert response.json()["status"] == "failed"
            assert "cache budget" in response.json()["error"]
            assert response.json()["result"] is None
