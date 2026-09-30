"""Controller-side research proposal reception (S07f, offline).

This module maps a Hermes ``ResearchProposal`` (contracts.research, produced
by the agent-runtime) into the Ledger's ``SourcePrediction`` for the
``llm_adjusted`` position, and implements the Phase 1B fallback decision.

It is pure logic over the shared ``youwei-contracts`` types plus the sealing
``SourcePrediction``: no database, no attempt/fencing, no gateway. The actual
sealing (``seal_commit``) still runs through the existing fencing + window +
evidence-discipline path; this module only turns a proposal into the value
the sealing path already knows how to validate.

Why this lives in Core, not agent-runtime: the agent-runtime is an
independent Python 3.14 package that must not import Core. The Controller
(which owns sealing, fencing, and the evidence snapshot identity)
receives the proposal across a process boundary and maps it here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from youwei_contracts.research import ResearchProposal
from youwei_core.ledger.sealing import SourcePrediction
from youwei_core.ledger.service import PHASE1B_FALLBACK_POLICY


class ProposalReceptionError(Exception):
    """The proposal cannot be turned into a sealable llm_adjusted position."""


@dataclass(frozen=True)
class ProposalReception:
    """The llm_adjusted SourcePrediction derived from a proposal, plus the
    fallback decision made by the Controller (never by Hermes)."""

    prediction: SourcePrediction
    fallback_applied: bool
    # When fallback was applied: which source position the value was copied
    # from and why (preserved for evaluation to distinguish actual fallback
    # from an original LLM success — campaign-policy §3).
    fallback_from: str | None = None
    fallback_reason: str | None = None


def _proposal_model_version(proposal: ResearchProposal) -> str | None:
    if proposal.model is None:
        return None
    return proposal.model.model_version


def proposal_to_llm_adjusted(
    proposal: ResearchProposal,
    *,
    evidence_snapshot_id: uuid.UUID,
) -> SourcePrediction:
    """Map a produced/unavailable ResearchProposal into the llm_adjusted
    SourcePrediction position.

    The evidence_snapshot_id is supplied by the Controller (it is the
    snapshot the Controller itself froze and authorized), never trusted from
    the proposal's references. A produced proposal must carry a model version
    (the wire contract already enforces this); it is recorded for attribution.
    """
    if proposal.source_status == "produced":
        model_version = _proposal_model_version(proposal)
        if model_version is None:
            raise ProposalReceptionError(
                "produced proposal is missing an attributable model version"
            )
        return SourcePrediction(
            source="llm_adjusted",
            source_status="produced",
            p_outperform=proposal.p_outperform,
            expected_excess_return=proposal.expected_excess_return,
            evidence_snapshot_id=evidence_snapshot_id,
            model_version=model_version,
        )

    # unavailable: no values, a reason is mandatory (wire contract enforced)
    return SourcePrediction(
        source="llm_adjusted",
        source_status="unavailable",
        reason=proposal.reason,
        evidence_snapshot_id=evidence_snapshot_id,
        model_version=_proposal_model_version(proposal),
    )


def apply_phase1b_fallback(
    proposal: ResearchProposal,
    *,
    quant_prediction: SourcePrediction,
    evidence_snapshot_id: uuid.UUID,
    fallback_policy: str = PHASE1B_FALLBACK_POLICY,
) -> ProposalReception:
    """Decide the llm_adjusted position under Phase 1B fallback policy.

    When the LLM did not produce a usable prediction (unavailable), copy the
    same case/release quant output and mark it ``fallback``; when quant has
    no valid output either, stay unavailable. A produced proposal passes
    through unchanged. The Controller records which source the value was
    copied from and why, so evaluation can distinguish fallback from an
    original LLM success (campaign-policy §3).
    """
    if fallback_policy != PHASE1B_FALLBACK_POLICY:
        raise ProposalReceptionError(
            f"unsupported fallback policy {fallback_policy!r}"
        )

    if proposal.source_status == "produced":
        return ProposalReception(
            prediction=proposal_to_llm_adjusted(
                proposal, evidence_snapshot_id=evidence_snapshot_id
            ),
            fallback_applied=False,
        )

    # LLM unavailable -> fallback to quant when quant produced a value
    if (
        quant_prediction.source_status == "produced"
        and quant_prediction.p_outperform is not None
        and quant_prediction.expected_excess_return is not None
    ):
        return ProposalReception(
            prediction=SourcePrediction(
                source="llm_adjusted",
                source_status="fallback",
                reason=proposal.reason,
                p_outperform=quant_prediction.p_outperform,
                expected_excess_return=quant_prediction.expected_excess_return,
                evidence_snapshot_id=evidence_snapshot_id,
                model_version=quant_prediction.model_version,
            ),
            fallback_applied=True,
            fallback_from="quant_model",
            fallback_reason=proposal.reason,
        )

    # neither LLM nor quant produced a value -> stay unavailable
    return ProposalReception(
        prediction=SourcePrediction(
            source="llm_adjusted",
            source_status="unavailable",
            reason=proposal.reason or "llm unavailable and quant has no valid output",
            evidence_snapshot_id=evidence_snapshot_id,
        ),
        fallback_applied=False,
    )
