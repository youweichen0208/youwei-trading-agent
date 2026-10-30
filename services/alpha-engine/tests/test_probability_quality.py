import json
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from yta_alpha.calibration import reliability
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.probability_quality import ProbabilityQualityPolicy, assess_probability_quality

ROOT = Path(__file__).resolve().parents[3]


def report():
    return json.loads((ROOT / "packages/contracts/fixtures/calibration-report.json").read_text())


def policy(**changes):
    return ProbabilityQualityPolicy.model_validate(
        {
            "policy_version": "synthetic-test-only-v1",
            "data_mode": "REPLAY",
            "horizon_sessions": 5,
            "minimum_calibration_samples": 5,
            "minimum_evaluation_samples": 5,
            "minimum_evaluation_up": 3,
            "minimum_evaluation_non_up": 2,
            "maximum_brier": "1",
            "maximum_ece": "1",
            **changes,
        }
    )


def test_exact_metric_boundaries_and_determinism_never_authorize_promotion():
    saved = report()
    metrics = saved["report"]["reliability"]
    rule = policy(maximum_brier=metrics["brier"], maximum_ece=metrics["ece"])
    with localcontext() as context:
        context.prec = 2
        result = assess_probability_quality(saved, rule)
    assert result == assess_probability_quality(saved, rule)
    assert result["numeric_pass"] is True
    assert result["promotion_allowed"] is False
    assert result["qualification"] == "UNVERIFIED"
    digest = result.pop("content_hash")
    assert digest == content_hash(result)
    assert result["evaluation_cohort_hash"] == content_hash(saved["report"]["evaluation_ids"])
    for field, metric in [("maximum_brier", "brier"), ("maximum_ece", "ece")]:
        strict = format(Decimal(metrics[metric]) - Decimal("0.000000000001"), "f")
        checked = assess_probability_quality(saved, policy(**{field: strict}))
        assert checked["numeric_pass"] is False
        assert [check["name"] for check in checked["checks"] if not check["passed"]] == [field]


@pytest.mark.parametrize(
    "field",
    [
        "minimum_calibration_samples",
        "minimum_evaluation_samples",
        "minimum_evaluation_up",
        "minimum_evaluation_non_up",
    ],
)
def test_insufficient_counts_remain_visible(field):
    result = assess_probability_quality(report(), policy(**{field: 100}))
    assert result["numeric_pass"] is False
    assert [row["name"] for row in result["checks"] if not row["passed"]] == [field]


def test_single_class_evaluation_does_not_pass_class_coverage():
    saved = report()
    saved["report"]["evaluation_targets"] = [0] * len(saved["report"]["evaluation_ids"])
    saved["report"]["reliability"] = reliability(
        tuple(Decimal(p) for p in saved["report"]["probabilities"]),
        saved["report"]["evaluation_targets"],
    )
    saved["report_hash"] = content_hash(saved["report"])
    result = assess_probability_quality(saved, policy())
    assert not result["numeric_pass"]
    assert (
        next(row for row in result["checks"] if row["name"] == "minimum_evaluation_up")["observed"]
        == 0
    )


@pytest.mark.parametrize(
    "change",
    [
        {"maximum_brier": 0.5},
        {"maximum_ece": "1.000000000001"},
        {"maximum_brier": "NaN"},
        {"minimum_evaluation_samples": True},
        {"minimum_evaluation_up": 0},
        {"promotion_allowed": True},
        {"policy_version": " "},
    ],
)
def test_policy_requires_explicit_strict_thresholds(change):
    with pytest.raises(ValidationError):
        policy(**change)


def test_scope_integrity_and_rehashed_numerical_tampering_are_rejected():
    saved = report()
    for changes in [{"data_mode": "LIVE_RESEARCH"}, {"horizon_sessions": 1}]:
        with pytest.raises(DomainError, match="scope"):
            assess_probability_quality(saved, policy(**changes))
    for key in ["brier", "ece"]:
        changed = deepcopy(saved)
        changed["report"]["reliability"][key] = "0"
        changed["report_hash"] = content_hash(changed["report"])
        with pytest.raises(DomainError, match="summary differs"):
            assess_probability_quality(changed, policy())
    changed = deepcopy(saved)
    changed["report_hash"] = "0" * 64
    with pytest.raises(ValidationError):
        assess_probability_quality(changed, policy())


def test_transport_rejects_rehashed_contradictory_checks_and_approval():
    from yta_alpha.probability_quality_contracts import (
        ProbabilityAssessmentResponse,
        ProbabilityPolicyResponse,
    )

    original = assess_probability_quality(report(), policy())
    assert (
        ProbabilityAssessmentResponse.model_validate(original).model_dump(mode="json") == original
    )
    rule = original["policy"]
    digest = content_hash(rule)
    envelope = {
        "id": "probability-quality-policy-" + digest,
        "content_hash": digest,
        "policy": rule,
    }
    assert ProbabilityPolicyResponse.model_validate(envelope).model_dump(mode="json") == envelope
    with pytest.raises(ValidationError):
        ProbabilityPolicyResponse.model_validate(envelope | {"content_hash": "0" * 64})
    for change in ("threshold", "outcome", "count", "order", "approval", "qualification"):
        value = deepcopy(original)
        if change == "threshold":
            value["checks"][0]["required"] += 1
        elif change == "outcome":
            value["checks"][0]["passed"] = False
            value["numeric_pass"] = False
        elif change == "count":
            value["checks"][2]["observed"] += 1
        elif change == "order":
            value["checks"].reverse()
        elif change == "approval":
            value["promotion_allowed"] = True
        else:
            value["qualification"] = "VERIFIED"
        value.pop("content_hash")
        value["content_hash"] = content_hash(value)
        with pytest.raises(ValidationError):
            ProbabilityAssessmentResponse.model_validate(value)
