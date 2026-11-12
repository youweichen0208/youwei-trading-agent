from decimal import Decimal

import pytest
from alpha_quant.core import DataError
from alpha_quant.forecast import assess_batch, evaluate, forecast_plan, research_schedule, score


def test_future_open_holiday_and_count_inclusive():
    plan = forecast_plan("2025-07-03T18:00:00Z", 5)
    assert plan["startSession"] == "2025-07-07"
    assert plan["targetSession"] == "2025-07-11"
    assert forecast_plan("2025-07-07T12:00:00Z", 5)["startSession"] == "2025-07-07"
    assert forecast_plan("2025-07-07T13:30:00Z", 5)["startSession"] == "2025-07-08"


def test_early_close_and_finalization():
    assert research_schedule("2025-07-03T17:19:59Z")["latestCompletedSession"] == "2025-07-02"
    value = research_schedule("2025-07-03T17:20:00Z")
    assert value["marketClose"] == "2025-07-03T17:00:00Z"
    assert value["latestCompletedSession"] == "2025-07-03"


def test_probabilistic_and_interval_scoring():
    forecast = dict(meanReturn=".1", lowerReturn="0", upperReturn=".2", upProbability=".8")
    value = score(forecast, Decimal(".3"))
    assert Decimal(value["brier"]) == Decimal(".04")
    assert Decimal(value["absoluteError"]) == Decimal(".2")
    assert Decimal(value["intervalScore"]) == Decimal("1.2")
    assert not value["intervalCovered"]
    assert score(forecast, Decimal(0))["brier"] == "0.64"


def test_settlement_uses_future_open_and_same_price_snapshot():
    distribution = dict(meanReturn=".2", lowerReturn="0", upperReturn=".3", upProbability=".7")
    request = dict(
        startSession="2025-07-07",
        targetSession="2025-07-11",
        rows=[
            dict(session="2025-07-07", open="100", close="150"),
            dict(session="2025-07-11", open="125", close="120"),
        ],
        benchmarkRows=[
            dict(session="2025-07-07", open="100", close="101"),
            dict(session="2025-07-11", open="105", close="110"),
        ],
        forecast=distribution,
    )
    value = evaluate(request)
    assert Decimal(value["actualReturn"]) == Decimal(".2")
    assert Decimal(value["excessReturn"]) == Decimal(".1")
    assert value["metrics"]["absoluteError"] == "0.0"
    request["rows"].pop()
    assert evaluate(request)["status"] == "pending"


def test_invalid_contracts_and_real_batch_threshold():
    with pytest.raises(DataError):
        score(
            dict(meanReturn=".1", lowerReturn=".2", upperReturn=".3", upProbability=".9"),
            Decimal(0),
        )
    sample = dict(
        control=dict(brier=".2", absoluteError=".1", intervalScore=".2"),
        candidate=dict(brier=".18", absoluteError=".105", intervalScore=".21"),
    )
    assert assess_batch([sample] * 30)["passes"]
    sample["candidate"]["absoluteError"] = ".106"
    assert not assess_batch([sample] * 30)["passes"]
