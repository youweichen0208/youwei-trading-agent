"""S07m: agent-runtime invocation codec + Ed25519 grant check (pure, no Hermes).

These tests pin the subprocess boundary from the agent-runtime side: the
request codec, the Ed25519 research-grant verification (audience + scope +
tenant + case binding), and the result encoding — all without importing the
pinned Hermes checkout. The research container holds only PUBLIC keys.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_contracts.research import FrozenEvidence, ResearchProposal
from youwei_contracts.research_capability import (
    AUD_RUNTIME_RESEARCH,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    sign_research_token,
)
from youwei_agent_runtime.invoke import (
    InvocationError,
    decode_request,
    encode_error,
    encode_result,
    honor_request,
)
from youwei_agent_runtime.runtime import ResearchTurn, UsageReport


@pytest.fixture(scope="module")
def keypair():
    priv, pub = generate_research_keypair()
    return priv, pub


def _evidence(tenant_id) -> FrozenEvidence:
    sec = str(uuid.uuid4())
    bars = [{"security_id": sec, "trade_date": "2026-09-25", "close": 100.0}]
    content_sha = hashlib.sha256(
        json.dumps(bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return FrozenEvidence(
        run_id=uuid.uuid4(),
        tenant_id=tenant_id,
        case={
            "case_id": str(uuid.uuid4()),
            "security_id": sec,
            "benchmark_security_id": str(uuid.uuid4()),
            "horizon_td": 20,
            "target_spec_id": "target-spec-v1",
            "target_spec_sha256": "c" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00+00:00",
            "prediction_deadline_utc": "2026-09-28T13:15:00+00:00",
            "entry_at_utc": "2026-09-28T13:30:00+00:00",
            "exit_at_utc": "2026-10-23T20:00:00+00:00",
        },
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00+00:00",
            "mode": "forward",
            "content_sha256": content_sha,
            "content": bars,
            "manifest": {"code_version": "daily-bars-snapshot-v1"},
        },
        target_policy_sha256="d" * 64,
        batch_manifest={},
    )


def _token(priv, evidence, *, tenant_id=None, scopes=(SCOPE_RESEARCH_RUN,), exp=None, aud=AUD_RUNTIME_RESEARCH):
    return sign_research_token(
        priv,
        kid="k1",
        aud=aud,
        invocation_id=uuid.uuid4(),
        tenant_id=tenant_id if tenant_id is not None else evidence.tenant_id,
        run_id=evidence.run_id,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=evidence.case.case_id,
        evidence_sha256="e" * 64,
        exec_config_version="v1",
        scopes=scopes,
        exp=exp or (datetime.now(UTC) + timedelta(minutes=5)),
    )


class _Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


async def _fake_run_research(evidence, config, *, capability_token=None, public_keys=None, experiments=None):
    return ResearchTurn(
        proposal=ResearchProposal(
            run_id=evidence.run_id,
            case_id=evidence.case.case_id,
            source_status="produced",
            p_outperform=0.6,
            expected_excess_return=0.02,
            model={"model_version": "llm-v1", "provider": "test"},
        ),
        usage=UsageReport(
            source="unavailable", scope="unknown", complete=False,
            incomplete_reasons=("no_usage_signal",),
        ),
    )


def test_honor_request_returns_proposal_for_valid_grant(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence)
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {"base_url": "x", "api_key": "k", "model": "m"},
    }
    result = asyncio.run(
        honor_request(
            payload,
            public_keys={"k1": pub},
            run_research=_fake_run_research,
            config_factory=_Config,
        )
    )
    decoded = json.loads(result)
    assert decoded["ok"] is True
    assert decoded["proposal"]["source_status"] == "produced"


def test_honor_request_passes_grant_to_run_research(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence)
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {"base_url": "x", "api_key": "k", "model": "m"},
    }
    captured = {}

    async def _spy(evidence, config, *, capability_token=None, public_keys=None, experiments=None):
        captured["token"] = capability_token
        captured["public_keys"] = public_keys
        return await _fake_run_research(
            evidence, config,
            capability_token=capability_token,
            public_keys=public_keys,
        )

    asyncio.run(
        honor_request(
            payload, public_keys={"k1": pub},
            run_research=_spy, config_factory=_Config,
        )
    )
    assert captured["token"] == token
    assert captured["public_keys"] == {"k1": pub}


def test_honor_request_rejects_wrong_tenant(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence, tenant_id=uuid.uuid4())  # other tenant
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="tenant"):
        asyncio.run(
            honor_request(
                payload, public_keys={"k1": pub},
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_wrong_case(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    # Sign for a DIFFERENT case_id than the evidence carries.
    token = sign_research_token(
        priv, kid="k1", aud=AUD_RUNTIME_RESEARCH, invocation_id=uuid.uuid4(),
        tenant_id=tenant, run_id=evidence.run_id, job_id=uuid.uuid4(), attempt_no=1,
        case_id=uuid.uuid4(),  # wrong case
        evidence_sha256="e" * 64, exec_config_version="v1",
        scopes=(SCOPE_RESEARCH_RUN,), exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="case"):
        asyncio.run(
            honor_request(
                payload, public_keys={"k1": pub},
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_missing_scope(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence, scopes=("snapshot_read",))
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="scope"):
        asyncio.run(
            honor_request(
                payload, public_keys={"k1": pub},
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_bad_signature(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    payload = {
        "capability_token": "ywr_garbage",
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="capability"):
        asyncio.run(
            honor_request(
                payload, public_keys={"k1": pub},
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_expired_grant(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence, exp=datetime.now(UTC) - timedelta(minutes=1))
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="capability"):
        asyncio.run(
            honor_request(
                payload, public_keys={"k1": pub},
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_unknown_kid(keypair):
    priv, pub = keypair
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = _token(priv, evidence)
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="capability"):
        asyncio.run(
            honor_request(
                payload, public_keys={"other": pub},  # wrong kid
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_decode_request_rejects_non_json():
    with pytest.raises(InvocationError, match="not JSON"):
        decode_request("not json")


def test_encode_result_and_error_shapes():
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    proposal = ResearchProposal(
        run_id=evidence.run_id, case_id=evidence.case.case_id,
        source_status="unavailable", reason="insufficient_history",
    )
    turn = ResearchTurn(proposal=proposal, usage=UsageReport(
        source="session_delta", scope="chat_turn", complete=True,
        prompt_tokens=10, completion_tokens=20, total_tokens=30,
        input_tokens=8, output_tokens=20, api_calls=1,
    ))
    out = json.loads(encode_result(turn))
    assert out["ok"] is True
    assert out["proposal"]["source_status"] == "unavailable"
    assert out["usage"]["source"] == "session_delta"
    assert out["usage"]["complete"] is True
    assert out["usage"]["prompt_tokens"] == 10
    assert out["usage"]["api_calls"] == 1

    err = json.loads(encode_error(InvocationError("boom")))
    assert err["ok"] is False
    assert "boom" in err["error"]
