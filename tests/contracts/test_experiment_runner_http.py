"""S08: Runner experiment surface (app layer, no Docker).

Control plane (aud=runner-exec, experiment:admin): register authorization
(bindings + limits + injected snapshot), terminate, read receipts.
Tool plane (aud=runner-tools, per-operation scopes): submit / status / read.

These tests pin: the full happy round trip with a fake executor, persistent
idempotency (same id+payload replays the original receipt; different payload
409), limit enforcement (count / concurrency / duration budget), terminate
propagation, **restart survival** (a new app over the same store dir reloads
receipts; a receipt left running becomes failed/interrupted and replays
idempotently), and the privilege separation matrix (tool tokens cannot reach
the control interfaces; control tokens cannot reach the tool endpoints).
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentComputationRequest,
    ExperimentLimits,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    SCOPE_EXPERIMENT_ADMIN,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    generate_research_keypair,
    sign_research_token,
)
from youwei_contracts.sandbox import ExecutionResult, SnapshotBundle

EXEC_CONFIG = "exp-test-v1"


@pytest.fixture(scope="module")
def keys():
    priv, pub = generate_research_keypair()
    return priv, pub


class Ctx:
    """One experiment's binding context + token minting."""

    def __init__(self, priv):
        self.priv = priv
        self.experiment_id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.attempt_id = uuid.uuid4()
        self.attempt_no = 1
        self.case_id = uuid.uuid4()
        self.evidence_sha256 = "e" * 64

    def authorization(self, **limit_overrides) -> ExperimentAuthorization:
        content = "security_id,trade_date,close\ns1,2026-09-20,101.5"
        snapshot = SnapshotBundle(
            snapshot_id=uuid.uuid4(),
            content=content,
            manifest={"content_sha256": hashlib.sha256(content.encode()).hexdigest()},
        )
        limits = dict(
            max_computations=3, max_concurrent=2,
            max_total_duration_seconds=600.0, max_artifact_bytes=1024 * 1024,
        )
        limits.update(limit_overrides)
        return ExperimentAuthorization(
            experiment_invocation_id=self.experiment_id,
            tenant_id=self.tenant_id, run_id=self.run_id, job_id=self.job_id,
            attempt_id=self.attempt_id, attempt_no=self.attempt_no,
            case_id=self.case_id, evidence_sha256=self.evidence_sha256,
            exec_config_version=EXEC_CONFIG,
            limits=ExperimentLimits(**limits), snapshot=snapshot,
        )

    def token(self, *, aud, scopes, experiment_id=None, **binding_overrides):
        binding = dict(
            invocation_id=experiment_id or self.experiment_id,
            tenant_id=self.tenant_id, run_id=self.run_id, job_id=self.job_id,
            attempt_no=self.attempt_no, case_id=self.case_id,
            evidence_sha256=self.evidence_sha256,
            exec_config_version=EXEC_CONFIG,
        )
        binding.update(binding_overrides)
        return sign_research_token(
            self.priv, kid="k1", aud=aud, scopes=scopes,
            exp=datetime.now(UTC) + timedelta(minutes=5), **binding,
        )

    def headers(self, *, aud=AUD_RUNNER_TOOLS, scopes=(SCOPE_EXPERIMENT_SUBMIT,),
                experiment_id=None, **binding_overrides):
        return {"Authorization": "Bearer " + self.token(
            aud=aud, scopes=scopes, experiment_id=experiment_id,
            **binding_overrides)}

    def computation(self, code="print('ratio')", **overrides) -> dict:
        base = dict(
            experiment_invocation_id=str(self.experiment_id),
            computation_id=str(uuid.uuid4()),
            code=code, argv=[], env={},
            expected_extensions=[".json"], timeout_seconds=30.0,
        )
        base.update(overrides)
        return base


def _app(keys, store_dir, *, executor=None):
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings

    settings = RunnerSettings(
        secret="test-runner-secret-at-least-32-bytes",
        development=True,
        agent_runtime_image="youwei-agent-runtime@sha256:" + "a" * 64,
        agent_runtime_gateway_url="http://gateway:8000",
        agent_runtime_public_keys={"k1": keys[1]},
        experiment_store_dir=str(store_dir),
        image="python:3.13-alpine",
    )
    return create_app(settings, executor=executor)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner")


