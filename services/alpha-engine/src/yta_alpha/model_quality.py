"""Explicit historical screening policy; never grants promotion authority."""

from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, rounded
from yta_alpha.model_evaluation_contracts import ExperimentalPriceReport
from yta_alpha.models import Contract, DataMode

Threshold = Annotated[str, Field(pattern=r"^[0-9]{1,12}(?:\.[0-9]{1,12})?$")]


class HistoricalQualityPolicy(Contract):
    schema_version: Literal["historical-quality-policy-v1"] = "historical-quality-policy-v1"
    policy_version: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    minimum_samples: Annotated[StrictInt, Field(ge=1)]
    maximum_mae: Threshold
    minimum_direction_accuracy: Threshold
    minimum_mae_improvement: Threshold
    require_reference: StrictBool

    @model_validator(mode="after")
    def probability_range(self):
        if Decimal(self.minimum_direction_accuracy) > 1:
            raise ValueError("Direction accuracy must be between zero and one")
        return self


def _report(value):
    parsed = ExperimentalPriceReport.model_validate(value)
    body = dict(value)
    digest = body.pop("content_hash")
    if content_hash(body) != digest or content_hash(value["cohort"]) != parsed.cohort_hash:
        raise DomainError("MODEL_EVALUATION_INTEGRITY_ERROR", "Report hash differs")
    return parsed


def assess_historical_quality(candidate, policy: HistoricalQualityPolicy, reference=None):
    """Called after registry replay; numeric screening is not evidence qualification."""
    report = _report(candidate)
    if (report.cohort.data_mode, report.cohort.horizon_sessions) != (
        policy.data_mode,
        policy.horizon_sessions,
    ):
        raise DomainError("QUALITY_POLICY_SCOPE_MISMATCH", "Policy and report scope differ")
    other = _report(reference) if reference is not None else None
    if other and (
        other.cohort != report.cohort
        or other.cohort_hash != report.cohort_hash
        or other.protocol_version != report.protocol_version
        or other.return_basis != report.return_basis
        or other.model_id == report.model_id
    ):
        raise DomainError(
            "EVALUATION_COHORT_MISMATCH",
            "Reference requires a different model on identical samples",
        )
    checks = []

    def check(name, observed, required, passed):
        checks.append({"name": name, "observed": observed, "required": required, "passed": passed})

    metric = report.metrics.model
    check(
        "minimum_samples",
        metric.sample_count,
        policy.minimum_samples,
        metric.sample_count >= policy.minimum_samples,
    )
    check(
        "maximum_mae",
        metric.mae,
        policy.maximum_mae,
        metric.mae is not None and Decimal(metric.mae) <= Decimal(policy.maximum_mae),
    )
    check(
        "minimum_direction_accuracy",
        metric.direction_accuracy,
        policy.minimum_direction_accuracy,
        metric.direction_accuracy is not None
        and Decimal(metric.direction_accuracy) >= Decimal(policy.minimum_direction_accuracy),
    )
    check(
        "required_reference",
        other is not None,
        policy.require_reference,
        not policy.require_reference or other is not None,
    )
    baselines = [("zero-return-v1", report.metrics.zero_return)]
    if report.cohort.horizon_sessions == 5:
        baselines.append(("momentum20-v1", report.metrics.momentum))
    if other is not None:
        baselines.append(("reference", other.metrics.model))
    with localcontext() as context:
        context.prec = 50
        for name, baseline in baselines:
            improvement = None
            if (
                metric.mae is not None
                and baseline is not None
                and baseline.mae is not None
                and baseline.sample_count == metric.sample_count
            ):
                improvement = format(rounded(Decimal(baseline.mae) - Decimal(metric.mae)), "f")
            check(
                "mae_improvement:" + name,
                improvement,
                policy.minimum_mae_improvement,
                improvement is not None
                and Decimal(improvement) >= Decimal(policy.minimum_mae_improvement),
            )
    result = {
        "schema_version": "historical-quality-assessment-v1",
        "candidate_evaluation_id": "model-evaluation-experimental-" + report.content_hash,
        "reference_evaluation_id": None
        if other is None
        else "model-evaluation-experimental-" + other.content_hash,
        "cohort_hash": report.cohort_hash,
        "policy": policy.model_dump(mode="json"),
        "policy_hash": content_hash(policy.model_dump(mode="json")),
        "checks": checks,
        "numeric_pass": all(item["passed"] for item in checks),
        "qualification": "UNVERIFIED",
        "promotion_allowed": False,
        "promotion_blockers": ["HISTORICAL_EVIDENCE_UNQUALIFIED", "HUMAN_APPROVAL_REQUIRED"],
    }
    result["content_hash"] = content_hash(result)
    return result
