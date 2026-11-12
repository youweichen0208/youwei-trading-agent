"""Private stdin bridge. Only the trusted data service supplies rows, never an Agent tool."""

import json
import sys
from datetime import UTC, date, datetime, timedelta

from alpha_quant.core import (
    VERSION,
    Close,
    DataError,
    calendar,
    decimal,
    indicators,
    portfolio,
    prediction,
    sessions,
)
from alpha_quant.forecast import assess_batch, contract, evaluate, forecast_plan, research_schedule

MAX_INPUT = 4 * 1024 * 1024


def session_plan(start: str, end: str, as_of: str, delay_seconds: int = 1200):
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if not (2000 <= start_date.year <= end_date.year <= 2099):
        raise DataError("unsupported_session_range")
    if (end_date - start_date).days > 3653:
        raise DataError("session_range_too_large")
    dates = sessions(start, end)
    if not dates:
        raise DataError("no_sessions")
    now = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise DataError("timestamp_timezone_required")
    cal = calendar(start_date.year, end_date.year)
    close = cal.session_close(dates[-1]).to_pydatetime()
    if close + timedelta(seconds=delay_seconds) > now:
        raise DataError("session_not_finalized")
    earlier = sessions((start_date - timedelta(days=90)).isoformat(), dates[0])
    # 20 prior sessions provide MA20 and Wilder RSI14 seed/warmup at display start.
    first = earlier[max(0, len(earlier) - 21)]
    return {
        "start": first,
        "end": dates[-1],
        "displayStart": dates[0],
        "displayEnd": dates[-1],
        "dataAsOf": close.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }


def validate_market(data: dict):
    plan = data["plan"]
    expected = sessions(plan["start"], plan["end"])
    if not expected or len(expected) > 2600:
        raise DataError("invalid_session_range")
    if not isinstance(data["rows"], list) or len(data["rows"]) > len(expected):
        raise DataError("invalid_market_rows")
    mapped, previous = {}, ""
    fields = {"session", "open", "high", "low", "close", "volume"}
    for row in data["rows"]:
        if not isinstance(row, dict) or set(row) != fields:
            raise DataError("invalid_market_row_fields")
        day = row["session"]
        if day not in expected or day <= previous:
            raise DataError("duplicate_unordered_or_foreign_session")
        previous = day
        prices = {k: decimal(row[k], positive=True) for k in ("open", "high", "low", "close")}
        if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(
            prices["open"], prices["close"]
        ):
            raise DataError("inconsistent_ohlc")
        decimal(row["volume"])
        mapped[day] = row
    rows, missing = [], []
    for day in expected:
        if day in mapped:
            rows.append(mapped[day])
        else:
            missing.append(day)
            rows.append(
                {"session": day, **dict.fromkeys(fields - {"session"}), "reason": "missing_bar"}
            )
    return {"rows": rows, "missingSessions": missing}


def execute(data: dict):
    operation = data["operation"]
    if operation == "validate_forecast":
        contract(data["forecast"])
        return {"valid": True}
    if operation == "forecast_plan":
        return forecast_plan(data["publishedAt"], data["horizon"])
    if operation == "research_schedule":
        return research_schedule(data["asOf"])
    if operation == "evaluate_forecast":
        return evaluate(data)
    if operation == "assess_experience_batch":
        return assess_batch(data["samples"])
    if operation == "plan_market":
        return session_plan(data["start"], data["end"], data["asOf"])
    if operation == "validate_market":
        return validate_market(data)
    if operation in ("indicators", "prediction"):
        rows = [Close(row["session"], row["close"]) for row in data["rows"]]
        if operation == "prediction":
            return prediction(rows, adjustment=data["adjustment"])
        result = indicators(rows)
        start = data["displayStart"]
        return {
            "codeVersion": VERSION,
            "status": "available",
            "rows": [r for r in result if r["session"] >= start],
        }
    if operation == "portfolio":
        session_plan(data["asOf"], data["asOf"], datetime.now(UTC).isoformat())
        return {
            "codeVersion": VERSION,
            **portfolio(data["positions"], data["cashUsd"], data["quotes"], as_of=data["asOf"]),
        }
    raise DataError("unsupported_operation")


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise DataError("input_too_large")
        result = execute(json.loads(raw))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
    except DataError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
    except (ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({"ok": False, "error": "invalid_quant_input"}))


if __name__ == "__main__":
    main()