def _artifact_result(exit_code=0, path="out.json", content='{"ratio": 1.32}'):
    data = content.encode()
    return ExecutionResult(
        exit_code=exit_code, stdout="done", stderr="",
        artifacts=[{
            "path": path, "extension": ".json", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "content": content,
        }],
        duration_seconds=0.1,
    )


async def _wait_terminal(client, ctx, computation_id, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(
            f"/v1/experiment-computations/{ctx.experiment_id}/{computation_id}",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_STATUS,)),
        )
        assert r.status_code == 200, r.text
        if r.json()["status"] != "running":
            return r.json()
        await asyncio.sleep(0.02)
    raise AssertionError("computation did not finish in time")


# --- full round trip ----------------------------------------------------------


async def test_register_submit_status_read_round_trip(keys, tmp_path):
    ctx = Ctx(keys[0])
    calls = {}

    async def executor(req):
        calls["script"] = req.script
        calls["snapshot"] = req.snapshot.content
        return _artifact_result()

    app = _app(keys, tmp_path / "store", executor=executor)
    async with _client(app) as client:
        reg = await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        assert reg.status_code == 200, reg.text

        comp = ctx.computation()
        sub = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=comp,
        )
        assert sub.status_code == 202, sub.text
        assert sub.json()["status"] == "running"

        final = await _wait_terminal(client, ctx, comp["computation_id"])
        assert final["status"] == "succeeded"
        assert final["artifacts"][0]["path"] == "out.json"

        # the sandbox got the Runner-INJECTED snapshot and the generated code
        assert calls["script"] == "print('ratio')"
        assert "security_id" in calls["snapshot"]

        read = await client.get(
            f"/v1/experiment-computations/{ctx.experiment_id}/{comp['computation_id']}"
            f"/artifacts/out.json",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_READ,)),
        )
        assert read.status_code == 200, read.text
        artifact = read.json()
        assert artifact["content"] == '{"ratio": 1.32}'
        assert artifact["sha256"] == hashlib.sha256(b'{"ratio": 1.32}').hexdigest()

        # receipts (control plane) carry the trusted execution evidence
        receipts = await client.get(
            f"/v1/experiment-authorizations/{ctx.experiment_id}/receipts",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
        )
        assert receipts.status_code == 200
        receipt = receipts.json()[0]
        assert receipt["status"] == "succeeded"
        assert receipt["code_sha256"] == hashlib.sha256(b"print('ratio')").hexdigest()
        assert receipt["image"] == "python:3.13-alpine"
        assert receipt["snapshot_sha256"]


# --- idempotency + limits ------------------------------------------------------


async def test_submit_is_idempotent_same_payload_and_conflicts_different(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(req):
        return _artifact_result()

    app = _app(keys, tmp_path / "store", executor=executor)
    async with _client(app) as client:
        await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        comp = ctx.computation()
        first = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )
        assert first.status_code == 202
        await _wait_terminal(client, ctx, comp["computation_id"])

        again = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )
        assert again.status_code == 200  # replay returns the original receipt
        assert again.json()["status"] == "succeeded"

        conflict = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=ctx.computation(
                computation_id=comp["computation_id"], code="print('other')"),
        )
        assert conflict.status_code == 409


async def test_limits_count_concurrency_duration(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def slow_executor(req):
        await asyncio.sleep(0.2)
        return _artifact_result()

    app = _app(keys, tmp_path / "store", executor=slow_executor)
    async with _client(app) as client:
        await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization(
                max_computations=2, max_concurrent=1, max_total_duration_seconds=25.0,
            ).model_dump(mode="json"),
        )
        # concurrency: first runs (slow), second overlaps -> 429
        c1 = ctx.computation(timeout_seconds=10.0)
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=c1,
        )).status_code == 202
        c2 = ctx.computation(timeout_seconds=10.0)
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=c2,
        )).status_code == 429
        await _wait_terminal(client, ctx, c1["computation_id"])
        # after c1 finishes, c2 fits (count budget 2)
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=c2,
        )).status_code == 202
        await _wait_terminal(client, ctx, c2["computation_id"])
        # count budget exhausted
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=ctx.computation(),
        )).status_code == 429
        # duration budget: remaining < requested timeout
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=ctx.computation(timeout_seconds=30.0),
        )).status_code == 429


