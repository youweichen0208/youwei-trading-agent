"""S07m-2: Controller-side research link (signing + HTTP client, no Docker)."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.agent_runtime import (
    ResearchInvocationRequest,
    ResearchInvocationResult,
    ResearchInvocationStatus,
)
from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    verify_research_token,
)
from youwei_core.ledger.research_client import (
    ResearchRunnerError,
    ResearchRunnerClient,
    ResearchSigningKey,
    build_research_request,
    run_research_via_runner,
    sign_invocation_tokens,
)


@pytest.fixture(scope="module")
def signing_key():
    priv, _ = generate_research_keypair()
    return ResearchSigningKey(kid="k1", private_key_pem=priv, exec_config_version="v1")


def _evidence(tenant_id, run_id, case_id):
    content = []
    content_sha = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
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
        quant={
            "model_version": "quant-momentum-v0",
            "source_status": "produced",
            "p_outperform": 0.55,
            "expected_excess_return": 0.01,
        },
    )


def _request(signing_key):
    tenant = uuid.uuid4()
    run = uuid.uuid4()
    case = uuid.uuid4()
    ev = _evidence(tenant, run, case)
    canonical = json.dumps(ev.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return build_research_request(
        invocation_id=uuid.uuid4(),
        tenant_id=tenant,
        run_id=run,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=case,
        evidence=ev,
        evidence_sha256=hashlib.sha256(canonical).hexdigest(),
        exec_config_version=signing_key.exec_config_version,
        config={"model": "m"},
    )

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


def test_sign_invocation_tokens_mints_two_audiences(signing_key):
    req = _request(signing_key)
    runner_exec, runtime = sign_invocation_tokens(
        signing_key, req, exp=datetime.now(UTC) + timedelta(seconds=30)
    )
    pub = {"k1": None}  # we only have the private key here; load the public from it
    # Re-derive the public key from the private key to verify both tokens.
    from youwei_contracts.research_capability import _load_private_key  # noqa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv_obj = serialization.load_pem_private_key(signing_key.private_key_pem.encode(), password=None)
    pub_pem = priv_obj.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    keys = {"k1": pub_pem}

    cap_exec = verify_research_token(keys, runner_exec)
    cap_runtime = verify_research_token(keys, runtime)
    assert cap_exec.aud == AUD_RUNNER_EXEC
    assert cap_runtime.aud == AUD_RUNTIME_RESEARCH
    assert cap_exec.invocation_id == req.invocation_id
    assert cap_runtime.invocation_id == req.invocation_id
    assert SCOPE_RESEARCH_RUN in cap_exec.scopes


def test_build_research_request_binds_identity(signing_key):
    req = _request(signing_key)
    assert req.exec_config_version == "v1"
    assert req.attempt_no == 1
    assert req.config.model == "m"


async def test_run_research_via_runner_submit_and_poll(signing_key):
    req = _request(signing_key)
    result = ResearchInvocationResult(ok=True, exit_code=0, image_digest="sha256:" + "a" * 64, proposal=_proposal())

    state = {"submits": 0, "polls": 0}

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["submits"] += 1
            return httpx.Response(202, json=ResearchInvocationStatus(
                invocation_id=req.invocation_id,
                request_sha256="",  # will be validated by client
                status="running",
            ).model_dump(mode="json"))
        # GET
        state["polls"] += 1
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=req.invocation_id,
            request_sha256="",
            status="succeeded",
            result=result,
        ).model_dump(mode="json"))

    # The client validates request_sha256 against the digest; supply the right one.
    from youwei_contracts.agent_runtime import invocation_digest
    digest = invocation_digest(req)

    def transport2(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["submits"] += 1
            return httpx.Response(202, json=ResearchInvocationStatus(
                invocation_id=req.invocation_id, request_sha256=digest, status="running",
            ).model_dump(mode="json"))
        state["polls"] += 1
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=req.invocation_id, request_sha256=digest, status="succeeded",
            result=result,
        ).model_dump(mode="json"))

    async def expiry():
        return datetime.now(UTC) + timedelta(seconds=30)

    client = ResearchRunnerClient("http://runner", transport=httpx.MockTransport(transport2))
    got = await run_research_via_runner(client, signing_key, req, expiry_provider=expiry)
    await client.aclose()
    assert got.ok is True
    assert state["submits"] == 1
    assert state["polls"] >= 1


async def test_run_research_via_runner_failure_raises(signing_key):
    req = _request(signing_key)
    from youwei_contracts.agent_runtime import invocation_digest
    digest = invocation_digest(req)

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=req.invocation_id, request_sha256=digest, status="failed",
            error="research boom",
        ).model_dump(mode="json"))

    async def expiry():
        return datetime.now(UTC) + timedelta(seconds=30)

    client = ResearchRunnerClient("http://runner", transport=httpx.MockTransport(transport))
    with pytest.raises(ResearchRunnerError, match="research boom"):
        await run_research_via_runner(client, signing_key, req, expiry_provider=expiry)
    await client.aclose()


# --- fetcher (pipeline seam) ------------------------------------------------


def _snapshot():
    bars = [{"security_id": "s1", "trade_date": "2026-09-25", "close": 100.0}]
    content_sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "id": uuid.uuid4(),
        "manifest": {
            "content_sha256": content_sha,
            "kind": "daily_bars",
            "query": {"mode": "forward", "as_of": "2026-09-26T10:00:00+00:00"},
        },
        "content": bars,
    }


def _case():
    return {
        "id": uuid.uuid4(),
        "security_id": uuid.uuid4(),
        "benchmark_security_id": uuid.uuid4(),
        "horizon_td": 20,
        "target_spec_id": "target-spec-v1",
        "target_spec_sha256": "c" * 64,
        "decision_cutoff_utc": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "prediction_deadline_utc": datetime(2026, 9, 28, 13, 15, tzinfo=UTC),
        "entry_at_utc": datetime(2026, 9, 28, 13, 30, tzinfo=UTC),
        "exit_at_utc": datetime(2026, 10, 23, 20, 0, tzinfo=UTC),
    }


async def test_make_runner_research_fetcher_roundtrip(signing_key):
    from youwei_contracts.research import ResearchProposal
    from youwei_core.ledger.research_client import make_runner_research_fetcher
    from youwei_contracts.agent_runtime import invocation_digest

    tenant = uuid.uuid4()
    run = uuid.uuid4()
    job = uuid.uuid4()
    case = _case()
    snap = _snapshot()

    # The Runner returns a produced proposal; the fetcher must assemble the
    # request, submit, poll, and validate the proposal binding.
    captured_request = {}

    def transport(request: httpx.Request) -> httpx.Response:
        # POST carries the envelope; GET carries nothing.
        if request.method == "POST":
            body = json.loads(request.content)
            captured_request["invocation_id"] = body["request"]["invocation_id"]
            captured_request["case_id"] = body["request"]["case_id"]
            inv_id = uuid.UUID(body["request"]["invocation_id"])
            req_obj = ResearchInvocationRequest.model_validate(body["request"])
            return httpx.Response(202, json=ResearchInvocationStatus(
                invocation_id=inv_id, request_sha256=invocation_digest(req_obj),
                status="running",
            ).model_dump(mode="json"))
        # GET: derive invocation_id from URL and return succeeded.
        inv_id = uuid.UUID(str(request.url).rsplit("/", 1)[-1])
        case_id = captured_request["case_id"]
        proposal = ResearchProposal(
            run_id=run, case_id=uuid.UUID(case_id), source_status="produced",
            p_outperform=0.6, expected_excess_return=0.02,
            model={"model_version": "llm-v1", "provider": "test"},
        )
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=inv_id,
            request_sha256=captured_request.get("sha", ""),
            status="succeeded",
            result=ResearchInvocationResult(ok=True, exit_code=0, image_digest="sha256:" + "a" * 64, proposal=proposal),
        ).model_dump(mode="json"))

    async def expiry():
        return datetime.now(UTC) + timedelta(seconds=30)

    client = ResearchRunnerClient("http://runner", transport=httpx.MockTransport(transport))
    fetch = make_runner_research_fetcher(
        run_id=run,
        tenant_id=tenant,
        job_id=job,
        attempt_no=1,
        snapshot=snap,
        batch_manifest={},
        client=client,
        key=signing_key,
        expiry_provider=expiry,
        research_config={"model": "m"},
    )
    proposal = await fetch(case, None, {
        "source": "quant_model", "source_status": "produced",
        "p_outperform": 0.55, "expected_excess_return": 0.01,
        "model_version": "quant-momentum-v0",
    })
    await client.aclose()
    assert proposal.run_id == run
    assert proposal.case_id == case["id"]
    assert proposal.p_outperform == 0.6


async def test_make_runner_research_fetcher_reports_attribution(signing_key):
    """S12e (D2 版本留痕): the fetcher surfaces the container attribution
    (prompt hash / execution config / model_returned + scope) alongside
    image_digest / usage / exec_config_version so the exploratory report can
    record what actually ran, next to the configured routing."""
    from youwei_contracts.research import ResearchProposal
    from youwei_contracts.agent_runtime import invocation_digest, ResearchInvocationAttribution
    from youwei_core.ledger.research_client import make_runner_research_fetcher

    tenant = uuid.uuid4()
    run = uuid.uuid4()
    job = uuid.uuid4()
    case = _case()
    snap = _snapshot()
    captured = {}

    usage = {
        "source": "session_delta", "scope": "chat_turn", "complete": True,
        "prompt_tokens": 100, "completion_tokens": 40, "api_calls": 2,
    }
    import hashlib as _hl
    exec_cfg = {"model": "glm-5.3", "provider": "custom", "max_iterations": 8,
                "run_budget_seconds": None, "max_output_tokens": 16384,
                "gateway_base_url": "http://litellm:4000/v1"}
    cfg_sha = _hl.sha256(json.dumps(exec_cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    attribution = {
        "brief_sha256": "b" * 64,
        "execution_config": exec_cfg,
        "execution_config_sha256": cfg_sha,
        "model_returned": "glm-5.3-actual",
        "model_returned_scope": "last_completed_provider_response",
    }

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            captured["case_id"] = body["request"]["case_id"]
            req_obj = ResearchInvocationRequest.model_validate(body["request"])
            return httpx.Response(202, json=ResearchInvocationStatus(
                invocation_id=uuid.UUID(body["request"]["invocation_id"]),
                request_sha256=invocation_digest(req_obj),
                status="running",
            ).model_dump(mode="json"))
        inv_id = uuid.UUID(str(request.url).rsplit("/", 1)[-1])
        proposal = ResearchProposal(
            run_id=run, case_id=uuid.UUID(captured["case_id"]),
            source_status="produced", p_outperform=0.6,
            expected_excess_return=0.02,
            model={"model_version": "glm-5.3", "provider": "custom"},
        )
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=inv_id, request_sha256=captured.get("sha", ""),
            status="succeeded",
            result=ResearchInvocationResult(
                ok=True, exit_code=0,
                image_digest="sha256:" + "b" * 64,
                proposal=proposal, usage=usage,
                attribution=ResearchInvocationAttribution.model_validate(attribution),
            ),
        ).model_dump(mode="json"))

    async def expiry():
        return datetime.now(UTC) + timedelta(seconds=30)

    client = ResearchRunnerClient("http://runner", transport=httpx.MockTransport(transport))
    sink_payloads = []
    fetch = make_runner_research_fetcher(
        run_id=run, tenant_id=tenant, job_id=job, attempt_no=1,
        snapshot=snap, batch_manifest={},
        client=client, key=signing_key, expiry_provider=expiry,
        research_config={"model": "glm-5.3"},
        attribution_sink=sink_payloads.append,
    )
    proposal = await fetch(case, None, {
        "source": "quant_model", "source_status": "produced",
        "p_outperform": 0.55, "expected_excess_return": 0.01,
        "model_version": "quant-momentum-v0",
    })
    await client.aclose()
    assert proposal.p_outperform == 0.6
    assert len(sink_payloads) == 1
    payload = sink_payloads[0]
    assert payload["attribution"]["model_returned"] == "glm-5.3-actual"
    assert payload["attribution"]["model_returned_scope"] == "last_completed_provider_response"
    assert payload["attribution"]["brief_sha256"] == "b" * 64
    assert payload["attribution"]["execution_config"]["model"] == "glm-5.3"
    assert payload["attribution"]["execution_config_sha256"] == cfg_sha
    assert payload["image_digest"] == "sha256:" + "b" * 64
    assert payload["usage"] == usage
    assert payload["exec_config_version"] == signing_key.exec_config_version


async def test_make_runner_research_fetcher_without_sink_still_works(signing_key):
    """The sink is optional observability: absent sink must not change the
    fetcher's behavior (proposal still returned)."""
    from youwei_contracts.research import ResearchProposal
    from youwei_contracts.agent_runtime import invocation_digest
    from youwei_core.ledger.research_client import make_runner_research_fetcher

    tenant = uuid.uuid4()
    run = uuid.uuid4()
    case = _case()
    snap = _snapshot()
    captured = {}

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            captured["case_id"] = body["request"]["case_id"]
            req_obj = ResearchInvocationRequest.model_validate(body["request"])
            return httpx.Response(202, json=ResearchInvocationStatus(
                invocation_id=uuid.UUID(body["request"]["invocation_id"]),
                request_sha256=invocation_digest(req_obj),
                status="running",
            ).model_dump(mode="json"))
        inv_id = uuid.UUID(str(request.url).rsplit("/", 1)[-1])
        proposal = ResearchProposal(
            run_id=run, case_id=uuid.UUID(captured["case_id"]),
            source_status="unavailable", reason="no_benchmark_rows",
            model=None,
        )
        return httpx.Response(200, json=ResearchInvocationStatus(
            invocation_id=inv_id, request_sha256=captured.get("sha", ""),
            status="succeeded",
            result=ResearchInvocationResult(
                ok=True, exit_code=0,
                image_digest="sha256:" + "c" * 64,
                proposal=proposal,
            ),
        ).model_dump(mode="json"))

    async def expiry():
        return datetime.now(UTC) + timedelta(seconds=30)

    client = ResearchRunnerClient("http://runner", transport=httpx.MockTransport(transport))
    fetch = make_runner_research_fetcher(
        run_id=run, tenant_id=tenant, job_id=uuid.uuid4(), attempt_no=1,
        snapshot=snap, batch_manifest={},
        client=client, key=signing_key, expiry_provider=expiry,
        research_config={"model": "glm-5.3"},
    )
    proposal = await fetch(case, None, {
        "source": "quant_model", "source_status": "unavailable",
        "reason": "insufficient_history",
        "model_version": "quant-momentum-v0",
    })
    await client.aclose()
    assert proposal.source_status == "unavailable"
