"""Strict wire records for blocked/rejected reviews, never approved model state."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import ModelId, Timestamp
from yta_alpha.model_quality_contracts import AssessmentId, FailedCheck
from yta_alpha.model_quality_gate_contracts import ProbabilityFailedCheck
from yta_alpha.model_review import ModelReviewRequest
from yta_alpha.model_review_v2 import CombinedReviewRequest
from yta_alpha.models import SHA256, Contract, DataMode
from yta_alpha.probability_quality_contracts import ProbabilityAssessmentId


class ModelReviewRecord(ModelReviewRequest):
    schema_version: Literal["model-review-record-v1"]
    reviewer_id: UUID
    request_id: UUID
    data_mode: DataMode
    proposal_content_hash: SHA256
    assessment_content_hash: SHA256
    policy_hash: SHA256
    candidate_model_id: ModelId
    disposition: Literal["BLOCKED", "REJECTED"]
    blocking_reasons: tuple[Literal["HISTORICAL_EVIDENCE_UNQUALIFIED", "NUMERIC_GATE_FAILED"], ...]
    failed_checks: tuple[FailedCheck, ...]
    promotion_allowed: Literal[False]
    previous_review_id: UUID | None
    revision: Annotated[StrictInt, Field(ge=1)]
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent_record(self):
        expected = "REJECTED" if self.action == "REJECT" else "BLOCKED"
        blockers = [] if expected == "REJECTED" else ["HISTORICAL_EVIDENCE_UNQUALIFIED"]
        if expected == "BLOCKED" and self.failed_checks:
            blockers.append("NUMERIC_GATE_FAILED")
        body = self.model_dump(mode="json")
        body.pop("content_hash")
        if (
            self.disposition != expected
            or list(self.blocking_reasons) != blockers
            or len(set(self.failed_checks)) != len(self.failed_checks)
            or (self.revision == 1) != (self.previous_review_id is None)
            or self.assessment_id != "historical-quality-assessment-" + self.assessment_content_hash
            or content_hash(body) != self.content_hash
        ):
            raise ValueError("Review record identity or state differs")
        return self


class CombinedModelReviewRecord(CombinedReviewRequest):
    schema_version: Literal["model-review-record-v2"]
    reviewer_id: UUID
    request_id: UUID
    data_mode: DataMode
    proposal_content_hash: SHA256
    quality_gate_content_hash: SHA256
    price_assessment_id: AssessmentId
    probability_assessment_id: ProbabilityAssessmentId
    price_policy_hash: SHA256
    probability_policy_hash: SHA256
    candidate_model_id: ModelId
    disposition: Literal["BLOCKED", "REJECTED"]
    blocking_reasons: tuple[
        Literal[
            "HISTORICAL_EVIDENCE_UNQUALIFIED",
            "PRICE_NUMERIC_GATE_FAILED",
            "PROBABILITY_NUMERIC_GATE_FAILED",
        ],
        ...,
    ]
    failed_price_checks: tuple[FailedCheck, ...]
    failed_probability_checks: tuple[ProbabilityFailedCheck, ...]
    promotion_allowed: Literal[False]
    previous_review_id: UUID | None
    revision: Annotated[StrictInt, Field(ge=1)]
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent_record(self):
        expected = "REJECTED" if self.action == "REJECT" else "BLOCKED"
        blockers = [] if expected == "REJECTED" else ["HISTORICAL_EVIDENCE_UNQUALIFIED"]
        if expected == "BLOCKED":
            if self.failed_price_checks:
                blockers.append("PRICE_NUMERIC_GATE_FAILED")
            if self.failed_probability_checks:
                blockers.append("PROBABILITY_NUMERIC_GATE_FAILED")
        body = self.model_dump(mode="json")
        body.pop("content_hash")
        if (
            self.disposition != expected
            or list(self.blocking_reasons) != blockers
            or len(set(self.failed_price_checks)) != len(self.failed_price_checks)
            or len(set(self.failed_probability_checks)) != len(self.failed_probability_checks)
            or (self.revision == 1) != (self.previous_review_id is None)
            or self.quality_gate_id != "model-quality-gate-" + self.quality_gate_content_hash
            or content_hash(body) != self.content_hash
        ):
            raise ValueError("Combined review record identity or state differs")
        return self


class ModelReviewResponse(Contract):
    id: UUID
    created_at: Timestamp
    review: Annotated[
        ModelReviewRecord | CombinedModelReviewRecord, Field(discriminator="schema_version")
    ]


class ModelReviewPage(Contract):
    items: tuple[ModelReviewResponse, ...]
    next_cursor: UUID | None
