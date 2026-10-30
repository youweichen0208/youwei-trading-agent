"""Versioned review preflight bound to a persisted combined quality gate."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_proposals import ProposalResponse
from yta_alpha.model_quality_gate_contracts import ModelQualityGateResponse, QualityGateId
from yta_alpha.model_quality_gate_registry import ModelQualityGateRegistry
from yta_alpha.models import Contract, DataMode


class CombinedReviewRequest(Contract):
    proposal_id: UUID
    quality_gate_id: QualityGateId
    action: Literal["APPROVE", "REJECT"]
    reason: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


def combined_review_decision(reviewer_id, request_id, mode, request, proposal, gate):
    """Caller must authorize the human reviewer and replay all stored evidence first."""
    reviewer_id, request_id, mode = UUID(str(reviewer_id)), UUID(str(request_id)), DataMode(mode)
    request = CombinedReviewRequest.model_validate(
        request.model_dump(mode="json") if isinstance(request, CombinedReviewRequest) else request
    )
    submitted = ProposalResponse.model_validate(proposal)
    checked = ModelQualityGateResponse.model_validate(gate)
    p = submitted.proposal
    if (
        submitted.id != request.proposal_id
        or submitted.content_hash != content_hash(p.model_dump(mode="json"))
        or checked.proposal_id != request.proposal_id
        or checked.proposal_content_hash != submitted.content_hash
        or request.quality_gate_id != "model-quality-gate-" + checked.content_hash
        or p.data_mode != mode
        or checked.data_mode != mode
        or checked.candidate_evaluation_id != p.candidate_evaluation_id
        or checked.reference_evaluation_id != p.reference_evaluation_id
        or checked.candidate_model_id != p.candidate_model_id
        or checked.cohort_hash != p.cohort_hash
    ):
        raise DomainError(
            "MODEL_REVIEW_EVIDENCE_MISMATCH",
            "Review must bind the exact proposal and combined gate",
        )
    # Human intent is now supplied, but cannot grant missing historical qualification.
    blockers = [item for item in checked.promotion_blockers if item != "HUMAN_APPROVAL_REQUIRED"]
    result = {
        "schema_version": "model-review-decision-v2",
        "reviewer_id": str(reviewer_id),
        "request_id": str(request_id),
        "data_mode": mode.value,
        **request.model_dump(mode="json"),
        "proposal_content_hash": submitted.content_hash,
        "quality_gate_content_hash": checked.content_hash,
        "price_assessment_id": checked.price_assessment_id,
        "probability_assessment_id": checked.probability_assessment_id,
        "price_policy_hash": checked.price_policy_hash,
        "probability_policy_hash": checked.probability_policy_hash,
        "candidate_model_id": p.candidate_model_id,
        "disposition": "REJECTED" if request.action == "REJECT" else "BLOCKED",
        "blocking_reasons": [] if request.action == "REJECT" else blockers,
        "failed_price_checks": list(checked.failed_price_checks),
        "failed_probability_checks": list(checked.failed_probability_checks),
        "promotion_allowed": False,
    }
    result["content_hash"] = content_hash(result)
    return result


class CombinedModelReviewService:
    def __init__(self, url, calendar):
        self.gates = ModelQualityGateRegistry(url, calendar)
        self.proposals = self.gates.service.proposals

    def evaluate(self, reviewer_id, request_id, mode, request):
        """Internal read-only preflight; BFF reviewer authorization is a prerequisite."""
        reviewer_id, request_id, mode = (
            UUID(str(reviewer_id)),
            UUID(str(request_id)),
            DataMode(mode),
        )
        request = CombinedReviewRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, CombinedReviewRequest)
            else request
        )
        with self.proposals._connect() as connection:
            row = connection.execute(
                "SELECT owner_id FROM yta.model_proposals WHERE id=%s AND data_mode=%s",
                (request.proposal_id, mode.value),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
        proposal = self.proposals.get(row["owner_id"], mode, request.proposal_id)
        gate = self.gates.get(row["owner_id"], mode, request.quality_gate_id)
        return combined_review_decision(reviewer_id, request_id, mode, request, proposal, gate)
