"""S08c-2: Runner experiment instance dispatch (app layer, no Docker).

Control plane (aud=runner-exec): the Controller dispatches ONE experiment
instance per experiment (keyed by experiment_invocation_id) with scope
experiment:run and polls it with experiment:run_status. These tests pin the
dispatch lifecycle, idempotency, the registration precondition, terminate
propagation to a running instance, and the privilege matrix (tool tokens and
admin grants cannot dispatch; dispatch grants cannot reach other surfaces).
"""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.experiment import (
    ExperimentInvocationEnvelope,
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentRequest,
    ExperimentResult,
    ExperimentResultComputation,
    ExperimentRuntimeConfig,
    experiment_invocation_digest,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    SCOPE_EXPERIMENT_ADMIN,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_RUN_STATUS,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    generate_research_keypair,
    sign_research_token,
)
from youwei_contracts.sandbox import SnapshotBundle

from test_experiment_runner_http import Ctx, _app, _client


@pytest.fixture(scope="module")
def keys():
    priv, pub = generate_research_keypair()
    return priv, pub


def _invocation_request(ctx: Ctx) -> ExperimentInvocationRequest:
    return ExperimentInvocationRequest(
        experiment_invocation_id=ctx.experiment_id,
        tenant_id=ctx.tenant_id,
        run_id=ctx.run_id,
        job_id=ctx.job_id,
        attempt_no=ctx.attempt_no,
        case_id=ctx.case_id,
        evidence_sha256=ctx.evidence_sha256,
        exec_config_version=ctx.authorization().exec_config_version,
        question=ExperimentRequest(
            question="rolling realized vol ratio quantile",
            motivation="not covered by the quant library",
            requested_shape="{ratio: float, quantile: float}",
        ),
        snapshot_manifest={"snapshot_id": "s1", "row_count": 120},
        config=ExperimentRuntimeConfig(model="glm-5.3"),
    )


def _instance_result(ok=True) -> ExperimentInvocationResult:
    code = "print('ratio')"
    return ExperimentInvocationResult(
        ok=ok,
        result=(
            ExperimentResult(
                findings="the ratio sits at its median",
                warnings=[],
                computations=[
                    ExperimentResultComputation(
                        computation_id=uuid.uuid4(),
                        code=code,
                        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                        status="succeeded",
                    )
                ],
            )
            if ok
            else None
        ),
        error=None if ok else "model produced no findings",
        exit_code=0 if ok else 1,
        image_digest="sha256:" + "a" * 64,
    )


async def _register(client, ctx):
    reg = await client.post(
        "/v1/experiment-authorizations",
        headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
        json=ctx.authorization().model_dump(mode="json"),
    )
    assert reg.status_code == 200, reg.text


async def _submit_instance(client, ctx, request=None, *, scopes=(SCOPE_EXPERIMENT_RUN,)):
    request = request or _invocation_request(ctx)
    envelope = ExperimentInvocationEnvelope(
        request=request,
        runtime_token=ctx.token(
            aud="runtime-experiment", scopes=(SCOPE_EXPERIMENT_RUN,)
        ),
        tool_token=ctx.token(
            aud=AUD_RUNNER_TOOLS,
            scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ),
        ),
    )
    response = await client.post(
        "/v1/experiment-invocations",
        headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=scopes),
        json=envelope.model_dump(mode="json"),
    )
    return request, response


