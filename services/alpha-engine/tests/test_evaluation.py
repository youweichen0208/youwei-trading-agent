from decimal import localcontext

import pytest
from pydantic import ValidationError
from yta_alpha.evaluation import EvaluationRequest, summarize


def test_statistics_use_decimal_and_account_for_missing_results():
    rows = [
        {
            "status": "SETTLED",
            "base_session": "2024-11-22",
            "result": {
                "direction_correct": correct,
                "absolute_error": "0.015",
                "zero_return_absolute_error": "0.02",
            },
        }
        for correct in (True, False, False)
    ] + [
        {"status": status, "base_session": "2024-11-25", "result": None}
        for status in ("UNSCORABLE", "WAITING_DATA", "WAITING_TARGET", "SETTLEMENT_FAILED")
    ]
    with localcontext() as context:
        context.prec = 3  # caller context cannot lower financial precision
        report = summarize(rows)
    assert report["sample_count"] == 7
    assert report["scored_count"] == 3
    assert report["direction_accuracy"] == "0.333333333333"
    assert report["mae"] == "0.015000000000"
    assert sum(report["counts"].values()) == 7
    assert summarize([])["direction_accuracy"] is None


def test_evaluation_request_requires_ordered_dates_and_aware_cutoff():
    data = dict(
        data_mode="REPLAY",
        base_from="2024-11-22",
        base_to="2024-11-21",
        cutoff="2024-12-02T22:00:00Z",
    )
    with pytest.raises(ValidationError):
        EvaluationRequest(**data)
    with pytest.raises(ValidationError):
        EvaluationRequest(**{**data, "base_to": "2024-11-22", "cutoff": "2024-12-02T22:00:00"})
