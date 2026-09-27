"""Frozen engineering model vehicles used to exercise the prediction pipeline.

These are not approved production models. Model selection still requires trial
registration and release approval. Callers supply snapshot bars in chronological
order after enforcing their time and authorization policies.
"""

from dataclasses import dataclass
from decimal import Decimal

BASELINE_MODEL_VERSION = "baseline-constant-v0"
QUANT_MODEL_VERSION = "quant-momentum-v0"
MOMENTUM_WINDOW = 20
MOMENTUM_MIN_BARS = MOMENTUM_WINDOW + 1


@dataclass(frozen=True)
class ModelPrediction:
    """Model result before Controller validation, provenance, and sealing."""

    source: str
    source_status: str
    model_version: str
    reason: str | None = None
    p_outperform: float | None = None
    expected_excess_return: float | None = None


def predict_baseline(bars: list[dict]) -> ModelPrediction:
    """Return the constant base-rate vehicle, independently of market history."""
    return ModelPrediction(
        source="baseline",
        source_status="produced",
        p_outperform=0.5,
        expected_excess_return=0.0,
        model_version=BASELINE_MODEL_VERSION,
    )


def predict_quant(bars: list[dict] | None = None) -> ModelPrediction:
    """Apply the existing momentum vehicle without changing its v0 semantics.

    It requires 21 valid bars, then compares the first and last of the final
    20 closes (19 close-to-close intervals). Insufficient history yields an
    unavailable result. These parameters are frozen engineering behavior.
    """
    bars = [
        bar
        for bar in bars or []
        if bar.get("quality") == "ok" and Decimal(str(bar["close"])) > 0
    ]
    if len(bars) < MOMENTUM_MIN_BARS:
        return ModelPrediction(
            source="quant_model",
            source_status="unavailable",
            reason="insufficient_history",
            model_version=QUANT_MODEL_VERSION,
        )
    window = bars[-MOMENTUM_WINDOW:]
    first = Decimal(str(window[0]["close"]))
    last = Decimal(str(window[-1]["close"]))
    momentum = (last / first - 1).quantize(Decimal("0.0000000001"))
    p = min(max(Decimal("0.5") + momentum, Decimal("0.05")), Decimal("0.95"))
    expected = min(max(momentum, Decimal("-0.5")), Decimal("0.5"))
    return ModelPrediction(
        source="quant_model",
        source_status="produced",
        p_outperform=float(p),
        expected_excess_return=float(expected),
        model_version=QUANT_MODEL_VERSION,
    )