async def _poll_terminal(client, ctx, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(
            f"/v1/experiment-invocations/{ctx.experiment_id}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN_STATUS,)),
        )
        assert r.status_code == 200, r.text
        if r.json()["status"] != "running":
            return r.json()
        await asyncio.sleep(0.02)
    raise AssertionError("experiment invocation did not finish in time")


# --- lifecycle -----------------------------------------------------------------


async def test_dispatch_round_trip_with_registered_experiment(keys, tmp_path):
    ctx = Ctx(keys[0])
    seen = {}

    async def executor(request, runtime_token, tool_token):
        seen["request"] = request
        seen["runtime_token"] = runtime_token
        seen["tool_token"] = tool_token
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 202, submit.text
        assert submit.json()["status"] == "running"

        final = await _poll_terminal(client, ctx)
        assert final["status"] == "succeeded"
        assert final["result"]["result"]["findings"] == "the ratio sits at its median"
        # the executor received the full request + both grants
        assert seen["request"].experiment_invocation_id == ctx.experiment_id
        assert seen["runtime_token"].startswith("ywr_")
        assert seen["tool_token"].startswith("ywr_")


async def test_dispatch_requires_registration(keys, tmp_path):
    ctx = Ctx(keys[0])
    app = _app(keys, tmp_path / "store", executor=None)
    async with _client(app) as client:
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 404


async def test_dispatch_rejected_after_terminate(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(request, runtime_token, tool_token):
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        term = await client.delete(
            f"/v1/experiment-authorizations/{ctx.experiment_id}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
        )
        assert term.status_code == 200
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 403


async def test_terminate_cancels_running_instance(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def slow_executor(request, runtime_token, tool_token):
        await asyncio.sleep(5.0)
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=slow_executor)
    async with _client(app) as client:
        await _register(client, ctx)
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 202
        term = await client.delete(
            f"/v1/experiment-authorizations/{ctx.experiment_id}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
        )
        assert term.status_code == 200
        final = await _poll_terminal(client, ctx)
        assert final["status"] == "cancelled"


async def test_failed_instance_result_marks_invocation_failed(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(request, runtime_token, tool_token):
        return _instance_result(ok=False)

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 202
        final = await _poll_terminal(client, ctx)
        assert final["status"] == "failed"
        assert "no findings" in final["error"]


# --- idempotency -----------------------------------------------------------------


async def test_dispatch_idempotent_same_payload_conflicts_different(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(request, runtime_token, tool_token):
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        request, first = await _submit_instance(client, ctx)
        assert first.status_code == 202
        await _poll_terminal(client, ctx)

        _, replay = await _submit_instance(client, ctx, request)
        assert replay.status_code == 200  # same content replays the view
        assert replay.json()["status"] == "succeeded"

        changed = request.model_copy(deep=True)
        changed.config = ExperimentRuntimeConfig(model="other-model")
        _, conflict = await _submit_instance(client, ctx, changed)
        assert conflict.status_code == 409


# --- binding + privilege matrix ----------------------------------------------------


async def test_dispatch_binding_mismatch_rejected(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(request, runtime_token, tool_token):
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        # a dispatch token bound to another experiment id
        _, submit = await _submit_instance(
            client, ctx, scopes=(SCOPE_EXPERIMENT_RUN,),
        )
        # token minted with a DIFFERENT experiment id via headers override
        bad_headers = ctx.headers(
            aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,),
            experiment_id=uuid.uuid4(),
        )
        envelope = ExperimentInvocationEnvelope(
            request=_invocation_request(ctx),
            runtime_token=ctx.token(aud="runtime-experiment", scopes=(SCOPE_EXPERIMENT_RUN,)),
            tool_token=ctx.token(
                aud=AUD_RUNNER_TOOLS,
                scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ),
            ),
        )
        r = await client.post(
            "/v1/experiment-invocations", headers=bad_headers,
            json=envelope.model_dump(mode="json"),
        )
        assert r.status_code == 403


async def test_dispatch_privilege_matrix(keys, tmp_path):
    ctx = Ctx(keys[0])
    app = _app(keys, tmp_path / "store", executor=None)
    async with _client(app) as client:
        await _register(client, ctx)
        envelope = ExperimentInvocationEnvelope(
            request=_invocation_request(ctx),
            runtime_token=ctx.token(aud="runtime-experiment", scopes=(SCOPE_EXPERIMENT_RUN,)),
            tool_token=ctx.token(
                aud=AUD_RUNNER_TOOLS,
                scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ),
            ),
        )
        body = envelope.model_dump(mode="json")
        # tool-plane audience cannot dispatch
        r = await client.post(
            "/v1/experiment-invocations",
            headers=ctx.headers(aud=AUD_RUNNER_TOOLS, scopes=(SCOPE_EXPERIMENT_SUBMIT,)),
            json=body,
        )
        assert r.status_code == 403
        # admin scope does not include dispatch
        r = await client.post(
            "/v1/experiment-invocations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_ADMIN,)),
            json=body,
        )
        assert r.status_code == 403
        # run_status alone cannot submit
        r = await client.post(
            "/v1/experiment-invocations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN_STATUS,)),
            json=body,
        )
        assert r.status_code == 403
        # a research:run grant cannot dispatch an experiment
        r = await client.post(
            "/v1/experiment-invocations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=("research:run",)),
            json=body,
        )
        assert r.status_code == 403
        # a dispatch grant cannot register/terminate/receipts
        r = await client.post(
            "/v1/experiment-authorizations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,)),
            json=ctx.authorization().model_dump(mode="json"),
        )
        assert r.status_code == 403
        # a dispatch grant (runner-exec audience) cannot reach the tool plane
        r = await client.get(
            f"/v1/experiment-computations/{ctx.experiment_id}/{uuid.uuid4()}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,)),
        )
        assert r.status_code == 403


async def test_status_scope_required_for_polling(keys, tmp_path):
    ctx = Ctx(keys[0])

    async def executor(request, runtime_token, tool_token):
        return _instance_result()

    app = _app(keys, tmp_path / "store", experiment_instance_executor=executor)
    async with _client(app) as client:
        await _register(client, ctx)
        _, submit = await _submit_instance(client, ctx)
        assert submit.status_code == 202
        # experiment:run (submit scope) alone cannot poll
        r = await client.get(
            f"/v1/experiment-invocations/{ctx.experiment_id}",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,)),
        )
        assert r.status_code == 403
        final = await _poll_terminal(client, ctx)
        assert final["status"] == "succeeded"
        # unknown invocation id — polled with a token bound to THAT id
        other_id = uuid.uuid4()
        r = await client.get(
            f"/v1/experiment-invocations/{other_id}",
            headers=ctx.headers(
                aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN_STATUS,),
                experiment_id=other_id,
            ),
        )
        assert r.status_code == 404
        # a token bound to a DIFFERENT experiment cannot poll this one
        r = await client.get(
            f"/v1/experiment-invocations/{ctx.experiment_id}",
            headers=ctx.headers(
                aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN_STATUS,),
                experiment_id=uuid.uuid4(),
            ),
        )
        assert r.status_code == 403


