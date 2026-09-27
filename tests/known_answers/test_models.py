"""Known answers for the frozen engineering model vehicles, not efficacy tests."""

from dataclasses import asdict

import pytest

from quant.models import predict_baseline, predict_quant


def test_constant_baseline_remains_available_without_market_history():
    assert asdict(predict_baseline([])) == {
        "source": "baseline",
        "source_status": "produced",
        "reason": None,
        "p_outperform": 0.5,
        "expected_excess_return": 0.0,
        "model_version": "baseline-constant-v0",
    }


def test_momentum_uses_last_twenty_valid_closes_from_frozen_history():
    # The current v0 vehicle requires 21 bars but uses the last 20 closes.
    # The oldest close (10) is deliberately outside its calculation window.
    # In the window, a move from 100 to 125 is a 25% gain.
    bars = [
        {"close": "10", "quality": "ok"},
        *[{"close": "100", "quality": "ok"} for _ in range(19)],
        {"close": "125", "quality": "ok"},
    ]

    assert asdict(predict_quant(bars)) == {
        "source": "quant_model",
        "source_status": "produced",
        "reason": None,
        "p_outperform": 0.75,
        "expected_excess_return": 0.25,
        "model_version": "quant-momentum-v0",
    }


@pytest.mark.parametrize(
    "bars",
    [
        None,
        [],
        [
            *[{"close": "100", "quality": "ok"} for _ in range(20)],
            {"close": "125", "quality": "missing"},
            {"close": "125"},
            {"close": "0", "quality": "ok"},
            {"close": "-1", "quality": "ok"},
        ],
    ],
    ids=["no-history", "empty-history", "only-twenty-valid-bars"],
)
def test_insufficient_valid_history_has_no_fabricated_forecast(bars):
    assert asdict(predict_quant(bars)) == {
        "source": "quant_model",
        "source_status": "unavailable",
        "reason": "insufficient_history",
        "p_outperform": None,
        "expected_excess_return": None,
        "model_version": "quant-momentum-v0",
    }


@pytest.mark.parametrize(
    ("first_close", "last_close", "probability", "expected_return"),
    [
        ("100", "100", 0.5, 0.0),
        ("100", "60", 0.1, -0.4),
        ("100", "20", 0.05, -0.5),
        ("100", "200", 0.95, 0.5),
        ("3", "4", 0.8333333333, 0.3333333333),
    ],
    ids=["flat", "loss", "lower-clips", "upper-clips", "decimal-rounding"],
)
def test_momentum_known_answers_for_clipping_and_decimal_rounding(
    first_close, last_close, probability, expected_return
):
    bars = [
        *[{"close": first_close, "quality": "ok"} for _ in range(20)],
        {"close": last_close, "quality": "ok"},
    ]

    prediction = predict_quant(bars)

    assert prediction.source_status == "produced"
    assert prediction.p_outperform == probability
    assert prediction.expected_excess_return == expected_return
