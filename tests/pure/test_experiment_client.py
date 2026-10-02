"""S08c-1b: the Controller's client for the Runner's experiment control
plane (register / terminate / receipts) and the Ed25519 token signing.

Pure logic over httpx MockTransport — no real Runner, no database.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentLimits,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    SCOPE_EXPERIMENT_ADMIN,
    generate_research_keypair,
    public_key_thumbprint,
    verify_research_token,
)
from youwei_core.ledger.experiment_client import (
    ExperimentBinding,
    ExperimentRunnerClient,
    ExperimentRunnerError,
    experiment_binding_from_auth,
    sign_experiment_admin_token,
)
from youwei_contracts.sandbox import SnapshotBundle

EXP = uuid.uuid4()
TENANT = uuid.uuid4()
RUN = uuid.uuid4()
JOB = uuid.uuid4()
ATTEMPT_NO = 1
CASE = uuid.uuid4()
EV_SHA = "e" * 64
EXEC_CONFIG = "research-exec-2026-10"


def _snapshot_bundle() -> SnapshotBundle:
    import hashlib, json

    content = json.dumps([{"security_id": "x", "close": 1.0}])
    return SnapshotBundle(
        snapshot_id=uuid.uuid4(),
        content=content,
        manifest={"content_sha256": hashlib.sha256(content.encode()).hexdigest()},
    )


def _auth() -> ExperimentAuthorization:
    return ExperimentAuthorization(
        experiment_invocation_id=EXP,
        tenant_id=TENANT,
        run_id=RUN,
        job_id=JOB,
        attempt_id=uuid.uuid4(),
        attempt_no=ATTEMPT_NO,
        case_id=CASE,
        evidence_sha256=EV_SHA,
        exec_config_version=EXEC_CONFIG,
        limits=ExperimentLimits(
            max_computations=2,
            max_concurrent=1,
            max_total_duration_seconds=600.0,
            max_artifact_bytes=1024 * 1024,
        ),
        snapshot=_snapshot_bundle(),
    )


def _binding() -> ExperimentBinding:
    return ExperimentBinding(
        experiment_invocation_id=EXP,
        tenant_id=TENANT,
        run_id=RUN,
        job_id=JOB,
        attempt_no=ATTEMPT_NO,
        case_id=CASE,
        evidence_sha256=EV_SHA,
        exec_config_version=EXEC_CONFIG,
    )


def _receipt(experiment_id=EXP, status="succeeded") -> ExperimentComputationReceipt:
    return ExperimentComputationReceipt(
        experiment_invocation_id=experiment_id,
        computation_id=uuid.uuid4(),
        request_sha256="1" * 64,
        code_sha256="2" * 64,
        image="youwei-sandbox@sha256:fixed",
        snapshot_sha256="3" * 64,
        status=status,
        duration_seconds=1.0,
    )


def _key():
    from youwei_core.ledger.research_client import ResearchSigningKey

    private, public = generate_research_keypair()
    return ResearchSigningKey(
        kid=public_key_thumbprint(public),
        private_key_pem=private,
        exec_config_version=EXEC_CONFIG,
    )


async def _exp_provider():
    return datetime.now(UTC) + timedelta(minutes=5)


class TestAdminToken:
    def test_signs_runner_exec_admin_scope_with_binding(self):
        key = _key()
        token = sign_experiment_admin_token(key, _binding(), exp=datetime.now(UTC) + timedelta(minutes=5))
        public = {key.kid: _public_of(key)}
        cap = verify_research_token(public, token)
        assert cap.aud == AUD_RUNNER_EXEC
        assert SCOPE_EXPERIMENT_ADMIN in cap.scopes
        assert cap.invocation_id == EXP
        assert cap.tenant_id == TENANT
        assert cap.run_id == RUN
        assert cap.job_id == JOB
        assert cap.attempt_no == ATTEMPT_NO
        assert cap.case_id == CASE
        assert cap.evidence_sha256 == EV_SHA
        assert cap.exec_config_version == EXEC_CONFIG

    def test_binding_derived_from_authorization(self):
        auth = _auth()
        binding = experiment_binding_from_auth(auth)
        assert binding == _binding()


def _public_of(key):
    # The public PEM for a ResearchSigningKey: derive from the private PEM.
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(
        key.private_key_pem.encode(), password=None
    )
    return private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


class TestControlPlaneCalls:
    async def test_register_authorization_posts_bound_token_and_body(self):
        key = _key()
        auth = _auth()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth_header"] = request.headers.get("authorization", "")
            seen["body"] = ExperimentAuthorization.model_validate_json(request.content)
            return httpx.Response(200, json={"status": "registered"})

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        await client.register_authorization(
            key=key, auth=auth,
            binding=experiment_binding_from_auth(auth),
            expiry_provider=_exp_provider,
        )
        assert seen["path"] == "/v1/experiment-authorizations"
        assert seen["auth_header"].startswith("Bearer ywr_")
        assert seen["body"].experiment_invocation_id == EXP
        cap = verify_research_token(
            {key.kid: _public_of(key)}, seen["auth_header"][7:]
        )
        assert SCOPE_EXPERIMENT_ADMIN in cap.scopes
        assert seen["body"] == auth

    async def test_register_conflict_surfaces_status(self):
        key = _key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"detail": "experiment already registered with different content"}
            )

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ExperimentRunnerError, match="409"):
            await client.register_authorization(
                key=key, auth=_auth(), binding=_binding(),
                expiry_provider=_exp_provider,
            )

    async def test_terminate_deletes_by_id(self):
        key = _key()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"status": "terminated"})

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        await client.terminate(
            key=key, binding=_binding(), expiry_provider=_exp_provider
        )
        assert seen["method"] == "DELETE"
        assert seen["path"] == f"/v1/experiment-authorizations/{EXP}"

    async def test_receipts_parses_and_validates(self):
        key = _key()
        receipts = [_receipt(), _receipt(status="failed")]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[r.model_dump(mode="json") for r in receipts]
            )

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        fetched = await client.receipts(
            key=key, binding=_binding(), expiry_provider=_exp_provider
        )
        assert fetched == receipts

    async def test_receipts_for_another_experiment_rejected(self):
        key = _key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[_receipt(experiment_id=uuid.uuid4()).model_dump(mode="json")]
            )

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ExperimentRunnerError, match="another experiment"):
            await client.receipts(
                key=key, binding=_binding(), expiry_provider=_exp_provider
            )

    async def test_error_status_raises_with_code(self):
        key = _key()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "forbidden"})

        client = ExperimentRunnerClient(
            "http://runner.test", transport=httpx.MockTransport(handler)
        )
        for call in (
            lambda: client.register_authorization(
                key=key, auth=_auth(), binding=_binding(),
                expiry_provider=_exp_provider,
            ),
            lambda: client.terminate(
                key=key, binding=_binding(), expiry_provider=_exp_provider
            ),
            lambda: client.receipts(
                key=key, binding=_binding(), expiry_provider=_exp_provider
            ),
        ):
            with pytest.raises(ExperimentRunnerError, match="403"):
                await call()
