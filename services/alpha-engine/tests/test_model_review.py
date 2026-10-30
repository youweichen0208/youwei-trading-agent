import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_proposals import ProposalRequest, proposal_payload
from yta_alpha.model_review import ModelReviewRequest, review_decision
from yta_alpha.models import DataMode

ROOT = Path(__file__).resolve().parents[3]


def inputs():
    assessment = json.loads(
        (ROOT / "packages/contracts/fixtures/quality-assessment.json").read_text()
    )
    reports = json.loads(
        (ROOT / "packages/contracts/fixtures/quality-source-reports.json").read_text()
    )
    request = ProposalRequest(
        candidate_evaluation_id=assessment["candidate_evaluation_id"],
        reference_evaluation_id=assessment["reference_evaluation_id"],
        rationale="synthetic",
    )
    payload = proposal_payload(uuid4(), uuid4(), DataMode.REPLAY, request, reports[0], reports[1])
    proposal = {
        "id": str(uuid4()),
        "created_at": "2026-10-29T00:00:00+00:00",
        "content_hash": content_hash(payload),
        "proposal": payload,
    }
    review = ModelReviewRequest(
        proposal_id=proposal["id"],
        assessment_id="historical-quality-assessment-" + assessment["content_hash"],
        action="APPROVE",
        reason="Human review request",
    )
    return proposal, assessment, review


def test_human_approval_request_cannot_qualify_historical_numeric_success():
    proposal, assessment, request = inputs()
    reviewer, key = uuid4(), uuid4()
    result = review_decision(reviewer, key, DataMode.REPLAY, request, proposal, assessment)
    assert result["disposition"] == "BLOCKED"
    assert "HISTORICAL_EVIDENCE_UNQUALIFIED" in result["blocking_reasons"]
    assert result["promotion_allowed"] is False
    assert result["reviewer_id"] == str(reviewer)
    assert result["request_id"] == str(key)
    assert result == review_decision(reviewer, key, DataMode.REPLAY, request, proposal, assessment)
    digest = result.pop("content_hash")
    assert content_hash(result) == digest
    rejected = review_decision(
        reviewer,
        key,
        DataMode.REPLAY,
        request.model_copy(update={"action": "REJECT"}),
        proposal,
        assessment,
    )
    assert rejected["disposition"] == "REJECTED"
    assert rejected["blocking_reasons"] == []
    assert rejected["promotion_allowed"] is False
    assert rejected["content_hash"] != digest


@pytest.mark.parametrize(
    "field", ["candidate_evaluation_id", "reference_evaluation_id", "cohort_hash"]
)
def test_review_rejects_rehashed_proposal_evidence_substitution(field):
    proposal, assessment, request = inputs()
    proposal = deepcopy(proposal)
    proposal["proposal"][field] = (
        "0" * 64 if field == "cohort_hash" else "model-evaluation-experimental-" + "0" * 64
    )
    proposal["content_hash"] = content_hash(proposal["proposal"])
    with pytest.raises(DomainError, match="exact proposal"):
        review_decision(uuid4(), uuid4(), DataMode.REPLAY, request, proposal, assessment)


def test_review_rejects_wrong_request_identity_mode_and_untrusted_fields():
    proposal, assessment, request = inputs()
    for changed in [
        request.model_copy(update={"proposal_id": uuid4()}),
        request.model_copy(update={"assessment_id": "historical-quality-assessment-" + "0" * 64}),
    ]:
        with pytest.raises(DomainError):
            review_decision(uuid4(), uuid4(), DataMode.REPLAY, changed, proposal, assessment)
    with pytest.raises(DomainError):
        review_decision(uuid4(), uuid4(), DataMode.LIVE_RESEARCH, request, proposal, assessment)
    for values in [
        {"reviewer_id": str(uuid4())},
        {"promotion_allowed": True},
        {"action": "APPROVED"},
        {"reason": " "},
        {"reason": "x" * 2001},
    ]:
        with pytest.raises(ValidationError):
            ModelReviewRequest.model_validate(request.model_dump(mode="json") | values)
