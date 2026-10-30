"""Strict transport for combined, unqualified model screening evidence."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import CalibrationId, EvaluationId, ModelId
from yta_alpha.model_quality_contracts import FailedCheck
from yta_alpha.model_quality_gate import ModelQualityGateRequest
from yta_alpha.models import SHA256, DataMode

QualityGateId = Annotated[str, Field(pattern=r"^model-quality-gate-[a-f0-9]{64}$")]
ProbabilityFailedCheck = Literal[
    "minimum_calibration_samples",
    "minimum_evaluation_samples",
    "minimum_evaluation_up",
    "minimum_evaluation_non_up",
    "maximum_brier",
    "maximum_ece",
]


class ModelQualityGateResponse(ModelQualityGateRequest):
    schema_version: Literal["model-quality-gate-v1"]
    data_mode: DataMode
    proposal_content_hash: SHA256
    candidate_evaluation_id: EvaluationId
    candidate_model_id: ModelId
    reference_evaluation_id: EvaluationId | None
    cohort_hash: SHA256
    calibration_id: CalibrationId
    calibration_content_hash: SHA256
    probability_report_hash: SHA256
    calibration_cohort_hash: SHA256
    evaluation_cohort_hash: SHA256
    price_policy_hash: SHA256
    probability_policy_hash: SHA256
    price_numeric_pass: StrictBool
    probability_numeric_pass: StrictBool
    numeric_pass: StrictBool
    failed_price_checks: tuple[FailedCheck, ...]
    failed_probability_checks: tuple[ProbabilityFailedCheck, ...]
    qualification: Literal["UNVERIFIED"]
    promotion_allowed: Literal[False]
    promotion_blockers: tuple[
        Literal[
            "HISTORICAL_EVIDENCE_UNQUALIFIED",
            "HUMAN_APPROVAL_REQUIRED",
            "PRICE_NUMERIC_GATE_FAILED",
            "PROBABILITY_NUMERIC_GATE_FAILED",
        ],
        ...,
    ]
    content_hash: SHA256

    @model_validator(mode="after")
    def consistent_result(self):
        blockers = ["HISTORICAL_EVIDENCE_UNQUALIFIED", "HUMAN_APPROVAL_REQUIRED"]
        if not self.price_numeric_pass:
            blockers.append("PRICE_NUMERIC_GATE_FAILED")
        if not self.probability_numeric_pass:
            blockers.append("PROBABILITY_NUMERIC_GATE_FAILED")
        price_order = [
            "minimum_samples",
            "maximum_mae",
            "minimum_direction_accuracy",
            "required_reference",
            "mae_improvement:zero-return-v1",
            "mae_improvement:momentum20-v1",
            "mae_improvement:reference",
        ]
        probability_order = [
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
            self.price_numeric_pass != (not self.failed_price_checks)
            or self.probability_numeric_pass != (not self.failed_probability_checks)
            or self.numeric_pass != (self.price_numeric_pass and self.probability_numeric_pass)
            or list(self.promotion_blockers) != blockers
            or list(self.failed_price_checks)
            != [n for n in price_order if n in self.failed_price_checks]
            or list(self.failed_probability_checks)
            != [n for n in probability_order if n in self.failed_probability_checks]
            or (
                self.reference_evaluation_id is None
                and "mae_improvement:reference" in self.failed_price_checks
            )
            or content_hash(body) != self.content_hash
        ):
            raise ValueError("Combined quality identity or results differ")
        return self
