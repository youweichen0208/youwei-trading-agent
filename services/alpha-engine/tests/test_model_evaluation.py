from decimal import Decimal, localcontext

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.model_evaluation import price_metrics


def test_price_errors_and_directions_use_decimal_protocol():
    with localcontext() as context:
        context.prec = 6
        result = price_metrics(
            (Decimal("0.1"), Decimal("-0.2"), Decimal("0")),
            (Decimal("0.2"), Decimal("-0.1"), Decimal("0.3")),
        )
    assert result == {
        "sample_count": 3,
        "mae": "0.166666666667",
        "direction_accuracy": "0.666666666667",
    }


def test_direction_classification_follows_half_even_return_rounding():
    result = price_metrics(
        (Decimal("0.0000000000005"), Decimal("0.0000000000015")),
        (Decimal("0"), Decimal("0.000000000002")),
    )
    assert result["mae"] == "0.000000000000"
    assert result["direction_accuracy"] == "1.000000000000"
    assert price_metrics((), ()) == {"sample_count": 0, "mae": None, "direction_accuracy": None}


@pytest.mark.parametrize(
    "expected,actual",
    [
        ((0.1,), (Decimal("0"),)),
        ((Decimal("NaN"),), (Decimal("0"),)),
        ((Decimal("-1.1"),), (Decimal("0"),)),
        ((Decimal("1e100"),), (Decimal("0"),)),
        ((Decimal("0"),), ()),
    ],
)
def test_invalid_or_unaligned_returns_rejected(expected, actual):
    with pytest.raises(DomainError) as error:
        price_metrics(expected, actual)
    assert error.value.code == "INVALID_EVALUATION_INPUT"