# --- decode failure paths (pure) -------------------------------------------


def _decode(stdout, stderr, exit_code):
    from youwei_runner.experiment_instance import ExperimentRunConfig, _decode_result

    config = ExperimentRunConfig(
        image="youwei-agent-runtime@sha256:" + "a" * 64,
        gateway_url="http://gateway:8000",
        gateway_host="gateway:8000",
        public_keys={},
        exec_config_version="v1",
        tool_base_url="http://runner:8000",
    )
    return _decode_result(stdout, stderr, exit_code, config)


def test_decode_nonzero_exit_prefers_structured_error():
    view = _decode(
        '{"ok": false, "error": "findings not JSON"}', "banner noise", 2
    )
    assert not view.ok
    assert "findings not JSON" in view.error
    assert view.image_digest == "sha256:" + "a" * 64


def test_decode_non_json_stdout_fails():
    view = _decode("not json at all", "", 0)
    assert not view.ok
    assert "non-JSON stdout" in view.error


def test_decode_invalid_experiment_result_fails():
    view = _decode('{"ok": true, "result": {"findings": ""}}', "", 0)
    assert not view.ok
    assert "invalid result" in view.error


def test_decode_ok_result_with_usage():
    import hashlib

    code = "print(1)"
    result = ExperimentResult(
        findings="ok",
        computations=[
            ExperimentResultComputation(
                computation_id=uuid.uuid4(),
                code=code,
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                status="succeeded",
            )
        ],
    )
    view = _decode(
        '{"ok": true, "result": ' + result.model_dump_json() + ', "usage": {"source": "x"}}',
        "", 0,
    )
    assert view.ok and view.result is not None and view.result.findings == "ok"
    assert view.usage == {"source": "x"}


# --- disabled surface -------------------------------------------------------------


async def test_dispatch_disabled_without_store_answers_503(keys, tmp_path):
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
        envelope = ExperimentInvocationEnvelope(
            request=_invocation_request(ctx),
            runtime_token=ctx.token(aud="runtime-experiment", scopes=(SCOPE_EXPERIMENT_RUN,)),
            tool_token=ctx.token(
                aud=AUD_RUNNER_TOOLS,
                scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ),
            ),
        )
        r = await client.post(
            "/v1/experiment-invocations",
            headers=ctx.headers(aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_RUN,)),
            json=envelope.model_dump(mode="json"),
        )
        assert r.status_code == 503
