"""S07h: agent-runtime invocation codec + capability check (pure, no Hermes).

These tests pin the subprocess boundary from the agent-runtime side: the
request codec, the capability-token verification (scope + tenant binding),
and the result encoding — all without importing the pinned Hermes checkout.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from youwei_contracts.capability import sign_capability
from youwei_contracts.research import FrozenEvidence, ResearchProposal
from youwei_agent_runtime.invoke import (
    InvocationError,
    decode_request,
    encode_error,
    encode_result,
    honor_request,
)


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


class _Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


async def _fake_run_research(evidence, config):
    return ResearchProposal(
        run_id=evidence.run_id,
        case_id=evidence.case.case_id,
        source_status="produced",
        p_outperform=0.6,
        expected_excess_return=0.02,
        model={"model_version": "llm-v1", "provider": "test"},
    )


SECRET = "test-capability-secret"


def test_honor_request_returns_proposal_for_valid_capability():
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = sign_capability(
        SECRET, job_id=uuid.uuid4(), attempt_no=1, tenant_id=tenant,
        scopes=("llm_call", "snapshot_read"),
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {"base_url": "x", "api_key": "k", "model": "m"},
    }
    result = asyncio.run(
        honor_request(
            payload,
            capability_secret=SECRET,
            run_research=_fake_run_research,
            config_factory=_Config,
        )
    )
    decoded = json.loads(result)
    assert decoded["ok"] is True
    assert decoded["proposal"]["source_status"] == "produced"


def test_honor_request_rejects_wrong_tenant():
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = sign_capability(
        SECRET, job_id=uuid.uuid4(), attempt_no=1, tenant_id=uuid.uuid4(),  # other tenant
        scopes=("llm_call",),
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="tenant"):
        asyncio.run(
            honor_request(
                payload, capability_secret=SECRET,
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_missing_scope():
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    token = sign_capability(
        SECRET, job_id=uuid.uuid4(), attempt_no=1, tenant_id=tenant,
        scopes=("snapshot_read",),  # no llm_call
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    payload = {
        "capability_token": token,
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="scope"):
        asyncio.run(
            honor_request(
                payload, capability_secret=SECRET,
                run_research=_fake_run_research, config_factory=_Config,
            )
        )


def test_honor_request_rejects_bad_signature():
    tenant = uuid.uuid4()
    evidence = _evidence(tenant)
    payload = {
        "capability_token": "ywc_garbage",
        "evidence": evidence.model_dump(mode="json"),
        "config": {},
    }
    with pytest.raises(InvocationError, match="capability"):
        asyncio.run(
            honor_request(
                payload, capability_secret=SECRET,
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
    out = json.loads(encode_result(proposal))
    assert out["ok"] is True
    assert out["proposal"]["source_status"] == "unavailable"

    err = json.loads(encode_error(InvocationError("boom")))
    assert err["ok"] is False
    assert "boom" in err["error"]
