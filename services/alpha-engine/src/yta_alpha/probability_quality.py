"""Explicit numerical probability gates over fully replayed calibration evidence."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from yta_alpha.calibration import reliability
from yta_alpha.calibration_contracts import CalibrationReportResponse
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import Contract, DataMode

ProbabilityThreshold = Annotated[str, Field(pattern=r"^(?:0(?:\.[0-9]{1,12})?|1(?:\.0{1,12})?)$")]
MinimumCount = Annotated[StrictInt, Field(ge=1)]


class ProbabilityQualityPolicy(Contract):
    schema_version: Literal["probability-quality-policy-v1"] = "probability-quality-policy-v1"
    policy_version: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    minimum_calibration_samples: MinimumCount
    minimum_evaluation_samples: MinimumCount
    minimum_evaluation_up: MinimumCount
    minimum_evaluation_non_up: MinimumCount
    maximum_brier: ProbabilityThreshold
    maximum_ece: ProbabilityThreshold

    @model_validator(mode="after")
    def bounded_thresholds(self):
        if not all(
            Decimal(0) <= Decimal(v) <= Decimal(1) for v in (self.maximum_brier, self.maximum_ece)
        ):
            raise ValueError("Probability thresholds must be in [0,1]")
        return self


def assess_probability_quality(value, policy: ProbabilityQualityPolicy):
    """Pure screening after registry replay; hashes do not replace provenance verification."""
    policy = ProbabilityQualityPolicy.model_validate(
        policy.model_dump(mode="json") if isinstance(policy, ProbabilityQualityPolicy) else policy
    )
    saved = CalibrationReportResponse.model_validate(value)
    report = saved.report
    if (report.data_mode, report.horizon_sessions) != (policy.data_mode, policy.horizon_sessions):
        raise DomainError(
            "QUALITY_POLICY_SCOPE_MISMATCH", "Policy and probability report scope differ"
        )
    # Independently bind the supplied numerical summary to its per-sample evidence.
    expected = reliability(
        tuple(Decimal(value) for value in report.probabilities), report.evaluation_targets
    )
    if expected != report.reliability.model_dump(mode="json"):
        raise DomainError("CALIBRATION_INTEGRITY_ERROR", "Probability summary differs from samples")
    metrics = report.reliability
    checks = []
    for name, observed, required in (
        (
            "minimum_calibration_samples",
            len(report.calibration_ids),
            policy.minimum_calibration_samples,
        ),
        ("minimum_evaluation_samples", metrics.sample_count, policy.minimum_evaluation_samples),
        ("minimum_evaluation_up", metrics.up_count, policy.minimum_evaluation_up),
        (
            "minimum_evaluation_non_up",
            metrics.sample_count - metrics.up_count,
            policy.minimum_evaluation_non_up,
        ),
    ):
        checks.append(
            {
                "name": name,
                "observed": observed,
                "required": required,
                "passed": observed >= required,
            }
        )
    for name, observed, required in (
        ("maximum_brier", metrics.brier, policy.maximum_brier),
        ("maximum_ece", metrics.ece, policy.maximum_ece),
    ):
        checks.append(
            {
                "name": name,
                "observed": observed,
                "required": required,
                "passed": observed is not None and Decimal(observed) <= Decimal(required),
            }
        )
    result = {
        "schema_version": "probability-quality-assessment-v1",
        "calibration_id": saved.id,
        "model_id": saved.model_id,
        "calibration_content_hash": saved.content_hash,
        "report_hash": saved.report_hash,
        "calibration_cohort_hash": content_hash(list(report.calibration_ids)),
        "evaluation_cohort_hash": content_hash(list(report.evaluation_ids)),
        "policy": policy.model_dump(mode="json"),
        "policy_hash": content_hash(policy.model_dump(mode="json")),
        "checks": checks,
        "numeric_pass": all(check["passed"] for check in checks),
        "qualification": "UNVERIFIED",
        "promotion_allowed": False,
        "promotion_blockers": ["HISTORICAL_EVIDENCE_UNQUALIFIED", "HUMAN_APPROVAL_REQUIRED"],
    }
    result["content_hash"] = content_hash(result)
    return result
