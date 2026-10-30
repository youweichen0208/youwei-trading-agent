import pytest
from pydantic import TypeAdapter, ValidationError
from yta_alpha.calibration_contracts import CalibrationDecimal, ProbabilityString


def test_calibration_decimal_preserves_scientific_zero_and_exact_probability_bounds():
    probability = TypeAdapter(ProbabilityString)
    for value in ["0E-12", "-0E+4", "1E-20", "0.9999999999999999999999999999", "1.000"]:
        assert probability.validate_python(value) == value
    for value in ["1.0000000000000000000000001", "-1E-20", "2e0", "1e1", "NaN", "1eBAD", 0.5]:
        with pytest.raises(ValidationError):
            probability.validate_python(value)
    assert TypeAdapter(CalibrationDecimal).validate_python("-1.2E-12") == "-1.2E-12"
