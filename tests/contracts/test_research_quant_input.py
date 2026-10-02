"""research-v1 quant input contract.

The llm_adjusted position is defined RELATIVE to the frozen quant
prediction (architecture: 量化预测 → 研究证据与反证 → 保持或调整 →
记录调整理由). This pins:

1. QuantPrediction value discipline (mirrors ResearchProposal /
   SourcePrediction: produced carries both values, unavailable carries a
   reason and never a value);
2. FrozenEvidence REQUIRES the quant prediction — omitting it is exactly
   the defect this closes (Hermes must never answer blind to quant);
3. validate_proposal_references: a produced proposal over a produced
   quant must state quant_relation (kept / adjusted); the relation is
   meaningless in any other combination and is rejected there.
"""

import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError

from youwei_contracts.research import (
    FrozenEvidence,
    QuantPrediction,
    ResearchProposal,
    validate_proposal_references,
)

QUANT_MODEL = "quant-logistic-ridge-v1"


def _evidence(quant=None):
    rows = [{"security_id": "stock-a", "trade_date": "2026-09-25", "close": "103"}]
    content_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if quant is None:
        quant = QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="produced",
            p_outperform=0.61,
            expected_excess_return=0.02,
        )
    return FrozenEvidence(
        run_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        case={
            "case_id": str(uuid.uuid4()),
            "security_id": str(uuid.uuid4()),
            "benchmark_security_id": str(uuid.uuid4()),
            "horizon_td": 20,
            "target_spec_id": "target-spec-v1",
            "target_spec_sha256": "a" * 64,
            "decision_cutoff_utc": "2026-09-26T10:00:00Z",
            "prediction_deadline_utc": "2026-09-28T13:15:00Z",
            "entry_at_utc": "2026-09-28T13:30:00Z",
            "exit_at_utc": "2026-10-23T20:00:00Z",
        },
        evidence={
            "snapshot_id": str(uuid.uuid4()),
            "kind": "daily_bars",
            "as_of": "2026-09-26T10:00:00Z",
            "mode": "forward",
            "content_sha256": content_hash,
            "content": rows,
            "manifest": {"code_version": "daily-bars-snapshot-v1"},
        },
        target_policy_sha256="a" * 64,
        batch_manifest={},
        quant=quant,
    )


def _proposal(evidence, *, source_status="produced", quant_relation="kept"):
    return ResearchProposal(
        run_id=evidence.run_id,
        case_id=evidence.case.case_id,
        source_status=source_status,
        p_outperform=0.6 if source_status == "produced" else None,
        expected_excess_return=0.015 if source_status == "produced" else None,
        reason=None if source_status == "produced" else "insufficient evidence",
        model=(
            {"model_version": "llm-v1", "provider": "test"}
            if source_status == "produced"
            else None
        ),
        quant_relation=quant_relation if source_status == "produced" else None,
    )


# --- QuantPrediction value discipline ---------------------------------------


def test_quant_prediction_produced_requires_both_values():
    with pytest.raises(ValidationError, match="both values"):
        QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="produced",
            p_outperform=0.6,
            expected_excess_return=None,
        )


def test_quant_prediction_produced_bounds_p_outperform():
    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="produced",
            p_outperform=1.5,
            expected_excess_return=0.02,
        )


def test_quant_prediction_unavailable_requires_reason_and_forbids_values():
    with pytest.raises(ValidationError, match="must not carry values"):
        QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="unavailable",
            reason="insufficient_history",
            p_outperform=0.6,
        )
    with pytest.raises(ValidationError, match="requires a reason"):
        QuantPrediction(model_version=QUANT_MODEL, source_status="unavailable")


def test_quant_prediction_requires_model_version():
    with pytest.raises(ValidationError):
        QuantPrediction(
            model_version="",
            source_status="produced",
            p_outperform=0.6,
            expected_excess_return=0.02,
        )


# --- FrozenEvidence requires the quant input --------------------------------


def test_frozen_evidence_requires_quant():
    # the omission case: construct the bundle without the quant field
    rows = [{"security_id": "stock-a", "trade_date": "2026-09-25", "close": "103"}]
    content_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="quant"):
        FrozenEvidence(
            run_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            case={
                "case_id": str(uuid.uuid4()),
                "security_id": str(uuid.uuid4()),
                "benchmark_security_id": str(uuid.uuid4()),
                "horizon_td": 20,
                "target_spec_id": "target-spec-v1",
                "target_spec_sha256": "a" * 64,
                "decision_cutoff_utc": "2026-09-26T10:00:00Z",
                "prediction_deadline_utc": "2026-09-28T13:15:00Z",
                "entry_at_utc": "2026-09-28T13:30:00Z",
                "exit_at_utc": "2026-10-23T20:00:00Z",
            },
            evidence={
                "snapshot_id": str(uuid.uuid4()),
                "kind": "daily_bars",
                "as_of": "2026-09-26T10:00:00Z",
                "mode": "forward",
                "content_sha256": content_hash,
                "content": rows,
                "manifest": {"code_version": "daily-bars-snapshot-v1"},
            },
            target_policy_sha256="a" * 64,
            batch_manifest={},
        )


def test_evidence_roundtrips_quant_through_wire_json():
    evidence = _evidence()
    wire = evidence.model_dump(mode="json")
    assert wire["quant"]["model_version"] == QUANT_MODEL
    parsed = FrozenEvidence.model_validate(wire)
    assert parsed.quant == evidence.quant


# --- proposal must state its relation to the quant prediction ---------------


def test_produced_proposal_over_produced_quant_must_state_relation():
    evidence = _evidence()
    proposal = _proposal(evidence, quant_relation=None)
    with pytest.raises(ValueError, match="quant_relation"):
        validate_proposal_references(evidence, proposal)


@pytest.mark.parametrize("relation", ["kept", "adjusted"])
def test_produced_proposal_accepts_kept_or_adjusted(relation):
    evidence = _evidence()
    proposal = _proposal(evidence, quant_relation=relation)
    validate_proposal_references(evidence, proposal)


def test_quant_relation_rejected_when_quant_unavailable():
    evidence = _evidence(
        quant=QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="unavailable",
            reason="insufficient_history",
        )
    )
    proposal = _proposal(evidence, quant_relation="kept")
    with pytest.raises(ValueError, match="quant_relation"):
        validate_proposal_references(evidence, proposal)


def test_quant_relation_rejected_when_proposal_unavailable():
    evidence = _evidence()
    proposal = ResearchProposal(
        run_id=evidence.run_id,
        case_id=evidence.case.case_id,
        source_status="unavailable",
        reason="insufficient evidence",
        quant_relation="adjusted",
    )
    with pytest.raises(ValueError, match="quant_relation"):
        validate_proposal_references(evidence, proposal)


def test_unavailable_proposal_over_unavailable_quant_needs_no_relation():
    evidence = _evidence(
        quant=QuantPrediction(
            model_version=QUANT_MODEL,
            source_status="unavailable",
            reason="insufficient_history",
        )
    )
    proposal = ResearchProposal(
        run_id=evidence.run_id,
        case_id=evidence.case.case_id,
        source_status="unavailable",
        reason="no benchmark bars at as_of",
    )
    validate_proposal_references(evidence, proposal)
