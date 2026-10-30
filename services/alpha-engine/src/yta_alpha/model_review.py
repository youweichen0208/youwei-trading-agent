"""Prepare an evidence-bound review decision; persistence and authorization are separate."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_proposals import ModelProposalRegistry, ProposalResponse
from yta_alpha.model_quality_contracts import AssessmentId, QualityAssessmentResponse
from yta_alpha.model_quality_registry import ModelQualityRegistry
from yta_alpha.models import Contract, DataMode


class ModelReviewRequest(Contract):
    proposal_id: UUID
    assessment_id: AssessmentId
    action: Literal["APPROVE", "REJECT"]
    reason: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


def review_decision(reviewer_id, request_id, mode, request, proposal, assessment):
    """Only verified stored evidence is eligible input. Never interprets model text as approval."""
    reviewer_id, request_id = UUID(str(reviewer_id)), UUID(str(request_id))
    request = ModelReviewRequest.model_validate(request)
    submitted = ProposalResponse.model_validate(proposal)
    checked = QualityAssessmentResponse.model_validate(assessment)
    p = submitted.proposal
    if (
        submitted.id != request.proposal_id
        or submitted.content_hash != content_hash(p.model_dump(mode="json"))
        or request.assessment_id != "historical-quality-assessment-" + checked.content_hash
        or p.data_mode != mode
        or checked.policy.data_mode != mode
        or p.candidate_evaluation_id != checked.candidate_evaluation_id
        or p.reference_evaluation_id != checked.reference_evaluation_id
        or p.cohort_hash != checked.cohort_hash
    ):
        raise DomainError(
            "MODEL_REVIEW_EVIDENCE_MISMATCH", "Review must bind the exact proposal evidence"
        )
    # This schema only admits historical UNVERIFIED assessments. Even a valid human
    # request cannot convert numeric success into qualified promotion evidence.
    blockers = ["HISTORICAL_EVIDENCE_UNQUALIFIED"]
    if not checked.numeric_pass:
        blockers.append("NUMERIC_GATE_FAILED")
    result = {
        "schema_version": "model-review-decision-v1",
        "reviewer_id": str(reviewer_id),
        "request_id": str(request_id),
        "data_mode": mode.value,
        **request.model_dump(mode="json"),
        "proposal_content_hash": submitted.content_hash,
        "assessment_content_hash": checked.content_hash,
        "policy_hash": checked.policy_hash,
        "candidate_model_id": p.candidate_model_id,
        "disposition": "REJECTED" if request.action == "REJECT" else "BLOCKED",
        "blocking_reasons": [] if request.action == "REJECT" else blockers,
        "failed_checks": [item.name for item in checked.checks if not item.passed],
        "promotion_allowed": False,
    }
    result["content_hash"] = content_hash(result)
    return result


class ModelReviewService:
    def __init__(self, url, calendar):
        self.proposals = ModelProposalRegistry(url, calendar)
        self.quality = ModelQualityRegistry(url, calendar)

    def evaluate(self, reviewer_id, request_id, mode: DataMode, request: ModelReviewRequest):
        """Internal preflight only: caller must authenticate/authorize reviewer in the BFF.

        Reads the proposal owner from storage, never from the request body. This
        method does not persist the resulting decision or change proposal status.
        """
        request = ModelReviewRequest.model_validate(request)
        with self.proposals._connect() as connection:
            row = connection.execute(
                "SELECT owner_id FROM yta.model_proposals WHERE id=%s AND data_mode=%s",
                (request.proposal_id, mode.value),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
        proposal = self.proposals.get(row["owner_id"], mode, request.proposal_id)
        assessment = self.quality.get(request.assessment_id, data_mode=mode)
        return review_decision(reviewer_id, request_id, mode, request, proposal, assessment)