async def test_terminate_rejects_new_submissions_and_cancels(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def slow_executor(req):
        await asyncio.sleep(5.0)
        return _artifact_result()

    app = _app(keys, tmp_path / "store", executor=slow_executor)
    async with _client(app) as client:
        await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        comp = ctx.computation()
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )).status_code == 202

        term = await client.delete(
            f"/v1/experiment-authorizations/{ctx.experiment_id}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
        )
        assert term.status_code == 200

        final = await _wait_terminal(client, ctx, comp["computation_id"])
        assert final["status"] == "cancelled"

        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=ctx.computation(),
        )).status_code == 403


# --- restart survival ------------------------------------------------------------


async def test_receipts_survive_app_restart_and_running_becomes_interrupted(keys, tmp_path):
    ctx = Ctx(keys[0])
    store_dir = tmp_path / "store"

    async def never_executor(req):
        await asyncio.sleep(60)
        return _artifact_result()

    app1 = _app(keys, store_dir, executor=never_executor)
    async with _client(app1) as client:
        await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        comp = ctx.computation()
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )).status_code == 202

    # "restart": a brand-new app over the same store dir; the receipt that
    # was left running reloads as failed(interrupted) and stays idempotent.
    async def fast_executor(req):
        return _artifact_result()

    app2 = _app(keys, store_dir, executor=fast_executor)
    async with _client(app2) as client:
        status = await client.get(
            f"/v1/experiment-computations/{ctx.experiment_id}/{comp['computation_id']}",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_STATUS,)),
        )
        assert status.status_code == 200
        assert status.json()["status"] == "failed"
        assert "interrupted" in (status.json()["error"] or "")

        # replaying the same id+payload returns the interrupted receipt —
        # never a silent re-execution
        replay = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == "failed"


# --- privilege separation (D1) ----------------------------------------------------


async def test_tool_token_cannot_reach_control_interfaces(keys, tmp_path):
    ctx = Ctx(keys[0])
    app = _app(keys, tmp_path / "store", executor=None)
    async with _client(app) as client:
        tool = ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,))
        # HMAC sandbox entry rejects an Ed25519 experiment token
        r = await client.post("/v1/executions", headers=tool, json={})
        assert r.status_code == 403
        # research invocation entry rejects the runner-tools audience
        r = await client.post("/v1/research-invocations", headers=tool, json={})
        assert r.status_code == 403


async def test_control_token_cannot_reach_tool_endpoints(keys, tmp_path):
    ctx = Ctx(keys[0])
    app = _app(keys, tmp_path / "store", executor=None)
    async with _client(app) as client:
        control = ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,))
        r = await client.post(
            "/v1/experiment-computations", headers=control, json=ctx.computation(),
        )
        assert r.status_code == 403  # wrong audience


async def test_scope_and_binding_enforced(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(req):
        return _artifact_result()

    app = _app(keys, tmp_path / "store", executor=executor)
    async with _client(app) as client:
        await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        # submit-only token cannot poll
        comp = ctx.computation()
        assert (await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)), json=comp,
        )).status_code == 202
        r = await client.get(
            f"/v1/experiment-computations/{ctx.experiment_id}/{comp['computation_id']}",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
        )
        assert r.status_code == 403  # missing experiment:status

        # a token bound to another experiment id is rejected
        r = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(
                scopes=(SCOPE_EXPERIMENT_SUBMIT,), experiment_id=uuid.uuid4(),
            ),
            json=ctx.computation(),
        )
        assert r.status_code == 403

        # unregistered experiment -> 404
        other = Ctx(keys[0])
        r = await client.post(
            "/v1/experiment-computations",
            headers=other.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=other.computation(),
        )
        assert r.status_code == 404


async def test_experiment_entry_disabled_answers_503(keys, tmp_path):
    from youwei_runner.app import create_app
    from youwei_runner.settings import RunnerSettings

    ctx = Ctx(keys[0])
    settings = RunnerSettings(
        secret="test-runner-secret-at-least-32-bytes",
        development=True,
        agent_runtime_image="youwei-agent-runtime@sha256:" + "a" * 64,
        agent_runtime_gateway_url="http://gateway:8000",
        agent_runtime_public_keys={"k1": keys[1]},
    )
    app = create_app(settings)
    async with _client(app) as client:
        r = await client.post(
            "/v1/experiment-computations",
            headers=ctx.headers(scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=ctx.computation(),
        )
        assert r.status_code == 503
