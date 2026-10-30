"""Bind price and probability screening to one immutable proposal and evaluation."""

from uuid import UUID

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import ExperimentalPriceReport
from yta_alpha.model_proposals import ModelProposalRegistry, ProposalResponse
from yta_alpha.model_quality_contracts import AssessmentId, QualityAssessmentResponse
from yta_alpha.model_quality_registry import ModelQualityRegistry
from yta_alpha.models import Contract, DataMode
from yta_alpha.probability_quality_contracts import (
    ProbabilityAssessmentId,
    ProbabilityAssessmentResponse,
)
from yta_alpha.probability_quality_registry import ProbabilityQualityRegistry


class ModelQualityGateRequest(Contract):
    proposal_id: UUID
    price_assessment_id: AssessmentId
    probability_assessment_id: ProbabilityAssessmentId


def combine_quality(mode, request, proposal, candidate, price, probability):
    """Called after registry replay. Matching hashes alone do not qualify provenance."""
    mode = DataMode(mode)
    request = ModelQualityGateRequest.model_validate(
        request.model_dump(mode="json") if isinstance(request, ModelQualityGateRequest) else request
    )
    submitted = ProposalResponse.model_validate(proposal)
    report = ExperimentalPriceReport.model_validate(candidate)
    price = QualityAssessmentResponse.model_validate(price)
    probability = ProbabilityAssessmentResponse.model_validate(probability)
    p = submitted.proposal
    body = report.model_dump(mode="json", by_alias=True)
    body.pop("content_hash")
    if (
        submitted.id != request.proposal_id
        or submitted.content_hash != content_hash(p.model_dump(mode="json"))
        or report.content_hash != content_hash(body)
        or report.cohort_hash != content_hash(report.cohort.model_dump(mode="json"))
        or p.data_mode != mode
        or report.cohort.data_mode != mode
        or price.policy.data_mode != mode
        or probability.policy.data_mode != mode
        or p.candidate_evaluation_id != "model-evaluation-experimental-" + report.content_hash
        or p.candidate_report_hash != report.content_hash
        or p.candidate_model_id != report.model_id
        or p.cohort_hash != report.cohort_hash
        or price.candidate_evaluation_id != p.candidate_evaluation_id
        or price.reference_evaluation_id != p.reference_evaluation_id
        or price.cohort_hash != p.cohort_hash
        or request.price_assessment_id != "historical-quality-assessment-" + price.content_hash
        or request.probability_assessment_id
        != "probability-quality-assessment-" + probability.content_hash
        or probability.model_id != report.model_id
        or probability.calibration_id != report.calibration_id
        or probability.calibration_content_hash != report.calibration_content_hash
        or probability.evaluation_cohort_hash != content_hash(list(report.cohort.label_ids))
        or price.policy.horizon_sessions != report.cohort.horizon_sessions
        or probability.policy.horizon_sessions != report.cohort.horizon_sessions
    ):
        raise DomainError(
            "MODEL_QUALITY_GATE_EVIDENCE_MISMATCH",
            "Price and probability checks must bind the exact proposal evidence",
        )
    blockers = ["HISTORICAL_EVIDENCE_UNQUALIFIED", "HUMAN_APPROVAL_REQUIRED"]
    if not price.numeric_pass:
        blockers.append("PRICE_NUMERIC_GATE_FAILED")
    if not probability.numeric_pass:
        blockers.append("PROBABILITY_NUMERIC_GATE_FAILED")
    result = {
        "schema_version": "model-quality-gate-v1",
        "data_mode": mode.value,
        **request.model_dump(mode="json"),
        "proposal_content_hash": submitted.content_hash,
        "candidate_evaluation_id": p.candidate_evaluation_id,
        "candidate_model_id": report.model_id,
        "reference_evaluation_id": p.reference_evaluation_id,
        "cohort_hash": report.cohort_hash,
        "calibration_id": report.calibration_id,
        "calibration_content_hash": report.calibration_content_hash,
        "probability_report_hash": probability.report_hash,
        "calibration_cohort_hash": probability.calibration_cohort_hash,
        "evaluation_cohort_hash": probability.evaluation_cohort_hash,
        "price_policy_hash": price.policy_hash,
        "probability_policy_hash": probability.policy_hash,
        "price_numeric_pass": price.numeric_pass,
        "probability_numeric_pass": probability.numeric_pass,
        "numeric_pass": price.numeric_pass and probability.numeric_pass,
        "failed_price_checks": [item.name for item in price.checks if not item.passed],
        "failed_probability_checks": [item.name for item in probability.checks if not item.passed],
        "qualification": "UNVERIFIED",
        "promotion_allowed": False,
        "promotion_blockers": blockers,
    }
    result["content_hash"] = content_hash(result)
    return result


class ModelQualityGateService:
    def __init__(self, url, calendar):
        self.proposals = ModelProposalRegistry(url, calendar)
        self.price = ModelQualityRegistry(url, calendar)
        self.probability = ProbabilityQualityRegistry(url, calendar)

    def evaluate(self, owner_id, mode, request):
        """Read-only preflight. Owner/mode must come from the authenticated caller."""
        mode = DataMode(mode)
        request = ModelQualityGateRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, ModelQualityGateRequest)
            else request
        )
        proposal = self.proposals.get(owner_id, mode, request.proposal_id)
        price = self.price.get(request.price_assessment_id, data_mode=mode)
        probability = self.probability.get(request.probability_assessment_id, data_mode=mode)
        candidate = self.price.reports.get(
            proposal["proposal"]["candidate_evaluation_id"], data_mode=mode
        )
        return combine_quality(mode, request, proposal, candidate, price, probability)
