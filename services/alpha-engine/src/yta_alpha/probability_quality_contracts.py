"""Strict transport of fixed probability screening evidence, never approval."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from yta_alpha.calibration_contracts import Count, ProbabilityString
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import CalibrationId, ModelId
from yta_alpha.models import SHA256, Contract
from yta_alpha.probability_quality import (
    MinimumCount,
    ProbabilityQualityPolicy,
    ProbabilityThreshold,
)

ProbabilityPolicyId = Annotated[str, Field(pattern=r"^probability-quality-policy-[a-f0-9]{64}$")]
ProbabilityAssessmentId = Annotated[
    str, Field(pattern=r"^probability-quality-assessment-[a-f0-9]{64}$")
]


class ProbabilityAssessmentRequest(Contract):
    policy_id: ProbabilityPolicyId
    calibration_id: CalibrationId


class ProbabilityCountCheck(Contract):
    name: Literal[
        "minimum_calibration_samples",
        "minimum_evaluation_samples",
        "minimum_evaluation_up",
        "minimum_evaluation_non_up",
    ]
    observed: Count
    required: MinimumCount
    passed: StrictBool


class ProbabilityMetricCheck(Contract):
    name: Literal["maximum_brier", "maximum_ece"]
    observed: ProbabilityString | None
    required: ProbabilityThreshold
    passed: StrictBool


ProbabilityCheck = Annotated[
    ProbabilityCountCheck | ProbabilityMetricCheck, Field(discriminator="name")
]


class ProbabilityPolicyResponse(Contract):
    id: ProbabilityPolicyId
    content_hash: SHA256
    policy: ProbabilityQualityPolicy

    @model_validator(mode="after")
    def identity(self):
        if (
            content_hash(self.policy.model_dump(mode="json")) != self.content_hash
            or self.id != "probability-quality-policy-" + self.content_hash
        ):
            raise ValueError("Probability policy identity differs")
        return self


class ProbabilityAssessmentResponse(Contract):
    schema_version: Literal["probability-quality-assessment-v1"]
    calibration_id: CalibrationId
    model_id: ModelId
    calibration_content_hash: SHA256
    report_hash: SHA256
    calibration_cohort_hash: SHA256
    evaluation_cohort_hash: SHA256
    policy: ProbabilityQualityPolicy
    policy_hash: SHA256
    checks: tuple[ProbabilityCheck, ...]
    numeric_pass: StrictBool
    qualification: Literal["UNVERIFIED"]
    promotion_allowed: Literal[False]
    promotion_blockers: tuple[
        Literal["HISTORICAL_EVIDENCE_UNQUALIFIED"], Literal["HUMAN_APPROVAL_REQUIRED"]
    ]
    content_hash: SHA256

    @model_validator(mode="after")
    def identity_and_checks(self):
        names = [
            "minimum_calibration_samples",
            "minimum_evaluation_samples",
            "minimum_evaluation_up",
            "minimum_evaluation_non_up",
            "maximum_brier",
            "maximum_ece",
        ]
        body = self.model_dump(mode="json")
        body.pop("content_hash")
        if (
            content_hash(self.policy.model_dump(mode="json")) != self.policy_hash
            or content_hash(body) != self.content_hash
            or [item.name for item in self.checks] != names
            or self.numeric_pass != all(item.passed for item in self.checks)
        ):
            raise ValueError("Probability assessment identity or checks differ")
        for item in self.checks:
            passed = (
                item.observed >= item.required
                if isinstance(item, ProbabilityCountCheck)
                else item.observed is not None and Decimal(item.observed) <= Decimal(item.required)
            )
            if item.required != getattr(self.policy, item.name) or item.passed != passed:
                raise ValueError("Probability check differs from policy or observed value")
        if self.checks[1].observed != self.checks[2].observed + self.checks[3].observed:
            raise ValueError("Probability class counts differ")
        return self
