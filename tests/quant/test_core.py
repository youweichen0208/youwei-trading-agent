from decimal import Decimal

import pytest
from alpha_quant.core import Close, DataError, decimal, indicators, portfolio, prediction, sessions


def rows(n=100):
    dates = sessions("2025-01-02", "2025-08-01")[:n]
    return [Close(d, str(100 + i)) for i, d in enumerate(dates)]


@pytest.mark.parametrize("value", [1.2, "NaN", "Infinity", "-1", "1e9999", "abc"])
def test_decimal_rejects_unsafe_values(value):
    with pytest.raises(DataError):
        decimal(value)


def test_exchange_holidays_and_weekends():
    assert sessions("2025-07-03", "2025-07-07") == ["2025-07-03", "2025-07-07"]


def test_missing_session_is_not_silently_filled():
    data = rows()
    del data[30]
    with pytest.raises(DataError, match="missing"):
        prediction(data, adjustment="split")


def test_indicators_warmup_and_monotonic_prices():
    result = indicators(rows())
    assert all(p["ma20"] is None for p in result[:19])
    assert result[19]["ma20"] == "109.5"
    assert all(p["rsi14"] is None for p in result[:14])
    assert result[14]["rsi14"] == "100"


def test_wilder_rsi_known_series():
    values = [
        "44.34",
        "44.09",
        "44.15",
        "43.61",
        "44.33",
        "44.83",
        "45.10",
        "45.42",
        "45.84",
        "46.08",
        "45.89",
        "46.03",
        "45.61",
        "46.28",
        "46.28",
        "46.00",
    ]
    data = [Close(r.session, v) for r, v in zip(rows(16), values, strict=True)]
    result = indicators(data)
    assert abs(Decimal(result[14]["rsi14"]) - Decimal("70.4641")) < Decimal("0.0001")
    assert abs(Decimal(result[15]["rsi14"]) - Decimal("66.2496")) < Decimal("0.0001")


def test_gap_resets_indicator_warmup():
    data = rows()
    data[30] = Close(data[30].session, None)
    result = indicators(data)
    assert all(x["ma20"] is None for x in result[30:50])
    assert result[50]["ma20"] is not None
    assert all(x["rsi14"] is None for x in result[30:45])


def test_predictions_have_no_future_leakage():
    data = rows(81)
    first = prediction(data, adjustment="split")
    data[-1] = Close(data[-1].session, "9999")
    second = prediction(data, adjustment="split")
    assert len(first["points"]) == 60
    assert first["points"][:-1] == second["points"][:-1]
    assert first["points"][-1]["prediction"] == second["points"][-1]["prediction"]
    assert first["points"][-1]["actual"] != second["points"][-1]["actual"]
    assert first["points"][0]["origin"] == data[20].session
    assert first["points"][0]["target"] == data[21].session


def test_prediction_insufficient_and_wrong_basis():
    assert prediction(rows(80), adjustment="split")["status"] == "unavailable"
    with pytest.raises(DataError):
        prediction(rows(), adjustment="raw")


def test_flat_price_baseline():
    data = [Close(r.session, "100") for r in rows()]
    result = prediction(data, adjustment="split")
    assert result["metrics"] == {"mae": "0", "rmse": "0"}
    assert result["prediction"] == "0"
    assert indicators(data)[-1]["rsi14"] == "50"


def test_portfolio_decimal_exactness_and_cash_concentration():
    result = portfolio(
        [{"symbol": "AAPL", "quantity": "0.3"}],
        "0.07",
        {"AAPL": {"close": "0.1", "session": "2025-07-03", "adjustment": "raw"}},
        as_of="2025-07-03",
    )
    assert Decimal(result["total"]) == Decimal("0.10")
    assert Decimal(result["positions"][0]["weight"]) == Decimal("0.3")
    assert Decimal(result["concentration"]) == Decimal("0.58")


def test_partial_portfolio_does_not_publish_false_total():
    result = portfolio([{"symbol": "AAPL", "quantity": "1"}], "5", {}, as_of="2025-07-03")
    assert result["total"] is None
    assert result["maxWeight"] is None
    assert result["positions"][0]["weight"] is None
    assert result["missing"]


def test_zero_portfolio_and_stale_quote():
    assert portfolio([], "0", {}, as_of="2025-07-03")["concentration"] is None
    result = portfolio(
        [{"symbol": "AAPL", "quantity": "1"}],
        "0",
        {"AAPL": {"close": "100", "session": "2025-07-02", "adjustment": "raw"}},
        as_of="2025-07-03",
    )
    assert result["total"] is None
