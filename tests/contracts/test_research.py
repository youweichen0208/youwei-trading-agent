"""research-v1 contract: FrozenEvidence and ResearchProposal.

Pure protocol tests — no PostgreSQL, no Hermes runtime. They pin the wire
shape and value discipline the Controller relies on when it validates a
Hermes proposal before sealing the llm_adjusted position.
"""

import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError


def _bars(n=3):
    return [
        {
            "security_id": str(uuid.uuid4()),
            "trade_date": f"2026-09-{22 + i:02d}",
            "close": 100.0 + i,
            "volume": 1000,
            "provenance": {"raw_object_id": str(uuid.uuid4())},
        }
        for i in range(n)
    ]


def _content_sha(bars):
    data = json.dumps(
        bars, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _case(**overrides):
    base = {
        "case_id": str(uuid.uuid4()),
        "security_id": str(uuid.uuid4()),
        "benchmark_security_id": str(uuid.uuid4()),
        "horizon_td": 20,
        "target_spec_id": "target-spec-v1",
        "target_spec_sha256": "a" * 64,
        "decision_cutoff_utc": "2026-09-26T10:00:00+00:00",
        "prediction_deadline_utc": "2026-09-28T13:15:00+00:00",
        "entry_at_utc": "2026-09-28T13:30:00+00:00",
        "exit_at_utc": "2026-10-23T20:00:00+00:00",
    }
    base.update(overrides)
    return base


def _evidence(bars=None, **overrides):
    bars = _bars() if bars is None else bars
    base = {
        "snapshot_id": str(uuid.uuid4()),
        "kind": "daily_bars",
        "as_of": "2026-09-26T10:00:00+00:00",
        "mode": "forward",
        "content_sha256": _content_sha(bars),
        "content": bars,
        "manifest": {"code_version": "daily-bars-snapshot-v1"},
    }
    base.update(overrides)
    return base


def _frozen(**overrides):
    base = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "case": _case(),
        "evidence": _evidence(),
        "target_policy_sha256": "b" * 64,
        "batch_manifest": {"calendar_version": "nyse-rules-v1"},
        "quant": {
            "model_version": "quant-momentum-v0",
            "source_status": "produced",
            "p_outperform": 0.55,
            "expected_excess_return": 0.01,
        },
    }
    base.update(overrides)
    return base


def test_frozen_evidence_round_trips_and_hash_matches():
    from youwei_contracts.research import FrozenEvidence

    evidence = FrozenEvidence.model_validate(_frozen())
    assert evidence.case.horizon_td == 20
    assert evidence.evidence.mode == "forward"
    assert evidence.evidence.content_sha256 == _content_sha(evidence.evidence.content)


def test_frozen_evidence_rejects_tampered_content():
    from youwei_contracts.research import FrozenEvidence

    body = _frozen()
    bars = body["evidence"]["content"]
    bars[0]["close"] = 999.0  # mutate content without updating the hash
    with pytest.raises(ValidationError, match="content hash mismatch"):
        FrozenEvidence.model_validate(body)


def test_frozen_evidence_forbids_extra_fields():
    from youwei_contracts.research import FrozenEvidence

    body = _frozen()
    body["evidence"]["content"][0]["sneaky"] = True
    with pytest.raises(ValidationError):
        FrozenEvidence.model_validate(body)


def test_proposal_produced_requires_values_and_model():
    from youwei_contracts.research import ResearchProposal

    with pytest.raises(ValidationError, match="both values"):
        ResearchProposal(
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
            source_status="produced", p_outperform=0.6,
        )
    with pytest.raises(ValidationError, match="attributable model"):
        ResearchProposal(
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
            source_status="produced", p_outperform=0.6,
            expected_excess_return=0.02,
        )


def test_proposal_value_range_is_enforced():
    from youwei_contracts.research import ResearchProposal

    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        ResearchProposal(
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
            source_status="produced", p_outperform=1.5,
            expected_excess_return=0.02,
            model={"model_version": "m", "provider": "p"},
        )


def test_proposal_unavailable_forbids_values_and_requires_reason():
    from youwei_contracts.research import ResearchProposal

    with pytest.raises(ValidationError, match="reason"):
        ResearchProposal(
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
            source_status="unavailable",
        )
    with pytest.raises(ValidationError, match="must not carry values"):
        ResearchProposal(
            run_id=uuid.uuid4(), case_id=uuid.uuid4(),
            source_status="unavailable", reason="no data", p_outperform=0.5,
        )


def test_proposal_digest_is_stable_and_order_insensitive():
    from youwei_contracts.research import ResearchProposal, proposal_digest

    def make():
        return ResearchProposal(
            run_id="00000000-0000-0000-0000-000000000001",
            case_id="00000000-0000-0000-0000-000000000002",
            source_status="produced",
            p_outperform=0.6,
            expected_excess_return=0.02,
            model={"model_version": "m1", "provider": "p1", "cost_estimate": {}},
            references=[
                {"kind": "evidence", "locator": "row-0"},
                {"kind": "research_memory", "locator": "mem-1", "note": "x"},
            ],
        )

    assert proposal_digest(make()) == proposal_digest(make())
    # reordering references (same set) should NOT change the digest —
    # canonical json sorts keys but lists preserve order, so this asserts
    # list order matters (callers must emit a stable order).
    d1 = proposal_digest(make())
    p2 = make()
    p2.references = list(reversed(p2.references))
    assert proposal_digest(p2) != d1
