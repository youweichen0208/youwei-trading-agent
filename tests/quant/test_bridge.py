import pytest
from alpha_quant.bridge import execute, session_plan, validate_market
from alpha_quant.core import DataError


def test_market_plan_honors_early_close_and_warmup():
    with pytest.raises(DataError, match="session_not_finalized"):
        session_plan("2025-07-03", "2025-07-03", "2025-07-03T17:10:00Z")
    plan = session_plan("2025-07-03", "2025-07-06", "2025-07-03T17:21:00Z")
    assert plan["end"] == "2025-07-03"
    assert plan["dataAsOf"] == "2025-07-03T17:00:00Z"
    assert plan["start"] < plan["displayStart"]


def test_market_validator_marks_absent_sessions_without_filling_prices():
    plan = {"start": "2025-07-02", "end": "2025-07-03"}
    row = {
        "session": "2025-07-02",
        "open": "1.000000000000000001",
        "high": "2",
        "low": "1",
        "close": "1.5",
        "volume": "100.5",
    }
    result = validate_market({"plan": plan, "rows": [row]})
    assert result["rows"][0] == row
    assert result["rows"][1]["close"] is None
    assert result["rows"][1]["reason"] == "missing_bar"
    assert result["missingSessions"] == ["2025-07-03"]
    with pytest.raises(DataError, match="duplicate"):
        validate_market({"plan": plan, "rows": [row, row]})
    with pytest.raises(DataError, match="inconsistent_ohlc"):
        validate_market({"plan": plan, "rows": [{**row, "high": "1"}]})
    with pytest.raises(DataError, match="decimal_string_required"):
        validate_market({"plan": plan, "rows": [{**row, "close": 1.5}]})


def test_private_bridge_refuses_future_portfolio_and_unknown_operation():
    with pytest.raises(DataError, match="session_not_finalized"):
        execute(
            {
                "operation": "portfolio",
                "asOf": "2090-01-03",
                "positions": [],
                "cashUsd": "0",
                "quotes": {},
            }
        )
    with pytest.raises(DataError, match="unsupported_operation"):
        execute({"operation": "exec", "command": "anything"})
