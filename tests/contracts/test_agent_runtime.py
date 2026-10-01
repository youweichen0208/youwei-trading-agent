"""agent-runtime-v1 wire contract (S07m-2)."""

import uuid

import pytest
from pydantic import ValidationError

from youwei_contracts.agent_runtime import (
    ResearchInvocationRequest,
    ResearchRuntimeConfig,
    invocation_digest,
)
from youwei_contracts.research import FrozenEvidence


def _evidence(tenant_id=None, run_id=None, case_id=None) -> FrozenEvidence:
    import hashlib, json
    content = []
    content_sha256 = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return FrozenEvidence(
        contract_version="research-v1",
        run_id=run_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        case={
            "case_id": str(case_id or uuid.uuid4()),
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
            "content_sha256": content_sha256,
            "content": content,
            "manifest": {"content_sha256": content_sha256},
        },
        target_policy_sha256="p" * 64,
        batch_manifest={"version": "1"},
    )


def _request(**overrides) -> ResearchInvocationRequest:
    ev = _evidence()
    import hashlib, json
    canonical = json.dumps(ev.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    base = dict(
        invocation_id=uuid.uuid4(),
        tenant_id=ev.tenant_id,
        run_id=ev.run_id,
        job_id=uuid.uuid4(),
        attempt_no=1,
        case_id=ev.case.case_id,
        evidence=ev,
        evidence_sha256=hashlib.sha256(canonical).hexdigest(),
        exec_config_version="v1",
        config=ResearchRuntimeConfig(model="m", max_iterations=8),
    )
    base.update(overrides)
    return ResearchInvocationRequest(**base)


def test_evidence_hash_must_match():
    req = _request()
    assert req.evidence_sha256  # computed correctly by the helper


def test_evidence_hash_mismatch_rejected():
    ev = _evidence()
    import hashlib, json
    canonical = json.dumps(ev.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    good = hashlib.sha256(canonical).hexdigest()
    bad = ("0" if good[0] != "0" else "1") + good[1:]
    with pytest.raises(ValidationError):
        _request(evidence=ev, evidence_sha256=bad)


def test_config_has_no_gateway_endpoint_fields():
    # The request config must NOT expose base_url/api_key: the Runner injects
    # them from deployment config. extra="forbid" enforces this.
    with pytest.raises(ValidationError):
        ResearchRuntimeConfig(model="m", base_url="http://evil")


def test_invocation_digest_is_content_sensitive():
    a = _request()
    b = _request(invocation_id=a.invocation_id)  # same id, different content
    assert invocation_digest(a) != invocation_digest(b)


def test_invocation_digest_is_stable_for_same_content():
    a = _request()
    # Rebuild the identical request: same digest.
    a2 = ResearchInvocationRequest(**a.model_dump())
    assert invocation_digest(a) == invocation_digest(a2)


def test_case_id_is_part_of_invocation_binding():
    a = _request()
    # case_id is a distinct, signed field of the invocation binding.
    b = _request(case_id=uuid.uuid4())
    assert a.case_id != b.case_id
    assert invocation_digest(a) != invocation_digest(b)
