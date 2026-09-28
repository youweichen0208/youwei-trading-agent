"""S07f: Controller proposal reception (offline).

The llm_adjusted position in Phase 1B comes from a Hermes ResearchProposal.
These tests pin the pure mapping (proposal -> SourcePrediction) and the
fallback decision (campaign-policy §3: LLM failure copies the quant output
marked fallback), without a database, gateway, or real model.
"""

import uuid

import pytest

from youwei_contracts.research import ResearchProposal
from youwei_core.ledger.controller import (
    PHASE1B_FALLBACK_POLICY,
    ProposalReceptionError,
    apply_phase1b_fallback,
    proposal_to_llm_adjusted,
)
from youwei_core.ledger.sealing import SourcePrediction


def _proposal(**overrides) -> ResearchProposal:
    base = {
        "run_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "source_status": "produced",
        "p_outperform": 0.6,
        "expected_excess_return": 0.02,
        "references": [{"kind": "evidence", "locator": "snapshot:00000000-0000-0000-0000-000000000030/rows/0"}],
        "model": {"model_version": "llm-v1", "provider": "test"},
    }
    base.update(overrides)
    return ResearchProposal(**base)


def _quant(**overrides) -> SourcePrediction:
    base = {
        "source": "quant_model",
        "source_status": "produced",
        "p_outperform": 0.4,
        "expected_excess_return": 0.01,
        "evidence_snapshot_id": uuid.uuid4(),
        "model_version": "quant-momentum-v0",
    }
    base.update(overrides)
    return SourcePrediction(**base)


SNAP = uuid.uuid4()


def test_produced_proposal_maps_to_llm_adjusted_source_prediction():
    proposal = _proposal()
    pred = proposal_to_llm_adjusted(proposal, evidence_snapshot_id=SNAP)
    assert pred.source == "llm_adjusted"
    assert pred.source_status == "produced"
    assert pred.p_outperform == 0.6
    assert pred.expected_excess_return == 0.02
    assert pred.evidence_snapshot_id == SNAP
    assert pred.model_version == "llm-v1"


def test_unavailable_proposal_maps_with_reason_and_no_values():
    proposal = _proposal(
        source_status="unavailable", reason="insufficient_history",
        p_outperform=None, expected_excess_return=None, model=None,
    )
    pred = proposal_to_llm_adjusted(proposal, evidence_snapshot_id=SNAP)
    assert pred.source_status == "unavailable"
    assert pred.reason == "insufficient_history"
    assert pred.p_outperform is None
    assert pred.expected_excess_return is None
    assert pred.evidence_snapshot_id == SNAP


def test_produced_proposal_without_model_is_rejected_at_contract_layer():
    # The wire contract already enforces produced -> attributable model;
    # a proposal reaching the Controller can never be in this state.
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="attributable model"):
        _proposal(model=None)


def test_produced_proposal_passes_through_without_fallback():
    proposal = _proposal()
    reception = apply_phase1b_fallback(
        proposal, quant_prediction=_quant(), evidence_snapshot_id=SNAP
    )
    assert reception.fallback_applied is False
    assert reception.prediction.source_status == "produced"
    assert reception.prediction.p_outperform == 0.6


def test_llm_unavailable_falls_back_to_quant_when_quant_produced():
    proposal = _proposal(
        source_status="unavailable", reason="timeout",
        p_outperform=None, expected_excess_return=None, model=None,
    )
    quant = _quant(p_outperform=0.4, expected_excess_return=0.01)
    reception = apply_phase1b_fallback(
        proposal, quant_prediction=quant, evidence_snapshot_id=SNAP
    )
    assert reception.fallback_applied is True
    assert reception.fallback_from == "quant_model"
    assert reception.prediction.source_status == "fallback"
    assert reception.prediction.p_outperform == 0.4
    assert reception.prediction.expected_excess_return == 0.01
    assert reception.prediction.model_version == "quant-momentum-v0"
    # the LLM failure reason is preserved for evaluation
    assert reception.prediction.reason == "timeout"


def test_llm_unavailable_stays_unavailable_when_quant_has_no_value():
    proposal = _proposal(
        source_status="unavailable", reason="timeout",
        p_outperform=None, expected_excess_return=None, model=None,
    )
    quant = _quant(
        source_status="unavailable", reason="insufficient_history",
        p_outperform=None, expected_excess_return=None,
    )
    reception = apply_phase1b_fallback(
        proposal, quant_prediction=quant, evidence_snapshot_id=SNAP
    )
    assert reception.fallback_applied is False
    assert reception.prediction.source_status == "unavailable"


def test_unsupported_fallback_policy_is_rejected():
    proposal = _proposal()
    with pytest.raises(ProposalReceptionError, match="fallback policy"):
        apply_phase1b_fallback(
            proposal, quant_prediction=_quant(), evidence_snapshot_id=SNAP,
            fallback_policy="phase1a-none",
        )


def test_fallback_policy_constant_matches_protocol():
    assert PHASE1B_FALLBACK_POLICY == "phase1b-llm-from-quant"
