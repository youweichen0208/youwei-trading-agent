"""experiment-v1 dispatch wire contract tests (S08c-2): the Controller ->
Runner -> experiment-container invocation types, their digests, and the
runtime-experiment grant audience."""

import uuid

import pytest
from pydantic import ValidationError

from youwei_contracts.experiment import (
    ExperimentInvocationEnvelope,
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentInvocationStatus,
    ExperimentRequest,
    ExperimentResult,
    ExperimentResultComputation,
    ExperimentRuntimeConfig,
    experiment_invocation_digest,
)
from youwei_contracts.research_capability import (
    AUD_RUNTIME_EXPERIMENT,
    AUD_RUNNER_EXEC,
    SCOPE_EXPERIMENT_RUN,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
    verify_research_token,
)

EXP = uuid.uuid4()
TENANT = uuid.uuid4()
RUN = uuid.uuid4()
JOB = uuid.uuid4()
CASE = uuid.uuid4()
EV_SHA = "e" * 64


def _request() -> ExperimentInvocationRequest:
    return ExperimentInvocationRequest(
        experiment_invocation_id=EXP,
        tenant_id=TENANT,
        run_id=RUN,
        job_id=JOB,
        attempt_no=1,
        case_id=CASE,
        evidence_sha256=EV_SHA,
        exec_config_version="research-exec-2026-10",
        question=ExperimentRequest(
            question="20-session rolling realized vol ratio vs benchmark; current ratio's quantile in its own history",
            motivation="the quant library does not cover this statistic",
            requested_shape="{ratio: float, quantile: float}",
        ),
        snapshot_manifest={"snapshot_id": "11111111-1111-1111-1111-111111111111", "row_count": 120},
        config=ExperimentRuntimeConfig(model="glm-5.3"),
    )


class TestExperimentInvocationRequest:
    def test_valid_request_roundtrip(self):
        req = _request()
        dumped = req.model_dump(mode="json")
        assert ExperimentInvocationRequest.model_validate(dumped) == req

    def test_bad_evidence_hash_rejected(self):
        with pytest.raises(ValidationError):
            ExperimentInvocationRequest(
                **{
                    **_request().model_dump(mode="json"),
                    "evidence_sha256": "not-a-hash",
                }
            )

    def test_attempt_no_must_be_positive(self):
        with pytest.raises(ValidationError):
            ExperimentInvocationRequest(
                **{
                    **_request().model_dump(mode="json"),
                    "attempt_no": 0,
                }
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ExperimentInvocationRequest(
                **{
                    **_request().model_dump(mode="json"),
                    "snapshot_content": "[]",  # the instance never sees content
                }
            )

    def test_digest_stable_and_sensitive(self):
        req = _request()
        same = _request()
        assert experiment_invocation_digest(req) == experiment_invocation_digest(same)
        changed = req.model_copy(deep=True)
        changed.config = ExperimentRuntimeConfig(model="other-model")
        assert experiment_invocation_digest(req) != experiment_invocation_digest(changed)


class ExperimentResultSample:
    @staticmethod
    def build():
        import hashlib

        code = "print(1)"
        return ExperimentResult(
            findings="ratio at median",
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


class TestInvocationResultAndStatus:
    def test_ok_result_carries_experiment_result(self):
        result = ExperimentResultSample.build()
        view = ExperimentInvocationResult(
            ok=True, result=result, exit_code=0, image_digest="sha256:abc"
        )
        assert view.result.findings == "ratio at median"

    def test_failed_invocation_has_no_result(self):
        view = ExperimentInvocationResult(
            ok=False, error="boom", exit_code=2, image_digest="sha256:abc"
        )
        assert view.result is None

    def test_status_shape(self):
        status = ExperimentInvocationStatus(
            invocation_id=EXP,
            request_sha256="0" * 64,
            status="succeeded",
            result=ExperimentInvocationResult(
                ok=True, result=ExperimentResultSample.build(),
                exit_code=0, image_digest="sha256:abc",
            ),
        )
        assert status.result.result.findings == "ratio at median"

    def test_envelope_separates_auth_from_content(self):
        req = _request()
        env = ExperimentInvocationEnvelope(
            request=req, runtime_token="t1", tool_token="t2"
        )
        assert env.runtime_token == "t1" and env.tool_token == "t2"
        # the digest covers the request only, never the tokens
        assert experiment_invocation_digest(env.request) == experiment_invocation_digest(req)


class TestRuntimeExperimentGrant:
    def test_runtime_experiment_audience_roundtrip(self):
        private, public = generate_research_keypair()
        kid = public_key_thumbprint(public)
        token = sign_research_token(
            private,
            kid=kid,
            aud=AUD_RUNTIME_EXPERIMENT,
            scopes=(SCOPE_EXPERIMENT_RUN,),
            invocation_id=EXP,
            tenant_id=TENANT,
            run_id=RUN,
            job_id=JOB,
            attempt_no=1,
            case_id=CASE,
            evidence_sha256=EV_SHA,
            exec_config_version="research-exec-2026-10",
            exp=__import__("datetime").datetime(
                2100, 1, 1, tzinfo=__import__("datetime").UTC
            ),
        )
        cap = verify_research_token({kid: public}, token)
        assert cap.aud == AUD_RUNTIME_EXPERIMENT
        assert SCOPE_EXPERIMENT_RUN in cap.scopes
        assert cap.invocation_id == EXP

    def test_runner_exec_dispatch_token_roundtrip(self):
        private, public = generate_research_keypair()
        kid = public_key_thumbprint(public)
        token = sign_research_token(
            private,
            kid=kid,
            aud=AUD_RUNNER_EXEC,
            scopes=(SCOPE_EXPERIMENT_RUN,),
            invocation_id=EXP,
            tenant_id=TENANT,
            run_id=RUN,
            job_id=JOB,
            attempt_no=1,
            case_id=CASE,
            evidence_sha256=EV_SHA,
            exec_config_version="research-exec-2026-10",
            exp=__import__("datetime").datetime(
                2100, 1, 1, tzinfo=__import__("datetime").UTC
            ),
        )
        cap = verify_research_token({kid: public}, token)
        assert cap.aud == AUD_RUNNER_EXEC
        assert SCOPE_EXPERIMENT_RUN in cap.scopes
