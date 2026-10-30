import json
from copy import deepcopy
from decimal import localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_quality import HistoricalQualityPolicy, assess_historical_quality

ROOT = Path(__file__).resolve().parents[3]


def report():
    return json.loads(
        (ROOT / "packages/contracts/fixtures/experimental-price-report.json").read_text()
    )


def rehash(value):
    value.pop("content_hash", None)
    value["content_hash"] = content_hash(value)
    return value


def policy(**changes):
    return HistoricalQualityPolicy.model_validate(
        {
            "policy_version": "synthetic-test-only-v1",
            "data_mode": "REPLAY",
            "horizon_sessions": 5,
            "minimum_samples": 5,
            "maximum_mae": "1",
            "minimum_direction_accuracy": "0",
            "minimum_mae_improvement": "0",
            "require_reference": False,
            **changes,
        }
    )


def test_numeric_boundary_and_precision_never_authorize_promotion():
    value = report()
    for metric in value["metrics"].values():
        if metric is not None:
            metric["mae"] = "0.100000000001"
            metric["direction_accuracy"] = "0.500000000000"
    value["metrics"]["model"]["mae"] = "0.100000000000"
    rehash(value)
    with localcontext() as context:
        context.prec = 3
        result = assess_historical_quality(
            value,
            policy(
                maximum_mae="0.1",
                minimum_direction_accuracy="0.5",
                minimum_mae_improvement="0.000000000001",
            ),
        )
    assert result["numeric_pass"] is True
    assert result["promotion_allowed"] is False
    assert result["qualification"] == "UNVERIFIED"
    assert result["checks"][-1]["observed"] == "0.000000000001"
    digest = result.pop("content_hash")
    assert content_hash(result) == digest
    assert assess_historical_quality(value, policy(minimum_samples=6))["numeric_pass"] is False
    assert (
        assess_historical_quality(value, policy(maximum_mae="0.099999999999"))["numeric_pass"]
        is False
    )
    assert (
        assess_historical_quality(value, policy(minimum_direction_accuracy="0.500000000001"))[
            "numeric_pass"
        ]
        is False
    )
    assert (
        assess_historical_quality(value, policy(minimum_mae_improvement="0.000000000002"))[
            "numeric_pass"
        ]
        is False
    )


def test_missing_metrics_reference_and_momentum_fail_closed():
    value = report()
    value["metrics"]["model"]["mae"] = None
    value["metrics"]["model"]["direction_accuracy"] = None
    value["metrics"]["momentum20-v1"] = None
    rehash(value)
    result = assess_historical_quality(value, policy(require_reference=True))
    checks = {item["name"]: item for item in result["checks"]}
    for name in [
        "maximum_mae",
        "minimum_direction_accuracy",
        "required_reference",
        "mae_improvement:zero-return-v1",
        "mae_improvement:momentum20-v1",
    ]:
        assert checks[name]["passed"] is False
    assert checks["mae_improvement:momentum20-v1"]["observed"] is None


def test_scope_hash_and_same_model_reference_are_rejected():
    value = report()
    for changes in [{"data_mode": "LIVE_RESEARCH"}, {"horizon_sessions": 1}]:
        with pytest.raises(DomainError, match="scope"):
            assess_historical_quality(value, policy(**changes))
    with pytest.raises(DomainError):
        assess_historical_quality(value, policy(), deepcopy(value))
    value["metrics"]["model"]["mae"] = "999"
    with pytest.raises(DomainError, match="hash"):
        assess_historical_quality(value, policy())


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_samples": True},
        {"minimum_samples": 0},
        {"maximum_mae": 0.1},
        {"maximum_mae": "NaN"},
        {"maximum_mae": "-1"},
        {"minimum_direction_accuracy": "1.1"},
        {"minimum_mae_improvement": "0.0000000000001"},
        {"require_reference": "false"},
        {"policy_version": " "},
        {"promotion_allowed": True},
    ],
)
def test_policy_requires_explicit_strict_thresholds(changes):
    with pytest.raises(ValidationError):
        policy(**changes)


def test_policy_has_no_implicit_quality_thresholds():
    with pytest.raises(ValidationError):
        HistoricalQualityPolicy.model_validate({"policy_version": "v1"})
