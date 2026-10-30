"""Strict transport for immutable historical quality evidence, never approval."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import EvaluationId, NonnegativeString, ReturnString
from yta_alpha.model_quality import HistoricalQualityPolicy, Threshold
from yta_alpha.models import SHA256, Contract

PolicyId = Annotated[str, Field(pattern=r"^historical-quality-policy-[a-f0-9]{64}$")]
AssessmentId = Annotated[str, Field(pattern=r"^historical-quality-assessment-[a-f0-9]{64}$")]


class QualityAssessmentRequest(Contract):
    policy_id: PolicyId
    candidate_evaluation_id: EvaluationId
    reference_evaluation_id: EvaluationId | None = None


class SampleCheck(Contract):
    name: Literal["minimum_samples"]
    observed: Annotated[StrictInt, Field(ge=0)]
    required: Annotated[StrictInt, Field(ge=1)]
    passed: StrictBool


class MetricCheck(Contract):
    name: Literal["maximum_mae", "minimum_direction_accuracy"]
    observed: NonnegativeString | None
    required: Threshold
    passed: StrictBool


class ReferenceCheck(Contract):
    name: Literal["required_reference"]
    observed: StrictBool
    required: StrictBool
    passed: StrictBool


class ImprovementCheck(Contract):
    name: Literal[
        "mae_improvement:zero-return-v1",
        "mae_improvement:momentum20-v1",
        "mae_improvement:reference",
    ]
    observed: ReturnString | None
    required: Threshold
    passed: StrictBool


QualityCheck = Annotated[
    SampleCheck | MetricCheck | ReferenceCheck | ImprovementCheck, Field(discriminator="name")
]


class QualityPolicyResponse(Contract):
    id: PolicyId
    content_hash: SHA256
    policy: HistoricalQualityPolicy

    @model_validator(mode="after")
    def identity(self):
        if (
            content_hash(self.policy.model_dump(mode="json")) != self.content_hash
            or self.id != "historical-quality-policy-" + self.content_hash
        ):
            raise ValueError("Policy identity differs")
        return self


class QualityAssessmentResponse(Contract):
    schema_version: Literal["historical-quality-assessment-v1"]
    candidate_evaluation_id: EvaluationId
    reference_evaluation_id: EvaluationId | None
    cohort_hash: SHA256
    policy: HistoricalQualityPolicy
    policy_hash: SHA256
    checks: tuple[QualityCheck, ...]
    numeric_pass: StrictBool
    qualification: Literal["UNVERIFIED"]
    promotion_allowed: Literal[False]
    promotion_blockers: tuple[
        Literal["HISTORICAL_EVIDENCE_UNQUALIFIED"], Literal["HUMAN_APPROVAL_REQUIRED"]
    ]
    content_hash: SHA256

    @model_validator(mode="after")
    def identity_and_check_set(self):
        names = [
            "minimum_samples",
            "maximum_mae",
            "minimum_direction_accuracy",
            "required_reference",
            "mae_improvement:zero-return-v1",
        ]
        if self.policy.horizon_sessions == 5:
            names.append("mae_improvement:momentum20-v1")
        if self.reference_evaluation_id is not None:
            names.append("mae_improvement:reference")
        body = self.model_dump(mode="json")
        body.pop("content_hash")
        if (
            content_hash(self.policy.model_dump(mode="json")) != self.policy_hash
            or content_hash(body) != self.content_hash
            or [item.name for item in self.checks] != names
            or self.numeric_pass != all(item.passed for item in self.checks)
        ):
            raise ValueError("Assessment identity or checks differ")
        return self


FailedCheck = Literal[
    "minimum_samples",
    "maximum_mae",
    "minimum_direction_accuracy",
    "required_reference",
    "mae_improvement:zero-return-v1",
    "mae_improvement:momentum20-v1",
    "mae_improvement:reference",
]
